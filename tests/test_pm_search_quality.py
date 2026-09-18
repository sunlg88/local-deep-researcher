"""Deterministic filtering must not confuse ranking with a truth judgement."""
import importlib
import unittest
from ollama_deep_researcher.pm_types import Settings


class SearchQuality(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ollama_deep_researcher.pm_search_quality'))
        self.q=importlib.import_module('ollama_deep_researcher.pm_search_quality')
        self.cfg=Settings()

    def intent(self, query='"TEST press" nuclear', anchors=None):
        return self.q.parse_search_intent(query,'exact_entity',anchors or ['TEST','press'])

    def test_site_cn_filters_microsoft_and_lookalike(self):
        intent=self.intent('TEST press site:cn')
        for url in ['https://microsoft.com/x','https://foo.cn.evil.com/x']:
            r=self.q.score_hit(intent,{'url':url,'title':'TEST press'},self.cfg)
            self.assertFalse(r.accepted)
            self.assertIn('SITE_MISMATCH',r.reasons)
        self.assertTrue(self.q.score_hit(intent,{'url':'https://example.com.cn/a'},self.cfg).accepted)

    def test_negative_site_not_treated_as_positive(self):
        intent=self.intent('TEST press -site:example.com')
        self.assertFalse(self.q.score_hit(intent,{'url':'https://sub.example.com/x'},self.cfg).accepted)
        self.assertTrue(self.q.score_hit(intent,{'url':'https://other.org/x'},self.cfg).accepted)

    def test_multiple_site_union_and_path_scope(self):
        i=self.intent('TEST (site:one.org/docs OR site:two.org)')
        self.assertTrue(self.q.score_hit(i,{'url':'https://one.org/docs/a'},self.cfg).accepted)
        self.assertFalse(self.q.score_hit(i,{'url':'https://one.org/unrelated'},self.cfg).accepted)
        self.assertTrue(self.q.score_hit(i,{'url':'https://two.org/x'},self.cfg).accepted)

    def test_invalid_site_does_not_silently_remove_constraint(self):
        with self.assertRaises(ValueError): self.intent('TEST site:')
        with self.assertRaises(ValueError): self.intent('TEST site:*.example.org')

    def test_weak_hit_is_retained_but_not_promoted_as_fact(self):
        d=self.q.score_hit(self.intent(),{'url':'https://other.org/a','title':'Catalog'},self.cfg)
        self.assertTrue(d.accepted)
        self.assertIn('LOW_LEXICAL_MATCH',d.reasons)
        self.assertLess(d.score,2)

    def test_relevant_snippet_beats_unrelated_title(self):
        i=self.intent()
        a=self.q.score_hit(i,{'url':'https://one.org/x','title':'TEST press nuclear','content':'equipment'},self.cfg)
        b=self.q.score_hit(i,{'url':'https://two.org/x','title':'PowerShell VM help'},self.cfg)
        self.assertGreater(a.score,b.score)

    def test_diversity_limits_host_and_prefers_other_host_first(self):
        i=self.intent()
        hits=[{'url':f'https://one.org/{x}','title':'TEST press'} for x in range(5)]+[
            {'url':'https://two.org/1','title':'TEST press'}, {'url':'https://three.org/1','title':'TEST press'}]
        selected=self.q.select_hits(i,hits,self.cfg,limit=5)
        hosts=[d.host for h,d in selected]
        self.assertEqual(hosts.count('one.org'),2)
        self.assertEqual(len(set(hosts[:3])),3)

    def test_weak_fallback_is_one_not_entire_irrelevant_corpus(self):
        hits=[{'url':f'https://h{x}.org/a','title':'unrelated'} for x in range(8)]
        self.assertEqual(len(self.q.select_hits(self.intent(),hits,self.cfg,limit=3)),1)

    def test_near_duplicate_word_order_and_different_constraints(self):
        q=self.q.near_duplicate_query
        self.assertTrue(q('TEST press nuclear capacity','nuclear capacity press TEST'))
        self.assertFalse(q('TEST press capacity 2025','TEST press capacity 2026'))
        self.assertFalse(q('TEST press site:one.org','TEST press site:two.org'))
        self.assertFalse(q('TEST press -site:one.org','TEST press site:one.org'))
        self.assertFalse(q('TEST press licensed','TEST press not licensed'))
        self.assertFalse(q('"Company A" nuclear forge','"Company B" nuclear forge'))

    def test_normalize_backend_keys_preserves_snippet_and_original_url(self):
        hit=self.q.normalize_hit({'href':'https://example.org/a','body':'TEST press','title':'Product'})
        self.assertEqual(hit['url'],'https://example.org/a')
        self.assertEqual(hit['content'],'TEST press')

    def test_disallowed_policy_and_nonweb_url_rejected(self):
        cfg=Settings(source_mode='allowlist',allowed_domains=['safe.org'])
        self.assertFalse(self.q.score_hit(self.intent(),{'url':'https://evil.org/a'},cfg).accepted)
        self.assertFalse(self.q.score_hit(self.intent(),{'url':'file:///secret'},self.cfg).accepted)

    def test_long_similar_queries_with_new_unquoted_entity_are_not_duplicates(self):
        common=' nuclear forging pressure vessel manufacturing capability equipment maximum capacity technology'
        self.assertFalse(self.q.near_duplicate_query('Alpha'+common,'Beta'+common))
