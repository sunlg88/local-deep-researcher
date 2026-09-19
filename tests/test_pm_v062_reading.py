"""Exploratory topic matches do not override an actual negative extraction."""
import json
import tempfile
import unittest
from unittest.mock import patch
from ollama_deep_researcher.pm_engine_v06 import EngineV06
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_fetch_quality import reuse_key
from ollama_deep_researcher import pm_v06_store as audit, pm_research_metrics as metrics
from v06_fixtures import Model06,Web06
from test_pm_v061_hotfix import intent
class Reading062(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);w=Workspace(self.tmp.name)
        self.pid=w.create_v06('Nuclear forging equipment and supply records',Settings(max_attempts=1,draft_enabled=False))
        self.st=w.open(self.pid);self.model=Model06();self.e=EngineV06(self.st,self.model,Web06(hits=[]));self.s=self.st.load(self.pid)
        self.s['tasks']=[self.e.make_task({'title':'Sheffield Forgemasters ASME records','query':'Sheffield Forgemasters ASME','criteria':['company certificate']},1)]
        self.s['tasks'][0].update(attempts=1,last_intent=json.dumps(intent(gap='ASME pressure vessel certification',keywords=['ASME','pressure vessel','certification'])))
        self.s.update(stage='select',references_v06_scanned=True);self.e.upgrade_budget_checkpoint(self.s)
    def seed(self,body,url='https://guide.example/a',ranges=None):
        title='ASME U Stamp guide';focus=json.loads(self.s['tasks'][0]['last_intent']);did=self.st.add_document(url,title,body,metadata={'research_intent':focus})
        plan={'document_id':did,'reuse_key':reuse_key(body,title),'alias_of':None,'status':'QUEUED',
            'quality':{'status':'READY','score':1,'reasons':['LEGACY']},'query':self.s['topic'],'task_terms':['Sheffield Forgemasters'],
            'ranked':[],'chunks':[],'selected_ranges':ranges or [[0,len(body)]],'pass_number':0,'unread_chars':len(body),'total_chars':len(body)}
        audit.save_plan(self.st,plan);metrics.queue_document(self.st,did);self.s.setdefault('enqueued_documents',[]).append(did)
        self.e._restore_plan(self.s,plan);self.st.save(self.s);return did
    def run_bounded(self):
        with patch.object(self.e.pacer,'wait',return_value=.1):
            for _ in range(160):
                if not self.e.step(self.pid):return
        self.fail('reading did not terminate')
    def test_topic_rich_asme_guide_stops_after_one_negative(self):
        body='ASME U Stamp pressure vessel certification. Application review and inspection requirements. '*160
        did=self.seed(body);self.run_bounded();calls=[p for r,p in self.model.payloads if r=='extractor']
        self.assertEqual(len(calls),1);self.assertEqual(audit.get_plan(self.st,did)['status'],'DEFERRED')
        self.assertEqual(self.st.document(did)['body'],body);self.assertGreater(audit.get_plan(self.st,did)['unread_chars'],0)
        s=self.st.load(self.pid);s.update(stage='select',status='PENDING');self.st.save(s)
        self.e=EngineV06(self.st,self.model,Web06(hits=[]));self.run_bounded();self.assertEqual(len([p for r,p in self.model.payloads if r=='extractor']),1)
    def test_other_document_not_starved_by_first_ranges(self):
        first=self.seed('Sheffield Forgemasters ASME pressure vessel guide. '*80,ranges=[[0,800],[800,1600]])
        second=self.seed('Sheffield Forgemasters ASME company certificate details.',url='https://records.example/b')
        self.s=self.st.load(self.pid);self.e.extract(self.s,self.s['tasks'][0],Settings.from_saved(self.s['settings']))
        self.assertEqual(self.s['document_queue'][0]['did'],second);self.assertTrue(any(x['did']==first for x in self.s['document_queue']))
    def test_entity_fact_at_tail_prevents_deferral(self):
        body='ASME U Stamp certification documentation and pressure vessel inspection. '*120+'\nSheffield Forgemasters has a 500 t pressure vessel contract.'
        did=self.seed(body);self.run_bounded();self.assertNotEqual(audit.get_plan(self.st,did)['status'],'DEFERRED')
        self.assertTrue(any('500 t' in p['source_text'] for r,p in self.model.payloads if r=='extractor'))
    def test_another_task_entity_at_tail_preserved(self):
        other=self.e.make_task({'title':'Doosan Enerbility supply records','query':'Doosan SMR','criteria':['supply']},2)
        other.update(attempts=1,last_intent=json.dumps(intent(entity='Doosan Enerbility',gap='SMR pressure vessel')))
        self.s['tasks'].append(other);self.s.pop('task_catalog_hash',None)
        body='ASME pressure vessel certification records and documentation. '*100+'\nDoosan Enerbility SMR pressure vessel contract data.'
        did=self.seed(body);self.run_bounded();self.assertNotEqual(audit.get_plan(self.st,did)['status'],'DEFERRED')
