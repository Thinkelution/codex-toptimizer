# Codex LeanTask

**Browser UI:** after installing the package, run `tasklean ui` to create/import tasks, send follow-up prompts, and inspect account limits, usage and saved logs. See the [beta guide](docs/beta.md).

**Start with the [0.3 beta guide](docs/beta.md)** for installation, the offline walkthrough, the multi-prompt launcher, code reads, task memory, compact logs and troubleshooting. The sections below cover the original prompt-preparation workflow.

A local Codex efficiency prototype: inspect instruction size, prepare a reviewable prompt, and compare recorded task usage including the optimizer's overhead.

Version 0.2 also adds [reusable SSH sessions and in-memory datasets](docs/sessions.md). For repeated server/data work, start a named task session, load a dataset once, and query bounded summaries across prompts. The session layer has been tested on the supplied Linux server; prompt-optimization savings still require model benchmarks.

**Status:** local/SSH beta. The new launcher has passed a real two-prompt coding smoke test; there is no real-model A/B savings benchmark yet. The optional API prompt rewriter is tested with mocked responses. This is not yet a validated commercial product.

## What works

| Component | Behavior |
| --- | --- |
| Codex skill | Audits repository instructions and guides prompt preparation and measurement. |
| Passive hook | After installation and hook trust, records prompt length locally. Adds no model context and makes no model call. |
| Local preparation | Removes outer blank lines only. Provides a baseline review workflow, not substantive compression. |
| Optional model preparation | Makes one OpenAI Responses request using an explicitly selected model. Produces a shorter candidate or falls back. |
| Launcher | Previews a `codex exec` invocation; executes only with `--execute`. Supports explicit model and reasoning selection. |
| Comparison | Includes optimizer tokens and rejects unmatched, incomplete, unreviewed, or usage-unknown runs. |

The plugin cannot silently replace every message typed into Codex. The documented `UserPromptSubmit` hook can add context or block submission; it does not document replacing the original prompt or switching its model. Rewriting before submission is provided by the separate launcher. Automatic model routing is not implemented in this version.

## Try it without installing anything

Requires Python 3.11 or newer. The source launcher uses only the Python standard library.

```bash
cd ~/plugins/tasklean
python3 scripts/tasklean.py audit --project /absolute/path/to/your/repository
python3 scripts/tasklean.py prepare \
  --prompt examples/task.txt \
  --out ~/tasklean-runs/first-review
```

Open `~/tasklean-runs/first-review/review.html`. Output directories must be new. The example is deliberately short, so a model rewrite would normally be skipped: spending a second inference on a short prompt may increase total usage.

Every prompt estimate is **characters divided by four**, not model tokenization. The HTML report labels estimates separately from measured API/CLI usage.

## Optional model rewrite

Set `OPENAI_API_KEY` through your preferred local secret-management method. This is an independent API request and can incur API charges; the subsequent Codex execution uses your CLI's authentication. No key is needed for local preparation, audits, or the passive hook.

```bash
python3 scripts/tasklean.py prepare \
  --prompt /absolute/path/to/task.txt \
  --optimizer model \
  --optimizer-model YOUR_RESPONSES_MODEL_ID \
  --out ~/tasklean-runs/model-review
```

Choose a model supporting Responses structured outputs. There is no default optimizer model or claim that a particular model is cheapest. `--min-characters` defaults to 1600; shorter prompts, missing keys, and common credential patterns cause a local fallback. Credential detection is a heuristic, not a redaction or data-loss prevention system. Model mode sends the entire supplied prompt and protected spans to OpenAI, with `store: false` and no tools. It does not upload your repository. `store: false` is not a promise of zero provider retention.

The rewriter must preserve detected code, numbers, paths, and constraint lines. Add exact phrases with `--keep-file /path/to/phrases.txt`, one phrase per line. Literal checks cannot establish semantic equivalence. Review `changes.diff` or `review.html`; using an accepted model rewrite requires `--accept-model-rewrite`. Rejected or failed requests still count toward optimizer usage when the API provides it. Unknown usage prevents a savings claim.

## Preview or execute a task

Install and authenticate the Codex CLI separately. Use an existing Git repository. Replace `YOUR_CODEX_MODEL_ID` with a model available to that CLI/account.

```bash
python3 scripts/tasklean.py run \
  --prepared ~/tasklean-runs/model-review \
  --project /absolute/path/to/repository \
  --out ~/tasklean-runs/optimized-01 \
  --model YOUR_CODEX_MODEL_ID \
  --reasoning medium \
  --variant optimized \
  --accept-model-rewrite
```

This prints a command preview. Add `--execute` to run it. The default sandbox is read-only; choose `--sandbox workspace-write` for a coding task that needs edits. Existing Codex policies and configuration still apply. The launcher does not use permission-bypass flags. Supply `--codex-binary /path/to/codex` if Codex is not on PATH. On this Mac the bundled executable is `/Applications/ChatGPT.app/Contents/Resources/codex`.

The original or prepared prompt goes through stdin, never shell interpolation. Prompt-file changes after preparation are detected. Model and reasoning selection are explicit; support is validated by your Codex CLI. Failed executions and timeouts retain available usage and logs, but cannot qualify as successful savings comparisons.

## Compare quality-matched runs

1. Keep artifacts outside the measured repository. Prepare a prompt once.
2. Run `--variant baseline` against a clean task checkout, using the original prompt.
3. Run `--variant optimized` against a separate checkout with the same starting contents, model, reasoning, sandbox, and Codex version. Do not run the optimized task on changes made by the baseline. You create and manage these checkouts; Codex LeanTask does not reset repositories.
4. Evaluate both against the same tests and acceptance criteria. A process exit code is not task success.
5. Attach the human quality judgments, then compare:

```bash
python3 scripts/tasklean.py grade --run ~/tasklean-runs/baseline-01 \
  --outcome pass --evidence 'Describe the tests and acceptance checks that actually passed.'
python3 scripts/tasklean.py grade --run ~/tasklean-runs/optimized-01 \
  --outcome pass --evidence 'Describe the same checks on the optimized result.'
python3 scripts/tasklean.py compare \
  --baseline ~/tasklean-runs/baseline-01 \
  --optimized ~/tasklean-runs/optimized-01
```

Use `--outcome fail` for a failed result. Evidence is recorded, not independently verified. Annotations cannot be overwritten through the CLI. `run.json` holds actual execution metadata; `quality.json` is bound to that record's hash.

The pairwise comparator sums execution input + output tokens and optimizer input + output tokens. Cached input is already included in input, and reasoning output is not added a second time. Negative savings are reported. Token sums across models are **not dollar costs or Codex subscription quota**.

The checkout fingerprint covers Git HEAD, tracked changes, and untracked files up to a 20 MB budget. It excludes ignored files, external services, global instructions/configuration, and tool versions other than Codex. A missing fingerprint prevents comparison. These checks reduce mismatches; they do not prove identical execution conditions or causal savings. Keep ignored inputs and external state fixed manually.

For a benchmark, repeat representative tasks and count all failed attempts and retries, not just pairs that passed. This version reports individual pairs; it does not automatically aggregate separate retries or calculate a benchmark-wide success-adjusted cost. See [benchmark protocol](docs/benchmark.md).

## Codex plugin installation

For a public installation, run `codex plugin marketplace add .` from the repository root, then `codex plugin add tasklean@codex-leantask`. This registers the repository source and installs the companion plugin. For existing local development, the personal marketplace entry at `~/.agents/plugins/marketplace.json` remains available. Availability in the personal marketplace is not installation or activation. Start a new task after installation for skill discovery.

The hook must be reviewed and trusted in Codex before it runs. It uses `hooks/hooks.json` and the `PLUGIN_ROOT`/`PLUGIN_DATA` variables provided by Codex. It records only a timestamp, event name, prompt character count, rough token estimate, and large-prompt flag. It writes no prompt text, reads no transcript, adds no model context, and makes no network request. It keeps a bounded local log of roughly 1 MB. If `PLUGIN_DATA` is absent, the fallback is `~/.local/share/tasklean`.

Ask Codex: **“Use Codex LeanTask to audit this project's context and explain what can be measured.”** The skill runs only when relevant; it is not an invisible optimizer for all conversations.

## Data and product boundaries

- Prepared artifacts contain full original and rewritten prompts. Execution logs can contain code and other task data. They stay in your selected local output directory with owner-only permissions; do not share them without review.
- Local audits and hooks do not send data to a server. Explicit model rewriting sends the prompt to OpenAI; explicit Codex execution uses your configured Codex services and tools.
- No hosted backend, billing, activation bypass, analytics upload, paid directory listing, or public publication is included.
- A sellable version still needs real quality/cost benchmarks, service authentication and entitlement checks, retention/deletion policy, support, and platform review. Current plugin directory rules restrict digital sales/subscription flows inside plugins; a marketplace checkout should not be assumed. Review the current rules before choosing a commercial distribution path.

## Verify

```bash
cd ~/plugins/tasklean
python3 -m unittest discover -s tests -v
python3 scripts/tasklean.py --help
```

Tests use synthetic prompts, mocked API responses, temporary repositories, and local subprocesses. They do not contact a model or consume API credits. Optional local CLI installation is `python3 -m pip install -e .` in your own virtual environment; the source launcher above needs no package installation.

## References

- [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Building plugins](https://developers.openai.com/plugins/build/plugins)
- [Plugin directory guidelines](https://developers.openai.com/plugins/app-guidelines)
- [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

Implementation checked against local Codex `0.155.0-alpha.9.2` help and official documentation on September 21, 2026. Live API compatibility and live Codex execution remain to be benchmarked.
