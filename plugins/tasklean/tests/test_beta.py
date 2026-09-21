import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tasklean import cli
from tasklean.capture import capture
from tasklean.demo import run_demo
from tasklean.launcher import argv_for, launch
from tasklean.mcp_server import dispatch, serve, TOOLS
from tasklean.workspace import Workspace, initialize


class BetaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.state = self.root / 'state'
        self.source = self.project / 'app.py'
        self.source.write_text('def total(items):\n    return sum(items)\n\nclass App:\n    def run(self):\n        return 42\n')
        initialize(self.project, self.state, 'Preserve all requirements while fixing the app.')
        self.work = Workspace(self.state)
        self.addCleanup(self.work.db.close)

    def test_task_must_be_outside_project(self):
        with self.assertRaises(ValueError):
            initialize(self.project, self.project / 'state', 'test')

    def test_index_handles_changed_and_deleted_files(self):
        self.assertEqual(self.work.find('total')['matches'][0]['symbols'][0]['name'], 'total')
        self.source.write_text('def renamed():\n    return 1\n')
        self.assertEqual(self.work.find('total')['matches'], [])
        self.assertEqual(len(self.work.find('renamed')['matches']), 1)
        self.source.unlink()
        self.assertEqual(self.work.find('renamed')['matches'], [])

    def test_targeted_python_read(self):
        result = self.work.read('app.py', symbol='App.run')
        self.assertIn('return 42', result['text'])
        self.assertNotIn('def total', result['text'])
        self.assertFalse(result['truncated'])

    def test_other_languages_use_lines(self):
        (self.project / 'app.ts').write_text('export const first = 1;\nexport const second = 2;\n')
        self.assertEqual(self.work.read('app.ts', start=2, end=2)['text'], 'export const second = 2;\n')
        with self.assertRaises(ValueError):
            self.work.read('app.ts', symbol='first')

    def test_default_returns_content_even_with_prior_receipt(self):
        first = self.work.read('app.py', symbol='total')
        again = self.work.read('app.py', symbol='total', since=first['receipt'])
        self.assertEqual(again['mode'], 'content')
        self.assertEqual(first['text'], again['text'])

    def test_explicit_base_allows_unchanged(self):
        first = self.work.read('app.py', symbol='total')
        again = self.work.read('app.py', symbol='total', since=first['receipt'], base_in_context=True)
        self.assertEqual(again['mode'], 'unchanged')
        self.assertEqual(again['text'], '')

    def test_wrong_scope_or_unknown_receipt_falls_back(self):
        first = self.work.read('app.py', symbol='total')
        for receipt in (first['receipt'], 'unknown'):
            result = self.work.read('app.py', symbol='App.run', since=receipt, base_in_context=True)
            self.assertEqual(result['mode'], 'content')

    def test_truncated_reads_have_no_delta_receipt(self):
        (self.project / 'long.txt').write_text('hello' * 1000)
        result = self.work.read('long.txt', limit=200)
        self.assertTrue(result['truncated'])
        self.assertNotIn('receipt', result)

    def test_evidence_becomes_stale_but_note_remains(self):
        self.work.remember('verified', 'total uses sum', 'observation', 'app.py')
        self.assertFalse(self.work.status()['notes'][0]['stale'])
        self.source.write_text('def total(items):\n    return 0\n')
        note = self.work.status()['notes'][0]
        self.assertTrue(note['stale'])
        self.assertEqual(note['text'], 'total uses sum')

    def test_notes_persist_between_process_instances(self):
        self.work.remember('constraint', 'Keep the API unchanged', 'requirement')
        with Workspace(self.state) as reopened:
            self.assertEqual(reopened.status()['notes'][0]['key'], 'constraint')

    def test_traversal_sensitive_paths_and_symlinks_rejected(self):
        outside = self.root / 'outside.py'
        outside.write_text('secret = 1')
        (self.project / 'escape.py').symlink_to(outside)
        (self.project / '.env').write_text('synthetic-only')
        (self.project / 'config.py').symlink_to(self.project / '.env')
        for path in ('../outside.py', str(outside), 'escape.py', '.env', 'config.py'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.work.read(path)

    def test_binary_rejected(self):
        (self.project / 'binary.txt').write_bytes(b'hello\x00world')
        with self.assertRaises(ValueError):
            self.work.read('binary.txt')

    def test_empty_file_is_valid_content(self):
        (self.project / 'empty.py').write_text('')
        result = self.work.read('empty.py')
        self.assertEqual(result['mode'], 'content')
        self.assertEqual(result['text'], '')

    def test_note_limit_never_silently_evicts_requirements(self):
        for i in range(100):
            self.work.remember(str(i), 'Preserve requirement ' + str(i), 'requirement')
        with self.assertRaises(ValueError):
            self.work.remember('new', 'extra note')
        self.assertEqual(self.work.status(query='requirement 0')['notes'][0]['text'], 'Preserve requirement 0')
        self.work.remember('0', 'Explicitly updated requirement', 'requirement')

    def test_mcp_batch_requests(self):
        stdout = io.StringIO()
        serve(self.state, io.StringIO(json.dumps([{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'ping'}]) + '\n'), stdout)
        self.assertEqual(len(json.loads(stdout.getvalue())), 2)

    def test_compact_output_keeps_retrievable_log(self):
        result = capture(self.work, [sys.executable, '-c',
            'print("noise\\n" * 2000); print("IMPORTANT_END"); raise SystemExit(3)'])
        self.assertEqual(result['execution_status'], 'failed')
        self.assertEqual(result['returncode'], 3)
        self.assertTrue(result['summary_shortened'])
        raw = self.work.artifact(result['artifact'], result['log_bytes'] - 20, 20)
        self.assertIn('IMPORTANT_END', raw['text'])

    def test_timeout_is_not_a_successful_receipt(self):
        result = capture(self.work, [sys.executable, '-c', 'import time;time.sleep(10)'], timeout=1)
        self.assertEqual(result['execution_status'], 'timeout')
        self.assertFalse(result['log_complete'])

    def test_output_limit_terminates_and_marks_incomplete(self):
        result = capture(self.work, [sys.executable, '-c', 'print("x" * 100000)'], max_log=1024)
        self.assertEqual(result['execution_status'], 'output_limit')
        self.assertEqual(result['log_bytes'], 1024)
        self.assertFalse(result['log_complete'])

    def test_no_shell_interpolation(self):
        result = capture(self.work, [sys.executable, '-c', 'import sys;print(sys.argv[1])', '$(touch surprise)'])
        self.assertIn('$(touch surprise)', result['summary'])
        self.assertFalse((self.project / 'surprise').exists())

    def test_artifact_path_cannot_escape(self):
        with self.assertRaises(ValueError):
            self.work.artifact('../task')

    def test_mcp_exposes_no_command_execution(self):
        self.assertEqual(len(TOOLS), 5)
        self.assertFalse(any('exec' in t['name'] or 'run' in t['name'] for t in TOOLS))
        response = dispatch(self.work, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                       'params': {'name': 'tasklean_run', 'arguments': {}}})
        self.assertTrue(response['result']['isError'])

    def test_mcp_validates_unknown_keys_and_types(self):
        for arguments in ({'path': 'app.py', 'start': True}, {'path': 'app.py', 'execute': 'bad'}):
            response = dispatch(self.work, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                           'params': {'name': 'tasklean_read', 'arguments': arguments}})
            self.assertTrue(response['result']['isError'])

    def test_mcp_stdio_handshake_and_read(self):
        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26'}},
                    {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                     'params': {'name': 'tasklean_read', 'arguments': {'path': 'app.py', 'symbol': 'total'}}}]
        stdout = io.StringIO()
        serve(self.state, io.StringIO('\n'.join(json.dumps(r) for r in requests) + '\n'), stdout)
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]['result']['protocolVersion'], '2025-03-26')
        result = json.loads(responses[1]['result']['content'][0]['text'])
        self.assertIn('sum(items)', result['text'])

    def test_report_does_not_include_note_or_file_contents(self):
        self.work.remember('private', 'PRIVATE_CONTENT')
        self.work.read('app.py')
        report = json.dumps(self.work.report())
        self.assertNotIn('PRIVATE_CONTENT', report)
        self.assertNotIn('sum(items)', report)

    def test_launcher_preview_never_executes(self):
        with patch('tasklean.launcher.execute') as executor:
            result = launch(self.state, 'Explain total', binary='synthetic-codex')
        executor.assert_not_called()
        self.assertTrue(result['dry_run'])
        self.assertNotIn('--ephemeral', result['argv'])
        self.assertFalse(result['global_config_changed'])
        self.assertFalse(any('bypass' in x for x in result['argv']))

    def fake_codex(self):
        path = self.root / 'fake-codex'
        script = '''import sys,json
if '--version' in sys.argv:
    print('synthetic-codex-1')
    raise SystemExit(0)
prompt=sys.stdin.read()
print(json.dumps({'type':'thread.started','thread_id':'00000000-0000-4000-8000-000000000001'}))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'RESUMED' if 'resume' in sys.argv else 'INITIAL'}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':50,'output_tokens':10}}))
'''
        path.write_text('#!' + sys.executable + '\n' + script)
        path.chmod(0o700)
        return str(path)

    def test_launcher_two_turns_resume_same_thread_and_count_both(self):
        binary = self.fake_codex()
        one = launch(self.state, 'First prompt', binary=binary, execute_turn=True)
        two = launch(self.state, 'Second prompt', binary=binary, execute_turn=True)
        self.assertEqual(one['thread_id'], two['thread_id'])
        self.assertTrue(two['resumed'])
        self.assertEqual(two['answer'], 'RESUMED')
        self.assertEqual(len(self.work.report()['model_turns']), 2)
        self.assertEqual((Path(two['evidence_path']) / 'prompt.txt').read_text(), 'Second prompt')

    def test_launcher_concurrency_lock(self):
        with (self.state / 'launch.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'already has a running'):
                launch(self.state, 'Prompt', binary=self.fake_codex(), execute_turn=True)

    def test_offline_demo_passes_without_inference(self):
        result = run_demo(self.root / 'demo')
        self.assertTrue(result['passed'])
        self.assertFalse(result['model_inference_used'])
        report = json.loads(Path(result['json']).read_text())
        self.assertTrue(all(report['quality_checks'].values()))


if __name__ == '__main__':
    unittest.main()
