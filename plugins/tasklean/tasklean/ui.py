# SPDX-License-Identifier: GPL-3.0-or-later
"""Loopback-only browser launcher. No hosted credentials or global Codex edits."""
import json
import os
import secrets
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .launcher import doctor, launch
from .limits import LimitsReader
from .storage import write_json
from .workspace import Workspace, initialize


class Dashboard:
    def __init__(self, directory, binary=None):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.registry = self.directory / 'tasks.json'
        self.tasks = json.loads(self.registry.read_text()) if self.registry.exists() else {}
        self.trash_file = self.directory / 'deleted-tasks.json'
        self.deleted = json.loads(self.trash_file.read_text()) if self.trash_file.exists() else {}
        self.binary = binary
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.jobs = {}
        self.limits = LimitsReader(binary)

    def path(self, task):
        with self.lock:
            if task not in self.tasks or task in self.deleted:
                raise ValueError('Unknown task')
            return Path(self.tasks[task])

    def attach(self, directory):
        with Workspace(directory) as work:
            task = work.config['id']
            path = str(work.directory)
        with self.lock:
            self.tasks[task] = path
            temporary = self.directory / ('registry-' + uuid.uuid4().hex + '.tmp')
            write_json(temporary, self.tasks)
            os.replace(temporary, self.registry)
            if task in self.deleted:
                self.set_deleted(task, False)
        return {'task': task}

    def set_deleted(self, task, deleted=True):
        # Soft deletion changes dashboard visibility only. Project and task files stay intact.
        with self.lock:
            if task not in self.tasks:
                raise ValueError('Unknown task')
            if self.jobs.get(task, {}).get('state') == 'running':
                raise ValueError('Wait for the running turn to finish before deleting this task')
            if not deleted and task not in self.deleted:
                raise ValueError('Task is not deleted')
            updated = dict(self.deleted)
            if deleted:
                updated[task] = time.time()
            else:
                updated.pop(task)
            temporary = self.directory / ('deleted-' + uuid.uuid4().hex + '.tmp')
            write_json(temporary, updated)
            os.replace(temporary, self.trash_file)
            self.deleted = updated
        return {'task': task, 'deleted': deleted}

    def listing(self):
        rows, trash = [], []
        with self.lock:
            entries = list(self.tasks.items())
            deleted = dict(self.deleted)
        for task, path in entries:
            try:
                with Workspace(path) as work:
                    row = {'id': task, 'goal': work.config['goal'], 'project': str(work.project)}
            except (OSError, ValueError, KeyError):
                row = {'id': task, 'goal': 'Unavailable task', 'project': path}
            if task in deleted:
                trash.append({**row, 'deleted_at': deleted[task]})
            else:
                rows.append(row)
        projects = {}
        for row in rows:
            project = row['project']
            if project not in projects:
                projects[project] = {'path': project, 'name': Path(project).name or project, 'tasks': []}
            projects[project]['tasks'].append(row)
        return {'tasks': rows, 'projects': list(projects.values()), 'home_directory': str(Path.home()),
                'deleted': sorted(trash, key=lambda row: row['deleted_at'], reverse=True)}

    def detail(self, task):
        directory = self.path(task)
        with Workspace(directory) as work:
            result = {'status': work.status(), 'report': work.report(), 'directory': str(directory)}
            result['commands'] = [json.loads(r['record']) for r in work.db.execute(
                'SELECT record FROM commands ORDER BY rowid DESC LIMIT 20')]
        turns = []
        for out in sorted((directory / 'turns').iterdir(), key=lambda p: p.stat().st_mtime_ns)[-30:]:
            if out.is_symlink() or not out.is_dir():
                continue
            record = out / 'run.json'
            if record.exists():
                try:
                    data = json.loads(record.read_text())
                    turns.append({k: data.get(k) for k in ('turn', 'answer', 'execution_status', 'execution_usage', 'elapsed_seconds', 'sandbox')})
                    prompt_file = out / 'user_prompt.txt'
                    if prompt_file.exists():
                        turns[-1]['prompt'] = prompt_file.read_text()
                except ValueError:
                    pass  # A CLI turn may be finishing its record concurrently.
        result['turns'] = turns
        with self.lock:
            result['job'] = dict(self.jobs.get(task, {}))
        return result

    def run(self, task, data):
        directory = self.path(task)
        prompt = data.get('prompt', '')
        model = data.get('model') or None
        reasoning = data.get('reasoning') or None
        sandbox = data.get('sandbox', 'read-only')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 128 * 1024:
            raise ValueError('Enter a prompt up to 128 KB')
        if model is not None and (not isinstance(model, str) or len(model) > 100):
            raise ValueError('Invalid model')
        if reasoning not in (None, 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra') or sandbox not in ('read-only', 'workspace-write'):
            raise ValueError('Invalid reasoning or sandbox')
        args = dict(binary=self.binary, model=model, reasoning=reasoning, sandbox=sandbox, timeout=600)
        # Validate prerequisites synchronously before accepting a background turn.
        launch(directory, prompt, **args)
        with self.lock:
            self.path(task)  # Recheck after validation, in case the task was deleted meanwhile.
            if self.jobs.get(task, {}).get('state') == 'running':
                raise ValueError('This task already has a running turn')
            self.jobs[task] = {'state': 'running', 'started': time.time(), 'prompt': prompt}
        def worker():
            try:
                result = launch(directory, prompt, **args, execute_turn=True)
                update = {'state': result['execution_status'], 'finished': time.time()}
            except Exception as error:
                update = {'state': 'failed', 'error': str(error), 'finished': time.time()}
            with self.lock:
                self.jobs[task].update(update)
        threading.Thread(target=worker, daemon=False).start()
        return {'accepted': True}

    def action(self, route, data):
        if route == '/api/feedback':
            from .feedback import submit_feedback
            return submit_feedback(data)
        if route == '/api/tasks':
            goal, project = data.get('goal'), data.get('project')
            if not isinstance(goal, str) or not isinstance(project, str) or not project.strip():
                raise ValueError('Project path and goal are required')
            directory = self.directory / 'tasks' / uuid.uuid4().hex
            directory.parent.mkdir(mode=0o700, exist_ok=True)
            initialize(project, directory, goal)
            return self.attach(directory)
        if route == '/api/import':
            path = data.get('directory')
            if not isinstance(path, str) or not path.strip():
                raise ValueError('Task directory is required')
            return self.attach(path)
        if route == '/api/demo':
            from .demo import run_demo
            out = self.directory / ('demo-' + uuid.uuid4().hex)
            report = run_demo(out)
            return {**self.attach(out / 'task'), 'demo': report}
        task = data.get('task')
        if not isinstance(task, str):
            raise ValueError('Task ID is required')
        if route == '/api/delete':
            return self.set_deleted(task)
        if route == '/api/restore':
            return self.set_deleted(task, False)
        if route == '/api/detail':
            return self.detail(task)
        if route == '/api/run':
            return self.run(task, data)
        if route == '/api/artifact':
            handle = data.get('id')
            offset = data.get('offset', 0)
            if not isinstance(handle, str) or type(offset) is not int or offset < 0:
                raise ValueError('Invalid artifact or offset')
            with Workspace(self.path(task)) as work:
                return work.artifact(handle, offset, 8000)
        raise ValueError('Unknown action')


def make_server(directory, port=0, binary=None):
    app = Dashboard(directory, binary)
    assets = Path(__file__).with_name('web')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log launch URL, prompts, or credentials.

        def reply(self, status, body, content_type='application/json'):
            payload = json.dumps(body).encode() if content_type == 'application/json' else body
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def allowed(self, api=False):
            authority = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != authority:
                self.reply(403, {'error': 'Invalid host'})
                return False
            origin = self.headers.get('Origin')
            if origin is not None and origin != 'http://' + authority:
                self.reply(403, {'error': 'Cross-origin requests are forbidden'})
                return False
            if api and not secrets.compare_digest(self.headers.get('X-TaskLean-Token', '').encode(), app.token.encode()):
                self.reply(401, {'error': 'Open the launch URL printed by tasklean ui to connect'})
                return False
            return True

        def do_GET(self):
            route = urlsplit(self.path).path
            if not self.allowed(route.startswith('/api/')):
                return
            if route == '/api/tasks':
                self.reply(200, app.listing())
            elif route == '/api/limits':
                self.reply(200, app.limits.read(force=urlsplit(self.path).query == 'refresh=1'))
            elif route == '/api/models':
                self.reply(200, app.limits.models(force=urlsplit(self.path).query == 'refresh=1'))
            elif route == '/api/doctor':
                self.reply(200, doctor(app.binary))
            elif route in ('/', '/app.js', '/style.css', '/icon.svg'):
                name, mime = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/icon.svg': ('icon.svg', 'image/svg+xml')}[route]
                self.reply(200, (assets / name).read_bytes(), mime)
            else:
                self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            if not self.allowed(True):
                return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.reply(415, {'error': 'JSON required'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 160 * 1024:
                    raise ValueError('Request must be 1–160 KB')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('Expected a JSON object')
                result = app.action(urlsplit(self.path).path, data)
                self.reply(200, result)
            except (ValueError, OSError, KeyError) as error:
                self.reply(400, {'error': str(error)})
            except Exception:
                self.reply(500, {'error': 'Task operation failed. Check the local task state and retry.'})

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

    class Server(ThreadingHTTPServer):
        def server_close(self):
            super().server_close()
            app.limits.close()

    server = Server(('127.0.0.1', port), Handler)
    server.app = app
    return server


def serve(directory, port=0, open_browser=True, binary=None):
    server = make_server(directory, port, binary)
    url = f'http://127.0.0.1:{server.server_port}/#token={server.app.token}'
    print('Codex LeanTask local dashboard\n' + url, flush=True)
    print('Keep this terminal running. Ctrl+C stops the dashboard; let active turns finish first.', flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Dashboard closed. Any active turn will finish within its timeout before this process exits.", flush=True)
    finally:
        server.server_close()
