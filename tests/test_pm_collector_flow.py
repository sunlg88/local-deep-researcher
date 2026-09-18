"""End-to-end offline collector invariants, not model accuracy benchmarks."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_pm import FakeModel, FakeWeb, TEXT, claim
from ollama_deep_researcher.pm_engine import Engine
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings


class CollectorModel(FakeModel):
    def __init__(self, fail_role=None, relevance='relevant'):
        super().__init__()
        self.payloads = []
        self.fail_role = fail_role
        self.relevance = relevance

    def ask(self, role, payload, check):
        self.payloads.append((role, json.loads(json.dumps(payload))))
        if role == self.fail_role:
            raise ValueError('Generation hit output limit; incomplete response rejected')
        result = super().ask(role, payload, check)
        if role == 'extractor':
            result['relevance'] = self.relevance
            result['reason'] = 'Synthetic context assessment'
            result['claims'] = [claim()] if TEXT in payload['source_text'] else []
        return result


class CountingWeb(FakeWeb):
    def __init__(self):
        self.fetches = 0
        self.queries = []
    def search(self, query):
        self.queries.append(query)
        return super().search(query)
    def fetch(self, url):
        self.fetches += 1
        return super().fetch(url)


class CollectorFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.w = Workspace(self.tmp.name)
        self.cfg = Settings(min_tasks=1, max_tasks=3, max_attempts=2, min_sources=2)
        self.pid = self.w.create('Original immutable research question', self.cfg,
                                 'Original user constraint; do not replace it.')
        self.store = self.w.open(self.pid)

    def run_pm(self, model=None, web=None):
        model = model or CollectorModel()
        web = web or CountingWeb()
        Engine(self.store, model, web).run(self.pid)
        return self.w.load(self.pid), model, web

    def test_one_source_is_retained_without_mandatory_two_source_gate(self):
        s, model, web = self.run_pm()
        self.assertEqual(s['status'], 'NO_NEW_WORK')
        self.assertEqual(self.store.counts()['evidence'], 1)
        self.assertEqual(s['tasks'][0]['source_status'], 'SINGLE_SOURCE')
        self.assertTrue(s['tasks'][0]['evidence_ids'])
        self.assertNotIn('DONE', [t['status'] for t in s['tasks']])

    def test_original_goal_and_instructions_reach_every_role(self):
        _, model, _ = self.run_pm()
        self.assertEqual({r for r, p in model.payloads}, {'planner','researcher','extractor','critic','writer'})
        for role, payload in model.payloads:
            self.assertEqual(payload['topic'], 'Original immutable research question', role)
            self.assertIn('Original user constraint', payload['instructions'], role)

    def test_same_document_range_not_extracted_or_fetched_twice(self):
        _, model, web = self.run_pm()
        self.assertEqual(web.fetches, 1)
        self.assertEqual(sum(r == 'extractor' for r, p in model.payloads), 1)
        self.assertEqual(len(self.store.processing()), 1)

    def test_irrelevant_doc_kept_but_not_adopted_as_evidence(self):
        s, _, _ = self.run_pm(CollectorModel(relevance='irrelevant'))
        self.assertEqual(self.store.counts(), {'documents': 1, 'evidence': 0})
        self.assertEqual(s['tasks'][0]['evidence_ids'], [])
        self.assertEqual(self.store.document_links()[0]['status'], 'EXCLUDED')

    def test_critic_truncation_does_not_erase_sources_or_stop_research(self):
        s, _, _ = self.run_pm(CollectorModel(fail_role='critic'))
        self.assertEqual(s['status'], 'NO_NEW_WORK')
        self.assertEqual(self.store.counts()['evidence'], 1)
        self.assertEqual(self.store.all_evidence()[0]['review_status'], 'REVIEW_INCOMPLETE')
        self.assertTrue((self.store.export(self.pid) / 'documents').exists())

    def test_extractor_failure_preserves_original_and_records_failed_ranges(self):
        s, _, web = self.run_pm(CollectorModel(fail_role='extractor'))
        self.assertEqual(s['status'], 'NO_NEW_WORK')
        self.assertEqual(self.store.counts(), {'documents': 1, 'evidence': 0})
        self.assertEqual(web.fetches, 1)
        self.assertTrue(any(r['status'] == 'FAILED' for r in self.store.processing()))

    def test_planner_failure_falls_back_to_user_question_not_global_error(self):
        s, _, _ = self.run_pm(CollectorModel(fail_role='planner'))
        self.assertEqual(s['status'], 'NO_NEW_WORK')
        self.assertEqual(s['tasks'][0]['title'], s['topic'][:240])
        self.assertTrue(s.get('planner_fallback'))

    def test_writer_failure_leaves_evidence_and_no_false_draft(self):
        s, _, _ = self.run_pm(CollectorModel(fail_role='writer'))
        self.assertEqual(s['status'], 'NO_NEW_WORK')
        self.assertEqual(self.store.counts()['evidence'], 1)
        self.assertFalse(s.get('draft_sections'))
        self.assertTrue(s['tasks'][0].get('draft_error'))

    def test_long_source_tail_is_reached_and_ranges_are_checkpointed(self):
        class LongWeb(CountingWeb):
            def fetch(self, url):
                self.fetches += 1
                return ('Background material. ' * 400) + '\n\n' + TEXT
        s, model, web = self.run_pm(web=LongWeb())
        self.assertEqual(s['status'], 'NO_NEW_WORK')
        seen = [p['source_text'] for r, p in model.payloads if r == 'extractor']
        self.assertGreater(len(seen), 2)
        self.assertTrue(any(TEXT in text for text in seen))
        self.assertEqual(self.store.counts()['evidence'], 1)

    def test_unselected_project_never_supplies_search_evidence(self):
        other = self.w.create('TEST press', self.cfg)
        d = self.w.open(other).add_document('https://example.net/old', 'TEST press', TEXT)
        self.w.open(other).add_evidence(d, claim())
        class Empty(CountingWeb):
            def search(self, query): return []
        s, _, _ = self.run_pm(web=Empty())
        self.assertEqual(self.store.counts(), {'documents': 0, 'evidence': 0})
        self.assertFalse(s['tasks'][0]['evidence_ids'])

    def test_selected_reference_is_reextracted_not_trusted(self):
        a = self.w.create('TEST press reference', self.cfg)
        d = self.w.open(a).add_document('https://example.net/old', 'TEST press', TEXT)
        self.w.open(a).add_evidence(d, claim())
        self.pid = self.w.create('TEST press', self.cfg, reference_projects=[a])
        self.store = self.w.open(self.pid)
        class Empty(CountingWeb):
            def search(self, query): return []
        s, model, _ = self.run_pm(CollectorModel(relevance='irrelevant'), Empty())
        self.assertEqual(self.store.counts(), {'documents': 1, 'evidence': 0})
        self.assertTrue(any(r == 'extractor' for r, p in model.payloads))
        self.assertEqual(self.store.all_documents()[0]['metadata']['origin_project_id'], a)

    def test_two_tasks_receive_first_search_before_one_retries(self):
        class TwoTasks(CollectorModel):
            def ask(self, role, payload, check):
                if role == 'planner':
                    return {'tasks': [{'title': title, 'query': title, 'criteria': ['Inspect source']} for title in ['Alpha topic','Beta topic']]}
                return super().ask(role, payload, check)
        class Empty(CountingWeb):
            def search(self, query): return []
        _, model, _ = self.run_pm(TwoTasks(), Empty())
        order = [p['task'] for r,p in model.payloads if r == 'researcher']
        self.assertEqual(order[:2], ['Alpha topic', 'Beta topic'])

    def test_time_limit_is_not_called_completed_research(self):
        s = self.w.load(self.pid)
        s['settings']['time_limit_minutes'] = 1
        s['active_seconds'] = 60
        self.store.save(s)
        result, model, _ = self.run_pm()
        self.assertEqual(result['status'], 'TIME_LIMIT_REACHED')
        self.assertEqual(model.payloads, [])

    def test_fetch_error_retains_url_query_and_attempt_count(self):
        class Broken(CountingWeb):
            def fetch(self, url): raise TimeoutError('Synthetic transient timeout')
        s, _, _ = self.run_pm(web=Broken())
        self.assertEqual(s['status'], 'NO_NEW_WORK')
        source = self.store.sources()[0]
        self.assertEqual(source['url'], 'https://example.org/a')
        self.assertTrue(source['queries'])
        self.assertEqual(source['error_type'], 'TimeoutError')
        self.assertGreaterEqual(source['attempts'], 1)

    def test_resume_keeps_completed_range_history(self):
        model, web = CollectorModel(), CountingWeb()
        engine = Engine(self.store, model, web)
        for _ in range(30):
            engine.step(self.pid)
            if self.store.counts()['evidence']:
                break
        self.store.control(self.pid, 'PAUSE')
        engine.run(self.pid)
        self.store.control(self.pid, 'RUN')
        new = CollectorModel()
        Engine(self.w.open(self.pid), new, web).run(self.pid)
        self.assertFalse(any(r == 'extractor' for r,p in new.payloads))
        self.assertEqual(web.fetches, 1)

    def test_committed_range_is_replayed_after_checkpoint_interruption(self):
        model,web=CollectorModel(),CountingWeb()
        engine=Engine(self.store,model,web)
        before=None
        for _ in range(12):
            state=self.w.load(self.pid)
            if state['stage']=='extract':
                before=state;break
            engine.step(self.pid)
        self.assertIsNotNone(before)
        engine.step(self.pid)
        self.assertEqual(self.store.counts()['evidence'],1)
        # Simulate a crash after work/evidence commit but before the project checkpoint.
        self.store.save(before)
        new=CollectorModel()
        Engine(self.w.open(self.pid),new,web).run(self.pid)
        self.assertFalse(any(role=='extractor' for role,payload in new.payloads))
        self.assertEqual(len(self.w.load(self.pid)['tasks'][0]['evidence_ids']),1)

    def test_split_children_survive_checkpoint_interruption(self):
        class Long(CountingWeb):
            def fetch(self,url):return 'Context sentence. '*80+'\n'+TEXT
        model,web=CollectorModel(fail_role='extractor'),Long()
        engine=Engine(self.store,model,web)
        before=None
        for _ in range(12):
            state=self.w.load(self.pid)
            if state['stage']=='extract':before=state;break
            engine.step(self.pid)
        self.assertIsNotNone(before)
        original=before['tasks'][0]['chunks'][0]
        engine.step(self.pid)
        self.assertEqual(self.store.processing()[0]['status'],'SPLIT')
        self.store.save(before)
        new=CollectorModel()
        Engine(self.w.open(self.pid),new,web).run(self.pid)
        spans=[p['source_range'] for r,p in new.payloads if r=='extractor']
        self.assertTrue(spans)
        self.assertNotIn({'start':original['start'],'end':original['end']},spans)
        self.assertEqual(self.store.counts()['evidence'],1)

    def test_failed_ranges_and_single_source_status_are_explicit_in_handoff(self):
        self.run_pm(CollectorModel(fail_role='extractor'))
        text=(self.store.export(self.pid)/'unresolved.md').read_text()
        self.assertIn('FAILED',text)
        self.assertIn('output limit',text)
