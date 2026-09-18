"""Versioned contracts: v0.4 regression fixtures are not v0.5 acceptance tests."""
import importlib.util
import tempfile
import unittest
from ollama_deep_researcher.pm_budget import schema_for, request_parts
from ollama_deep_researcher.pm_types import Settings


class Contracts05(unittest.TestCase):
    def test_researcher_schema_has_intent_not_followups(self):
        props = schema_for('researcher', {'_pm_version': 5})['properties']
        self.assertNotIn('followups', props)
        self.assertIn('strategy', props)
        self.assertIn('anchors', props)
        self.assertEqual(props['anchors']['minItems'], 2)

    def test_versioned_research_prompt_requires_single_entity_and_no_guessed_domain(self):
        text, _, _ = request_parts(Settings(), 'researcher', {'_pm_version':5})
        self.assertIn('one entity', text)
        self.assertIn('Never guess', text)
        self.assertNotIn('optionally up to two followups', text)

    def test_frozen_tasks_reject_new_researcher_task_definitions(self):
        self.assertIsNotNone(importlib.util.find_spec('ollama_deep_researcher.pm_engine_v05'))
        from ollama_deep_researcher.pm_engine_v05 import EngineV05
        from ollama_deep_researcher.pm_projects import Workspace
        from test_pm import FakeModel, FakeWeb
        class Expanding(FakeModel):
            def ask(self, role, payload, check):
                if role == 'researcher':
                    return {'query':'TEST press', 'strategy':'broad', 'anchors':['TEST','press'],
                            'followups':[{'title':'Extra','query':'extra','criteria':['x']}]}
                return super().ask(role,payload,check)
        with tempfile.TemporaryDirectory() as root:
            w = Workspace(root)
            pid = w.create('TEST press capacity', Settings(max_attempts=1))
            st = w.open(pid)
            engine = EngineV05(st,Expanding(),FakeWeb())
            engine.run(pid)
            state = st.load(pid)
            self.assertEqual(len(state['tasks']),1)
            self.assertEqual(state['tasks'][0]['title'],'TEST press capacity')
            self.assertTrue(any('followups' in e['message'] for e in st.events(pid,500)))
