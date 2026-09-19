"""Bounded semantic intent -> deterministic search query. No model-owned policy."""
from dataclasses import asdict, dataclass
import re
from urllib.parse import urlsplit

MODES = ('efficient', 'balanced', 'quality')
STRATEGIES = ('broad', 'exact_entity', 'official_site', 'pdf', 'gap')
OPERATORS = re.compile(r'\b(?:OR|AND|NOT)\b|(?:site|filetype|inurl|intitle):|[|\r\n]', re.ASCII)


@dataclass(frozen=True)
class ResearchIntent:
    entity: str
    gap: str
    keywords: tuple[str, ...]
    strategy: str
    language: str
    site_hint: str = ''

    def to_dict(self):
        d = asdict(self)
        d['keywords'] = list(self.keywords)
        return d


def _bounded(value, name, maximum):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise ValueError(f'{name}: expected 1..{maximum} characters')
    if OPERATORS.search(value) or '"' in value or any(ord(c) < 32 for c in value):
        raise ValueError(f'{name}: return semantic text, not search operators or quotes')
    return ' '.join(value.split())


def validate_intent(data: dict, known_domains: set[str]) -> ResearchIntent:
    keys = {'entity','gap','keywords','strategy','language','site_hint'}
    if not isinstance(data, dict) or set(data) != keys:
        raise ValueError('Researcher must return entity/gap/keywords/strategy/language/site_hint only')
    entity = _bounded(data['entity'], 'entity', 120)
    gap = _bounded(data['gap'], 'gap', 80)
    if re.search(r'[,;|/]', entity):
        raise ValueError('entity: choose ONE name, not a list of entities')
    # Reject instruction dumps instead of silently truncating requirements.
    if (len(gap.split()) > 10 or re.search(r'(?<!\d),(?!\d)|[;!?]|\.\s', gap) or
        re.search(r'\b(?:please|confirm|verify|check whether|need to|must|data preservation)\b', gap, re.I) or
        any(x in gap for x in ('\ud655\uc778 \ud544\uc694','\ub370\uc774\ud130 \ubcf4\uc874','\uc5ec\ubd80','\ubbf8\ud655\uc778'))):
        raise ValueError('gap: choose ONE short search phrase, not instructions or a multi-item checklist')
    if data.get('language') == 'en' and re.search(r'[\uac00-\ud7af\u3040-\u30ff]', gap):
        raise ValueError('language: use English search words for en, or select the actual query language')
    kw = data['keywords']
    if not isinstance(kw, list) or not 1 <= len(kw) <= 4:
        raise ValueError('Expected 1..4 keywords')
    keywords = tuple(dict.fromkeys(_bounded(k, 'keyword', 80) for k in kw))
    if any(len(k.split()) > 8 or re.search(r'[;!?]', k) for k in keywords):
        raise ValueError('keywords: use short topic phrases, not instructions')
    if data['strategy'] not in STRATEGIES:
        raise ValueError('Unknown research strategy')
    if data['language'] not in ('en','ko','ja','zh','de','fr','es','pt','ru','it','ar','auto'):
        raise ValueError('Unknown language hint')
    hint = data['site_hint']
    if not isinstance(hint, str):
        raise ValueError('site_hint must be a host or empty string')
    hint = hint.strip().casefold().rstrip('.')
    if hint:
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*\.[a-z]{2,}', hint):
            raise ValueError('site_hint must be a plain host, no URL/credentials')
        approved = {d.casefold().rstrip('.') for d in known_domains}
        if not any(hint == d or hint.endswith('.' + d) for d in approved):
            raise ValueError('Unobserved/unapproved site_hint; do not guess an official domain')
    intent = ResearchIntent(entity, gap, keywords, data['strategy'], data['language'], hint)
    compile_query(intent, 'duckduckgo')  # Enforce the final query budget at the producer boundary.
    return intent


def compile_query(intent: ResearchIntent, backend: str) -> str:
    if backend not in ('duckduckgo','searxng','tavily'):
        raise ValueError('Unsupported backend')
    parts = [f'"{intent.entity}"', intent.gap]
    parts += [k for k in intent.keywords if k.casefold() not in intent.gap.casefold()]
    if intent.site_hint:
        parts.append('site:' + intent.site_hint)
    if intent.strategy == 'pdf':
        parts.append('filetype:pdf')
    q = ' '.join(parts)
    if len(q) > 300 or len(q.split()) > 24:
        raise ValueError('gap/keywords: compiled query exceeds 300 characters or 24 words; select one compact gap')
    return q


def query_variants(intent: ResearchIntent, mode: str, zero_yield_streak: int = 0) -> list[str]:
    """Vary operators/quoting, never erase years, negations or technical constraints.

    Unlike deleting clauses from a raw query, all alternatives retain the full
    semantic intent. A new entity or language requires a new explicit intent.
    """
    if mode not in MODES:
        raise ValueError('Unknown optimization mode')
    base = compile_query(intent, 'duckduckgo')
    variants = [base]
    # Quoting relaxation retains entity words and all conditions.
    variants.append(base.replace(f'"{intent.entity}"', intent.entity, 1))
    if 'filetype:pdf' not in base:
        variants.append(base + ' filetype:pdf')
    else:
        variants.append(base.replace(' filetype:pdf', '') + ' technical report')
    variants = list(dict.fromkeys(variants))
    offset = min(max(int(zero_yield_streak), 0), len(variants) - 1)
    if mode == 'quality':
        return (variants[offset:] + variants[:offset])[:3]
    return [variants[offset]]


def intent_anchors(intent: ResearchIntent) -> list[str]:
    """Short ranking hints, distinct from the full preserved intent/query.

    A long legal entity name remains intact in the query; only its inexpensive
    ranking hints are split. Every producer satisfies the downstream <=100 cap.
    """
    anchors = []
    for value in (intent.entity, intent.gap, *intent.keywords):
        while len(value) > 100:
            cut = value.rfind(' ', 0, 101)
            if cut < 1:
                cut = 100
            anchors.append(value[:cut])
            value = value[cut:].strip()
        if value:
            anchors.append(value)
    return list(dict.fromkeys(anchors))[:6]
