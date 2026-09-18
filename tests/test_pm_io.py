"""Ollama protocol and search process tests use synthetic fixtures, never a paid API."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_io import Ollama, Web, TextHTML, public_url, passage

class Handler(BaseHTTPRequestHandler):
    received, mode = [], 'ok'
    def log_message(self, *args): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"models":[{"name":"fixture-model"}]}')
    def do_POST(self):
        type(self).received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(200); self.end_headers()
        chunks = [{'message': {'thinking': 'synthetic hidden draft'}, 'done': False},
                  {'message': {'content': '{"query":"fixture"}'}, 'done': False},
                  {'done': True, 'done_reason': 'length' if type(self).mode == 'length' else 'stop'}]
        for chunk in chunks: self.wfile.write((json.dumps(chunk) + '\n').encode())
        self.wfile.flush()

class Transport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
    def setUp(self):
        Handler.mode, Handler.received = 'ok', []
        self.model = Ollama(Settings(ollama_url=f'http://127.0.0.1:{self.server.server_port}'))
    def test_models(self): self.assertEqual(self.model.models(), ['fixture-model'])
    def test_thinking_not_mixed_into_json(self):
        self.assertEqual(self.model.ask('researcher', {}, lambda: None), {'query': 'fixture'})
        self.assertFalse(Handler.received[0]['think']); self.assertEqual(Handler.received[0]['options']['num_ctx'], 8192)
    def test_critic_thinking_and_json(self):
        self.model.ask('critic', {}, lambda: None)
        self.assertTrue(Handler.received[0]['think']); self.assertEqual(Handler.received[0]['format'], 'json')
    def test_output_limit_not_silent_success(self):
        Handler.mode = 'length'
        with self.assertRaises(ValueError): self.model.ask('planner', {}, lambda: None)
    def test_input_budget_before_request(self):
        with self.assertRaises(ValueError): self.model.ask('planner', {'text': 'x'*100000}, lambda: None)
        self.assertEqual(Handler.received, [])
    def test_cancel_before_request(self):
        def cancel(): raise InterruptedError('stop')
        with self.assertRaises(InterruptedError): self.model.ask('planner', {}, cancel)
        self.assertEqual(Handler.received, [])

class Safety(unittest.TestCase):
    def test_loopback_blocked(self):
        with self.assertRaises(ValueError): public_url('http://127.0.0.1/')
    def test_private_dns_blocked(self):
        with patch('socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.5', 443))]):
            with self.assertRaises(ValueError): public_url('https://example.org/')
    def test_local_file_blocked(self):
        with self.assertRaises(ValueError): public_url('file:///etc/passwd')
    def test_html_script_navigation_removed(self):
        p = TextHTML(); p.feed('<nav>IGNORE</nav><p>Capacity <b>100 t</b></p><script>evil()</script>')
        text = ''.join(p.parts)
        self.assertIn('100 t', text); self.assertNotIn('IGNORE', text); self.assertNotIn('evil', text)
    def test_relevant_passage_bounded(self):
        result = passage('unrelated '*1000 + 'TEST press capacity 100 t. '*10, 'TEST press', 300)
        self.assertLessEqual(len(result), 300); self.assertIn('TEST press', result)

class SearchWorker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); package = root/'ollama_deep_researcher'; package.mkdir()
        source = Path(__file__).resolve().parents[1]/'src'/'ollama_deep_researcher'
        (package/'__init__.py').write_text('__path__.append(' + repr(str(source)) + ')', encoding='utf-8')
        self.utils = package/'utils.py'
        self.utils.write_text('def duckduckgo_search(query, **kw):\n return {"results":[{"url":"https://example.org/a","title":"Synthetic","content":query}]}\nsearxng_search=tavily_search=duckduckgo_search\n', encoding='utf-8')
        self.env = {'PYTHONPATH': str(root) + os.pathsep + os.environ.get('PYTHONPATH', '')}
    def test_control_hook_exists(self): self.assertTrue(hasattr(Web(Settings()), 'check'))
    def test_search_cancel_before_network(self):
        web = Web(Settings())
        def cancel(): raise InterruptedError('stop')
        web.check = cancel
        with self.assertRaises(InterruptedError): web.search('fixture')
    def test_real_subprocess_returns_fixture(self):
        with patch.dict(os.environ, self.env): rows = Web(Settings()).search('fixture')
        self.assertEqual(rows[0]['title'], 'Synthetic'); self.assertIn('fixture', rows[0]['content'])
    def test_real_subprocess_error_visible(self):
        self.utils.write_text('raise RuntimeError("synthetic adapter failure")', encoding='utf-8')
        with patch.dict(os.environ, self.env):
            with self.assertRaisesRegex(RuntimeError, 'synthetic adapter failure'): Web(Settings()).search('fixture')
    def test_running_subprocess_killed_on_cancel(self):
        self.utils.write_text('import time\ntime.sleep(30)', encoding='utf-8')
        web = Web(Settings()); calls, children = [], []; original = subprocess.Popen
        def record(*args, **kwargs):
            child = original(*args, **kwargs); children.append(child); return child
        def cancel():
            calls.append(1)
            if len(calls) >= 3: raise InterruptedError('stop in flight')
        web.check = cancel
        with patch.dict(os.environ, self.env), patch('subprocess.Popen', side_effect=record):
            with self.assertRaises(InterruptedError): web.search('fixture')
        self.assertTrue(children); self.assertIsNotNone(children[0].poll())
