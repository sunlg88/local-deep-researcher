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
    gap = _bounded(data['gap'], 'gap', 200)
    kw = data['keywords']
    if not isinstance(kw, list) or not 1 <= len(kw) <= 4:
        raise ValueError('Expected 1..4 keywords')
    keywords = tuple(dict.fromkeys(_bounded(k, 'keyword', 80) for k in kw))
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
    return ResearchIntent(entity, gap, keywords, data['strategy'], data['language'], hint)


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
    if len(q) > 600:
        raise ValueError('Compiled query exceeds 600 characters; shorten semantic intent')
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
