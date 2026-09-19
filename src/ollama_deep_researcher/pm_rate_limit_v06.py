"""Project-local bounded pacing. Explicit Retry-After always overrides learned caps."""
from collections import deque
from email.utils import parsedate_to_datetime
import json
import math
import random
import statistics
import time


class AdaptivePacer:
    def __init__(self, store, *, clock=time.time, jitter=True):
        self.store, self.clock, self.jitter = store, clock, jitter
        with store.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS pacing_v06(key TEXT PRIMARY KEY, data TEXT NOT NULL)')

    def state(self, key):
        with self.store.db() as c:
            r = c.execute('SELECT data FROM pacing_v06 WHERE key=?', (key,)).fetchone()
        return json.loads(r[0]) if r else {'samples': 0, 'base_wait': .1, 'recent': [], 'not_before': 0}

    def next_wait(self, key):
        state = self.state(key)
        learned = state['base_wait'] * (random.uniform(.9, 1.1) if self.jitter else 1)
        return max(.05, min(10, learned), state['not_before'] - self.clock())

    def wait(self, key, check):
        delay = self.next_wait(key)
        deadline = time.monotonic() + delay
        while True:
            check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return delay
            time.sleep(min(.1, remaining))

    def record(self, key, wait, *, success, status_code=None, error_type='', retry_after=None):
        state = self.state(key)
        if retry_after is not None:
            try:
                seconds = float(retry_after)
            except (TypeError, ValueError):
                try:
                    seconds = parsedate_to_datetime(str(retry_after)).timestamp() - self.clock()
                except (ValueError, TypeError, OverflowError):
                    seconds = 0
            if math.isfinite(seconds) and seconds > 0:
                state['not_before'] = max(state['not_before'], self.clock() + seconds)
        rate_related = status_code in (429, 503) or error_type in ('TimeoutError', 'Timeout', 'ReadTimeout')
        if success or rate_related:
            recent = deque(state['recent'], maxlen=20)
            recent.append({'wait': min(10, max(.05, float(wait))), 'success': bool(success)})
            state['recent'] = list(recent)
            state['samples'] += 1
            if state['samples'] >= 3:
                good = [x['wait'] for x in recent if x['success']]
                if rate_related:
                    target = max(state['base_wait'] * 1.5, .3)
                    state['base_wait'] = min(10, target)
                elif good:
                    target = max(.05, statistics.median(good) * .95)
                    state['base_wait'] = min(10, max(.05, .8 * state['base_wait'] + .2 * target))
        state['updated'] = self.clock()
        with self.store.db() as c:
            c.execute('INSERT OR REPLACE INTO pacing_v06 VALUES(?,?)', (key, json.dumps(state)))
