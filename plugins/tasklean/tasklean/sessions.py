# SPDX-License-Identifier: GPL-3.0-or-later
"""Task-scoped SSH transport reuse and explicit remote dataset handles."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
import uuid
from .storage import write_json

NAME = re.compile(r'^[a-zA-Z0-9_-]{1,48}$')


def state_dir():
    root = Path(os.environ.get('TASKLEAN_STATE_DIR', str(Path.home() / '.local/share/tasklean'))).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def state_path(task):
    if not NAME.fullmatch(task):
        raise ValueError('Task name must be 1–48 letters, digits, underscores or hyphens')
    return state_dir() / (task + '.json')


def read(task):
    path = state_path(task)
    if not path.exists():
        raise ValueError('No such task session; start one first')
    return json.loads(path.read_text())


def ssh_args(state):
    args = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        '-o', 'ControlMaster=auto', '-o', 'ControlPersist=' + str(state['idle_seconds']),
        '-o', 'ControlPath=' + state['control_path'], '-o', 'ServerAliveInterval=30',
        '-o', 'ServerAliveCountMax=3', '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
        '-p', str(state['port'])]
    if state.get('identity'):
        args += ['-i', state['identity'], '-o', 'IdentitiesOnly=yes']
    return args


def master_check(state):
    result = subprocess.run([*ssh_args(state), '-O', 'check', state['host']],
                            capture_output=True, text=True, timeout=12)
    match = re.search(r'pid=(\d+)', result.stderr)
    return {'connected': result.returncode == 0, 'master_pid': int(match[1]) if match else None}


def remote(state, command, stdin='', timeout=30, output_limit=262144):
    before = master_check(state)
    started = time.monotonic()
    # Remote command quoting is required even though the local process uses argv.
    remote_command = shlex.join(command) if isinstance(command, list) else command
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            result = subprocess.run([*ssh_args(state), state['host'], remote_command], input=stdin.encode(),
                                    stdout=stdout, stderr=stderr, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ValueError('SSH request timed out; remote command outcome may be unknown. Do not blindly retry mutations.') from None
        stdout.seek(0)
        stderr.seek(0)
        out, err = stdout.read(output_limit + 1), stderr.read(output_limit + 1)
    return {'returncode': result.returncode, 'stdout': out[:output_limit].decode(errors='replace'),
            'stderr': err[:output_limit].decode(errors='replace'),
            'output_truncated': len(out) > output_limit or len(err) > output_limit,
            'transport_reused': before['connected'], 'master_pid_before': before['master_pid'],
            'elapsed_ms': round((time.monotonic() - started) * 1000, 2)}


def rpc(state, payload):
    result = remote(state, ['python3', state['agent'], 'request', '--session', state['session']], json.dumps(payload))
    if result['output_truncated']:
        raise ValueError('Worker response exceeded limit')
    try:
        response = json.loads(result['stdout'])
    except ValueError:
        raise ValueError('Worker unavailable or SSH failed. Check session status; no operation was automatically retried.') from None
    if not response.get('ok'):
        raise ValueError('Worker: ' + response.get('error', 'unknown error'))
    return {**response['result'], 'transport_reused': result['transport_reused'],
            'roundtrip_ms': result['elapsed_ms']}


def start(a):
    path = state_path(a.task)
    if path.exists():
        raise ValueError('Task session already exists; use status or close it before starting again')
    if not re.fullmatch(r'(?:[a-zA-Z0-9_][a-zA-Z0-9_.-]*@)?[a-zA-Z0-9][a-zA-Z0-9_.-]*', a.host):
        raise ValueError('Use a hostname/IP or user@hostname; SSH aliases are supported')
    if not 1 <= a.port <= 65535 or not 10 <= a.idle_seconds <= 3600 or not 1 <= a.max_cache_mb <= 256:
        raise ValueError('Invalid port, idle timeout (10–3600 seconds), or memory budget (1–256 MiB)')
    if not a.root.startswith('/'):
        raise ValueError('--root must be an absolute dataset directory on the remote server')
    identity = str(Path(a.identity).expanduser().resolve(strict=True)) if a.identity else None
    session = uuid.uuid4().hex
    sockets = Path.home() / '.cache/tasklean/ssh'
    sockets.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(sockets, 0o700)
    control = str(sockets / session[:24])
    if len(control.encode()) > 100:
        raise ValueError('Home path too long for an SSH control socket')
    source = Path(__file__).with_name('remote_worker.py').read_text()
    agent = '.cache/tasklean/agents/' + hashlib.sha256(source.encode()).hexdigest()[:20] + '/worker.py'
    state = {'task': a.task, 'session': session, 'host': a.host, 'port': a.port,
             'identity': identity, 'root': a.root, 'idle_seconds': a.idle_seconds,
             'max_cache_mb': a.max_cache_mb, 'control_path': control, 'agent': agent}
    # Create exclusively before starting: two callers cannot own the same task name.
    write_json(path, state)
    try:
        upload = remote(state, 'umask 077; mkdir -p ' + shlex.quote(str(Path(agent).parent)) +
                        ' && cat > ' + shlex.quote(agent), source)
        if upload['returncode']:
            raise ValueError('Could not install the task worker over SSH: ' + upload['stderr'][:500])
        launched = remote(state, ['python3', agent, 'start', '--session', session, '--root', a.root,
            '--idle-seconds', str(a.idle_seconds), '--max-cache-mb', str(a.max_cache_mb)])
        reply = json.loads(launched['stdout'])
        if not reply.get('ok'):
            raise ValueError('Worker did not start: ' + reply.get('error', 'unknown error'))
        print(json.dumps({'task': a.task, 'worker': reply['result'], 'ssh': master_check(state),
                          'idle_seconds': a.idle_seconds, 'data_publicly_exposed': False}, indent=2))
    except (OSError, ValueError, subprocess.SubprocessError):
        # Keep state for an explicit close/recovery, since remote completion can be uncertain.
        raise


def status(a):
    state = read(a.task)
    result = {'task': a.task, 'ssh': master_check(state)}
    try:
        result['worker'] = rpc(state, {'op': 'status'})
    except ValueError as error:
        result['worker_error'] = str(error)
        result['recovery'] = 'An expired worker loses its in-memory data. Close this session, then start and load again.'
    print(json.dumps(result, indent=2))


def close(a):
    state = read(a.task)
    result = {'task': a.task}
    try:
        result['worker'] = rpc(state, {'op': 'close'})
    except (ValueError, subprocess.SubprocessError) as error:
        result['worker_close_warning'] = str(error)
    try:
        stopped = subprocess.run([*ssh_args(state), '-O', 'exit', state['host']],
                                 capture_output=True, text=True, timeout=12)
        result['master_exit_returncode'] = stopped.returncode
    finally:
        state_path(a.task).unlink(missing_ok=True)
    print(json.dumps(result, indent=2))


def execute(a):
    command = a.command[1:] if a.command and a.command[0] == '--' else a.command
    if not command:
        raise ValueError('Supply a remote command after --')
    if not 1 <= a.timeout <= 3600:
        raise ValueError('Timeout must be 1–3600 seconds')
    result = remote(read(a.task), command, timeout=a.timeout, output_limit=32768)
    print(json.dumps(result, indent=2))
    if result['returncode']:
        raise SystemExit(1)


def dataset(a):
    payload = {'op': a.operation, 'name': a.name}
    for key in ('path', 'limit', 'group_by', 'value'):
        if getattr(a, key, None) is not None:
            payload[key] = getattr(a, key)
    print(json.dumps(rpc(read(a.task), payload), indent=2))


def register(subparsers):
    p = subparsers.add_parser('session', help='Reuse SSH connections and task-scoped remote datasets')
    sub = p.add_subparsers(dest='session_command', required=True)
    s = sub.add_parser('start', help='Start a private task worker and persistent SSH transport')
    s.add_argument('--task', required=True)
    s.add_argument('--host', required=True)
    s.add_argument('--root', required=True, help='Absolute allowed dataset root on remote host')
    s.add_argument('--identity', help='Local SSH key path; never uploaded')
    s.add_argument('--port', type=int, default=22)
    s.add_argument('--idle-seconds', type=int, default=600)
    s.add_argument('--max-cache-mb', type=int, default=64)
    s.set_defaults(func=start)
    for name, func in [('status', status), ('close', close)]:
        s = sub.add_parser(name)
        s.add_argument('--task', required=True)
        s.set_defaults(func=func)
    s = sub.add_parser('exec', help='Execute a command using the task SSH transport; shell variables do not persist')
    s.add_argument('--task', required=True)
    s.add_argument('--timeout', type=int, default=60)
    s.add_argument('command', nargs='...')
    s.set_defaults(func=execute)
    s = sub.add_parser('data', help='Operate on explicitly loaded datasets in remote memory')
    d = s.add_subparsers(dest='operation', required=True)
    for operation in ('load', 'describe', 'sample', 'aggregate', 'release'):
        q = d.add_parser(operation)
        q.add_argument('--task', required=True)
        q.add_argument('--name', required=True)
        if operation == 'load':
            q.add_argument('--path', required=True)
        if operation in ('sample', 'aggregate'):
            q.add_argument('--limit', type=int, default=10)
        if operation == 'aggregate':
            q.add_argument('--group-by', required=True)
            q.add_argument('--value', help='Numeric column to sum/average; omit for group counts')
        q.set_defaults(func=dataset)
