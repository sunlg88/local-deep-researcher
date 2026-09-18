"""Fixed-purpose PDF extraction child. No model instructions or network access."""
import json
from pathlib import Path
import sys


def main():
    if len(sys.argv) != 3:
        raise ValueError('Expected input PDF and output JSON paths')
    # Unix-only hard address-space bound; on Windows the parent still enforces a
    # wall-clock timeout and raw/page/text limits. This is not an OS sandbox.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (768*1024*1024, 768*1024*1024))
    except (ImportError, ValueError, OSError):
        pass
    source, target = map(Path, sys.argv[1:])
    if source.stat().st_size > 20_000_000:
        raise ValueError('PDF original exceeds 20 MB limit')
    from .pm_documents import decode_pdf
    result = decode_pdf(source.read_bytes())
    target.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
