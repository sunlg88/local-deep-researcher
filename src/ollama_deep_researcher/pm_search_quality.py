"""Cheap, auditable hit screening. Lexical scores rank leads, never certify facts."""
from dataclasses import dataclass
import re
import unicodedata
from urllib.parse import urlsplit, unquote

from .pm_types import Settings, canonical_url

STRATEGIES = ('broad', 'exact_entity', 'official_site', 'pdf', 'gap')
SITE = re.compile(r'(?<![\w-])(-?)site:([^\s()]+)', re.I)
STOP = {'the','a','an','and','or','of','for','in','with','to','about'}


@dataclass(frozen=True)
class SearchIntent:
    query: str
    strategy: str
    anchors: tuple[str, ...]
    explicit_sites: tuple[str, ...]
    excluded_sites: tuple[str, ...] = ()


@dataclass(frozen=True)
class HitDecision:
    accepted: bool
    score: float
    reasons: tuple[str, ...]
    host: str


def _site(value):
    value=value.strip('"').rstrip('/').lower()
    parsed=urlsplit(value if '://' in value else 'https://'+value)
    host=parsed.hostname or ''
    if not host or parsed.username or parsed.password or '*' in value or parsed.query or parsed.fragment:
        raise ValueError('Use an explicit site host/path, not wildcards or credentials')
    host=host.encode('idna').decode('ascii').rstrip('.')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',host):
        raise ValueError('Invalid site constraint')
    return host+parsed.path


def parse_search_intent(query: str, strategy: str, anchors: list[str]) -> SearchIntent:
    if not isinstance(query,str) or not 1 <= len(query.strip()) <= 4096:
        raise ValueError('Search query must be a bounded nonempty string')
    if strategy not in STRATEGIES:
        raise ValueError('Unsupported search strategy')
    if not isinstance(anchors,list) or not 1 <= len(anchors) <= 6 or not all(
        isinstance(a,str) and 1 <= len(a.strip()) <= 100 for a in anchors):
        raise ValueError('Search anchors must be short nonempty strings')
    matches=list(SITE.finditer(query))
    if len(matches) != len(re.findall(r'(?<![\w-])-?site:',query,re.I)):
        raise ValueError('Empty or malformed site constraint; query not executed')
    included,excluded=[],[]
    for match in matches:
        (excluded if match[1] else included).append(_site(match[2]))
    return SearchIntent(query.strip(),strategy,tuple(dict.fromkeys(a.strip() for a in anchors)),
                        tuple(dict.fromkeys(included)),tuple(dict.fromkeys(excluded)))


def normalize_hit(hit: dict) -> dict:
    if not isinstance(hit,dict):
        return {'url':'','title':'','content':''}
    def text(*keys, limit):
        for key in keys:
            value=hit.get(key)
            if isinstance(value,str) and value.strip():
                return value[:limit]
        return ''
    return {'url':text('url','href','link',limit=4096),
            'title':text('title','name',limit=1000),
            'content':text('content','snippet','body','description',limit=6000)}


def _site_matches(url, site):
    p=urlsplit(url); host=(p.hostname or '').rstrip('.').lower()
    wanted, _, path=site.partition('/')
    return (host==wanted or host.endswith('.'+wanted)) and (
        not path or p.path.startswith('/'+path))


def site_allows(intent: SearchIntent, url: str) -> bool:
    return (not intent.explicit_sites or any(_site_matches(url,x) for x in intent.explicit_sites)) and not any(
        _site_matches(url,x) for x in intent.excluded_sites)


def _match(anchor, text):
    term=unicodedata.normalize('NFKC',anchor).casefold()
    if term.isascii():
        return bool(re.search(r'(?<!\w)'+re.escape(term)+r'(?!\w)',text))
    return term in text


def score_hit(intent: SearchIntent, hit: dict, settings: Settings) -> HitDecision:
    hit=normalize_hit(hit)
    try:
        url=canonical_url(hit['url'])
    except (ValueError,UnicodeError):
        return HitDecision(False,0,('INVALID_URL',),'')
    host=(urlsplit(url).hostname or '').lower().removeprefix('www.')
    if not settings.allows(url):
        return HitDecision(False,0,('SOURCE_POLICY',),host)
    if not site_allows(intent,url):
        return HitDecision(False,0,('SITE_MISMATCH',),host)
    title=unicodedata.normalize('NFKC',hit['title']).casefold()
    snippet=unicodedata.normalize('NFKC',hit['content']).casefold()
    path=unquote(url).casefold()
    matches=sum(_match(a,title) or _match(a,snippet) or _match(a,path) for a in intent.anchors)
    score=sum(2*_match(a,title)+_match(a,snippet)+0.5*_match(a,path) for a in intent.anchors)
    reasons=['ANCHOR_MATCH' if matches else 'LOW_LEXICAL_MATCH']
    if settings.source_mode=='preferred' and settings.group(url) in settings.allowed_domains:
        score+=0.5; reasons.append('PREFERRED_DOMAIN_NOT_TRUST_CERTIFICATE')
    if intent.explicit_sites:
        reasons.append('SITE_MATCH')
    if intent.strategy=='pdf' and urlsplit(url).path.lower().endswith('.pdf'):
        score+=0.25; reasons.append('PDF_LEAD')
    return HitDecision(True,float(score),tuple(reasons),host)


def diversify_hits(rows: list, per_host: int = 2) -> list:
    """Round-robin score-ranked hosts; a host can supply at most per_host leads."""
    groups={}
    for hit,decision in sorted(rows,key=lambda pair: -pair[1].score):
        if decision.accepted:
            groups.setdefault(decision.host,[]).append((hit,decision))
    return [items[i] for i in range(per_host) for items in groups.values() if i<len(items)]


def select_hits(intent, hits, settings, limit=3, per_host=2):
    rows=[(normalize_hit(h),score_hit(intent,h,settings)) for h in hits]
    valid=[pair for pair in rows if pair[1].accepted]
    strong=[pair for pair in valid if 'LOW_LEXICAL_MATCH' not in pair[1].reasons]
    # Do not discard weak leads. Only schedule one when no positive lexical lead exists.
    return diversify_hits(strong or valid,per_host)[:limit if strong else min(1,limit)]


def query_signature(query: str) -> frozenset[str]:
    value=unicodedata.normalize('NFKC',query).casefold()
    return frozenset(x for x in re.findall(r'[\w]+',value) if x not in STOP)


def near_duplicate_query(a: str, b: str, threshold: float = 0.82) -> bool:
    # Dates, explicit exclusions, quoted entity names and file constraints carry meaning.
    def protected(q):
        q=unicodedata.normalize('NFKC',q).casefold()
        return (frozenset(re.findall(r'\b\d+\b',q)),
                frozenset(re.findall(r'-?(?:site|filetype|inurl|intitle):[^\s()]+',q)),
                frozenset(re.findall(r'"[^"]+"',q)),
                frozenset(re.findall(r'(?<!\w)-\w+|\b(?:not|without|exclude)\b',q)))
    if protected(a)!=protected(b):
        return False
    sa,sb=query_signature(a),query_signature(b)
    # Do not suppress a new unquoted entity or technical term merely because
    # the rest of a long query is unchanged. Only neutral query wording may differ.
    neutral={'official','website','report','reports','information','overview','details'}
    if (sa ^ sb) - neutral:
        return False
    return bool(sa and sb) and len(sa & sb)/len(sa | sb)>=threshold
