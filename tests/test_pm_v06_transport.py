"""Search failures must not be disguised as an empty research result."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys
import types
from urllib.error import HTTPError


class Transport06(unittest.TestCase):
    def test_backend_exception_not_converted_to_empty_list(self):
        from ollama_deep_researcher.pm_search_worker_v06 import execute
        factory=MagicMock()
        with patch.dict(sys.modules, {'duckduckgo_search':types.SimpleNamespace(DDGS=factory)}):
            factory.return_value.__enter__.return_value.text.side_effect = RuntimeError('backend denied')
            with self.assertRaises(RuntimeError):
                execute({'backend':'duckduckgo','query':'TEST press','max_results':6})

    def test_error_envelope_has_code_and_retry_after_but_not_secrets(self):
        from ollama_deep_researcher.pm_search_worker_v06 import error_envelope
        error=HTTPError('https://example.org/?key=secret',429,'secret access token',{'Retry-After':'120'},None)
        data=error_envelope(error)
        self.assertEqual(data['error']['code'],429)
        self.assertEqual(data['error']['retry_after'],'120')
        self.assertNotIn('secret',json.dumps(data))

    def test_transport_interprets_failure_envelope(self):
        from ollama_deep_researcher.pm_io_v06 import SearchAdapterError, parse_search_reply
        with self.assertRaises(SearchAdapterError) as caught:
            parse_search_reply({'error':{'type':'RateLimitError','code':429,'retry_after':'120'}})
        self.assertEqual(caught.exception.code,429)
        self.assertEqual(caught.exception.headers['Retry-After'],'120')
        self.assertEqual(parse_search_reply({'results':[]}),[])
        with self.assertRaises(ValueError): parse_search_reply({'unexpected':True})

    def test_v06_uses_own_worker_without_changing_v05_worker(self):
        from ollama_deep_researcher.pm_io import Web
        from ollama_deep_researcher.pm_io_v06 import WebV06
        self.assertEqual(Web.worker_module,'ollama_deep_researcher.pm_search_worker')
        self.assertEqual(WebV06.worker_module,'ollama_deep_researcher.pm_search_worker_v06')

    def test_worker_bounds_results_and_retains_real_snippets(self):
        from ollama_deep_researcher.pm_search_worker_v06 import execute
        factory=MagicMock()
        with patch.dict(sys.modules, {'duckduckgo_search':types.SimpleNamespace(DDGS=factory)}):
            factory.return_value.__enter__.return_value.text.return_value = [
                {'href':'https://example.org/a','title':'TEST press','body':'TEST capacity'}]*8
            reply=execute({'backend':'duckduckgo','query':'TEST press','max_results':6})
        self.assertEqual(len(reply['results']),6)
        self.assertEqual(reply['results'][0]['content'],'TEST capacity')
        with self.assertRaises(ValueError): execute({'backend':'duckduckgo','query':'x','max_results':10000})
