"""Deterministic evidence oracle; it never ranks documents or calls a real model."""
from test_pm import FakeModel, TEXT, claim


class Model06(FakeModel):
    def __init__(self, task_count=1, intents=None):
        super().__init__()
        self.task_count=task_count
        self.payloads=[]
        self.intents=intents or []
        self.index=0
        self.last_metrics={}

    def ask(self,role,payload,check):
        self.payloads.append((role,dict(payload)))
        check()
        if role=='planner':
            return {'tasks':[{'title':f'TEST press capacity {i}', 'query':f'TEST press capacity {i}',
                              'criteria':['Identify original press capacity']} for i in range(self.task_count)]}
        if role=='researcher':
            if self.intents:
                value=self.intents[min(self.index,len(self.intents)-1)]
                self.index+=1
                return dict(value)
            return {'entity':'TEST press','gap':'capacity','keywords':['capacity','press'],
                    'strategy':'exact_entity','language':'en','site_hint':''}
        if role=='extractor':
            supported=TEXT in payload['source_text']
            return {'relevance':'relevant' if supported else 'irrelevant',
                    'reason':'Synthetic exact source match only',
                    'claims':[dict(claim(),task_ids=[t['id'] for t in payload['task_catalog']])]
                             if supported else []}
        return super().ask(role,payload,check)


class Web06:
    def __init__(self,hits=None,bodies=None):
        self.hits=hits if hits is not None else [{'url':'https://example.org/a','title':'TEST press','content':TEXT}]
        self.bodies=bodies or {}
        self.queries=[];self.fetches=[];self.check=lambda:None
    def search(self,query):
        self.check();self.queries.append(query)
        return self.hits
    def fetch(self,url):
        self.check();self.fetches.append(url)
        value=self.bodies.get(url,TEXT)
        if isinstance(value,BaseException): raise value
        return value
