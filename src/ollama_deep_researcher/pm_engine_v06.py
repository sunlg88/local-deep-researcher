"""v0.6 optimization candidate. Operates only on new, explicitly versioned projects."""
import json
from .pm_engine import Engine
from .pm_engine_v05 import EngineV05
from .pm_single_pass_v06 import SinglePassV06
from .pm_search_cycle_v06 import SearchCycleV06
from .pm_budget import calibration_key, request_parts, update_calibration
from .pm_types import Settings
from .pm_context_v062 import fit_research_payload
from .pm_query_v062 import task_entity, require_task_entity
from .pm_rate_limit_v06 import AdaptivePacer
from . import pm_research_metrics as metrics
from . import pm_v06_store as audit


def next_task_action(task: dict, mode: str) -> str:
    if task.get('pending_work'): return 'CONTINUE'
    zero=task.get('zero_yield_streak',0)
    limit=6 if mode=='quality' else 4
    if zero>=limit and task.get('strategy_changes',0)>=2: return 'STALLED'
    if task.get('supported') and task.get('no_new_evidence_streak',0)>=2: return 'COMPLETE'
    if zero>=2: return 'CHANGE_STRATEGY'
    return 'CONTINUE'


class EngineV06(SinglePassV06,SearchCycleV06,EngineV05):
    def __init__(self,store,model,web,semantic_backend=None):
        if store.project_id is None or store.load(store.project_id).get('engine_version')!=6:
            raise ValueError('v0.6 requires a new engine_version=6 project; existing projects are unchanged')
        if hasattr(web,'preflight'):
            metadata=web.preflight()
            store.log(store.project_id,'SEARCH_PROVIDER',json.dumps(metadata))
        super().__init__(store,model,web)
        audit.initialize(store)
        self.pacer=AdaptivePacer(store)
        self.semantic_backend=semantic_backend
        cfg=Settings.from_saved(store.load(store.project_id)['settings'])
        if semantic_backend is None and cfg.semantic_rerank!='off':
            from .pm_semantic_v06 import build_backend
            self.semantic_backend=build_backend(cfg)
            self.semantic_backend.check=lambda:self.check(store.project_id)

    def upgrade_budget_checkpoint(self,s):
        super().upgrade_budget_checkpoint(s)
        if s.get('hotfix_version')!='0.6.2':
            s['hotfix_version']='0.6.2'
            self.store.log(s['id'],'HOTFIX_POLICY',json.dumps({'version':'0.6.2',
                'engine_version':6,'context_tokens':s['settings']['context_tokens'],
                'completed_work_preserved':True}))

    def pending_search(self,task):
        return bool(task.get('active_search_id') or task.get('query_queue_v06'))

    def research(self,s,task,cfg):
        expected=task_entity(task.get('title',''),s['topic']+' '+s['instructions'])
        queued=task.get('query_queue_v06') or []
        if expected and queued:
            try:
                for entry in queued:
                    require_task_entity(entry.get('intent',{}),expected)
            except ValueError as exc:
                task['query_queue_v06']=[]
                task.pop('intent_context_v062',None)
                task['feedback']=[str(exc)]
                self.store.log(s['id'],'INTENT_RETIRED',json.dumps({'reason':str(exc),'search_called':False,
                    'originals_and_completed_work_preserved':True}))
                s['stage']='select'
                self.store.save(s)
                return
        return super().research(s,task,cfg)

    def _ask(self,s,role,payload):
        payload.update(_pm_version=6,topic=s['topic'],instructions=s['instructions'])
        cfg=Settings.from_saved(s['settings'])
        if role=='researcher':
            payload['_pm_search_contract']='v062'
            payload['_pm_bound_entity']=task_entity(payload.get('task',''),s['topic']+' '+s['instructions'])
        profile_payload=({'topic':s['topic'],'instructions':s['instructions'],'task':payload.get('task','')} if role=='researcher' else payload)
        key=calibration_key(cfg,role,profile_payload)
        previous=s.get('budget_calibration',{}).get(key,{})
        s.setdefault('token_budget_scales',{})[role]=previous.get('scale',1.0)
        if role=='researcher':
            payload['_pm_budget_scale']=previous.get('scale',1.0)
            payload,info=fit_research_payload(cfg,payload)
            if info['bounded_estimate']!=info['original_estimate']:
                self.store.log(s['id'],'CONTEXT_COMPACTED',json.dumps(info))
        if hasattr(self.model,'last_metrics'): self.model.last_metrics={}
        success=False
        try:
            result=Engine._ask(self,s,role,payload)
            if role=='researcher':
                require_task_entity(result,payload.get('_pm_bound_entity',''))
            success=True
            return result
        finally:
            observed=getattr(self.model,'last_metrics',{})
            measurement=request_parts(cfg,role,payload)[2]
            if 'qwen' in cfg.model.casefold():
                updated=update_calibration(previous,measurement['estimated_input_tokens']/measurement['estimate_scale'],
                                           observed.get('prompt_eval_count'),success and observed.get('done_reason')=='stop')
                s.setdefault('budget_calibration',{})[key]=updated
                s['token_budget_scales'][role]=updated['scale']
                if updated['scale']!=previous.get('scale',1.0):
                    self.store.log(s['id'],'BUDGET_CALIBRATED',json.dumps(dict(updated,profile=key)))
                self.store.save(s)

    def _attempt_feedback(self,aid):
        out=metrics.attempt_outcome(self.store,aid)
        with self.store.db() as c:
            statuses=[r[0] for r in c.execute('SELECT status FROM search_hits WHERE attempt_id=?',(aid,))]
            deferred=c.execute('''SELECT count(*) FROM attempt_documents a JOIN retrieval_plans_v06 r
                ON a.document_id=r.document_id WHERE a.attempt_id=? AND r.status IN ('DEFERRED','PARTIAL')''',(aid,)).fetchone()[0]
            canonical=[r[0] for r in c.execute('SELECT DISTINCT coalesce(r.alias_of,a.document_id)\n                FROM attempt_documents a LEFT JOIN retrieval_plans_v06 r ON r.document_id=a.document_id\n                WHERE a.attempt_id=?',(aid,))]
            ids=set(); relevant=set(); pending=0
            for did in canonical:
                job=c.execute('SELECT status FROM document_jobs WHERE document_id=?',(did,)).fetchone()
                pending+=int(job is None or job[0]!='DONE')
                for row in c.execute('SELECT relevance,evidence_ids FROM extraction_outcomes WHERE document_id=?',(did,)):
                    ids.update(json.loads(row[1]))
                    if row[0]=='relevant': relevant.add(did)
            marker=c.execute('SELECT data FROM search_intents_v06 WHERE attempt_id=?',(aid,)).fetchone()
            before=json.loads(marker[0]).get('evidence_rowid_before',0) if marker else 0
            new_ids={r[0] for r in c.execute('SELECT id FROM evidence WHERE rowid>?',(before,))}
        out['focus_deferred_hits']=sum(x=='FOCUS_DEFERRED' for x in statuses)
        out['accepted_claims']=len(ids)
        out['new_accepted_claims']=len(ids & new_ids)
        out['relevant_documents']=len(relevant)
        out['pending_documents']=pending
        if pending: out['outcome']='EXTRACTION_PENDING'
        elif ids: out['outcome']='YIELD'
        out['deferred_documents']=deferred
        if out['outcome']=='ZERO_YIELD' and statuses and all(x in ('FETCH_FAILED','COOLDOWN','KNOWN_FAILURE','REJECTED') for x in statuses):
            out['outcome']='ACCESS_OR_POLICY_BLOCKED'
        if deferred and not out['accepted_claims'] and out['outcome']!='EXTRACTION_PENDING':
            out['outcome']='LOW_YIELD_DEFERRED'
        return out

    def _task_feedback(self,task_id):
        rows=metrics.task_search_feedback(self.store,task_id,limit=8)
        for row in rows:
            status=row['status']
            row.update(self._attempt_feedback(row['id']))
            if status!='COMPLETED': row['outcome']=status
        return rows

    def _apply_task_stops(self,s,cfg):
        for task in s['tasks']:
            rows=self._task_feedback(task['id'])
            zero=0
            for row in rows:
                if row['outcome'] in ('EXTRACTION_PENDING','PLANNED','SEARCHING','HITS_READY'): continue
                if row['outcome'] not in ('ZERO_YIELD','LOW_YIELD_DEFERRED'): break
                zero+=1
            task['zero_yield_streak']=zero
            # Pending work cannot be counted as exhausted research.
            task['pending_work']=self.pending_search(task) or bool(s.get('document_queue'))
            checks=task.get('review',{}).get('checks',[])
            task['supported']=bool(checks) and not task.get('review',{}).get('issues') and all(any(c.get('criterion')==criterion['id'] and c.get('passed') is True
                                                    for c in checks) for criterion in task['criteria'])
            no_new=0
            for row in rows:
                if row['outcome']=='EXTRACTION_PENDING': continue
                if row['outcome'] not in ('YIELD','ZERO_YIELD','LOW_YIELD_DEFERRED') or row.get('new_accepted_claims',0): break
                no_new+=1
            task['no_new_evidence_streak']=no_new
            action=next_task_action(task,cfg.optimization_mode)
            if action in ('STALLED','COMPLETE'):
                task['attempts']=max(task['attempts'],cfg.max_attempts)
                task['v06_stop']=action
                task['feedback']=list(dict.fromkeys(task['feedback']+[
                    'Reading/search budget stopped on diminishing returns; deferred originals and unresolved gaps remain.']))
            elif action=='CHANGE_STRATEGY':
                task['feedback']=list(dict.fromkeys(task['feedback']+['Change entity/gap or language; recent completed searches added no accepted evidence.']))
