"""Deterministic offline beta walkthrough. Never claims measured model savings."""
import html
import json
from pathlib import Path
import subprocess
import sys
from .capture import capture
from .storage import new_directory, private_write, write_json
from .workspace import Workspace, initialize

BUGGY = '''def total(items):
    """Total cart prices; quantity defaults to one."""
    return sum(item["price"] for item in items)
'''
FIXED = '''def total(items):
    """Total cart prices; quantity defaults to one."""
    return sum(item["price"] * item.get("quantity", 1) for item in items)
'''
TESTS = '''import unittest
from cart import total

class CartTests(unittest.TestCase):
    def test_quantity(self):
        self.assertEqual(total([{"price": 7, "quantity": 3}]), 21)
    def test_default_quantity(self):
        self.assertEqual(total([{"price": 7}]), 7)
    def test_empty(self):
        self.assertEqual(total([]), 0)

if __name__ == "__main__":
    unittest.main()
'''


def fixture(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700)
    filler = '\n'.join(f'\ndef unrelated_helper_{i}(value):\n    """Unrelated demo helper {i}; included to exercise focused reads."""\n    return value\n' for i in range(120))
    private_write(directory / 'cart.py', BUGGY + filler)
    private_write(directory / 'test_cart.py', TESTS)
    private_write(directory / '.gitignore', '__pycache__/\n*.pyc\n')
    for args in [('init',), ('add', '.'), ('-c', 'user.name=Codex LeanTask Demo', '-c', 'user.email=demo@example.invalid',
                  '-c', 'commit.gpgsign=false', 'commit', '-m', 'Offline demo fixture')]:
        subprocess.run(['git', '-C', str(directory), *args], check=True, capture_output=True)
    return directory


def run_demo(directory):
    out = new_directory(directory)
    project = fixture(out / 'project')
    task = out / 'task'
    initialize(project, task, 'Fix cart totals to honor quantity while preserving default quantity and empty carts.')
    with Workspace(task) as work:
        scan = work.find('total')
        first = work.read('cart.py', symbol='total')
        work.remember('quantity-bug', 'The original total ignores quantity.', 'observation', 'cart.py')
        failed = capture(work, [sys.executable, '-m', 'unittest', '-v'])
        assert failed['execution_status'] == 'failed', 'Fixture must fail before the scripted fix'
        text = (project / 'cart.py').read_text()
        (project / 'cart.py').write_text(text.replace(BUGGY, FIXED, 1))
        stale = work.status()['notes'][0]['stale']
        assert stale, 'Source edit must invalidate evidence'
        fresh = work.read('cart.py', symbol='total')
        delta = work.read('cart.py', symbol='total', since=fresh['receipt'], base_in_context=True)
        assert delta['mode'] == 'unchanged'
        # Without base-in-context, a known receipt must still return full source.
        restored = work.read('cart.py', symbol='total', since=fresh['receipt'])
        assert restored['mode'] == 'content' and restored['text']
        passed = capture(work, [sys.executable, '-m', 'unittest', '-v'])
        assert passed['execution_status'] == 'completed'
        noisy = capture(work, [sys.executable, '-c',
            'for i in range(1200): print("synthetic progress event", i, "x" * 40)\nprint("END: synthetic output demo completed")'])
        recovered = work.artifact(noisy['artifact'], offset=max(0, noisy['log_bytes'] - 150), limit=150)
        assert 'END: synthetic output demo completed' in recovered['text']
        report = {'kind': 'offline_scripted_demo', 'model_inference_used': False, 'model_tokens_saved': None,
                  'quality_checks': {'initial_test_failure_observed': True, 'tests_pass_after_scripted_fix': True,
                    'stale_note_detected': stale, 'full_read_after_context_loss': restored['mode'] == 'content',
                    'explicit_known_base_reused': delta['mode'] == 'unchanged', 'raw_log_retrievable': True},
                  'source_read': {'whole_file_characters': len(text), 'symbol_text_characters': len(first['text']),
                                  'actual_result_characters': len(json.dumps(first))},
                  'synthetic_command': {'raw_output_bytes': noisy['log_bytes'],
                                        'returned_result_characters': len(json.dumps(noisy)),
                                        'artifact': noisy['artifact']},
                  'scan': scan['scan'], 'operations': work.report(),
                  'project': str(project), 'task_dir': str(task),
                  'limitations': 'The repair is scripted, not performed by a model. This verifies mechanics and selected I/O sizes, not end-to-end LLM savings.'}
    write_json(out / 'report.json', report)
    body = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Codex LeanTask beta · Offline walkthrough</title><style>
body{font:16px/1.6 system-ui;margin:0;background:#f3f5f7;color:#17212d}main{max-width:960px;margin:auto;padding:40px 24px}
.tag{color:#14694b;font-weight:700}h1{font-size:42px;letter-spacing:-1.5px;line-height:1.1}h2{font-size:22px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}.card,pre{background:white;border:1px solid #dae0e8;border-radius:12px;padding:20px}
.num{font-size:32px;font-weight:700}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}li{margin:8px 0}.note{color:#536273}code{background:#e9edf1;padding:2px 5px}
</style><main><div class="tag">TASKLEAN · BETA WALKTHROUGH</div><h1>Keep the task.<br>Return the relevant evidence.</h1>
<p>This local walkthrough exercises code lookup, durable notes, compact logs, and context-safe reads. No account or model inference was used.</p>
<div class="cards">'''
    body += '<div class="card"><div class="num">6 / 6</div>mechanical checks passed</div>'
    body += '<div class="card"><div class="num">' + str(report['source_read']['symbol_text_characters']) + '</div>characters in the selected function<br><span class="note">' + str(len(text)) + ' in the source file</span></div>'
    body += '<div class="card"><div class="num">3</div>cart regression tests pass after the scripted fix</div></div>'
    body += '<h2>What happened</h2><ol><li>Created an isolated demo repository with a quantity bug.</li><li>Read the cart function and recorded source-backed evidence.</li><li>Observed failing tests, applied a scripted correction, and detected that the old observation was stale.</li><li>Verified the corrected tests, full-content fallback, and recoverable compact command output.</li></ol>'
    body += '<p><strong>No model token-savings claim.</strong> The source and log examples are synthetic. Real task usage includes history, tool schemas, reasoning, retries and the optimizer itself.</p>'
    body += '<h2>Try your next step</h2><p>Use the beta guide to initialize a separate task on your repository, then run <code>tasklean chat</code> with your existing Codex login. The demo fixture is available locally for experimentation.</p>'
    body += '<details><summary>Inspect the machine-readable report</summary><pre>' + html.escape(json.dumps(report, indent=2)) + '</pre></details></main></html>'
    private_write(out / 'report.html', body)
    return {'passed': True, 'checks': 6, 'report': str(out / 'report.html'), 'json': str(out / 'report.json'),
            'model_inference_used': False, 'project': str(project), 'task_dir': str(task)}
