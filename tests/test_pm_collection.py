"""Collection contracts: preserve sources, locations, status and reusable history."""
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from ollama_deep_researcher.pm_store import Store
from ollama_deep_researcher.pm_types import Settings, canonical_url


class CollectionStore(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = Store(self.root)
        self.pid = self.store.create('General research', Settings(min_tasks=1))

    def test_export_retains_original_without_any_evidence(self):
        text = 'Original source retained even when the local model misses the finding.'
        did = self.store.add_document('https://example.org/a', 'Untouched original', text)
        folder = self.store.export(self.pid)
        self.assertTrue((folder / 'handoff.md').is_file())
        for name in ('evidence.json', 'sources.csv', 'unresolved.md', 'manifest.json'):
            self.assertTrue((folder / name).is_file(), name)
        manifest = json.loads((folder / 'manifest.json').read_text())
        doc = next(d for d in manifest['documents'] if d['id'] == did)
        self.assertEqual((folder / doc['text_path']).read_text(), text)
        self.assertEqual(json.loads((folder / 'evidence.json').read_text()), [])
        for item in manifest['files']:
            content = (folder / item['path']).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), item['sha256'])

    def test_large_original_separate_from_model_window(self):
        text = 'Full original paragraph.\n' * 12000
        did = self.store.add_document('https://example.org/long', 'Long original', text)
        self.assertEqual(self.store.document(did)['body'], text)

    def test_raw_only_document_survives_parse_failure(self):
        raw = b'%PDF-invalid but original source bytes'
        did = self.store.add_document('https://example.org/scan.pdf', 'Scan', '',
            raw=raw, metadata={'content_type': 'application/pdf', 'parse_status': 'PARSE_FAILED'})
        self.assertEqual(self.store.raw_document(did), raw)
        manifest = json.loads((self.store.export(self.pid) / 'manifest.json').read_text())
        self.assertEqual(manifest['documents'][0]['metadata']['parse_status'], 'PARSE_FAILED')

    def test_quote_location_and_optional_fields(self):
        text = 'Preface.\n\nThe material\n  is corrosion resistant.\nEnd.'
        did = self.store.add_document('https://example.org/a', 'General evidence', text,
            metadata={'pages': [{'page': 1, 'start': 0, 'end': len(text)}]})
        eid = self.store.add_evidence(did, dict(entity='material', metric='corrosion resistance',
            claim_text='The material is corrosion resistant.', quote='The material is corrosion resistant.'))
        evidence = self.store.get_evidence([eid])[0]
        self.assertEqual(evidence['period'], '')
        self.assertEqual(evidence['unit'], '')
        location = evidence['location']
        quoted = text[location['start']:location['end']]
        self.assertEqual(' '.join(quoted.split()), evidence['quote'])
        self.assertEqual(location['pages'], [1])
        self.assertEqual(evidence['comparison_status'], 'CONDITIONS_UNCONFIRMED')

    def test_same_number_different_subentity_not_a_conflict(self):
        ids = []
        for sub, value in [('press A', '100'), ('press B', '120')]:
            text = f'{sub} has rated capacity {value} t in 2025.'
            did = self.store.add_document('https://example.org/' + sub.replace(' ', '-'), sub, text)
            ids.append(self.store.add_evidence(did, dict(entity='company', subentity=sub,
                metric='capacity', value=value, unit='t', period='2025', scope='rated', quote=text)))
        self.assertFalse(any(e['conflict'] for e in self.store.get_evidence(ids)))

    def test_unknown_period_is_not_a_confirmed_conflict(self):
        ids = []
        for i, value in enumerate(('100', '120')):
            text = f'The press has a capacity of {value} t.'
            did = self.store.add_document(f'https://example.org/{i}', 'Press', text)
            ids.append(self.store.add_evidence(did, dict(entity='company', subentity='press A',
                metric='capacity', value=value, unit='t', period='', scope='rated', quote=text)))
        rows = self.store.get_evidence(ids)
        self.assertFalse(any(e['conflict'] for e in rows))
        self.assertTrue(all(e['comparison_status'] == 'CONDITIONS_UNCONFIRMED' for e in rows))

    def test_source_failure_keeps_url_query_and_error(self):
        self.assertTrue(hasattr(self.store, 'record_source'))
        self.store.record_source('https://example.org/missing', 'FETCH_ERROR', task_id='t1',
            query='general research', error_type='HTTP_403', error='forbidden')
        folder = self.store.export(self.pid)
        with (folder / 'sources.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]['url'], 'https://example.org/missing')
        self.assertIn('general research', rows[0]['queries'])
        self.assertEqual(rows[0]['error_type'], 'HTTP_403')

    def test_processing_history_is_range_and_question_specific(self):
        self.assertTrue(hasattr(self.store, 'record_work'))
        did = self.store.add_document('https://example.org/a', 'A', 'Original source text ' * 100)
        key = self.store.work_key(did, 'question-one', 0, 100, 'extract-v2')
        self.store.record_work(key, 'DONE', document_id=did, start=0, end=100)
        self.assertEqual(self.store.work(key)['status'], 'DONE')
        self.assertIsNone(self.store.work(self.store.work_key(did, 'question-two', 0, 100, 'extract-v2')))
        self.assertIsNone(self.store.work(self.store.work_key(did, 'question-one', 100, 200, 'extract-v2')))
        self.assertIsNone(self.store.work(self.store.work_key(did, 'question-one', 0, 100, 'extract-v3')))

    def test_csv_untrusted_formula_is_escaped_but_json_original_kept(self):
        self.assertTrue(hasattr(self.store, 'record_source'))
        self.store.record_source('https://example.org/a', 'DISCOVERED', title='=unsafe()', query='example')
        folder = self.store.export(self.pid)
        with (folder / 'sources.csv').open(encoding='utf-8-sig', newline='') as stream:
            self.assertEqual(list(csv.DictReader(stream))[0]['title'], "'=unsafe()")


class DocumentDecoding(unittest.TestCase):
    def decoder(self):
        self.assertIsNotNone(importlib.util.find_spec('ollama_deep_researcher.pm_documents'))
        from ollama_deep_researcher.pm_documents import decode_document
        return decode_document

    def test_unicode_url_is_request_safe_and_idempotent(self):
        url = canonical_url('https://\uc608\uc2dc.\ud55c\uad6d/\uc790\ub8cc \ubaa9\ub85d?q=\ud55c\uae00')
        self.assertTrue(url.isascii())
        self.assertIn('%', url)
        self.assertEqual(canonical_url(url), url)

    def test_legacy_charset_and_bad_header_fallback(self):
        decode = self.decoder()
        text = '<meta charset="euc-kr"><p>\ud55c\uae00 \uc790\ub8cc</p>'
        result = decode(text.encode('euc-kr'), 'text/html', 'unknown-charset')
        self.assertIn('\ud55c\uae00 \uc790\ub8cc', result['body'])
        self.assertTrue(result['metadata']['warnings'])
        self.assertNotIn('<meta', result['body'])

    def test_utf8_and_raw_html_are_not_confused(self):
        result = self.decoder()('<p>\ud55c\uae00 \uc790\ub8cc</p><script>ignore()</script>'.encode(), 'text/html')
        self.assertIn('\ud55c\uae00', result['body'])
        self.assertNotIn('ignore()', result['body'])

    def test_text_pdf_page_offsets_and_scan_warning(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
            DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 20 250 Td (Known text on first page.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        writer.add_blank_page(width=300, height=300)
        data = io.BytesIO()
        writer.write(data)
        result = self.decoder()(data.getvalue(), 'application/pdf')
        self.assertIn('Known text on first page.', result['body'])
        pages = result['metadata']['pages']
        self.assertEqual([p['page'] for p in pages], [1, 2])
        self.assertIn('Known text', result['body'][pages[0]['start']:pages[0]['end']])
        self.assertTrue(pages[1]['needs_visual_review'])
        self.assertTrue(result['metadata']['layout_not_validated'])

    def test_broken_pdf_is_labelled_not_pretended_to_be_text(self):
        result = self.decoder()(b'%PDF-garbage', 'application/pdf')
        self.assertEqual(result['body'], '')
        self.assertEqual(result['metadata']['parse_status'], 'PARSE_FAILED')

class ImmutableVersions(unittest.TestCase):
    def test_reparsed_original_gets_new_text_version_without_corrupting_old_quotes(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(root)
            raw=b'<html>original bytes</html>'
            first=store.add_document('https://example.org/version','Version','First extraction has exact text.',raw=raw)
            second=store.add_document('https://example.org/version','Version','Improved extraction has different text.',raw=raw)
            self.assertNotEqual(first,second)
            doc=store.document(first)
            self.assertEqual((Path(root)/doc['metadata']['text_path']).read_text(),'First extraction has exact text.')
            self.assertEqual(store.raw_document(first),raw)
