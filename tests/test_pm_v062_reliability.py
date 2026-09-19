"""Bound model views independently of persistent audit history."""
import copy
import json
import tempfile
import unittest
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_engine_v06 import EngineV06
from ollama_deep_researcher.pm_budget import request_parts, FixedPromptBudgetError
from v06_fixtures import Model06, Web06

class Context062(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg=Settings(max_attempts=2,draft_enabled=False);self.w=Workspace(self.tmp.name)
        self.instructions='Keep original conditions 2026, BWRX-300, not cancelled. '+('\uc6d0\ubb38 \ubcf4\uc874. '*45)
        self.pid=self.w.create_v06('Nuclear forging supply',self.cfg,self.instructions)
        self.st=self.w.open(self.pid);self.model=Model06();self.e=EngineV06(self.st,self.model,Web06(hits=[]))
    def large_payload(self):
        return {'task':'Sheffield Forgemasters contracts','criteria':[{'id':'c1','text':'SMR contracts'}],
            'known_domains':['example.org'],'previous_queries':['past query '+str(i)+' x'*100 for i in range(30)],
            'feedback':['No new sources. '*100]*20,
            'attempt_feedback':[{'id':str(i),'query':'old query '*100,'outcome':'ZERO_YIELD',
                'anchors':['x'*100]*6,'error':'old failure '*150,'pending_documents':0,'accepted_claims':0} for i in range(30)],
            'evidence':[{'id':'e1','quote':'A large original '*300,'claim_text':'Original condition preserved'}]}
    def test_oversized_history_fits_without_changing_saved_question(self):
        payload=self.large_payload();before=copy.deepcopy(payload)
        try:self.e._ask(self.st.load(self.pid),'researcher',payload)
        except FixedPromptBudgetError as exc:self.fail('Optional history blocked request: '+str(exc))
        sent=self.model.payloads[-1][1];m=request_parts(self.cfg,'researcher',sent)[2]
        self.assertLessEqual(m['estimated_input_tokens'],m['input_budget']-128)
        self.assertEqual(sent['instructions'],self.instructions);self.assertEqual(self.st.load(self.pid)['instructions'],self.instructions)
        self.assertEqual(before['attempt_feedback'][0]['anchors'],['x'*100]*6);self.assertLess(len(json.dumps(sent)),len(json.dumps(before)))
    def test_repair_request_is_really_smaller(self):
        s=self.st.load(self.pid)
        try:
            self.e._ask(s,'researcher',self.large_payload())
            self.e._ask(s,'researcher',dict(self.large_payload(),compact_retry=True,feedback=['Invalid structured intent: choose one search target']))
        except FixedPromptBudgetError as exc:self.fail('Repair not bounded: '+str(exc))
        one,two=[v for r,v in self.model.payloads]
        self.assertLess(request_parts(self.cfg,'researcher',two)[2]['prompt_bytes'],request_parts(self.cfg,'researcher',one)[2]['prompt_bytes'])
        self.assertEqual(two['instructions'],one['instructions']);self.assertIn('Invalid structured intent',two['feedback'][0])
    def test_impossible_immutable_core_not_truncated(self):
        s=self.st.load(self.pid);s['instructions']='\uc790'*10000
        with self.assertRaises(FixedPromptBudgetError):self.e._ask(s,'researcher',{})
        self.assertEqual(s['instructions'],'\uc790'*10000);self.assertEqual(self.model.payloads,[])
    def test_50_growing_histories_remain_bounded(self):
        s=self.st.load(self.pid)
        for n in range(1,51):
            p=self.large_payload();p['attempt_feedback']=p['attempt_feedback']*n
            try:self.e._ask(s,'researcher',p)
            except FixedPromptBudgetError as exc:self.fail('Unbounded cycle '+str(n)+': '+str(exc))
            self.assertLessEqual(request_parts(self.cfg,'researcher',self.model.payloads[-1][1])[2]['estimated_input_tokens'],6400)
        self.assertEqual(len(self.model.payloads),50)
