"""Opt-in public search and actual local-model checks. No private project data."""
import argparse
import json
from pathlib import Path
import platform
import tempfile
import time
from urllib.request import urlopen
from ollama_deep_researcher.pm_io_v06 import WebV06
from ollama_deep_researcher.pm_io import Ollama
from ollama_deep_researcher.pm_engine_v06 import EngineV06
from ollama_deep_researcher.pm_projects import Workspace
from ollama_deep_researcher.pm_types import Settings
from ollama_deep_researcher.pm_query_v062 import query_proposals
from ollama_deep_researcher.pm_query_policy import validate_intent,intent_anchors
from ollama_deep_researcher.pm_search_quality import parse_search_intent
from ollama_deep_researcher.pm_focus_v061 import score_focused_hit
from ollama_deep_researcher.pm_diagnostics import failure_details

CASES=[('Sheffield Forgemasters','SMR contracts'),('Japan Steel Works','nuclear forging'),('Doosan Enerbility','SMR supply')]
def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def search_check(output):
    cfg=Settings(source_limit=3);web=WebV06(cfg)
    report={'kind':'public_search_smoke','platform':platform.platform(),'cases':[], 'meaning':'Lexical matches are leads, not factual verification.'}
    try:report['provider']=web.preflight()
    except Exception as exc:
        report['error']=failure_details(exc,'dependency');write(output,report);return False
    for entity,gap in CASES:
        query=f'"{entity}" {gap}'
        focus=dict(entity=entity,gap=gap,keywords=[gap],strategy='exact_entity',language='en',site_hint='')
        intent=parse_search_intent(query,'exact_entity',intent_anchors(validate_intent(focus,set())))
        entry={'query':query};start=time.monotonic()
        try:
            hits=web.search(query);entry['provider']=dict(web.last_search_metadata)
            scored=[(h,score_focused_hit(intent,h,cfg,focus)) for h in hits]
            entry['hits']=[dict(h,eligible=d.accepted,score=d.score,reasons=d.reasons) for h,d in scored]
            selected=[h for h,d in scored if d.accepted and 'ENTITY_TOPIC_DEFERRED' not in d.reasons]
            entry['candidate_count']=len(selected)
            entry['fetch_attempts']=[]
            for lead in selected[:3]:
                try:
                    doc=web.fetch_document(lead['url'])
                    entry['fetch']={'url':lead['url'],'characters':len(doc.body),'metadata':doc.metadata}
                    entry['fetch_attempts'].append({'url':lead['url'],'status':'COLLECTED'})
                    if doc.body:break
                except Exception as exc:entry['fetch_attempts'].append({'url':lead['url'],'error':failure_details(exc,'fetch')})
            entry['status']='RESULTS' if hits else 'EMPTY'
        except Exception as exc:
            entry['error']=failure_details(exc,'search');entry['status']='FAILED';entry['provider']=dict(web.last_search_metadata)
        entry['seconds']=round(time.monotonic()-start,3);report['cases'].append(entry);write(output,report);time.sleep(30)
    report['passed']=all(x.get('candidate_count',0)>0 for x in report['cases']);write(output,report);return report['passed']

def model_check(output):
    report={'kind':'actual_ollama_contract_check','model':'qwen3.5:9b','cases':[],
            'meaning':'CPU inference on CI with public tasks and synthetic source text, not a laptop speed or factual accuracy test.'}
    try:
        with urlopen('http://127.0.0.1:11434/api/tags',timeout=10) as r:report['models']=json.load(r)
        with urlopen('http://127.0.0.1:11434/api/version',timeout=10) as r:report['ollama']=json.load(r)
        with tempfile.TemporaryDirectory() as td:
            cfg=Settings(think=False,request_timeout=240,draft_enabled=False);workspace=Workspace(td)
            pid=workspace.create_v06('Global nuclear and SMR forging capabilities',cfg,
                'Investigate Sheffield Forgemasters, Japan Steel Works and Doosan Enerbility. '
                'Preserve units, year, rated versus actual capacity and original quotations. '
                'Find ingot weights, forging press capacities and SMR supply records in separate searches.')
            class NoWeb:pass
            st=workspace.open(pid);engine=EngineV06(st,Ollama(cfg),NoWeb());state=st.load(pid)
            for entity,gap in CASES:
                payload={'task':entity+' '+gap,'criteria':[gap],'known_domains':[], 'feedback':['Choose an independent short target.'],
                    'previous_queries':['previous query '+str(i)+' '+('long history '*30) for i in range(12)],
                    'attempt_feedback':[{'query':'historic '+('old context '*70),'outcome':'ZERO_YIELD','unused_details':'audit '*100} for _ in range(8)]}
                record={'entity':entity};start=time.monotonic()
                try:
                    data=engine._ask(state,'researcher',payload);entries=query_proposals(data,set())
                    record.update(passed=bool(entries),response=data,queries=[e['query'] for e in entries],metrics=engine.model.last_metrics)
                except Exception as exc:record.update(passed=False,error=failure_details(exc,'researcher'))
                record['seconds']=round(time.monotonic()-start,3);report['cases'].append(record);write(output,report)
            pid=workspace.create_v06('Example Engineering forging press rated capacity',cfg,'Extract only exact source-supported equipment ratings with original units and quotations.')
            st=workspace.open(pid);engine=EngineV06(st,Ollama(cfg),NoWeb());state=st.load(pid)
            catalog=[{'id':'t001','title':'Example Engineering forging capacity','criteria':[]}]
            for body,expected in [('Example Engineering operates a forging press rated at 4200 tonnes.','relevant'),('This university offers student courses and publishes campus rankings.','irrelevant')]:
                record={'source':'synthetic','expected':expected}
                try:
                    data=engine._ask(state,'extractor',{'task_catalog':catalog,'source_text':body,'source_url':'https://example.org/synthetic','source_range':{'start':0,'end':len(body)},'max_claims':3})
                    quotes=[c.get('quote','') for c in data.get('claims',[])]
                    record.update(response=data,metrics=engine.model.last_metrics,passed=(data.get('relevance')==expected and (bool(quotes) and all(q and q in body for q in quotes) if expected=='relevant' else not quotes)))
                except Exception as exc:record.update(passed=False,error=failure_details(exc,'extractor'))
                report['cases'].append(record);write(output,report)
    except Exception as exc:report['error']=failure_details(exc,'setup')
    report['passed']=len(report['cases'])==5 and all(c.get('passed') for c in report['cases']);write(output,report);return report['passed']

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['search','model']);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    passed=search_check(args.output) if args.mode=='search' else model_check(args.output)
    print(json.dumps(dict(mode=args.mode,passed=passed,report=str(args.output))));raise SystemExit(0 if passed else 1)
