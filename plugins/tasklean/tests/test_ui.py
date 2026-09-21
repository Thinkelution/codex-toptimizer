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
