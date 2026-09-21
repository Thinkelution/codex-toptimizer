# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit Codex launcher; no shell interpolation or permission bypasses."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from .storage import private_write
from .usage import codex_usage


def fingerprint(project):
    try:
        def git(*args):
            return subprocess.check_output(['git', '-C', str(project), *args], stderr=subprocess.DEVNULL, timeout=10)
        root = Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve()
        h = hashlib.sha256(git('rev-parse', 'HEAD') + git('diff', 'HEAD', '--binary'))
        h.update(str(Path(project).resolve().relative_to(root)).encode())
        # Count untracked inputs, too; don't describe a commit alone as an identical checkout.
        paths = git('-C', str(root), 'ls-files', '--others', '--exclude-standard', '--full-name', '-z').split(b'\0')
        budget = 20_000_000
        for raw in sorted(p for p in paths if p):
            path = root / os.fsdecode(raw)
            if path.is_symlink() or not path.is_file():
                return None
            budget -= path.stat().st_size
            if budget < 0:
                return None
            h.update(raw + b'\0' + path.read_bytes())
        return h.hexdigest()
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def command(binary, project, model, reasoning, sandbox):
    return [binary, 'exec', '--json', '--ephemeral', '--color', 'never',
            '--sandbox', sandbox, '--cd', str(project), '--model', model,
            '-c', 'model_reasoning_effort=' + json.dumps(reasoning), '-']


def version(binary):
    try:
        return subprocess.check_output([binary, '--version'], stderr=subprocess.DEVNULL,
                                       timeout=5, text=True).strip()[:200] or None
    except (OSError, subprocess.SubprocessError):
        return None


def execute(argv, prompt, out, timeout):
    started = time.monotonic()
    stdout_path, stderr_path = out / 'events.jsonl', out / 'stderr.log'
    private_write(stdout_path, '')
    private_write(stderr_path, '')
    proc = None
    state = 'completed'
    with stdout_path.open('w') as stdout, stderr_path.open('w') as stderr:
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                    text=True, start_new_session=True)
            proc.communicate(prompt, timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as e:
            state = 'timeout' if isinstance(e, subprocess.TimeoutExpired) else 'interrupted'
            if proc is not None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.communicate()
        except OSError:
            state = 'launch_failed'
    usage = codex_usage(stdout_path.read_text())
    if state == 'completed' and (proc.returncode != 0 or usage['failed_event']):
        state = 'failed'
    return {"execution_status": state, "returncode": proc.returncode if proc else None,
            "execution_usage": usage, "elapsed_seconds": round(time.monotonic() - started, 3),
            "quality_outcome": "unreviewed"}
