"""Killable search worker with typed failures instead of silent empty results."""
import itertools
import json
import os
from pathlib import Path
import sys

from .pm_search_quality import normalize_hit


def execute(request):
    query=request.get('query')
    count=request.get('max_results')
    backend=request.get('backend')
    if not isinstance(query,str) or not 1<=len(query)<=4096 or type(count) is not int or not 1<=count<=30:
        raise ValueError('Invalid bounded search request')
    if backend=='duckduckgo':
        from duckduckgo_search import DDGS
        with DDGS() as client:
            rows=list(itertools.islice(client.text(query,max_results=count),count))
    elif backend=='tavily':
        from tavily import TavilyClient
        rows=TavilyClient().search(query,max_results=min(count,20),include_raw_content=False)['results']
    elif backend=='searxng':
        from langchain_community.utilities import SearxSearchWrapper
        client=SearxSearchWrapper(searx_host=os.environ.get('SEARXNG_URL','http://localhost:8888'))
        rows=client.results(query,num_results=count)
    else:
        raise ValueError('Unsupported search backend')
    if not isinstance(rows,list):
        raise ValueError('Invalid backend result type')
    return {'results':[normalize_hit(row) for row in rows[:count]]}


def error_envelope(error):
    code=getattr(error,'code',None)
    code=code if type(code) is int else getattr(getattr(error,'response',None),'status_code',None)
    headers=getattr(error,'headers',None) or getattr(getattr(error,'response',None),'headers',{}) or {}
    retry=str(headers.get('Retry-After',''))[:100]
    name=type(error).__name__
    if code is None and 'ratelimit' in name.casefold(): code=429
    return {'error':{'type':name,'code':code if type(code) is int else None,'retry_after':retry}}


def main():
    os.environ['LANGSMITH_TRACING']='false'
    os.environ['LANGCHAIN_TRACING_V2']='false'
    source,target=map(Path,sys.argv[1:3])
    try:
        if source.stat().st_size>20000: raise ValueError('Search request too large')
        reply=execute(json.loads(source.read_text(encoding='utf-8')))
    except Exception as error:
        reply=error_envelope(error)
    target.write_text(json.dumps(reply,ensure_ascii=False),encoding='utf-8')


if __name__=='__main__': main()
