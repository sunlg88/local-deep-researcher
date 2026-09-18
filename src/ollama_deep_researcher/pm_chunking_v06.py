"""Paragraph-first exact source partitions. Ranking never changes source text."""
from dataclasses import dataclass
import hashlib
import re


@dataclass(frozen=True)
class SourceChunk:
    id: str
    start: int
    end: int
    text: str
    heading: str = ''
    page_hint: str = ''
    previous_id: str | None = None
    next_id: str | None = None


def build_chunks(body: str, *, target_chars: int = 1600, max_chars: int = 2400,
                 pages: list[dict] | None = None) -> list[SourceChunk]:
    if not isinstance(body, str):
        raise ValueError('Source must be text')
    if type(target_chars) is not int or type(max_chars) is not int or not 0 < target_chars <= max_chars:
        raise ValueError('Require 0 < target_chars <= max_chars')
    if not body:
        return []
    boundaries = [m.end() for m in re.finditer(r'(?:\r?\n)[ \t]*(?:\r?\n)', body)]
    boundaries.append(len(body))
    spans, start, index = [], 0, 0
    while start < len(body):
        while index < len(boundaries) and boundaries[index] <= start:
            index += 1
        end = start
        j = index
        while j < len(boundaries) and boundaries[j] - start <= max_chars:
            end = boundaries[j]
            j += 1
            if end - start >= target_chars:
                break
        if end <= start:
            end = min(start + max_chars, len(body))
            # Prefer a word/sentence boundary, but never lose the delimiter.
            candidate = max(body.rfind(' ', start + max_chars // 2, end),
                            body.rfind('\n', start + max_chars // 2, end))
            if candidate >= start:
                end = candidate + 1
        spans.append((start, end))
        start = end
    ids = ['c-' + hashlib.sha256(f'{a}:{b}:'.encode() + body[a:b].encode('utf-8')).hexdigest()[:20]
           for a, b in spans]
    headings = [(m.start(), m.group(1).strip()[:160])
                for m in re.finditer(r'^#{1,6}[ \t]+([^\r\n]+)', body, flags=re.M)]
    result, hi, heading = [], 0, ''
    for i, (a, b) in enumerate(spans):
        while hi < len(headings) and headings[hi][0] < b:
            heading = headings[hi][1]
            hi += 1
        ph = ','.join(str(p['page']) for p in pages or []
                      if all(k in p for k in ('page','start','end')) and p['start'] < b and p['end'] > a)
        result.append(SourceChunk(ids[i], a, b, body[a:b], heading, ph,
                                  ids[i-1] if i else None, ids[i+1] if i+1 < len(ids) else None))
    return result


def expand_with_neighbors(chunks: list[SourceChunk], chunk_id: str, max_chars: int = 3200) -> tuple[int, int, str]:
    index = next((i for i, c in enumerate(chunks) if c.id == chunk_id), None)
    if index is None:
        raise KeyError(chunk_id)
    lo = hi = index
    if chunks[index].end - chunks[index].start > max_chars:
        # A caller must budget-fit this original range; never silently truncate it.
        c = chunks[index]
        return c.start, c.end, c.text
    if lo and chunks[hi].end - chunks[lo-1].start <= max_chars:
        lo -= 1
    if hi + 1 < len(chunks) and chunks[hi+1].end - chunks[lo].start <= max_chars:
        hi += 1
    window = chunks[lo:hi+1]
    if any(a.end != b.start for a, b in zip(window, window[1:])):
        raise ValueError('Neighbor chunks must form a contiguous original range')
    return window[0].start, window[-1].end, ''.join(c.text for c in window)
