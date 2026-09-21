# Codex TaskLean

Task efficiency for Codex: reviewable prompt preparation, measured execution usage, reusable SSH connections, and task-scoped datasets held in server memory.

The installable plugin lives in [`plugins/tasklean`](plugins/tasklean). Its package is called `tasklean`; the repository is `codex-toptimizer`.

## Start here

```bash
cd plugins/tasklean
python3 scripts/tasklean.py --help
python3 -m unittest discover -s tests -v
```

- [Plugin setup and prompt optimization](plugins/tasklean/README.md)
- [Persistent SSH and in-memory datasets](plugins/tasklean/docs/sessions.md)
- [Recorded server smoke test](plugins/tasklean/docs/server-smoke-2026-09-21.json)
- [Quality and token benchmark protocol](plugins/tasklean/docs/benchmark.md)

The session layer reuses an OpenSSH connection and a separate dataset worker across commands/prompts. It does not preserve shell variables or put an entire dataset into the model's context. The agent refers to a task and dataset handle, then requests small results. Files are checked for changes before reuse. Memory is isolated per task and discarded on close or idle expiry.

**Verified:** local tests and an actual SSH smoke test against the supplied Linux server. One 20,000-row dataset was parsed once and queried repeatedly by the same worker. Editing the source triggered stale-cache rejection. This demonstrates transport/state reuse, not a proven LLM token savings percentage.

**Product status:** local/SSH prototype, no hosted dashboard or billing. Preferred future app hostname: `codex-lean-task.thinkelution.com`; alternate mapped hostname: `codex-toptimizer.thinkelution.com`. No HTTP service or Cloudflare configuration is changed by this project. The worker communicates through SSH and a private Unix socket, not a public endpoint.

Model-based prompt rewriting is optional and uses a separately configured OpenAI API key. Local audit, session, dataset and test commands need no model inference.
