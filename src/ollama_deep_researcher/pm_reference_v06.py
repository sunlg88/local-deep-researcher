"""Read only explicitly selected project originals, rank once, keep rejection trail."""
from .pm_projects import read_database
from .pm_retrieval_v06 import rank_chunks
from .pm_chunking_v06 import build_chunks
from .pm_fetch_quality import assess_fetched_page
from . import pm_v06_store as audit
from .pm_types import Settings


def reference_candidates(workspace, pid, query, task_terms, limit=12, check=lambda: None):
    state=workspace.load(pid)
    cfg=Settings.from_saved(state['settings'])
    store=workspace.open(pid)
    candidates=[]
    # Scan bounded batches: do not stop at the first generic SQL LIKE match.
    for origin in state.get('reference_projects',[]):
        offset=0
        while True:
            check()
            with read_database(workspace._db_path(origin)) as c:
                rows=c.execute('SELECT id,url,title,body,content_hash FROM documents ORDER BY id LIMIT 20 OFFSET ?',
                               (offset,)).fetchall()
            if not rows: break
            offset+=len(rows)
            for row in rows:
                check()
                quality=assess_fetched_page(query_anchors=[query]+task_terms,search_title='',search_snippet='',
                                           fetched_title=row['title'],body=row['body'])
                chunks=build_chunks(row['body'])
                ranked=rank_chunks(query,chunks,task_terms)
                score=max((x.lexical_score for x in ranked),default=0)
                candidate={'origin_project_id':origin,'origin_document_id':row['id'],
                    'url':row['url'],'title':row['title'],'content_hash':row['content_hash'],
                    'score':score,'quality':quality.status,'retrieval_reason':quality.reasons}
                allowed=cfg.allows(row['url'])
                status='CANDIDATE' if allowed and quality.status=='READY' and score>0 else 'DEFERRED' if allowed else 'POLICY_REJECTED'
                audit.record_reference(store,candidate,status)
                if status=='CANDIDATE': candidates.append(candidate)
            # Keep only the top bounded list in memory; all decisions remain in SQLite.
            candidates=sorted(candidates,key=lambda x:(-x['score'],x['origin_project_id'],x['origin_document_id']))[:limit]
    return candidates
