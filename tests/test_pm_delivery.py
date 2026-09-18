"""Regressions for source coverage, durable evidence and cited synthesis."""
import json
import os
from pathlib import Path
from unittest.mock import patch
from test_pm import Base, FakeModel, FakeWeb, claim
import test_pm_io as io_fixtures
from ollama_deep_researcher.pm_engine import Engine
from ollama_deep_researcher.pm_io import Web
from ollama_deep_researcher.pm_types import Settings

class Delivery(Base):
    def test_task_keeps_older_evidence_beyond_working_context(self):
        ids = [self.evidence(f'https://example.org/{i}') for i in range(24)]
        new_doc = self.store.add_document('https://example.net/new', 'Synthetic', claim()['quote'])
        s = self.store.load(self.pid)
        s['stage'], s['active'] = 'extract', 0
        s['tasks'] = [dict(id='t1', title='TEST', status='RUNNING', attempts=1, criteria=self.task['criteria'],
                           feedback=[], evidence_ids=ids, document_ids=[new_doc], document_index=0)]
        self.store.save(s)
        Engine(self.store, FakeModel(), FakeWeb()).step(self.pid)
        saved = self.store.load(self.pid)['tasks'][0]['evidence_ids']
        self.assertEqual(len(saved), 25)
        self.assertTrue(set(ids).issubset(saved))

    def test_writer_without_inline_citation_is_not_accepted(self):
        class UncitedWriter(FakeModel):
            def ask(self, role, payload, check):
                result = super().ask(role, payload, check)
                if role == 'writer': result['summary'] = 'An unsupported prose draft.'
                return result
        Engine(self.store, UncitedWriter(), FakeWeb()).run(self.pid)
        self.assertEqual(self.store.load(self.pid)['status'], 'ERROR')

    def test_allowlisted_domains_not_silently_dropped(self):
        fixture = io_fixtures.SearchWorker('test_real_subprocess_returns_fixture')
        fixture.setUp()
        try:
            domains = [f'source{i}.example' for i in range(10)]
            with patch.dict(os.environ, fixture.env):
                results = Web(Settings(allowed_domains=domains)).search('fixture')
            for domain in domains:
                self.assertIn('site:' + domain, results[0]['content'])
        finally: fixture.doCleanups()
