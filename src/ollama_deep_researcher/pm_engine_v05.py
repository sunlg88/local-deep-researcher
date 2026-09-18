"""Fixed-catalogue v0.5 engine; legacy checkpoints use read-only continuation."""
from .pm_engine import Engine
from .pm_search_cycle import SearchCycle
from .pm_single_pass import SinglePass
import json
from .pm_budget import calibration_key, update_calibration, request_parts
from .pm_types import Settings
from . import pm_research_metrics as metrics


class EngineV05(SinglePass, SearchCycle, Engine):
    def __init__(self, store, model, web):
        if store.project_id is None or store.load(store.project_id).get('engine_version',4) < 5:
            raise ValueError('Legacy project is read-only in v0.5; use continue_as_v05')
        super().__init__(store,model,web)
        metrics.initialize(store)

    def scale_for(self,s,role,payload):
        cfg=Settings.from_saved(s['settings'])
        return s.get('budget_calibration',{}).get(calibration_key(cfg,role,payload),{}).get('scale',1.0)

    def _ask(self, state, role, payload):
        payload.update(_pm_version=5,topic=state['topic'],instructions=state['instructions'])
        cfg = Settings.from_saved(state['settings'])
        key = calibration_key(cfg,role,payload)
        previous = state.get('budget_calibration',{}).get(key,{})
        state.setdefault('token_budget_scales',{})[role] = previous.get('scale',1.0)
        # A preflight failure must not reuse usage left by a previous request.
        if hasattr(self.model, 'last_metrics'):
            self.model.last_metrics = {}
        successful = False
        try:
            result = super()._ask(state, role, payload)
            successful = True
        finally:
            observed = getattr(self.model,'last_metrics',{})
            prompt, user, measurement = request_parts(cfg,role,payload)
            if 'qwen' in cfg.model.casefold():
                actual = observed.get('prompt_eval_count')
                base = measurement['estimated_input_tokens']/measurement['estimate_scale']
                updated = update_calibration(previous,base,actual,
                    successful and observed.get('done_reason')=='stop')
                state.setdefault('budget_calibration',{})[key] = updated
                state['token_budget_scales'][role] = updated['scale']
                if updated['scale'] != previous.get('scale',1.0):
                    self.store.log(state['id'],'BUDGET_CALIBRATED',json.dumps(dict(updated,profile=key)))
                self.store.save(state)
        if role == 'researcher':
            extra = set(result) - {'query', 'strategy', 'anchors'}
            if extra:
                raise ValueError('Researcher cannot change tasks or return extra fields: ' + ', '.join(sorted(extra)))
            if result.get('strategy') not in ('broad','exact_entity','official_site','pdf','gap'):
                raise ValueError('Researcher strategy missing or invalid')
            anchors = result.get('anchors')
            if (not isinstance(anchors,list) or not 2 <= len(anchors) <= 6 or
                not all(isinstance(a,str) and 1 <= len(a.strip()) <= 100 for a in anchors)):
                raise ValueError('Researcher requires 2-6 short anchors')
        return result

    def critic(self,s,task,cfg):
        if not task['review_queue']:
            s['stage']='select';return
        batch=task['review_queue'][0]
        tries=task['review_retry'].get('|'.join(batch),0)
        payload={'task':task['title'],'criteria':task['criteria'],
                 'evidence':self.compact(self.store.get_evidence(batch)), 'compact_retry':bool(tries)}
        result=self._ask(s,'critic',payload)
        shown={e['id'] for e in payload['evidence']}
        checks,issues=result.get('checks'),result.get('issues',[])
        if not isinstance(checks,list) or not isinstance(issues,list) or not all(isinstance(x,str) for x in issues):
            raise ValueError('Invalid critic response')
        supported,mentioned=set(),set()
        for check in checks:
            if not isinstance(check,dict) or type(check.get('passed')) is not bool:
                raise ValueError('Invalid critic check')
            ids=check.get('evidence_ids')
            if not isinstance(ids,list) or not all(isinstance(e,str) and e in shown for e in ids):
                raise ValueError('Critic returned unknown evidence IDs')
            mentioned.update(ids)
            if check['passed']: supported.update(ids)
        for eid in shown:
            status='REVIEWED_SUPPORT' if eid in supported and not issues else 'NEEDS_REVIEW'
            if eid not in mentioned: status='REVIEW_INCOMPLETE'
            self.store.review_evidence_task(eid,task['id'],status,json.dumps(result,ensure_ascii=False))
        task['reviewed_ids']=list(dict.fromkeys(task['reviewed_ids']+list(shown)))
        task['review_queue'].pop(0)
        rest=[eid for eid in batch if eid not in shown]
        if rest: task['review_queue'].insert(0,rest)
        task['review']=result
        if isinstance(result.get('next_query'),str): task['suggested_query']=result['next_query'][:500]
        task['feedback']=issues[:5]
        s['stage']='select'

    def recover(self,s,stage,exc,cfg):
        if stage=='extract' and s.get('document_queue'):
            self.recover_extraction(s,exc);return
        task=s['tasks'][s['active']] if s.get('active') is not None else None
        before=set(task.get('reviewed_ids',[])) if task else set()
        super().recover(s,stage,exc,cfg)
        if stage=='critic' and task:
            for eid in set(task['reviewed_ids'])-before:
                self.store.review_evidence_task(eid,task['id'],'REVIEW_INCOMPLETE',str(exc))
