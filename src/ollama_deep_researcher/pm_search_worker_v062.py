"""Explicit pinned DuckDuckGo endpoint, no hidden alternate engine or API cache."""
from importlib.metadata import PackageNotFoundError, version
import itertools
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from .pm_search_quality import normalize_hit
from .pm_search_worker_v06 import error_envelope, execute as legacy_execute

DDGS_VERSION='9.16.0'
class SearchDependencyError(RuntimeError):
    pass


def provider_metadata(backend):
    if backend=='duckduckgo':
        try:installed=version('ddgs')
        except PackageNotFoundError as exc:
            raise SearchDependencyError('Run UPDATE_SEARCH_BACKEND.bat once: ddgs=='+DDGS_VERSION+' required; no silent legacy fallback.') from exc
        if installed!=DDGS_VERSION:
            raise SearchDependencyError('Run UPDATE_SEARCH_BACKEND.bat: tested ddgs=='+DDGS_VERSION+' required, installed '+installed)
        return dict(library='ddgs',library_version=installed,backend='duckduckgo',endpoint_host='html.duckduckgo.com',automatic_fallback=False,api_peer_cache=False,region='us-en')
    if backend not in ('tavily','searxng'):raise ValueError('Unsupported search backend')
    package='tavily-python' if backend=='tavily' else 'langchain-community'
    try:installed=version(package)
    except PackageNotFoundError:installed='not-installed'
    return dict(library=package,library_version=installed,backend=backend,automatic_fallback=False,underlying_engines='server-managed')


def execute(request):
    query=request.get('query');count=request.get('max_results');backend=request.get('backend')
    if not isinstance(query,str) or not 1<=len(query)<=4096 or type(count) is not int or not 1<=count<=30:
        raise ValueError('Invalid bounded search request')
    meta=provider_metadata(backend)
    if backend!='duckduckgo':
        reply=legacy_execute(request);reply['search_metadata']=meta;return reply
    from ddgs import DDGS
    with DDGS(timeout=15,verify=True) as client:
        engines=client._get_engines('text','duckduckgo')
        if not engines or any(e.name!='duckduckgo' or urlsplit(e.search_url).hostname!='html.duckduckgo.com' for e in engines):
            raise RuntimeError('Unexpected search provider; refusing automatic fallback')
        try:rows=list(itertools.islice(client.text(query,max_results=count,backend='duckduckgo',region='us-en'),count))
        except Exception as exc:
            if type(exc).__name__=='DDGSException' and str(exc)=='No results found.':
                rows=[];meta['empty_result']=True
            else:raise
    return {'results':[normalize_hit(r) for r in rows],'search_metadata':meta}


def main():
    os.environ['LANGSMITH_TRACING']='false';os.environ['LANGCHAIN_TRACING_V2']='false'
    source,target=map(Path,sys.argv[1:3]);meta={}
    try:
        if source.stat().st_size>20000:raise ValueError('Search request too large')
        request=json.loads(source.read_text(encoding='utf-8'));meta=provider_metadata(request.get('backend'))
        reply=execute(request)
    except Exception as exc:
        reply=error_envelope(exc)
        if isinstance(exc,SearchDependencyError):reply['error']['action']='UPDATE_SEARCH_BACKEND.bat'
        reply['search_metadata']=meta
    target.write_text(json.dumps(reply,ensure_ascii=False),encoding='utf-8')
if __name__=='__main__':main()
