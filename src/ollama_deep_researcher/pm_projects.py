"""Independent project databases and explicit, read-only reference selection.

There is no global evidence index. A reference is a candidate source, not an
accepted fact. The low-level Store remains usable for old standalone callers.
"""
from contextlib import contextmanager, nullcontext
import json
import hashlib
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

from .pm_store import Store, atomic_text, now, worker_lock

PROJECT_ID = re.compile(r'^[a-f0-9]{16}$')


def search_terms(query):
    """Coarse candidate search only; numbers alone never establish relevance."""
    stop = {'the', 'and', 'for', 'with', 'from', 'this', 'that', 'none'}
    return list(dict.fromkeys(t for t in re.findall(r'[^\W\d_]{2,}', str(query).casefold())
                              if t not in stop))[:12]


@contextmanager
def read_database(path):
    """Read existing SQLite including its WAL without creating a missing DB."""
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.projects_root = self.root / 'projects'
        self.projects_root.mkdir(exist_ok=True)
        if self.projects_root.is_symlink():
            raise ValueError('Project directory must not be a symbolic link')
        if (self.root / 'research.sqlite3').exists() and not (self.root / 'migration.json').exists():
            self._migrate_legacy()

    def project_path(self, pid):
        if not isinstance(pid, str) or not PROJECT_ID.fullmatch(pid):
            raise ValueError('Invalid project ID')
        folder = self.projects_root / pid
        if folder.is_symlink() or folder.resolve().parent != self.projects_root.resolve():
            raise ValueError('Project path escapes workspace')
        return folder

    def _db_path(self, pid):
        folder = self.project_path(pid)
        path = folder / 'project.sqlite3'
        if not path.is_file():
            raise KeyError('Unknown project: ' + pid)
        if path.is_symlink():
            raise ValueError('Project database must not be a symbolic link')
        return path

    def open(self, pid):
        path = self._db_path(pid)
        store = Store(path.parent, filename='project.sqlite3', project_id=pid,
                      read_only=self.load(pid).get('engine_version',4) < 5)
        store.workspace = self
        return store

    def load(self, pid):
        with read_database(self._db_path(pid)) as conn:
            row = conn.execute('SELECT state,control FROM projects WHERE id=?', (pid,)).fetchone()
        if row is None:
            raise KeyError('Project state missing: ' + pid)
        state = json.loads(row['state'])
        state['control'] = row['control']
        return state

    def save(self, state):
        self.open(state['id']).save(state)

    def control(self, pid, action):
        self.open(pid).control(pid, action)

    def export(self, pid):
        return self.open(pid).export(pid)

    def events(self, pid, limit=80):
        return self.open(pid).events(pid, limit)

    def projects(self):
        result = []
        for folder in self.projects_root.iterdir():
            if not PROJECT_ID.fullmatch(folder.name):
                continue
            # Corruption is reported, not silently represented as an empty project.
            state = self.load(folder.name)
            result.append(state)
        return sorted(result, key=lambda p: p.get('created', ''), reverse=True)

    def create(self, topic, settings, instructions='', reference_projects=()):
        from .pm_types import validate_research_input
        from .pm_budget import preflight_research_start
        validate_research_input(topic,instructions)
        preflight_research_start(settings,topic,instructions)
        if isinstance(reference_projects, (str, bytes)):
            raise ValueError('Reference projects must be a list of project IDs')
        refs = list(dict.fromkeys(reference_projects))
        if len(refs) > 20:
            raise ValueError('Select at most 20 reference projects')
        for ref in refs:
            self.load(ref)
        pid = uuid.uuid4().hex[:16]
        folder = self.project_path(pid)
        stage = self.projects_root / ('.new-' + pid)
        stage.mkdir()
        try:
            store = Store(stage, filename='project.sqlite3', project_id=pid)
            store.create(topic, settings, instructions)
            state = store.load(pid)
            state['reference_projects'] = refs
            state['schema_version'] = 3
            state['engine_version'] = 5
            from .pm_research_metrics import initialize
            initialize(store)
            store.save(state)
            stage.rename(folder)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        return pid

    def continue_as_v05(self, legacy_project_id, settings=None):
        from .pm_types import Settings
        original = self.load(legacy_project_id)
        if original.get('engine_version',4) >= 5:
            raise ValueError('This project is already v0.5; use its resume control')
        folder = self.project_path(legacy_project_id)
        # Do not create even a lock file in an untouched legacy directory.
        lock = worker_lock(folder) if (folder / 'worker.lock').exists() else nullcontext()
        with lock:
            cfg = settings or Settings.from_saved(original['settings'])
            return self.create(original['topic'],cfg,original.get('instructions',''),
                               reference_projects=[legacy_project_id])

    def reference_candidates(self, pid, query, limit=12):
        refs = self.load(pid).get('reference_projects', [])
        terms = search_terms(query)
        if not terms:
            return []
        result = []
        for ref in refs:
            # Do not traverse the reference project's own reference selection.
            with read_database(self._db_path(ref)) as conn:
                clauses = ' OR '.join('lower(title || " " || body) LIKE ? ESCAPE "\\"' for _ in terms)
                args = ['%' + t.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%' for t in terms]
                rows = conn.execute('SELECT id,url,title,body,content_hash FROM documents WHERE ' + clauses +
                                    ' ORDER BY collected DESC,id LIMIT ?', args + [min(40, max(1, limit))]).fetchall()
            for row in rows:
                result.append({'origin_project_id': ref, 'origin_document_id': row['id'],
                               'url': row['url'], 'title': row['title'],
                               'content_hash': row['content_hash'], 'status': 'CANDIDATE'})
            if len(result) >= limit:
                break
        return result[:max(1, limit)]

    def import_candidate(self, pid, candidate):
        state = self.load(pid)
        origin, did = candidate.get('origin_project_id'), candidate.get('origin_document_id')
        if origin == pid or origin not in state.get('reference_projects', []):
            raise ValueError('Reference project was not explicitly selected')
        with read_database(self._db_path(origin)) as conn:
            row = conn.execute('SELECT * FROM documents WHERE id=?', (did,)).fetchone()
        if row is None:
            raise KeyError('Reference document no longer exists')
        if candidate.get('content_hash') and candidate['content_hash'] != row['content_hash']:
            raise ValueError('Reference version changed; rediscover the candidate')
        metadata = json.loads(row['metadata']) if 'metadata' in row.keys() else {}
        if metadata.get('origin_project_id'):
            metadata['upstream_origin_project_id'] = metadata['origin_project_id']
            metadata['upstream_origin_document_id'] = metadata.get('origin_document_id')
        metadata.update(origin_project_id=origin, origin_document_id=did,
                        origin_content_hash=row['content_hash'], origin_collected=row['collected'],
                        relation='reference_candidate', imported_at=now())
        raw = None
        if metadata.get('raw_path'):
            origin_root = self.project_path(origin).resolve()
            raw_path = (origin_root / metadata['raw_path']).resolve()
            if not raw_path.is_relative_to(origin_root):
                raise ValueError('Reference original escapes its project')
            raw = raw_path.read_bytes()
            if metadata.get('version_hash') and hashlib.sha256(raw).hexdigest() != metadata['version_hash']:
                raise ValueError('Reference original hash mismatch')
        store = self.open(pid)
        imported = store.add_document(row['url'], row['title'], row['body'], metadata=metadata, raw=raw)
        store.log(pid, 'REFERENCE_CANDIDATE', json.dumps({'origin_project_id': origin,
                  'origin_document_id': did, 'document_id': imported}, ensure_ascii=False))
        return imported

    def _migrate_legacy(self):
        """Back up first, migrate only historical links, quarantine ambiguous data."""
        with worker_lock(self.root):
            if (self.root / 'migration.json').exists():
                return
            pending_path = self.root / 'migration.pending.json'
            if pending_path.exists():
                pending = json.loads(pending_path.read_text(encoding='utf-8'))
                backup = self.root / pending['backup']
                if backup.resolve().parent != (self.root / 'backups').resolve():
                    raise ValueError('Invalid legacy backup path')
            else:
                backup = self.root / 'backups' / ('legacy-' + uuid.uuid4().hex + '.sqlite3')
                backup.parent.mkdir(exist_ok=True)
                with read_database(self.root / 'research.sqlite3') as source:
                    dest = sqlite3.connect(backup)
                    try:
                        source.backup(dest)
                    finally:
                        dest.close()
                pending = {'backup': str(backup.relative_to(self.root)), 'started_at': now()}
                atomic_text(pending_path, json.dumps(pending, indent=2))
            assigned, migrated = set(), []
            with read_database(backup) as conn:
                states = [json.loads(row['state']) for row in conn.execute('SELECT state FROM projects')]
                all_docs = {row['id'] for row in conn.execute('SELECT id FROM documents')}
                for original in states:
                    pid = original['id']
                    folder = self.project_path(pid)
                    doc_ids, legacy_evidence = set(), []
                    for task in original.get('tasks', []):
                        doc_ids.update(task.get('document_ids', []))
                        for eid in task.get('evidence_ids', []):
                            row = conn.execute('SELECT * FROM evidence WHERE id=?', (eid,)).fetchone()
                            if row:
                                doc_ids.add(row['document_id'])
                                legacy_evidence.append(dict(row))
                    doc_ids &= all_docs
                    assigned.update(doc_ids)
                    if (folder / 'project.sqlite3').exists():
                        existing = self.load(pid)
                        if existing.get('legacy_backup') != pending['backup']:
                            raise ValueError('Migration would overwrite an existing project: ' + pid)
                        migrated.append(pid)
                        continue
                    stage = self.projects_root / ('.migrate-' + pid)
                    if stage.exists():
                        shutil.rmtree(stage)
                    try:
                        store = Store(stage, filename='project.sqlite3', project_id=pid)
                        state = json.loads(json.dumps(original))
                        state.update(reference_projects=[], schema_version=2, legacy_review_required=True,
                                     legacy_backup=pending['backup'], summary=None, errors=0, last_error='',
                                     status='PENDING', active=None, stage='select' if state.get('tasks') else 'plan')
                        state.pop('draft_sections', None)
                        for task in state.get('tasks', []):
                            linked = set(task.get('document_ids', []))
                            for eid in task.get('evidence_ids', []):
                                row = conn.execute('SELECT document_id FROM evidence WHERE id=?', (eid,)).fetchone()
                                if row:
                                    linked.add(row['document_id'])
                            task.update(evidence_ids=[], accepted_ids=[], attempts=0, status='PENDING',
                                        feedback=['Legacy candidate; original relevance must be checked again'],
                                        queries=[], document_ids=sorted(linked & doc_ids), document_index=0)
                            task.pop('review', None)
                        with store.db() as dest:
                            dest.execute('INSERT INTO projects VALUES(?,?,?,?)',
                                         (pid, json.dumps(state, ensure_ascii=False), 'PAUSE', now()))
                        id_map = {}
                        for did in sorted(doc_ids):
                            row = conn.execute('SELECT * FROM documents WHERE id=?', (did,)).fetchone()
                            meta = json.loads(row['metadata']) if 'metadata' in row.keys() else {}
                            meta.update(relation='legacy_candidate', legacy_document_id=did,
                                        legacy_review_required=True, origin_collected=row['collected'])
                            raw = None
                            if meta.get('raw_path'):
                                raw_path = (self.root / meta['raw_path']).resolve()
                                if not raw_path.is_relative_to(self.root):
                                    raise ValueError('Legacy original escapes workspace')
                                raw = raw_path.read_bytes()
                                if meta.get('version_hash') and hashlib.sha256(raw).hexdigest() != meta['version_hash']:
                                    raise ValueError('Legacy original hash mismatch')
                            id_map[did] = store.add_document(row['url'], row['title'], row['body'], metadata=meta, raw=raw)
                        for task in state.get('tasks', []):
                            task['document_ids'] = [id_map[d] for d in task['document_ids']]
                        store.save(state)
                        with store.db() as dest:
                            for event in conn.execute('SELECT project_id,time,kind,message FROM events WHERE project_id=?', (pid,)):
                                dest.execute('INSERT INTO events(project_id,time,kind,message) VALUES(?,?,?,?)', tuple(event))
                        atomic_text(stage / 'evidence' / 'legacy_state.json', json.dumps(original, ensure_ascii=False, indent=2))
                        atomic_text(stage / 'evidence' / 'legacy_evidence.json', json.dumps(legacy_evidence, ensure_ascii=False, indent=2))
                        # Preserve an old export as a whole before installing the complete DB.
                        # Moving entries individually could expose a half-migrated project.
                        archive = None
                        if folder.exists():
                            archive = self.root / 'backups' / ('exports-' + pid + '-' + uuid.uuid4().hex[:8])
                            folder.rename(archive)
                        try:
                            stage.rename(folder)
                        except BaseException:
                            if archive is not None and not folder.exists():
                                archive.rename(folder)
                            raise
                    except BaseException:
                        shutil.rmtree(stage, ignore_errors=True)
                        raise
                    migrated.append(pid)
            report = dict(pending, completed_at=now(), migrated_project_ids=migrated,
                          unassigned_document_ids=sorted(all_docs - assigned),
                          notice='Unassigned originals remain in the untouched legacy DB and backup. '
                                 'Historical evidence is archived, not automatically accepted.')
            atomic_text(self.root / 'migration.json', json.dumps(report, ensure_ascii=False, indent=2))
            pending_path.unlink(missing_ok=True)
