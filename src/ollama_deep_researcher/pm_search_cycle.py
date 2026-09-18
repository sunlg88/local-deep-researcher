"""Resumable search/fetch cycle shared by v0.5 fixed-catalogue work."""
import json
import sqlite3
import time
from urllib.parse import urlsplit

from .pm_engine import ControlRequested, TimeLimitExceeded, BudgetExceeded
from .pm_store import now
from .pm_search_quality import (parse_search_intent, normalize_hit, score_hit, diversify_hits,
                                HitDecision, near_duplicate_query, site_allows)
from . import pm_research_metrics as metrics
from .pm_types import canonical_url


class SearchCycle:
    def research(self, s, task, cfg):
        aid=task.get('active_search_id')
        if not aid:
            task['attempts']+=1
            self.store.save(s)
            known=list(dict.fromkeys(cfg.allowed_domains+[
                (urlsplit(x['url']).hostname or '') for x in self.store.sources() if x.get('document_id')]))[:20]
            decision=self._ask(s,'researcher',{
                'task':task['title'],'criteria':task['criteria'],'initial_query':task['query'],
                'previous_queries':task['queries'][-6:],'feedback':task['feedback'][-3:],
                'suggested_query':task.get('suggested_query',''),'known_domains':known,
                'attempt_feedback':metrics.task_search_feedback(self.store,task['id']),
                'evidence':self.compact(self.store.get_evidence(task['evidence_ids'][-2:]))})
            intent=parse_search_intent(decision['query'],decision['strategy'],decision['anchors'])
            actual_query=cfg.search_query(intent.query)
            aid=metrics.begin_search_attempt(self.store,s['id'],task['id'],intent.query,
                intent.strategy,list(intent.anchors),executed_query=actual_query)
            if any(near_duplicate_query(intent.query,old) for old in task['queries']):
                metrics.finish_search_attempt(self.store,aid,status='DUPLICATE_QUERY')
                task['feedback']=['Near-duplicate query not executed. Change entity phrase, language or information gap.']
                self.store.log(s['id'],'SEARCH_SKIPPED',json.dumps({'attempt':aid,'reason':'DUPLICATE_QUERY','query':intent.query}))
                s['stage']='select'
                return
            task['queries'].append(intent.query)
            task['active_search_id']=aid
            self.store.save(s)
        attempt=metrics.attempt(self.store,aid)
        # User allowlist stays a separate intersection, not an OR that widens query sites.
        intent=parse_search_intent(attempt['query'],attempt['strategy'],attempt['anchors'])
        if attempt['status'] in ('PLANNED','SEARCHING'):
            if s['searches']>=cfg.max_searches:
                raise BudgetExceeded('Search request budget exhausted')
            s['searches']+=1
            metrics.finish_search_attempt(self.store,aid,status='SEARCHING')
            self.store.save(s)
            self.store.log(s['id'],'SEARCH_STARTED',json.dumps({'attempt':aid,'task':task['id'],
                'query':attempt['query'],'executed_query':attempt['executed_query'],'backend':cfg.search_api}))
            try:
                hits=self.web.search(attempt['query'])
                if not isinstance(hits,list):
                    raise ValueError('Search adapter must return a hit list')
                normalized=[normalize_hit(h) for h in hits[:cfg.source_limit*3]]
                metrics.save_search_results(self.store,aid,[(i,h,score_hit(intent,h,cfg)) for i,h in enumerate(normalized)])
            except (ControlRequested,TimeLimitExceeded,sqlite3.Error):
                raise
            except Exception as exc:
                metrics.finish_search_attempt(self.store,aid,status='FAILED',error=str(exc))
                task.pop('active_search_id',None)
                raise
        pending=[]
        for hit in metrics.hits(self.store,aid):
            if hit['status'] not in ('CANDIDATE','FETCHING'):
                continue
            url=canonical_url(hit['url'])
            old=self.store.source(url)
            if old and old.get('document_id'):
                metrics.update_hit(self.store,aid,hit['rank'],'DUPLICATE',document_id=old['document_id'],reason='KNOWN_DOCUMENT')
                metrics.link_attempt_document(self.store,aid,old['document_id'],False)
                self.store.record_source(url,old['status'],task['id'],attempt['query'])
                self.enqueue(s,task,old['document_id'])
                continue
            if old and old.get('permanent_failure'):
                metrics.update_hit(self.store,aid,hit['rank'],'KNOWN_FAILURE',reason='PERMANENT_URL_FAILURE')
                continue
            if metrics.host_in_cooldown(self.store,hit['host']):
                metrics.update_hit(self.store,aid,hit['rank'],'COOLDOWN',reason='HOST_COOLDOWN')
                continue
            if old and old.get('next_retry_at',0)>time.time():
                metrics.update_hit(self.store,aid,hit['rank'],'COOLDOWN',reason='URL_RETRY_DELAY')
                continue
            pending.append((hit,HitDecision(True,hit['score'],tuple(hit['reasons']),hit['host'])))
        strong=[p for p in pending if 'LOW_LEXICAL_MATCH' not in p[1].reasons]
        eligible=strong or pending
        arranged=diversify_hits(eligible,per_host=2)
        if not strong:
            arranged=arranged[:1]
        selected={h['rank'] for h,d in arranged}
        for h,d in pending:
            if h['rank'] not in selected:
                why='WEAK_DEFERRED' if 'LOW_LEXICAL_MATCH' in d.reasons else 'HOST_DIVERSITY'
                metrics.update_hit(self.store,aid,h['rank'],why,reason=why)
        used={}
        started=0
        for h in metrics.hits(self.store,aid):
            if h['fetch_started']:
                started+=1; used[h['host']]=used.get(h['host'],0)+1
        for hit,decision in arranged:
            self.check(s['id'])
            url=canonical_url(hit['url']); rank=hit['rank']; host=hit['host']
            if metrics.host_in_cooldown(self.store,host):
                metrics.update_hit(self.store,aid,rank,'COOLDOWN',reason='HOST_COOLDOWN')
                continue
            # A FETCHING hit resumes the same logical slot after an interrupted read.
            if not hit['fetch_started']:
                if started>=cfg.source_limit or used.get(host,0)>=2:
                    metrics.update_hit(self.store,aid,rank,'QUOTA_DEFERRED',reason='FETCH_QUOTA')
                    continue
                started+=1; used[host]=used.get(host,0)+1
            metrics.update_hit(self.store,aid,rank,'FETCHING',fetch_started=True)
            old=self.store.source(url) or {}
            attempts=old.get('attempts',0)+1
            self.store.record_source(url,'FETCHING',task['id'],attempt['query'],attempts=attempts,
                original_url=hit['url'],title=hit['title'],search_attempt_id=aid)
            # Checked before every redirect in the real Web transport, too.
            self.web.source_allowed=lambda value: cfg.allows(value) and site_allows(intent,value)
            try:
                if hasattr(self.web,'fetch_document'):
                    fetched=self.web.fetch_document(url)
                    body,raw,meta=fetched.body,fetched.raw,fetched.metadata
                else:
                    body,raw,meta=self.web.fetch(url),None,{'content_type':'text/plain'}
                final_url=meta.get('final_url',url)
                if not self.web.source_allowed(final_url):
                    raise ValueError('Redirect target violates search site/source policy')
                did=self.store.add_document(final_url,hit['title'] or url,body,metadata=meta,raw=raw)
                self.store.record_source(url,'COLLECTED' if body else 'NEEDS_REPROCESSING',task['id'],attempt['query'],
                    document_id=did,final_url=final_url,error='',error_type='',next_retry_at=0,permanent_failure=False)
                metrics.link_attempt_document(self.store,aid,did,True)
                metrics.update_hit(self.store,aid,rank,'FETCHED',document_id=did)
                self.enqueue(s,task,did)
                s['last_progress_at']=now()
                self.store.save(s)
            except (ControlRequested,TimeLimitExceeded,sqlite3.Error):
                raise
            except OSError as exc:
                if getattr(exc,'errno',None) in (13,28,30):
                    raise
                self._failed_fetch(s,task,aid,hit,url,attempts,exc)
            except Exception as exc:
                self._failed_fetch(s,task,aid,hit,url,attempts,exc)
        metrics.finish_search_attempt(self.store,aid,status='COMPLETED')
        outcome=metrics.attempt_outcome(self.store,aid)
        self.store.log(s['id'],'SEARCH_COMPLETED',json.dumps(dict(outcome,attempt=aid,task=task['id'])))
        task['feedback']=[] if outcome['documents_collected'] else ['No useful new originals fetched. Inspect attempt_feedback and change strategy.']
        task.pop('active_search_id',None)
        s['stage']='select'

    def _failed_fetch(self,s,task,aid,hit,url,attempts,exc):
        code=getattr(exc,'code',None)
        permanent=code in (400,401,404,410) or isinstance(exc,(ValueError,UnicodeError))
        metrics.record_host_failure(self.store,hit['host'],code)
        self.store.record_source(url,'FETCH_FAILED',task['id'],metrics.attempt(self.store,aid)['query'],
            attempts=attempts,error_type=type(exc).__name__,error=str(exc)[:1500],permanent_failure=permanent,
            next_retry_at=0 if permanent else time.time()+(900 if code in (403,451) else 60*attempts))
        metrics.update_hit(self.store,aid,hit['rank'],'FETCH_FAILED',error=str(exc),reason=type(exc).__name__)
        self.store.log(s['id'],'FETCH_ERROR',json.dumps({'attempt':aid,'task':task['id'],'url':url,
            'error_type':type(exc).__name__,'code':code,'error':str(exc)[:1500]}))
