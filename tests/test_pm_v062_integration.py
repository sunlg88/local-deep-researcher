"""Real loopback HTTP boundary with bounded history and a corrective request."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from ollama_deep_researcher.pm_engine_v06 import EngineV06
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_io import Ollama
from v06_fixtures import Web06
from test_pm_v062_queries import PROPOSAL
class Handler(BaseHTTPRequestHandler):
    requests=[]
    def log_message(self,*args):pass
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.requests.append(data)
        result=dict(PROPOSAL,search_phrases=['verify all requested values, contracts and certifications']) if len(self.requests)==1 else PROPOSAL
        self.send_response(200);self.end_headers()
        self.wfile.write((json.dumps({'message':{'content':json.dumps(result)},'done':True,'done_reason':'stop','prompt_eval_count':1234,'eval_count':100})+'\n').encode())
class Integration062(unittest.TestCase):
    def test_large_history_bad_first_reply_repairs_and_searches(self):
        with tempfile.TemporaryDirectory() as td:
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);Handler.requests=[]
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            cfg=Settings(ollama_url=f'http://127.0.0.1:{server.server_port}',draft_enabled=False)
            w=Workspace(td);pid=w.create_v06('Sheffield Forgemasters SMR equipment',cfg,'Keep all source conditions and originals.')
            st=w.open(pid);web=Web06(hits=[]);engine=EngineV06(st,Ollama(cfg),web)
            s=st.load(pid);task=engine.make_task({'title':'Sheffield Forgemasters manufacturing','query':'SMR','criteria':['capacity and contract evidence']},1)
            task['queries']=['previous '+str(i)+' '+('history '*80) for i in range(50)]
            s.update(tasks=[task],stage='research',current_task=task['id'],status='PENDING');st.save(s)
            history=[{'query':'historic '+('old context '*70),'outcome':'ZERO_YIELD','unused_details':'audit '*100}]*8
            with patch.object(engine,'_task_feedback',return_value=history),patch.object(engine.pacer,'wait',return_value=.1):engine.research(s,task,cfg)
            self.assertEqual(len(Handler.requests),2);self.assertEqual(len(web.queries),1)
            first,repair=Handler.requests;self.assertIn('search_phrases',repair['format']['properties'])
            self.assertLess(len(json.dumps(repair['messages'])),len(json.dumps(first['messages'])))
            self.assertEqual(repair['options']['num_ctx'],8192);self.assertIn('Keep all source conditions and originals.',repair['messages'][1]['content'])
            self.assertEqual(len(task['queries']),51)
            kinds=[e['kind'] for e in st.events(pid,200)]
            self.assertIn('CONTEXT_COMPACTED',kinds);self.assertIn('INTENT_RETRY',kinds);self.assertIn('SEARCH_COMPLETED',kinds)
            server.shutdown();thread.join(timeout=2)
    def test_dependency_failure_precedes_planner(self):
        with tempfile.TemporaryDirectory() as td:
            w=Workspace(td);pid=w.create_v06('equipment',Settings());st=w.open(pid)
            class Web:
                def preflight(self):raise RuntimeError('UPDATE_SEARCH_BACKEND.bat')
            class Model:
                def ask(self,*args):raise AssertionError('Must not run model')
            with self.assertRaisesRegex(RuntimeError,'UPDATE_SEARCH_BACKEND.bat'):EngineV06(st,Model(),Web())
            self.assertEqual(st.load(pid)['calls'],0)
