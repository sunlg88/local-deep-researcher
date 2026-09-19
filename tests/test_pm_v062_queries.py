"""Independent target searches instead of concatenated research descriptions."""
import copy
import tempfile
import unittest
from unittest.mock import patch
from ollama_deep_researcher.pm_engine_v06 import EngineV06
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_budget import schema_for
from v06_fixtures import Model06, Web06

PROPOSAL={'entity':'Sheffield Forgemasters','search_phrases':['SMR pressure vessel contracts','ASME N certification','forging press capacity'],
          'constraints':['2026','not cancelled'],'language':'en','site_hint':''}
class Queries062(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.w=Workspace(self.tmp.name)
    def run_case(self,data=PROPOSAL,attempts=3):
        pid=self.w.create_v06('Sheffield Forgemasters nuclear forging',Settings(max_attempts=attempts,draft_enabled=False))
        st=self.w.open(pid);m=Model06(intents=[copy.deepcopy(data)]);web=Web06(hits=[]);e=EngineV06(st,m,web)
        with patch.object(e.pacer,'wait',return_value=.1):
            for _ in range(120):
                if not e.step(pid):break
            else:self.fail('unbounded query planning')
        return st,m,web,pid
    def test_three_independent_targets_not_keyword_soup(self):
        st,m,web,pid=self.run_case();self.assertEqual(len(web.queries),3)
        self.assertTrue(any('ASME' in q for q in web.queries))
        for q in web.queries:
            self.assertIn('2026',q);self.assertIn('not cancelled',q)
            self.assertFalse('ASME' in q and 'forging press capacity' in q);self.assertLessEqual(len(q.split()),14)
        self.assertEqual(len([p for r,p in m.payloads if r=='researcher']),1);self.assertEqual(st.load(pid)['errors'],0)
    def test_transport_uses_small_search_phrase_schema(self):
        st,m,web,pid=self.run_case(attempts=1);payload=next(p for r,p in m.payloads if r=='researcher')
        props=schema_for('researcher',payload)['properties']
        self.assertIn('search_phrases',props);self.assertIn('constraints',props);self.assertNotIn('gap',props)
    def test_unknown_domain_not_invented(self):
        st,m,web,pid=self.run_case(dict(PROPOSAL,site_hint='guessed.example.org'),1)
        self.assertEqual(web.queries,[]);self.assertEqual(len([p for r,p in m.payloads if r=='researcher']),2)
    def test_proposal_and_question_retained(self):
        st,m,web,pid=self.run_case();state=st.load(pid)
        self.assertEqual(state['tasks'][0].get('search_proposal_v062'),PROPOSAL);self.assertEqual(state['topic'],'Sheffield Forgemasters nuclear forging')
    def test_reopen_without_extra_model_call(self):
        pid=self.w.create_v06('Sheffield Forgemasters',Settings(max_attempts=3,draft_enabled=False));st=self.w.open(pid)
        m=Model06(intents=[PROPOSAL]);web=Web06(hits=[]);e=EngineV06(st,m,web)
        with patch.object(e.pacer,'wait',return_value=.1):
            for _ in range(40):
                if web.queries or not e.step(pid):break
        self.assertTrue(web.queries);e=EngineV06(st,m,web)
        with patch.object(e.pacer,'wait',return_value=.1):
            for _ in range(100):
                if not e.step(pid):break
        self.assertEqual(len(web.queries),3);self.assertEqual(len([p for r,p in m.payloads if r=='researcher']),1)
    def test_operator_injection_rejected(self):
        st,m,web,pid=self.run_case(dict(PROPOSAL,entity='Sheffield OR other company'),1);self.assertFalse(web.queries)
    def test_legacy_qualifiers_not_silently_deleted(self):
        from ollama_deep_researcher.pm_query_v062 import query_proposals
        old=dict(entity='Japan Steel Works',gap='new nuclear forging press rated capacity',keywords=['stainless steel','not planned'],strategy='exact_entity',language='en',site_hint='')
        for row in query_proposals(old,set()):
            self.assertIn('stainless steel',row['query']);self.assertIn('not planned',row['query'])
