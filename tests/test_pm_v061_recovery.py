"""Pause/restart, conservative false-negative and bounded-generator tests."""
import json
import tempfile
import unittest
from unittest.mock import patch
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_engine_v06 import EngineV06
from ollama_deep_researcher.pm_query_policy import validate_intent
from ollama_deep_researcher import pm_research_metrics as m, pm_v06_store as audit
from v06_fixtures import Model06,Web06
from test_pm_v061_hotfix import intent


class Recovery061(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=Workspace(self.tmp.name)
    def build(self,model=None,attempts=2):
        pid=self.w.create_v06('Sheffield Forgemasters SMR contracts',Settings(max_attempts=attempts,draft_enabled=False))
        st=self.w.open(pid);e=EngineV06(st,model or Model06(intents=[intent()]),Web06(hits=[]))
        return pid,st,e
    def run_bounded(self,e,pid):
        with patch.object(e.pacer,'wait',return_value=.1):
            for _ in range(100):
                if not e.step(pid):return
        self.fail('Runaway recovery loop')
    def seed_task(self,e,st,pid):
        s=st.load(pid);s['tasks']=[e.make_task({'title':'SMR contracts','query':'SMR','criteria':['contracts']},1)]
        s.update(stage='research',active=0);e.upgrade_budget_checkpoint(s);st.save(s)
        return s
    def test_legacy_planned_long_gap_retires_once_instead_of_spinning(self):
        pid,st,e=self.build();s=self.seed_task(e,st,pid)
        bad=intent(gap='x'*150)
        aid=m.begin_search_attempt(st,pid,'t001','legacy verbose query','exact_entity',[bad['entity'],bad['gap']])
        audit.save_intent(st,aid,{'intent':bad,'query':'legacy verbose query','variant':0})
        s['tasks'][0].update(active_search_id=aid,attempts=1);st.save(s)
        self.run_bounded(e,pid)
        self.assertEqual(m.attempt(st,aid)['status'],'INVALID_INTENT')
        self.assertNotIn('legacy verbose query',e.web.queries)
        self.assertEqual(st.load(pid)['errors'],0)
    def test_invalid_intent_gets_one_corrective_retry_then_valid_query(self):
        pid,st,e=self.build(Model06(intents=[intent(gap='x'*150),intent()]),attempts=1)
        self.run_bounded(e,pid)
        calls=[p for r,p in e.model.payloads if r=='researcher']
        self.assertEqual(len(calls),2)
        self.assertIn('Invalid structured intent',calls[1]['feedback'][0])
        self.assertEqual(len(e.web.queries),1)
        self.assertNotIn('x'*100,e.web.queries[0])
    def test_two_invalid_replies_do_not_invent_a_fallback_query(self):
        pid,st,e=self.build(Model06(intents=[intent(gap='x'*150)]),attempts=1)
        self.run_bounded(e,pid)
        self.assertEqual(e.web.queries,[])
        self.assertEqual(len([p for r,p in e.model.payloads if r=='researcher']),2)
    def test_repeated_exhausted_intent_is_bounded(self):
        pid,st,e=self.build(attempts=20)
        self.run_bounded(e,pid)
        self.assertEqual(len(e.web.queries),3)
        self.assertLessEqual(len([p for r,p in e.model.payloads if r=='researcher']),3)
        self.assertEqual(st.load(pid)['tasks'][0].get('v06_stop'),'STALLED')
    def test_cache_is_not_reused_after_explicit_goal_change(self):
        pid,st,e=self.build(attempts=3)
        with patch.object(e.pacer,'wait',return_value=.1):
            while not e.web.queries:e.step(pid)
            s=st.load(pid);s['instructions']='Exclude cancelled contracts';st.save(s)
            e._plan_queries(s,s['tasks'][0],Settings.from_saved(s['settings']))
        self.assertEqual(len([p for r,p in e.model.payloads if r=='researcher']),2)
    def test_long_keywords_cannot_recreate_a_large_query(self):
        d=intent(keywords=['a very long group of unrelated different topics']*1+
                 ['other unrelated extensive search details and phrases']*1+
                 ['further important specifications for many other industrial products']*1)
        with self.assertRaises(ValueError):validate_intent(d,set())
    def test_focus_exploration_retains_sector_source_with_no_company_name(self):
        from ollama_deep_researcher.pm_focus_v061 import focused_signal
        s=focused_signal(intent(), 'A technical paper describes SMR pressure vessel fabrication.')
        self.assertTrue(s.plausible)
    def test_two_generic_words_do_not_satisfy_entity_absent_focus(self):
        from ollama_deep_researcher.pm_focus_v061 import focused_signal
        s=focused_signal(intent(gap='2026 project record capacity',keywords=['project','capacity']),
                          'University of Sheffield has global project records and capacity in 2026.')
        self.assertFalse(s.plausible)
    def test_hotfix_policy_visible_once_without_changing_engine_version(self):
        pid,st,e=self.build();self.run_bounded(e,pid)
        events=[r for r in st.events(pid,500) if r['kind']=='HOTFIX_POLICY']
        self.assertEqual(len(events),1)
        self.assertEqual(st.load(pid).get('hotfix_version'),'0.6.2')
        self.assertEqual(st.load(pid)['engine_version'],6)
    def test_search_completion_exposes_soft_deferrals_separately_from_hard_rejects(self):
        pid,st,e=self.build(attempts=1)
        e.web=Web06([{'url':'https://u.example','title':'University of Sheffield','content':'rankings and projects'}])
        self.run_bounded(e,pid)
        event=next(json.loads(r['message']) for r in st.events(pid,100) if r['kind']=='SEARCH_COMPLETED')
        self.assertEqual(event.get('focus_deferred_hits'),1)
        self.assertEqual(event['prefilter_rejected'],0)
        self.assertEqual(event['fetched'],0)

    def test_numerical_body_with_real_entity_title_is_not_lost(self):
        from types import SimpleNamespace
        pid,st,e=self.build(attempts=1)
        class TableWeb(Web06):
            def fetch_document(self,url):
                self.fetches.append(url)
                return SimpleNamespace(body='14000 t',raw=b'14000 t',
                    metadata={'title':'Sheffield Forgemasters press capacity'})
        e.web=TableWeb([{'url':'https://forge.example/table','title':'Sheffield Forgemasters SMR',
                        'content':'SMR pressure vessel'}])
        self.run_bounded(e,pid)
        self.assertTrue([p for r,p in e.model.payloads if r=='extractor'])
        self.assertEqual(st.all_documents()[0]['body'],'14000 t')
    def test_anchor_boundary_is_consistent_for_every_accepted_size(self):
        from ollama_deep_researcher.pm_query_policy import intent_anchors
        from ollama_deep_researcher.pm_search_quality import parse_search_intent
        for n in (80,81,100,101,119):
            value=intent(entity='E'*n,gap='SMR vessel contracts')
            parsed=validate_intent(value,set())
            parse_search_intent('fixture','exact_entity',intent_anchors(parsed))
        for n in (81,101,200):
            with self.assertRaises(ValueError):validate_intent(intent(gap='x'*n),set())
    def test_unqualified_weak_hit_has_no_false_irrelevant_claim(self):
        pid,st,e=self.build(attempts=1)
        e.web=Web06([{'url':'https://unknown.example/short','title':'An unfamiliar report','content':''}])
        self.run_bounded(e,pid)
        hits=m.hits(st)
        self.assertEqual(len(hits),1)
        self.assertEqual(hits[0]['status'],'FOCUS_DEFERRED')
        self.assertEqual(st.counts()['evidence'],0)
