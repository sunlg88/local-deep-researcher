"""Bounded source decoding and stable text ranges. No OCR or table certification."""
from dataclasses import dataclass, field
import codecs
from html.parser import HTMLParser
import io
import re


@dataclass
class FetchedDocument:
    body: str
    raw: bytes
    metadata: dict = field(default_factory=dict)


class SourceHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript'):
            self.skip.append(tag)
        if tag in ('p', 'div', 'br', 'tr', 'li', 'h1', 'h2', 'h3', 'section') and not self.skip:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.skip and tag == self.skip[-1]:
            self.skip.pop()
        if tag in ('p', 'div', 'tr', 'li', 'section') and not self.skip:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data + ' ')


def decode_text(raw, charset=None):
    """Use BOM/declarations before heuristics, recording failures and uncertainty."""
    warnings, encodings = [], []
    if raw.startswith(codecs.BOM_UTF8):
        encodings.append('utf-8-sig')
    elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings.append('utf-16')
    if charset:
        encodings.append(charset.strip())
    declaration = re.search(br'charset\s*=\s*["\']?\s*([a-zA-Z0-9_-]+)', raw[:8192], re.I)
    if declaration:
        encodings.append(declaration.group(1).decode('ascii'))
    encodings.append('utf-8')
    tried = set()
    aliases = {'ks_c_5601-1987': 'cp949', 'x-windows-949': 'cp949', 'windows-949': 'cp949'}
    for name in encodings:
        name = aliases.get(name.lower(), name)
        try:
            canonical = codecs.lookup(name).name
            if canonical in tried:
                continue
            tried.add(canonical)
            return raw.decode(canonical), canonical, warnings
        except (LookupError, UnicodeError) as exc:
            warnings.append('ENCODING_DECLARATION_FAILED: ' + name + ' (' + type(exc).__name__ + ')')
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(raw).best()
        if best is not None:
            warnings.append('ENCODING_HEURISTIC: decoding needs review')
            return str(best), best.encoding, warnings
    except ImportError:
        warnings.append('ENCODING_DETECTOR_UNAVAILABLE')
    warnings.append('ENCODING_REPLACEMENTS: original bytes retained')
    return raw.decode('utf-8', errors='replace'), 'utf-8-replace', warnings


def decode_pdf(raw, max_pages=300, max_chars=2_000_000):
    metadata = {'content_type': 'application/pdf', 'pages': [], 'warnings': [],
                'parse_status': 'TEXT_EXTRACTED', 'layout_not_validated': True}
    parts, offset = [], 0
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted:
            metadata.update(parse_status='ENCRYPTED', needs_visual_review=True)
            metadata['warnings'].append('Encrypted PDF retained without attempting to bypass protection')
            return {'body': '', 'metadata': metadata}
        metadata['page_count'] = len(reader.pages)
        for i, page in enumerate(reader.pages[:max_pages], 1):
            warning, text = '', ''
            try:
                contents = page.get_contents()
                # Runtime PDF parsing is also isolated in a killable subprocess.
                if contents is not None and len(contents.get_data()) > 8_000_000:
                    raise ValueError('Page content stream exceeds 8 MB parser limit')
                text = page.extract_text() or ''
            except Exception as exc:
                warning = type(exc).__name__ + ': ' + str(exc)[:300]
            if offset + len(text) > max_chars:
                metadata['warnings'].append('TEXT_LIMIT: remaining pages retained in original PDF')
                metadata['parse_status'] = 'PARTIAL_TEXT'
                break
            if parts:
                parts.append('\n\n')
                offset += 2
            start = offset
            parts.append(text)
            offset += len(text)
            needs_visual = not text.strip() or bool(warning)
            metadata['pages'].append({'page': i, 'start': start, 'end': offset,
                                      'needs_visual_review': needs_visual, 'error': warning})
            if needs_visual:
                metadata['warnings'].append(f'PAGE_{i}: visual review/OCR may be needed')
        if len(reader.pages) > max_pages:
            metadata['warnings'].append(f'PAGE_LIMIT: only first {max_pages} pages processed')
            metadata['parse_status'] = 'PARTIAL_TEXT'
        body = ''.join(parts)
        if not body.strip():
            metadata['parse_status'] = 'NO_TEXT'
        metadata['needs_visual_review'] = any(p['needs_visual_review'] for p in metadata['pages']) or not body.strip()
        return {'body': body, 'metadata': metadata}
    except Exception as exc:
        metadata.update(parse_status='PARSE_FAILED', needs_visual_review=True,
                        parse_error=type(exc).__name__ + ': ' + str(exc)[:500])
        return {'body': '', 'metadata': metadata}


def decode_document(raw, content_type, charset=None):
    if content_type == 'application/pdf' or raw.lstrip().startswith(b'%PDF'):
        return decode_pdf(raw)
    metadata = {'content_type': content_type, 'warnings': [], 'parse_status': 'TEXT_EXTRACTED'}
    if content_type not in ('text/html', 'text/plain', 'application/xhtml+xml'):
        metadata.update(parse_status='UNSUPPORTED', needs_visual_review=True)
        return {'body': '', 'metadata': metadata}
    text, encoding, warnings = decode_text(raw, charset)
    metadata.update(encoding=encoding, warnings=warnings)
    if content_type != 'text/plain':
        parser = SourceHTML()
        parser.feed(text)
        text = '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines() if line.strip())
    if len(text) > 2_000_000:
        text = text[:2_000_000]
        metadata['warnings'].append('TEXT_LIMIT: remainder retained in original bytes')
        metadata['parse_status'] = 'PARTIAL_TEXT'
    if not text.strip():
        metadata['parse_status'] = 'EMPTY_BODY'
    return {'body': text, 'metadata': metadata}


def text_ranges(text, chars=3500, overlap=120):
    """Cover all text with stable paragraph-preferred ranges and bounded overlap."""
    if chars < 200:
        raise ValueError('Chunk size must be at least 200 characters')
    start = 0
    while start < len(text):
        end = min(len(text), start + chars)
        if end < len(text):
            boundary = text.rfind('\n', start + chars // 2, end)
            if boundary > start:
                end = boundary + 1
        yield {'start': start, 'end': end}
        if end == len(text):
            break
        start = max(start + 1, end - min(overlap, (end - start) // 4))
