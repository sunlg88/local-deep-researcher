"""Regression gates for omissions, alias attribution, budgets and scientific scope."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from v06_fixtures import Model06,Web06,TEXT


class Edges06(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=Workspace(self.tmp.name)

    def engine(self,**settings):
        from ollama_deep_researcher.pm_engine_v06 import EngineV06
        pid=self.w.create_v06('TEST press',Settings(max_attempts=1,draft_enabled=False,**settings))
        st=self.w.open(pid)
        return pid,st,EngineV06(st,Model06(),Web06())

    def test_body_alias_search_is_pending_then_inherits_source_evidence_attribution(self):
        from ollama_deep_researcher import pm_research_metrics as metrics
        from ollama_deep_researcher import pm_v06_store as audit
        pid,st,e=self.engine()
        state=st.load(pid)
        state['tasks']=[e.make_task({'title':'TEST press','query':'TEST press','criteria':['capacity']},1)]
        e.upgrade_budget_checkpoint(state)
        first=st.add_document('https://example.org/a','same title',TEXT)
        second=st.add_document('https://example.net/b','same title',TEXT)
        e.enqueue(state,state['tasks'][0],first);e.enqueue(state,state['tasks'][0],second)
        aid=metrics.begin_search_attempt(st,pid,'t001','TEST press','broad',['TEST','press'])
        metrics.link_attempt_document(st,aid,second,True)
        metrics.finish_search_attempt(st,aid)
        self.assertEqual(e._attempt_feedback(aid)['outcome'],'EXTRACTION_PENDING')
        state['stage']='select';st.save(state)
        with patch.object(e.pacer,'wait',return_value=.1): e.run(pid)
        self.assertEqual(e._attempt_feedback(aid)['accepted_claims'],1)
        self.assertEqual(audit.project_counters(st)['unread_characters'],0)

    def test_short_numeric_section_keeps_fetched_heading_context(self):
        from ollama_deep_researcher.pm_fetch_quality import assess_fetched_page
        result=assess_fetched_page(query_anchors=['TEST press capacity'],search_title='TEST press capacity',
            search_snippet='14000 tonnes',fetched_title='TEST press capacity',body='14000 tonnes\n500 t')
        self.assertEqual(result.status,'READY')
        from ollama_deep_researcher.pm_retrieval_v06 import rank_chunks
        from ollama_deep_researcher.pm_chunking_v06 import SourceChunk
        chunk=SourceChunk('n',0,19,'14000 tonnes\n500 t',heading='TEST press capacity')
        self.assertGreater(rank_chunks('TEST press capacity',[chunk],[])[0].combined_score,0)

    def test_quality_feedback_retains_six_outcomes_for_stop_policy(self):
        from ollama_deep_researcher import pm_research_metrics as m
        pid,st,e=self.engine(optimization_mode='quality')
        for i in range(6):
            aid=m.begin_search_attempt(st,pid,'t001',f'TEST {i}','broad',['TEST','press'])
            m.finish_search_attempt(st,aid)
        self.assertEqual(len(e._task_feedback('t001')),6)

    def test_failed_optional_embedding_keeps_running_with_lexical(self):
        pid,st,e=self.engine(semantic_rerank='on',semantic_model='no/such/local/weights')
        with patch.object(e.pacer,'wait',return_value=.1): e.run(pid)
        self.assertEqual(st.counts()['evidence'],1)

    def test_different_title_scope_is_never_body_aliased(self):
        pid,st,e=self.engine()
        e.web=Web06([{'url':'https://example.org/a','title':'TEST press 2024','content':TEXT},
                     {'url':'https://example.net/b','title':'TEST press 2026','content':TEXT}])
        with patch.object(e.pacer,'wait',return_value=.1): e.run(pid)
        self.assertEqual(len([p for r,p in e.model.payloads if r=='extractor']),2)

    def test_real_ollama_http_contract_is_v06_and_preserves_all_input(self):
        from ollama_deep_researcher.pm_engine_v06 import EngineV06
        from ollama_deep_researcher.pm_io import Ollama
        oracle=Model06();received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append(request)
                props=request['format']['properties']
                role=('planner' if 'tasks' in props else 'researcher' if 'entity' in props else
                      'extractor' if 'relevance' in props else 'critic' if 'checks' in props else 'writer')
                payload=json.loads(request['messages'][1]['content'])
                result=oracle.ask(role,payload,lambda:None)
                self.send_response(200);self.end_headers()
                self.wfile.write((json.dumps({'message':{'content':json.dumps(result)},'done':True,
                    'done_reason':'stop'})+'\n').encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            cfg=Settings(max_attempts=1,draft_enabled=False,ollama_url=f'http://127.0.0.1:{server.server_port}')
            pid=self.w.create_v06('TEST press',cfg,'Preserve every condition')
            st=self.w.open(pid);e=EngineV06(st,Ollama(cfg),Web06())
            with patch.object(e.pacer,'wait',return_value=.1):e.run(pid)
            self.assertEqual(st.counts()['evidence'],1)
            self.assertTrue(all(r['options']['num_ctx']==8192 for r in received))
            self.assertTrue(all(json.loads(r['messages'][1]['content'])['instructions']=='Preserve every condition' for r in received))
            researcher=next(r for r in received if 'entity' in r['format']['properties'])
            self.assertIn('site_hint',researcher['format']['properties'])
            self.assertNotIn('followups',researcher['format']['properties'])
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_html_title_is_source_metadata_not_search_snippet(self):
        from ollama_deep_researcher.pm_documents import decode_document
        result=decode_document(b'<html><title>Actual Title</title><body>data 100</body></html>','text/html')
        self.assertEqual(result['metadata'].get('title'),'Actual Title')

    def test_off_mode_never_calls_injected_semantic_backend(self):
        from ollama_deep_researcher.pm_engine_v06 import EngineV06
        class Probe:
            name='fixture';calls=0
            def score(self,q,texts): self.calls+=1;return [1.] * len(texts)
        probe=Probe();pid,st,old=self.engine()
        web=Web06(bodies={'https://example.org/a':('Background passage. '*200+'\n\n')*3+TEXT})
        e=EngineV06(st,Model06(),web,semantic_backend=probe)
        with patch.object(e.pacer,'wait',return_value=.1):e.run(pid)
        self.assertEqual(probe.calls,0)

    def test_restart_after_interrupted_ranking_does_not_strand_original(self):
        from ollama_deep_researcher.pm_engine import ControlRequested
        pid,st,e=self.engine()
        def pause(*args,**kwargs):
            st.control(pid,'PAUSE')
            raise ControlRequested()
        with patch.object(e.pacer,'wait',return_value=.1):
            with patch('ollama_deep_researcher.pm_single_pass_v06.rank_chunks',side_effect=pause):
                e.run(pid)
            self.assertEqual(st.load(pid)['status'],'PAUSED')
            self.assertEqual(st.counts()['documents'],1)
            st.control(pid,'RUN')
            e.run(pid)
        self.assertEqual(st.counts()['evidence'],1)
        self.assertEqual(len(e.web.fetches),1)
