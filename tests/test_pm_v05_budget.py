"""Calibrated estimates retain margins and stay isolated by model/role/script."""
import unittest
from ollama_deep_researcher import pm_budget as b
from ollama_deep_researcher.pm_types import Settings


class Calibration05(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(b,'update_calibration'))
    def test_three_samples_required_and_downward_change_is_slow(self):
        d={}
        for _ in range(2): d=b.update_calibration(d,4000,1400,True)
        self.assertEqual(d['scale'],1)
        d=b.update_calibration(d,4000,1400,True)
        self.assertGreaterEqual(d['scale'],0.9)
        self.assertLess(d['scale'],1)
    def test_underestimate_raises_immediately(self):
        d=b.update_calibration({'scale':0.7,'recent':[],'samples':0},4000,4001,False)
        self.assertGreater(d['scale'],1)
    def test_failed_or_zero_observation_never_causes_downward_learning(self):
        d={}
        for _ in range(6): d=b.update_calibration(d,4000,200,False)
        self.assertEqual(d['scale'],1)
        for _ in range(6): d=b.update_calibration(d,4000,0,True)
        self.assertEqual(d['scale'],1)
    def test_floor_and_output_reserve_remain(self):
        d={}
        for _ in range(20): d=b.update_calibration(d,4000,1000,True)
        self.assertGreaterEqual(d['scale'],0.65)
        cfg=Settings()
        _,_,normal=b.request_parts(cfg,'extractor',{'_pm_version':5,'source_text':'x '*500})
        _,_,small=b.request_parts(cfg,'extractor',{'_pm_version':5,'source_text':'x '*500,'_pm_budget_scale':d['scale']})
        self.assertLess(small['estimated_input_tokens'],normal['estimated_input_tokens'])
        self.assertEqual(small['input_budget'],normal['input_budget'])
        self.assertEqual(small['output_budget'],2560)
        self.assertEqual(small['context_tokens'],8192)
    def test_profile_separates_model_role_and_script(self):
        cfg=Settings()
        latin=b.calibration_key(cfg,'extractor',{'source_text':'abc def '*100})
        cjk=b.calibration_key(cfg,'extractor',{'source_text':'\ud55c\uae00 '*100})
        self.assertNotEqual(latin,cjk)
        self.assertNotEqual(latin,b.calibration_key(Settings(model='different'),'extractor',{'source_text':'abc def '*100}))
        self.assertNotEqual(latin,b.calibration_key(cfg,'writer',{'source_text':'abc def '*100}))
    def test_unknown_model_keeps_upper_bound(self):
        _,_,m=b.request_parts(Settings(model='custom-model'),'extractor',{'_pm_version':5,'_pm_budget_scale':0.65})
        self.assertGreaterEqual(m['estimate_scale'],1)

class CalibrationCallIsolation(unittest.TestCase):
    def test_preflight_failure_does_not_reuse_previous_call_metrics(self):
        import tempfile
        from ollama_deep_researcher.pm_projects import Workspace
        from ollama_deep_researcher.pm_engine_v05 import EngineV05
        from v05_fixtures import Model05, Web05
        with tempfile.TemporaryDirectory() as root:
            workspace=Workspace(root)
            pid=workspace.create('TEST source research',Settings())
            store=workspace.open(pid)
            model=Model05()
            model.last_metrics={'prompt_eval_count':100000,'done_reason':'stop'}
            engine=EngineV05(store,model,Web05())
            state=store.load(pid)
            state['instructions']='x '*40000
            with self.assertRaises(b.FixedPromptBudgetError):
                engine._ask(state,'researcher',{'task':'TEST'})
            self.assertEqual(state['calls'],0)
            self.assertTrue(all(x['scale']==1.0 for x in state.get('budget_calibration',{}).values()))
