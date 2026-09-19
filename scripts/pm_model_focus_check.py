"""Inspect task binding as well as syntactic validity of actual model replies."""
import argparse
import json
from pathlib import Path
from pm_live_validation import model_check, write

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    model_check(args.output)
    report=json.loads(args.output.read_text(encoding='utf-8'))
    for case in report.get('cases',[]):
        if 'entity' in case:
            correct=case.get('response',{}).get('entity','').casefold()==case['entity'].casefold()
            case['correct_task_entity']=correct
            case['passed']=bool(case.get('passed') and correct)
    report['passed']=len(report.get('cases',[]))==5 and all(case.get('passed') for case in report['cases'])
    report['task_binding_checked']=True
    write(args.output,report)
    print(json.dumps({'passed':report['passed'],'task_binding_checked':True,'report':str(args.output)}))
    raise SystemExit(0 if report['passed'] else 1)
