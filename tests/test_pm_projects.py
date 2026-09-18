"""Offline acceptance tests for physical project boundaries and explicit reuse."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from ollama_deep_researcher import pm_store
from ollama_deep_researcher.pm_store import Store
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings


class Projects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = Settings(min_tasks=1)

    def workspace(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('ollama_deep_researcher.pm_projects'))
        from ollama_deep_researcher.pm_projects import Workspace
        return Workspace(self.root)

    def test_physical_isolation_and_no_default_references(self):
        w = self.workspace()
        a = w.create('steel forging', self.cfg)
        b = w.create('restaurant', self.cfg)
        sa, sb = w.open(a), w.open(b)
        sa.add_document('https://example.org/a', 'Forging', 'Forging press capacity is 100 t.')
        self.assertNotEqual(sa.path, sb.path)
        self.assertEqual(sa.path, self.root / 'projects' / a / 'project.sqlite3')
        self.assertEqual(sb.counts()['documents'], 0)
        self.assertEqual(w.load(b)['reference_projects'], [])
        self.assertEqual(w.reference_candidates(b, 'forging press'), [])
        self.assertFalse((self.root / 'research.sqlite3').exists())

    def test_explicit_candidate_preserves_origin_without_auto_evidence(self):
        w = self.workspace()
        a = w.create('steel forging', self.cfg)
        did = w.open(a).add_document('https://example.org/a', 'Forging', 'Forging press capacity is 100 t.')
        b = w.create('forging research', self.cfg, reference_projects=[a])
        candidate = w.reference_candidates(b, 'forging press')[0]
        self.assertEqual(candidate['origin_project_id'], a)
        self.assertEqual(candidate['origin_document_id'], did)
        before = hashlib.sha256(w.open(a).path.read_bytes()).hexdigest()
        imported = w.import_candidate(b, candidate)
        self.assertEqual(w.open(b).counts(), {'documents': 1, 'evidence': 0})
        doc = w.open(b).document(imported)
        self.assertEqual(doc['metadata']['origin_project_id'], a)
        self.assertEqual(doc['metadata']['relation'], 'reference_candidate')
        self.assertEqual(before, hashlib.sha256(w.open(a).path.read_bytes()).hexdigest())

    def test_unselected_forged_reference_is_rejected(self):
        w = self.workspace()
        a, b = w.create('A', self.cfg), w.create('B', self.cfg)
        did = w.open(a).add_document('https://example.org/a', 'A', 'Candidate source text')
        with self.assertRaises(ValueError):
            w.import_candidate(b, {'origin_project_id': a, 'origin_document_id': did})
        self.assertEqual(w.open(b).counts()['documents'], 0)

    def test_no_transitive_reference_traversal(self):
        w = self.workspace()
        a = w.create('source A', self.cfg)
        w.open(a).add_document('https://example.org/a', 'Forging', 'Forging press capacity is 100 t.')
        b = w.create('source B', self.cfg, reference_projects=[a])
        c = w.create('source C', self.cfg, reference_projects=[b])
        self.assertEqual(w.reference_candidates(c, 'forging press'), [])

    def test_numeric_match_is_not_relevance(self):
        w = self.workspace()
        a = w.create('restaurant', self.cfg)
        w.open(a).add_document('https://example.org/a', 'Restaurant', 'Restaurant serves 100 people.')
        b = w.create('forging', self.cfg, reference_projects=[a])
        self.assertEqual(w.reference_candidates(b, '100'), [])

    def test_invalid_ids_and_missing_references_do_not_create_projects(self):
        w = self.workspace()
        for pid in ('../escape', '', 'not-a-project', '0'*16):
            with self.assertRaises((ValueError, KeyError)):
                w.open(pid)
        with self.assertRaises((ValueError, KeyError)):
            w.create('B', self.cfg, reference_projects=['0'*16])
        self.assertEqual(w.projects(), [])

    def test_bound_store_refuses_another_project(self):
        w = self.workspace()
        a, b = w.create('A', self.cfg), w.create('B', self.cfg)
        with self.assertRaises(KeyError):
            w.open(a).load(b)
        with self.assertRaises(ValueError):
            w.open(a).create('hidden second project', self.cfg)

    def test_source_backup_and_migration_do_not_spread_unlinked_material(self):
        old = pm_store.Store(self.root)
        a, b = old.create('forging legacy', self.cfg), old.create('restaurant legacy', self.cfg)
        did = old.add_document('https://example.org/a', 'Forging', 'Forging press capacity is 100 t.')
        orphan = old.add_document('https://example.org/orphan', 'Unknown owner', 'Unassigned document text.')
        eid = old.add_evidence(did, dict(entity='press', metric='capacity', value='100', unit='t',
            period='2025', scope='rated', quote='Forging press capacity is 100 t.'))
        s = old.load(a)
        s['tasks'] = [dict(id='t1', title='press', query='press', criteria=[], status='DONE',
                           attempts=1, feedback=[], evidence_ids=[eid], document_ids=[])]
        old.save(s)
        w = self.workspace()
        self.assertTrue((self.root / 'research.sqlite3').exists())
        backups = list((self.root / 'backups').glob('*.sqlite3'))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(backups[0]) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM documents').fetchone()[0], 2)
        self.assertEqual(w.open(b).counts()['documents'], 0)
        self.assertEqual(w.open(a).counts()['documents'], 1)
        self.assertEqual(w.open(a).counts()['evidence'], 0)
        self.assertTrue(w.load(a)['legacy_review_required'])
        self.assertEqual(w.load(a)['tasks'][0]['evidence_ids'], [])
        report = json.loads((self.root / 'migration.json').read_text())
        self.assertEqual(report['unassigned_document_ids'], [orphan])
        w2 = self.workspace()
        self.assertEqual(len(w2.projects()), 2)
        self.assertEqual(len(list((self.root / 'backups').glob('*.sqlite3'))), 1)

    def test_migration_refuses_running_legacy_worker(self):
        pm_store.Store(self.root).create('legacy', self.cfg)
        with pm_store.worker_lock(self.root):
            with self.assertRaises(RuntimeError):
                self.workspace()
        self.assertFalse((self.root / 'migration.json').exists())

    def test_migration_archives_preexisting_export_folder(self):
        old = pm_store.Store(self.root)
        pid = old.create('legacy project', self.cfg)
        exports = old.export(pid)
        (exports / 'documents').mkdir()
        (exports / 'documents' / 'keep.txt').write_text('user retained source', encoding='utf-8')
        w = self.workspace()
        self.assertEqual(len(w.projects()), 1)
        preserved = list((self.root / 'backups').glob('exports-*/documents/keep.txt'))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_text(), 'user retained source')

class SourceVersionRegressions(unittest.TestCase):
    def test_reference_copies_original_bytes_not_just_decoded_text(self):
        with tempfile.TemporaryDirectory() as root:
            w = Workspace(root)
            a = w.create('reference raw', Settings())
            raw = b'<html><p>Reference material has a capacity of 42 t.</p></html>'
            did = w.open(a).add_document('https://example.org/raw', 'reference',
                'Reference material has a capacity of 42 t.',
                metadata={'content_type': 'text/html'}, raw=raw)
            b = w.create('new reference', Settings(), reference_projects=[a])
            imported = w.import_candidate(b, {'origin_project_id': a, 'origin_document_id': did})
            self.assertEqual(w.open(b).raw_document(imported), raw)
            self.assertEqual(w.open(b).document(imported)['metadata']['origin_document_id'], did)

    def test_legacy_document_id_is_remapped_after_version_algorithm_change(self):
        with tempfile.TemporaryDirectory() as root:
            old = Store(root)
            pid = old.create('migration mapping', Settings())
            body = 'Source    contains    repeated whitespace and remains readable.'
            did = old.add_document('https://example.org/map', 'mapped text', body)
            legacy_id = 'd-' + 'a' * 24
            with old.db() as conn:
                conn.execute('UPDATE documents SET id=? WHERE id=?', (legacy_id, did))
            state = old.load(pid)
            state['tasks'] = [{'id': 't1', 'title': 'mapping', 'document_ids': [legacy_id], 'evidence_ids': []}]
            old.save(state)
            w = Workspace(root)
            saved_ids = w.load(pid)['tasks'][0]['document_ids']
            self.assertEqual(len(saved_ids), 1)
            self.assertEqual(w.open(pid).document(saved_ids[0])['body'], body)

    def test_legacy_migration_keeps_original_html_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            old=Store(root)
            pid=old.create('HTML migration', Settings())
            raw=b'<html><p>Original HTML must survive migration.</p></html>'
            did=old.add_document('https://example.org/html','HTML','Original HTML must survive migration.',
                                 metadata={'content_type':'text/html'},raw=raw)
            state=old.load(pid);state['tasks']=[{'id':'t1','title':'HTML','document_ids':[did],'evidence_ids':[]}]
            old.save(state)
            w=Workspace(root)
            saved=w.load(pid)['tasks'][0]['document_ids'][0]
            self.assertEqual(w.open(pid).raw_document(saved),raw)
