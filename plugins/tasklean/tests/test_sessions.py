import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tasklean.remote_worker import DatasetCache, request_worker
from tasklean import sessions


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / 'animals.csv'
        self.file.write_text('animal,score\ncat,2\ndog,4\ncat,6\n')
        self.cache = DatasetCache(self.root)

    def test_parses_once_reuses_memory(self):
        self.assertFalse(self.cache.load('a', 'animals.csv')['cache_hit'])
        original_rows = self.cache.datasets['a']['rows']
        self.assertTrue(self.cache.load('a', 'animals.csv')['cache_hit'])
        result = self.cache.dispatch({'op': 'aggregate', 'name': 'a', 'group_by': 'animal', 'value': 'score'})
        self.assertEqual(result['groups'][0], {'group': 'cat', 'count': 2, 'sum': 8.0, 'mean': 4.0})
        self.assertIs(original_rows, self.cache.datasets['a']['rows'])
        self.assertEqual(self.cache.parses, 1)

    def test_rejects_stale_until_explicit_reload(self):
        self.cache.load('a', 'animals.csv')
        self.file.write_text('animal,score\nfox,9\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.cache.dispatch({'op': 'sample', 'name': 'a'})
        self.assertNotIn('a', self.cache.datasets)
        self.cache.load('a', 'animals.csv')
        self.assertEqual(self.cache.parses, 2)

    def test_same_length_change_detected(self):
        self.cache.load('a', 'animals.csv')
        old = self.file.stat()
        self.file.write_text(self.file.read_text().replace('cat', 'fox'))
        os.utime(self.file, ns=(old.st_atime_ns, old.st_mtime_ns))
        with self.assertRaises(ValueError):
            self.cache.get('a')  # ctime also changes even when mtime is restored

    def test_root_boundary_and_symlink_escape(self):
        outside = self.root.parent / (self.root.name + '-outside.csv')
        outside.write_text('secret\nvalue\n')
        self.addCleanup(outside.unlink)
        (self.root / 'link.csv').symlink_to(outside)
        for path in (str(outside), '../' + outside.name, 'link.csv'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.cache.load('x', path)

    def test_memory_budget(self):
        cache = DatasetCache(self.root, max_bytes=10)
        with self.assertRaisesRegex(ValueError, 'memory'):
            cache.load('a', 'animals.csv')
        self.assertEqual(cache.used(), 0)

    def test_sample_limit_and_release(self):
        self.cache.load('a', 'animals.csv')
        result = self.cache.dispatch({'op': 'sample', 'name': 'a', 'limit': 1})
        self.assertEqual(len(result['rows']), 1)
        self.assertTrue(result['truncated'])
        with self.assertRaises(ValueError):
            self.cache.dispatch({'op': 'sample', 'name': 'a', 'limit': 51})
        self.cache.dispatch({'op': 'release', 'name': 'a'})
        self.assertEqual(self.cache.used(), 0)

    def test_invalid_csv_and_nested_json(self):
        for text in ['a,a\n1,2\n', 'a,b\n1,2,3\n', 'a,b\n1\n']:
            self.file.write_text(text)
            with self.assertRaises(ValueError):
                self.cache.load('a', 'animals.csv')
        (self.root / 'a.json').write_text('[{"nested": {"x": 2}}]')
        with self.assertRaises(ValueError):
            self.cache.load('a', 'a.json')

    def test_nonfinite_aggregate_rejected(self):
        self.file.write_text('animal,score\ncat,nan\n')
        self.cache.load('a', 'animals.csv')
        with self.assertRaises(ValueError):
            self.cache.dispatch({'op': 'aggregate', 'name': 'a', 'group_by': 'animal', 'value': 'score'})

    def test_flat_json_records(self):
        (self.root / 'a.json').write_text('[{"animal":"cat", "score":2},{"animal":"dog", "score":3}]')
        result = self.cache.load('a', 'a.json')
        self.assertEqual(result['rows'], 2)
        self.assertEqual(result['columns'], ['animal', 'score'])

    def test_no_cross_task_cache(self):
        self.cache.load('a', 'animals.csv')
        other = DatasetCache(self.root)
        with self.assertRaises(ValueError):
            other.get('a')


class SessionTests(unittest.TestCase):
    def test_host_cannot_be_interpreted_as_ssh_option(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {'TASKLEAN_STATE_DIR': temp}), \
                patch('tasklean.sessions.remote') as remote:
            with self.assertRaises(ValueError):
                sessions.start(SimpleNamespace(task='test', host='-option@example.com'))
            remote.assert_not_called()

    def test_ssh_options_task_scoped_and_no_agent_forwarding(self):
        state = {'host': 'u@example.com', 'port': 22, 'idle_seconds': 600,
                 'control_path': '/private/socket', 'identity': '/private/key'}
        argv = sessions.ssh_args(state)
        self.assertIn('ControlPersist=600', argv)
        self.assertIn('ControlPath=/private/socket', argv)
        self.assertIn('ForwardAgent=no', argv)
        self.assertNotIn('StrictHostKeyChecking=no', argv)

    def test_remote_command_arguments_are_quoted(self):
        state = {'host': 'u@example.com', 'port': 22, 'idle_seconds': 600,
                 'control_path': '/private/socket'}
        with patch('tasklean.sessions.master_check', return_value={'connected': True, 'master_pid': 123}), \
                patch('tasklean.sessions.subprocess.run') as run:
            run.return_value.returncode = 0
            result = sessions.remote(state, ['printf', '%s', '$(touch nope); `whoami`'])
        self.assertEqual(run.call_args.args[0][-1], "printf %s '$(touch nope); `whoami`'")
        self.assertTrue(result['transport_reused'])

    def test_state_name_cannot_escape(self):
        with self.assertRaises(ValueError):
            sessions.state_path('../escape')

    def test_worker_subprocess_keeps_pid_and_closes(self):
        # Short temporary path is necessary for macOS Unix socket path limits.
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='tl-') as temp:
            root = Path(temp)
            (root / 'data.csv').write_text('animal,score\ncat,1\ndog,2\n')
            sock = root / '.cache/tasklean/workers/test.sock'
            worker = Path(sessions.__file__).with_name('remote_worker.py')
            proc = subprocess.Popen([sys.executable, str(worker), 'serve', '--session', 'test',
                '--root', str(root), '--idle-seconds', '10'], env={**os.environ, 'HOME': temp},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not sock.exists() and proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                one = request_worker(sock, {'op': 'status'})['result']
                request_worker(sock, {'op': 'load', 'name': 'a', 'path': 'data.csv'})
                two = request_worker(sock, {'op': 'status'})['result']
                self.assertEqual(one['pid'], two['pid'])
                self.assertEqual(two['parse_count'], 1)
                self.assertTrue(request_worker(sock, {'op': 'close'})['ok'])
                proc.wait(timeout=3)
                self.assertFalse(sock.exists())
                self.assertEqual(proc.returncode, 0)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                proc.communicate(timeout=3)

    def test_worker_expires_and_removes_socket(self):
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='tl-') as temp:
            sock = Path(temp) / '.cache/tasklean/workers/expiry.sock'
            worker = Path(sessions.__file__).with_name('remote_worker.py')
            proc = subprocess.Popen([sys.executable, str(worker), 'serve', '--session', 'expiry',
                '--root', temp, '--idle-seconds', '10'], env={**os.environ, 'HOME': temp},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not sock.exists() and proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(sock.exists())
                proc.wait(timeout=13)
                self.assertEqual(proc.returncode, 0)
                self.assertFalse(sock.exists())
            finally:
                if proc.poll() is None:
                    proc.terminate()
                proc.communicate(timeout=3)


if __name__ == '__main__':
    unittest.main()
