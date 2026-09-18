"""Project-scoped source queue and durable model-result replay for v0.5."""
import json
from .pm_documents import text_ranges
from .pm_store import digest, now
from .pm_types import Settings
from .pm_budget import request_parts, FixedPromptBudgetError, preflight_research_start
from .pm_prompts import OutputLimitError, PromptBudgetError
from . import pm_research_metrics as metrics

EXTRACT_VERSION='extract-v05'


class SinglePass:
    @staticmethod
    def task_catalog(s):
        return [{'id':t['id'],'title':t['title'],'criteria':t['criteria']} for t in s['tasks']]

    def question(self,s,task=None):
        return json.dumps([s['topic'],s['instructions'],self.task_catalog(s),s['settings']['model'],EXTRACT_VERSION],
                          ensure_ascii=False,sort_keys=True)

    def upgrade_budget_checkpoint(self,s):
        s.setdefault('document_queue',[])
        s.setdefault('enqueued_documents',[])
        s.setdefault('references_intake',[])
        if s['tasks']:
            fingerprint=digest(json.dumps(self.task_catalog(s),sort_keys=True,ensure_ascii=False))
            if s.get('task_catalog_hash') not in (None,fingerprint):
                raise ValueError('Frozen task catalogue changed; create a new project')
            if s.get('task_catalog_hash') is None:
                preflight_research_start(Settings.from_saved(s['settings']),s['topic'],s['instructions'],self.task_catalog(s))
            s['task_catalog_hash']=fingerprint

    def enqueue(self,s,task,did):
        cfg=Settings.from_saved(s['settings'])
        doc=self.store.document(did)
        if not cfg.allows(doc['url']):
            return
        if task:
            if did not in task['document_ids']:
                task['document_ids'].append(did)
            self.store.link_document(did,task['id'])
        metrics.queue_document(self.store,did)
        if did in s.setdefault('enqueued_documents',[]):
            return
        s['enqueued_documents'].append(did)
        queued={x['key'] for x in s.setdefault('document_queue',[])}
        for span in text_ranges(doc['body'],cfg.source_chars):
            key=self.store.work_key(did,self.question(s),span['start'],span['end'],EXTRACT_VERSION)
            if key not in queued:
                s['document_queue'].append(dict(did=did,key=key,start=span['start'],end=span['end'],attempts=0))
        if not doc['body']:
            metrics.finish_document(self.store,did)

    def _intake_references(self,s):
        workspace=getattr(self.store,'workspace',None)
        if not workspace:
            return
        for task in s['tasks']:
            if task['id'] in s['references_intake']:
                continue
            for candidate in workspace.reference_candidates(s['id'],s['topic']+' '+task['title']):
                if not Settings.from_saved(s['settings']).allows(candidate['url']):
                    continue
                try:
                    did=workspace.import_candidate(s['id'],candidate)
                    self.enqueue(s,task,did)
                except (ValueError,KeyError) as exc:
                    self.store.log(s['id'],'REFERENCE_ERROR',str(exc))
            s['references_intake'].append(task['id'])

    def _sync_evidence(self,s):
        for task in s['tasks']:
            task['evidence_ids']=self.store.task_evidence_ids(task['id'])
            task['reviewed_ids']=list(dict.fromkeys(task['reviewed_ids']))

    def select(self,s,cfg):
        self.upgrade_budget_checkpoint(s)
        self._intake_references(s)
        # IDs only: recovered fetch commits cannot strand an original after a crash.
        with self.store.db() as c:
            dids=[r[0] for r in c.execute('SELECT id FROM documents ORDER BY collected,id')]
        for did in dids:
            if did not in s['enqueued_documents']:
                self.enqueue(s,None,did)
        queued={x['did'] for x in s['document_queue']}
        for did in s['enqueued_documents']:
            if did not in queued:
                metrics.finish_document(self.store,did)
        self._sync_evidence(s)
        tasks=s['tasks']
        ready=None
        for offset in range(len(tasks)):
            index=(s.get('cursor',0)+offset)%len(tasks)
            task=tasks[index]
            pending=[x for x in task['evidence_ids'] if x not in task['reviewed_ids']]
            if task.get('active_search_id'):
                # Finish a persisted batch before issuing a new query.
                ready=(index,'research');break
            if task['review_queue'] or pending:
                if not task['review_queue']:
                    task['review_queue']=[pending[i:i+4] for i in range(0,len(pending),4)]
                ready=(index,'critic');break
            if task['attempts']<cfg.max_attempts:
                ready=(index,'research');break
            task['status']='COLLECTED' if task['evidence_ids'] else 'NO_FINDINGS'
        if s['document_queue'] and (s.get('last_scheduled')!='extract' or ready is None):
            s.update(stage='extract',active=0,last_scheduled='extract')
        elif ready:
            index,stage=ready
            s.update(stage=stage,active=index,cursor=(index+1)%len(tasks),last_scheduled=stage)
            tasks[index]['status']='RUNNING'
        else:
            s.update(stage='writer',active=None)

    def _split(self,s,item,split_at,reason,attempts):
        overlap=min(80,(split_at-item['start'])//4)
        children=[]
        for start,end in ((item['start'],split_at),(split_at-overlap,item['end'])):
            key=self.store.work_key(item['did'],self.question(s),start,end,EXTRACT_VERSION)
            children.append(dict(did=item['did'],key=key,start=start,end=end,attempts=attempts))
        self.store.record_work(item['key'],'SPLIT',document_id=item['did'],start=item['start'],end=item['end'],
            extractor_version=EXTRACT_VERSION,reason=reason,children=children,attempts=attempts)
        s['document_queue'][:1]=children
        s['stage']='select'

    def _fit(self,s,item,payload,cfg):
        def measure(end):
            data=dict(payload,topic=s['topic'],instructions=s['instructions'],_pm_version=5,
                source_text=payload['source_text'][:end-item['start']],
                source_range={'start':item['start'],'end':end},
                _pm_budget_scale=1.0)
            data['_pm_budget_scale']=self.scale_for(s,'extractor',data)
            return request_parts(cfg,'extractor',data)[2]
        full=measure(item['end'])
        if full['estimated_input_tokens']<=full['input_budget']:
            return False
        base=measure(item['start'])
        if base['estimated_input_tokens']+128>base['input_budget']:
            raise FixedPromptBudgetError('Fixed task catalogue/instructions leave insufficient source room; no source deleted')
        lo,hi=item['start'],item['end']
        while lo<hi:
            mid=(lo+hi+1)//2
            test=measure(mid)
            if test['estimated_input_tokens']<=test['input_budget']: lo=mid
            else: hi=mid-1
        if lo-item['start']<min(64,item['end']-item['start']):
            raise FixedPromptBudgetError('Fixed prompt leaves less than 64 source characters; original retained')
        self._split(s,item,lo,'BUDGET_PREFLIGHT',item['attempts'])
        self.store.log(s['id'],'CHUNK_RESIZED',json.dumps(dict(document=item['did'],start=item['start'],end=item['end'],
            split_at=lo,model_called=False,estimated_input_tokens=full['estimated_input_tokens'],input_budget=full['input_budget'])))
        return True

    def extract(self,s,task,cfg):
        queue=s['document_queue']
        if not queue:
            s['stage']='select';return
        item=queue[0]
        previous=self.store.work(item['key'])
        if previous and previous['status'] in ('DONE','FAILED','SPLIT'):
            queue.pop(0)
            if previous['status']=='SPLIT':
                keys={x['key'] for x in queue}
                queue[:0]=[x for x in previous.get('children',[]) if x['key'] not in keys]
            s['stage']='select';return
        doc=self.store.document(item['did'])
        payload={'task_catalog':self.task_catalog(s),'source_url':doc['url'],'source_title':doc['title'],
                 'source_text':doc['body'][item['start']:item['end']],
                 'source_range':{'start':item['start'],'end':item['end']},
                 'max_claims':1 if item['attempts'] else 3}
        data=dict(document_id=doc['id'],start=item['start'],end=item['end'],extractor_version=EXTRACT_VERSION,
                  question=self.question(s),attempts=item['attempts'])
        if previous and previous['status']=='RESULT_READY':
            result=previous['result']
        else:
            if self._fit(s,item,payload,cfg):
                return
            self.store.record_work(item['key'],'IN_PROGRESS',**data)
            result=self._ask(s,'extractor',payload)
            # A crash while linking claims replays this result without another model call.
            self.store.record_work(item['key'],'RESULT_READY',**data,result=result)
        relevance=result.get('relevance'); claims=result.get('claims')
        if relevance not in ('relevant','uncertain','irrelevant') or not isinstance(claims,list) or len(claims)>payload['max_claims']:
            raise ValueError('Invalid project extraction response')
        known={t['id'] for t in s['tasks']}
        for row in claims:
            ids=row.get('task_ids') if isinstance(row,dict) else None
            if not isinstance(ids,list) or not all(isinstance(x,str) and x in known for x in ids):
                raise ValueError('Extractor returned unknown/missing task IDs')
            if relevance=='relevant' and not ids:
                raise ValueError('Relevant claim lacks task IDs')
        if relevance=='irrelevant' and claims:
            raise ValueError('Irrelevant response must not contain claims')
        accepted=[]
        for row in claims:
            try:
                eid=self.store.add_evidence(doc['id'],row,(item['start'],item['end']))
            except (ValueError,TypeError) as exc:
                self.store.log(s['id'],'QUOTE_REJECT',f"{doc['id']}: {exc}")
                continue
            if relevance=='relevant':
                for tid in dict.fromkeys(row['task_ids']):
                    self.store.link_evidence_task(eid,tid)
                    self.store.link_document(doc['id'],tid,'RELEVANT',str(result.get('reason',''))[:500])
                accepted.append(eid)
            else:
                self.store.review_evidence(eid,'RELEVANCE_DISPUTED','Project relevance uncertain; no task adoption')
        for link in self.store.document_links():
            if link['document_id']==doc['id'] and link['status']!='RELEVANT':
                self.store.link_document(doc['id'],link['task_id'],
                    'EXCLUDED' if relevance=='irrelevant' else 'UNCONFIRMED',str(result.get('reason',''))[:500])
        metrics.record_extraction(self.store,item['key'],doc['id'],item['start'],item['end'],relevance,len(claims),accepted)
        self.store.record_work(item['key'],'DONE',**data,relevance=relevance,evidence_ids=accepted)
        self.store.log(s['id'],'EXTRACTION_COMPLETED',json.dumps({'document':doc['id'],
            'start':item['start'],'end':item['end'],'relevance':relevance,'reason':str(result.get('reason',''))[:500],
            'returned_claims':len(claims),'accepted_claims':len(set(accepted)),
            'task_ids':sorted({t for e in accepted for t in self.store.evidence_task_ids(e)})}))
        queue.pop(0)
        if accepted: s['last_progress_at']=now()
        self._sync_evidence(s)
        s['stage']='select'

    def recover_extraction(self,s,exc):
        item=s['document_queue'][0]
        attempts=item['attempts']+1
        message=f'{type(exc).__name__}: {exc}'[:1500]
        s['errors']=s.get('errors',0)+1;s['last_error']=message
        self.store.log(s['id'],'WORK_ERROR',json.dumps({'stage':'extract','document':item['did'],'error':message}))
        if isinstance(exc,FixedPromptBudgetError):
            s.update(status='INPUT_BUDGET_BLOCKED',stop_reason=message);return
        length=item['end']-item['start']
        if isinstance(exc,(OutputLimitError,PromptBudgetError)) and length>256 and attempts<=4:
            self._split(s,item,item['start']+length//2,message,attempts);return
        if attempts<2:
            item['attempts']=attempts; status='RETRY'
        else:
            status='FAILED';s['document_queue'].pop(0)
            metrics.record_extraction(self.store,item['key'],item['did'],item['start'],item['end'],'failed',0,[])
        self.store.record_work(item['key'],status,document_id=item['did'],start=item['start'],end=item['end'],
                              extractor_version=EXTRACT_VERSION,error=message,attempts=attempts)
        s['stage']='select'
