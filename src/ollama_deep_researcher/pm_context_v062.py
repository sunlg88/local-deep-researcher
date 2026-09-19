"""Bounded model views; the original question and persistent audit are unchanged."""
from copy import deepcopy
from .pm_budget import request_parts, FixedPromptBudgetError

OPTIONAL = ('evidence', 'attempt_feedback', 'previous_queries', 'known_domains', 'feedback')


def fit_research_payload(cfg, payload, margin=256):
    repair=bool(payload.get('compact_retry'))
    core={k:deepcopy(v) for k,v in payload.items() if k not in OPTIONAL}
    core['active_task']=payload.get('task','')
    original=request_parts(cfg,'researcher',payload)[2]
    base=request_parts(cfg,'researcher',core)[2]
    if base['estimated_input_tokens']>base['input_budget']:
        raise FixedPromptBudgetError('Immutable researcher question/task does not fit after optional history removal; original and audit retained.')
    view=dict(core)
    view['known_domains']=list(payload.get('known_domains',[]))[:4 if repair else 12]
    view['previous_queries']=list(payload.get('previous_queries',[]))[-(1 if repair else 4):]
    view['feedback']=[str(x)[:180] for x in list(payload.get('feedback',[]))[:1 if repair else 3]]
    keys=('query','outcome','new_accepted_claims','pending_documents','fetch_failed')
    rows=list(payload.get('attempt_feedback',[]))[:1 if repair else 3]
    view['attempt_feedback']=[{k:deepcopy(r[k]) for k in keys if k in r} for r in rows if isinstance(r,dict)]
    ekeys=('id','entity','metric','value','unit','period','scope')
    view['evidence']=[] if repair else [{k:deepcopy(r[k]) for k in ekeys if k in r} for r in list(payload.get('evidence',[]))[-2:] if isinstance(r,dict)]
    target=max(base['estimated_input_tokens'],base['input_budget']-margin)
    removed=[]
    while request_parts(cfg,'researcher',view)[2]['estimated_input_tokens']>target:
        for field in OPTIONAL:
            if view.get(field):
                view[field].pop(0 if field=='previous_queries' else -1);removed.append(field);break
        else:
            view=core;break
    # Put the current task after broad project/history context. A global company
    # list must not silently replace the task selected by the scheduler.
    focus=view.pop('active_task',payload.get('task',''))
    view['active_task']=focus
    final=request_parts(cfg,'researcher',view)[2]
    return view,dict(original_estimate=original['estimated_input_tokens'],bounded_estimate=final['estimated_input_tokens'],
        input_budget=final['input_budget'],repair=repair,question_preserved=True,audit_history_preserved=True,pruned_fields=sorted(set(removed)))
