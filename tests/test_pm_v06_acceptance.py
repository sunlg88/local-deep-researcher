"""Production engines, same synthetic pages/oracle, originals and source coverage gates."""
import tempfile
import unittest
from v06_replay_fixtures import run_comparison


class Acceptance06(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.result=run_comparison(cls.tmp.name)

    def test_replay_has_no_missing_labelled_facts_or_lost_originals(self):
        for name,item in self.result['cases'].items():
            with self.subTest(case=name):
                self.assertEqual(item['candidate_validation']['missing_facts'],[])
                self.assertTrue(item['candidate_validation']['originals_preserved'])
                self.assertEqual(item['candidate_validation']['status'],'NO_NEW_WORK')
                self.assertGreaterEqual(item['candidate']['unique_relevant_documents'],item['baseline']['unique_relevant_documents'])

    def test_reduces_irrelevant_calls_and_estimated_tokens_on_controlled_corpus(self):
        rows=list(self.result['cases'].values())
        bi=sum(x['baseline']['irrelevant_extractions'] for x in rows)
        ci=sum(x['candidate']['irrelevant_extractions'] for x in rows)
        bt=sum(x['baseline']['estimated_extractor_tokens'] for x in rows)
        ct=sum(x['candidate']['estimated_extractor_tokens'] for x in rows)
        self.assertGreater(bi,0)
        self.assertLessEqual(ci,bi*.5)
        self.assertLessEqual(ct,bt*.65)

    def test_never_labels_estimates_as_measured_tokens_or_live_quality(self):
        self.assertFalse(self.result['actual_ollama_inference'])
        for row in self.result['cases'].values():
            if row['candidate']['extractor_calls']:
                self.assertIsNone(row['candidate']['extractor_prompt_tokens'])
            self.assertIsNone(row['extractor_prompt_token_reduction'])
