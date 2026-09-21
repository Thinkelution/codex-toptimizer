import http.client
import json
from pathlib import Path
import tempfile
import sys
import threading
import time
import unittest
from unittest.mock import patch
from tasklean.ui import Dashboard, make_server
from tasklean.workspace import Workspace


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.server = make_server(self.root / 'ui')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, data=None, token=True, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        self.addCleanup(conn.close)
        h = {'Content-Type': 'application/json'}
        if token:
            h['X-TaskLean-Token'] = self.server.app.token
        h.update(headers or {})
        conn.request('GET' if data is None else 'POST', path, None if data is None else json.dumps(data), h)
        response = conn.getresponse()
        body = response.read()
        return response.status, json.loads(body) if response.getheader('Content-Type') == 'application/json' else body

    def create(self):
        status, data = self.request('/api/tasks', {'project': str(self.project), 'goal': 'Review the app'})
        self.assertEqual(status, 200, data)
        return data['task']

    def test_account_limits_endpoint_requires_auth_and_supports_refresh(self):
        snapshot={'available':False,'buckets':[],'checked_at':None}
        with patch.object(self.server.app.limits,'read',return_value=snapshot) as read:
            self.assertEqual(self.request('/api/limits',token=False)[0],401)
            read.assert_not_called()
            self.assertEqual(self.request('/api/limits'),(200,snapshot))
            read.assert_called_with(force=False)
            self.assertEqual(self.request('/api/limits?refresh=1')[0],200)
            read.assert_called_with(force=True)

    def test_assets_packaged_and_no_token_in_html(self):
        for path in ('/', '/app.js', '/style.css'):
            status, body = self.request(path, token=False)
            self.assertEqual(status, 200)
            self.assertNotIn(self.server.app.token.encode(), body)
        self.assertEqual(self.request('/../ui.py')[0], 404)

    def test_token_required_for_read_and_mutation(self):
        self.assertEqual(self.request('/api/tasks', token=False)[0], 401)
        self.assertEqual(self.request('/api/demo', {}, token=False)[0], 401)
        self.assertEqual(self.request('/api/tasks', headers={'X-TaskLean-Token': 'wrong'})[0], 401)

    def test_origin_host_and_content_type_enforced(self):
        self.assertEqual(self.request('/api/tasks', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/tasks', headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/demo', {}, headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('/api/tasks', headers={'Origin': f'http://127.0.0.1:{self.server.server_port}'})[0], 200)

    def test_create_import_restart_and_detail(self):
        task = self.create()
        task2 = self.create()
        self.assertNotEqual(task, task2)
        path = self.server.app.path(task)
        self.assertEqual(self.request('/api/import', {'directory': str(path)})[0], 200)
        restored = Dashboard(self.root / 'ui')
        self.assertEqual(len(restored.listing()['tasks']), 2)
        self.assertEqual(restored.path(task), path)
        status, detail = self.request('/api/detail', {'task': task})
        self.assertEqual(status, 200)
        self.assertEqual(detail['status']['goal'], 'Review the app')
        self.assertEqual(detail['turns'], [])
        self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        self.assertEqual(restored.registry.stat().st_mode & 0o777, 0o600)

    def test_project_groups_use_full_paths_and_reuse_existing_projects(self):
        one, two = self.create(), self.create()
        other = self.root / 'another' / 'project'
        other.mkdir(parents=True)
        status, data = self.request('/api/tasks', {'project': str(other), 'goal': 'Another project'})
        self.assertEqual(status, 200)
        listing = self.request('/api/tasks')[1]
        self.assertEqual(len(listing['projects']), 2)
        groups = {p['path']: p['tasks'] for p in listing['projects']}
        self.assertEqual({t['id'] for t in groups[str(self.project.resolve())]}, {one, two})
        self.assertEqual(groups[str(other.resolve())][0]['id'], data['task'])

    def test_delete_restore_persists_and_preserves_all_files(self):
        task = self.create()
        path = self.server.app.path(task)
        source = self.project / 'app.py'
        source.write_text('valuable project code')
        with Workspace(path) as work:
            work.put_meta('keep_me', 'saved context')
        config = (path / 'task.json').read_bytes()
        self.assertEqual(self.request('/api/delete', {'task': task}, token=False)[0], 401)
        self.assertEqual(self.request('/api/delete', {'task': task})[0], 200)
        listing = self.request('/api/tasks')[1]
        self.assertEqual(listing['tasks'], [])
        self.assertEqual(listing['projects'], [])
        self.assertEqual(listing['deleted'][0]['id'], task)
        self.assertEqual(self.request('/api/detail', {'task': task})[0], 400)
        restarted = Dashboard(self.root / 'ui')
        self.assertEqual(restarted.listing()['deleted'][0]['id'], task)
        self.assertEqual(restarted.trash_file.stat().st_mode & 0o777, 0o600)
        restarted.action('/api/restore', {'task': task})
        self.assertEqual(restarted.path(task), path)
        self.assertEqual(restarted.listing()['deleted'], [])
        self.assertEqual(source.read_text(), 'valuable project code')
        self.assertEqual((path / 'task.json').read_bytes(), config)
        with Workspace(path) as work:
            self.assertEqual(work.get_meta('keep_me'), 'saved context')

    def test_restore_endpoint_and_import_restore_deleted_task(self):
        task = self.create()
        path = self.server.app.path(task)
        for method in ('restore', 'import'):
            self.assertEqual(self.request('/api/delete', {'task': task})[0], 200)
            route, data = ('/api/restore', {'task': task}) if method == 'restore' else ('/api/import', {'directory': str(path)})
            self.assertEqual(self.request(route, data)[0], 200)
            self.assertEqual(self.request('/api/tasks')[1]['deleted'], [])
        self.assertEqual(self.request('/api/delete', {'task': 'unknown'})[0], 400)
        self.assertEqual(self.request('/api/restore', {'task': task})[0], 400)

    def test_deleted_task_cannot_start_when_deleted_during_validation(self):
        task = self.create()
        def fake(*args, **kwargs):
            self.server.app.set_deleted(task)
        with patch('tasklean.ui.launch', side_effect=fake) as launch:
            self.assertEqual(self.request('/api/run', {'task': task, 'prompt': 'review'})[0], 400)
            self.assertEqual(launch.call_count, 1)
        self.assertNotIn(task, self.server.app.jobs)

    def test_invalid_inputs_and_artifact_traversal(self):
        task = self.create()
        for route, data in [('/api/tasks', {'project': [], 'goal': 'x'}), ('/api/detail', {'task': '../other'}),
                            ('/api/run', {'task': task, 'prompt': 'x', 'sandbox': 'danger-full-access'}),
                            ('/api/run', {'task': task, 'prompt': []}),
                            ('/api/artifact', {'task': task, 'id': '../task.json'})]:
            self.assertEqual(self.request(route, data)[0], 400)

    def test_background_turn_duplicate_guard_and_failure(self):
        task = self.create()
        entered, release = threading.Event(), threading.Event()
        def fake(*args, **kwargs):
            if not kwargs.get('execute_turn'):
                return {'dry_run': True}
            entered.set()
            release.wait(5)
            raise ValueError('Model unavailable')
        with patch('tasklean.ui.launch', side_effect=fake):
            self.assertEqual(self.request('/api/run', {'task': task, 'prompt': 'review'})[0], 200)
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.request('/api/run', {'task': task, 'prompt': 'duplicate'})[0], 400)
            self.assertEqual(self.server.app.detail(task)['job']['state'], 'running')
            self.assertEqual(self.request('/api/delete', {'task': task})[0], 400)
            self.assertEqual(self.request('/api/tasks')[1]['deleted'], [])
            release.set()
            for _ in range(100):
                if self.server.app.detail(task)['job']['state'] != 'running':
                    break
                time.sleep(.01)
        self.assertEqual(self.server.app.detail(task)['job']['error'], 'Model unavailable')

    def test_offline_demo_and_artifact_pagination(self):
        status, data = self.request('/api/demo', {})
        self.assertEqual(status, 200, data)
        task = data['task']
        detail = self.server.app.detail(task)
        self.assertGreaterEqual(len(detail['commands']), 3)
        longest = max(detail['commands'], key=lambda c: c['log_bytes'])
        status, artifact = self.request('/api/artifact', {'task': task, 'id': longest['artifact']})
        self.assertEqual(status, 200)
        self.assertTrue(artifact['more'])
        self.assertEqual(artifact['next_offset'], 8000)
        self.assertIn('text', artifact)
        self.assertEqual(detail['report']['model_turns'], [])

    def test_http_to_real_launcher_subprocess_and_resume(self):
        fake = self.root / 'codex-fixture'
        fake.write_text('#!' + sys.executable + '\n' + """import json, sys
if '--version' in sys.argv:
    print('fixture-codex'); raise SystemExit(0)
sys.stdin.read()
print(json.dumps({'type':'thread.started','thread_id':'fixture-thread'}))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'resumed' if 'resume' in sys.argv else 'first'}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':4,'output_tokens':2}}))
""")
        fake.chmod(0o700)
        self.server.app.binary = str(fake)
        task = self.create()
        for prompt, answer in [('review', 'first'), ('continue', 'resumed')]:
            status, result = self.request('/api/run', {'task': task, 'prompt': prompt})
            self.assertEqual(status, 200, result)
            for _ in range(200):
                detail = self.server.app.detail(task)
                if detail['job']['state'] != 'running':
                    break
                time.sleep(.01)
            self.assertEqual(detail['job']['state'], 'completed')
            self.assertEqual(detail['turns'][-1]['answer'], answer)
            self.assertEqual(detail['turns'][-1]['prompt'], prompt)
        self.assertEqual(len(detail['report']['model_turns']), 2)
        self.assertEqual(detail['status']['codex_thread'], 'fixture-thread')

    def test_usage_and_completed_turn_are_visible(self):
        task = self.create()
        from tasklean.storage import write_json
        path = self.server.app.path(task)
        with Workspace(path) as work:
            work.put_meta('usage_totals', [{'usage': {'available': True, 'input_tokens': 10, 'cached_input_tokens': 4, 'output_tokens': 2}}])
        out = path / 'turns' / 'test'
        out.mkdir()
        (out / 'user_prompt.txt').write_text('my prompt')
        write_json(out / 'run.json', {'answer': '<script>untrusted</script>', 'execution_status': 'completed'})
        detail = self.server.app.detail(task)
        self.assertEqual(detail['turns'][0]['prompt'], 'my prompt')
        self.assertEqual(detail['report']['model_turns'][0]['usage']['input_tokens'], 10)


if __name__ == '__main__':
    unittest.main()
