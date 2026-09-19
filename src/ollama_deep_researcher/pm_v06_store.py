"""v0.6-only retrieval checkpoints. Original document/evidence tables stay intact."""
import json
from .pm_store import now


def initialize(store):
    with store.db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS retrieval_plans_v06(
          document_id TEXT PRIMARY KEY REFERENCES documents(id), reuse_key TEXT NOT NULL,
          status TEXT NOT NULL, alias_of TEXT, unread_chars INTEGER NOT NULL, data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS retrieval_reuse ON retrieval_plans_v06(reuse_key);
        CREATE TABLE IF NOT EXISTS retrieval_decisions_v06(
          seq INTEGER PRIMARY KEY, document_id TEXT, kind TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reference_decisions_v06(
          origin_project_id TEXT NOT NULL, origin_document_id TEXT NOT NULL,
          url TEXT NOT NULL, score REAL NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL,
          PRIMARY KEY(origin_project_id,origin_document_id));
        CREATE TABLE IF NOT EXISTS search_intents_v06(
          attempt_id TEXT PRIMARY KEY REFERENCES search_attempts(id), data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS search_excerpts_v06(
          excerpt_key TEXT NOT NULL, attempt_id TEXT NOT NULL, rank INTEGER NOT NULL,
          PRIMARY KEY(excerpt_key,attempt_id,rank));
        ''')


def get_plan(store, did):
    with store.db() as c:
        row=c.execute('SELECT data FROM retrieval_plans_v06 WHERE document_id=?',(did,)).fetchone()
    return json.loads(row[0]) if row else None


def plans(store):
    with store.db() as c:
        return [json.loads(r[0]) for r in c.execute('SELECT data FROM retrieval_plans_v06 ORDER BY rowid')]


def save_plan(store, plan):
    with store.db() as c:
        c.execute('INSERT OR REPLACE INTO retrieval_plans_v06 VALUES(?,?,?,?,?,?)',
                  (plan['document_id'],plan['reuse_key'],plan['status'],plan.get('alias_of'),
                   plan['unread_chars'],json.dumps(plan,ensure_ascii=False)))


def alias_candidate(store, key, did):
    with store.db() as c:
        r=c.execute('SELECT document_id FROM retrieval_plans_v06 WHERE reuse_key=? AND document_id!=? '
                    'AND alias_of IS NULL ORDER BY rowid LIMIT 1',(key,did)).fetchone()
    return r[0] if r else None


def record_decision(store, kind, did=None, **data):
    with store.db() as c:
        c.execute('INSERT INTO retrieval_decisions_v06(document_id,kind,data,created) VALUES(?,?,?,?)',
                  (did,kind,json.dumps(data,ensure_ascii=False),now()))


def decisions(store):
    with store.db() as c:
        return [dict(r, data=json.loads(r['data'])) for r in c.execute('SELECT * FROM retrieval_decisions_v06 ORDER BY seq')]


def reference_rows(store):
    with store.db() as c:
        return [dict(r,data=json.loads(r['data'])) for r in c.execute('SELECT * FROM reference_decisions_v06')]


def record_reference(store, candidate, status):
    with store.db() as c:
        c.execute('INSERT OR REPLACE INTO reference_decisions_v06 VALUES(?,?,?,?,?,?)',
                  (candidate['origin_project_id'],candidate['origin_document_id'],candidate['url'],
                   candidate['score'],status,json.dumps(candidate,ensure_ascii=False)))


def save_intent(store, aid, data):
    with store.db() as c:
        c.execute('INSERT OR REPLACE INTO search_intents_v06 VALUES(?,?)',(aid,json.dumps(data,ensure_ascii=False)))


def project_counters(store):
    with store.db() as c:
        return dict(c.execute('''SELECT count(*) AS ranked_documents,
          coalesce(sum(status='DEFERRED'),0) AS deferred_documents,
          coalesce(sum(alias_of IS NOT NULL),0) AS duplicate_bodies_avoided,
          coalesce(sum(CASE WHEN alias_of IS NULL THEN unread_chars ELSE 0 END),0) AS unread_characters
          FROM retrieval_plans_v06''').fetchone())
