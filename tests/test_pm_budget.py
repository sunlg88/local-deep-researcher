"""Budget regressions: multilingual prompts, real HTTP framing, checkpoint replay."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from ollama_deep_researcher.pm_engine import Engine
from ollama_deep_researcher.pm_io import Ollama
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_prompts import OutputLimitError, PromptBudgetError
from ollama_deep_researcher.pm_types import Settings
from test_pm import TEXT, claim
from test_pm_collector_flow import CollectorModel, CountingWeb

# Exactly the old permitted 1200 UTF-8 bytes. Synthetic, not a factual source.
INSTRUCTIONS = '\uc6d0\ubb38\uc758 \uc870\uac74\uacfc \uc2dc\uc810\uc744 \ubcf4\uc874\ud558\ub77c. '
INSTRUCTIONS = (INSTRUCTIONS * 100).encode('utf-8')[:1200].decode('utf-8', errors='ignore')
INSTRUCTIONS += 'x' * (1200 - len(INSTRUCTIONS.encode('utf-8')))
TOPIC = '2026\ub144 \uc6d0\uc790\ub825 SMR \ub300\ud615 \ub2e8\uc870\ud488 \uc81c\uc870\uc5c5\uccb4 \uacf5\uae09\ub9dd \uc870\uc0ac'


class BudgetFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.w = Workspace(self.tmp.name)
        self.cfg = Settings(max_attempts=1, max_tasks=3, draft_enabled=False)
        self.pid = self.w.create(TOPIC, self.cfg, INSTRUCTIONS)
        self.store = self.w.open(self.pid)

    def engine(self, model=None):
        return Engine(self.store, model or CollectorModel(), CountingWeb())

    def force_source(self, text):
        engine = self.engine()
        s = self.store.load(self.pid)
        t = engine.make_task({'title': TOPIC, 'query': 'SMR forging', 'criteria': ['Keep original conditions']}, 1)
        t['attempts'] = self.cfg.max_attempts
        t['intake_done'] = True
        s.update(tasks=[t], active=0, stage='extract')
        d = self.store.add_document('https://example.org/a', 'Synthetic source', text)
        engine.enqueue(s, t, d)
        self.store.save(s)
        return engine, d


class BudgetFlow(BudgetFixture):
    def test_korean_instruction_plus_3500_ascii_source_reaches_model(self):
        model = CollectorModel()
        engine = self.engine(model)
        s = self.store.load(self.pid)
        payload = {'task': TOPIC, 'source_text': ('Original conditions. ' * 200)[:3500],
                   'source_range': {'start': 0, 'end': 3500}, 'max_claims': 3}
        try:
            engine._ask(s, 'extractor', payload)
        except PromptBudgetError as exc:
            self.fail('8K request wrongly rejected before transport: ' + str(exc))
        self.assertEqual(model.payloads[0][1]['instructions'], INSTRUCTIONS)
        self.assertEqual(model.payloads[0][1]['source_text'], payload['source_text'])

    def test_korean_long_source_is_fitted_before_calls_without_error_storm(self):
        body = ('\uc6d0\uc790\ub825 \ub2e8\uc870\ud488 \uc81c\uc870 \uc870\uac74\uacfc \uc2dc\uc810. ' * 500) + '\n' + TEXT
        engine, did = self.force_source(body)
        model = engine.model
        engine.run(self.pid)
        s = self.store.load(self.pid)
        errors = [e for e in self.store.events(self.pid, 10000) if e['kind'] == 'WORK_ERROR']
        self.assertEqual(errors, [], 'Expected proactive fitting, not fail-and-halve')
        spans = [p['source_range'] for r, p in model.payloads if r == 'extractor']
        covered = bytearray(len(body))
        for role, payload in model.payloads:
            if role != 'extractor':
                continue
            a, b = payload['source_range']['start'], payload['source_range']['end']
            self.assertEqual(payload['source_text'], body[a:b])
            self.assertEqual(payload['instructions'], INSTRUCTIONS)
            covered[a:b] = b'1' * (b-a)
        self.assertTrue(spans)
        self.assertTrue(all(covered), 'Unread tails must stay queued')
        self.assertEqual(self.store.document(did)['body'], body)
        self.assertEqual(self.store.counts()['evidence'], 1)
        self.assertEqual(s['status'], 'NO_NEW_WORK')

    def test_fixed_overhead_stops_once_without_shredding_source(self):
        engine, did = self.force_source('Source text. ' * 1000)
        s = self.store.load(self.pid)
        # Simulates an old/imported state that does not fit even with an empty source.
        s['instructions'] = '\uac00' * 20000
        before = list(s['tasks'][0]['chunks'])
        self.store.save(s)
        engine.run(self.pid)
        s = self.store.load(self.pid)
        self.assertEqual(s['status'], 'INPUT_BUDGET_BLOCKED')
        self.assertEqual(s['calls'], 0)
        self.assertEqual(s['tasks'][0]['chunks'], before)
        self.assertEqual(len([e for e in self.store.events(self.pid, 10000) if e['kind']=='WORK_ERROR']), 1)
        self.assertEqual(self.store.document(did)['body'], 'Source text. ' * 1000)

    def test_planner_retry_changes_contract_instead_of_identical_retry(self):
        class FailingPlanner(CollectorModel):
            def ask(self, role, payload, check):
                if role == 'planner':
                    self.payloads.append((role, dict(payload)))
                    raise OutputLimitError('Synthetic output limit')
                return super().ask(role, payload, check)
        model = FailingPlanner()
        engine = self.engine(model)
        engine.step(self.pid)
        engine.step(self.pid)
        plans = [p for r, p in model.payloads if r=='planner']
        self.assertEqual(len(plans), 2)
        self.assertLess(plans[1]['max_tasks'], plans[0]['max_tasks'])
        self.assertTrue(plans[1].get('compact_retry'))
        self.assertEqual(plans[0]['instructions'], plans[1]['instructions'])

    def test_success_and_failed_call_are_logged_separately(self):
        engine = self.engine()
        s = self.store.load(self.pid)
        engine._ask(s, 'researcher', {'task': 'Synthetic'})
        logs = self.store.events(self.pid, 100)
        completed = [e for e in logs if e['kind']=='MODEL_COMPLETED']
        self.assertEqual(len(completed), 1)
        data = json.loads(completed[0]['message'])
        self.assertEqual(data['call'], 1)
        self.assertIn('estimated_input_tokens', data)
        self.assertIn('output_budget', data)

    def test_old_failed_budget_range_is_recovered_without_deleting_evidence(self):
        engine, did = self.force_source(TEXT)
        s = self.store.load(self.pid)
        item = s['tasks'][0]['chunks'].pop(0)
        self.store.record_work(item['key'], 'FAILED', task_id='t001', document_id=did,
            start=item['start'], end=item['end'], extractor_version='extract-v2',
            error='PromptBudgetError: old byte gate', attempts=1)
        s['stage'] = 'select'
        self.store.save(s)
        engine.run(self.pid)
        self.assertEqual(self.store.counts()['evidence'], 1)
        self.assertEqual(self.store.work(item['key'])['status'], 'DONE')


class ProbeHandler(BaseHTTPRequestHandler):
    mode = 'ok'
    received = []
    def log_message(self, *args):
        pass
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).received.append(data)
        self.send_response(200)
        self.end_headers()
        chunks = [{'message': {'thinking': 'synthetic reasoning'}, 'done': False},
                  {'message': {'content': '{"query":"fixture"}'}, 'done': False},
                  {'done': True, 'done_reason': 'length' if self.mode=='length' else 'stop',
                   'prompt_eval_count': 8000 if self.mode=='near_context' else 800,
                   'eval_count': data['options']['num_predict'] if self.mode=='length' else 20,
                   'eval_duration': 1000000000, 'prompt_eval_duration': 1000000}]
        for chunk in chunks:
            self.wfile.write((json.dumps(chunk)+'\n').encode())
        self.wfile.flush()


class BudgetTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), ProbeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
    def setUp(self):
        ProbeHandler.received, ProbeHandler.mode = [], 'ok'
        self.cfg = Settings(ollama_url=f'http://127.0.0.1:{self.server.server_port}')
        self.model = Ollama(self.cfg)

    def test_planner_is_non_thinking_and_schema_matches_small_plan(self):
        self.model.ask('planner', {'min_tasks':1, 'max_tasks':3}, lambda: None)
        request = ProbeHandler.received[-1]
        self.assertFalse(request['think'])
        self.assertEqual(request['format']['properties']['tasks']['maxItems'], 3)
        self.assertLess(request['options']['num_predict'], self.cfg.output_tokens)
        self.assertEqual(request['options']['num_ctx'], 8192)

    def test_each_role_uses_its_output_reservation(self):
        counts = []
        for role in ('planner', 'researcher', 'extractor', 'critic', 'writer'):
            self.model.ask(role, {}, lambda: None)
            counts.append(ProbeHandler.received[-1]['options']['num_predict'])
        self.assertGreaterEqual(len(set(counts)), 3)
        self.assertTrue(all(0 < x <= self.cfg.output_tokens for x in counts))

    def test_transport_agrees_with_engine_on_multilingual_input(self):
        payload = {'topic':TOPIC, 'instructions':INSTRUCTIONS,
                   'source_text': ('Original conditions. ' * 200)[:3500]}
        try:
            self.model.ask('extractor', payload, lambda: None)
        except ValueError as exc:
            self.fail('Transport still uses a separate UTF-8 byte gate: ' + str(exc))
        self.assertEqual(len(ProbeHandler.received), 1)

    def test_length_error_keeps_metrics_and_thinking_not_content(self):
        ProbeHandler.mode = 'length'
        with self.assertRaises(OutputLimitError):
            self.model.ask('planner', {}, lambda: None)
        self.assertEqual(self.model.last_metrics.get('done_reason'), 'length')
        self.assertEqual(self.model.last_metrics.get('prompt_eval_count'), 800)
        self.assertGreater(self.model.last_metrics.get('thinking_chars', 0), 0)
        self.assertEqual(self.model.last_metrics.get('answer_chars'), len('{"query":"fixture"}'))

    def test_near_context_response_is_not_silently_accepted(self):
        ProbeHandler.mode = 'near_context'
        with self.assertRaises(PromptBudgetError):
            self.model.ask('extractor', {}, lambda: None)
        self.assertEqual(self.model.last_metrics.get('prompt_eval_count'), 8000)

    def test_compact_review_retry_disables_thinking(self):
        self.model.ask('critic', {'compact_retry':True}, lambda: None)
        self.assertFalse(ProbeHandler.received[-1]['think'])

class BudgetRecovery(BudgetFixture):
    def test_prefit_checkpoint_replay_preserves_unread_tail(self):
        body = '\uc6d0\uc790\ub825\ub2e8\uc870\ud488 ' * 1000 + TEXT
        engine, _ = self.force_source(body)
        before = self.store.load(self.pid)
        first = before['tasks'][0]['chunks'][0]
        engine.step(self.pid)
        self.assertEqual(self.store.work(first['key'])['status'], 'SPLIT')
        self.store.save(before)  # interruption between work commit and state checkpoint
        engine.run(self.pid)
        spans = [p['source_range'] for r, p in engine.model.payloads if r=='extractor']
        self.assertNotIn({'start':first['start'], 'end':first['end']}, spans)
        covered = bytearray(len(body))
        for span in spans:
            covered[span['start']:span['end']] = b'1' * (span['end']-span['start'])
        self.assertTrue(all(covered))
        self.assertEqual(self.store.counts()['evidence'], 1)

    def test_completed_extraction_logs_kept_claims_and_range(self):
        engine, did = self.force_source(TEXT)
        engine.run(self.pid)
        events = [e for e in self.store.events(self.pid, 1000) if e['kind']=='EXTRACTION_COMPLETED']
        self.assertEqual(len(events), 1)
        item = json.loads(events[0]['message'])
        self.assertEqual(item['document'], did)
        self.assertEqual(item['accepted_claims'], 1)
        self.assertEqual(item['start'], 0)
        self.assertEqual(item['end'], len(TEXT))

    def test_fixed_question_budget_is_diagnosed_before_planner_retry(self):
        s = self.store.load(self.pid)
        s['instructions'] = '\uac00' * 10000
        self.store.save(s)
        self.engine().run(self.pid)
        s = self.store.load(self.pid)
        self.assertEqual(s['status'], 'INPUT_BUDGET_BLOCKED')
        self.assertEqual(s['calls'], 0)
        self.assertEqual(s['errors'], 1)

    def test_output_limit_retries_fewer_claims_not_same_large_answer(self):
        class Limited(CollectorModel):
            def ask(self, role, payload, check):
                if role == 'extractor' and payload.get('max_claims',3)>1:
                    self.payloads.append((role, dict(payload)))
                    raise OutputLimitError('Synthetic output limit')
                return super().ask(role, payload, check)
        engine, _ = self.force_source('Background. '*100 + TEXT)
        engine.model = Limited()
        engine.run(self.pid)
        calls = [p for r,p in engine.model.payloads if r=='extractor']
        self.assertEqual(calls[0]['max_claims'],3)
        self.assertEqual(calls[1]['max_claims'],1)
        self.assertEqual(self.store.counts()['evidence'],1)


class BudgetInvariants(BudgetFixture):
    def test_upgrade_does_not_replace_completed_work_with_old_failure(self):
        engine, did = self.force_source(TEXT)
        state = self.store.load(self.pid)
        task = state['tasks'][0]
        span = task['chunks'].pop(0)
        eid = self.store.add_evidence(did, claim())
        task['evidence_ids'] = [eid]
        task['reviewed_ids'] = [eid]
        data = dict(task_id=task['id'], document_id=did, start=0, end=len(TEXT))
        self.store.record_work(span['key'], 'DONE', **data, evidence_ids=[eid])
        self.store.record_work('old-policy-failure', 'FAILED', **data, error='PromptBudgetError: old byte gate')
        state['stage'] = 'select'
        self.store.save(state)
        engine.run(self.pid)
        self.assertFalse(any(role=='extractor' for role,payload in engine.model.payloads))
        self.assertEqual(self.store.work(span['key'])['status'], 'DONE')
        self.assertEqual(self.store.counts()['evidence'], 1)

    def test_measured_usage_increases_estimate_for_later_calls(self):
        class Measured(CollectorModel):
            def ask(self, role, payload, check):
                result = super().ask(role, payload, check)
                self.last_metrics = {'prompt_eval_count': 2500, 'eval_count': 30}
                return result
        model = Measured()
        engine = self.engine(model)
        state = self.store.load(self.pid)
        engine._ask(state, 'researcher', {'task': 'Synthetic'})
        self.assertGreater(self.store.load(self.pid)['token_budget_scales']['researcher'], 1)
        self.assertTrue(any(e['kind']=='BUDGET_CALIBRATED' for e in self.store.events(self.pid,100)))

    def test_unknown_model_fallback_does_not_assume_english_token_ratio(self):
        from ollama_deep_researcher.pm_budget import estimate_tokens
        text = '\ud55c\uae00\u4e2d\u6587\U0001f30d abc\n' * 100
        self.assertGreaterEqual(estimate_tokens(text, 'unknown-custom-model'), len(text.encode('utf-8')))

    def test_full_pipeline_uses_real_http_transport_and_preserves_korean_tail(self):
        body = ('\uc6d0\uc790\ub825 \ub2e8\uc870\ud488 \uc870\uac74. ' * 500) + TEXT
        oracle = CollectorModel()
        received = []
        class RoleHandler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append(request)
                properties = request['format']['properties']
                role = ('planner' if 'tasks' in properties else 'extractor' if 'relevance' in properties
                        else 'critic' if 'checks' in properties else 'writer' if 'summary' in properties else 'researcher')
                payload = json.loads(request['messages'][1]['content'])
                result = oracle.ask(role, payload, lambda: None)
                self.send_response(200); self.end_headers()
                self.wfile.write((json.dumps({'message':{'content':json.dumps(result)}, 'done':True,
                    'done_reason':'stop', 'prompt_eval_count':500, 'eval_count':100})+'\n').encode())
        class LongWeb(CountingWeb):
            def fetch(self, url):
                self.fetches += 1
                return body
        server = ThreadingHTTPServer(('127.0.0.1',0), RoleHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            state = self.store.load(self.pid)
            state['settings']['ollama_url'] = f'http://127.0.0.1:{server.server_port}'
            self.store.save(state)
            cfg = Settings.from_saved(state['settings'])
            engine = Engine(self.store, Ollama(cfg), LongWeb())
            engine.run(self.pid)
            final = self.store.load(self.pid)
            self.assertEqual(final['status'],'NO_NEW_WORK')
            self.assertEqual(self.store.counts()['evidence'],1)
            self.assertTrue(all(x['options']['num_ctx']==8192 for x in received))
            self.assertFalse([e for e in self.store.events(self.pid,10000) if e['kind']=='WORK_ERROR'])
            self.assertTrue(all(json.loads(x['messages'][1]['content'])['instructions']==INSTRUCTIONS for x in received))
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_old_budget_requeue_replays_after_interrupted_state_save(self):
        engine, did = self.force_source(TEXT)
        old = self.store.load(self.pid)
        span = old['tasks'][0]['chunks'].pop(0)
        old['stage'] = 'select'
        self.store.record_work(span['key'], 'FAILED', task_id='t001', document_id=did,
            start=span['start'], end=span['end'], error='PromptBudgetError: old byte gate')
        self.store.save(old)
        transient = self.store.load(self.pid)
        engine.upgrade_budget_checkpoint(transient)
        # Crash before transient state/queue/version was committed.
        self.assertNotIn('budget_policy_version', self.store.load(self.pid))
        Engine(self.store, engine.model, engine.web).run(self.pid)
        self.assertEqual(self.store.counts()['evidence'], 1)
        self.assertEqual(self.store.work(span['key'])['status'], 'DONE')
