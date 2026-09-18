"""Low-cost ranking must not invent cross-language or factual verification."""
import unittest


class Retrieval06(unittest.TestCase):
    def chunks(self):
        from ollama_deep_researcher.pm_chunking_v06 import build_chunks
        return build_chunks('English conjunctions and or but nor.\n\nNuclear forging press capacity 14000 tonnes.\n\nPressure vessel temperature 500 C.',target_chars=30,max_chars=70)

    def test_relevant_chunk_above_grammar_and_stable_ties(self):
        from ollama_deep_researcher.pm_retrieval_v06 import rank_chunks
        chunks=self.chunks()
        a=rank_chunks('nuclear forging press capacity',chunks,['forging','press'])
        byid={c.id:c for c in chunks}
        self.assertIn('forging press',byid[a[0].chunk_id].text)
        self.assertEqual(a,rank_chunks('nuclear forging press capacity',chunks,['forging','press']))
        grammar=next(r for r in a if 'conjunction' in byid[r.chunk_id].text)
        self.assertEqual(grammar.lexical_score,0)

    def test_progressive_selection_has_no_repeat_and_low_signal_not_selected(self):
        from ollama_deep_researcher.pm_retrieval_v06 import RankedChunk, select_progressive
        rows=[RankedChunk(str(i),float(10-i),None,float(10-i),('LEXICAL',)) for i in range(8)]
        first=select_progressive(rows,'balanced',0)
        second=select_progressive(rows,'balanced',1)
        self.assertEqual((len(first),len(second)),(3,2))
        self.assertFalse(set(first)&set(second))
        self.assertEqual(select_progressive([RankedChunk('x',0,None,0,('LOW_SIGNAL',))],'balanced',0),[])

    def test_cjk_terms_match_without_word_spaces(self):
        from ollama_deep_researcher.pm_retrieval_v06 import rank_chunks
        from ollama_deep_researcher.pm_chunking_v06 import build_chunks
        chunks=build_chunks('\ud575\uc2ec \ub2e8\uc870\ud488 \uc81c\uc870\ub2a5\ub825\n\n\uc601\uc5b4 \ubb38\ubc95 \uad50\uc7ac',target_chars=12,max_chars=18)
        ranked=rank_chunks('\ub2e8\uc870\ud488',chunks,[])
        self.assertGreater(ranked[0].combined_score,0)
        self.assertIn('\ub2e8\uc870\ud488',next(c.text for c in chunks if c.id==ranked[0].chunk_id))

    def test_optional_semantic_failure_does_not_drop_lexical_candidates(self):
        from ollama_deep_researcher.pm_retrieval_v06 import rank_chunks, rerank_semantic
        rows=rank_chunks('forging',self.chunks(),[])
        class Broken:
            name='broken'
            def score(self,query,texts): raise RuntimeError('fixture failure')
        updated,meta=rerank_semantic('forging',self.chunks(),rows,Broken())
        self.assertEqual(updated,rows)
        self.assertEqual(meta['status'],'SEMANTIC_RERANK_SKIPPED')

    def test_semantic_invalid_or_nan_values_rejected(self):
        from ollama_deep_researcher.pm_retrieval_v06 import rank_chunks, rerank_semantic
        chunks=self.chunks(); rows=rank_chunks('forging',chunks,[])
        class Invalid:
            name='invalid'
            def score(self,query,texts): return [float('nan')]*len(texts)
        updated,meta=rerank_semantic('forging',chunks,rows,Invalid())
        self.assertEqual(updated,rows)
        self.assertEqual(meta['status'],'SEMANTIC_RERANK_SKIPPED')
