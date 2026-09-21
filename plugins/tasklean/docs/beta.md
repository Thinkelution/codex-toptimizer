# Codex LeanTask 0.3 beta: try it yourself

The beta includes a local browser dashboard, CLI launcher and Codex plugin. It supports a reproducible offline demo and real multi-prompt Codex tasks. It does not require a hosted Codex LeanTask service. macOS/Linux, Python 3.11+, and Git are required. Real model turns additionally require an installed, authenticated Codex CLI.

## 1. Install the beta

Clone the main branch:

```bash
git clone https://github.com/Thinkelution/codex-toptimizer.git
cd codex-toptimizer
python3 -m venv .venv
. .venv/bin/activate
python -m pip install ./plugins/tasklean
codex plugin marketplace add .
codex plugin add tasklean@codex-leantask
tasklean doctor
```

This setup installs two components: the Python launcher/browser UI and the companion Codex plugin. The plugin commands register this repository as a plugin source and install `tasklean@codex-leantask` through Codex’s own CLI. Start a new Codex chat for skill discovery and review/trust its hook before it runs. `pip install` alone installs only the launcher package. The launcher can be used without the companion plugin and does not change global model or sandbox settings. There are no runtime Python dependencies. Package building uses setuptools. You can also run `python3 plugins/tasklean/scripts/tasklean.py` instead of installing the command.

## 2. Open the browser dashboard

```bash
tasklean ui
```

This starts a local server on a free loopback port and opens your browser. Keep the terminal running. Create a task by entering an existing project folder and a goal, or **Import existing task** by entering a folder containing `task.json`. The default UI state lives under `~/.local/share/tasklean/ui`, separate from your source. Use `--state-dir PATH` for another private state location, `--port 8785` for a fixed port, or `--no-open` to print the launch URL without opening a browser.

- **Try the offline demo** creates a disposable example with saved test logs and a stale-evidence note. No inference is involved.
- **Account limits** shows each account-reported quota group, the percentage remaining, and both a reset countdown and local reset date/time. Windows are labeled by their reported duration (weekly, five-hour, or another duration), not assumed from their order. This is shared account capacity, separate from the current task's measured tokens.
- Quota groups stay in a stable order across refreshes, with Codex first.
- **Model** lists the visible models reported by your installed Codex CLI. **Reasoning** follows the selected model’s supported efforts; leaving the model at **Codex default** preserves your Codex configuration. Selecting a model uses its reported default effort unless you explicitly choose another. **Refresh models** reloads the catalog (cached for five minutes, with manual refreshes throttled to five seconds). Failed refreshes keep the last known catalog. Model discovery shares the quota connection and performs no inference.
- Limits refresh every minute while the page is visible, after a task finishes, and using **Refresh**. The backend reuses a private Codex app-server process, caches normal reads for 60 seconds and throttles manual reads to at most one every five seconds. This makes no model call. It uses the same CLI selection and local authentication as the launcher, without reading or sending auth files to the browser. A missing login, API-key account or unsupported CLI can leave limits unavailable. Failed refreshes retain last-known values with a stale label; passing a reset time does not invent a replenished balance. No reset credit is consumed.
- **Run with Codex** sends your prompt through the existing launcher and your Codex account. Follow-up prompts resume the same saved conversation. Read-only is the default; choose **Allow project edits** for coding work.
- The dashboard shows running status and elapsed time, then the completed answer and reported usage. It does not stream individual model/tool events or support cancellation yet. Each turn has a ten-minute timeout.
- Task memory shows the latest eight notes. The latest 30 turn answers and 20 command receipts are shown; older records remain in the task directory. Prompts from older CLI releases may not appear, but their saved answers do.
- Command logs can be opened and paged through. Actual input, cached input and output token counts are shown separately; cached input is already included in input. Missing usage is not treated as zero or savings.
- Closing the browser does not stop a running turn. Let turns finish before stopping the launcher. Ctrl+C closes the dashboard; accepted turns finish within their timeout before the process exits.

The server binds only to `127.0.0.1`. Its launch URL contains a per-process access token in the URL fragment, removed from the address bar after loading and retained only in that tab's session storage. API requests require that token, the exact local host and a same-origin request when an Origin header is present. No CORS access, telemetry upload, public hosting, credential sharing, or global Codex configuration changes are enabled. Do not publish or reverse-proxy this local beta. It is a single-user tool, not a multi-user web service. Restarting the server generates a new token; use the newly printed launch URL to reconnect.

### CLI alternative: run the offline walkthrough

```bash
tasklean demo --out /tmp/tasklean-demo-01
```

Open the `report.html` path printed by the command. Use a new output directory each time. The demo creates its own Git repository, never edits your application, and needs no API key, Codex login, or model inference. It checks:

1. A generated cart fixture initially fails its regression test.
2. A Python function can be read without returning its unrelated neighbors.
3. Editing the source marks a source-backed observation stale.
4. A default read returns complete relevant content even if a prior receipt exists.
5. Explicitly asserting a known base allows an unchanged response.
6. Tests pass after a scripted correction, and full noisy output is retrievable from its artifact.

The correction is scripted. This validates mechanics and selected I/O sizes; it is not an AI coding or savings benchmark. The HTML and JSON reports say so explicitly.

## 3. Start a real coding task

Use an existing Git repository and a **new task directory outside it**. For a first trial, use a disposable checkout.

```bash
tasklean task init \
  --project /absolute/path/to/your/repository \
  --task-dir /tmp/tasklean-my-app \
  --goal 'Fix cart totals while preserving the public API and regression coverage.'

tasklean launch \
  --task-dir /tmp/tasklean-my-app \
  --prompt 'Find the cart total calculation and explain how quantity is handled.'
```

`launch` defaults to a preview. Add `--execute` to make a model call. It uses your existing Codex authentication and configured model. Override with `--model` or `--reasoning` only when you intend to change them. This can consume your Codex account allowance or API usage according to your existing configuration.

For ongoing interaction:

```bash
tasklean chat --task-dir /tmp/tasklean-my-app --sandbox workspace-write
```

Enter a prompt and wait for the result. Enter another prompt to continue the **same Codex conversation**. `:status` shows task state and `:quit` leaves the chat loop. The stored conversation ID and notes remain available for the next invocation. The launcher never uses `resume --last`; it resumes only that task's recorded ID. Concurrent launcher turns against one task directory are rejected.

The default sandbox is read-only; `workspace-write` enables coding changes under the normal Codex controls. The task directory is passed as an additional writable directory so the command wrapper can retain logs. No approval-bypass flags are used. Existing configuration, rules, enabled tools and their costs remain relevant.

The launcher supplies five task-scoped MCP tools through per-invocation configuration: `tasklean_status`, `tasklean_find`, `tasklean_read`, `tasklean_remember`, and `tasklean_artifact`. The MCP server has **no command execution tool**. Shell commands remain under the host's normal execution controls. The first prompt includes brief workflow guidance; subsequent prompts are sent without repeatedly prepending that guidance.

## 4. Exercise each feature directly

```bash
# Find a file or Python function name.
tasklean task find --task-dir /tmp/tasklean-my-app --query total

# Read an exact Python symbol. Dotted class/method names are supported.
tasklean task read --task-dir /tmp/tasklean-my-app --path cart.py --symbol total

# JavaScript/TypeScript and other text sources currently use line reads.
tasklean task read --task-dir /tmp/tasklean-my-app --path src/cart.ts --start 10 --end 70

# Retain a concise observation with source evidence.
tasklean task remember --task-dir /tmp/tasklean-my-app \
  --key quantity-behavior --kind observation \
  --text 'Cart totals multiply unit price by quantity.' --evidence cart.py

# Execute an already-authorized command, save the full bounded log,
# and receive an execution receipt and compact output.
tasklean task run --task-dir /tmp/tasklean-my-app -- python3 -m unittest -v

# Retrieve more of the log using the artifact ID returned by task run.
tasklean task artifact --task-dir /tmp/tasklean-my-app --id ARTIFACT_ID --offset 0 --limit 4000

# Inspect state or export metadata-only diagnostics to a file you choose.
tasklean task status --task-dir /tmp/tasklean-my-app
tasklean task report --task-dir /tmp/tasklean-my-app > tasklean-report.json
```

`task run` executes on every invocation: it never treats a previous success as permission to skip required checks. A nonzero exit, timeout, or output-limit stop returns failure. The raw command log is capped at 8 MiB; reaching that limit stops the command and marks the artifact incomplete. The default timeout is 120 seconds. Summaries prioritize error/status lines and the output tail but do not replace inspecting relevant raw evidence.

For tiny outputs, receipt metadata can be larger than the raw log. Prefer the native execution tool for trivial commands when retaining a receipt has no value. Shell commands are not automatically intercepted; the agent must choose the wrapper. The same applies to focused reads: native tools remain available.

### Context-safe deltas

Each complete read returns a receipt. Passing `--since RECEIPT --base-in-context` asserts that the receiver still has the exact earlier source text for that same selection. Only then may the reader return a smaller diff or `unchanged`. An unknown receipt, a different selection, or omission of the assertion returns complete relevant content. A truncated read cannot be a delta base. After compaction, a new conversation, or uncertainty, omit the assertion and request a fresh focused read. The beta cannot prove what a model remembers; it defaults conservatively.

### Scope and freshness

Source reads are constrained to the chosen project; traversal, escaping symlinks, common secret-file paths, and binary files are rejected. This is a scope boundary, not a complete credential scanner. Read content is sent to Codex when requested. Exact symbol extraction currently supports Python ASTs, not all languages. Other source languages use path lookup and line ranges; repository-wide text search remains available through native tools.

The source index uses file metadata for cache freshness and reads changed files. A read hashes current source and detects changes during the read. Indexing is bounded to 2,000 candidate files, 1,000 directories and 64 MiB of changed source per scan; truncation is reported. Individual files are limited to 2 MiB and returned text to 16,000 characters. Oversized MCP results return a narrowing error instead of dumping unlimited context. Notes allow at most 100 entries and reject new keys at that limit; existing requirements are not silently evicted. Read receipts retain the latest 200, with full-read fallback for evicted bases.

Evidence-backed notes are marked stale after their source changes or disappears. Notes without evidence are not automatically validated. They are labeled requirements, decisions, observations or hypotheses; labels never grant authorization or override current user instructions. Command fingerprints are partial repository evidence, not a complete environment snapshot or basis for automatic test-result reuse.

## 5. Understand the measurements

`task report` distinguishes:

- Characters in referenced source/logs and returned Codex LeanTask results. These are diagnostic counts, not tokens saved, and omit native tools and schema/history overhead.
- Actual Codex-reported input, cached-input and output tokens for launcher turns. Cached input is included in input, not added again. Failed turns and missing usage remain visible.

There is no guaranteed savings percentage. No dollar or subscription-quota estimate is inferred from raw token totals. A real comparison requires matched tasks, quality checks, all failures/retries and the optimizer's overhead. See [benchmark protocol](benchmark.md).

The [first real two-prompt smoke test](beta-live-smoke-2026-09-21.json) resumed the same conversation, exercised MCP reads/notes and compact commands, and passed five regression tests plus independent acceptance checks. It used 310,588 total reported tokens, including 264,192 cached input tokens, under the existing Codex configuration. There was no baseline; these numbers prove instrumentation, not savings. Existing runtime context was substantial.

## Beta coverage

| Capability | Status |
| --- | --- |
| Local browser dashboard | Implemented; offline browser walkthrough, imported live history, HTTP/subprocess integration tests |
| Multi-prompt Codex launcher and chat loop | Implemented and live-tested |
| Focused Python symbols and general line reads | Implemented; indexed lookup is bounded |
| Durable notes with source freshness | Implemented |
| Opt-in deltas with full-content fallback | Implemented; receiver assertion required |
| Compact commands and artifact retrieval | Implemented; no automatic interception |
| SSH connection and remote dataset reuse | Implemented; see [sessions guide](sessions.md) |
| Model-assisted prompt rewriting | Optional existing feature; requires separate API setup |
| Automatic test skipping, dependency-aware verification | Not implemented |
| General JavaScript/TypeScript AST/refactoring support | Not implemented |
| Automatic model routing or whole-history control | Not implemented |
| Hosted dashboard, billing and public MCP service | Not implemented |

## Troubleshooting and feedback

- **Codex not found:** use `--codex-binary /absolute/path/to/codex`; the launcher also recognizes the bundled macOS app executable.
- **Authentication/configuration error:** verify the Codex CLI normally first. Codex LeanTask does not bypass or replace login.
- **MCP startup failed:** check task-directory permissions and the printed evidence directory's `stderr.log`. The scoped MCP server is required, so startup failure is surfaced.
- **A command fails or times out:** inspect its artifact before retrying. A timeout does not establish that a mutation failed to happen.
- **A read is stale, truncated, or missing:** request a narrower fresh read. Use native tools for unsupported languages/files, preserving the same permission boundaries.
- **Model run hangs or is interrupted:** the launcher retains available logs and usage. Do not auto-retry a mutating prompt.

For feedback, share your version, platform, exact reproduction command, expected/actual behavior, and a reviewed `task report`. Full task directories contain prompts, answers, notes, source snapshots and logs; they are private local artifacts, not default share bundles. The beta performs no analytics upload. OpenAI/Codex services still receive information used in explicitly launched model tasks.

Run the suite from the repository with `cd plugins/tasklean && python3 -m unittest discover -s tests -v`. CI also installs the package and runs the offline demo on Python 3.11–3.13.

The account-limit integration uses the documented [Codex app-server account/rateLimits/read endpoint](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt). It preserves multiple quota groups, prefers `rateLimitsByLimitId`, and falls back to the legacy `rateLimits` field. Missing percentages and reset times remain unknown. Account-limit snapshots stay in process memory and are not written to task reports or sent to the public website.

## Organize and delete tasks

The sidebar groups tasks by their resolved project folder. Expand or collapse a project, use its **New task** button, or choose a recent folder when creating a task. The New task dialog defaults to the last folder entered in this browser, or your home folder on first use. A project’s New task button uses that project’s folder. Folders with the same name stay separate; their full paths appear in the sidebar.

Use **Delete task** on an idle task to move it to **Recently deleted**. Confirm the dialog to remove it from the active project list. Restore it from **Recently deleted**; the deletion state survives dashboard restarts. This is a recoverable dashboard deletion: project files, task conversations, notes, and logs remain on disk, and reimporting the task restores it. A running dashboard turn must finish before deletion. Deletion does not reclaim disk space or delete the underlying Codex conversation.

## What the efficiency claim means

Tokens are the pieces of text a model reads and writes. Codex LeanTask gives Codex a more organized workbench: selected code reads, short task notes, and compact command output with full logs retained for inspection. For repeated work, authorized SSH connections and parsed datasets can also be reused.

For example, “fix the cart total” followed by “add a regression test” may need only the relevant function, a note about quantity behavior, and a test summary. Avoiding repeated large file/log output can reduce context. The agent still has to select those tools. A continued task is not unique to LeanTask: ordinary Codex supports continued conversations and efficient tools too.

The launcher may use fewer, equal or more tokens than running the same task directly in Codex. Its instructions, tool definitions, notes and receipts also cost context. No controlled comparison has established net savings yet. Compare identical tasks, starting code, model and quality requirements across the whole prompt sequence, counting retries and overhead. Token counts alone do not establish dollar savings or reduced weekly allowance consumption.

The app’s display name is **Codex LeanTask**. The package, command, plugin ID and existing task-state paths remain `tasklean` for compatibility.

## Send feedback

Use **Send feedback** in the sidebar. The form sends your message, optional rating/email, app version, and a submission reference to Thinkelution only after you press **Send to Thinkelution**. It does not attach task files, prompts, logs, usage, or credentials. Errors retain your text so you can retry; repeat submissions with the same reference are deduplicated. This optional action uses HTTPS but makes no model call. Read the [feedback notice](https://codex-lean-task.thinkelution.com/#feedback-privacy).

## License and roadmap

This beta is GPLv3-or-later; see [LICENSE](../LICENSE). Cross-login task resume, hosted sync, storage, and recovery are in development. The existing launcher resumes local tasks with the available Codex login; it cannot currently move a Codex conversation between accounts. Hosted services may be offered for a fee separately from the GPL app.
