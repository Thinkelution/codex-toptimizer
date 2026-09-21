# Reuse task state across prompts

Two different resources are reused:

| Resource | Reuse mechanism | Benefit |
| --- | --- | --- |
| SSH transport | OpenSSH ControlMaster, task-specific control socket, finite ControlPersist | Avoid repeated authentication and connection setup. Primarily latency savings. |
| Parsed datasets | Task-scoped Python worker on the server, private Unix socket | Avoid repeated parsing/transfers; return only requested summaries to the model. Potential context/token reduction. |

An SSH control connection does **not** preserve a shell's working directory, variables, virtualenv activation, or Python objects. Shell commands run in new remote processes. Dataset objects live in the separate worker. This version supports flat CSV/JSON records; arbitrary notebooks, database connections and general Python object persistence are not implemented.

## Example

Run from the plugin directory, or use an absolute path to `scripts/tasklean.py`. Requires local OpenSSH and Python 3.11+ locally and remotely. This implementation targets macOS/Linux and uses Unix sockets. ProxyJump/ProxyCommand from your SSH configuration is respected. Host-key checking remains enabled; establish host trust through your normal SSH workflow.

```bash
python3 scripts/tasklean.py session start \
  --task animal-analysis \
  --host administrator@YOUR_SERVER \
  --root /absolute/server/path/to/datasets \
  --idle-seconds 600 \
  --max-cache-mb 64

python3 scripts/tasklean.py session exec --task animal-analysis -- uname -s

python3 scripts/tasklean.py session data load \
  --task animal-analysis --name animals --path animals.csv

python3 scripts/tasklean.py session data aggregate \
  --task animal-analysis --name animals --group-by species --value score

# On the next prompt, use the same task and dataset name.
python3 scripts/tasklean.py session data describe \
  --task animal-analysis --name animals

python3 scripts/tasklean.py session data sample \
  --task animal-analysis --name animals --limit 3

python3 scripts/tasklean.py session status --task animal-analysis
python3 scripts/tasklean.py session close --task animal-analysis
```

The host can be an SSH alias. `--identity /path/to/key` optionally selects a local key; keys are never uploaded. Git authentication is separate from server SSH authentication. A Cloudflare-proxied web hostname does not itself establish an SSH route: use a reachable SSH address or an explicitly configured SSH proxy alias. The supplied server was tested through its direct SSH address.

The first `start` uploads a versioned, standard-library worker into the remote user's `~/.cache/tasklean/agents/` and starts it. Task metadata, including the server and key *path*, is stored locally under `~/.local/share/tasklean/` with owner-only permissions. `TASKLEAN_STATE_DIR` overrides the metadata location. The local SSH socket is task-specific under `~/.cache/tasklean/ssh/`. Do not share a task name across unrelated jobs.

## Keeping and releasing memory

`load` returns a named handle, row/column counts, source hash and estimated cache memory. Loading the same name/path/version again is a cache hit. `describe`, `sample`, and `aggregate` use the parsed objects already in memory. `status` shows the worker PID, parse count and cache hits so reuse can be verified rather than assumed.

Before dataset access, the worker checks the resolved path and file device, inode, size, mtime and ctime. A changed or missing source invalidates the handle and returns an error. Explicitly call `load` to accept the new version; the next query then sees the new rows. It also checks metadata before and after loading. These are filesystem freshness checks, not transactional snapshots or a cryptographic re-read on every query. Concurrent external writes, remote/network filesystem semantics and unusual metadata-preserving changes are outside the guarantee. Use immutable, versioned inputs for stronger reproducibility.

Only paths inside the configured dataset root are accepted, including after symlink resolution. This is a boundary for **dataset reads**, not a sandbox for `session exec`: explicit shell commands still have the remote SSH user's normal permissions. Arbitrary expressions, SQL and Python code are not accepted by dataset operations.

Limits in this prototype:

- 4 MiB source file, 50,000 rows, 200 columns; JSON must be flat records.
- Default 64 MiB estimated cache budget across datasets, configurable from 1 to 256 MiB. Python object-size accounting is approximate and differs from RSS. Linux also limits worker address space to the cache budget plus 192 MiB for interpreter/parsing overhead.
- Up to 50 returned sample rows or groups, and 2,000 distinct groups during aggregation. Replies are capped at 256 KiB. Large cells may require a smaller sample.
- Group counts and optional numeric sum/mean; numeric aggregation uses floating point and is not intended for exact financial arithmetic.
- Remote shell output is returned up to 32 KiB per stream with an explicit truncation flag. Full output is temporarily spooled locally; use focused commands and redirect intentionally large output on the server.
- Worker idle expiry and SSH ControlPersist default to 600 seconds. These timers are independent: shell activity does not refresh an unused dataset worker. `close` requests immediate worker shutdown and closes that task's SSH master. Parsed data is not saved to disk or restored after expiry.

Use `session data release --task ... --name ...` to free one dataset. If a worker expires or is lost, `status` reports the condition. Close the local session, start a fresh session, and explicitly reload its datasets. There is no silent cache reconstruction or automatic retry of a potentially mutating command after a timeout.

Connection reuse is scoped to this launcher; it does not intercept unrelated `ssh` commands or all tools in Codex. The plugin skill guides the agent to choose the session commands for repeated SSH/data work. It does not guarantee automatic use in every conversation.

## What the live test showed

The [recorded smoke test](server-smoke-2026-09-21.json) used a generated CSV on the provided Linux host:

- Same SSH master and remote worker PID across requests.
- 20,000 rows, 278,021 source bytes, parsed once before the source changed.
- Repeated load hit memory; aggregation returned four groups in a 357-byte JSON response.
- A source edit was rejected as stale; explicit reload produced 20,001 rows and parse count 2.
- The task was explicitly closed after testing.

These byte counts describe one selected summary versus its source, not a general compression ratio or measured LLM token reduction. The first-load and repeated-request timings include network and client startup; they are individual observations. Compare equivalent completed tasks using the benchmark protocol before making cost claims.
