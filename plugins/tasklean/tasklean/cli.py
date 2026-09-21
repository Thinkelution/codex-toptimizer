import argparse
import difflib
import html
import json
import os
from pathlib import Path
import sys
import subprocess
import time
from . import __version__
from .audit import audit_project
from .core import MAX_PROMPT_BYTES, SECRET, audit_prompt, check_candidate, digest, metrics, protected_spans, tidy
from .provider import RewriteFailure, rewrite
from .runner import command, execute, fingerprint, version
from .storage import new_directory, private_write, write_json
from .usage import api_usage, compare_runs


def read_prompt(path):
    if path == '-':
        text = sys.stdin.read(MAX_PROMPT_BYTES + 1)
    else:
        with Path(path).open(encoding='utf-8') as stream:
            text = stream.read(MAX_PROMPT_BYTES + 1)
    if not text.strip() or len(text.encode()) > MAX_PROMPT_BYTES:
        raise ValueError('Prompt must be nonempty and at most 128 KB')
    return text


def prepare(a):
    if Path(a.out).expanduser().exists():
        raise ValueError('Output directory already exists; choose a new directory')
    if a.min_characters < 0:
        raise ValueError('--min-characters must be nonnegative')
    original = read_prompt(a.prompt)
    keeps = [line for line in Path(a.keep_file).read_text().splitlines() if line] if a.keep_file else []
    if any(k not in original for k in keeps):
        raise ValueError('Every keep-file line must already occur in the original prompt')
    selected = tidy(original)
    method = 'local_outer_blank_line_cleanup'
    reason = 'Local mode; no model request'
    raw_usage, request_sent = None, False
    started = time.monotonic()
    if a.optimizer == 'model':
        if not a.optimizer_model:
            raise ValueError('--optimizer-model is required for model mode')
        if len(original) < a.min_characters:
            reason = 'Skipped: prompt below model rewrite threshold'
        elif SECRET.search(original):
            reason = 'Skipped: possible credential in prompt; no network request'
        elif not os.environ.get('OPENAI_API_KEY'):
            reason = 'Skipped: OPENAI_API_KEY not set; no network request'
        else:
            request_sent = True
            try:
                candidate, raw_usage = rewrite(original, a.optimizer_model, protected_spans(original) + keeps)
                errors = check_candidate(original, candidate, keeps)
                if errors:
                    reason = 'Rejected candidate: ' + ', '.join(errors)
                else:
                    selected, method = candidate, 'model_rewrite'
                    reason = 'Candidate passed literal checks; semantic equivalence still requires review'
            except RewriteFailure as error:
                reason, raw_usage = str(error), error.usage
    if method != 'model_rewrite' and any(k not in selected for k in keeps):
        selected = original
        method = 'unchanged'
    directory = new_directory(a.out)
    private_write(directory / 'original.txt', original)
    private_write(directory / 'prepared.txt', selected)
    diff = ''.join(difflib.unified_diff(original.splitlines(True), selected.splitlines(True),
                                      fromfile='original', tofile='prepared'))
    private_write(directory / 'changes.diff', diff)
    plan = {"version": __version__, "method": method, "reason": reason,
            "original_prompt_sha256": digest(original), "prepared_prompt_sha256": digest(selected),
            "original": audit_prompt(original), "prepared": metrics(selected),
            "optimizer_model": a.optimizer_model if request_sent else None,
            "optimizer_usage": api_usage(raw_usage, request_sent),
            "optimizer_seconds": round(time.monotonic() - started, 3),
            "prompt_estimated_tokens_reduced": metrics(original)['estimated_tokens'] - metrics(selected)['estimated_tokens'],
            "net_savings_measured": False, "requires_rewrite_acceptance": method == 'model_rewrite'}
    write_json(directory / 'plan.json', plan)
    markup = '<!doctype html><meta charset="utf-8"><title>TaskLean prompt review</title><style>body{font:16px system-ui;max-width:1050px;margin:40px auto;padding:0 24px;background:#f8fafc;color:#172033}pre{white-space:pre-wrap;background:white;padding:20px;border:1px solid #dbe2eb;border-radius:10px}h1{letter-spacing:-1px}small{color:#475569}</style>'
    markup += '<h1>TaskLean · Prompt review</h1><p>' + html.escape(reason) + '</p>'
    markup += '<p><b>Prompt estimate: ' + str(plan['original']['estimated_tokens']) + ' → ' + str(plan['prepared']['estimated_tokens']) + '</b><br><small>Characters ÷ 4, not a tokenizer count. No task savings have been measured. Optimizer usage is included below.</small></p>'
    for title, content in [('Original', original), ('Prepared', selected), ('Changes', diff or 'No text changes.'), ('Measurement record', json.dumps(plan, indent=2))]:
        markup += '<h2>' + title + '</h2><pre>' + html.escape(content) + '</pre>'
    private_write(directory / 'review.html', markup)
    print(json.dumps({"directory": str(directory), **plan}, indent=2))


def run(a):
    if a.timeout <= 0:
        raise ValueError('--timeout must be positive')
    directory = Path(a.prepared).resolve()
    plan = json.loads((directory / 'plan.json').read_text())
    original = (directory / 'original.txt').read_text()
    selected = (directory / 'prepared.txt').read_text()
    if digest(original) != plan['original_prompt_sha256'] or digest(selected) != plan['prepared_prompt_sha256']:
        raise ValueError('Prepared files changed after review; prepare them again')
    if a.variant == 'optimized' and plan['requires_rewrite_acceptance'] and not a.accept_model_rewrite:
        raise ValueError('Review changes.diff or review.html, then pass --accept-model-rewrite to use this candidate')
    project = Path(a.project).resolve()
    if not project.is_dir():
        raise ValueError('Project does not exist')
    chosen = original if a.variant == 'baseline' else selected
    argv = command(a.codex_binary, project, a.model, a.reasoning, a.sandbox)
    if not a.execute:
        print(json.dumps({"dry_run": True, "argv": argv, "prompt_via_stdin": True,
                          "variant": a.variant, "prompt": metrics(chosen)}, indent=2))
        return
    out = new_directory(a.out)
    before = fingerprint(project)
    private_write(out / 'submitted.txt', chosen)
    result = execute(argv, chosen, out, a.timeout)
    record = {**result, "variant": a.variant, "project": str(project), "project_fingerprint": before,
              "original_prompt_sha256": digest(original), "execution_prompt_sha256": digest(chosen),
              "model": a.model, "reasoning": a.reasoning, "sandbox": a.sandbox,
              "codex_version": version(a.codex_binary),
              "optimizer_usage": plan['optimizer_usage'] if a.variant == 'optimized' else api_usage(None, False),
              "optimizer_seconds": plan['optimizer_seconds'] if a.variant == 'optimized' else 0,
              "command": argv}
    write_json(out / 'run.json', record)
    print(json.dumps(record, indent=2))
    if result['execution_status'] != 'completed':
        raise SystemExit(1)


def load_run(path):
    directory = Path(path)
    run = json.loads((directory / 'run.json').read_text())
    quality = directory / 'quality.json'
    if quality.exists():
        review = json.loads(quality.read_text())
        if review['run_sha256'] != digest((directory / 'run.json').read_text()):
            raise ValueError('Quality annotation does not match run record')
        run['quality_outcome'] = review['outcome']
    return run


def parser():
    p = argparse.ArgumentParser(prog='tasklean', description='Local efficiency audits and explicit Codex launcher. No guaranteed savings.')
    p.add_argument('--version', action='version', version=__version__)
    s = p.add_subparsers(dest='cmd', required=True)
    from .sessions import register
    register(s)
    from .beta_cli import register as register_beta
    register_beta(s)
    q = s.add_parser('audit', help='Inventory repository instructions locally')
    q.add_argument('--project', default='.')
    q.set_defaults(func=lambda a: print(json.dumps(audit_project(a.project), indent=2)))
    q = s.add_parser('prepare', help='Prepare a prompt and HTML review; never executes its task')
    q.add_argument('--prompt', required=True, help='UTF-8 file, or - for stdin')
    q.add_argument('--out', required=True, help='New private directory for local artifacts')
    q.add_argument('--optimizer', choices=['local', 'model'], default='local')
    q.add_argument('--optimizer-model', help='Explicit Responses API model; no model is silently selected')
    q.add_argument('--min-characters', type=int, default=1600)
    q.add_argument('--keep-file', help='One exact literal/constraint to preserve per line')
    q.set_defaults(func=prepare)
    q = s.add_parser('run', help='Preview a Codex command; --execute starts it')
    q.add_argument('--prepared', required=True)
    q.add_argument('--project', required=True)
    q.add_argument('--out', required=True)
    q.add_argument('--variant', choices=['baseline', 'optimized'], default='optimized')
    q.add_argument('--model', required=True)
    q.add_argument('--reasoning', choices=['low', 'medium', 'high', 'xhigh'], default='medium')
    q.add_argument('--sandbox', choices=['read-only', 'workspace-write'], default='read-only')
    q.add_argument('--codex-binary', default='codex')
    q.add_argument('--timeout', type=int, default=600)
    q.add_argument('--accept-model-rewrite', action='store_true')
    q.add_argument('--execute', action='store_true')
    q.set_defaults(func=run)
    q = s.add_parser('grade', help='Attach an explicit human task-quality result')
    q.add_argument('--run', required=True)
    q.add_argument('--outcome', required=True, choices=['pass', 'fail'])
    q.add_argument('--evidence', required=True, help='Tests/review that establish task success; exit status alone is insufficient')
    q.set_defaults(func=grade)
    q = s.add_parser('compare', help='Compare matched, quality-reviewed run records')
    q.add_argument('--baseline', required=True)
    q.add_argument('--optimized', required=True)
    q.set_defaults(func=lambda a: print(json.dumps(compare_runs(load_run(a.baseline), load_run(a.optimized)), indent=2)))
    return p


def grade(a):
    if not a.evidence.strip():
        raise ValueError('Evidence is required')
    directory = Path(a.run)
    record = json.loads((directory / 'run.json').read_text())
    if a.outcome == 'pass' and record['execution_status'] != 'completed':
        raise ValueError('Cannot mark an incomplete/failed execution as passing')
    write_json(directory / 'quality.json', {"outcome": a.outcome, "evidence": a.evidence,
               "run_sha256": digest((directory / 'run.json').read_text())})
    print('Saved quality annotation; no model inference performed.')


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        args.func(args)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as e:
        print(f'TaskLean: {e}', file=sys.stderr)
        return 2
    return 0

if __name__ == '__main__':
    sys.exit(main())
