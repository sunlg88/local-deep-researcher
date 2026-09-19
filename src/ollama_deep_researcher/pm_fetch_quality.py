"""Cheap page quality signals and safe identities. Deferred originals remain intact."""
from dataclasses import dataclass
import hashlib
import json
import re

from .pm_types import canonical_url
from .pm_retrieval_v06 import terms


@dataclass(frozen=True)
class PageQuality:
    status: str
    score: float
    reasons: tuple[str, ...]


def search_excerpt_key(url: str, snippet: str) -> str:
    # Case and punctuation can encode technical meaning; normalize whitespace only.
    text = json.dumps([canonical_url(url), ' '.join(snippet.split())], ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def document_body_key(body: str) -> str:
    if not isinstance(body, str):
        raise ValueError('Body must be text')
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def reuse_key(body: str, title: str) -> str:
    # A different title may carry year/entity scope. Do not borrow that interpretation.
    value = json.dumps([document_body_key(body), ' '.join(title.split())], ensure_ascii=False)
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def assess_fetched_page(*, query_anchors: list[str], search_title: str,
                        search_snippet: str, fetched_title: str, body: str) -> PageQuality:
    if not body.strip():
        return PageQuality('LOW_SIGNAL_DEFERRED', 0, ('NO_PARSED_TEXT_OR_SCAN', 'NOT_PROOF_OF_IRRELEVANCE'))
    opening = body.lstrip()[:200].casefold()
    if len(body) < 800 and re.match(r'(?:error\s*)?(?:access denied|403 forbidden|404 not found|'
                                   r'page not found|request forbidden|checking your browser|'
                                   r'just a moment|please enable javascript)', opening):
        return PageQuality('ERROR_PAGE_DEFERRED', 0, ('SHORT_ERROR_OR_CHALLENGE_PAGE',))
    q = set(terms(' '.join(query_anchors)))
    body_terms = set(terms(body))
    matched = q & body_terms
    score = len(matched) / max(1, len(q))
    if len(matched) >= min(2, len(q)) and matched:
        return PageQuality('READY', score, ('BODY_SIGNAL_PRESENT', 'NOT_FACT_VERIFICATION'))
    if len(body) < 1200 and any(phrase in opening for phrase in ('we use cookies','accept all cookies','cookie preferences','privacy preferences')):
        return PageQuality('LOW_SIGNAL_DEFERRED', 0, ('COOKIE_OR_CONSENT_ONLY', 'NOT_PROOF_OF_IRRELEVANCE'))
    # Numeric table bodies may rely on a title actually obtained from the source.
    # A search-result title alone never qualifies for this exception.
    if re.search(r'\d',body) and len(q & set(terms(fetched_title)))>=2:
        return PageQuality('READY',score,('SOURCE_TITLE_NUMERIC_CONTEXT','CONDITIONS_REQUIRE_REVIEW'))
    expected = set(terms(search_title + ' ' + search_snippet))
    actual_title = set(terms(fetched_title))
    if actual_title and expected and not (actual_title & expected) and not (expected & body_terms):
        return PageQuality('CONTENT_MISMATCH_DEFERRED', 0,
                           ('SEARCH_BODY_TITLE_MISMATCH', 'NOT_PROOF_OF_IRRELEVANCE'))
    return PageQuality('LOW_SIGNAL_DEFERRED', 0, ('NO_LEXICAL_OVERLAP', 'NOT_PROOF_OF_IRRELEVANCE'))
