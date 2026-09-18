"""Actual engine search boundary tests, including interruption and irrelevant hits."""
import tempfile
import unittest
from urllib.error import HTTPError
from ollama_deep_researcher.pm_engine_v05 import EngineV05
from ollama_deep_researcher.pm_engine import ControlRequested
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher import pm_research_metrics as metrics
from v05_fixtures import Model05, Web05


class SearchFlow05(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.w=Workspace(self.tmp.name)
        self.cfg=Settings(max_attempts=2,draft_enabled=False)
        self.pid=self.w.create('TEST press nuclear study',self.cfg)
        self.st=self.w.open(self.pid)
    def run_pm(self,model=None,web=None):
        e=EngineV05(self.st,model or Model05(),web or Web05()); e.run(self.pid); return e

    def test_site_mismatch_never_fetched(self):
        class SiteModel(Model05):
            def ask(self,r,p,c):
                result=super().ask(r,p,c)
                if r=='researcher': result['query']='TEST press site:cn'
                return result
        w=Web05([{'url':'https://microsoft.com/a','title':'TEST press'},
                 {'url':'https://example.cn/a','title':'TEST press'}])
        self.run_pm(SiteModel(),w)
        self.assertEqual(w.fetches,['https://example.cn/a'])
        self.assertTrue(any('SITE_MISMATCH' in h['reasons'] for h in metrics.hits(self.st)))

    def test_unrelated_hits_only_one_fallback_not_three(self):
        w=Web05([{'url':f'https://a{i}.org/x','title':'Microsoft PowerShell'} for i in range(9)])
        self.run_pm(Model05(relevant=False),w)
        self.assertLessEqual(len(w.fetches),1)
        self.assertTrue(any(h['status']=='WEAK_DEFERRED' for h in metrics.hits(self.st)))

    def test_same_query_not_executed_twice_and_feedback_retained(self):
        m=Model05(); w=Web05([])
        self.run_pm(m,w)
        self.assertEqual(len(w.queries),1)
        payloads=[p for r,p in m.payloads if r=='researcher']
        self.assertIn('attempt_feedback',payloads[-1])
        self.assertTrue(payloads[-1]['attempt_feedback'])
        self.assertTrue(any(a['status']=='DUPLICATE_QUERY' for a in metrics.attempts(self.st)))

    def test_host_diversity_at_actual_fetch_boundary(self):
        self.cfg.source_limit=6
        s=self.st.load(self.pid);s['settings']=self.cfg.to_dict(); self.st.save(s)
        hits=[{'url':f'https://one.org/{i}','title':'TEST press'} for i in range(5)]+[
            {'url':'https://two.org/a','title':'TEST press'}, {'url':'https://three.org/a','title':'TEST press'}]
        w=Web05(hits); self.run_pm(web=w)
        self.assertLessEqual(sum('/one.org/' in u for u in w.fetches),2)
        self.assertIn('https://two.org/a',w.fetches)

    def test_two_denials_create_host_cooldown_without_erasing_originals(self):
        hits=[{'url':f'https://denied.org/{i}','title':'TEST press'} for i in range(4)]
        class Broken(Web05):
            def fetch(self,url):
                self.fetches.append(url)
                raise HTTPError(url,403,'denied',{},None)
        w=Broken(hits); self.run_pm(web=w)
        self.assertEqual(len(w.fetches),2)
        self.assertTrue(metrics.host_in_cooldown(self.st,'denied.org'))

    def test_search_hit_batch_resume_avoids_repeating_completed_fetch(self):
        class Pausing(Web05):
            paused=False
            def fetch(self,url):
                if url.endswith('/b') and not self.paused:
                    self.paused=True
                    self.check_pause()
                    raise ControlRequested()
                return super().fetch(url)
        w=Pausing([{'url':'https://one.org/a','title':'TEST press'},
                   {'url':'https://two.org/b','title':'TEST press'}])
        w.check_pause=lambda:self.st.control(self.pid,'PAUSE')
        self.run_pm(web=w)
        self.assertEqual(self.st.load(self.pid)['status'],'PAUSED')
        self.st.control(self.pid,'RUN')
        self.run_pm(web=w)
        self.assertEqual(w.fetches.count('https://one.org/a'),1)
        self.assertEqual(len(w.queries),1)
        self.assertEqual(len(metrics.attempts(self.st)),2) # one real, one duplicate intent
