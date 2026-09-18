"""Synthetic models for the actual v0.5 execution path; no live claims."""
from test_pm import FakeModel, TEXT, claim


class Model05(FakeModel):
    def __init__(self, task_count=1, relevant=True):
        super().__init__()
        self.payloads=[]
        self.task_count=task_count
        self.relevant=relevant
    def ask(self,role,payload,check):
        self.payloads.append((role,dict(payload)))
        if role=='planner':
            return {'tasks':[{'title':f'TEST press topic {i}', 'query':f'TEST press item {i}',
                 'criteria':['Identify original capacity']} for i in range(self.task_count)]}
        if role=='researcher':
            return {'query':payload.get('initial_query','TEST press'), 'strategy':'exact_entity','anchors':['TEST','press']}
        if role=='extractor':
            ids=[t['id'] for t in payload.get('task_catalog',[])]
            return {'relevance':'relevant' if self.relevant else 'irrelevant','reason':'Synthetic relevance',
                'claims':[dict(claim(),task_ids=ids)] if self.relevant and TEXT in payload['source_text'] else []}
        return super().ask(role,payload,check)


class Web05:
    def __init__(self, hits=None, text=TEXT):
        self.hits=hits if hits is not None else [{'url':'https://example.org/a','title':'TEST press','content':TEXT}]
        self.text=text
        self.queries=[]; self.fetches=[]
        self.check=lambda:None
    def search(self,query):
        self.queries.append(query)
        return self.hits
    def fetch(self,url):
        self.fetches.append(url)
        self.check()
        return self.text
