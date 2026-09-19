"""Backend queries are code-rendered; source policies never come from a model."""
import unittest


class Query06(unittest.TestCase):
    def data(self, **overrides):
        d=dict(entity='Sheffield Forgemasters',gap='SMR pressure vessel contracts',
               keywords=['BWRX-300','forging'],strategy='exact_entity',language='en',site_hint='')
        d.update(overrides)
        return d

    def test_single_entity_compiler_and_variants(self):
        from ollama_deep_researcher.pm_query_policy import validate_intent, compile_query, query_variants
        i=validate_intent(self.data(),set())
        q=compile_query(i,'duckduckgo')
        self.assertIn('"Sheffield Forgemasters"',q)
        self.assertIn('SMR pressure vessel contracts',q)
        self.assertNotIn(' OR ',q)
        self.assertEqual(len(query_variants(i,'efficient',0)),1)
        self.assertLessEqual(len(query_variants(i,'quality',0)),3)
        self.assertNotEqual(query_variants(i,'balanced',0),query_variants(i,'balanced',2))

    def test_unknown_domain_rejected_boundary_not_suffix_spoof(self):
        from ollama_deep_researcher.pm_query_policy import validate_intent
        for hint in ['guessed.example','allowed.example.evil.org','https://allowed.example','user@allowed.example']:
            with self.assertRaises(ValueError):
                validate_intent(self.data(site_hint=hint),{'allowed.example'})
        i=validate_intent(self.data(site_hint='docs.allowed.example'),{'allowed.example'})
        self.assertEqual(i.site_hint,'docs.allowed.example')

    def test_arbitrary_boolean_operators_rejected(self):
        from ollama_deep_researcher.pm_query_policy import validate_intent
        for d in [self.data(entity='Sheffield OR JSW OR Doosan'),
                  self.data(gap='SMR AND NuScale OR BWRX-300'),
                  self.data(keywords=['site:evil.example']),
                  self.data(keywords=['x -site:allowed.example']),
                  self.data(query='extra field')]:
            with self.assertRaises(ValueError): validate_intent(d,set())

    def test_quoted_name_with_natural_and_not_rejected(self):
        from ollama_deep_researcher.pm_query_policy import validate_intent, compile_query
        i=validate_intent(self.data(entity='Research and Development Ltd'),set())
        self.assertIn('"Research and Development Ltd"',compile_query(i,'duckduckgo'))

    def test_numerical_conditions_not_silently_removed(self):
        from ollama_deep_researcher.pm_query_policy import validate_intent, query_variants
        i=validate_intent(self.data(gap='2026 contracts 500 MW',keywords=['BWRX-300','not cancelled']),set())
        for q in query_variants(i,'quality',0):
            self.assertIn('2026',q)
            self.assertIn('500 MW',q)
            self.assertIn('not cancelled',q)

    def test_bad_mode_empty_values_and_excess_length_rejected(self):
        from ollama_deep_researcher.pm_query_policy import validate_intent, query_variants
        for d in [self.data(entity=''),self.data(keywords=[]),self.data(gap='x'*201),self.data(language='anything')]:
            with self.assertRaises(ValueError): validate_intent(d,set())
        i=validate_intent(self.data(),set())
        with self.assertRaises(ValueError): query_variants(i,'unbounded',0)
