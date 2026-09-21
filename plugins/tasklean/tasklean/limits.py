# SPDX-License-Identifier: GPL-3.0-or-later
"""Read account quota through Codex's documented app-server protocol; no inference."""
import json
import math
import os
import selectors
import subprocess
import threading
import time
from .launcher import codex_binary


class LimitsUnavailable(Exception):
    pass


def number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def normalize(raw):
    if not isinstance(raw, dict):
        raise LimitsUnavailable()
    by_id = raw.get('rateLimitsByLimitId')
    if isinstance(by_id, dict) and by_id:
        buckets = [(key, value) for key, value in by_id.items() if isinstance(value, dict)]
    else:
        legacy = raw.get('rateLimits')
        buckets = [('codex', legacy)] if isinstance(legacy, dict) else []
    result = []
    for key, bucket in buckets:
        windows = []
        for slot in ('primary', 'secondary'):
            window = bucket.get(slot)
            if not isinstance(window, dict):
                continue
            used = window.get('usedPercent')
            duration = window.get('windowDurationMins')
            reset = window.get('resetsAt')
            windows.append({'slot': slot,
                'remaining_percent': round(max(0, min(100, 100 - used)), 1) if number(used) else None,
                'window_minutes': duration if type(duration) is int and duration > 0 else None,
                'resets_at': reset if number(reset) and 0 < reset < 8640000000000 else None})
        name = bucket.get('limitName') or bucket.get('limitId') or key
        result.append({'id': str(key)[:100], 'name': str(name)[:100], 'windows': windows})
    if not result or not any(b['windows'] for b in result):
        raise LimitsUnavailable()
    return result


class LimitsReader:
    """Reuse one private stdio process and coalesce requests for all browser tabs."""
    def __init__(self, binary=None, timeout=12):
        self.binary, self.timeout = binary, timeout
        self.lock = threading.Lock()
        self.proc = None
        self.selector = None
        self.buffer = b''
        self.next_id = 0
        self.snapshot = None
        self.attempted = None
        self.error = None

    def _stop(self):
        if self.selector:
            self.selector.close()
            self.selector = None
        if self.proc:
            proc, self.proc = self.proc, None
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            for stream in (proc.stdin, proc.stdout):
                if stream:
                    stream.close()
        self.buffer = b''

    def close(self):
        with self.lock:
            self._stop()

    def _send(self, payload):
        self.proc.stdin.write((json.dumps(payload) + '\n').encode())
        self.proc.stdin.flush()

    def _request(self, method, params=None):
        self.next_id += 1
        ident = self.next_id
        self._send({'method': method, 'id': ident, 'params': params or {}})
        deadline = time.monotonic() + self.timeout
        received = 0
        while time.monotonic() < deadline:
            if b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    raise LimitsUnavailable() from None
                if not isinstance(event, dict):
                    continue
                if event.get('id') == ident and ('result' in event or 'error' in event):
                    if 'error' in event:
                        raise LimitsUnavailable()
                    return event['result']
                # Never fulfill auth refresh, tool execution or other server requests.
                if 'id' in event and 'method' in event:
                    self._send({'id': event['id'], 'error': {'code': -32601, 'message': 'Read-only quota client'}})
                continue
            if not self.selector.select(max(0, deadline - time.monotonic())):
                break
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                break
            received += len(chunk)
            if received > 1024 * 1024:
                raise LimitsUnavailable()
            self.buffer += chunk
        raise LimitsUnavailable()

    def _read(self):
        if not self.proc or self.proc.poll() is not None:
            self._stop()
            # Do not read auth files or return CLI diagnostics: Codex owns authentication.
            self.proc = subprocess.Popen([codex_binary(self.binary), 'app-server', '--listen', 'stdio://'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                start_new_session=True)
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.proc.stdout, selectors.EVENT_READ)
            self._request('initialize', {'clientInfo': {'name': 'tasklean_quota', 'title': 'Codex LeanTask account limits', 'version': '0.3.0'}})
            self._send({'method': 'initialized', 'params': {}})
        return normalize(self._request('account/rateLimits/read'))

    def read(self, force=False):
        with self.lock:
            now = time.monotonic()
            ttl = 5 if force else 60
            if self.attempted is None or now - self.attempted >= ttl:
                try:
                    buckets = self._read()
                    self.snapshot = {'buckets': buckets, 'checked_at': time.time()}
                    self.error = None
                except (LimitsUnavailable, OSError, ValueError, subprocess.SubprocessError):
                    self.error = 'Account limits unavailable. Check your Codex CLI login and version; API-key accounts may not report ChatGPT limits.'
                    self._stop()
                self.attempted = time.monotonic()
            return {'available': self.snapshot is not None, 'stale': bool(self.error),
                'error': self.error, 'scope': 'account', 'server_time': time.time(),
                **(self.snapshot or {'buckets': [], 'checked_at': None})}
