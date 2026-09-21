import argparse
import json
from pathlib import Path
from .capture import capture
from .workspace import Workspace, initialize


def output(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def workspace_action(a):
    with Workspace(a.task_dir) as work:
        if a.action == 'status':
            output(work.status(a.query))
        elif a.action == 'find':
            output(work.find(a.query, a.limit))
        elif a.action == 'read':
            output(work.read(a.path, a.symbol, a.start, a.end, a.limit, a.since, a.base_in_context))
        elif a.action == 'remember':
            output(work.remember(a.key, a.text, a.kind, a.evidence))
        elif a.action == 'artifact':
            output(work.artifact(a.id, a.offset, a.limit))
        elif a.action == 'report':
            output(work.report())
        elif a.action == 'run':
            command = a.command[1:] if a.command and a.command[0] == '--' else a.command
            result = capture(work, command, a.timeout)
            output(result)
            if result['execution_status'] != 'completed':
                raise SystemExit(1)


def launch_action(a):
    from .launcher import launch
    from .cli import read_prompt
    prompt = read_prompt(a.prompt_file) if a.prompt_file else a.prompt
    result = launch(a.task_dir, prompt, a.codex_binary, a.model, a.reasoning, a.sandbox, a.timeout, a.execute)
    output(result)
    if a.execute and result['execution_status'] != 'completed':
        raise SystemExit(1)


def chat_action(a):
    from .launcher import launch
    print('Codex LeanTask beta. Enter a prompt; :status shows task state, :quit exits. Each prompt executes Codex using your login.')
    while True:
        try:
            prompt = input('You> ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if prompt == ':quit':
            break
        if prompt == ':status':
            with Workspace(a.task_dir) as work:
                output(work.status())
            continue
        if not prompt:
            continue
        print('Running Codex…')
        result = launch(a.task_dir, prompt, a.codex_binary, a.model, a.reasoning, a.sandbox, a.timeout, True)
        print(result.get('answer') or result['execution_status'])
        output({'status': result['execution_status'], 'usage': result['execution_usage'], 'evidence': result['evidence_path']})


def register(sub):
    q = sub.add_parser('ui', help='Open the local Codex LeanTask browser dashboard')
    q.add_argument('--state-dir', default=str(Path.home() / '.local/share/tasklean/ui'))
    q.add_argument('--port', type=int, default=0, help='Loopback port; default chooses a free port')
    q.add_argument('--no-open', action='store_true')
    q.add_argument('--codex-binary')
    def ui(a):
        from .ui import serve
        serve(a.state_dir, a.port, not a.no_open, a.codex_binary)
    q.set_defaults(func=ui)
    p = sub.add_parser('task', help='Versioned code reads, durable notes and compact command results')
    actions = p.add_subparsers(dest='action', required=True)
    init = actions.add_parser('init')
    init.add_argument('--project', required=True)
    init.add_argument('--task-dir', required=True)
    init.add_argument('--goal', required=True)
    init.set_defaults(func=lambda a: output(initialize(a.project, a.task_dir, a.goal)))
    for action in ('status', 'find', 'read', 'remember', 'run', 'artifact', 'report'):
        q = actions.add_parser(action)
        q.add_argument('--task-dir', required=True)
        q.set_defaults(func=workspace_action)
        if action in {'status', 'find'}:
            q.add_argument('--query', default='', required=action == 'find')
        if action == 'find':
            q.add_argument('--limit', type=int, default=12)
        if action == 'read':
            q.add_argument('--path', required=True)
            q.add_argument('--symbol')
            q.add_argument('--start', type=int, default=1)
            q.add_argument('--end', type=int)
            q.add_argument('--limit', type=int, default=8000)
            q.add_argument('--since', help='Prior receipt for this same selection')
            q.add_argument('--base-in-context', action='store_true', help='Assert that the receiver still has that exact base text')
        if action == 'remember':
            q.add_argument('--key', required=True)
            q.add_argument('--text', required=True)
            q.add_argument('--kind', choices=['requirement', 'decision', 'observation', 'hypothesis'], default='decision')
            q.add_argument('--evidence', help='Project-relative source path')
        if action == 'run':
            q.add_argument('--timeout', type=int, default=120)
            q.add_argument('command', nargs=argparse.REMAINDER)
        if action == 'artifact':
            q.add_argument('--id', required=True)
            q.add_argument('--offset', type=int, default=0)
            q.add_argument('--limit', type=int, default=4000)
    for action, func in [('launch', launch_action), ('chat', chat_action)]:
        q = sub.add_parser(action, help='Continue one Codex task with scoped Codex LeanTask MCP tools')
        q.add_argument('--task-dir', required=True)
        q.add_argument('--codex-binary')
        q.add_argument('--model', help='Omit to use existing Codex settings')
        q.add_argument('--reasoning', choices=['low', 'medium', 'high', 'xhigh'])
        q.add_argument('--sandbox', choices=['read-only', 'workspace-write'], default='read-only')
        q.add_argument('--timeout', type=int, default=300)
        if action == 'launch':
            group = q.add_mutually_exclusive_group(required=True)
            group.add_argument('--prompt')
            group.add_argument('--prompt-file')
            q.add_argument('--execute', action='store_true')
        q.set_defaults(func=func)
    q = sub.add_parser('doctor', help='Check local prerequisites without inference')
    q.add_argument('--codex-binary')
    def diagnose(a):
        from .launcher import doctor
        output(doctor(a.codex_binary))
    q.set_defaults(func=diagnose)
    q = sub.add_parser('mcp', help='Serve task-scoped source and memory tools over stdio')
    q.add_argument('--task-dir', required=True)
    def mcp(a):
        from .mcp_server import serve
        serve(a.task_dir)
    q.set_defaults(func=mcp)
    q = sub.add_parser('demo', help='Run an offline, reproducible beta walkthrough and create an HTML report')
    q.add_argument('--out', required=True, help='New demo directory')
    def demo(a):
        from .demo import run_demo
        output(run_demo(a.out))
    q.set_defaults(func=demo)
