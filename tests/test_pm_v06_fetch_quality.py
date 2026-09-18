import unittest


class FetchQuality06(unittest.TestCase):
    def assess(self,body,**kw):
        from ollama_deep_researcher.pm_fetch_quality import assess_fetched_page
        d=dict(query_anchors=['nuclear','forging'],search_title='Nuclear forging capacity',
               search_snippet='Pressure vessel forging',fetched_title='Nuclear forging',body=body)
        d.update(kw)
        return assess_fetched_page(**d)

    def test_distinct_excerpts_and_exact_body_identity(self):
        from ollama_deep_researcher.pm_fetch_quality import search_excerpt_key, document_body_key, reuse_key
        url='https://example.org/a'
        self.assertNotEqual(search_excerpt_key(url,'capacity 500 t'),search_excerpt_key(url,'capacity 600 t'))
        self.assertEqual(search_excerpt_key(url+'?utm_source=test','capacity 500 t'),search_excerpt_key(url,'capacity 500 t'))
        self.assertNotEqual(document_body_key('m'),document_body_key('M'))
        self.assertNotEqual(document_body_key('a b'),document_body_key('a  b'))
        self.assertNotEqual(reuse_key('body','2025 results'),reuse_key('body','2026 results'))

    def test_cookie_error_and_grammar_pages_deferred_without_model(self):
        self.assertEqual(self.assess('Access Denied. Request forbidden.').status,'ERROR_PAGE_DEFERRED')
        self.assertEqual(self.assess('We use cookies. Accept all cookies. Privacy preferences.').status,'LOW_SIGNAL_DEFERRED')
        self.assertIn(self.assess('English grammar conjunctions and or nor but.',fetched_title='English lesson').status,
                      ('LOW_SIGNAL_DEFERRED','CONTENT_MISMATCH_DEFERRED'))

    def test_relevant_content_not_blocked_by_title_mismatch(self):
        self.assertEqual(self.assess('Our nuclear forging press capacity is 14000 tonnes.',fetched_title='Annual report').status,'READY')

    def test_short_fact_not_dropped_just_for_length(self):
        self.assertEqual(self.assess('Nuclear forging: 42 t.').status,'READY')

    def test_neutral_cross_language_title_only_is_uncertain_not_error(self):
        result=self.assess('\uc6d0\uc790\ub825 \ub2e8\uc870\ud488 \uc81c\uc870\ub2a5\ub825',fetched_title='Annual Report')
        self.assertNotEqual(result.status,'ERROR_PAGE_DEFERRED')
        self.assertIn('NOT_PROOF_OF_IRRELEVANCE',result.reasons)
