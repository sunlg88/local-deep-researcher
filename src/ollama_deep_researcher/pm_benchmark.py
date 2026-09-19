"""Reproducible input bundles and honest efficiency metrics, not accuracy scores."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from .pm_store import atomic_text
from .pm_types import canonical_url


@dataclass(frozen=True)
class EfficiencyMetrics:
    model_calls: int = 0
    extractor_calls: int = 0
    extractor_prompt_tokens: int | None = 0
    irrelevant_extractions: int = 0
    accepted_claims: int = 0
    unique_relevant_documents: int = 0
    fetch_attempts: int = 0
    fetch_failures: int = 0
    zero_yield_searches: int = 0
    duplicate_fetches: int = 0
    duplicate_extractions: int = 0
    estimated_extractor_tokens: int = 0
    productive_extractions: int = 0
    completed_extractions: int = 0
    failed_model_calls: int = 0


def event_payload(event: dict) -> dict:
    if not isinstance(event, dict) or not isinstance(event.get('kind'), str):
        raise ValueError('Malformed event')
    if 'message' in event:
        try:
            payload = json.loads(event['message'])
        except (ValueError, TypeError) as exc:
            raise ValueError('Malformed event JSON') from exc
        if not isinstance(payload, dict):
            raise ValueError('Event payload must be an object')
        return dict(payload, kind=event['kind'])
    return dict(event)


def metrics_from_events(events: list[dict], evidence: list[dict]) -> EfficiencyMetrics:
    """Count invocation IDs once; completion/log replay is not another invocation.

    Token totals stay unknown when even one extractor invocation has no observed
    usage. Estimates are reported separately. An accepted ID is quote-checked,
    not independently verified truth. Unassigned claims are not accepted.
    """
    calls, extracts, searches = {}, {}, {}
    duplicate_fetches = duplicate_extractions = 0
    measured = {'MODEL_COMPLETED', 'MODEL_FAILED', 'planner', 'researcher',
                'extractor', 'critic', 'writer', 'EXTRACTION_COMPLETED',
                'SEARCH_COMPLETED', 'DUPLICATE_FETCH', 'DUPLICATE_EXTRACTION'}
    for index, raw in enumerate(events):
        if raw.get('kind') not in measured:
            continue
        e = event_payload(raw)
        kind = e['kind']
        if kind in ('MODEL_COMPLETED', 'MODEL_FAILED') or e.get('status') == 'started':
            key = e.get('call', ('event', index))
            row = calls.setdefault(key, {})
            row.update(e)
            row['role'] = e.get('role', kind if kind not in ('MODEL_COMPLETED', 'MODEL_FAILED') else row.get('role'))
        elif kind == 'EXTRACTION_COMPLETED':
            key = (e.get('document', e.get('document_id')), e.get('start'), e.get('end'))
            # A logical range completed twice is recorded for audit, not counted twice.
            if key in extracts:
                duplicate_extractions += 1
            extracts[key] = e
        elif kind == 'SEARCH_COMPLETED':
            searches[e.get('attempt', ('event', index))] = e
        elif kind == 'DUPLICATE_FETCH':
            duplicate_fetches += 1
        elif kind == 'DUPLICATE_EXTRACTION':
            duplicate_extractions += 1
    ec = [e for e in calls.values() if e.get('role') == 'extractor']
    actual = [e.get('prompt_eval_count') for e in ec]
    total = sum(actual) if all(type(n) is int and n >= 0 for n in actual) else None
    accepted = {e['id'] for e in evidence if 'task_ids' not in e or e['task_ids']}
    return EfficiencyMetrics(
        model_calls=len(calls), extractor_calls=len(ec), extractor_prompt_tokens=total,
        irrelevant_extractions=sum(e.get('relevance') == 'irrelevant' for e in extracts.values()),
        accepted_claims=len(accepted),
        unique_relevant_documents=len({k[0] for k, e in extracts.items() if e.get('relevance') == 'relevant'}),
        fetch_attempts=sum(e.get('fetched', 0) for e in searches.values()),
        fetch_failures=sum(e.get('fetch_failed', 0) for e in searches.values()),
        zero_yield_searches=sum(e.get('outcome') == 'ZERO_YIELD' for e in searches.values()),
        duplicate_fetches=duplicate_fetches, duplicate_extractions=duplicate_extractions,
        estimated_extractor_tokens=sum(e.get('estimated_input_tokens', 0) for e in ec),
        productive_extractions=sum(e.get('accepted_claims', 0) > 0 for e in extracts.values()),
        completed_extractions=len(extracts),
        failed_model_calls=sum(e.get('kind') == 'MODEL_FAILED' for e in calls.values()),
    )


def ratio(numerator, denominator):
    return numerator / denominator if denominator is not None and denominator > 0 else None


def compare_efficiency(baseline: EfficiencyMetrics, candidate: EfficiencyMetrics) -> dict:
    result = {'baseline': asdict(baseline), 'candidate': asdict(candidate),
              'accepted_claims_delta': candidate.accepted_claims - baseline.accepted_claims}
    for label, n, d in (
        ('claims_per_model_call', 'accepted_claims', 'model_calls'),
        ('claims_per_prompt_token', 'accepted_claims', 'extractor_prompt_tokens'),
        ('productive_extraction_ratio', 'productive_extractions', 'completed_extractions'),
        ('irrelevant_extraction_ratio', 'irrelevant_extractions', 'completed_extractions'),
        ('fetch_failure_ratio', 'fetch_failures', 'fetch_attempts'),
    ):
        result[label] = {name: ratio(getattr(obj, n), getattr(obj, d))
                         for name, obj in (('baseline', baseline), ('candidate', candidate))}
    for label, field in (('irrelevant_call_reduction', 'irrelevant_extractions'),
                         ('extractor_prompt_token_reduction', 'extractor_prompt_tokens')):
        a, b = getattr(baseline, field), getattr(candidate, field)
        result[label] = 1 - b / a if a and b is not None else None
    result['notice'] = 'Operational metrics, not factual accuracy. Unobserved tokens are null, never invented.'
    return result


def _safe_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise ValueError('Invalid replay path')
    p = root / relative
    if Path(relative).is_absolute() or not p.resolve().is_relative_to(root.resolve()):
        raise ValueError('Replay path escapes bundle')
    if any(x.is_symlink() for x in (p, *p.parents) if x != root.parent):
        raise ValueError('Replay symlinks are forbidden')
    return p


def _read_json(path: Path, max_bytes=20_000_000):
    if path.stat().st_size > max_bytes:
        raise ValueError('Replay entry exceeds size bound')
    return json.loads(path.read_text(encoding='utf-8'))


def write_replay_bundle(root: Path, documents: list[dict], hits: list[dict],
                        outcomes: list[dict], metadata: dict, labels: dict | None = None) -> Path:
    """Write original parsed text, not generated summaries. Labels are caller-supplied."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    entries = []
    seen = set()
    for index, doc in enumerate(documents):
        did, body = doc['id'], doc['body']
        if not isinstance(did, str) or not isinstance(body, str) or did in seen:
            raise ValueError('Document ID/body invalid or duplicated')
        seen.add(did)
        url = canonical_url(doc['url'])
        rel = f'documents/{index:06d}.txt'
        atomic_text(_safe_file(root, rel), body)
        entries.append({'id': did, 'url': url, 'title': doc.get('title', ''),
                        'body_path': rel, 'text_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest()})
    for name, rows in (('search_hits.jsonl', hits), ('fetch_outcomes.jsonl', outcomes)):
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError('Replay rows must be objects')
        atomic_text(root / name, ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
    atomic_text(root / 'expected_labels.json', json.dumps(labels or {}, ensure_ascii=False, indent=2))
    integrity={name:hashlib.sha256((root/name).read_bytes()).hexdigest()
               for name in ('search_hits.jsonl','fetch_outcomes.jsonl','expected_labels.json')}
    atomic_text(root / 'manifest.json', json.dumps({'schema_version': 1, 'files':integrity, 'documents': entries,
        'metadata': metadata, 'notice': 'Original replay inputs. Empty labels mean no ground truth supplied.'},
        ensure_ascii=False, indent=2))
    return root


def load_replay_bundle(path: Path) -> dict[str, Any]:
    root = Path(path).resolve()
    m = _read_json(_safe_file(root, 'manifest.json'))
    if not isinstance(m, dict) or m.get('schema_version') != 1 or not isinstance(m.get('documents'), list):
        raise ValueError('Invalid replay manifest')
    integrity=m.get('files')
    if not isinstance(integrity,dict) or set(integrity)!= {'search_hits.jsonl','fetch_outcomes.jsonl','expected_labels.json'}:
        raise ValueError('Missing replay input hashes')
    for name,expected in integrity.items():
        source=_safe_file(root,name)
        if source.stat().st_size>20_000_000:raise ValueError('Replay input size exceeded')
        if hashlib.sha256(source.read_bytes()).hexdigest()!=expected:raise ValueError('Replay input hash mismatch')
    if len(m['documents']) > 10000:
        raise ValueError('Replay document count exceeds bound')
    docs, seen = [], set()
    total = 0
    for d in m['documents']:
        if not isinstance(d, dict) or not all(k in d for k in ('id', 'url', 'body_path', 'text_sha256')):
            raise ValueError('Missing replay document identity')
        if d['id'] in seen:
            raise ValueError('Duplicate document ID')
        seen.add(d['id'])
        canonical_url(d['url'])
        p = _safe_file(root, d['body_path'])
        total += p.stat().st_size
        if p.stat().st_size > 20_000_000 or total > 200_000_000:
            raise ValueError('Replay text size exceeds bound')
        content = p.read_bytes()
        if hashlib.sha256(content).hexdigest() != d['text_sha256']:
            raise ValueError('Replay text hash mismatch')
        docs.append(dict(d, body=content.decode('utf-8')))
    result = dict(m, documents=docs)
    for name in ('search_hits', 'fetch_outcomes'):
        p = _safe_file(root, name + '.jsonl')
        if p.stat().st_size > 20_000_000:
            raise ValueError('Replay rows too large')
        rows = [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]
        if not all(isinstance(r, dict) for r in rows):
            raise ValueError('Malformed replay row')
        result[name] = rows
    result['expected_labels'] = _read_json(_safe_file(root, 'expected_labels.json'))
    return result


def read_handoff_metrics(folder: Path) -> dict:
    """Compare existing exports without modifying either project or guessing log gaps."""
    root=Path(folder).resolve()
    event_file=_safe_file(root,'events.jsonl')
    if event_file.stat().st_size>50_000_000: raise ValueError('Event log exceeds comparison memory bound')
    events=[json.loads(line) for line in event_file.read_text(encoding='utf-8').splitlines() if line.strip()]
    evidence=_read_json(_safe_file(root,'evidence.json'))
    if not isinstance(evidence,list): raise ValueError('Evidence export must be a list')
    value=metrics_from_events(events,evidence)
    project={}
    pack=_safe_file(root,'evidence_pack.json')
    if pack.is_file(): project=_read_json(pack).get('project',{})
    expected=project.get('calls')
    complete=type(expected) is int and value.model_calls==expected
    warnings=[]
    if not complete: warnings.append('TRUNCATED_OR_INCOMPLETE_EVENT_LOG' if expected is not None else 'TOTAL_CALL_COUNT_UNKNOWN')
    if value.extractor_prompt_tokens is None: warnings.append('OBSERVED_TOKEN_TOTAL_INCOMPLETE')
    return {'metrics':asdict(value),'complete_call_log':complete,'warnings':warnings,
            'settings':project.get('settings',{}),'project_id':project.get('id')}


def main():
    import argparse
    parser=argparse.ArgumentParser(description='Read-only Research PM efficiency comparison; not an accuracy score')
    commands=parser.add_subparsers(dest='command',required=True)
    compare=commands.add_parser('compare')
    compare.add_argument('baseline');compare.add_argument('candidate');compare.add_argument('--output')
    validate=commands.add_parser('validate-replay');validate.add_argument('bundle')
    args=parser.parse_args()
    if args.command=='validate-replay':
        bundle=load_replay_bundle(Path(args.bundle))
        result={'valid':True,'documents':len(bundle['documents']),
                'has_ground_truth':bool(bundle['expected_labels'])}
    else:
        base=read_handoff_metrics(Path(args.baseline));candidate=read_handoff_metrics(Path(args.candidate))
        result={'baseline_input':base,'candidate_input':candidate,
                'comparison':compare_efficiency(EfficiencyMetrics(**base['metrics']),EfficiencyMetrics(**candidate['metrics'])),
                'notice':'A change in model/search/settings/cache makes this an observational, not controlled, comparison.'}
    output=json.dumps(result,ensure_ascii=False,indent=2)
    if getattr(args,'output',None): atomic_text(Path(args.output),output)
    print(output)


if __name__=='__main__': main()
