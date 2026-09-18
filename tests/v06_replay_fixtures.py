"""Deterministic synthetic corpus and exact-quote oracle, never a real LLM benchmark."""
from pathlib import Path
import json
import time
from unittest.mock import patch

from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_engine_v05 import EngineV05
from ollama_deep_researcher.pm_engine_v06 import EngineV06
from ollama_deep_researcher.pm_benchmark import metrics_from_events,compare_efficiency

FACTS=[
    'Fixture Forge has a rated press capacity of 14000 t in 2025.',
    'Fixture Forge produced a 500 t ingot in 2025 as an actual production record.',
    'Fixture Forge plans a 18000 t press for 2028; it is not operational in 2025.',
    'Fixture Forge nuclear ring diameter is 8000 mm according to the 2025 catalogue.',
]
CLAIMS=[dict(entity='Fixture Forge',subentity=sub,metric=metric,value=value,
             unit=unit,period=period,scope=scope,claim_text=f,quote=f)
        for f,sub,metric,value,unit,period,scope in zip(FACTS,
            ['press','ingot','press','ring'],['rated capacity','produced mass','planned capacity','diameter'],
            ['14000','500','18000','8000'],['t','t','t','mm'],['2025','2025','2028','2025'],
            ['rated','actual production','planned not operational','catalogue'])]
NOISE=('English grammar explains conjunctions and communication styles. '
       'Readers study examples of language and culture. ')*38


def corpus_cases():
    return {
        'sparse_tail':[(NOISE+'\n\n')*4+FACTS[0],NOISE],
        'dense_conditions':['\n\n'.join(FACTS[:3]),'We use cookies. Accept all cookies.'],
        'three_useful_sources':[FACTS[0],FACTS[1],FACTS[3]],
        'mixed_script':['\uc6d0\uc790\ub825 \ub2e8\uc870\ud488 \uad00\ub828 \uc124\uba85.\n\n'+FACTS[3],NOISE],
        'no_useful_fact':[NOISE, '404 Not Found. Resource does not exist.'],
    }


class ReplayOracle:
    def __init__(self,version):
        self.version=version;self.payloads=[];self.last_metrics={}
    def ask(self,role,payload,check):
        check();self.payloads.append((role,dict(payload)))
        if role=='planner':
            return {'tasks':[{'title':'Fixture Forge press ingot nuclear ring capacity and plans',
                     'query':'"Fixture Forge" press ingot nuclear ring capacity plans',
                     'criteria':['Preserve rated capacity, actual record and future plan separately']} ]}
        if role=='researcher':
            if self.version==5:
                return {'query':'"Fixture Forge" press ingot nuclear ring capacity plans','strategy':'exact_entity',
                        'anchors':['Fixture Forge','press','ingot','nuclear','ring','capacity']}
            return {'entity':'Fixture Forge','gap':'press ingot nuclear ring capacity plans',
                    'keywords':['capacity'],'strategy':'exact_entity','language':'en','site_hint':''}
        if role=='extractor':
            found=[dict(c) for c in CLAIMS if c['quote'] in payload['source_text']][:payload.get('max_claims',3)]
            for claim in found: claim['task_ids']=[t['id'] for t in payload['task_catalog']]
            return {'relevance':'relevant' if found else 'irrelevant','reason':'Synthetic exact-quote oracle', 'claims':found}
        if role=='critic':
            return {'checks':[{'criterion':c['id'],'passed':True,'evidence_ids':[e['id'] for e in payload['evidence']],
                               'reason':'Synthetic presence check, not scientific evaluation'} for c in payload['criteria']],
                    'issues':[],'next_query':''}
        raise AssertionError('Unexpected role '+role)


class ReplayWeb:
    def __init__(self,documents):
        self.check=lambda:None;self.fetches=[]
        self.rows=[{'url':f'https://fixture{i}.example.org/item','title':'Fixture Forge technical source',
                    'content':'Fixture Forge press ingot nuclear ring capacity plans','body':body}
                   for i,body in enumerate(documents)]
    def search(self,query):
        self.check()
        return [{k:v for k,v in row.items() if k!='body'} for row in self.rows]
    def fetch(self,url):
        self.check();self.fetches.append(url)
        return next(row['body'] for row in self.rows if row['url']==url)


def run_case(root,version,documents):
    w=Workspace(root)
    cfg=Settings(max_attempts=1,draft_enabled=False,max_calls=240,optimization_mode='balanced')
    pid=w.create('Fixture Forge press ingot nuclear ring capability',cfg,engine_version=version)
    st=w.open(pid);model=ReplayOracle(version);web=ReplayWeb(documents)
    engine=(EngineV05 if version==5 else EngineV06)(st,model,web)
    started=time.monotonic()
    if version==6:
        with patch.object(engine.pacer,'wait',return_value=.1): engine.run(pid)
    else: engine.run(pid)
    seconds=time.monotonic()-started
    with st.db() as conn: events=[dict(r) for r in conn.execute('SELECT * FROM events ORDER BY seq')]
    evidence=st.all_evidence()
    for row in evidence: row['task_ids']=st.evidence_task_ids(row['id'])
    output=metrics_from_events(events,evidence)
    retained={row['body'] for row in st.all_documents()}
    expected={f for f in FACTS if any(f in d for d in documents)}
    seen={e['quote'] for e in evidence}
    details={'seconds_without_model_network_latency':seconds,'expected_fact_count':len(expected),
             'found_fact_count':len(expected & seen),'missing_facts':sorted(expected-seen),
             'originals_preserved':all(d in retained for d in documents),
             'status':st.load(pid)['status']}
    return output,details


def run_comparison(root):
    from dataclasses import asdict
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    results={}
    for name,documents in corpus_cases().items():
        baseline,b_detail=run_case(root/name/'v05',5,documents)
        candidate,c_detail=run_case(root/name/'v06',6,documents)
        item=compare_efficiency(baseline,candidate)
        item['baseline_validation']=b_detail;item['candidate_validation']=c_detail
        results[name]=item
    return {'kind':'SYNTHETIC_INPUT_REPLAY_WITH_EXACT_QUOTE_ORACLE',
            'actual_ollama_inference':False,'public_web_search':False,
            'notice':'Token estimates are not measured Ollama tokens. Timings omit inference/network latency. '
                     'Small controlled cases are regression evidence, not universal research-quality guarantees.',
            'cases':results}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('output')
    args=parser.parse_args();root=Path(args.output)
    result=run_comparison(root)
    (root/'comparison.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    for name,row in result['cases'].items():
        b,c=row['baseline'],row['candidate']
        print(name, 'calls',b['extractor_calls'],'->',c['extractor_calls'],
              'est.tokens',b['estimated_extractor_tokens'],'->',c['estimated_extractor_tokens'],
              'facts',row['candidate_validation']['found_fact_count'],'/',row['candidate_validation']['expected_fact_count'])
