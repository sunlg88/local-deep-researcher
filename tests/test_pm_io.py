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
        self.assertTrue(Handler.received[0]['think']); self.assertEqual(Handler.received[0]['format']['type'], 'object'); self.assertIn('checks', Handler.received[0]['format']['properties'])
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

class SourceTransport(unittest.TestCase):
    def response(self, data, kind):
        import email.message
        import io
        from unittest.mock import MagicMock
        headers = email.message.Message()
        headers['Content-Type'] = kind
        response = MagicMock()
        response.headers = headers
        response.read1.side_effect = io.BytesIO(data).read
        response.__enter__.return_value = response
        return response

    def test_full_html_raw_and_unicode_request_are_preserved(self):
        from unittest.mock import MagicMock
        raw = '<html><p>\ud55c\uae00 source text with original markup.</p></html>'.encode()
        response = self.response(raw, 'text/html; charset=utf-8')
        http = MagicMock(); http.open.return_value = response
        with patch('ollama_deep_researcher.pm_io.public_url', side_effect=lambda x:x), patch('ollama_deep_researcher.pm_io.opener', return_value=http):
            result = Web(Settings()).fetch_document('https://example.org/\ud55c\uae00')
        self.assertEqual(result.raw, raw)
        self.assertIn('\ud55c\uae00 source', result.body)
        self.assertNotIn('<p>', result.body)
        http.open.call_args.args[0].full_url.encode('ascii')
        self.assertIn('final_url', result.metadata)

    def test_invalid_pdf_returns_original_and_parse_failure(self):
        from unittest.mock import MagicMock
        raw = b'%PDF-1.7\ninvalid fixture'
        http=MagicMock(); http.open.return_value=self.response(raw, 'application/pdf')
        with patch('ollama_deep_researcher.pm_io.public_url',side_effect=lambda x:x), patch('ollama_deep_researcher.pm_io.opener',return_value=http):
            result=Web(Settings()).fetch_document('https://example.org/a.pdf')
        self.assertEqual(result.raw,raw)
        self.assertEqual(result.body,'')
        self.assertEqual(result.metadata['parse_status'],'PARSE_FAILED')

    def test_pdf_parser_process_terminates_on_user_stop(self):
        from ollama_deep_researcher import pm_io
        self.assertTrue(hasattr(pm_io, 'parse_pdf_isolated'))
        children=[]; original=subprocess.Popen
        def record(*args,**kwargs):
            child=original(*args,**kwargs);children.append(child);return child
        def stop(): raise InterruptedError('user stopped parser')
        with patch('subprocess.Popen',side_effect=record):
            with self.assertRaises(InterruptedError):
                pm_io.parse_pdf_isolated(b'%PDF-1.7\nfixture', stop)
        self.assertTrue(children)
        self.assertIsNotNone(children[0].poll())

    def test_schema_is_sent_to_ollama_instead_of_json_string(self):
        from ollama_deep_researcher.pm_prompts import SCHEMAS
        self.assertEqual(SCHEMAS['extractor']['properties']['relevance']['enum'],['relevant','uncertain','irrelevant'])
        self.assertEqual(SCHEMAS['extractor']['properties']['claims']['maxItems'],3)
