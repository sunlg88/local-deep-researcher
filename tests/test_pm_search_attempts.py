"""Search evidence trail and metrics survive processes; counters are not model claims."""
import importlib
import json
import tempfile
import unittest
from ollama_deep_researcher.pm_store import Store
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_search_quality import HitDecision


class Attempts(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ollama_deep_researcher.pm_research_metrics'))
        self.m=importlib.import_module('ollama_deep_researcher.pm_research_metrics')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name)
        self.pid=self.store.create('TEST press',Settings())
        self.m.initialize(self.store)

    def attempt(self,task='t001'):
        return self.m.begin_search_attempt(self.store,self.pid,task,'TEST press','broad',['TEST','press'])

    def test_search_attempt_survives_reopen(self):
        a=self.attempt()
        self.m.finish_search_attempt(self.store,a,status='COMPLETED')
        again=Store(self.tmp.name)
        self.assertEqual(self.m.attempts(again)[0]['id'],a)
        self.assertEqual(self.m.attempts(again)[0]['query'],'TEST press')

    def test_decision_is_durable_and_updates_do_not_duplicate_hit(self):
        a=self.attempt(); h={'url':'https://example.org/a','title':'TEST','content':'snippet'}
        self.m.record_hit_decision(self.store,a,h,HitDecision(False,0,('SITE_MISMATCH',),'example.org'),rank=0)
        self.m.record_hit_decision(self.store,a,h,HitDecision(False,0,('SITE_MISMATCH',),'example.org'),rank=0)
        hits=self.m.hits(self.store,a)
        self.assertEqual(len(hits),1)
        self.assertIn('SITE_MISMATCH',hits[0]['reasons'])
        self.assertEqual(self.m.project_metrics(self.store)['prefilter_rejected'],1)

    def test_zero_yield_feedback_waits_for_pending_extraction(self):
        a=self.attempt(); did=self.store.add_document('https://example.org/a','TEST','Known original text.')
        self.m.link_attempt_document(self.store,a,did,True)
        self.m.finish_search_attempt(self.store,a,status='COMPLETED')
        f=self.m.task_search_feedback(self.store,'t001')[0]
        self.assertEqual(f['outcome'],'EXTRACTION_PENDING')
        self.m.finish_document(self.store,did)
        f=self.m.task_search_feedback(self.store,'t001')[0]
        self.assertEqual(f['outcome'],'ZERO_YIELD')

    def test_host_cooldown_bounded_and_404_not_host_ban(self):
        self.m.record_host_failure(self.store,'example.org',404,now_ts=1)
        self.m.record_host_failure(self.store,'example.org',404,now_ts=2)
        self.assertFalse(self.m.host_in_cooldown(self.store,'example.org',3))
        self.m.record_host_failure(self.store,'example.org',403,now_ts=3)
        self.assertFalse(self.m.host_in_cooldown(self.store,'example.org',4))
        self.m.record_host_failure(self.store,'example.org',451,now_ts=4)
        self.assertTrue(self.m.host_in_cooldown(self.store,'example.org',5))
        self.assertFalse(self.m.host_in_cooldown(self.store,'example.org',2000))
        self.assertFalse(self.m.host_in_cooldown(self.store,'different.org',5))

    def test_extraction_outcome_upsert_is_idempotent(self):
        a=self.attempt(); d=self.store.add_document('https://example.org/a','TEST','Original source text.')
        for _ in range(2):
            self.m.record_extraction(self.store,'key',d,0,21,'irrelevant',0,[])
        metrics=self.m.project_metrics(self.store)
        self.assertEqual(metrics['extractions'],1)
        self.assertEqual(metrics['irrelevant_extractions'],1)
        self.assertEqual(metrics['productive_extraction_ratio'],0)

    def test_empty_metrics_not_false_success(self):
        self.assertIsNone(self.m.project_metrics(self.store)['productive_extraction_ratio'])
        self.assertEqual(self.m.task_search_feedback(self.store,'t001'),[])
