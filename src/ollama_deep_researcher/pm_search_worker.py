"""Fixed-purpose child process; contains potentially hanging upstream search calls."""
import json
import os
from pathlib import Path
import sys


def main():
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    from .utils import duckduckgo_search, searxng_search, tavily_search
    functions = {'duckduckgo': duckduckgo_search, 'searxng': searxng_search, 'tavily': tavily_search}
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    result = functions[request['backend']](request['query'], max_results=request['max_results'], fetch_full_page=False)
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
