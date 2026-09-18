"""Auditable retrieval decisions, complete event counters and source-text replay."""
from dataclasses import asdict
import csv
import io
import json

from . import pm_v06_store as audit
from . import pm_research_metrics as metrics
from .pm_benchmark import metrics_from_events, ratio, write_replay_bundle


def export_details(store, state, documents, evidence, events, folder,
                   write_text, write_json, csv_cell):
    plans = audit.plans(store)
    decisions = audit.decisions(store)
    observed = asdict(metrics_from_events(events, evidence))
    operational = metrics.project_metrics(store)
    # The hit table is current across interruptions; log snapshots are historical.
    observed.update(fetch_attempts=operational['fetch_attempts'],
                    fetch_failures=operational['fetch_failures'])
    observed['productive_extraction_ratio'] = ratio(
        observed['productive_extractions'], observed['completed_extractions'])
    observed['claims_per_model_call'] = ratio(observed['accepted_claims'], observed['model_calls'])
    observed['claims_per_observed_extractor_token'] = ratio(
        observed['accepted_claims'], observed['extractor_prompt_tokens'])
    observed['retrieval'] = audit.project_counters(store)
    observed['notice'] = ('Acceptance is not factual accuracy. Null token usage is unobserved. '
                          'Estimated tokens are separate. Deferred text remains in originals. '
                          'Duplicate-body aliases are not independent supporting sources.')
    write_json('efficiency_metrics.json', observed)
    write_json('retrieval_plans.json', plans)
    write_json('retrieval_decisions.json', decisions)
    write_json('reference_decisions.json', audit.reference_rows(store))
    with store.db() as c:
        pacing = [dict(key=r['key'], **json.loads(r['data']))
                  for r in c.execute('SELECT * FROM pacing_v06')] if c.execute(
                      "SELECT 1 FROM sqlite_master WHERE name='pacing_v06'").fetchone() else []
        intents = [dict(attempt_id=r['attempt_id'], intent=json.loads(r['data']))
                   for r in c.execute('SELECT * FROM search_intents_v06')]
        excerpts = [dict(r) for r in c.execute('SELECT * FROM search_excerpts_v06')]
    write_json('pacing.json', pacing)
    write_json('research_intents.json', intents)
    write_json('search_excerpts.json', excerpts)
    write_text('chunk_rankings.jsonl', ''.join(json.dumps(p, ensure_ascii=False) + '\n' for p in plans))
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=['seq', 'document_id', 'kind', 'data', 'created'])
    writer.writeheader()
    for row in decisions:
        writer.writerow({key: csv_cell(json.dumps(value, ensure_ascii=False)
                         if isinstance(value, (dict, list)) else value) for key, value in row.items()})
    write_text('retrieval_decisions.csv', '\ufeff' + out.getvalue())
    attempts = metrics.attempts(store)
    for attempt in attempts:
        attempt.update(metrics.attempt_outcome(store, attempt['id']))
    replay = write_replay_bundle(folder / 'replay', documents, metrics.hits(store), attempts,
        {'project_id': state['id'], 'engine_version': 6, 'topic': state['topic'],
         'instructions': state['instructions'], 'settings': state['settings'],
         'task_catalog': state.get('tasks', []),
         'scope': 'Captured search/page inputs. Model output is not a ground-truth label.'})
    # Include every replay member in the outer manifest using the existing writer.
    for path in sorted(replay.rglob('*')):
        if path.is_file():
            write_text(path.relative_to(folder).as_posix(), path.read_text(encoding='utf-8'))
    gaps = ['', '## v0.6 retrieval coverage (not completeness or accuracy)']
    for plan in plans:
        if plan['status'] != 'DONE' or plan['unread_chars']:
            gaps.append(f"- {plan['document_id']}: {plan['status']}; "
                        f"unread original characters={plan['unread_chars']}; "
                        f"alias_of={plan.get('alias_of') or '-'}; "
                        f"reason={plan.get('quality', {})}")
    for task in state['tasks']:
        if task.get('v06_stop'):
            gaps.append(f"- {task['id']}: {task['v06_stop']}; bounded exploration stopped, "
                        'not proof that no additional information exists.')
    return observed, gaps
