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


def atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.tmp-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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
    def __init__(self, root, filename="research.sqlite3", project_id=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / filename
        self.project_id = project_id
        for name in ('documents', 'evidence', 'logs', 'handoff'):
            (self.root / name).mkdir(exist_ok=True)
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
              CREATE TABLE IF NOT EXISTS sources(url TEXT PRIMARY KEY, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS processing(work_key TEXT PRIMARY KEY, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS document_tasks(document_id TEXT NOT NULL, task_id TEXT NOT NULL,
                status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(document_id,task_id), FOREIGN KEY(document_id) REFERENCES documents(id));
            ''')

            columns = {r[1] for r in c.execute('PRAGMA table_info(documents)')}
            if 'metadata' not in columns:
                c.execute("ALTER TABLE documents ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")

    def _bound(self, pid):
        if self.project_id is not None and pid != self.project_id:
            raise KeyError('Project does not belong to this database')

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
        from .pm_types import validate_research_input
        validate_research_input(topic, instructions)
        if self.project_id is not None and self.projects():
            raise ValueError('An isolated database can contain only one project')
        pid = self.project_id or uuid.uuid4().hex[:16]
        state = {'id': pid, 'topic': topic.strip(), 'instructions': instructions,
                 'settings': settings.to_dict(), 'status': 'PENDING', 'stage': 'plan',
                 'tasks': [], 'active': None, 'calls': 0, 'searches': 0, 'errors': 0,
                 'created': now(), 'last_error': '', 'summary': None}
        with self.db() as c:
            c.execute('INSERT INTO projects VALUES(?,?,?,?)', (pid, json.dumps(state), 'RUN', now()))
        return pid

    def load(self, pid):
        self._bound(pid)
        with self.db() as c:
            row = c.execute('SELECT state,control FROM projects WHERE id=?', (pid,)).fetchone()
        if row is None:
            raise KeyError(f'Unknown project: {pid}')
        s = json.loads(row['state'])
        s['control'] = row['control']
        return s

    def save(self, state):
        self._bound(state['id'])
        data = {k: v for k, v in state.items() if k != 'control'}
        with self.db() as c:
            c.execute('UPDATE projects SET state=?,updated=? WHERE id=?',
                      (json.dumps(data, ensure_ascii=False), now(), state['id']))

    def control(self, pid, action):
        self._bound(pid)
        if action not in ('RUN', 'PAUSE', 'STOP'):
            raise ValueError('Unknown control action')
        with self.db() as c:
            c.execute('UPDATE projects SET control=?,updated=? WHERE id=?', (action, now(), pid))

    def projects(self):
        with self.db() as c:
            rows = c.execute('SELECT id,state FROM projects ORDER BY updated DESC LIMIT 100').fetchall()
        return [json.loads(r['state']) for r in rows]

    def log(self, pid, kind, message):
        self._bound(pid)
        with self.db() as c:
            c.execute('INSERT INTO events(project_id,time,kind,message) VALUES(?,?,?,?)',
                      (pid, now(), kind, str(message)[:5000]))

    def events(self, pid, limit=80):
        self._bound(pid)
        with self.db() as c:
            rows = c.execute('SELECT * FROM events WHERE project_id=? ORDER BY seq DESC LIMIT ?',
                             (pid, min(500, max(1, limit)))).fetchall()
        return [dict(r) for r in reversed(rows)]

    def add_document(self, url, title, body, metadata=None, raw=None):
        url = canonical_url(url)
        if not isinstance(body, str):
            raise ValueError('Source body must be text')
        if len(body) > 2_000_000:
            raise ValueError('Source text exceeds 2000000-character storage limit')
        if not body.strip() and raw is None:
            raise ValueError('Source body is empty and no original bytes were supplied')
        if raw is not None and (not isinstance(raw, bytes) or len(raw) > 20_000_000):
            raise ValueError('Original source must be bytes within the 20 MB storage limit')
        sha = digest(normalized(body))
        original = raw if raw is not None else body.encode('utf-8')
        version_hash = hashlib.sha256(original).hexdigest()
        did = 'd-' + digest(url + '\n' + version_hash + '\n' + digest(body))[:24]
        meta = dict(metadata or {})
        meta.update(version_hash=version_hash, text_hash=digest(body), byte_count=len(original))
        kind = meta.get('content_type', 'text/plain')
        extension = {'application/pdf': '.pdf', 'text/html': '.html',
                     'application/xhtml+xml': '.html', 'text/plain': '.txt'}.get(kind, '.bin')
        raw_path = Path('documents') / did / ('original' + extension)
        text_path = Path('documents') / did / 'text.txt'
        meta.update(raw_path=raw_path.as_posix(), text_path=text_path.as_posix())
        atomic_bytes(self.root / raw_path, original)
        atomic_text(self.root / text_path, body)
        with self.db() as c:
            c.execute('INSERT OR IGNORE INTO documents(id,url,title,body,content_hash,collected,metadata) VALUES(?,?,?,?,?,?,?)',
                      (did, url, str(title)[:500], body, sha, now(), json.dumps(meta, ensure_ascii=False)))
        return did

    def raw_document(self, did):
        meta = self.document(did)['metadata']
        relative = meta.get('raw_path')
        if not relative:
            return None
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or path.is_symlink():
            raise ValueError('Original path escapes project directory')
        return path.read_bytes()

    def all_documents(self):
        with self.db() as c:
            ids = [row[0] for row in c.execute('SELECT id FROM documents ORDER BY collected,id')]
        return [self.document(did) for did in ids]

    def link_document(self, did, task_id, status='CANDIDATE', reason=''):
        allowed = {'CANDIDATE', 'RELEVANT', 'UNCONFIRMED', 'EXCLUDED'}
        if status not in allowed:
            raise ValueError('Invalid document relevance status')
        with self.db() as c:
            previous = c.execute('SELECT status FROM document_tasks WHERE document_id=? AND task_id=?',
                                 (did, task_id)).fetchone()
            # One irrelevant paragraph does not overrule another relevant paragraph.
            if previous and (previous[0] == 'RELEVANT' or status == 'CANDIDATE'):
                status = previous[0]
            c.execute('INSERT OR REPLACE INTO document_tasks VALUES(?,?,?,?)', (did, task_id, status, str(reason)[:1000]))

    def document_links(self):
        with self.db() as c:
            return [dict(row) for row in c.execute('SELECT * FROM document_tasks')]

    def record_source(self, url, status, task_id='', query='', **fields):
        try:
            key = canonical_url(url)
        except ValueError:
            key = str(url)[:4096]
        with self.db() as c:
            row = c.execute('SELECT data FROM sources WHERE url=?', (key,)).fetchone()
            data = json.loads(row[0]) if row else {'url': key, 'original_url': str(url),
                                                'tasks': [], 'queries': [], 'discovered_at': now()}
            for name, value in (('tasks', task_id), ('queries', query)):
                if value and value not in data[name]:
                    data[name].append(value)
            data.update(fields)
            data.update(status=status, updated_at=now())
            c.execute('INSERT OR REPLACE INTO sources VALUES(?,?)', (key, json.dumps(data, ensure_ascii=False)))
        return data

    def sources(self):
        with self.db() as c:
            return [json.loads(row[0]) for row in c.execute('SELECT data FROM sources ORDER BY url')]

    def source(self, url):
        key = canonical_url(url)
        with self.db() as c:
            row = c.execute('SELECT data FROM sources WHERE url=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def work_key(self, did, question, start, end, version='extract-v2'):
        doc = self.document(did)
        return digest(json.dumps([did, doc['metadata'].get('version_hash', doc['content_hash']),
                                  question, start, end, version], ensure_ascii=False))

    def work(self, key):
        with self.db() as c:
            row = c.execute('SELECT data FROM processing WHERE work_key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def record_work(self, key, status, **data):
        record = dict(data, key=key, status=status, updated_at=now())
        with self.db() as c:
            c.execute('INSERT OR REPLACE INTO processing VALUES(?,?)', (key, json.dumps(record, ensure_ascii=False)))
        return record

    def processing(self):
        with self.db() as c:
            return [json.loads(row[0]) for row in c.execute('SELECT data FROM processing')]

    def document(self, did):
        with self.db() as c:
            row = c.execute('SELECT * FROM documents WHERE id=?', (did,)).fetchone()
        if row is None:
            raise KeyError('Unknown document')
        result = dict(row)
        result['metadata'] = json.loads(result.get('metadata') or '{}')
        return result

    def add_evidence(self, did, claim, source_range=None):
        if not isinstance(claim, dict):
            raise ValueError('Claim must be an object')
        clean = {}
        for key in ('entity', 'subentity', 'metric', 'value', 'unit', 'period', 'scope', 'claim_text', 'quote'):
            value = claim.get(key, '')
            if value is None:
                value = ''
            if not isinstance(value, str) or len(value) > (1600 if key in ('quote', 'claim_text') else 400):
                raise ValueError('Invalid claim field: ' + key)
            clean[key] = value.strip() if key == 'quote' else normalized(value)
        if not all(clean[k] for k in ('entity', 'metric', 'quote')) or not (clean['value'] or clean['claim_text']):
            raise ValueError('Entity, metric, quote and a value or narrative claim are required')
        doc = self.document(did)
        lower, upper = (0, len(doc['body'])) if source_range is None else source_range
        if not (0 <= lower <= upper <= len(doc['body'])):
            raise ValueError('Invalid source range')
        if len(clean['quote']) < 12:
            raise ValueError('Quotation is too short to retain context')
        pattern = r'\s+'.join(re.escape(word) for word in clean['quote'].split())
        match = re.compile(pattern).search(doc['body'], lower, upper)
        if match is None:
            raise ValueError('Quotation is absent from the fetched source text/range')
        clean['location'] = {'start': match.start(), 'end': match.end(), 'pages': [
            p['page'] for p in doc['metadata'].get('pages', [])
            if p['start'] < match.end() and p['end'] > match.start()]}
        identity = [clean[k].casefold() for k in ('entity', 'subentity', 'metric', 'unit', 'period', 'scope')]
        unknown = {'', 'unknown', 'unspecified', 'not stated', 'n/a', 'none', '\ubbf8\ud655\uc778'}
        complete = all(v not in unknown for v in identity)
        clean['comparison_status'] = 'COMPARABLE' if complete else 'CONDITIONS_UNCONFIRMED'
        clean['status'], clean['review_status'] = 'QUOTE_CHECKED', 'REVIEW_PENDING'
        clean['document_version'] = doc['metadata'].get('version_hash', doc['content_hash'])
        clean['origin_project_id'] = doc['metadata'].get('origin_project_id', self.project_id)
        key = digest(json.dumps(identity))
        if not complete:
            key = digest(key + did + clean['value'] + clean['claim_text'])
        eid = 'e-' + digest(did + json.dumps(clean, sort_keys=True, ensure_ascii=False))[:24]
        value_norm = (clean['value'] or clean['claim_text']).casefold()
        with self.db() as c:
            c.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?)',
                      (eid, did, key, value_norm, json.dumps(clean, ensure_ascii=False),
                       ' '.join(str(v) for v in clean.values()).casefold()))
        return eid

    def review_evidence(self, eid, status, reason=''):
        allowed = {'REVIEW_PENDING', 'REVIEWED_SUPPORT', 'NEEDS_REVIEW',
                   'RELEVANCE_DISPUTED', 'REVIEW_INCOMPLETE'}
        if status not in allowed:
            raise ValueError('Unknown evidence review status')
        with self.db() as c:
            row = c.execute('SELECT data FROM evidence WHERE id=?', (eid,)).fetchone()
            if row is None:
                raise KeyError('Unknown evidence ID')
            data = json.loads(row[0])
            data.update(review_status=status, review_reason=str(reason)[:1200], reviewed_at=now())
            c.execute('UPDATE evidence SET data=? WHERE id=?', (json.dumps(data, ensure_ascii=False), eid))

    def all_evidence(self):
        with self.db() as c:
            ids = [row[0] for row in c.execute('SELECT id FROM evidence ORDER BY rowid')]
        return self.get_evidence(ids)

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
            item['conflict'] = bool(item['conflict']) and item.get('comparison_status') == 'COMPARABLE'
            if item['conflict']:
                item['comparison_status'] = 'CONFLICT_CANDIDATE'
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
        self._bound(pid)
        from .pm_export import export_project
        return export_project(self, pid)
