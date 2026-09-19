"""Explicit provider contract, no hidden substitution."""
import sys
import types
import unittest
from unittest.mock import patch
from ollama_deep_researcher import pm_search_worker_v062 as worker
from ollama_deep_researcher.pm_io_v06 import WebV06, SearchAdapterError
from ollama_deep_researcher.pm_types import Settings
class Search062(unittest.TestCase):
    def test_explicit_ddg_without_provider_fallback(self):
        from dataclasses import make_dataclass
        Row=make_dataclass('Row',['title','href','body']);calls=[]
        class Engine:
            name='duckduckgo';search_url='https://html.duckduckgo.com/html/'
            def __init__(self,**kw):
                calls.append(('init',kw))
                self.http_client=types.SimpleNamespace(request=lambda *a,**k:types.SimpleNamespace(status_code=200,text='<html>results</html>',headers={}))
            def search(self,query,**kw):
                calls.append(('search',query,kw));self.request('POST',self.search_url)
                return [Row('Sheffield Forgemasters SMR','https://example.org/a','SMR vessel')]
        with patch.object(worker,'version',return_value='9.16.0'),patch.dict(sys.modules,{'ddgs.engines.duckduckgo':types.SimpleNamespace(Duckduckgo=Engine)}):
            reply=worker.execute(dict(backend='duckduckgo',query='Sheffield Forgemasters SMR',max_results=3))
        self.assertEqual(calls[-1][2]['page'],1);self.assertNotIn('api_url',calls[0][1])
        self.assertEqual(reply['search_metadata']['endpoint_host'],'html.duckduckgo.com');self.assertFalse(reply['search_metadata']['automatic_fallback'])
        self.assertEqual(reply['results'][0]['content'],'SMR vessel')
    def test_unexpected_engine_fails_before_search(self):
        calls=[]
        class Engine:
            name='bing';search_url='https://bing.com/search'
            def __init__(self,**kw):calls.append(True)
        with patch.object(worker,'version',return_value='9.16.0'),patch.dict(sys.modules,{'ddgs.engines.duckduckgo':types.SimpleNamespace(Duckduckgo=Engine)}):
            with self.assertRaisesRegex(RuntimeError,'provider'):worker.execute(dict(backend='duckduckgo',query='press',max_results=3))
        self.assertFalse(calls)
    def test_dependency_failure_actionable(self):
        with patch.object(worker,'version',side_effect=worker.PackageNotFoundError):
            with self.assertRaisesRegex(RuntimeError,'UPDATE_SEARCH_BACKEND.bat'):WebV06(Settings()).preflight()
    def test_metadata_survives_success(self):
        web=WebV06(Settings());meta={'library':'ddgs','library_version':'9.16.0','backend':'duckduckgo'}
        self.assertEqual(web.search_reply({'results':[],'search_metadata':meta}),[]);self.assertEqual(web.last_search_metadata,meta)
    def test_metadata_survives_failure(self):
        web=WebV06(Settings());meta={'backend':'duckduckgo','library':'ddgs'}
        with self.assertRaises(SearchAdapterError):web.search_reply({'search_metadata':meta,'error':{'type':'RateLimitError','code':429,'retry_after':'30'}})
        self.assertEqual(web.last_search_metadata,meta)
    def test_legacy_worker_unchanged(self):
        from ollama_deep_researcher.pm_io import Web
        self.assertEqual(Web.worker_module,'ollama_deep_researcher.pm_search_worker');self.assertEqual(WebV06.worker_module,'ollama_deep_researcher.pm_search_worker_v062')
    def test_non_200_is_not_reported_as_empty_success(self):
        response=types.SimpleNamespace(status_code=202,text='challenge page',headers={})
        self.assertTrue(hasattr(worker,'checked_response'),'HTTP status was hidden by the library adapter')
        with self.assertRaises(Exception) as caught:worker.checked_response(response,{})
        self.assertEqual(caught.exception.code,202)
        self.assertIn('Retry-After',caught.exception.headers)
    def test_http_200_challenge_is_not_a_successful_zero_result(self):
        response=types.SimpleNamespace(status_code=200,text='<form id="challenge-form" class="anomaly-modal">test</form>',headers={})
        self.assertTrue(hasattr(worker,'checked_response'),'Challenge status inspection missing')
        with self.assertRaises(Exception):worker.checked_response(response,{})
    def test_success_html_remains_exact_for_upstream_parser(self):
        text='<html><div class="result">SMR supplier</div></html>'
        response=types.SimpleNamespace(status_code=200,text=text,headers={})
        self.assertTrue(hasattr(worker,'checked_response'))
        self.assertEqual(worker.checked_response(response,{}),text)
