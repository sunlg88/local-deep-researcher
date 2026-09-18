"""Persistent v0.5 search decisions, extraction outcomes and honest yield metrics.

Search-derived yields are attribution counts, not additive unique discoveries.
Project evidence is counted once. Pending extraction is never called zero yield.
"""
import json
import time
import uuid
from .pm_store import now


def initialize(store):
    with store.db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS evidence_tasks(
          evidence_id TEXT NOT NULL REFERENCES evidence(id), task_id TEXT NOT NULL,
          PRIMARY KEY(evidence_id,task_id));
        CREATE INDEX IF NOT EXISTS et_task ON evidence_tasks(task_id,evidence_id);
        CREATE TABLE IF NOT EXISTS evidence_task_reviews(
          evidence_id TEXT NOT NULL, task_id TEXT NOT NULL, status TEXT NOT NULL,
          reason TEXT NOT NULL, updated TEXT NOT NULL,
          PRIMARY KEY(evidence_id,task_id),
          FOREIGN KEY(evidence_id,task_id) REFERENCES evidence_tasks(evidence_id,task_id));
        CREATE TABLE IF NOT EXISTS search_attempts(
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
          query TEXT NOT NULL, executed_query TEXT NOT NULL, strategy TEXT NOT NULL,
          anchors TEXT NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
          created TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS attempts_task ON search_attempts(task_id,created);
        CREATE TABLE IF NOT EXISTS search_hits(
          attempt_id TEXT NOT NULL REFERENCES search_attempts(id), rank INTEGER NOT NULL,
          url TEXT NOT NULL, title TEXT NOT NULL, snippet TEXT NOT NULL,
          host TEXT NOT NULL, eligible INTEGER NOT NULL, score REAL NOT NULL,
          reasons TEXT NOT NULL, status TEXT NOT NULL, document_id TEXT,
          fetch_started INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(attempt_id,rank));
        CREATE INDEX IF NOT EXISTS hits_status ON search_hits(status,host);
        CREATE TABLE IF NOT EXISTS attempt_documents(
          attempt_id TEXT NOT NULL REFERENCES search_attempts(id),
          document_id TEXT NOT NULL REFERENCES documents(id), is_new INTEGER NOT NULL,
          PRIMARY KEY(attempt_id,document_id));
        CREATE INDEX IF NOT EXISTS ad_document ON attempt_documents(document_id);
        CREATE TABLE IF NOT EXISTS document_jobs(
          document_id TEXT PRIMARY KEY REFERENCES documents(id), status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS extraction_outcomes(
          work_key TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
          start INTEGER NOT NULL, end INTEGER NOT NULL, relevance TEXT NOT NULL,
          returned_claims INTEGER NOT NULL, accepted_count INTEGER NOT NULL,
          evidence_ids TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS extraction_doc ON extraction_outcomes(document_id,relevance);
        CREATE TABLE IF NOT EXISTS host_health(
          host TEXT PRIMARY KEY, failures INTEGER NOT NULL, denied INTEGER NOT NULL,
          last_code INTEGER, last_failure REAL NOT NULL, next_retry REAL NOT NULL);
        ''')


def begin_search_attempt(store, project_id, task_id, query, strategy, anchors, executed_query=None):
    store._bound(project_id)
    aid='s-'+uuid.uuid4().hex[:24]
    with store.db() as c:
        c.execute('INSERT INTO search_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (aid,project_id,task_id,query,executed_query or query,strategy,
             json.dumps(anchors,ensure_ascii=False),'PLANNED','',now(),now()))
    return aid


def finish_search_attempt(store, attempt_id, status='COMPLETED', error='', **counts):
    with store.db() as c:
        c.execute('UPDATE search_attempts SET status=?,error=?,updated=? WHERE id=?',
                  (status,str(error)[:1500],now(),attempt_id))


def _attempt(row):
    d=dict(row); d['anchors']=json.loads(d['anchors']); return d


def attempts(store):
    with store.db() as c:
        return [_attempt(r) for r in c.execute('SELECT * FROM search_attempts ORDER BY created,rowid')]


def attempt(store, aid):
    with store.db() as c:
        row=c.execute('SELECT * FROM search_attempts WHERE id=?',(aid,)).fetchone()
    if row is None:
        raise KeyError('Missing search attempt: '+aid)
    return _attempt(row)


def record_hit_decision(store, attempt_id, hit, decision, rank=0):
    with store.db() as c:
        # Never overwrite a completed fetch when replaying persisted search results.
        c.execute('INSERT OR IGNORE INTO search_hits(attempt_id,rank,url,title,snippet,host,eligible,score,reasons,status) '
                  'VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (attempt_id,rank,str(hit.get('url',''))[:4096],str(hit.get('title',''))[:1000],
                   str(hit.get('content',''))[:6000],decision.host,int(decision.accepted),decision.score,
                   json.dumps(decision.reasons),'CANDIDATE' if decision.accepted else 'REJECTED'))


def update_hit(store, aid, rank, status, document_id=None, fetch_started=None, error='', reason=None):
    with store.db() as c:
        row=c.execute('SELECT reasons,fetch_started,document_id FROM search_hits WHERE attempt_id=? AND rank=?',
                      (aid,rank)).fetchone()
        if row is None:
            raise KeyError('Missing search hit')
        reasons=json.loads(row['reasons'])
        if reason and reason not in reasons:
            reasons.append(reason)
        c.execute('UPDATE search_hits SET status=?,document_id=?,fetch_started=?,error=?,reasons=? '
                  'WHERE attempt_id=? AND rank=?',
                  (status,document_id or row['document_id'],row['fetch_started'] if fetch_started is None else int(fetch_started),
                   str(error)[:1500],json.dumps(reasons),aid,rank))


def hits(store, aid=None):
    with store.db() as c:
        sql='SELECT * FROM search_hits'+(' WHERE attempt_id=?' if aid else '')+' ORDER BY attempt_id,rank'
        rows=[dict(r) for r in c.execute(sql,(aid,) if aid else ())]
    for row in rows:
        row['reasons']=json.loads(row['reasons'])
        row['content']=row['snippet']
    return rows


def link_attempt_document(store, aid, did, is_new):
    with store.db() as c:
        c.execute('INSERT OR IGNORE INTO attempt_documents VALUES(?,?,?)',(aid,did,int(is_new)))
        c.execute("INSERT OR IGNORE INTO document_jobs VALUES(?,'PENDING')",(did,))


def queue_document(store, did):
    with store.db() as c:
        c.execute("INSERT OR IGNORE INTO document_jobs VALUES(?,'PENDING')",(did,))


def finish_document(store, did):
    with store.db() as c:
        c.execute("INSERT OR REPLACE INTO document_jobs VALUES(?,'DONE')",(did,))


def record_extraction(store, key, did, start, end, relevance, returned, evidence_ids):
    ids=list(dict.fromkeys(evidence_ids))
    with store.db() as c:
        c.execute('INSERT OR REPLACE INTO extraction_outcomes VALUES(?,?,?,?,?,?,?,?)',
                  (key,did,start,end,relevance,returned,len(ids),json.dumps(ids)))


def attempt_outcome(store, aid):
    with store.db() as c:
        h=c.execute('''SELECT count(*) AS returned_hits,
          coalesce(sum(eligible=0),0) AS prefilter_rejected,
          coalesce(sum(status='DUPLICATE'),0) AS duplicate_skipped,
          coalesce(sum(fetch_started),0) AS fetched,
          coalesce(sum(status='FETCH_FAILED'),0) AS fetch_failed
          FROM search_hits WHERE attempt_id=?''',(aid,)).fetchone()
        data=dict(h)
        d=c.execute('''SELECT count(*) AS documents_collected, coalesce(sum(a.is_new),0) AS new_documents,
          coalesce(sum(j.status IS NULL OR j.status!='DONE'),0) AS pending_documents
          FROM attempt_documents a LEFT JOIN document_jobs j ON a.document_id=j.document_id
          WHERE a.attempt_id=?''',(aid,)).fetchone()
        data.update(dict(d))
        data['relevant_documents']=c.execute('''SELECT count(DISTINCT a.document_id)
          FROM attempt_documents a JOIN extraction_outcomes x ON a.document_id=x.document_id
          WHERE a.attempt_id=? AND x.relevance='relevant' ''',(aid,)).fetchone()[0]
        # Count unique accepted IDs from the outcome table, not unreviewed/unassigned records.
        rows=c.execute('''SELECT x.evidence_ids FROM extraction_outcomes x JOIN attempt_documents a
          ON a.document_id=x.document_id WHERE a.attempt_id=?''',(aid,)).fetchall()
        data['accepted_claims']=len({eid for r in rows for eid in json.loads(r[0])})
        data['extraction_failures']=c.execute('''SELECT count(*) FROM extraction_outcomes x
            JOIN attempt_documents a ON a.document_id=x.document_id
            WHERE a.attempt_id=? AND x.relevance='failed' ''',(aid,)).fetchone()[0]
    if data['pending_documents']:
        data['outcome']='EXTRACTION_PENDING'
    elif data['extraction_failures'] and not data['accepted_claims']:
        data['outcome']='EXTRACTION_FAILED'
    else:
        data['outcome']='YIELD' if data['accepted_claims'] else 'ZERO_YIELD'
    return data


def task_search_feedback(store, task_id, limit=3):
    with store.db() as c:
        rows=c.execute('SELECT * FROM search_attempts WHERE task_id=? ORDER BY rowid DESC LIMIT ?',
                       (task_id,min(20,max(1,limit)))).fetchall()
    result=[]
    for row in rows:
        item=_attempt(row); item.update(attempt_outcome(store,item['id']))
        if item['status'] not in ('COMPLETED','FAILED'):
            item['outcome']=item['status']
        result.append(item)
    return result


def project_metrics(store):
    with store.db() as c:
        h=dict(c.execute('''SELECT count(*) AS hits_screened,
          coalesce(sum(eligible=0),0) AS prefilter_rejected,
          coalesce(sum(status='DUPLICATE'),0) AS duplicate_fetch_avoided,
          coalesce(sum(fetch_started),0) AS fetch_attempts,
          coalesce(sum(status='FETCH_FAILED'),0) AS fetch_failures
          FROM search_hits''').fetchone())
        x=dict(c.execute('''SELECT count(*) AS extractions,
          coalesce(sum(relevance='irrelevant'),0) AS irrelevant_extractions,
          coalesce(sum(accepted_count>0),0) AS productive_extractions
          FROM extraction_outcomes''').fetchone())
        h.update(x)
        h['unique_documents']=c.execute('SELECT count(*) FROM documents').fetchone()[0]
        h['unique_evidence']=c.execute('SELECT count(*) FROM evidence').fetchone()[0]
        h['host_failures']=c.execute('SELECT coalesce(sum(failures),0) FROM host_health').fetchone()[0]
        h['search_attempts']=c.execute('SELECT count(*) FROM search_attempts').fetchone()[0]
    h['productive_extraction_ratio']=h['productive_extractions']/h['extractions'] if h['extractions'] else None
    return h


def task_metrics(store, task_id):
    with store.db() as c:
        data={'searches':c.execute('SELECT count(*) FROM search_attempts WHERE task_id=?',(task_id,)).fetchone()[0]}
        data['fetched']=c.execute('''SELECT count(DISTINCT d.document_id) FROM attempt_documents d
            JOIN search_attempts a ON a.id=d.attempt_id WHERE a.task_id=?''',(task_id,)).fetchone()[0]
        data['relevant']=c.execute('''SELECT count(DISTINCT d.document_id) FROM attempt_documents d
            JOIN search_attempts a ON a.id=d.attempt_id JOIN extraction_outcomes x ON x.document_id=d.document_id
            WHERE a.task_id=? AND x.relevance='relevant' ''',(task_id,)).fetchone()[0]
    recent=task_search_feedback(store,task_id,limit=20)
    streak=0
    for row in recent:
        if row['outcome']=='EXTRACTION_PENDING':
            continue
        if row['outcome']!='ZERO_YIELD':
            break
        streak+=1
    data['zero_yield']=streak
    return data


def host_health(store, host):
    with store.db() as c:
        r=c.execute('SELECT * FROM host_health WHERE host=?',(host,)).fetchone()
    return dict(r) if r else {'host':host,'failures':0,'denied':0,'last_failure':0,'next_retry':0}


def host_in_cooldown(store, host, now_ts=None):
    return host_health(store,host)['next_retry']>(time.time() if now_ts is None else now_ts)


def record_host_failure(store, host, http_code, now_ts=None):
    stamp=time.time() if now_ts is None else now_ts
    old=host_health(store,host)
    denied=old['denied'] if stamp-old['last_failure']<=600 else 0
    if http_code in (403,451):
        denied+=1
    cooldown=stamp+900 if http_code in (403,451) and denied>=2 else old['next_retry']
    with store.db() as c:
        c.execute('INSERT OR REPLACE INTO host_health VALUES(?,?,?,?,?,?)',
                  (host,old['failures']+1,denied,http_code,stamp,cooldown))


def save_search_results(store, aid, rows):
    """Commit the entire result page with its ready marker, not a partial hit list."""
    with store.db() as c:
        for rank,hit,decision in rows:
            c.execute('INSERT OR IGNORE INTO search_hits(attempt_id,rank,url,title,snippet,host,eligible,score,reasons,status) '
                      'VALUES(?,?,?,?,?,?,?,?,?,?)',
                (aid,rank,hit['url'],hit['title'],hit['content'],decision.host,int(decision.accepted),
                 decision.score,json.dumps(decision.reasons),'CANDIDATE' if decision.accepted else 'REJECTED'))
        c.execute("UPDATE search_attempts SET status='HITS_READY',updated=? WHERE id=?",(now(),aid))
