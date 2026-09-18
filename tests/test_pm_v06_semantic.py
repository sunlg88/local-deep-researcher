"""Optional embedding boundaries: no implicit downloads, bounded HTTP and fallback."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import tempfile
import threading
import types
import sys
import unittest
from unittest.mock import patch,MagicMock
from pathlib import Path
from ollama_deep_researcher.pm_types import Settings


class Semantic06(unittest.TestCase):
    def test_disabled_returns_none_without_optional_imports(self):
        from ollama_deep_researcher.pm_semantic_v06 import build_backend
        with patch.dict(sys.modules,{'sentence_transformers':None}):
            self.assertIsNone(build_backend(Settings()))

    def test_cpu_requires_existing_local_model_and_never_downloads(self):
        from ollama_deep_researcher.pm_semantic_v06 import build_backend
        backend=build_backend(Settings(semantic_rerank='on',semantic_model='not/a/downloadable/model'))
        with self.assertRaises(ValueError): backend.score('q',['x'])
        with tempfile.TemporaryDirectory() as d:
            constructor=MagicMock()
            model=constructor.return_value
            model.max_seq_length=100
            model.tokenizer.return_value={'input_ids':[[1,2],[1,2]]}
            model.encode.return_value=types.SimpleNamespace(tolist=lambda:[[1.,0.],[1.,0.]])
            with patch.dict(sys.modules,{'sentence_transformers':types.SimpleNamespace(SentenceTransformer=constructor)}):
                backend=build_backend(Settings(semantic_rerank='on',semantic_model=d))
                self.assertAlmostEqual(backend.score('q',['x'])[0],1.)
            kwargs=constructor.call_args.kwargs
            self.assertEqual(kwargs['device'],'cpu')
            self.assertTrue(kwargs['local_files_only'])
            self.assertFalse(kwargs['trust_remote_code'])

    def test_vector_validation_and_no_zero_vectors(self):
        from ollama_deep_researcher.pm_semantic_v06 import cosine_scores
        self.assertEqual(cosine_scores([[1.,0.],[0.,1.]],1),[0.])
        for rows in ([[1.,0.],[0.,0.]], [[1.,0.],[float('nan'),1]], [[1.,0.],[1.]]):
            with self.assertRaises(ValueError): cosine_scores(rows,1)

    def test_real_http_embedding_transport_does_not_pull_or_truncate(self):
        from ollama_deep_researcher.pm_semantic_v06 import build_backend
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                requests.append((self.path,None))
                self.send_response(200);self.end_headers()
                self.wfile.write(b'{"models":[{"name":"local-embed:latest"}]}')
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path,data))
                self.send_response(200);self.end_headers()
                self.wfile.write(b'{"embeddings":[[1,0],[1,0],[0,1]],"load_duration":100,"prompt_eval_count":10}')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            cfg=Settings(semantic_rerank='on',semantic_backend='ollama',semantic_model='local-embed:latest',
                         ollama_url=f'http://127.0.0.1:{server.server_port}')
            backend=build_backend(cfg)
            self.assertEqual(backend.score('q',['x','y']),[1.,0.])
            self.assertEqual([p for p,d in requests],['/api/tags','/api/embed'])
            self.assertFalse(requests[-1][1]['truncate'])
            self.assertEqual(requests[-1][1]['options']['num_gpu'],0)
        finally:
            server.shutdown();server.server_close();thread.join()
