# SPDX-License-Identifier: GPL-3.0-or-later
"""Compact stdout at the execution boundary, with retrievable bounded raw logs."""
import json
import os
import re
import selectors
import signal
import subprocess
import time
import uuid
from .runner import fingerprint
from .storage import private_write

MAX_LOG = 8 * 1024 * 1024
IMPORTANT = re.compile(r'(^FAIL|^ERROR|^E\s|Traceback|AssertionError|\berror\b|\bfailed\b|^Ran \d+ tests?|^OK$|\bpassed\b)', re.I)


def summarize(text, limit=4000):
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    important = []
    seen = set()
    for line in lines:
        if IMPORTANT.search(line) and line not in seen:
            important.append(line[:400])
            seen.add(line)
    middle = '\n'.join(important)[:1800]
    return (text[:500] + '\n[... full output retained in artifact ...]\n' + middle +
            '\n[... tail ...]\n' + text[-1500:])[:limit]


def capture(work, argv, timeout=120, max_log=MAX_LOG):
    if not argv or not all(isinstance(a, str) for a in argv) or not 1 <= timeout <= 3600:
        raise ValueError('Supply a command and a timeout of 1–3600 seconds')
    if not 1024 <= max_log <= MAX_LOG:
        raise ValueError('Invalid log size limit')
    artifact = uuid.uuid4().hex[:20]
    path = work.directory / 'artifacts' / (artifact + '.log')
    private_write(path, '')
    before = fingerprint(work.project)
    began = time.monotonic()
    state, total, proc = 'completed', 0, None
    with path.open('wb') as log:
        try:
            proc = subprocess.Popen(argv, cwd=work.project, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic() - began >= timeout:
                        state = 'timeout'
                        break
                    ready = selector.select(timeout=0.1)
                    for key, _ in ready:
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            break
                        room = max_log - total
                        log.write(chunk[:room])
                        total += min(room, len(chunk))
                        if len(chunk) > room:
                            state = 'output_limit'
                            break
                    if state != 'completed':
                        break
                if state == 'completed':
                    try:
                        proc.wait(timeout=max(0.01, timeout - (time.monotonic() - began)))
                    except subprocess.TimeoutExpired:
                        state = 'timeout'
        except KeyboardInterrupt:
            state = 'interrupted'
        except OSError as error:
            state = 'launch_failed'
            log.write((type(error).__name__ + ': command could not be started\n').encode())
        finally:
            if proc is not None:
                if state != 'completed':
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                proc.stdout.close()
    text = path.read_text(errors='replace')
    code = proc.returncode if proc else None
    if state == 'completed' and code != 0:
        state = 'failed'
    record = {'artifact': artifact, 'command': argv, 'execution_status': state, 'returncode': code,
              'duration_seconds': round(time.monotonic() - began, 3), 'log_bytes': path.stat().st_size,
              'log_complete': state not in {'output_limit', 'timeout', 'interrupted'},
              'summary': summarize(text), 'summary_shortened': len(text) > 4000,
              'project_before': before, 'project_after': fingerprint(work.project),
              'verification': 'Execution receipt only; no automatic test skipping or task-quality claim.'}
    with work.db:
        work.db.execute('INSERT INTO commands VALUES (?,?)', (artifact, json.dumps(record)))
    return work.record_event('command', record, len(text))
