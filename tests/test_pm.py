"""Synthetic offline fixtures; these assertions do not measure research accuracy."""
import json
import tempfile
import unittest
from pathlib import Path
from ollama_deep_researcher.pm_types import Settings, canonical_url, parse_json, gate
from ollama_deep_researcher.pm_store import Store, worker_lock
from ollama_deep_researcher.pm_engine import Engine

TEXT = 'The test press has a capacity of 100 t.'

def claim(text=TEXT, value='100', period='2025'):
    return dict(entity='TEST', metric='press', value=value, unit='t', period=period, scope='capacity', quote=text)

def review(ids, passed=True):
    return {'checks': [{'criterion': 'c1', 'passed': passed, 'evidence_ids': ids, 'reason': 'Synthetic check'}], 'issues': []}

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = Settings(allowed_domains=['example.org', 'example.net'], min_sources=1, min_tasks=1, max_tasks=3, max_attempts=2)
        self.store = Store(self.root)
        self.pid = self.store.create('Synthetic fixture study', self.cfg)
        self.task = {'criteria': [{'id': 'c1', 'text': 'Identify capacity'}]}

    def evidence(self, url='https://example.org/a', text=TEXT, value='100', period='2025'):
        doc = self.store.add_document(url, 'Synthetic fixture', text)
        return self.store.add_evidence(doc, claim(text, value, period))

class Contracts(Base):
    def test_json_repairs_fence_and_thinking(self):
        self.assertEqual(parse_json('<think>draft</think>\n```json\n{"a":1}\n```'), {'a': 1})
    def test_json_array_rejected(self):
        with self.assertRaises(ValueError): parse_json('[1,2]')
    def test_canonical_url_preserves_semantic_query(self):
        self.assertEqual(canonical_url('https://EXAMPLE.org/a?id=7&utm_source=x#tag'), 'https://example.org/a?id=7')
    def test_invalid_retry_limit(self):
        with self.assertRaises(ValueError): Settings(max_attempts=0)
    def test_allowlist_domain_boundary(self):
        self.assertFalse(self.cfg.allows('https://example.org.evil.test/a'))
        self.assertTrue(self.cfg.allows('https://sub.example.org/a'))
    def test_unknown_evidence_fails_gate(self):
        ok, reasons, ids = gate(self.task, [], review(['invented']), self.cfg)
        self.assertFalse(ok); self.assertTrue(reasons); self.assertEqual(ids, [])
    def test_critic_cannot_omit_criterion(self):
        eid = self.evidence()
        self.task['criteria'].append({'id': 'c2', 'text': 'Identify date'})
        self.assertFalse(gate(self.task, self.store.get_evidence([eid]), review([eid]), self.cfg)[0])
    def test_string_true_not_boolean_pass(self):
        eid = self.evidence()
        self.assertFalse(gate(self.task, self.store.get_evidence([eid]), review([eid], 'true'), self.cfg)[0])
    def test_valid_evidence_gate(self):
        eid = self.evidence()
        self.assertTrue(gate(self.task, self.store.get_evidence([eid]), review([eid]), self.cfg)[0])

class EvidenceStore(Base):
    def test_idempotent_document_and_evidence(self):
        self.assertEqual(self.evidence(), self.evidence())
        self.assertEqual(self.store.counts(), {'documents': 1, 'evidence': 1})
    def test_fabricated_quote_rejected(self):
        doc = self.store.add_document('https://example.org/a', 'Fixture', 'Only known source text')
        with self.assertRaises(ValueError): self.store.add_evidence(doc, claim())
    def test_same_identity_different_value_is_conflict(self):
        ids = [self.evidence(), self.evidence('https://example.net/b', 'The test press has a capacity of 120 t.', '120')]
        self.assertTrue(all(e['conflict'] for e in self.store.get_evidence(ids)))
    def test_different_period_is_not_conflict(self):
        ids = [self.evidence(), self.evidence('https://example.net/b', 'Capacity is 120 t in 2026.', '120', '2026')]
        self.assertFalse(any(e['conflict'] for e in self.store.get_evidence(ids)))
    def test_mirrored_body_not_two_independent_sources(self):
        ids = [self.evidence(), self.evidence('https://example.net/copy')]
        self.cfg.min_sources = 2
        self.assertFalse(gate(self.task, self.store.get_evidence(ids), review(ids), self.cfg)[0])
        self.assertEqual(self.store.counts()['documents'], 2)
    def test_checkpoint_survives_reopen(self):
        s = self.store.load(self.pid); s['note'] = 'durable'; self.store.save(s)
        self.assertEqual(Store(self.root).load(self.pid)['note'], 'durable')
    def test_bounded_database_retrieval(self):
        eid = self.evidence()
        rows = self.store.retrieve('TEST press', limit=2)
        self.assertIn(eid, [r['id'] for r in rows]); self.assertLessEqual(len(rows), 2)
    def test_export_contains_sources_and_evidence(self):
        eid = self.evidence()
        s = self.store.load(self.pid)
        s['tasks'] = [dict(id='t1', title='Fixture', criteria=self.task['criteria'], status='DONE', evidence_ids=[eid], attempts=1, feedback=[])]
        self.store.save(s); folder = self.store.export(self.pid)
        data = json.loads((folder / 'evidence_pack.json').read_text(encoding='utf-8'))
        self.assertEqual(data['evidence'][0]['id'], eid)
        self.assertEqual(data['sources'][0]['body'], TEXT)
        self.assertNotIn('VERIFIED', (folder / 'progress.md').read_text(encoding='utf-8'))

class FakeModel:
    def __init__(self, reject=False): self.roles, self.reject = [], reject
    def ask(self, role, payload, check):
        check(); self.roles.append(role)
        if role == 'planner':
            return {'tasks': [{'title': 'TEST press capacity', 'query': 'TEST press', 'criteria': ['Identify rated capacity with primary text']}]}
        if role == 'researcher': return {'query': 'TEST press capacity ' + str(len(self.roles))}
        if role == 'extractor': return {'claims': [claim()]}
        if role == 'critic': return review([e['id'] for e in payload['evidence']], not self.reject)
        if role == 'writer':
            return {'summary': 'Synthetic finding ' + ' '.join('[' + x + ']' for x in payload['evidence_ids']),
                    'evidence_ids': payload['evidence_ids'], 'limitations': ['Synthetic fixture, not market research']}
        raise AssertionError(role)

class FakeWeb:
    def search(self, query): return [{'url': 'https://example.org/a', 'title': 'Synthetic', 'content': 'Snippet is not evidence'}]
    def fetch(self, url): return TEXT

class Flow(Base):
    def run_pm(self, model=None, web=None):
        Engine(self.store, model or FakeModel(), web or FakeWeb()).run(self.pid)
        return self.store.load(self.pid)
    def test_all_five_roles_execute(self):
        model = FakeModel(); s = self.run_pm(model)
        self.assertEqual(s['status'], 'COMPLETED_REVIEW_REQUIRED'); self.assertEqual(s['tasks'][0]['status'], 'DONE')
        self.assertEqual(set(model.roles), {'planner', 'researcher', 'extractor', 'critic', 'writer'})
    def test_failed_critic_blocks_after_retry_limit(self):
        s = self.run_pm(FakeModel(True))
        self.assertEqual(s['status'], 'PARTIAL'); self.assertEqual(s['tasks'][0]['status'], 'BLOCKED'); self.assertEqual(s['tasks'][0]['attempts'], 2)
    def test_pause_then_resume(self):
        engine = Engine(self.store, FakeModel(), FakeWeb()); engine.step(self.pid)
        self.store.control(self.pid, 'PAUSE'); before = self.store.load(self.pid)['calls']; engine.run(self.pid)
        self.assertEqual(self.store.load(self.pid)['calls'], before); self.assertEqual(self.store.load(self.pid)['status'], 'PAUSED')
        self.store.control(self.pid, 'RUN'); engine.run(self.pid)
        self.assertEqual(self.store.load(self.pid)['status'], 'COMPLETED_REVIEW_REQUIRED')
    def test_new_engine_resumes_saved_stage(self):
        engine = Engine(self.store, FakeModel(), FakeWeb()); engine.step(self.pid); engine.step(self.pid)
        model = FakeModel(); Engine(Store(self.root), model, FakeWeb()).run(self.pid)
        self.assertNotIn('planner', model.roles); self.assertEqual(self.store.load(self.pid)['status'], 'COMPLETED_REVIEW_REQUIRED')
    def test_no_sources_is_not_success(self):
        class Empty(FakeWeb):
            def search(self, query): return []
        self.assertEqual(self.run_pm(web=Empty())['status'], 'PARTIAL')
    def test_global_call_budget(self):
        s = self.store.load(self.pid); s['settings']['max_calls'] = 1; self.store.save(s)
        s = self.run_pm(); self.assertEqual(s['calls'], 1); self.assertEqual(s['status'], 'BUDGET_EXHAUSTED')
    def test_global_search_budget(self):
        s = self.store.load(self.pid); s['searches'] = s['settings']['max_searches']; self.store.save(s)
        self.assertEqual(self.run_pm()['status'], 'BUDGET_EXHAUSTED')
    def test_model_error_is_bounded_and_saved(self):
        class Broken(FakeModel):
            def ask(self, role, payload, check): raise ValueError('malformed JSON fixture')
        s = self.run_pm(Broken()); self.assertEqual(s['status'], 'ERROR'); self.assertLessEqual(s['calls'], 3); self.assertTrue(s['last_error'])

class Regressions(Base):
    def test_more_than_200_records_are_not_dropped(self):
        ids = [self.evidence(f'https://example.org/{i}', f'Test press number {i} has capacity 100 t.') for i in range(205)]
        self.assertEqual(len(self.store.get_evidence(ids)), 205)
    def test_writer_covers_each_task(self):
        s = self.store.load(self.pid); s['stage'] = 'writer'
        s['tasks'] = []
        for i in range(3):
            eid = self.evidence(f'https://example.org/{i}')
            s['tasks'].append(dict(id=f't{i}', title=f'Task {i}', status='DONE', attempts=1, criteria=self.task['criteria'], feedback=[], evidence_ids=[eid], accepted_ids=[eid]))
        self.store.save(s); model = FakeModel(); Engine(self.store, model, FakeWeb()).run(self.pid)
        self.assertEqual(model.roles.count('writer'), 3); self.assertEqual(len(self.store.load(self.pid)['draft_sections']), 3)
    def test_later_conflict_invalidates_completed_task(self):
        eid = self.evidence(); self.evidence('https://example.net/b', 'The test press has a capacity of 120 t.', '120')
        s = self.store.load(self.pid); s['stage'] = 'writer'; s['tasks'] = [dict(id='t1', title='TEST', status='DONE', attempts=1, criteria=[], feedback=[], evidence_ids=[eid], accepted_ids=[eid])]
        self.store.save(s); Engine(self.store, FakeModel(), FakeWeb()).run(self.pid)
        self.assertEqual(self.store.load(self.pid)['status'], 'PARTIAL')
    def test_second_worker_refused(self):
        with worker_lock(self.root):
            with self.assertRaises(RuntimeError):
                with worker_lock(self.root): pass
    def test_unknown_inline_citation_rejected(self):
        class BadWriter(FakeModel):
            def ask(self, role, payload, check):
                result = super().ask(role, payload, check)
                if role == 'writer': result['summary'] = 'Unsupported [e-invented123].'
                return result
        Engine(self.store, BadWriter(), FakeWeb()).run(self.pid)
        self.assertEqual(self.store.load(self.pid)['status'], 'ERROR')
