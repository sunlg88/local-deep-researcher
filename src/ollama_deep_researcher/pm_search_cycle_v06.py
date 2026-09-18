"""Focused queries, durable query batches, bounded fetches and ranked originals."""
import json
import hashlib
import sqlite3
import time
from urllib.parse import urlsplit

from .pm_engine import BudgetExceeded, ControlRequested, TimeLimitExceeded
from .pm_budget import FixedPromptBudgetError
from .pm_prompts import OutputLimitError, PromptBudgetError
from .pm_query_policy import validate_intent, query_variants, intent_anchors
from .pm_search_quality import normalize_hit, parse_search_intent, score_hit, diversify_hits, HitDecision, near_duplicate_query, site_allows
from .pm_fetch_quality import search_excerpt_key
from .pm_focus_v061 import score_focused_hit
from .pm_store import now
from .pm_types import canonical_url
from . import pm_research_metrics as metrics
from . import pm_v06_store as audit

# Three leads preserve the v0.5 source ceiling; a two-lead balanced preset
# lost a labelled third source in the controlled coverage regression.
FETCH_LIMITS={'efficient':1,'balanced':3,'quality':3}


class SearchCycleV06:
    def _known_domains(self,cfg):
        return list(dict.fromkeys(cfg.allowed_domains+[(urlsplit(x['url']).hostname or '')
                   for x in self.store.sources() if x.get('document_id')]))[:20]

    def _query_context(self,s,task,cfg):
        # Do not reuse a plan across changed goals, permissions, or new evidence.
        data=[s['id'],s['topic'],s['instructions'],task['title'],task['criteria'],
              cfg.model,cfg.search_api,cfg.source_mode,cfg.allowed_domains,sorted(task['evidence_ids'])]
        return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def _unseen_variants(self,intent,task,cfg):
        variants=query_variants(intent,'quality',0)
        entries=[{'query':q,'intent':intent.to_dict(),'variant':i} for i,q in enumerate(variants)
                 if not any(near_duplicate_query(q,old) for old in task['queries'])]
        return entries if cfg.optimization_mode=='quality' else entries[:1]

    def _plan_queries(self,s,task,cfg):
        task['attempts']+=1
        self.store.save(s)
        known=self._known_domains(cfg)
        context=self._query_context(s,task,cfg)
        if task.get('intent_context_v061')==context and task.get('last_intent'):
            try:
                cached=validate_intent(json.loads(task['last_intent']),set(known))
                available=self._unseen_variants(cached,task,cfg)
            except (TypeError,ValueError):
                available=[]
            if available:
                task['query_queue_v06']=available
                self.store.log(s['id'],'QUERY_VARIANT_REUSED',json.dumps({'task':task['id'],
                    'query':available[0]['query'],'model_called':False,'policy':'v061'}))
                self.store.save(s)
                return
        payload={'task':task['title'],'criteria':task['criteria'],'known_domains':known,
                 'previous_queries':task['queries'][-6:],'feedback':task['feedback'][-3:],
                 'attempt_feedback':self._task_feedback(task['id']),
                 'evidence':self.compact(self.store.get_evidence(task['evidence_ids'][-2:]))}
        # A malformed semantic reply gets one concise retry, not an invented query.
        for retry in range(2):
            try:
                data=self._ask(s,'researcher',dict(payload,compact_retry=bool(retry)))
                intent=validate_intent(data,set(known))
                break
            except (FixedPromptBudgetError,OutputLimitError,PromptBudgetError):
                raise
            except ValueError as exc:
                if retry: raise
                payload['feedback']=['Invalid structured intent: '+str(exc)]
                self.store.log(s['id'],'INTENT_RETRY',json.dumps({'task':task['id'],'reason':str(exc)}))
        signature=json.dumps(intent.to_dict(),sort_keys=True,ensure_ascii=False)
        if task.get('last_intent') and task['last_intent']!=signature:
            task['strategy_changes']=task.get('strategy_changes',0)+1
        task['last_intent']=signature
        task['intent_context_v061']=context
        task['query_queue_v06']=self._unseen_variants(intent,task,cfg)
        if not task['query_queue_v06']:
            task['duplicate_intent_streak']=task.get('duplicate_intent_streak',0)+1
            task['feedback']=['All variants of that intent were tried; choose a different information gap or entity.']
            if task['duplicate_intent_streak']>=2:
                task['attempts']=max(task['attempts'],cfg.max_attempts)
                task['v06_stop']='STALLED'
            self.store.log(s['id'],'INTENT_EXHAUSTED',json.dumps({'task':task['id'],
                'duplicate_streak':task['duplicate_intent_streak'],'search_called':False}))
        else:
            task['duplicate_intent_streak']=0
        self.store.save(s)

    def research(self,s,task,cfg):
        aid=task.get('active_search_id')
        if not aid:
            if not task.get('query_queue_v06'): self._plan_queries(s,task,cfg)
            if not task.get('query_queue_v06'):
                s['stage']='select';return
            entry=task['query_queue_v06'].pop(0)
            data=entry['intent']
            anchors=intent_anchors(validate_intent(data,set(self._known_domains(cfg))))
            query=entry['query']
            aid=metrics.begin_search_attempt(self.store,s['id'],task['id'],query,data['strategy'],anchors,
                                             executed_query=cfg.search_query(query))
            with self.store.db() as c:
                entry['evidence_rowid_before']=c.execute('SELECT coalesce(max(rowid),0) FROM evidence').fetchone()[0]
            audit.save_intent(self.store,aid,entry)
            if any(near_duplicate_query(query,old) for old in task['queries']):
                metrics.finish_search_attempt(self.store,aid,status='DUPLICATE_QUERY')
                task['feedback']=['Repeated query skipped; choose a genuinely new entity or gap.']
                self.store.log(s['id'],'SEARCH_SKIPPED',json.dumps({'attempt':aid,'reason':'DUPLICATE_QUERY'}))
                s['stage']='select';return
            task['queries'].append(query)
            task['active_search_id']=aid
            self.store.save(s)
        attempt=metrics.attempt(self.store,aid)
        if any(not isinstance(x, str) or not x.strip() or len(x.strip()) > 100 for x in attempt['anchors']):
            repaired=[]
            for text in attempt['anchors']:
                if isinstance(text,str):
                    repaired.extend(text[i:i+100].strip() for i in range(0,len(text),100) if text[i:i+100].strip())
            if not repaired:
                metrics.finish_search_attempt(self.store,aid,status='INVALID_INTENT',error='No usable anchors')
                task.pop('active_search_id',None)
                s['stage']='select'
                return
            attempt['anchors']=list(dict.fromkeys(repaired))[:6]
            with self.store.db() as c:
                c.execute('UPDATE search_attempts SET anchors=? WHERE id=?',(json.dumps(attempt['anchors']),aid))
            self.store.log(s['id'],'ANCHORS_REPAIRED',json.dumps({'attempt':aid,'original_query_preserved':True}))
        intent=parse_search_intent(attempt['query'],attempt['strategy'],attempt['anchors'])
        with self.store.db() as c:
            focus_row=c.execute('SELECT data FROM search_intents_v06 WHERE attempt_id=?',(aid,)).fetchone()
        attempt_focus=json.loads(focus_row[0])['intent'] if focus_row else {}
        try:
            validate_intent(attempt_focus,set(self._known_domains(cfg)))
        except ValueError as exc:
            metrics.finish_search_attempt(self.store,aid,status='INVALID_INTENT',error=str(exc))
            task.pop('active_search_id',None)
            task['query_queue_v06']=[]
            task['feedback']=['Stored intent incompatible with compact query policy: '+str(exc)]
            self.store.log(s['id'],'INTENT_RETIRED',json.dumps({'attempt':aid,'reason':str(exc),
                'originals_and_completed_work_preserved':True}))
            s['stage']='select';return
        if attempt['status'] in ('PLANNED','SEARCHING'):
            if s['searches']>=cfg.max_searches: raise BudgetExceeded('Search request budget exhausted')
            wait=self.pacer.wait('search:'+cfg.search_api,lambda:self.check(s['id']))
            s['searches']+=1
            metrics.finish_search_attempt(self.store,aid,status='SEARCHING')
            self.store.save(s)
            self.store.log(s['id'],'SEARCH_STARTED',json.dumps({'attempt':aid,'task':task['id'],
                           'query':attempt['query'],'executed_query':attempt['executed_query'],'backend':cfg.search_api}))
            try:
                hits=self.web.search(attempt['query'])
                if not isinstance(hits,list): raise ValueError('Search adapter must return a list')
                rows=[normalize_hit(h) for h in hits[:cfg.source_limit*3]]
                metrics.save_search_results(self.store,aid,[(i,h,score_focused_hit(intent,h,cfg,attempt_focus)) for i,h in enumerate(rows)])
                self.pacer.record('search:'+cfg.search_api,wait,success=True,status_code=200)
                with self.store.db() as c:
                    for i,h in enumerate(rows):
                        try: key=search_excerpt_key(h['url'],h['content'])
                        except ValueError: continue
                        c.execute('INSERT OR IGNORE INTO search_excerpts_v06 VALUES(?,?,?)',(key,aid,i))
            except (ControlRequested,TimeLimitExceeded,sqlite3.Error): raise
            except Exception as exc:
                self.pacer.record('search:'+cfg.search_api,wait,success=False,
                                  status_code=getattr(exc,'code',None),error_type=type(exc).__name__,
                                  retry_after=(getattr(exc,'headers',{}) or {}).get('Retry-After'))
                metrics.finish_search_attempt(self.store,aid,status='FAILED',error=type(exc).__name__)
                task.pop('active_search_id',None)
                raise
        candidates=[]
        for hit in metrics.hits(self.store,aid):
            if hit['status'] not in ('CANDIDATE','FETCHING'): continue
            if 'ENTITY_TOPIC_DEFERRED' in hit['reasons']:
                metrics.update_hit(self.store,aid,hit['rank'],'FOCUS_DEFERRED',reason='ENTITY_TOPIC_DEFERRED')
                continue
            url=canonical_url(hit['url']); old=self.store.source(url)
            if old and old.get('document_id'):
                did=old['document_id']
                metrics.update_hit(self.store,aid,hit['rank'],'DUPLICATE',document_id=did,reason='KNOWN_ORIGINAL')
                metrics.link_attempt_document(self.store,aid,did,False)
                self.enqueue(s,task,did)
                continue
            if old and old.get('permanent_failure'):
                metrics.update_hit(self.store,aid,hit['rank'],'KNOWN_FAILURE',reason='PERMANENT_URL_FAILURE');continue
            if metrics.host_in_cooldown(self.store,hit['host']) or old and old.get('next_retry_at',0)>time.time():
                metrics.update_hit(self.store,aid,hit['rank'],'COOLDOWN',reason='ACCESS_RETRY_DELAY');continue
            candidates.append((hit,HitDecision(True,hit['score'],tuple(hit['reasons']),hit['host'])))
        strong=[p for p in candidates if 'LOW_LEXICAL_MATCH' not in p[1].reasons]
        arranged=diversify_hits(strong or candidates,2)
        if not strong: arranged=arranged[:1]
        selected={h['rank'] for h,d in arranged}
        for h,d in candidates:
            if h['rank'] not in selected:
                metrics.update_hit(self.store,aid,h['rank'],'WEAK_DEFERRED',reason='RANK_OR_HOST_DIVERSITY')
        previous=metrics.hits(self.store,aid)
        used={}
        started=0
        for h in previous:
            if h['fetch_started']: started+=1;used[h['host']]=used.get(h['host'],0)+1
        limit=min(cfg.source_limit,FETCH_LIMITS[cfg.optimization_mode])
        with self.store.db() as c:
            row=c.execute('SELECT data FROM search_intents_v06 WHERE attempt_id=?',(aid,)).fetchone()
        semantic=json.loads(row[0])['intent']
        for hit,decision in arranged:
            self.check(s['id'])
            rank,url,host=hit['rank'],canonical_url(hit['url']),hit['host']
            if not hit['fetch_started'] and (started>=limit or used.get(host,0)>=2):
                metrics.update_hit(self.store,aid,rank,'QUOTA_DEFERRED',reason='MODE_FETCH_BUDGET');continue
            if metrics.host_in_cooldown(self.store,host):
                metrics.update_hit(self.store,aid,rank,'COOLDOWN',reason='HOST_COOLDOWN');continue
            wait=self.pacer.wait('host:'+host,lambda:self.check(s['id']))
            if not hit['fetch_started']: started+=1;used[host]=used.get(host,0)+1
            metrics.update_hit(self.store,aid,rank,'FETCHING',fetch_started=True)
            old=self.store.source(url) or {}
            attempts=old.get('attempts',0)+1
            self.store.record_source(url,'FETCHING',task['id'],attempt['query'],attempts=attempts,
                                     title=hit['title'],search_attempt_id=aid)
            self.web.source_allowed=lambda value:cfg.allows(value) and site_allows(intent,value)
            try:
                if hasattr(self.web,'fetch_document'):
                    fetched=self.web.fetch_document(url);body,raw,meta=fetched.body,fetched.raw,dict(fetched.metadata)
                else:
                    body,raw,meta=self.web.fetch(url),None,{'content_type':'text/plain'}
                final=meta.get('final_url',url)
                if not self.web.source_allowed(final): raise ValueError('Redirect violates source/site policy')
                meta.update(search_title=hit['title'],search_snippet=hit['snippet'],research_intent=semantic,
                            search_attempt_id=aid,fetched_title=meta.get('title',''))
                did=self.store.add_document(final,hit['title'] or url,body,metadata=meta,raw=raw)
                self.store.record_source(url,'COLLECTED' if body else 'NEEDS_REPROCESSING',task['id'],attempt['query'],
                    document_id=did,final_url=final,error='',error_type='',next_retry_at=0,permanent_failure=False)
                metrics.link_attempt_document(self.store,aid,did,True)
                metrics.update_hit(self.store,aid,rank,'FETCHED',document_id=did)
                self.pacer.record('host:'+host,wait,success=True,status_code=200)
                self.enqueue(s,task,did)
                s['last_progress_at']=now()
                self.store.save(s)
            except (ControlRequested,TimeLimitExceeded,sqlite3.Error): raise
            except OSError as exc:
                if getattr(exc,'errno',None) in (13,28,30): raise
                self._failed_fetch(s,task,aid,hit,url,attempts,exc)
                self._pace_failure(host,wait,exc)
            except Exception as exc:
                self._failed_fetch(s,task,aid,hit,url,attempts,exc)
                self._pace_failure(host,wait,exc)
        metrics.finish_search_attempt(self.store,aid,status='COMPLETED')
        outcome=self._attempt_feedback(aid)
        self.store.log(s['id'],'SEARCH_COMPLETED',json.dumps(dict(outcome,attempt=aid,task=task['id'])))
        task['feedback']=[] if outcome['documents_collected'] else ['No new original collected; change structured entity/gap.']
        task.pop('active_search_id',None)
        s['stage']='select'

    def _pace_failure(self,host,wait,exc):
        headers=getattr(exc,'headers',None)
        self.pacer.record('host:'+host,wait,success=False,status_code=getattr(exc,'code',None),
                          error_type=type(exc).__name__,retry_after=headers.get('Retry-After') if headers else None)
