"""v0.5 pipeline via real local HTTP streaming; all research contents are fixtures."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.error import HTTPError
from unittest.mock import MagicMock,patch
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_engine_v05 import EngineV05
from ollama_deep_researcher.pm_io import Ollama,Web
from ollama_deep_researcher.pm_prompts import OutputLimitError
from ollama_deep_researcher import pm_research_metrics as metrics
from v05_fixtures import Model05,Web05
from test_pm import TEXT


class Acceptance05(unittest.TestCase):
    def test_full_pipeline_streaming_http_scoped_search_single_pass_and_handoff(self):
        oracle=Model05(task_count=8)
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a): pass
            def do_POST(self):
                r=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(r)
                props=r['format']['properties']
                role=('planner' if 'tasks' in props else 'extractor' if 'relevance' in props else
                      'critic' if 'checks' in props else 'writer' if 'summary' in props else 'researcher')
                payload=json.loads(r['messages'][1]['content'])
                reply=oracle.ask(role,payload,lambda:None)
                if role=='researcher': reply['query']+=' site:cn'
                self.send_response(200);self.end_headers()
                content=json.dumps(reply,ensure_ascii=False)
                for part in [content[:len(content)//2],content[len(content)//2:]]:
                    self.wfile.write((json.dumps({'message':{'content':part},'done':False})+'\n').encode())
                self.wfile.write((json.dumps({'done':True,'done_reason':'stop','prompt_eval_count':900,
                                  'eval_count':100,'eval_duration':1000000000})+'\n').encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as root:
                cfg=Settings(ollama_url=f'http://127.0.0.1:{server.server_port}',
                             min_tasks=8,max_tasks=8,max_attempts=1)
                w=Workspace(root);pid=w.create('TEST press nuclear source study',cfg,'\ud55c\uae00 \uc5f0\uad6c \uc9c0\uc2dc. '*30)
                st=w.open(pid)
                body='Context on TEST press. '*150+'\ud55c\uae00 \uc6d0\ubb38 \uc870\uac74. '*400+'\n'+TEXT
                class Sources(Web05):
                    def fetch(self,url):
                        self.fetches.append(url)
                        if 'denied.cn' in url: raise HTTPError(url,403,'denied',{},None)
                        return self.text
                hits=[{'url':'https://microsoft.com/unrelated','title':'TEST press'},
                      {'url':'https://example.cn/source','title':'TEST press'},
                      {'url':'https://denied.cn/a','title':'TEST press'},
                      {'url':'https://denied.cn/b','title':'TEST press'}]
                web=Sources(hits,text=body)
                EngineV05(st,Ollama(cfg),web).run(pid)
                final=st.load(pid)
                self.assertEqual(final['status'],'NO_NEW_WORK')
                self.assertEqual(len(final['tasks']),8)
                self.assertNotIn('https://microsoft.com/unrelated',web.fetches)
                self.assertEqual(web.fetches.count('https://example.cn/source'),1)
                self.assertEqual(st.counts(),{'documents':1,'evidence':1})
                spans=[p['source_range'] for role,p in oracle.payloads if role=='extractor']
                self.assertEqual(len(spans),len({(x['start'],x['end']) for x in spans}))
                self.assertTrue(any(body[x['start']:x['end']].endswith(TEXT) for x in spans))
                self.assertTrue(all(t['evidence_ids'] for t in final['tasks']))
                self.assertTrue(all(r['options']['num_ctx']==8192 for r in requests))
                self.assertFalse([e for e in st.events(pid,500) if e['kind']=='WORK_ERROR'])
                self.assertTrue(metrics.host_in_cooldown(st,'denied.cn'))
                pack=st.export(pid)
                evidence=json.loads((pack/'evidence.json').read_text(encoding='utf-8'))
                self.assertEqual(len(evidence[0]['task_ids']),8)
                self.assertEqual(st.raw_document(st.all_documents()[0]['id']),body.encode())
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_redirect_outside_query_site_rejected_before_second_http_request(self):
        web=Web(Settings())
        web.source_allowed=lambda url: url.startswith('https://example.cn/')
        headers={'Location':'https://microsoft.com/outside'}
        transport=MagicMock();transport.open.side_effect=HTTPError('https://example.cn/a',302,'redirect',headers,None)
        with patch('ollama_deep_researcher.pm_io.opener',return_value=transport), \
             patch('ollama_deep_researcher.pm_io.public_url',side_effect=lambda x:x):
            with self.assertRaises(ValueError):web.fetch_document('https://example.cn/a')
        self.assertEqual(transport.open.call_count,1)

    def test_failed_extraction_not_mislabelled_zero_yield(self):
        class Broken(Model05):
            def ask(self,r,p,c):
                if r=='extractor':raise OutputLimitError('Synthetic failure')
                return super().ask(r,p,c)
        with tempfile.TemporaryDirectory() as root:
            w=Workspace(root);pid=w.create('TEST press',Settings(max_attempts=1,draft_enabled=False))
            st=w.open(pid)
            EngineV05(st,Broken(),Web05()).run(pid)
            f=metrics.task_search_feedback(st,'t001')[0]
            self.assertEqual(f['outcome'],'EXTRACTION_FAILED')
            self.assertEqual(st.counts()['documents'],1)

    def test_different_projects_do_not_share_range_cache(self):
        with tempfile.TemporaryDirectory() as root:
            w=Workspace(root);cfg=Settings(max_attempts=1,draft_enabled=False)
            one=w.create('TEST press',cfg);two=w.create('different topic',cfg)
            m=Model05();EngineV05(w.open(one),m,Web05()).run(one)
            other=Model05();EngineV05(w.open(two),other,Web05([])).run(two)
            self.assertFalse([r for r,p in other.payloads if r=='extractor'])
            self.assertEqual(w.open(two).counts(),{'documents':0,'evidence':0})
