"""Validated PM contracts. Mechanical checks are not scientific truth tests."""
from dataclasses import asdict, dataclass, field
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, quote


MAX_TOPIC_BYTES = 1000
MAX_INSTRUCTION_BYTES = 1200


def validate_research_input(topic, instructions):
    """Storage/GUI size guard, separate from the model's token budget."""
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError('\uc5f0\uad6c \uc8fc\uc81c\ub97c \uc785\ub825\ud558\uc138\uc694.')
    count = len(topic.encode('utf-8'))
    if count > MAX_TOPIC_BYTES:
        raise ValueError(f'\uc5f0\uad6c \uc8fc\uc81c: {count:,} / {MAX_TOPIC_BYTES:,} UTF-8 \ubc14\uc774\ud2b8. \uc8fc\uc81c\ub97c \uc904\uc5ec\uc8fc\uc138\uc694.')
    if not isinstance(instructions, str):
        raise ValueError('\ucd94\uac00 \uc9c0\uc2dc\ub294 \ubb38\uc790\uc5f4\uc774\uc5b4\uc57c \ud569\ub2c8\ub2e4.')
    count = len(instructions.encode('utf-8'))
    if count > MAX_INSTRUCTION_BYTES:
        raise ValueError(f'\ucd94\uac00 \uc9c0\uc2dc: {count:,} / {MAX_INSTRUCTION_BYTES:,} UTF-8 \ubc14\uc774\ud2b8. \uc785\ub825\ubb38\uc744 \uc904\uc5ec\uc8fc\uc138\uc694.\n'
                         '\ud55c\uae00 \uc74c\uc808\uc740 \ub300\uccb4\ub85c 3\ubc14\uc774\ud2b8\uc785\ub2c8\ub2e4. \uc774 \uc81c\ud55c\uc740 \ubaa8\ub378 \ucee8\ud14d\uc2a4\ud2b8 \ud1a0\ud070 \uc218\uc640 \ubcc4\uac1c\uc785\ub2c8\ub2e4.')


@dataclass
class Settings:
    model: str = 'qwen3.5:9b'
    ollama_url: str = 'http://localhost:11434'
    search_api: str = 'duckduckgo'
    source_mode: str = 'open'
    allowed_domains: list[str] = field(default_factory=list)
    min_sources: int = 2
    min_tasks: int = 1
    max_tasks: int = 8
    max_attempts: int = 6
    max_calls: int = 240
    max_searches: int = 60
    context_tokens: int = 8192
    output_tokens: int = 3072
    report_minutes: int = 15
    think: bool = True
    request_timeout: int = 300
    source_chars: int = 3500
    source_limit: int = 3
    time_limit_minutes: int = 120
    strict_final: bool = False
    draft_enabled: bool = True
    max_source_bytes: int = 20_000_000

    def __post_init__(self):
        limits = {'min_sources': (1, 10), 'min_tasks': (1, 20), 'max_tasks': (1, 20),
                  'max_attempts': (1, 20), 'max_calls': (1, 100000), 'max_searches': (1, 100000),
                  'context_tokens': (4096, 131072), 'output_tokens': (512, 16384),
                  'report_minutes': (1, 1440), 'request_timeout': (10, 3600),
                  'source_chars': (500, 20000), 'source_limit': (1, 10),
                  'time_limit_minutes': (0, 43200), 'max_source_bytes': (100_000, 20_000_000)}
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'{name}: integer in {low}..{high} required')
        if self.min_tasks > self.max_tasks or self.output_tokens + 2048 > self.context_tokens:
            raise ValueError('Task limits or input/output context reservation is invalid')
        if self.search_api not in ('duckduckgo', 'searxng', 'tavily'):
            raise ValueError('Unsupported search adapter')
        if self.source_mode not in ('open', 'preferred', 'allowlist'):
            raise ValueError('Unsupported source policy')
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError('An installed Ollama model name is required')
        if type(self.strict_final) is not bool or type(self.draft_enabled) is not bool:
            raise ValueError('Final filter and draft switches must be boolean')
        if type(self.think) is not bool:
            raise ValueError('think must be boolean')
        canonical_url(self.ollama_url)
        domains = []
        for domain in self.allowed_domains:
            if not isinstance(domain, str):
                raise ValueError('Domain allowlist must contain strings')
            domain = domain.strip().lower().rstrip('.')
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', domain) or '.' not in domain:
                raise ValueError(f'Use explicit domains, not URLs or wildcards: {domain}')
            domains.append(domain)
        self.allowed_domains = sorted(set(domains))
        if self.source_mode == 'allowlist' and not self.allowed_domains:
            raise ValueError('Allowlist-only mode requires at least one source domain')

    @classmethod
    def from_saved(cls, data):
        """Load persisted settings without weakening the policy of pre-v0.3 projects."""
        values = dict(data)
        values.setdefault('source_mode', 'allowlist')
        return cls(**values)

    def group(self, url):
        canonical = canonical_url(url)
        host = (urlsplit(canonical).hostname or '').lower().rstrip('.')
        matches = [d for d in self.allowed_domains if host == d or host.endswith('.' + d)]
        if matches:
            return min(matches, key=len)
        if self.source_mode != 'allowlist':
            return host.removeprefix('www.')
        return None

    def allows(self, url):
        try:
            canonical_url(url)
            return self.source_mode != 'allowlist' or self.group(url) is not None
        except ValueError:
            return False

    def search_query(self, query):
        query = str(query).strip()
        if self.source_mode == 'allowlist':
            domains = ' OR '.join('site:' + d for d in self.allowed_domains)
            return query + ' (' + domains + ')'
        return query

    def rank_hits(self, hits):
        rows = list(hits)
        if self.source_mode != 'preferred' or not self.allowed_domains:
            return rows
        def preferred(hit):
            try:
                return 0 if self.group(hit.get('url', '')) in self.allowed_domains else 1
            except ValueError:
                return 1
        return sorted(rows, key=preferred)

    def to_dict(self):
        return asdict(self)


def canonical_url(url):
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 32 for c in url):
        raise ValueError('Invalid URL')
    p = urlsplit(url.strip())
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Only HTTP(S) URLs without credentials are allowed')
    host = p.hostname.lower().rstrip('.')
    if ':' not in host:
        host = host.encode('idna').decode('ascii')
    if ':' in host:
        host = '[' + host + ']'
    port = p.port
    if port and not (p.scheme == 'https' and port == 443 or p.scheme == 'http' and port == 80):
        host += ':' + str(port)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    return urlunsplit((p.scheme.lower(), host, quote(p.path or '/', safe="/%:@!$&'()*+,;=-._~"), urlencode(sorted(query)), ''))


def normalized(text):
    return ' '.join(str(text).split())


def parse_json(text):
    if not isinstance(text, str):
        raise ValueError('Model returned non-text content')
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    if text.startswith('```') and text.endswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text).removesuffix('```').strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('A JSON object is required')
    return value


def gate(task, evidence, review, settings):
    """Fail closed; a critic cannot waive source, quote, conflict or coverage rules."""
    reasons, selected = [], set()
    rows = {e['id']: e for e in evidence}
    checks = review.get('checks', [])
    if not isinstance(checks, list):
        return False, ['Critic checks must be a list'], []
    for criterion in task['criteria']:
        match = [c for c in checks if isinstance(c, dict) and c.get('criterion') == criterion['id']]
        if len(match) != 1:
            reasons.append(f"{criterion['id']}: missing or duplicated review")
            continue
        c = match[0]
        ids = c.get('evidence_ids', [])
        if c.get('passed') is not True or not isinstance(ids, list) or not ids:
            reasons.append(f"{criterion['id']}: {str(c.get('reason', 'unsupported'))[:300]}")
            continue
        valid = True
        for eid in ids:
            row = rows.get(eid) if isinstance(eid, str) else None
            if not row or not settings.allows(row['url']) or row.get('conflict'):
                reasons.append(f"{criterion['id']}: unknown/disallowed/conflicting evidence {eid}")
                valid = False
        if valid:
            selected.update(ids)
    chosen = [rows[eid] for eid in selected]
    groups = {settings.group(e['url']) for e in chosen}
    hashes = {e['content_hash'] for e in chosen}
    if min(len(groups), len(hashes)) < settings.min_sources:
        reasons.append(f'Need {settings.min_sources} distinct source groups and document bodies')
    if review.get('issues'):
        reasons.append('Critic raised unresolved issues: ' + str(review['issues'])[:800])
    return not reasons, reasons, sorted(selected)
