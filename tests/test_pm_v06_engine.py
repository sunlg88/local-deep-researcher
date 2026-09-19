import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from v06_fixtures import Model06, Web06, TEXT


class Engine06Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=Workspace(self.tmp.name)

    def project(self,**kw):
        return self.w.create_v06('TEST press capacity',Settings(max_attempts=kw.pop('max_attempts',1),draft_enabled=False,**kw))

    def run06(self,pid,model=None,web=None):
        from ollama_deep_researcher.pm_engine_v06 import EngineV06
        self.model=model or Model06();self.web=web or Web06()
        e=EngineV06(self.w.open(pid),self.model,self.web)
        with patch.object(e.pacer,'wait',return_value=.1): e.run(pid)
        return e

    def test_version_selection_and_baseline_not_rewritten(self):
        old=self.w.create('old',Settings())
        before=self.w._db_path(old).read_bytes()
        new=self.w.continue_as_v06(old)
        self.assertEqual(self.w.load(new)['engine_version'],6)
        self.assertEqual(self.w.load(new)['reference_projects'],[old])
        self.assertEqual(self.w._db_path(old).read_bytes(),before)
        self.assertEqual(self.w.load(old)['engine_version'],5)

    def test_end_to_end_query_compiled_then_exact_evidence(self):
        pid=self.project()
        self.run06(pid)
        s=self.w.load(pid)
        self.assertEqual(s['status'],'NO_NEW_WORK')
        self.assertEqual(self.w.open(pid).counts()['evidence'],1)
        self.assertIn('"TEST press"',self.web.queries[0])
        self.assertTrue(all(p.get('_pm_version')==6 for role,p in self.model.payloads))

    def test_grammar_page_preserved_without_extractor_call(self):
        pid=self.project()
        self.run06(pid,web=Web06(bodies={'https://example.org/a':'English conjunctions and or but nor. Cultural communication.'}))
        st=self.w.open(pid)
        self.assertEqual(st.counts()['documents'],1)
        self.assertFalse([p for role,p in self.model.payloads if role=='extractor'])
        from ollama_deep_researcher.pm_v06_store import plans
        self.assertEqual(plans(st)[0]['status'],'DEFERRED')
        self.assertEqual(st.counts()['evidence'],0)

    def test_same_body_same_scope_different_urls_only_one_extraction(self):
        pid=self.project()
        hits=[{'url':u,'title':'TEST press','content':TEXT} for u in ['https://example.org/a','https://other.example/b']]
        self.run06(pid,web=Web06(hits))
        self.assertEqual(len(self.web.fetches),2)
        self.assertEqual(len([1 for role,p in self.model.payloads if role=='extractor']),1)
        self.assertEqual(self.w.open(pid).counts(),{'documents':2,'evidence':1})

    def test_quality_three_queries_from_one_model_intent(self):
        pid=self.project(optimization_mode='quality')
        self.run06(pid,web=Web06(hits=[]))
        self.assertEqual(len(self.web.queries),3)
        self.assertEqual(len([1 for role,p in self.model.payloads if role=='researcher']),1)
        self.assertTrue(all(' OR ' not in q for q in self.web.queries))

    def test_reference_scanned_once_and_distractor_not_copied(self):
        old=self.w.create('TEST press reference',Settings())
        st=self.w.open(old)
        st.add_document('https://example.org/g','English grammar','English and or but conjunction words. TEST once.')
        st.add_document('https://example.org/f','TEST press',TEXT)
        before=st.path.read_bytes()
        pid=self.w.continue_as_v06(old,Settings(max_attempts=1,draft_enabled=False))
        self.run06(pid,web=Web06(hits=[]))
        self.assertEqual(st.path.read_bytes(),before)
        self.assertEqual(self.w.open(pid).counts()['documents'],1)
        self.assertEqual(self.w.open(pid).counts()['evidence'],1)
        from ollama_deep_researcher.pm_v06_store import reference_rows
        self.assertEqual(len(reference_rows(self.w.open(pid))),2)

    def test_multiple_tasks_share_evidence(self):
        pid=self.project(min_tasks=3,max_tasks=3)
        self.run06(pid,model=Model06(task_count=3))
        st=self.w.open(pid)
        self.assertEqual(len([1 for role,p in self.model.payloads if role=='extractor']),1)
        e=st.all_evidence()[0]
        self.assertEqual(st.evidence_task_ids(e['id']),['t001','t002','t003'])

    def test_model_result_ready_survives_interruption_without_second_call(self):
        from ollama_deep_researcher.pm_engine_v06 import EngineV06
        pid=self.project();st=self.w.open(pid);m=Model06();web=Web06();e=EngineV06(st,m,web)
        real=st.link_evidence_task
        with patch.object(e.pacer,'wait',return_value=.1):
            with patch.object(st,'link_evidence_task',side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt): e.run(pid)
            e.run(pid)
        self.assertEqual(st.counts()['evidence'],1)
        self.assertEqual(len([1 for role,p in m.payloads if role=='extractor']),1)

    def test_replay_export_contains_all_events_not_last_500(self):
        pid=self.project();st=self.w.open(pid)
        for i in range(510): st.log(pid,'FIXTURE',json.dumps({'i':i}))
        self.run06(pid)
        f=st.export(pid)
        rows=(f/'events.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertGreater(len(rows),510)
        self.assertTrue((f/'replay/manifest.json').is_file())
        self.assertTrue((f/'retrieval_decisions.json').is_file())
        self.assertTrue((f/'efficiency_metrics.json').is_file())

    def test_settings_validate_modes(self):
        self.assertEqual(Settings().optimization_mode,'balanced')
        self.assertEqual(Settings().semantic_rerank,'off')
        with self.assertRaises(ValueError): Settings(optimization_mode='unlimited')
        with self.assertRaises(ValueError): Settings(semantic_rerank='on')

    def test_stop_policy_never_treats_pending_or_access_failures_as_zero_yield(self):
        from ollama_deep_researcher.pm_engine_v06 import next_task_action
        self.assertEqual(next_task_action({'zero_yield_streak':2},'balanced'),'CHANGE_STRATEGY')
        self.assertEqual(next_task_action({'zero_yield_streak':4,'strategy_changes':2},'balanced'),'STALLED')
        self.assertEqual(next_task_action({'zero_yield_streak':4,'strategy_changes':2,'pending_work':True},'balanced'),'CONTINUE')
