"""Network boundaries for local Ollama and policy-controlled public HTML research."""
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .pm_types import canonical_url, parse_json


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def opener():
    return build_opener(ProxyHandler({}), NoRedirect())


def public_url(url):
    url = canonical_url(url)
    p = urlsplit(url)
    if p.port not in (None, 80, 443):
        raise ValueError('Nonstandard source port is blocked')
    addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Private, loopback, reserved or unresolved source address blocked')
    return url


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'aside'):
            self.skip.append(tag)
        if tag in ('p', 'div', 'br', 'tr', 'li', 'h1', 'h2', 'h3') and not self.skip:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.skip and tag == self.skip[-1]:
            self.skip.pop()
        if tag in ('p', 'div', 'tr', 'li') and not self.skip:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data + ' ')


def passage(text, query, chars):
    if len(text) <= chars:
        return text
    terms = set(re.findall(r'\w+', query.casefold()))
    windows = [(i, text[i:i + chars]) for i in range(0, len(text), max(1, chars // 2))]
    start, chosen = max(windows, key=lambda pair: sum(pair[1].casefold().count(t) for t in terms))
    return chosen


class Web:
    def __init__(self, settings):
        self.settings = settings
        self.check = lambda: None

    def search(self, query):
        self.check()
        # Reuse upstream adapters in a killable fixed-purpose process, not an LLM shell.
        search_query = self.settings.search_query(query)
        with tempfile.TemporaryDirectory(prefix='research-pm-search-') as folder:
            request_path, result_path = Path(folder) / 'request.json', Path(folder) / 'result.json'
            request_path.write_text(json.dumps({'backend': self.settings.search_api,
                'query': search_query, 'max_results': self.settings.source_limit * 3}), encoding='utf-8')
            with open(Path(folder) / 'worker.log', 'wb') as log:
                process = subprocess.Popen([sys.executable, '-m', 'ollama_deep_researcher.pm_search_worker',
                                            str(request_path), str(result_path)], stdout=log, stderr=log)
                start = time.monotonic()
                try:
                    while process.poll() is None:
                        self.check()
                        if time.monotonic() - start > 45:
                            raise TimeoutError('Search adapter exceeded 45 seconds')
                        time.sleep(0.15)
                    if process.returncode != 0:
                        log.flush()
                        detail = (Path(folder) / 'worker.log').read_text(encoding='utf-8', errors='replace')[-1500:]
                        raise RuntimeError('Search adapter failed: ' + detail)
                    if not result_path.exists() or result_path.stat().st_size > 2_000_000:
                        raise ValueError('Search adapter returned missing or excessive data')
                    data = json.loads(result_path.read_text(encoding='utf-8'))
                    results = data.get('results', [])
                    if not isinstance(results, list):
                        raise ValueError('Search adapter returned invalid results')
                    return self.settings.rank_hits(results)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)

    def fetch(self, url):
        started = time.monotonic()
        for _ in range(4):
            self.check()
            if not self.settings.allows(url):
                raise ValueError('Source blocked by the selected source policy')
            url = public_url(url)
            request = Request(url, headers={'User-Agent': 'LocalResearchPM/0.2 (read-only research)',
                                            'Accept': 'text/html,text/plain'})
            try:
                with opener().open(request, timeout=15) as response:
                    kind = response.headers.get_content_type()
                    if kind not in ('text/html', 'text/plain', 'application/xhtml+xml'):
                        raise ValueError('Unsupported source type (PDF/OCR is not enabled): ' + kind)
                    chunks, total = [], 0
                    while True:
                        self.check()
                        if time.monotonic() - started > 45:
                            raise TimeoutError('Source fetch wall-clock limit exceeded')
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > 2_000_000:
                            raise ValueError('Source exceeds 2 MB fetch limit')
                        chunks.append(chunk)
                    raw = b''.join(chunks)
                    if raw.startswith(b'%PDF'):
                        raise ValueError('PDF source requires the future PDF reader')
                    text = raw.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
                    if kind != 'text/plain':
                        parser = TextHTML()
                        parser.feed(text)
                        text = '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines() if line.strip())
                    if len(text) > 200000:
                        raise ValueError('Extracted source exceeds 200000 characters')
                    return text
            except HTTPError as exc:
                if exc.code not in (301, 302, 303, 307, 308) or not exc.headers.get('Location'):
                    raise
                url = urljoin(url, exc.headers['Location'])
        raise ValueError('Too many source redirects')


class Ollama:
    def __init__(self, settings):
        self.settings = settings
        self.last_metrics = {}

    def models(self):
        with opener().open(self.settings.ollama_url.rstrip('/') + '/api/tags', timeout=10) as response:
            data = json.loads(response.read(2_000_000))
        return [m['name'] for m in data.get('models', [])]

    def ask(self, role, payload, check):
        from .pm_engine import BOUNDARY, PROMPTS
        cfg = self.settings
        prompt = BOUNDARY + PROMPTS[role]
        user = json.dumps(payload, ensure_ascii=False)
        if len((prompt + user).encode('utf-8')) > cfg.context_tokens - cfg.output_tokens - 768:
            raise ValueError('Prompt byte budget exceeded')
        body = {'model': cfg.model, 'messages': [{'role': 'system', 'content': prompt},
                                                {'role': 'user', 'content': user}],
                'stream': True, 'format': 'json', 'think': cfg.think and role in ('planner', 'critic', 'writer'),
                'keep_alive': '30m', 'options': {'temperature': 0, 'num_ctx': cfg.context_tokens,
                                               'num_predict': cfg.output_tokens}}
        request = Request(cfg.ollama_url.rstrip('/') + '/api/chat',
                          data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
        started, content, total = time.monotonic(), [], 0
        check()
        with opener().open(request, timeout=min(cfg.request_timeout, 60)) as response:
            done = False
            for line in response:
                check()
                if time.monotonic() - started > cfg.request_timeout:
                    raise TimeoutError('Ollama request wall-clock budget exceeded')
                total += len(line)
                if total > 2_000_000:
                    raise ValueError('Ollama response exceeds transport budget')
                chunk = json.loads(line)
                if chunk.get('error'):
                    raise RuntimeError(str(chunk['error']))
                content.append(chunk.get('message', {}).get('content', ''))
                if chunk.get('done'):
                    if chunk.get('done_reason') == 'length':
                        raise ValueError('Generation hit output limit; incomplete response rejected')
                    self.last_metrics = {k: chunk.get(k) for k in ('prompt_eval_count', 'eval_count', 'eval_duration')}
                    done = True
                    break
            if not done:
                raise ValueError('Ollama stream ended without completion')
        return parse_json(''.join(content))
