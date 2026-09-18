"""Local SQLite checkpoints and auditable evidence. No model owns database state."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid

from .pm_types import canonical_url, normalized


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.tmp-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def worker_lock(root):
    """OS lock released automatically after a process crash; one worker per DB."""
    path = Path(root) / 'worker.lock'
    with open(path, 'a+b') as f:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write(b'0')
            f.flush()
        f.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('Another PM worker is already using this database') from exc
        else:
            import fcntl
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError('Another PM worker is already using this database') from exc
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'research.sqlite3'
        with self.db() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
              CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, state TEXT NOT NULL,
                control TEXT NOT NULL DEFAULT 'RUN', updated TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, url TEXT NOT NULL,
                title TEXT NOT NULL, body TEXT NOT NULL, content_hash TEXT NOT NULL, collected TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS doc_url ON documents(url);
              CREATE INDEX IF NOT EXISTS doc_hash ON documents(content_hash);
              CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                claim_key TEXT NOT NULL, value_norm TEXT NOT NULL, data TEXT NOT NULL,
                search_text TEXT NOT NULL, FOREIGN KEY(document_id) REFERENCES documents(id));
              CREATE INDEX IF NOT EXISTS evidence_key ON evidence(claim_key);
              CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, project_id TEXT NOT NULL,
                time TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS event_project ON events(project_id,seq);
            ''')

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            with c:
                yield c
        finally:
            c.close()

    def create(self, topic, settings, instructions=''):
        if not isinstance(topic, str) or not topic.strip() or len(topic.encode('utf-8')) > 1000:
            raise ValueError('Research topic must be 1..1000 UTF-8 bytes')
        if len(instructions.encode('utf-8')) > 1200:
            raise ValueError('Additional instructions must fit 1200 UTF-8 bytes')
        pid = uuid.uuid4().hex[:16]
        state = {'id': pid, 'topic': topic.strip(), 'instructions': instructions,
                 'settings': settings.to_dict(), 'status': 'PENDING', 'stage': 'plan',
                 'tasks': [], 'active': None, 'calls': 0, 'searches': 0, 'errors': 0,
                 'created': now(), 'last_error': '', 'summary': None}
        with self.db() as c:
            c.execute('INSERT INTO projects VALUES(?,?,?,?)', (pid, json.dumps(state), 'RUN', now()))
        return pid

    def load(self, pid):
        with self.db() as c:
            row = c.execute('SELECT state,control FROM projects WHERE id=?', (pid,)).fetchone()
        if row is None:
            raise KeyError(f'Unknown project: {pid}')
        s = json.loads(row['state'])
        s['control'] = row['control']
        return s

    def save(self, state):
        data = {k: v for k, v in state.items() if k != 'control'}
        with self.db() as c:
            c.execute('UPDATE projects SET state=?,updated=? WHERE id=?',
                      (json.dumps(data, ensure_ascii=False), now(), state['id']))

    def control(self, pid, action):
        if action not in ('RUN', 'PAUSE', 'STOP'):
            raise ValueError('Unknown control action')
        with self.db() as c:
            c.execute('UPDATE projects SET control=?,updated=? WHERE id=?', (action, now(), pid))

    def projects(self):
        with self.db() as c:
            rows = c.execute('SELECT id,state FROM projects ORDER BY updated DESC LIMIT 100').fetchall()
        return [json.loads(r['state']) for r in rows]

    def log(self, pid, kind, message):
        with self.db() as c:
            c.execute('INSERT INTO events(project_id,time,kind,message) VALUES(?,?,?,?)',
                      (pid, now(), kind, str(message)[:5000]))

    def events(self, pid, limit=80):
        with self.db() as c:
            rows = c.execute('SELECT * FROM events WHERE project_id=? ORDER BY seq DESC LIMIT ?',
                             (pid, min(500, max(1, limit)))).fetchall()
        return [dict(r) for r in reversed(rows)]

    def add_document(self, url, title, body):
        url = canonical_url(url)
        if not isinstance(body, str) or not body.strip() or len(body) > 200000:
            raise ValueError('Source text missing or exceeds 200000 characters')
        sha = digest(normalized(body))
        did = 'd-' + digest(url + '\n' + sha)[:24]
        with self.db() as c:
            c.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?)',
                      (did, url, str(title)[:500], body, sha, now()))
        return did

    def document(self, did):
        with self.db() as c:
            row = c.execute('SELECT * FROM documents WHERE id=?', (did,)).fetchone()
        if row is None:
            raise KeyError('Unknown document')
        return dict(row)

    def add_evidence(self, did, claim):
        if not isinstance(claim, dict):
            raise ValueError('Claim must be an object')
        names = ['entity', 'metric', 'value', 'unit', 'period', 'scope', 'quote']
        clean = {}
        for key in names:
            value = claim.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > (1200 if key == 'quote' else 300):
                raise ValueError(f'Invalid claim field: {key}')
            clean[key] = normalized(value)
        doc = self.document(did)
        if len(clean['quote']) < 12 or clean['quote'] not in normalized(doc['body']):
            raise ValueError('Quotation is absent from the fetched source text')
        key = digest(json.dumps([clean[k].casefold() for k in ('entity', 'metric', 'unit', 'period', 'scope')]))
        eid = 'e-' + digest(did + json.dumps(clean, sort_keys=True, ensure_ascii=False))[:24]
        clean['status'] = 'QUOTE_CHECKED'
        with self.db() as c:
            c.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?)',
                      (eid, did, key, clean['value'].casefold(), json.dumps(clean, ensure_ascii=False),
                       ' '.join(clean.values()).casefold()))
        return eid

    def get_evidence(self, ids):
        ids = list(dict.fromkeys(ids))
        if not ids:
            return []
        rows = []
        with self.db() as c:
            for offset in range(0, len(ids), 200):
                batch = ids[offset:offset + 200]
                placeholders = ','.join('?' for _ in batch)
                rows.extend(c.execute(f'''SELECT e.*,d.url,d.title,d.content_hash,d.collected,
                    (SELECT COUNT(DISTINCT x.value_norm) FROM evidence x WHERE x.claim_key=e.claim_key)>1 AS conflict
                    FROM evidence e JOIN documents d ON e.document_id=d.id WHERE e.id IN ({placeholders})''', batch).fetchall())
        result = []
        for row in rows:
            item = json.loads(row['data'])
            item.update({k: row[k] for k in ('id', 'document_id', 'url', 'title', 'content_hash', 'collected', 'conflict')})
            item['conflict'] = bool(item['conflict'])
            result.append(item)
        return result

    def retrieve(self, query, limit=12):
        terms = list(dict.fromkeys(re.findall(r'\w+', query.casefold())))[:8]
        if not terms:
            return []
        clauses = ' OR '.join('search_text LIKE ?' for _ in terms)
        with self.db() as c:
            rows = c.execute(f'SELECT id FROM evidence WHERE {clauses} ORDER BY rowid DESC LIMIT ?',
                             [f'%{t}%' for t in terms] + [min(40, max(1, limit))]).fetchall()
        return self.get_evidence([r['id'] for r in rows])

    def counts(self):
        with self.db() as c:
            return {name: c.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                    for name in ('documents', 'evidence')}

    def export(self, pid):
        s = self.load(pid)
        folder = self.root / 'projects' / pid
        ids = [eid for task in s['tasks'] for eid in task.get('evidence_ids', [])]
        evidence = self.get_evidence(ids)
        sources = [self.document(did) for did in sorted({e['document_id'] for e in evidence})]
        pack = {'schema_version': 1, 'generated_at': now(), 'project': s, 'evidence': evidence,
                'sources': sources, 'notice': 'AI-assisted draft. Quote matching and critic approval are not proof of truth.'}
        atomic_text(folder / 'evidence_pack.json', json.dumps(pack, ensure_ascii=False, indent=2))
        lines = ['# Research PM progress', '', 'Human review required. No claim of graduate-researcher equivalence.',
                 '', f"Topic: {s['topic']}", f"Status: {s['status']} / stage: {s['stage']}",
                 f"Generated: {now()}", f"LLM calls: {s['calls']} | Searches: {s['searches']}", '']
        for t in s['tasks']:
            lines += [f"## {t['id']} - {t['title']} [{t['status']}]", f"Attempts: {t.get('attempts', 0)}"]
            lines += ['- ' + c['id'] + ': ' + c['text'] for c in t['criteria']]
            lines += ['- Gap: ' + str(x) for x in t.get('feedback', [])]
            lines += ['Evidence IDs: ' + ', '.join(t.get('evidence_ids', [])), '']
        if s.get('last_error'):
            lines += ['## Last error', s['last_error'], '']
        if s.get('summary'):
            lines += ['## AI synthesis draft', str(s['summary'].get('summary', '')),
                      'Evidence IDs: ' + ', '.join(s['summary'].get('evidence_ids', [])),
                      'Limitations: ' + str(s['summary'].get('limitations', [])), '']
        for e in evidence:
            lines += [f"### {e['id']} - {'CONFLICT' if e['conflict'] else e['status']}",
                      f"{e['entity']} | {e['metric']} | {e['value']} {e['unit']} | {e['scope']} | {e['period']}",
                      f"Source: {e['title']} ({e['url']})", f"Collected: {e['collected']}",
                      f"Quote: {e['quote']}", '']
        atomic_text(folder / 'progress.md', '\n'.join(lines))
        return folder
