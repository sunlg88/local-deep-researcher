"""Versioned search boundary. Original v0.5 transport remains selectable."""
from .pm_io import Web


class SearchAdapterError(RuntimeError):
    def __init__(self,kind,code=None,retry_after=''):
        self.code=code
        self.headers={'Retry-After':retry_after} if retry_after else {}
        super().__init__(f'Search backend failed ({kind}; HTTP {code}); not an empty result')


def parse_search_reply(data):
    if not isinstance(data,dict): raise ValueError('Search reply must be an object')
    if 'error' in data:
        error=data['error']
        if not isinstance(error,dict): raise ValueError('Malformed search failure')
        raise SearchAdapterError(str(error.get('type','UnknownError'))[:100],
                                 error.get('code'),str(error.get('retry_after',''))[:100])
    rows=data.get('results')
    if not isinstance(rows,list) or not all(isinstance(row,dict) for row in rows):
        raise ValueError('Missing or malformed search results')
    return rows


class WebV06(Web):
    worker_module='ollama_deep_researcher.pm_search_worker_v062'

    def preflight(self):
        from .pm_search_worker_v062 import provider_metadata
        self.last_search_metadata=provider_metadata(self.settings.search_api)
        return dict(self.last_search_metadata)

    def search(self,query):
        self.preflight()
        return super().search(query)

    def search_reply(self,data):
        self.last_search_metadata=data.get('search_metadata',{}) if isinstance(data,dict) else {}
        return parse_search_reply(data)
