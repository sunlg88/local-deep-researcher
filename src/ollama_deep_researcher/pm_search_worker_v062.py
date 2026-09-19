"""Explicit pinned DuckDuckGo endpoint, no hidden alternate engine or API cache."""
from importlib.metadata import PackageNotFoundError, version
from dataclasses import asdict
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


class SearchResponseError(RuntimeError):
    def __init__(self,message,code=None,retry_after=''):
        self.code=code
        self.headers={'Retry-After':str(retry_after)} if retry_after else {}
        super().__init__(message)


def checked_response(response,meta):
    code=response.status_code
    text=response.text
    meta['http_status']=code
    meta['response_characters']=len(text)
    low=text.casefold()
    challenge=any(marker in low for marker in ('challenge-form','anomaly-modal','anomaly.js','captcha'))
    if code!=200 or challenge:
        # The upstream base class returns None for non-200, which turns access
        # blocks into 'no results'. Preserve the actual status and back off.
        headers=getattr(response,'headers',{}) or {}
        delay=headers.get('Retry-After','30' if challenge or code in (202,403,429,503) else '')
        raise SearchResponseError('Search endpoint refused or challenged the request; not an empty result',code,delay)
    return text


def execute(request):
    query=request.get('query');count=request.get('max_results');backend=request.get('backend')
    if not isinstance(query,str) or not 1<=len(query)<=4096 or type(count) is not int or not 1<=count<=30:
        raise ValueError('Invalid bounded search request')
    meta=provider_metadata(backend)
    if backend!='duckduckgo':
        reply=legacy_execute(request);reply['search_metadata']=meta;return reply
    from ddgs.engines.duckduckgo import Duckduckgo
    if Duckduckgo.name!='duckduckgo' or urlsplit(Duckduckgo.search_url).hostname!='html.duckduckgo.com':
        raise RuntimeError('Unexpected search provider; refusing automatic fallback')
    class CheckedDuckduckgo(Duckduckgo):
        last_html=''
        def request(self,*args,**kwargs):
            self.last_html=checked_response(self.http_client.request(*args,**kwargs),meta)
            return self.last_html
    engine=CheckedDuckduckgo(timeout=15,verify=True)
    try:
        rows=engine.search(query,region='us-en',page=1) or []
        if not rows:
            # Only a recognizable normal empty-results page is a success.
            if not any(x in engine.last_html.casefold() for x in ('no-results','no results found','no more results')):
                raise SearchResponseError('Search HTML could not be parsed into results; not a confirmed empty result',200)
            meta['empty_result']=True
        return {'results':[normalize_hit(asdict(r)) for r in rows[:count]],'search_metadata':meta}
    except Exception as exc:
        exc.search_metadata=meta
        raise


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
        reply['search_metadata']=getattr(exc,'search_metadata',meta)
    target.write_text(json.dumps(reply,ensure_ascii=False),encoding='utf-8')
if __name__=='__main__':main()
