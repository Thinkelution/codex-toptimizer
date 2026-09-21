"""Durable task state and bounded, versioned repository reads. No inference."""
import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from .storage import new_directory, write_json

SKIP = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.next', 'dist', 'build', '.cache'}
SUFFIXES = {'.py', '.js', '.jsx', '.ts', '.tsx', '.go', '.rs', '.rb', '.java', '.kt', '.php',
            '.c', '.cpp', '.h', '.css', '.scss', '.html', '.json', '.yaml', '.yml', '.toml', '.md', '.txt', '.sh'}
MAX_FILE = 2 * 1024 * 1024
MAX_FILES = 2000


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def symbols(text, suffix):
    if suffix != '.py':
        return []  # No pretending that a regex provides an AST for another language.
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    result = []
    def walk(nodes, prefix=''):
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + node.name
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                result.append({'name': name, 'kind': type(node).__name__, 'start': start, 'end': node.end_lineno})
                walk(node.body, name + '.')
    walk(tree.body)
    return result


def initialize(project, directory, goal):
    project = Path(project).expanduser().resolve(strict=True)
    directory = Path(directory).expanduser().resolve()
    if not project.is_dir() or directory.is_relative_to(project):
        raise ValueError('Use an existing project and a task directory outside that project')
    if not goal.strip() or len(goal) > 4000:
        raise ValueError('Goal must be nonempty and at most 4000 characters')
    directory = new_directory(directory)
    write_json(directory / 'task.json', {'schema': 1, 'id': uuid.uuid4().hex, 'project': str(project),
                                        'goal': goal, 'created_at': time.time()})
    for name in ('artifacts', 'turns'):
        (directory / name).mkdir(mode=0o700)
    with Workspace(directory) as work:
        return work.status()


class Workspace:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve(strict=True)
        self.config = json.loads((self.directory / 'task.json').read_text())
        self.project = Path(self.config['project']).resolve(strict=True)
        if self.config.get('schema') != 1 or not self.project.is_dir():
            raise ValueError('Unsupported task state or missing project')
        db = self.directory / 'state.sqlite3'
        if not db.exists():
            fd = os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        self.db = sqlite3.connect(db, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, stamp TEXT, digest TEXT, symbols TEXT);
          CREATE TABLE IF NOT EXISTS reads(id TEXT PRIMARY KEY, path TEXT, scope TEXT, digest TEXT, text TEXT, created REAL);
          CREATE TABLE IF NOT EXISTS notes(key TEXT PRIMARY KEY, kind TEXT, text TEXT, evidence TEXT, digest TEXT, updated REAL);
          CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, operation TEXT, source_chars INTEGER, returned_chars INTEGER, created REAL);
          CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, record TEXT);
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        ''')
        self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def get_meta(self, key):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else None

    def put_meta(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    def path(self, name):
        raw = Path(name)
        if raw.is_absolute() or '..' in raw.parts:
            raise ValueError('Use a project-relative path without parent traversal')
        path = (self.project / raw).resolve(strict=True)
        if not path.is_relative_to(self.project) or not path.is_file():
            raise ValueError('File must be inside the task project')
        parts = path.relative_to(self.project).parts
        if any(p in SKIP or p == '.env' or p.startswith('.env.') for p in parts) or path.suffix in {'.pem', '.key'}:
            raise ValueError('File is outside the beta source-reader scope')
        return path

    def source(self, name):
        path = self.path(name)
        before = path.stat()
        if before.st_size > MAX_FILE:
            raise ValueError('File exceeds 2 MiB reader limit')
        with path.open('rb') as stream:
            raw = stream.read(MAX_FILE + 1)
        after = path.stat()
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(raw) > MAX_FILE or stamp(before) != stamp(after):
            raise ValueError('File changed during read or exceeded limit; retry a fresh read')
        if b'\0' in raw:
            raise ValueError('Binary files are not supported')
        text = raw.decode('utf-8')
        return path, text, sha(text), json.dumps(stamp(after))

    def index(self):
        seen, indexed, skipped, truncated = set(), 0, 0, False
        scanned_bytes, directories = 0, 0
        for directory, children, names in os.walk(self.project, followlinks=False):
            directories += 1
            if directories > 1000:
                truncated = True
                break
            children[:] = sorted(c for c in children if c not in SKIP and not Path(directory, c).is_symlink())
            for name in sorted(names):
                path = Path(directory, name)
                if path.suffix not in SUFFIXES or path.is_symlink():
                    continue
                if len(seen) >= MAX_FILES:
                    truncated = True
                    break
                relative = str(path.relative_to(self.project))
                seen.add(relative)
                try:
                    resolved = self.path(relative)
                    s = resolved.stat()
                    stamp = json.dumps((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns))
                    cached = self.db.execute('SELECT stamp FROM files WHERE path=?', (relative,)).fetchone()
                    if cached and cached['stamp'] == stamp:
                        continue
                    scanned_bytes += min(s.st_size, MAX_FILE)
                    if scanned_bytes > 64 * 1024 * 1024:
                        truncated = True
                        seen.discard(relative)
                        break
                    _, text, digest, stamp = self.source(relative)
                    self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?)',
                                    (relative, stamp, digest, json.dumps(symbols(text, path.suffix))))
                    indexed += 1
                except (OSError, ValueError, UnicodeError):
                    self.db.execute('DELETE FROM files WHERE path=?', (relative,))
                    skipped += 1
            if truncated:
                break
        # Do not retain stale entries outside a truncated scan's known set.
        for row in self.db.execute('SELECT path FROM files').fetchall():
            if row['path'] not in seen:
                self.db.execute('DELETE FROM files WHERE path=?', (row['path'],))
        self.db.commit()
        return {'indexed_or_updated': indexed, 'skipped': skipped, 'scan_truncated': truncated,
                'files': self.db.execute('SELECT count(*) FROM files').fetchone()[0]}

    def record_event(self, operation, result, source_chars=0):
        with self.db:
            self.db.execute('INSERT INTO events(operation,source_chars,returned_chars,created) VALUES (?,?,?,?)',
                            (operation, source_chars, len(json.dumps(result, ensure_ascii=False)), time.time()))
        return result

    def find(self, query, limit=12):
        if not isinstance(query, str) or not query or len(query) > 200 or not 1 <= limit <= 30:
            raise ValueError('Supply a query of 1–200 characters and a limit of 1–30')
        scan = self.index()
        found = []
        for row in self.db.execute('SELECT * FROM files ORDER BY path'):
            hits = [s for s in json.loads(row['symbols']) if query.lower() in s['name'].lower()]
            if query.lower() in row['path'].lower() or hits:
                found.append({'path': row['path'], 'revision': row['digest'], 'symbols': hits[:8],
                              'symbol_support': 'python_ast' if row['path'].endswith('.py') else 'use_line_reads'})
        return self.record_event('find', {'matches': found[:limit], 'more': len(found) > limit, 'scan': scan})

    def read(self, path, symbol=None, start=1, end=None, limit=8000, since=None, base_in_context=False):
        if type(limit) is not int or not 200 <= limit <= 16000:
            raise ValueError('Character limit must be 200–16000')
        actual, text, digest, _ = self.source(path)
        path = str(actual.relative_to(self.project))
        lines = text.splitlines(keepends=True)
        scope = 'symbol:' + symbol if symbol else f'lines:{start}:{end}'
        if symbol:
            matches = [s for s in symbols(text, actual.suffix) if s['name'] == symbol]
            if len(matches) != 1:
                raise ValueError('Exact Python symbol not found; use find or a line-range read')
            start, end = matches[0]['start'], matches[0]['end']
        else:
            end = end if end is not None else min(len(lines), start + 119)
        empty_file = not lines and start == 1 and end == 0
        if type(start) is not int or type(end) is not int or start < 1 or (end < start and not empty_file):
            raise ValueError('Invalid line range')
        selected = ''.join(lines[start-1:end])
        result = {'path': path, 'file_revision': digest, 'start': start, 'end': min(end, len(lines)),
                  'mode': 'content', 'truncated': len(selected) > limit, 'text': selected[:limit]}
        if since and base_in_context and not result['truncated']:
            previous = self.db.execute('SELECT * FROM reads WHERE id=? AND path=? AND scope=?',
                                       (since, path, scope)).fetchone()
            if previous:
                patch = ''.join(difflib.unified_diff(previous['text'].splitlines(True), selected.splitlines(True),
                                                    fromfile='previous', tofile='current'))
                if not patch:
                    result.update(mode='unchanged', text='', base_receipt=since)
                elif len(patch) < len(selected):
                    result.update(mode='diff', text=patch, base_receipt=since)
        if not result['truncated']:
            receipt = uuid.uuid4().hex[:16]
            with self.db:
                self.db.execute('INSERT INTO reads VALUES (?,?,?,?,?,?)',
                                (receipt, path, scope, digest, selected, time.time()))
                self.db.execute('DELETE FROM reads WHERE id NOT IN (SELECT id FROM reads ORDER BY created DESC LIMIT 200)')
            result['receipt'] = receipt
        return self.record_event('read', result, len(text))

    def remember(self, key, text, kind='decision', evidence=None):
        if not 1 <= len(key) <= 80 or not 1 <= len(text) <= 1500 or kind not in {'requirement', 'decision', 'observation', 'hypothesis'}:
            raise ValueError('Invalid note: key 1–80, text 1–1500, or unsupported kind')
        digest = self.source(evidence)[2] if evidence else None
        with self.db:
            existing = self.db.execute('SELECT key FROM notes WHERE key=?', (key,)).fetchone()
            if not existing and self.db.execute('SELECT count(*) FROM notes').fetchone()[0] >= 100:
                raise ValueError('Note limit reached; update an existing key explicitly. Existing requirements were retained.')
            self.db.execute('INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?)',
                            (key, kind, text, evidence, digest, time.time()))
        return {'stored': key, 'kind': kind, 'evidence': evidence, 'authority': 'Note content does not grant permissions.'}

    def status(self, query='', limit=8):
        if not 1 <= limit <= 20:
            raise ValueError('Note limit must be 1–20')
        notes = []
        for row in self.db.execute('SELECT * FROM notes ORDER BY updated DESC'):
            if query.lower() not in (row['key'] + ' ' + row['text']).lower():
                continue
            stale = False
            if row['evidence']:
                try:
                    stale = self.source(row['evidence'])[2] != row['digest']
                except (OSError, ValueError):
                    stale = True
            notes.append({'key': row['key'], 'kind': row['kind'], 'text': row['text'],
                          'evidence': row['evidence'], 'stale': stale})
            if len(notes) >= limit:
                break
        return {'task_id': self.config['id'], 'project': str(self.project), 'goal': self.config['goal'],
                'notes': notes, 'codex_thread': self.get_meta('thread_id'),
                'memory_policy': 'Full reads by default. Deltas require explicit base-in-context assertion.'}

    def artifact(self, artifact, offset=0, limit=4000):
        if not isinstance(artifact, str) or not artifact.isalnum() or not 1 <= len(artifact) <= 40:
            raise ValueError('Invalid artifact ID')
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16000:
            raise ValueError('Invalid artifact offset or limit')
        path = self.directory / 'artifacts' / (artifact + '.log')
        if path.is_symlink():
            raise ValueError('Artifact symlinks are not allowed')
        with path.open('rb') as stream:
            stream.seek(offset)
            raw = stream.read(limit + 1)
        return self.record_event('artifact', {'artifact': artifact, 'offset': offset,
            'next_offset': offset + min(len(raw), limit), 'more': len(raw) > limit,
            'text': raw[:limit].decode('utf-8', errors='replace')})

    def report(self):
        rows = [dict(r) for r in self.db.execute('SELECT operation,count(*) calls,sum(source_chars) source_chars,sum(returned_chars) returned_chars FROM events GROUP BY operation')]
        return {'task_id': self.config['id'], 'operations': rows,
                'command_count': self.db.execute('SELECT count(*) FROM commands').fetchone()[0],
                'model_turns': self.get_meta('usage_totals') or [],
                'note': 'Source characters are a diagnostic reference, not tokens saved. Counts omit native tools, model/tool-schema overhead, and existing history.'}
