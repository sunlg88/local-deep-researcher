import tempfile
import unittest


class Pacing06(unittest.TestCase):
    def setUp(self):
        from ollama_deep_researcher.pm_store import Store
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name)

    def test_rate_failures_raise_bounded_wait(self):
        from ollama_deep_researcher.pm_rate_limit_v06 import AdaptivePacer
        p=AdaptivePacer(self.store,clock=lambda:1000,jitter=False)
        before=p.next_wait('search:duckduckgo')
        for _ in range(3): p.record('search:duckduckgo',before,success=False,status_code=429)
        self.assertGreater(p.next_wait('search:duckduckgo'),before)
        self.assertLessEqual(p.next_wait('search:duckduckgo'),10)

    def test_pacing_persists_isolated_keys_and_success_slowly_reduces(self):
        from ollama_deep_researcher.pm_rate_limit_v06 import AdaptivePacer
        p=AdaptivePacer(self.store,jitter=False)
        for _ in range(6): p.record('search:duckduckgo',1,success=False,status_code=429)
        old=p.next_wait('search:duckduckgo')
        for _ in range(15): p.record('search:duckduckgo',.3,success=True,status_code=200)
        self.assertLess(p.next_wait('search:duckduckgo'),old)
        self.assertGreaterEqual(p.next_wait('search:duckduckgo'),.05)
        other=AdaptivePacer(self.store,jitter=False)
        self.assertEqual(other.next_wait('search:duckduckgo'),p.next_wait('search:duckduckgo'))
        self.assertEqual(other.next_wait('host:elsewhere.example'),.1)

    def test_retry_after_is_not_clamped_to_ten_seconds(self):
        from ollama_deep_researcher.pm_rate_limit_v06 import AdaptivePacer
        p=AdaptivePacer(self.store,clock=lambda:1000,jitter=False)
        p.record('host:example.org',.1,success=False,status_code=429,retry_after='120')
        self.assertGreaterEqual(p.next_wait('host:example.org'),120)

    def test_404_does_not_make_whole_host_slow(self):
        from ollama_deep_researcher.pm_rate_limit_v06 import AdaptivePacer
        p=AdaptivePacer(self.store,jitter=False)
        for _ in range(10): p.record('host:example.org',.1,success=False,status_code=404)
        self.assertEqual(p.next_wait('host:example.org'),.1)

    def test_actual_successful_pacing_does_not_drift_upward(self):
        from ollama_deep_researcher.pm_rate_limit_v06 import AdaptivePacer
        from ollama_deep_researcher.pm_store import Store
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            p=AdaptivePacer(Store(folder),clock=lambda:100.,jitter=False)
            for _ in range(3): p.record('search:ddg',.1,success=False,status_code=429)
            before=p.next_wait('search:ddg')
            for _ in range(10):
                p.record('search:ddg',p.next_wait('search:ddg'),success=True,status_code=200)
            self.assertLess(p.next_wait('search:ddg'),before)

    def test_nonfinite_retry_after_cannot_poison_persistent_state(self):
        from ollama_deep_researcher.pm_rate_limit_v06 import AdaptivePacer
        from ollama_deep_researcher.pm_store import Store
        import tempfile,math
        with tempfile.TemporaryDirectory() as folder:
            p=AdaptivePacer(Store(folder),clock=lambda:100.,jitter=False)
            p.record('search:ddg',.1,success=False,status_code=429,retry_after='inf')
            self.assertTrue(math.isfinite(p.next_wait('search:ddg')))
