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
from .pm_documents import FetchedDocument, decode_document
from .pm_prompts import SCHEMAS, OutputLimitError, PromptBudgetError
from .pm_budget import request_parts, ensure_fits, schema_for


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
        self.source_allowed = lambda url: True

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
        """Compatibility adapter. New engine calls fetch_document to keep originals."""
        return self.fetch_document(url).body

    def fetch_document(self, url):
        original_url = url
        started = time.monotonic()
        for _ in range(4):
            self.check()
            url = canonical_url(url)
            if not self.settings.allows(url) or not self.source_allowed(url):
                raise ValueError('Source blocked by the selected source policy')
            url = public_url(url)
            request = Request(url, headers={'User-Agent': 'LocalResearchPM/0.4 (read-only research)',
                          'Accept': 'text/html,text/plain,application/xhtml+xml,application/pdf'})
            try:
                with opener().open(request, timeout=15) as response:
                    kind = response.headers.get_content_type()
                    charset = response.headers.get_content_charset()
                    chunks, total = [], 0
                    while True:
                        self.check()
                        if time.monotonic() - started > 45:
                            raise TimeoutError('Source fetch wall-clock limit exceeded')
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.settings.max_source_bytes:
                            raise ValueError('Source exceeds configured original-byte limit')
                        chunks.append(chunk)
                    raw = b''.join(chunks)
                    if kind == 'application/pdf' or raw.startswith(b'%PDF'):
                        decoded = parse_pdf_isolated(raw, self.check)
                    else:
                        decoded = decode_document(raw, kind, charset)
                    meta = decoded['metadata']
                    meta.update(original_url=original_url, final_url=url,
                                content_type='application/pdf' if raw.startswith(b'%PDF') else kind)
                    return FetchedDocument(decoded['body'], raw, meta)
            except HTTPError as exc:
                if exc.code not in (301,302,303,307,308) or not exc.headers.get('Location'):
                    raise
                url = urljoin(url, exc.headers['Location'])
        raise ValueError('Too many source redirects')


def parse_pdf_isolated(raw, check, timeout=30):
    """Retain the original even when parsing fails; terminate child on cancellation."""
    with tempfile.TemporaryDirectory(prefix='research-pm-pdf-') as folder:
        source, target = Path(folder)/'original.pdf', Path(folder)/'parsed.json'
        source.write_bytes(raw)
        with open(Path(folder)/'parser.log', 'wb') as log:
            process = subprocess.Popen([sys.executable, '-m', 'ollama_deep_researcher.pm_pdf_worker',
                                        str(source), str(target)], stdout=log, stderr=log)
            started=time.monotonic()
            try:
                check()
                while process.poll() is None:
                    check()
                    if time.monotonic()-started>timeout:
                        return {'body':'', 'metadata':{'parse_status':'PARSE_TIMEOUT',
                            'warnings':['PDF parser timed out; original bytes retained for reprocessing']}}
                    time.sleep(0.1)
                if process.returncode != 0 or not target.exists() or target.stat().st_size>16_000_000:
                    log.flush()
                    detail=(Path(folder)/'parser.log').read_text(encoding='utf-8',errors='replace')[-1500:]
                    return {'body':'', 'metadata':{'parse_status':'PARSE_FAILED',
                        'warnings':['PDF extraction failed; original bytes retained'], 'error':detail}}
                result=json.loads(target.read_text(encoding='utf-8'))
                if not isinstance(result.get('body'),str) or not isinstance(result.get('metadata'),dict):
                    raise ValueError('Invalid PDF parser output')
                return result
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


class Ollama:
    def __init__(self, settings):
        self.settings = settings
        self.last_metrics = {}

    def models(self):
        with opener().open(self.settings.ollama_url.rstrip('/') + '/api/tags', timeout=10) as response:
            data = json.loads(response.read(2_000_000))
        return [m['name'] for m in data.get('models', [])]

    def ask(self, role, payload, check):
        cfg = self.settings
        self.last_metrics = {}
        prompt, user, metrics = request_parts(cfg, role, payload)
        self.last_metrics = dict(metrics, thinking_chars=0, answer_chars=0)
        ensure_fits(metrics)
        body = {'model': cfg.model, 'messages': [{'role': 'system', 'content': prompt},
                                                {'role': 'user', 'content': user}],
                'stream': True, 'format': schema_for(role, payload), 'think': metrics['think'],
                'keep_alive': '30m', 'options': {'temperature': 0, 'num_ctx': cfg.context_tokens,
                                               'num_predict': metrics['output_budget']}}
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
                message = chunk.get('message', {})
                answer = message.get('content', '')
                content.append(answer)
                self.last_metrics['answer_chars'] += len(answer)
                self.last_metrics['thinking_chars'] += len(message.get('thinking', ''))
                if chunk.get('done'):
                    self.last_metrics.update({k: chunk.get(k) for k in (
                        'done_reason', 'prompt_eval_count', 'eval_count', 'eval_duration',
                        'prompt_eval_duration', 'load_duration', 'total_duration')})
                    actual = chunk.get('prompt_eval_count')
                    if isinstance(actual, int) and actual > cfg.context_tokens - metrics['output_budget'] - 256:
                        raise PromptBudgetError(
                            f'Observed prompt {actual} tokens leaves insufficient output headroom; '
                            'response rejected, original retained for smaller retry')
                    if chunk.get('done_reason') == 'length':
                        raise OutputLimitError(
                            f"Generation hit output limit ({metrics['output_budget']}); incomplete response rejected; "
                            f"prompt_eval_count={actual}, eval_count={chunk.get('eval_count')}, "
                            f"thinking_chars={self.last_metrics['thinking_chars']}, "
                            f"answer_chars={self.last_metrics['answer_chars']}")
                    done = True
                    break
            if not done:
                raise ValueError('Ollama stream ended without completion')
        return parse_json(''.join(content))
