"""Search/yield addendum to the original-first handoff; no database needed to read it."""
import csv
import io
import json
from . import pm_research_metrics as metrics


def export_details(store,state,write_text,write_json,csv_cell):
    attempts=metrics.attempts(store)
    for row in attempts:
        row.update(metrics.attempt_outcome(store,row['id']))
        if row['status'] not in ('COMPLETED','FAILED'):
            row['outcome']=row['status']
    hits=metrics.hits(store)
    overall=metrics.project_metrics(store)
    per_task={t['id']:metrics.task_metrics(store,t['id']) for t in state['tasks']}
    write_json('search_attempts.json',attempts)
    write_json('search_hits.json',hits)
    write_json('research_metrics.json',{'project':overall,'tasks':per_task,
        'definitions':{
            'fetched':'Network fetch slots attempted, not successful documents',
            'documents_collected':'Distinct documents attributed to a search (including reused originals)',
            'new_documents':'Originals newly collected by this attempt',
            'accepted_claims':'Distinct quote-checked claim IDs attributed to this attempt; NOT proven facts',
            'productive_extraction_ratio':'Final extraction ranges with >=1 adopted claim / all final extraction ranges',
            'zero_yield':'Consecutive completed attempts with no adopted claim; pending and failed work is separate',
            'attribution':'Do not sum task/attempt yields for project totals; documents/evidence can support several tasks'
        }})
    def csv_file(name,rows,columns):
        out=io.StringIO(newline='');writer=csv.DictWriter(out,fieldnames=columns);writer.writeheader()
        for row in rows:
            result={}
            for k in columns:
                value=row.get(k,'')
                if isinstance(value,(dict,list)): value=json.dumps(value,ensure_ascii=False)
                result[k]=csv_cell(value)
            writer.writerow(result)
        write_text(name,'\ufeff'+out.getvalue())
    csv_file('search_attempts.csv',attempts,['id','task_id','query','executed_query','strategy','anchors','status','outcome',
        'returned_hits','prefilter_rejected','duplicate_skipped','fetched','fetch_failed','documents_collected',
        'new_documents','pending_documents','relevant_documents','accepted_claims','extraction_failures','error','created'])
    csv_file('search_hits.csv',hits,['attempt_id','rank','url','title','snippet','host','eligible','score','reasons',
                                    'status','fetch_started','document_id','error'])
    gaps=['','## Search and productivity diagnostics',
          'Counts measure recorded work, not factual correctness. Low lexical rank is not proof of irrelevance.']
    for row in attempts:
        if row['outcome']!='YIELD':
            gaps.append(f"- {row['id']} / {row['task_id']}: {row['outcome']}; hits={row['returned_hits']}, "
                        f"fetched={row['fetched']}, rejected={row['prefilter_rejected']}, claims={row['accepted_claims']}; {row['error']}")
    with store.db() as c:
        hosts=[dict(r) for r in c.execute('SELECT * FROM host_health')]
    write_json('host_health.json',hosts)
    for row in hosts:
        gaps.append(f"- Host {row['host']}: failures={row['failures']}; last HTTP={row['last_code']}; retry-after UTC epoch={row['next_retry']}")
    for e in store.all_evidence():
        if not store.evidence_task_ids(e['id']):
            gaps.append(f"- {e['id']}: UNASSIGNED; no task adoption, original quotation retained")
    return overall,gaps
