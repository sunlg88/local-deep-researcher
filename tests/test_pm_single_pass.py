"""Source work is project-scoped; linking a claim is not another extraction."""
import json
import tempfile
import unittest
from unittest.mock import patch
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_engine_v05 import EngineV05
from ollama_deep_researcher import pm_research_metrics as metrics
from test_pm import TEXT, claim
from v05_fixtures import Model05,Web05


class SinglePass(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=Workspace(self.tmp.name)
        self.cfg=Settings(min_tasks=8,max_tasks=8,max_attempts=1,draft_enabled=False)
        self.pid=self.w.create('TEST press',self.cfg)
        self.st=self.w.open(self.pid)
    def run_pm(self,model=None,web=None):
        model=model or Model05(task_count=8)
        web=web or Web05()
        EngineV05(self.st,model,web).run(self.pid)
        return model,web

    def test_eight_tasks_discover_same_doc_only_one_extraction(self):
        m,w=self.run_pm()
        self.assertEqual(sum(r=='extractor' for r,p in m.payloads),1)
        self.assertEqual(len(w.fetches),1)
        self.assertEqual(self.st.counts()['evidence'],1)
        state=self.st.load(self.pid)
        self.assertTrue(all(len(t['evidence_ids'])==1 for t in state['tasks']))
        eid=self.st.all_evidence()[0]['id']
        self.assertEqual(len(self.st.evidence_task_ids(eid)),8)

    def test_unknown_task_id_never_becomes_adopted_evidence(self):
        class Wrong(Model05):
            def ask(self,r,p,c):
                result=super().ask(r,p,c)
                if r=='extractor' and result['claims']:
                    result['claims'][0]['task_ids']=['t999']
                return result
        self.run_pm(Wrong(task_count=8))
        self.assertEqual(self.st.counts()['evidence'],0)
        self.assertEqual(self.st.counts()['documents'],1)
        self.assertTrue(any('task' in str(x.get('error','')) for x in self.st.processing()))

    def test_distinct_task_reviews_do_not_overwrite_each_other(self):
        self.run_pm()
        self.assertTrue(hasattr(self.st,'review_evidence_task'))
        eid=self.st.all_evidence()[0]['id']
        self.st.review_evidence_task(eid,'t001','NEEDS_REVIEW','Not supported for first task')
        self.st.review_evidence_task(eid,'t002','REVIEWED_SUPPORT','Supports second task')
        self.assertEqual(self.st.evidence_task_reviews(eid)['t001']['status'],'NEEDS_REVIEW')
        self.assertEqual(self.st.evidence_task_reviews(eid)['t002']['status'],'REVIEWED_SUPPORT')
        self.assertNotEqual(self.st.all_evidence()[0]['review_status'],'REVIEWED_SUPPORT')

    def test_uncertain_claim_is_preserved_without_task_adoption(self):
        class Uncertain(Model05):
            def ask(self,r,p,c):
                result=super().ask(r,p,c)
                if r=='extractor':
                    result.update(relevance='uncertain',claims=[dict(claim(),task_ids=[])])
                return result
        self.run_pm(Uncertain(task_count=8))
        self.assertEqual(self.st.counts()['evidence'],1)
        state=self.st.load(self.pid)
        self.assertTrue(all(not t['evidence_ids'] for t in state['tasks']))
        self.assertEqual(self.st.all_evidence()[0]['review_status'],'RELEVANCE_DISPUTED')

    def test_long_source_tail_preserved_and_ranges_not_task_multiplied(self):
        body='Background words. '*550+'\n\n'+TEXT
        m,w=self.run_pm(web=Web05(text=body))
        ranges=[p['source_range'] for r,p in m.payloads if r=='extractor']
        self.assertEqual(len(ranges),len({(p['start'],p['end']) for p in ranges}))
        covered=bytearray(len(body))
        for p in ranges: covered[p['start']:p['end']]=b'1'*(p['end']-p['start'])
        self.assertTrue(all(covered))
        self.assertEqual(self.st.counts()['evidence'],1)
        self.assertEqual(self.st.all_documents()[0]['body'],body)
        self.assertEqual(metrics.project_metrics(self.st)['unique_evidence'],1)

    def test_committed_result_replayed_without_call_after_interruption(self):
        m=Model05(task_count=8);w=Web05()
        e=EngineV05(self.st,m,w)
        for _ in range(30):
            if self.st.load(self.pid)['stage']=='extract': break
            e.step(self.pid)
        before=self.st.load(self.pid)
        self.assertIn('document_queue',before)
        e.step(self.pid)
        self.st.save(before)
        new=Model05(task_count=8)
        EngineV05(self.st,new,w).run(self.pid)
        self.assertEqual(sum(r=='extractor' for r,p in new.payloads),0)
        self.assertTrue(all(t['evidence_ids'] for t in self.st.load(self.pid)['tasks']))

    def test_resume_cached_model_response_after_link_interruption(self):
        self.assertTrue(hasattr(self.st,'link_evidence_task'))
        m=Model05(task_count=8); w=Web05();e=EngineV05(self.st,m,w)
        for _ in range(30):
            if self.st.load(self.pid)['stage']=='extract': break
            e.step(self.pid)
        with patch.object(self.st,'link_evidence_task',side_effect=OSError(28,'disk full')):
            e.step(self.pid)
        s=self.st.load(self.pid)
        self.assertEqual(s['status'],'STORAGE_ERROR')
        s['status']='PENDING';self.st.save(s)
        new=Model05(task_count=8)
        EngineV05(self.st,new,w).run(self.pid)
        self.assertEqual(sum(r=='extractor' for r,p in new.payloads),0)
        self.assertEqual(self.st.counts()['evidence'],1)
        self.assertTrue(all(t['evidence_ids'] for t in self.st.load(self.pid)['tasks']))

    def test_completed_zero_yield_is_fed_back_not_research_pending_forever(self):
        cfg=Settings(max_attempts=2,draft_enabled=False)
        self.pid=self.w.create('TEST press',cfg);self.st=self.w.open(self.pid)
        m=Model05(relevant=False); self.run_pm(m)
        f=metrics.task_search_feedback(self.st,'t001',20)
        self.assertTrue(any(r['outcome']=='ZERO_YIELD' for r in f))
