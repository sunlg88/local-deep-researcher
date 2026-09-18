"""Progressive source reading with durable selections and explicit unread coverage."""
from dataclasses import asdict, replace
import json

from .pm_single_pass import SinglePass
from .pm_chunking_v06 import build_chunks, expand_with_neighbors
from .pm_retrieval_v06 import rank_chunks, select_progressive, BUDGETS, terms, rerank_semantic
from .pm_fetch_quality import reuse_key, assess_fetched_page
from .pm_types import Settings
from . import pm_v06_store as audit
from . import pm_research_metrics as metrics


def _union(spans):
    merged=[]
    for a,b in sorted(spans):
        if merged and a <= merged[-1][1]: merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else: merged.append((a,b))
    return merged


class SinglePassV06(SinglePass):
    contract_version=6
    extract_version='extract-v06'

    def _retrieval_query(self,s):
        task_terms=[t['title'] for t in s['tasks']]
        return s['topic']+' '+s.get('instructions','')+' '+' '.join(task_terms),task_terms

    def _schedule_ids(self,s,doc,plan,ids,chunks):
        spans=[]
        for cid in ids:
            a,b,_=expand_with_neighbors(chunks,cid,max_chars=min(3200,Settings.from_saved(s['settings']).source_chars))
            spans.append((a,b))
        previous=plan['selected_ranges']
        # Remove intervals already scheduled. New contiguous windows may overlap
        # for conditions but exact repeated ranges are never invoked twice.
        for a,b in _union(spans):
            if any(x <= a and y >= b for x,y in previous): continue
            key=self.store.work_key(doc['id'],self.question(s),a,b,self.extract_version)
            previous.append([a,b])
            audit.record_decision(self.store,'CHUNK_SELECTED',doc['id'],start=a,end=b,
                                  pass_number=plan['pass_number'],chunk_ids=ids,work_key=key)
        plan['status']='QUEUED'
        audit.save_plan(self.store,plan)  # checkpoint before mutating the transient queue
        self._restore_plan(s,plan)

    def _restore_plan(self,s,plan):
        queued={x['key'] for x in s.setdefault('document_queue',[])}
        def leaves(item):
            work=self.store.work(item['key'])
            if work and work['status'] in ('DONE','FAILED'): return
            if work and work['status']=='SPLIT':
                for child in work['children']: leaves(child)
            elif item['key'] not in queued:
                s['document_queue'].append(item);queued.add(item['key'])
        for start,end in plan['selected_ranges']:
            key=self.store.work_key(plan['document_id'],self.question(s),start,end,self.extract_version)
            leaves(dict(did=plan['document_id'],start=start,end=end,key=key,attempts=0))

    def enqueue(self,s,task,did):
        cfg=Settings.from_saved(s['settings'])
        doc=self.store.document(did)
        if not cfg.allows(doc['url']): return
        if task:
            if did not in task['document_ids']: task['document_ids'].append(did)
            self.store.link_document(did,task['id'])
        if did not in s.setdefault('enqueued_documents',[]): s['enqueued_documents'].append(did)
        metrics.queue_document(self.store,did)
        existing=audit.get_plan(self.store,did)
        if existing:
            if existing['status']=='QUEUED': self._restore_plan(s,existing)
            return
        query,task_terms=self._retrieval_query(s)
        doc_intent=doc['metadata'].get('research_intent',{})
        anchors=[query]+task_terms+[doc_intent.get('entity',''),doc_intent.get('gap','')]+doc_intent.get('keywords',[])
        quality=assess_fetched_page(query_anchors=anchors,
            search_title=doc['metadata'].get('search_title',doc['title']),
            search_snippet=doc['metadata'].get('search_snippet',''),
            fetched_title=doc['metadata'].get('fetched_title',''),body=doc['body'])
        key=reuse_key(doc['body'],doc['title'])
        alias=audit.alias_candidate(self.store,key,did)
        plan={'document_id':did,'reuse_key':key,'alias_of':alias,'query':query,'task_terms':task_terms,
              'quality':asdict(quality),'selected_ranges':[],'ranked':[],'chunks':[],
              'pass_number':0,'status':'ALIAS' if alias else 'DEFERRED',
              'unread_chars':len(doc['body']),'total_chars':len(doc['body'])}
        if alias:
            audit.save_plan(self.store,plan)
            audit.record_decision(self.store,'IDENTICAL_BODY_SCOPE_REUSED',did,alias_of=alias)
            metrics.finish_document(self.store,did)
            return
        chunks=build_chunks(doc['body'],pages=doc['metadata'].get('pages',[]))
        source_title=doc['metadata'].get('fetched_title') or doc['metadata'].get('title','')
        if source_title:
            chunks=[replace(c,heading=c.heading or source_title) for c in chunks]
        ranked=rank_chunks(query,chunks,task_terms+[x for x in anchors[1:] if x])
        semantic_meta={'status':'SEMANTIC_OFF'}
        if cfg.semantic_rerank!='off' and quality.status!='ERROR_PAGE_DEFERRED' and self.semantic_backend is not None:
            # Optional reranking is bounded, and never loads weights implicitly.
            if cfg.semantic_rerank=='on' or len(chunks)>BUDGETS[cfg.optimization_mode][0] or quality.status!='READY':
                ranked,semantic_meta=rerank_semantic(query,chunks,ranked[:32],self.semantic_backend)
                audit.record_decision(self.store,'SEMANTIC_RETRIEVAL',did,**semantic_meta)
        plan['ranked']=[asdict(r) for r in ranked]
        plan['chunks']=[{k:v for k,v in asdict(c).items() if k!='text'} for c in chunks]
        semantic_ready=semantic_meta.get('status')=='SEMANTIC_RERANKED'
        ids=select_progressive(ranked,cfg.optimization_mode,0) if quality.status=='READY' or semantic_ready else []
        if ids:
            self._schedule_ids(s,doc,plan,ids,chunks)
        else:
            audit.save_plan(self.store,plan)
            metrics.finish_document(self.store,did)
            audit.record_decision(self.store,'DOCUMENT_DEFERRED',did,quality=asdict(quality),
                                  notice='Ranking is not proof of irrelevance; original remains available.')
            self.store.log(s['id'],'DOCUMENT_DEFERRED',json.dumps({'document':did,'reason':quality.status,'original_retained':True}))

    def _intake_references(self,s):
        if s.get('references_v06_scanned'): return
        workspace=getattr(self.store,'workspace',None)
        if workspace:
            from .pm_reference_v06 import reference_candidates
            query,task_terms=self._retrieval_query(s)
            imported={(r['origin_project_id'],r['origin_document_id']):r['data'].get('target_document_id')
                      for r in audit.reference_rows(self.store) if r['status']=='IMPORTED'}
            for candidate in reference_candidates(workspace,s['id'],query,task_terms,check=lambda:self.check(s['id'])):
                pair=(candidate['origin_project_id'],candidate['origin_document_id'])
                did=imported.get(pair) or workspace.import_candidate(s['id'],candidate)
                self.enqueue(s,None,did)
                audit.record_reference(self.store,dict(candidate,target_document_id=did),'IMPORTED')
        s['references_v06_scanned']=True

    def _advance_plans(self,s,cfg):
        pending={item['did'] for item in s['document_queue']}
        done_spans=None
        for plan in audit.plans(self.store):
            if plan['status']!='QUEUED' or plan['document_id'] in pending: continue
            did=plan['document_id']
            if done_spans is None:
                done_spans={}
                for row in self.store.processing():
                    if row['status']=='DONE': done_spans.setdefault(row.get('document_id'),[]).append((row['start'],row['end']))
            spans=done_spans.get(did,[])
            covered=_union(spans)
            plan['unread_chars']=max(0,plan['total_chars']-sum(b-a for a,b in covered))
            needs_more=any(not t['evidence_ids'] for t in s['tasks'])
            budget=BUDGETS[cfg.optimization_mode][1]
            ids=[r['chunk_id'] for r in plan['ranked'] if r['combined_score']>0 and not any(
                    a <= c['start'] and b >= c['end'] for a,b in plan['selected_ranges']
                    for c in plan['chunks'] if c['id']==r['chunk_id'])]
            if plan['pass_number']==0 and needs_more and budget and ids:
                plan['pass_number']=1
                doc=self.store.document(did)
                chunks=build_chunks(doc['body'],pages=doc['metadata'].get('pages',[]))
                self._schedule_ids(s,doc,plan,ids[:budget],chunks)
            else:
                plan['status']='PARTIAL' if plan['unread_chars'] else 'DONE'
                audit.save_plan(self.store,plan)
                metrics.finish_document(self.store,did)

    def select(self,s,cfg):
        # A crash/cancel can occur after original commit but before its ranked plan.
        # Re-open those sources; an in-memory enqueued flag is not proof of work.
        with self.store.db() as c:
            orphans={r[0] for r in c.execute('SELECT d.id FROM documents d LEFT JOIN retrieval_plans_v06 r ON d.id=r.document_id WHERE r.document_id IS NULL')}
        if orphans:
            s['enqueued_documents']=[did for did in s.get('enqueued_documents',[]) if did not in orphans]
        self.upgrade_budget_checkpoint(s)
        self._sync_evidence(s)
        for plan in audit.plans(self.store):
            if plan['status']=='QUEUED': self._restore_plan(s,plan)
        self._advance_plans(s,cfg)
        self._apply_task_stops(s,cfg)
        super().select(s,cfg)
        # The base scheduler labels exhausted tasks; retain our more precise reason.
        for task in s['tasks']:
            if task.get('v06_stop')=='STALLED': task['status']='STALLED'
