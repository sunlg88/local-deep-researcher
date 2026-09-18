"""Resumable, project-local source collection; model reviews never erase sources."""
import json
import re
import sqlite3
import time

from .pm_documents import text_ranges
from .pm_prompts import BOUNDARY, PROMPTS, OutputLimitError, PromptBudgetError
from .pm_store import now, worker_lock
from .pm_types import Settings, canonical_url, gate
from .pm_budget import (POLICY_VERSION, FixedPromptBudgetError, request_parts, ensure_fits)

TERMINAL = {'NO_NEW_WORK', 'TIME_LIMIT_REACHED', 'BUDGET_EXHAUSTED', 'STORAGE_ERROR',
            'ERROR', 'PARTIAL', 'COMPLETED_REVIEW_REQUIRED', 'INPUT_BUDGET_BLOCKED'}
EXTRACTOR_VERSION = 'extract-v2'


class ControlRequested(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class TimeLimitExceeded(Exception):
    pass


class Engine:
    def __init__(self, store, model, web):
        self.store, self.model, self.web = store, model, web
        self.deadline = None

    def check(self, pid):
        if self.store.load(pid)['control'] != 'RUN':
            raise ControlRequested()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeLimitExceeded('Configured active runtime reached; research is not certified complete')

    @staticmethod
    def compact(rows):
        keys = ('id','entity','subentity','metric','value','unit','period','scope','claim_text',
                'quote','url','conflict','review_status','comparison_status')
        return [{k: e.get(k, '') for k in keys} for e in rows]

    def _ask(self, s, role, payload):
        cfg = Settings.from_saved(s['settings'])
        payload.update(topic=s['topic'], instructions=s['instructions'])
        payload['_pm_budget_scale'] = s.get('token_budget_scales', {}).get(role, 1.0)
        self.check(s['id'])
        if s['calls'] >= cfg.max_calls:
            raise BudgetExceeded('LLM call budget exhausted')
        fixed_payload = dict(payload)
        for key, value in (('source_text', ''), ('evidence', []), ('evidence_ids', [])):
            if key in fixed_payload:
                fixed_payload[key] = value
        _, _, fixed = request_parts(cfg, role, fixed_payload)
        if fixed['estimated_input_tokens'] > fixed['input_budget']:
            raise FixedPromptBudgetError(
                f"Fixed {role} prompt estimate={fixed['estimated_input_tokens']}, available={fixed['input_budget']}; "
                'question/task metadata do not fit. No source splitting can fix this; data retained.')
        # Only the working evidence window may shrink, never the user's instructions.
        # Writer IDs are derived BEFORE measuring, and never include unseen evidence.
        while True:
            if role == 'writer':
                payload['evidence_ids'] = [e['id'] for e in payload.get('evidence', [])]
            _, _, metrics = request_parts(cfg, role, payload)
            if metrics['estimated_input_tokens'] <= metrics['input_budget']:
                break
            if len(payload.get('evidence', [])) <= 1:
                ensure_fits(metrics)
            payload['evidence'].pop()
        s['calls'] += 1
        self.store.save(s)
        self.store.log(s['id'], role, json.dumps(dict(metrics, call=s['calls'], status='started')))
        if hasattr(self.model, 'last_metrics'):
            self.model.last_metrics = {}
        status = 'MODEL_FAILED'
        try:
            result = self.model.ask(role, payload, lambda: self.check(s['id']))
            self.check(s['id'])
            if not isinstance(result, dict):
                raise ValueError('Role must return a JSON object')
            status = 'MODEL_COMPLETED'
            return result
        finally:
            observed = getattr(self.model, 'last_metrics', {})
            actual = observed.get('prompt_eval_count')
            if isinstance(actual, int) and actual > metrics['estimated_input_tokens']:
                old = payload['_pm_budget_scale']
                new = min(16.0, max(old, old * actual / metrics['estimated_input_tokens'] * 1.2))
                s.setdefault('token_budget_scales', {})[role] = new
                self.store.log(s['id'], 'BUDGET_CALIBRATED', json.dumps(
                    {'role': role, 'old_scale': old, 'new_scale': new, 'observed_input_tokens': actual}))
            self.store.log(s['id'], status, json.dumps(dict(metrics, **{
                k: v for k, v in observed.items() if k not in metrics},
                call=s['calls'], role=role)))
            self.store.save(s)

    def upgrade_budget_checkpoint(self, s):
        """Retry old pre-transport budget failures, keeping DONE work and evidence."""
        if s.get('budget_policy_version') == POLICY_VERSION:
            return
        restored = 0
        tasks = {t['id']: t for t in s['tasks']}
        for row in self.store.processing():
            task = tasks.get(row.get('task_id'))
            interrupted = (row.get('status') == 'RETRY' and
                           row.get('budget_policy_version') == POLICY_VERSION)
            if not task or (row.get('status') != 'FAILED' and not interrupted):
                continue
            error = row.get('error') or row.get('previous_error', '')
            if not any(word in error for word in ('PromptBudgetError', 'Prompt byte budget')):
                continue
            did, start, end = row.get('document_id'), row.get('start'), row.get('end')
            if not isinstance(start, int) or not isinstance(end, int) or start >= end:
                continue
            self.normalize_task(task)
            key = self.store.work_key(did, self.question(s, task), start, end, EXTRACTOR_VERSION)
            existing = self.store.work(key)
            if existing and existing['status'] in ('DONE', 'SPLIT'):
                continue
            if key not in {item['key'] for item in task['chunks']}:
                task['chunks'].append(dict(did=did, start=start, end=end, key=key, attempts=0))
            self.store.record_work(key, 'RETRY', task_id=task['id'], document_id=did,
                start=start, end=end, extractor_version=EXTRACTOR_VERSION,
                previous_error=error, budget_policy_version=POLICY_VERSION, attempts=0)
            restored += 1
        s['budget_policy_version'] = POLICY_VERSION
        if restored and s['stage'] in ('writer', 'finished'):
            s.update(stage='select', status='PENDING')
        self.store.log(s['id'], 'BUDGET_POLICY', json.dumps(
            {'version': POLICY_VERSION, 'restored_ranges': restored, 'completed_work_preserved': True}))

    def fit_source(self, s, task, item, payload, cfg):
        """Preflight a source range, checkpointing EVERY unread character before call."""
        def measure(end):
            data = dict(payload, topic=s['topic'], instructions=s['instructions'],
                source_text=payload['source_text'][:end-item['start']],
                source_range={'start': item['start'], 'end': end},
                _pm_budget_scale=s.get('token_budget_scales', {}).get('extractor', 1.0))
            return request_parts(cfg, 'extractor', data)[2]
        metrics = measure(item['end'])
        if metrics['estimated_input_tokens'] <= metrics['input_budget']:
            return False
        fixed = measure(item['start'])
        if fixed['estimated_input_tokens'] + 128 > fixed['input_budget']:
            raise FixedPromptBudgetError(
                f"Fixed prompt estimate={fixed['estimated_input_tokens']}, available={fixed['input_budget']}; "
                'question/metadata leave no safe source space. Change input/context settings; sources preserved.')
        lo, hi = item['start'], item['end']
        while lo < hi:
            mid = (lo + hi + 1) // 2
            m = measure(mid)
            if m['estimated_input_tokens'] <= m['input_budget']:
                lo = mid
            else:
                hi = mid - 1
        if lo - item['start'] < min(64, item['end']-item['start']):
            raise FixedPromptBudgetError('Fixed prompt leaves less than 64 source characters; source retained')
        # Preserve a little boundary context. Both children are strictly shorter.
        overlap = min(80, (lo-item['start'])//4)
        children = []
        for a, b in ((item['start'], lo), (lo-overlap, item['end'])):
            key = self.store.work_key(item['did'], self.question(s, task), a, b, EXTRACTOR_VERSION)
            children.append(dict(did=item['did'], start=a, end=b, key=key, attempts=item['attempts']))
        self.store.record_work(item['key'], 'SPLIT', task_id=task['id'], document_id=item['did'],
            start=item['start'], end=item['end'], extractor_version=EXTRACTOR_VERSION,
            reason='BUDGET_PREFLIGHT', children=children, attempts=item['attempts'])
        task['chunks'][:1] = children
        self.store.log(s['id'], 'CHUNK_RESIZED', json.dumps(
            {'task': task['id'], 'document': item['did'], 'start': item['start'], 'end': item['end'],
             'split_at': lo, 'estimated_input_tokens': metrics['estimated_input_tokens'],
             'input_budget': metrics['input_budget'], 'model_called': False}))
        s['stage'] = 'select'
        return True

    @staticmethod
    def make_task(item, number):
        if not isinstance(item, dict):
            raise ValueError('Task must be an object')
        title, query, criteria = item.get('title'), item.get('query'), item.get('criteria')
        if not all(isinstance(v, str) and 1 <= len(v.strip()) <= 240 for v in (title,query)):
            raise ValueError('Task title and query must be short, nonempty strings')
        if not isinstance(criteria, list) or not 1 <= len(criteria) <= 3 or not all(
                isinstance(c, str) and 1 <= len(c.strip()) <= 180 for c in criteria):
            raise ValueError('Task suggestions must contain 1-3 short criteria')
        return {'id': f't{number:03}', 'title': title.strip(), 'query': query.strip(),
                'criteria': [{'id': f'c{j}', 'text': c, 'kind': 'AI_SUGGESTION'}
                             for j,c in enumerate(criteria, 1)],
                'status': 'PENDING', 'attempts': 0, 'feedback': [], 'evidence_ids': [],
                'queries': [], 'document_ids': [], 'chunks': [], 'reviewed_ids': [],
                'review_queue': [], 'review_retry': {}, 'intake_done': False}

    def normalize_task(self, task):
        for name, default in [('feedback', []),('evidence_ids', []),('queries', []),
                ('document_ids', []),('chunks', []),('reviewed_ids', []),('review_queue', []),
                ('review_retry', {}),('attempts', 0)]:
            task.setdefault(name, default)

    @staticmethod
    def question(s, task):
        return json.dumps([s['topic'], s['instructions'], task['title']], ensure_ascii=False)

    def enqueue(self, s, task, did):
        cfg = Settings.from_saved(s['settings'])
        doc = self.store.document(did)
        if not cfg.allows(doc['url']):
            self.store.log(s['id'], 'SOURCE_REJECT', doc['url'])
            return
        if did not in task['document_ids']:
            task['document_ids'].append(did)
        self.store.link_document(did, task['id'])
        queued = {item['key'] for item in task['chunks']}
        for span in text_ranges(doc['body'], cfg.source_chars):
            start, end = span['start'], span['end']
            key = self.store.work_key(did, self.question(s, task), start, end, EXTRACTOR_VERSION)
            # SPLIT parents must not be regenerated; their children are saved in the task.
            previous = self.store.work(key)
            if previous and previous['status'] == 'DONE':
                task['evidence_ids'] = list(dict.fromkeys(task['evidence_ids'] + previous.get('evidence_ids', [])))
            if previous and previous['status'] == 'SPLIT':
                # Restore the parent to the queue; extract replays its committed children.
                if key not in queued:
                    task['chunks'].append({'did':did,'start':start,'end':end,'key':key,'attempts':0})
                    queued.add(key)
            if key not in queued and (not previous or previous['status'] == 'IN_PROGRESS'):
                task['chunks'].append({'did': did, 'start': start, 'end': end, 'key': key, 'attempts': 0})
                queued.add(key)

    def intake(self, s, task):
        if task.get('intake_done'):
            return
        task['intake_done'] = True
        for did in list(task['document_ids']):
            self.enqueue(s, task, did)
        if getattr(self.store, 'workspace', None):
            w = self.store.workspace
            for candidate in w.reference_candidates(s['id'], s['topic'] + ' ' + task['title']):
                if not Settings.from_saved(s['settings']).allows(candidate['url']):
                    continue
                try:
                    did = w.import_candidate(s['id'], candidate)
                    self.enqueue(s, task, did)
                except (ValueError, KeyError) as exc:
                    self.store.log(s['id'], 'REFERENCE_ERROR', str(exc))

    def select(self, s, cfg):
        tasks = s['tasks']
        if not tasks:
            s['stage'] = 'writer'
            return
        start = s.get('cursor', 0) % len(tasks)
        for offset in range(len(tasks)):
            index = (start + offset) % len(tasks)
            task = tasks[index]
            self.normalize_task(task)
            self.intake(s, task)
            pending = [eid for eid in task['evidence_ids'] if eid not in task['reviewed_ids']]
            if task['chunks']:
                stage = 'extract'
            elif task['review_queue'] or pending:
                if not task['review_queue']:
                    task['review_queue'] = [pending[i:i+4] for i in range(0, len(pending), 4)]
                stage = 'critic'
            elif task['attempts'] < cfg.max_attempts:
                stage = 'research'
            else:
                task['status'] = 'COLLECTED' if task['evidence_ids'] else 'NO_FINDINGS'
                continue
            s.update(active=index, stage=stage, cursor=(index+1) % len(tasks))
            task['status'] = 'RUNNING'
            return
        s['active'], s['stage'] = None, 'writer'

    def research(self, s, task, cfg):
        task['attempts'] += 1
        self.store.save(s)
        decision = self._ask(s, 'researcher', {'task': task['title'], 'criteria': task['criteria'],
            'feedback': task['feedback'][-5:], 'previous_queries': task['queries'],
            'initial_query': task['query'], 'suggested_query': task.get('suggested_query', ''),
            'evidence': self.compact(self.store.get_evidence(task['evidence_ids'][-3:]))})
        query = decision.get('query')
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
            raise ValueError('Researcher returned invalid query')
        query = query.strip()
        if query.casefold() in {q.casefold() for q in task['queries']}:
            if task['query'].casefold() not in {q.casefold() for q in task['queries']}:
                query = task['query']
            else:
                task['feedback'] = ['Repeated query not executed; no new search strategy was provided']
                s['stage'] = 'select'
                return
        if s['searches'] >= cfg.max_searches:
            raise BudgetExceeded('Search budget exhausted')
        task['queries'].append(query)
        s['searches'] += 1
        self.store.save(s)
        hits = self.web.search(query)
        if not isinstance(hits, list):
            raise ValueError('Search must return a list')
        discovered = 0
        for hit in hits[:cfg.source_limit*3]:
            self.check(s['id'])
            if not isinstance(hit, dict):
                continue
            url = hit.get('url', '')
            if not cfg.allows(url):
                self.store.record_source(url, 'POLICY_REJECTED', task['id'], query)
                continue
            url = canonical_url(url)
            old = self.store.source(url)
            if old and old.get('document_id'):
                self.store.record_source(url, old['status'], task['id'], query)
                self.enqueue(s, task, old['document_id'])
                continue
            if old and (old.get('permanent_failure') or old.get('attempts', 0) >= 2 or
                        old.get('next_retry_at', 0) > time.time()):
                self.store.record_source(url, old['status'], task['id'], query)
                continue
            attempts = (old or {}).get('attempts', 0) + 1
            self.store.record_source(url, 'FETCHING', task['id'], query, attempts=attempts,
                                     original_url=hit.get('url', ''), title=str(hit.get('title', url))[:1000])
            try:
                if hasattr(self.web, 'fetch_document'):
                    fetched = self.web.fetch_document(url)
                    body, raw, meta = fetched.body, fetched.raw, fetched.metadata
                else:
                    body, raw, meta = self.web.fetch(url), None, {'content_type': 'text/plain'}
                final_url = meta.get('final_url', url)
                if not cfg.allows(final_url):
                    raise ValueError('Redirect target violates source policy')
                did = self.store.add_document(final_url, str(hit.get('title', url))[:1000], body,
                                              metadata=meta, raw=raw)
                self.store.record_source(url, 'COLLECTED' if body else 'NEEDS_REPROCESSING', task['id'], query,
                    document_id=did, final_url=final_url, parse_status=meta.get('parse_status', 'TEXT'),
                    error='', error_type='', next_retry_at=0)
                self.enqueue(s, task, did)
                s['last_progress_at'] = now()
                discovered += 1
                self.store.save(s)
            except (ControlRequested, TimeLimitExceeded, sqlite3.Error):
                raise
            except OSError as exc:
                # Network errors are OSError subclasses too; local storage failures are not recoverable fetches.
                if getattr(exc, 'errno', None) in (13, 28, 30):
                    raise
                self.fetch_failure(s, task, url, query, attempts, exc)
            except Exception as exc:
                self.fetch_failure(s, task, url, query, attempts, exc)
            if discovered >= cfg.source_limit:
                break
        if discovered:
            followups = decision.get('followups', [])
            if isinstance(followups, list):
                for item in followups[:2]:
                    if len(s['tasks']) >= cfg.max_tasks:
                        break
                    try:
                        new = self.make_task(item, len(s['tasks'])+1)
                        if new['title'].casefold() not in {t['title'].casefold() for t in s['tasks']}:
                            s['tasks'].append(new)
                    except ValueError:
                        continue
        task['feedback'] = ([] if discovered else ['No new source this search; try a different query or public source'])
        s['stage'] = 'select'

    def fetch_failure(self, s, task, url, query, attempts, exc):
        code = getattr(exc, 'code', None)
        permanent = code in (400, 401, 403, 404, 410, 451) or isinstance(exc, ValueError)
        self.store.record_source(url, 'FETCH_FAILED', task['id'], query, attempts=attempts,
            error_type=type(exc).__name__, error=str(exc)[:1200], permanent_failure=permanent,
            next_retry_at=0 if permanent else time.time()+60*attempts)
        self.store.log(s['id'], 'FETCH_ERROR', json.dumps({'task': task['id'], 'query': query,
            'url': url, 'error_type': type(exc).__name__, 'attempt': attempts, 'error': str(exc)[:1200]}))

    def extract(self, s, task, cfg):
        # Supports a checkpoint written by the previous version, without trusting its old evidence.
        if not task['chunks']:
            for did in task['document_ids'][task.get('document_index', 0):]:
                self.enqueue(s, task, did)
        if not task['chunks']:
            s['stage'] = 'select'
            return
        item = task['chunks'][0]
        previous = self.store.work(item['key'])
        if previous and previous['status'] in ('DONE','FAILED','SPLIT'):
            task['chunks'].pop(0)
            if previous['status'] == 'DONE':
                task['evidence_ids'] = list(dict.fromkeys(task['evidence_ids'] + previous.get('evidence_ids', [])))
            elif previous['status'] == 'SPLIT':
                queued = {x['key'] for x in task['chunks']}
                task['chunks'][:0] = [child for child in previous.get('children', []) if child['key'] not in queued]
            s['stage'] = 'select'
            return
        doc = self.store.document(item['did'])
        payload = {'task': task['title'], 'criteria': task['criteria'], 'source_url': doc['url'],
                   'source_text': doc['body'][item['start']:item['end']],
                   'source_range': {'start': item['start'], 'end': item['end']},
                   'max_claims': 1 if item['attempts'] else 3, 'retry': item['attempts'],
                   'compact_retry': bool(item['attempts'])}
        if self.fit_source(s, task, item, payload, cfg):
            return
        self.store.record_work(item['key'], 'IN_PROGRESS', task_id=task['id'], document_id=doc['id'],
            start=item['start'], end=item['end'], extractor_version=EXTRACTOR_VERSION,
            question=self.question(s, task), attempts=item['attempts'])
        result = self._ask(s, 'extractor', payload)
        relevance, claims = result.get('relevance'), result.get('claims')
        if relevance not in ('relevant', 'uncertain', 'irrelevant') or not isinstance(claims, list) or len(claims)>12:
            raise ValueError('Extractor relevance and claims are missing or invalid')
        status = {'relevant':'RELEVANT', 'uncertain':'UNCONFIRMED', 'irrelevant':'EXCLUDED'}[relevance]
        self.store.link_document(doc['id'], task['id'], status, str(result.get('reason', ''))[:500])
        accepted = []
        if relevance != 'irrelevant':
            for claim in claims[:3]:
                try:
                    eid = self.store.add_evidence(doc['id'], claim, (item['start'], item['end']))
                    if relevance == 'relevant':
                        if eid not in task['evidence_ids']:
                            task['evidence_ids'].append(eid)
                        accepted.append(eid)
                    else:
                        self.store.review_evidence(eid, 'RELEVANCE_DISPUTED', 'Extractor relevance uncertain')
                except (ValueError, TypeError) as exc:
                    self.store.log(s['id'], 'QUOTE_REJECT', f"{task['id']} {doc['id']}: {exc}")
        self.store.record_work(item['key'], 'DONE', task_id=task['id'], document_id=doc['id'],
            start=item['start'], end=item['end'], extractor_version=EXTRACTOR_VERSION,
            question=self.question(s, task), relevance=relevance, evidence_ids=accepted,
            attempts=item['attempts']+1)
        task['chunks'].pop(0)
        self.store.log(s['id'], 'EXTRACTION_COMPLETED', json.dumps({
            'task': task['id'], 'document': doc['id'], 'start': item['start'], 'end': item['end'],
            'relevance': relevance, 'returned_claims': len(claims), 'accepted_claims': len(accepted)}))
        if accepted:
            s['last_progress_at'] = now()
        s['stage'] = 'select'

    def critic(self, s, task, cfg):
        if not task['review_queue']:
            ids = [eid for eid in task['evidence_ids'] if eid not in task['reviewed_ids']]
            task['review_queue'] = [ids[i:i+4] for i in range(0,len(ids),4)]
        if not task['review_queue']:
            s['stage'] = 'select'
            return
        batch = task['review_queue'][0]
        retry = task['review_retry'].get('|'.join(batch), 0)
        payload = {'task': task['title'], 'criteria': task['criteria'],
                   'compact_retry': bool(retry),
                   'evidence': self.compact(self.store.get_evidence(batch))}
        result = self._ask(s, 'critic', payload)
        shown = {e['id'] for e in payload['evidence']}
        checks, issues = result.get('checks'), result.get('issues', [])
        if not isinstance(checks,list) or not isinstance(issues,list) or not all(isinstance(x,str) for x in issues):
            raise ValueError('Critic response shape invalid')
        supported, mentioned = set(), set()
        for check in checks:
            if not isinstance(check,dict) or type(check.get('passed')) is not bool:
                raise ValueError('Critic check missing boolean')
            ids = check.get('evidence_ids')
            if not isinstance(ids,list) or not all(isinstance(x,str) and x in shown for x in ids):
                raise ValueError('Critic returned unknown evidence IDs')
            mentioned.update(ids)
            if check['passed']:
                supported.update(ids)
        for eid in shown:
            status = 'REVIEWED_SUPPORT' if eid in supported and not issues else 'NEEDS_REVIEW'
            if eid not in mentioned:
                status = 'REVIEW_INCOMPLETE'
            self.store.review_evidence(eid, status, json.dumps(result, ensure_ascii=False)[:2500])
        task['reviewed_ids'] = list(dict.fromkeys(task['reviewed_ids'] + list(shown)))
        task['review_queue'].pop(0)
        rest = [eid for eid in batch if eid not in shown]
        if rest:
            task['review_queue'].insert(0,rest)
        task['review'] = result
        suggestion = result.get('next_query')
        if isinstance(suggestion,str):
            task['suggested_query'] = suggestion[:500]
        task['feedback'] = issues[:5]
        s['stage'] = 'select'

    def writer(self, s, cfg):
        sections = s.setdefault('draft_sections', {})
        for task in s['tasks']:
            rows = self.store.get_evidence(task.get('evidence_ids', []))
            groups = {cfg.group(e['url']) for e in rows}
            bodies = {e['content_hash'] for e in rows}
            n = min(len(groups), len(bodies))
            task['source_status'] = 'NO_SOURCE' if not n else 'SINGLE_SOURCE' if n==1 else 'MULTIPLE_SOURCES_NOT_PROVEN_INDEPENDENT'
            task['source_target_met'] = n >= cfg.min_sources
            task['conflict_candidates'] = [e['id'] for e in rows if e.get('conflict')]
            if task['conflict_candidates']:
                task['feedback'] = list(dict.fromkeys(task.get('feedback', []) + ['Conflicting comparable values; both sources retained']))
            ids = [e['id'] for e in rows]
            if cfg.strict_final:
                ok, reasons, selected = gate(task, rows, task.get('review', {}), cfg)
                task['final_filter'] = {'passed': ok, 'reasons': reasons, 'evidence_ids': selected}
                ids = selected if ok else []
            if not cfg.draft_enabled or not ids or task['id'] in sections or task.get('draft_error'):
                continue
            s['writing_task'] = task['id']
            payload = {'task': task['title'], 'source_status': task['source_status'],
                       'compact_retry': bool(task.get('draft_retry')),
                       'evidence_ids': ids[-6:], 'evidence': self.compact(self.store.get_evidence(ids[-6:]))}
            result = self._ask(s, 'writer', payload)
            cited, text = result.get('evidence_ids'), result.get('summary')
            if not isinstance(text,str) or not text.strip() or not isinstance(cited,list) or not cited or not all(
                    isinstance(eid,str) and eid in payload['evidence_ids'] for eid in cited):
                raise ValueError('Writer summary or evidence IDs invalid')
            inline = re.findall(r'\[(e-[^\]\s]+)\]', text)
            if not inline or set(inline) != set(cited):
                raise ValueError('Writer omitted inline citations or used unknown evidence')
            if not isinstance(result.get('limitations'),list) or not all(isinstance(x,str) for x in result['limitations']):
                raise ValueError('Writer limitations invalid')
            result['notice'] = 'Partial model draft; all original material remains in handoff. Not a verified final report.'
            sections[task['id']] = result
            return
        s.update(status='NO_NEW_WORK', stage='finished', stop_reason='No untried work within current task and search-attempt limits; not certified research completion')
        s['summary'] = {'summary':'\n\n'.join(v['summary'] for v in sections.values()),
            'limitations':['Local model extraction/review is provisional; inspect original sources and unresolved.md.']}

    def recover(self, s, stage, exc, cfg):
        message = f'{type(exc).__name__}: {exc}'[:1500]
        s['last_error'] = message
        s['errors'] = s.get('errors',0)+1
        task = s['tasks'][s['active']] if s.get('active') is not None else None
        self.store.log(s['id'], 'WORK_ERROR', json.dumps({'stage':stage,'task': task and task['id'], 'error':message}))
        if isinstance(exc, FixedPromptBudgetError):
            s.update(status='INPUT_BUDGET_BLOCKED', stop_reason=message)
            return
        if stage == 'select':
            s['selection_errors'] = s.get('selection_errors',0)+1
            if s['selection_errors'] >= 2:
                s.update(status='ERROR',stop_reason='Project selection checkpoint is invalid; inspect saved error and references')
            return
        if stage == 'plan':
            s['planner_errors'] = s.get('planner_errors',0)+1
            if s['planner_errors'] >= 2:
                s['tasks'] = [self.make_task({'title':s['topic'][:240], 'query':s['topic'][:240],
                    'criteria':['Collect original material relevant to the unchanged user question']},1)]
                s.update(planner_fallback=True, stage='select')
            return
        if stage == 'extract' and task and task['chunks']:
            item = task['chunks'].pop(0)
            item['attempts'] += 1
            length = item['end']-item['start']
            children = []
            split = isinstance(exc,(OutputLimitError,PromptBudgetError)) or 'output limit' in str(exc).lower()
            if split and length > 256:
                mid = item['start']+length//2
                children=[]
                for start,end in ((item['start'],mid),(max(item['start'],mid-80),item['end'])):
                    key=self.store.work_key(item['did'],self.question(s,task),start,end,EXTRACTOR_VERSION)
                    children.append(dict(did=item['did'],start=start,end=end,key=key,attempts=item['attempts']))
                task['chunks'][:0]=children
                status='SPLIT'
            elif item['attempts']<2 and not isinstance(exc,PromptBudgetError):
                task['chunks'].insert(0,item)
                status='RETRY'
            else:
                status='FAILED'
            self.store.record_work(item['key'],status,task_id=task['id'],document_id=item['did'],
                start=item['start'],end=item['end'],extractor_version=EXTRACTOR_VERSION,
                attempts=item['attempts'],error=message,children=children)
        elif stage == 'critic' and task and task['review_queue']:
            batch=task['review_queue'].pop(0)
            key='|'.join(batch)
            tries=task['review_retry'].get(key,0)+1
            task['review_retry'][key]=tries
            if len(batch)>1:
                mid=len(batch)//2
                task['review_queue'][:0]=[batch[:mid],batch[mid:]]
            elif tries<2:
                task['review_queue'].append(batch)
            else:
                for eid in batch:
                    self.store.review_evidence(eid,'REVIEW_INCOMPLETE',message)
                task['reviewed_ids']=list(dict.fromkeys(task['reviewed_ids']+batch))
        elif stage == 'writer':
            writing=next((t for t in s['tasks'] if t['id']==s.get('writing_task')),None)
            if writing:
                writing['draft_retry'] = writing.get('draft_retry', 0) + 1
                if writing['draft_retry'] >= 2:
                    writing['draft_error'] = message
            else:
                s.update(status='NO_NEW_WORK',stage='finished',stop_reason='Draft unavailable; sources retained')
            return
        elif stage not in ('research','extract','critic','select'):
            s.update(status='ERROR',stop_reason='Unknown checkpoint; manual inspection required')
        if task:
            task['feedback']=[message]
        s['stage']='select'

    def step(self,pid):
        s=self.store.load(pid)
        if s['status'] in TERMINAL:
            return False
        if s['control']!='RUN':
            s['status']='PAUSED' if s['control']=='PAUSE' else 'STOPPED'
            s['stop_reason']='User '+s['control'].lower()
            self.store.save(s)
            return False
        cfg=Settings.from_saved(s['settings'])
        started=time.monotonic()
        remaining=cfg.time_limit_minutes*60-s.get('active_seconds',0)
        self.deadline=started+remaining if cfg.time_limit_minutes else None
        if hasattr(self.web,'check'):
            self.web.check=lambda:self.check(pid)
        s['status']='RUNNING'
        stage=s['stage']
        try:
            self.check(pid)
            for task in s['tasks']:
                self.normalize_task(task)
            self.upgrade_budget_checkpoint(s)
            stage = s['stage']
            if stage=='plan':
                retry = s.get('planner_errors', 0)
                maximum = cfg.min_tasks if retry else max(cfg.min_tasks, min(cfg.max_tasks, 3))
                result=self._ask(s,'planner',{'min_tasks':cfg.min_tasks,'max_tasks':maximum,
                    'compact_retry':bool(retry)})
                tasks=result.get('tasks')
                if not isinstance(tasks,list) or not cfg.min_tasks<=len(tasks)<=maximum:
                    raise ValueError('Planner task count invalid')
                created=[self.make_task(t,i) for i,t in enumerate(tasks,1)]
                if len({t['title'].casefold() for t in created})!=len(created):
                    raise ValueError('Planner duplicated task')
                s.update(tasks=created,stage='select')
            elif stage=='select':
                self.select(s,cfg)
            elif stage in ('research','extract','critic'):
                getattr(self,stage)(s,s['tasks'][s['active']],cfg)
            elif stage=='writer':
                self.writer(s,cfg)
            else:
                raise ValueError('Unknown persisted stage: '+stage)
        except ControlRequested:
            s['status']='PAUSED' if self.store.load(pid)['control']=='PAUSE' else 'STOPPED'
            s['stop_reason']='User control requested'
        except TimeLimitExceeded as exc:
            s.update(status='TIME_LIMIT_REACHED',stop_reason=str(exc))
        except BudgetExceeded as exc:
            s.update(status='BUDGET_EXHAUSTED',stop_reason=str(exc),last_error=str(exc))
        except (sqlite3.Error,OSError) as exc:
            # Connection/timeout errors from model calls are recoverable, filesystem errors are not.
            if isinstance(exc,sqlite3.Error) or getattr(exc,'errno',None) in (13,28,30):
                s.update(status='STORAGE_ERROR',stop_reason=str(exc),last_error=str(exc))
            else:
                self.recover(s,stage,exc,cfg)
        except Exception as exc:
            self.recover(s,stage,exc,cfg)
        finally:
            s['active_seconds']=s.get('active_seconds',0)+max(0,time.monotonic()-started)
            self.deadline=None
        self.store.save(s)
        return s['status'] not in TERMINAL|{'PAUSED','STOPPED'}

    def run(self,pid):
        # A report is generated at safe step boundaries, not by a racing background writer.
        # The SQLite checkpoint is persisted per action; bulk raw exports are periodic only.
        with worker_lock(self.store.root):
            cfg=Settings.from_saved(self.store.load(pid)['settings'])
            report_at=time.monotonic()+cfg.report_minutes*60
            try:
                while self.step(pid):
                    if time.monotonic()>=report_at:
                        self.store.export(pid)
                        report_at=time.monotonic()+cfg.report_minutes*60
            finally:
                self.store.export(pid)
