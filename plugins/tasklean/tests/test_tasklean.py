# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tasklean import cli
from tasklean.core import check_candidate, protected_spans, metrics, SECRET
from tasklean.provider import rewrite, RewriteFailure
from tasklean.runner import execute, fingerprint, command
from tasklean.usage import api_usage, codex_usage, compare_runs

ROOT = Path(__file__).resolve().parents[1]
USAGE = {'input_tokens': 100, 'cached_input_tokens': 60, 'output_tokens': 20}


def event(usage=None):
    return json.dumps({'type': 'turn.completed', 'usage': USAGE if usage is None else usage})


def record(variant):
    return dict(variant=variant, original_prompt_sha256='original', execution_prompt_sha256='original',
                project_fingerprint='checkout', codex_version='fake-1', model='test-model', reasoning='medium',
                sandbox='read-only', returncode=0, execution_status='completed', quality_outcome='pass',
                execution_usage=codex_usage(event()), optimizer_usage=api_usage(None, False), elapsed_seconds=1)


class Checks(unittest.TestCase):
    def test_missing_requirements_rejected(self):
        original = 'Please fix this issue.\nDo not change the API.\nUse `x = 2` in src/app.py.\n' * 2
        self.assertTrue(check_candidate(original, 'Fix the issue.'))
        self.assertIn('Do not change the API.', protected_spans(original))

    def test_code_and_numbers_protected(self):
        text = 'Calculate 37.5% using\n```python\nx = 37.5\n```\nSee https://example.com/guide'
        spans = protected_spans(text)
        self.assertIn('```python\nx = 37.5\n```', spans)
        self.assertIn('37.5%', spans)
        self.assertIn('https://example.com/guide', spans)

    def test_shorter_preserving_candidate(self):
        self.assertEqual(check_candidate('Explain sorting. Explain sorting.', 'Explain sorting.'), [])
        self.assertTrue(check_candidate('Explain sorting.', 'Explain sorting.'))

    def test_extra_keep(self):
        self.assertTrue(check_candidate('Make it warm and friendly.', 'Make it warm.', ['friendly']))

    def test_estimate_is_labeled(self):
        self.assertIn('not_model_tokenization', metrics('hello')['token_count_kind'])

    def test_secret_tripwire(self):
        self.assertTrue(SECRET.search('api_key=synthetic_fake_value'))


class UsageTests(unittest.TestCase):
    def test_cache_reasoning_not_double_counted(self):
        result = codex_usage(event({**USAGE, 'reasoning_output_tokens': 10}))
        self.assertEqual(result['total_tokens'], 120)
        self.assertEqual(result['uncached_input_tokens'], 40)

    def test_multiple_turns(self):
        self.assertEqual(codex_usage(event() + '\n' + event())['total_tokens'], 240)

    def test_invalid_events_do_not_crash(self):
        self.assertFalse(codex_usage('bad\n[]\nnull')['available'])

    def test_partial_usage_unknown(self):
        self.assertFalse(codex_usage(event() + '\n' + event({'input_tokens': 5}))['available'])
        self.assertFalse(codex_usage(event({**USAGE, 'cached_input_tokens': 101}))['available'])

    def test_api_unknown_is_not_zero(self):
        self.assertFalse(api_usage(None, True)['available'])
        self.assertEqual(api_usage(None, False)['total_tokens'], 0)

    def test_rewriter_cost_can_exceed_savings(self):
        b, o = record('baseline'), record('optimized')
        o['execution_usage'] = codex_usage(event({'input_tokens': 50, 'cached_input_tokens': 0, 'output_tokens': 20}))
        o['optimizer_usage'] = api_usage({'input_tokens': 70, 'output_tokens': 10}, True)
        result = compare_runs(b, o)
        self.assertTrue(result['comparable'])
        self.assertEqual(result['net_tokens_saved'], -30)

    def test_mismatch_or_failure_prevents_claim(self):
        for key, value in [('model', 'different'), ('project_fingerprint', None), ('quality_outcome', 'fail'),
                           ('execution_status', 'timeout'), ('codex_version', 'other'), ('variant', 'baseline'),
                           ('optimizer_usage', {'available': False})]:
            with self.subTest(key=key):
                b, o = record('baseline'), record('optimized')
                o[key] = value
                self.assertFalse(compare_runs(b, o)['comparable'])


class ProviderTests(unittest.TestCase):
    def request(self, data):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-test-key'}), \
                patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(data).encode())) as mocked:
            result = rewrite('Explain sorting. Explain sorting.', 'explicit-test-model', [])
            sent = json.loads(mocked.call_args.args[0].data)
            self.assertFalse(sent['store'])
            self.assertNotIn('tools', sent)
            return result

    def test_structured_response(self):
        result = self.request({'status': 'completed', 'usage': {'input_tokens': 3, 'output_tokens': 4},
            'output': [{'type': 'message', 'content': [{'type': 'output_text',
                'text': json.dumps({'optimized_prompt': 'Explain sorting.'})}]}]})
        self.assertEqual(result[0], 'Explain sorting.')

    def test_malformed_response_falls_back(self):
        for value in [[], None, {'status': 'completed', 'output': [None]},
                      {'status': 'completed', 'output': []}, {'status': 'incomplete'}]:
            with self.subTest(value=value), self.assertRaises(RewriteFailure):
                self.request(value)

    def test_error_keeps_usage(self):
        with self.assertRaises(RewriteFailure) as caught:
            self.request({'status': 'incomplete', 'usage': {'input_tokens': 12, 'output_tokens': 3}})
        self.assertEqual(caught.exception.usage['input_tokens'], 12)

    def test_network_error_sanitized(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-test-key'}), \
                patch('urllib.request.urlopen', side_effect=OSError('private request contents')), \
                self.assertRaises(RewriteFailure) as caught:
            rewrite('Prompt', 'test-model', [])
        self.assertNotIn('private request contents', str(caught.exception))


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prompt = self.root / 'prompt.txt'
        self.prompt.write_text('Explain sorting. Explain sorting.\n')

    def call(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = cli.main(list(map(str, args)))
        return status, stdout.getvalue(), stderr.getvalue()

    def prepare(self, *extra):
        out = self.root / 'prepared'
        result = self.call('prepare', '--prompt', self.prompt, '--out', out, *extra)
        self.assertEqual(result[0], 0, result[2])
        return out

    def test_local_prepare_private_no_network(self):
        with patch('tasklean.cli.rewrite') as mocked:
            out = self.prepare()
        mocked.assert_not_called()
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((out / 'original.txt').stat().st_mode), 0o600)
        self.assertFalse(json.loads((out / 'plan.json').read_text())['net_savings_measured'])

    def test_html_escaped(self):
        self.prompt.write_text('<script>alert(1)</script>')
        text = (self.prepare() / 'review.html').read_text()
        self.assertNotIn('<script>', text)
        self.assertIn('&lt;script&gt;', text)

    def test_model_skipped_for_short_prompt(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-test-key'}), patch('tasklean.cli.rewrite') as mocked:
            self.prepare('--optimizer', 'model', '--optimizer-model', 'test-model')
        mocked.assert_not_called()

    def test_model_skipped_for_secret(self):
        self.prompt.write_text('api_key=synthetic_fake_value\n' * 100)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-test-key'}), patch('tasklean.cli.rewrite') as mocked:
            self.prepare('--optimizer', 'model', '--optimizer-model', 'test-model')
        mocked.assert_not_called()

    def test_model_missing_key(self):
        with patch.dict(os.environ, {}, clear=True), patch('tasklean.cli.rewrite') as mocked:
            out = self.prepare('--optimizer', 'model', '--optimizer-model', 'test-model', '--min-characters', '0')
        mocked.assert_not_called()
        self.assertIn('not set', json.loads((out / 'plan.json').read_text())['reason'])

    def test_rejected_rewrite_is_still_charged(self):
        self.prompt.write_text('Fix the bug. Do not change the API. Repeat: fix the bug.')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-test-key'}), \
                patch('tasklean.cli.rewrite', return_value=('Fix bug.', {'input_tokens': 50, 'output_tokens': 10})):
            out = self.prepare('--optimizer', 'model', '--optimizer-model', 'test-model', '--min-characters', '0')
        plan = json.loads((out / 'plan.json').read_text())
        self.assertEqual(plan['optimizer_usage']['total_tokens'], 60)
        self.assertEqual((out / 'prepared.txt').read_text(), self.prompt.read_text())

    def test_model_candidate_requires_explicit_acceptance(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-test-key'}), \
                patch('tasklean.cli.rewrite', return_value=('Explain sorting.', {'input_tokens': 50, 'output_tokens': 10})):
            out = self.prepare('--optimizer', 'model', '--optimizer-model', 'test-model', '--min-characters', '0')
        args = ('run', '--prepared', out, '--project', self.root, '--out', self.root / 'run', '--model', 'test-model')
        self.assertEqual(self.call(*args)[0], 2)
        self.assertEqual(self.call(*args, '--accept-model-rewrite')[0], 0)

    def test_tampered_prompt_rejected(self):
        out = self.prepare()
        (out / 'prepared.txt').write_text('Changed task')
        result = self.call('run', '--prepared', out, '--project', self.root, '--out', self.root / 'run', '--model', 'test-model')
        self.assertEqual(result[0], 2)
        self.assertIn('changed after review', result[2])

    def test_dry_run_does_not_execute(self):
        out = self.prepare()
        with patch('tasklean.cli.execute') as mocked:
            result = self.call('run', '--prepared', out, '--project', self.root, '--out', self.root / 'run', '--model', 'test-model')
        mocked.assert_not_called()
        self.assertTrue(json.loads(result[1])['dry_run'])
        self.assertFalse((self.root / 'run').exists())

    def test_grade_cannot_pass_failed_execution(self):
        out = self.root / 'run'
        out.mkdir()
        (out / 'run.json').write_text(json.dumps({'execution_status': 'failed'}))
        self.assertEqual(self.call('grade', '--run', out, '--outcome', 'pass', '--evidence', 'looked fine')[0], 2)

    def test_existing_output_rejected_before_request(self):
        out = self.root / 'prepared'
        out.mkdir()
        with patch('tasklean.cli.rewrite') as mocked:
            result = self.call('prepare', '--prompt', self.prompt, '--out', out)
        self.assertEqual(result[0], 2)
        mocked.assert_not_called()

    def test_hook_silent_and_content_free(self):
        prompt = 'THIS CONTENT MUST NEVER BE LOGGED'
        env = {**os.environ, 'PLUGIN_DATA': str(self.root / 'telemetry')}
        result = subprocess.run([sys.executable, str(ROOT / 'hooks/prompt_metrics.py')],
            input=json.dumps({'hook_event_name': 'UserPromptSubmit', 'prompt': prompt}), text=True,
            capture_output=True, env=env, check=True)
        self.assertEqual(result.stdout + result.stderr, '')
        text = (self.root / 'telemetry/prompt-counts.jsonl').read_text()
        self.assertNotIn(prompt, text)
        self.assertEqual(json.loads(text)['characters'], len(prompt))
        for invalid in ['[]', 'null', '{']:
            result = subprocess.run([sys.executable, str(ROOT / 'hooks/prompt_metrics.py')], input=invalid,
                text=True, capture_output=True, env=env, check=True)
            self.assertEqual(result.stdout + result.stderr, '')

    def test_runner_passes_prompt_as_data(self):
        out = self.root / 'run'
        out.mkdir()
        prompt = 'Literal `touch SHOULD_NOT_EXIST` and $(touch ALSO_NOT)'
        script = 'import sys,json; p=sys.stdin.read(); print(json.dumps({"type":"echo", "prompt":p})); print(' + repr(event()) + ')'
        result = execute([sys.executable, '-c', script], prompt, out, 5)
        self.assertEqual(result['execution_status'], 'completed')
        self.assertEqual(json.loads((out / 'events.jsonl').read_text().splitlines()[0])['prompt'], prompt)
        self.assertEqual(result['execution_usage']['total_tokens'], 120)

    def test_runner_failure_event(self):
        out = self.root / 'run'
        out.mkdir()
        result = execute([sys.executable, '-c', 'print(\'{"type":"turn.failed"}\')'], '', out, 5)
        self.assertEqual(result['execution_status'], 'failed')

    def test_runner_timeout(self):
        out = self.root / 'run'
        out.mkdir()
        result = execute([sys.executable, '-c', 'import time;time.sleep(30)'], '', out, 0.05)
        self.assertEqual(result['execution_status'], 'timeout')
        self.assertFalse(result['execution_usage']['available'])

    def test_git_fingerprint_tracks_untracked_sibling_from_subdir(self):
        repo = self.root / 'repo'
        repo.mkdir()
        def git(*args):
            subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
        git('init')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-m', 'fixture')
        (repo / 'nested').mkdir()
        before = fingerprint(repo / 'nested')
        self.assertIsNotNone(before)
        (repo / 'untracked.txt').write_text('change')
        self.assertNotEqual(before, fingerprint(repo / 'nested'))

    def test_full_cli_with_synthetic_codex(self):
        repo = self.root / 'repo'
        repo.mkdir()
        subprocess.run(['git', '-C', str(repo), 'init'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '--allow-empty', '-m', 'fixture'], check=True, capture_output=True)
        binary = self.root / 'synthetic-codex'
        binary.write_text('#!' + sys.executable + '\nimport sys\n'
            'if "--version" in sys.argv:\n print("synthetic-codex-1")\nelse:\n sys.stdin.read()\n print(' + repr(event()) + ')\n')
        binary.chmod(0o700)
        prepared = self.prepare()
        for variant in ('baseline', 'optimized'):
            out = self.root / variant
            result = self.call('run', '--prepared', prepared, '--project', repo, '--out', out,
                '--variant', variant, '--model', 'synthetic-model', '--codex-binary', binary, '--execute')
            self.assertEqual(result[0], 0, result[2])
            self.assertEqual(json.loads(result[1])['execution_usage']['total_tokens'], 120)
            self.assertEqual(self.call('grade', '--run', out, '--outcome', 'pass',
                                       '--evidence', 'Synthetic integration fixture only.')[0], 0)
        result = self.call('compare', '--baseline', self.root / 'baseline', '--optimized', self.root / 'optimized')
        report = json.loads(result[1])
        self.assertTrue(report['comparable'])
        self.assertEqual(report['net_tokens_saved'], 0)


if __name__ == '__main__':
    unittest.main()
