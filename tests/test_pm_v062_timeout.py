"""Configured inference timeout reaches the HTTP transport boundary."""
import json
import unittest
from unittest.mock import patch
from ollama_deep_researcher.pm_io import Ollama
from ollama_deep_researcher.pm_types import Settings
class Timeout062(unittest.TestCase):
    def test_v06_first_token_timeout_not_silently_capped(self):
        seen=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def __iter__(self):return iter([(json.dumps({'message':{'content':'{}'},'done':True,'done_reason':'stop'})+'\n').encode()])
        class Opener:
            def open(self,request,timeout):seen.append(timeout);return Response()
        with patch('ollama_deep_researcher.pm_io.opener',return_value=Opener()):
            Ollama(Settings(request_timeout=240)).ask('researcher',{'_pm_version':6},lambda:None)
        self.assertEqual(seen,[240])
    def test_legacy_timeout_policy_unchanged(self):
        seen=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def __iter__(self):return iter([b'{"message":{"content":"{}"},"done":true,"done_reason":"stop"}\n'])
        class Opener:
            def open(self,request,timeout):seen.append(timeout);return Response()
        with patch('ollama_deep_researcher.pm_io.opener',return_value=Opener()):
            Ollama(Settings(request_timeout=240)).ask('researcher',{'_pm_version':5},lambda:None)
        self.assertEqual(seen,[60])
