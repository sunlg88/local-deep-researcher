"""Chunk/retrieval windows must round-trip to original character offsets."""
import unittest


class Chunk06(unittest.TestCase):
    def test_complete_partition_exact_offsets_and_bounds(self):
        from ollama_deep_researcher.pm_chunking_v06 import build_chunks
        for body in ['# Heading\n\nAlpha condition 500 C.\n\nBeta result.',
                     ('\ud55c\uae00 \uc870\uac74 500 C.\r\n\r\n' * 30),
                     'x' * 300, ' \n\r\n ']:
            chunks=build_chunks(body,target_chars=25,max_chars=40)
            self.assertEqual(''.join(c.text for c in chunks),body)
            for c in chunks:
                self.assertEqual(body[c.start:c.end],c.text)
                self.assertLessEqual(len(c.text),40)
                self.assertLess(c.start,c.end)
            self.assertEqual(len({c.id for c in chunks}),len(chunks))

    def test_heading_page_and_neighbor_metadata(self):
        from ollama_deep_researcher.pm_chunking_v06 import build_chunks, expand_with_neighbors
        body='# Pressure vessel\n\nTemperature 500 C.\n\nCapacity 42 t.\n\nLimits apply.'
        chunks=build_chunks(body,target_chars=20,max_chars=40,pages=[{'page':2,'start':0,'end':len(body)}])
        self.assertTrue(any('Pressure' in c.heading for c in chunks))
        self.assertTrue(all(c.page_hint == '2' for c in chunks))
        start,end,text=expand_with_neighbors(chunks,chunks[1].id,max_chars=80)
        self.assertEqual(body[start:end],text)
        self.assertLessEqual(end-start,80)

    def test_invalid_budgets_unknown_chunk_empty_source(self):
        from ollama_deep_researcher.pm_chunking_v06 import build_chunks, expand_with_neighbors
        self.assertEqual(build_chunks(''),[])
        for target,limit in [(0,10),(50,10),(-2,10)]:
            with self.assertRaises(ValueError): build_chunks('x',target_chars=target,max_chars=limit)
        with self.assertRaises(KeyError): expand_with_neighbors(build_chunks('abc'),'missing')
