"""Self-contained handoff includes search decisions, mappings and source hashes."""
import csv
import hashlib
import json
import tempfile
import unittest
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_engine_v05 import EngineV05
from v05_fixtures import Model05,Web05


class Export05(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=Workspace(self.tmp.name)
        self.pid=self.w.create('TEST press',Settings(max_attempts=1,draft_enabled=False))
        self.st=self.w.open(self.pid)
    def run_pm(self,relevant=True):
        EngineV05(self.st,Model05(relevant=relevant),Web05()).run(self.pid)
        return self.st.export(self.pid)
    def test_handoff_contains_evidence_task_map_and_search_decisions(self):
        folder=self.run_pm()
        e=json.loads((folder/'evidence.json').read_text(encoding='utf-8'))
        self.assertEqual(e[0].get('task_ids'),['t001'])
        self.assertIn('task_reviews',e[0])
        self.assertTrue((folder/'search_attempts.csv').exists())
        self.assertTrue((folder/'search_hits.csv').exists())
        manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest.get('engine_version'),5)
        self.assertEqual(manifest['research_metrics']['unique_evidence'],1)
        for entry in manifest['files']:
            self.assertEqual(hashlib.sha256((folder/entry['path']).read_bytes()).hexdigest(),entry['sha256'])
    def test_zero_yield_is_explicit_not_no_results_without_explanation(self):
        folder=self.run_pm(False)
        self.assertIn('ZERO_YIELD',(folder/'unresolved.md').read_text(encoding='utf-8'))
        with (folder/'search_attempts.csv').open(encoding='utf-8-sig',newline='') as f:
            row=list(csv.DictReader(f))[0]
        self.assertEqual(row['outcome'],'ZERO_YIELD')
        self.assertEqual(row['accepted_claims'],'0')
    def test_search_hit_csv_escapes_formula_but_json_keeps_original(self):
        title='=TEST press'
        EngineV05(self.st,Model05(),Web05([{'url':'https://example.org/a','title':title}])).run(self.pid)
        f=self.st.export(self.pid)
        self.assertTrue((f/'search_hits.csv').exists())
        with (f/'search_hits.csv').open(encoding='utf-8-sig',newline='') as stream:
            self.assertEqual(next(csv.DictReader(stream))['title'],"'"+title)
        rows=json.loads((f/'search_hits.json').read_text(encoding='utf-8'))
        self.assertEqual(rows[0]['title'],title)
