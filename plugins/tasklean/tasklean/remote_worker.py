"""Standalone SSH-side, task-scoped dataset worker. Standard library only.

No TCP listener, arbitrary code evaluation, or implicit command execution.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

MAX_SOURCE = 4 * 1024 * 1024
MAX_REQUEST = 32 * 1024
MAX_REPLY = 256 * 1024
MAX_ROWS = 50_000
NAME = re.compile(r'^[a-zA-Z0-9_-]{1,48}$')


def size_of(value):
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(size_of(k) + size_of(v) for k, v in value.items())
    elif isinstance(value, list):
        size += sum(size_of(v) for v in value)
    return size


def version(path):
    s = path.stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


class DatasetCache:
    def __init__(self, root, max_bytes=64 * 1024 * 1024):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('Dataset root must be a directory')
        self.max_bytes = max_bytes
        self.datasets = {}
        self.parses = 0
        self.hits = 0

    def path(self, raw):
        path = (self.root / raw).resolve(strict=True)
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError('Dataset must be a regular file inside the configured root')
        return path

    def used(self):
        return sum(d['memory_bytes'] for d in self.datasets.values())

    def get(self, name):
        if name not in self.datasets:
            raise ValueError('Dataset not loaded in this task')
        data = self.datasets[name]
        try:
            current = self.path(data['relative_path'])
            fresh = current == data['path'] and version(current) == data['version']
        except (OSError, ValueError):
            fresh = False
        if not fresh:
            del self.datasets[name]
            raise ValueError('Dataset changed or disappeared; explicitly load it again')
        self.hits += 1
        return data

    def describe(self, data):
        return {'rows': len(data['rows']), 'columns': data['columns'],
                'source_bytes': data['source_bytes'], 'memory_bytes_estimate': data['memory_bytes'],
                'source_sha256': data['sha256']}

    def load(self, name, raw):
        if not NAME.fullmatch(name):
            raise ValueError('Dataset name must be 1–48 letters, digits, underscores or hyphens')
        path = self.path(raw)
        initial = version(path)
        if initial[2] > MAX_SOURCE:
            raise ValueError('Source exceeds 4 MiB limit')
        if name in self.datasets:
            prior = self.datasets[name]
            if prior['path'] == path and prior['version'] == initial:
                self.hits += 1
                return {'cache_hit': True, **self.describe(prior)}
            del self.datasets[name]
        with path.open('rb') as stream:
            source = stream.read(MAX_SOURCE + 1)
        if len(source) > MAX_SOURCE or version(path) != initial:
            raise ValueError('Source too large or changed during load')
        text = source.decode('utf-8-sig')
        if path.suffix.lower() == '.csv':
            reader = csv.DictReader(io.StringIO(text))
            columns = reader.fieldnames or []
            if len(set(columns)) != len(columns) or any(not c for c in columns):
                raise ValueError('CSV requires nonempty, unique column names')
            rows = []
            running = size_of(columns)
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError('CSV row does not match its header')
                running += size_of(row)
                if len(rows) >= MAX_ROWS or running + self.used() > self.max_bytes:
                    raise ValueError('Dataset exceeds row or cache memory limit')
                rows.append(row)
        elif path.suffix.lower() == '.json':
            rows = json.loads(text)
            if not isinstance(rows, list) or len(rows) > MAX_ROWS or any(not isinstance(r, dict) for r in rows):
                raise ValueError('JSON must be an array of at most 50000 flat records')
            if any(not isinstance(k, str) or isinstance(v, (dict, list)) for r in rows for k, v in r.items()):
                raise ValueError('Only flat JSON records are supported')
            columns = sorted({k for row in rows for k in row})
        else:
            raise ValueError('Supported formats: .csv and flat-record .json')
        if len(columns) > 200:
            raise ValueError('Dataset exceeds 200 columns')
        memory = size_of(rows) + size_of(columns)
        if memory + self.used() > self.max_bytes:
            raise ValueError('Cache memory budget exceeded; release an existing dataset')
        if version(path) != initial:
            raise ValueError('Source changed during parsing')
        data = {'path': path, 'relative_path': str(path.relative_to(self.root)), 'version': initial,
                'rows': rows, 'columns': columns, 'source_bytes': len(source), 'memory_bytes': memory,
                'sha256': hashlib.sha256(source).hexdigest()}
        self.datasets[name] = data
        self.parses += 1
        return {'cache_hit': False, **self.describe(data)}

    def dispatch(self, request):
        op = request.get('op')
        if op == 'status':
            return {'pid': os.getpid(), 'root': str(self.root), 'datasets': sorted(self.datasets),
                    'parse_count': self.parses, 'cache_hits': self.hits,
                    'cache_bytes_estimate': self.used(), 'cache_budget_bytes': self.max_bytes}
        name = request.get('name', '')
        if op == 'load':
            return self.load(name, request['path'])
        if op == 'release':
            return {'released': self.datasets.pop(name, None) is not None}
        data = self.get(name)
        if op == 'describe':
            return self.describe(data)
        limit = request.get('limit', 10)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Limit must be between 1 and 50')
        if op == 'sample':
            return {'rows': data['rows'][:limit], 'total_rows': len(data['rows']),
                    'truncated': len(data['rows']) > limit}
        if op == 'aggregate':
            group = request['group_by']
            value = request.get('value')
            if group not in data['columns'] or (value and value not in data['columns']):
                raise ValueError('Unknown group or numeric value column')
            groups = {}
            for row in data['rows']:
                key = json.dumps(row.get(group), ensure_ascii=False)
                if key not in groups:
                    if len(groups) >= 2000:
                        raise ValueError('More than 2000 groups; use a lower-cardinality column')
                    groups[key] = {'group': row.get(group), 'count': 0, 'sum': 0.0}
                result = groups[key]
                result['count'] += 1
                if value:
                    number = float(row[value])
                    if not math.isfinite(number) or not math.isfinite(result['sum'] + number):
                        raise ValueError('Numeric values and sums must be finite')
                    result['sum'] += number
            result = sorted(groups.values(), key=lambda g: -g['count'])
            for g in result:
                if value:
                    g['mean'] = g['sum'] / g['count']
                else:
                    del g['sum']
            return {'groups': result[:limit], 'total_groups': len(result), 'truncated': len(result) > limit}
        raise ValueError('Unsupported dataset operation')


def location(session):
    if not NAME.fullmatch(session):
        raise ValueError('Invalid session identifier')
    root = Path.home() / '.cache/tasklean/workers'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return root / (session[:32] + '.sock')


def request_worker(path, request):
    payload = json.dumps(request).encode() + b'\n'
    if len(payload) > MAX_REQUEST:
        raise ValueError('Request exceeds limit')
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(15)
        client.connect(str(path))
        client.sendall(payload)
        with client.makefile('rb') as stream:
            reply = stream.readline(MAX_REPLY + 1)
    if len(reply) > MAX_REPLY:
        raise ValueError('Reply exceeds limit')
    return json.loads(reply)


def serve(args):
    path = location(args.session)
    cache = DatasetCache(args.root, args.max_cache_mb * 1024 * 1024)
    # Cache estimates are not RSS. Bound total address space separately on Linux.
    if sys.platform == 'linux':
        import resource
        ceiling = (args.max_cache_mb + 192) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
    os.umask(0o077)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))  # Never replace a socket belonging to another worker.
        try:
            server.listen(8)
            server.settimeout(args.idle_seconds)
            while True:
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    break
                closing = False
                with connection:
                    connection.settimeout(10)
                    try:
                        with connection.makefile('rb') as stream:
                            raw = stream.readline(MAX_REQUEST + 1)
                        if len(raw) > MAX_REQUEST:
                            raise ValueError('Request too large')
                        request = json.loads(raw)
                        if not isinstance(request, dict):
                            raise ValueError('Request must be an object')
                        if request.get('op') == 'close':
                            result, closing = {'closed': True}, True
                        else:
                            result = cache.dispatch(request)
                        reply = json.dumps({'ok': True, 'result': result}, allow_nan=False).encode() + b'\n'
                        if len(reply) > MAX_REPLY:
                            raise ValueError('Result too large; request a smaller sample')
                    except (ValueError, OSError, KeyError, TypeError, MemoryError, RecursionError) as error:
                        message = str(error) if isinstance(error, ValueError) else type(error).__name__
                        reply = json.dumps({'ok': False, 'error': message[:300]}).encode() + b'\n'
                    try:
                        connection.sendall(reply)
                    except OSError:
                        pass
                if closing:
                    break
        finally:
            path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'serve', 'request'])
    parser.add_argument('--session', required=True)
    parser.add_argument('--root')
    parser.add_argument('--idle-seconds', type=int, default=600)
    parser.add_argument('--max-cache-mb', type=int, default=64)
    args = parser.parse_args(argv)
    if not 10 <= args.idle_seconds <= 3600 or not 1 <= args.max_cache_mb <= 256:
        parser.error('Idle timeout must be 10–3600 seconds and cache budget 1–256 MiB')
    try:
        path = location(args.session)
        if args.action == 'serve':
            serve(args)
            return
        if args.action == 'start':
            root = str(Path(args.root).resolve(strict=True))
            if not Path(root).is_dir():
                raise ValueError('Dataset root must be a directory')
            if path.exists():
                result = request_worker(path, {'op': 'status'})
                if result.get('result', {}).get('root') != root:
                    raise ValueError('Worker already exists with a different dataset root')
            else:
                proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'serve',
                    '--session', args.session, '--root', root, '--idle-seconds', str(args.idle_seconds),
                    '--max-cache-mb', str(args.max_cache_mb)], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                deadline = time.monotonic() + 5
                while not path.exists() and proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.03)
                result = request_worker(path, {'op': 'status'})
        else:
            raw = sys.stdin.read(MAX_REQUEST + 1)
            if len(raw.encode()) > MAX_REQUEST:
                raise ValueError('Request too large')
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError('Request must be an object')
            result = request_worker(path, request)
        print(json.dumps(result))
        return 0 if result.get('ok') else 1
    except (ValueError, OSError, TypeError) as error:
        print(json.dumps({'ok': False, 'error': str(error)[:300]}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
