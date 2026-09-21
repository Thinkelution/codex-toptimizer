#!/usr/bin/env python3
"""Passive hook: bounded local counts only. No raw prompts, network or model call."""
import json
import math
import os
from pathlib import Path
import sys
import time


def main():
    try:
        data = json.loads(sys.stdin.read(256_001))
        if not isinstance(data, dict) or data.get('hook_event_name') != 'UserPromptSubmit':
            return
        prompt = data.get('prompt', '')
        if not isinstance(prompt, str):
            return
        row = {"time": time.time(), "event": "UserPromptSubmit", "characters": len(prompt),
               "estimated_tokens": math.ceil(len(prompt) / 4), "estimate_only": True,
               "large_prompt": len(prompt) >= 8000}
        root = Path(os.environ.get('PLUGIN_DATA', str(Path.home() / '.local/share/tasklean')))
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        log = root / 'prompt-counts.jsonl'
        if log.is_symlink():
            return
        # Bound storage; avoid raw prompts and transcript inspection entirely.
        if log.exists() and log.stat().st_size > 1_000_000:
            log.unlink()
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(log, flags, 0o600), 'w') as stream:
            stream.write(json.dumps(row) + '\n')
    except (OSError, ValueError, TypeError):
        pass  # A telemetry failure must not interrupt the user's task.


if __name__ == '__main__':
    main()
