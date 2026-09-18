"""Cheap retrieval first. Scores prioritize reading; zero overlap is not proof."""
from collections import Counter
from dataclasses import dataclass, replace
import math
import re
import time
from typing import Protocol
import unicodedata

from .pm_chunking_v06 import SourceChunk

STOP = set('a an the and or but nor for to of in on at by with from this that these those '
           'is are was were be been it its as into about all any how what who when where '
           'research investigate information collect source sources document documents '
           'please report find current official based following include related'.split())
BUDGETS = {'efficient': (2, 0), 'balanced': (3, 2), 'quality': (5, 5)}


def terms(text: str) -> list[str]:
    value = unicodedata.normalize('NFKC', text).casefold()
    tokens = re.findall(r'[a-z0-9]+(?:[-_.][a-z0-9]+)*|[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+', value)
    result = []
    for t in tokens:
        if t in STOP or len(t) < 2:
            continue
        result.append(t)
        if not t.isascii() and len(t) > 2:
            result.extend(t[i:i+2] for i in range(len(t)-1))
    return result


@dataclass(frozen=True)
class RankedChunk:
    chunk_id: str
    lexical_score: float
    semantic_score: float | None
    combined_score: float
    reasons: tuple[str, ...]


class SemanticReranker(Protocol):
    name: str

    def score(self, query: str, texts: list[str]) -> list[float]:
        """Return finite similarity scores in [-1, 1], one per original text."""


def rank_chunks(query_text: str, chunks: list[SourceChunk], task_terms: list[str]) -> list[RankedChunk]:
    q = set(terms(query_text + ' ' + ' '.join(task_terms)))
    counts = [Counter(terms(c.text)) for c in chunks]
    df = Counter(t for count in counts for t in count)
    average = sum(sum(c.values()) for c in counts) / max(1, len(counts)) or 1
    result = []
    for chunk, count in zip(chunks, counts):
        length = sum(count.values())
        score = 0.0
        overlap = q & count.keys()
        for t in overlap:
            idf = math.log(1 + (len(chunks) - df[t] + .5) / (df[t] + .5))
            tf = count[t]
            score += idf * (tf * 2.2) / (tf + 1.2 * (.25 + .75 * length / average))
        # Heading is only a priority boost if the actual body contains signal.
        if overlap:
            score += .25 * len(q & set(terms(chunk.heading)))
            lowered = unicodedata.normalize('NFKC', chunk.text).casefold()
            score += sum(.5 for term in task_terms if len(terms(term)) >= 2 and term.casefold() in lowered)
        if not overlap and re.search(r'\d',chunk.text):
            title_overlap=q & set(terms(chunk.heading))
            if len(title_overlap)>=2: score=.1*len(title_overlap)
        reasons = ('LEXICAL_MATCH',) if score > 0 else ('LOW_SIGNAL_DEFERRED',)
        result.append(RankedChunk(chunk.id, score, None, score, reasons))
    return sorted(result, key=lambda r: -r.combined_score)


def select_progressive(ranked: list[RankedChunk], mode: str, pass_number: int) -> list[str]:
    if mode not in BUDGETS or pass_number not in (0, 1):
        raise ValueError('Unknown mode or retrieval pass')
    first, second = BUDGETS[mode]
    begin, end = (0, first) if pass_number == 0 else (first, first + second)
    return [r.chunk_id for r in ranked if r.combined_score > 0][begin:end]


def rerank_semantic(query: str, chunks: list[SourceChunk], ranked: list[RankedChunk],
                    backend: SemanticReranker | None) -> tuple[list[RankedChunk], dict]:
    if backend is None:
        return ranked, {'status': 'SEMANTIC_OFF', 'seconds': 0.0}
    started = time.monotonic()
    try:
        by_id = {c.id: c.text for c in chunks}
        values = backend.score(query, [by_id[r.chunk_id] for r in ranked])
        if len(values) != len(ranked) or not all(type(v) in (float, int) and math.isfinite(v) and -1 <= v <= 1 for v in values):
            raise ValueError('Invalid semantic scores')
        # Fuse ranks, not incompatible BM25 and cosine magnitudes.
        semantic_order = sorted(range(len(values)), key=lambda i: -values[i])
        srank = {i: rank for rank, i in enumerate(semantic_order)}
        combined = [replace(r, semantic_score=float(values[i]),
                            combined_score=((1/(61+i) if r.lexical_score > 0 else 0)
                                            + (1/(61+srank[i]) if values[i] >= .35 else 0)),
                            reasons=r.reasons + ('SEMANTIC_PRIORITY_NOT_VERIFICATION',))
                    for i, r in enumerate(ranked)]
        return sorted(combined, key=lambda r: -r.combined_score), {
            'status': 'SEMANTIC_RERANKED', 'backend': backend.name,
            'seconds': time.monotonic() - started,
            'backend_metrics': getattr(backend, 'last_metrics', {})}
    except Exception as exc:
        from .pm_engine import ControlRequested, TimeLimitExceeded
        if isinstance(exc, (ControlRequested, TimeLimitExceeded)):
            raise
        return ranked, {'status': 'SEMANTIC_RERANK_SKIPPED', 'backend': backend.name,
                        'error_type': type(exc).__name__, 'seconds': time.monotonic() - started}
