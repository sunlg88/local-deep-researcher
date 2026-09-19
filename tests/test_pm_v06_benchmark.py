"""Offline metrics are operational measurements, never model accuracy claims."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest


class Benchmark06(unittest.TestCase):
    def test_events_with_real_log_envelopes_and_unique_evidence(self):
        from ollama_deep_researcher.pm_benchmark import metrics_from_events
        events = [
            {'kind': 'extractor', 'message': json.dumps({'call': 1, 'status': 'started'})},
            {'kind': 'MODEL_COMPLETED', 'message': json.dumps({'call': 1, 'role': 'extractor', 'prompt_eval_count': 1400})},
            {'kind': 'EXTRACTION_COMPLETED', 'message': json.dumps({'document': 'd0', 'start': 0, 'end': 20, 'relevance': 'irrelevant', 'accepted_claims': 0})},
            {'kind': 'MODEL_COMPLETED', 'role': 'extractor', 'call': 2, 'prompt_eval_count': 900},
            {'kind': 'EXTRACTION_COMPLETED', 'document': 'd1', 'start': 0, 'end': 30, 'relevance': 'relevant', 'accepted_claims': 2},
        ]
        m = metrics_from_events(events, [{'id': 'e1'}, {'id': 'e2'}, {'id': 'e1'}])
        self.assertEqual((m.model_calls, m.extractor_calls, m.extractor_prompt_tokens), (2, 2, 2300))
        self.assertEqual((m.irrelevant_extractions, m.accepted_claims, m.unique_relevant_documents), (1, 2, 1))

    def test_unmeasured_tokens_remain_unknown_not_estimated_as_actual(self):
        from ollama_deep_researcher.pm_benchmark import metrics_from_events
        m = metrics_from_events([{'kind': 'MODEL_COMPLETED', 'role': 'extractor', 'estimated_input_tokens': 500}], [])
        self.assertIsNone(m.extractor_prompt_tokens)
        self.assertEqual(m.estimated_extractor_tokens, 500)

    def test_compare_undefined_ratios_and_zero_base(self):
        from ollama_deep_researcher.pm_benchmark import metrics_from_events, compare_efficiency
        m = metrics_from_events([], [])
        r = compare_efficiency(m, m)
        self.assertIsNone(r['claims_per_model_call']['baseline'])
        self.assertIsNone(r['extractor_prompt_token_reduction'])
        self.assertEqual(r['accepted_claims_delta'], 0)

    def test_fetch_failures_do_not_count_as_success_and_retries_not_duplicates(self):
        from ollama_deep_researcher.pm_benchmark import metrics_from_events
        events = [{'kind':'MODEL_FAILED','role':'extractor','call':1,'prompt_eval_count':80},
                  {'kind':'MODEL_COMPLETED','role':'extractor','call':2,'prompt_eval_count':100},
                  {'kind':'SEARCH_COMPLETED','attempt':'s1','fetched':3,'fetch_failed':1,'outcome':'ZERO_YIELD'},
                  {'kind':'SEARCH_COMPLETED','attempt':'s1','fetched':3,'fetch_failed':1,'outcome':'ZERO_YIELD'}]
        m = metrics_from_events(events, [])
        self.assertEqual(m.extractor_calls, 2)
        self.assertEqual(m.extractor_prompt_tokens, 180)
        self.assertEqual((m.fetch_attempts,m.fetch_failures,m.zero_yield_searches),(3,1,1))

    def test_replay_round_trip_and_hash_tampering(self):
        from ollama_deep_researcher.pm_benchmark import write_replay_bundle, load_replay_bundle
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            d={'id':'d1','url':'https://example.org/a','title':'Fixture','body':'Original text\n'}
            write_replay_bundle(p,[d],[],[],{'fixture':True})
            r=load_replay_bundle(p)
            self.assertEqual(r['documents'][0]['body'],d['body'])
            (p/r['documents'][0]['body_path']).write_text('changed',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'hash'):
                load_replay_bundle(p)

    def test_replay_path_traversal_rejected(self):
        from ollama_deep_researcher.pm_benchmark import write_replay_bundle, load_replay_bundle
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            write_replay_bundle(p,[{'id':'d1','url':'https://example.org/a','title':'x','body':'text'}],[],[],{})
            m=json.loads((p/'manifest.json').read_text())
            m['documents'][0]['body_path']='../outside.txt'
            (p/'manifest.json').write_text(json.dumps(m))
            with self.assertRaises(ValueError): load_replay_bundle(p)

    def test_malformed_event_not_silently_ignored(self):
        from ollama_deep_researcher.pm_benchmark import metrics_from_events
        with self.assertRaises(ValueError):
            metrics_from_events([{'kind':'MODEL_COMPLETED','message':'not json'}],[])

class HandoffComparisonCLI(unittest.TestCase):
    def test_partial_old_log_is_reported_not_a_complete_baseline(self):
        from ollama_deep_researcher.pm_benchmark import read_handoff_metrics
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'events.jsonl').write_text(json.dumps({'kind':'MODEL_COMPLETED','call':9,'role':'planner'})+'\n')
            (root/'evidence.json').write_text('[]')
            (root/'evidence_pack.json').write_text(json.dumps({'project':{'calls':9,'settings':{'model':'fixture'}}}))
            result=read_handoff_metrics(root)
            self.assertFalse(result['complete_call_log'])
            self.assertIn('TRUNCATED_OR_INCOMPLETE_EVENT_LOG',result['warnings'])

    def test_compare_handoff_cli_uses_real_export_paths(self):
        import subprocess,sys
        from v06_fixtures import Model06,Web06
        from ollama_deep_researcher.pm_projects import Workspace
        from ollama_deep_researcher.pm_types import Settings
        from ollama_deep_researcher.pm_engine_v06 import EngineV06
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            w=Workspace(folder);pid=w.create_v06('TEST press',Settings(max_attempts=1,draft_enabled=False))
            st=w.open(pid);engine=EngineV06(st,Model06(),Web06())
            with patch.object(engine.pacer,'wait',return_value=.1):engine.run(pid)
            handoff=st.export(pid)
            result=subprocess.run([sys.executable,'-m','ollama_deep_researcher.pm_benchmark','compare',str(handoff),str(handoff)],
                                  capture_output=True,text=True,timeout=15,check=True)
            data=json.loads(result.stdout)
            self.assertEqual(data['comparison']['accepted_claims_delta'],0)
            self.assertTrue(data['baseline_input']['complete_call_log'])

class ReplayInputIntegrity(unittest.TestCase):
    def test_modified_search_results_are_rejected_like_modified_body(self):
        from ollama_deep_researcher.pm_benchmark import write_replay_bundle,load_replay_bundle
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            write_replay_bundle(root,[],[{'url':'https://example.org/a'}],[],{})
            (root/'search_hits.jsonl').write_text('{"url":"https://example.org/b"}\n')
            with self.assertRaisesRegex(ValueError,'hash'):load_replay_bundle(root)
