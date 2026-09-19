"""Real loopback HTTP transport; synthetic replies do not exercise a real model."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_engine_v06 import EngineV06
from ollama_deep_researcher.pm_engine import ControlRequested
from ollama_deep_researcher.pm_io import Ollama
from v06_fixtures import Web06


class Handler061(BaseHTTPRequestHandler):
    mode='bad_json'
    def log_message(self,*args): pass
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        if self.mode=='http':
            self.send_response(503);self.end_headers();return
        self.send_response(200);self.end_headers()
        if self.mode=='bad_line':
            self.wfile.write(b'not json\n');return
        content='not valid JSON' if self.mode=='bad_json' else '{}'
        self.wfile.write((json.dumps({'message':{'content':content},'done':False})+'\n').encode())
        if self.mode!='unfinished':
            self.wfile.write((json.dumps({'done':True,'done_reason':'length' if self.mode=='length' else 'stop',
                 'prompt_eval_count':100,'eval_count':30})+'\n').encode())


class Diagnostic061(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler061)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        cfg=Settings(ollama_url=f'http://127.0.0.1:{self.server.server_port}')
        w=Workspace(self.tmp.name);self.pid=w.create_v06('TEST press',cfg)
        self.st=w.open(self.pid);self.e=EngineV06(self.st,Ollama(cfg),Web06())
    def failure(self,mode):
        Handler061.mode=mode
        with self.assertRaises(Exception):self.e._ask(self.st.load(self.pid),'researcher',{})
        return next(json.loads(r['message']) for r in self.st.events(self.pid,50) if r['kind']=='MODEL_FAILED')
    def test_bad_final_json_records_decode_stage_not_gpu_failure(self):
        event=self.failure('bad_json')
        self.assertEqual(event.get('error_type'),'JSONDecodeError')
        self.assertEqual(event.get('error_stage'),'response_json')
        self.assertEqual(event['done_reason'],'stop')
    def test_bad_stream_line_records_stream_stage(self):
        self.assertEqual(self.failure('bad_line').get('error_stage'),'stream_decode')
    def test_unfinished_stream_is_not_output_limit(self):
        event=self.failure('unfinished')
        self.assertEqual(event.get('error_type'),'ValueError')
        self.assertEqual(event.get('error_stage'),'stream_completion')
    def test_http_failure_keeps_code_not_server_response_body(self):
        event=self.failure('http')
        self.assertEqual(event.get('error_type'),'HTTPError')
        self.assertEqual(event.get('http_status'),503)
    def test_length_failure_keeps_limit_reason(self):
        event=self.failure('length')
        self.assertEqual(event.get('error_type'),'OutputLimitError')
        self.assertEqual(event.get('error_stage'),'output_limit')
        self.assertEqual(event['done_reason'],'length')
    def test_user_cancel_not_reported_as_model_failure(self):
        class Cancel:
            last_metrics={}
            def ask(self,*args):raise ControlRequested()
        self.e.model=Cancel()
        with self.assertRaises(ControlRequested):self.e._ask(self.st.load(self.pid),'researcher',{})
        kinds=[r['kind'] for r in self.st.events(self.pid,50)]
        self.assertIn('MODEL_CANCELLED',kinds);self.assertNotIn('MODEL_FAILED',kinds)

    def test_failure_metrics_cannot_mask_the_original_exception(self):
        class Bad:
            last_metrics={}
            def ask(self,*args):
                self.last_metrics={'error_type':'untrusted-metric','error_message':'wrong'}
                raise TimeoutError('original timeout')
        self.e.model=Bad()
        with self.assertRaisesRegex(TimeoutError,'original timeout'):
            self.e._ask(self.st.load(self.pid),'researcher',{})
        event=next(json.loads(r['message']) for r in self.st.events(self.pid,50) if r['kind']=='MODEL_FAILED')
        self.assertEqual(event['error_type'],'TimeoutError')
    def test_bad_role_result_is_not_labelled_complete_transport_stage(self):
        class Bad:
            last_metrics={}
            def ask(self,*args):
                self.last_metrics={'response_stage':'complete'}
                return []
        self.e.model=Bad()
        with self.assertRaises(ValueError):self.e._ask(self.st.load(self.pid),'researcher',{})
        event=next(json.loads(r['message']) for r in self.st.events(self.pid,50) if r['kind']=='MODEL_FAILED')
        self.assertEqual(event['error_stage'],'role_result_validation')
    def test_configured_secret_is_redacted_from_work_error_too(self):
        secret='private-fixture-api-token-123'
        with patch.dict(os.environ,{'TAVILY_API_KEY':secret}):
            self.e.recover(self.st.load(self.pid),'research',RuntimeError('request '+secret),Settings())
        event=next(r['message'] for r in self.st.events(self.pid,50) if r['kind']=='WORK_ERROR')
        self.assertNotIn(secret,event)
