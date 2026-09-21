# SPDX-License-Identifier: GPL-3.0-or-later
"""Metadata-only repository instruction audit, no file content returned."""
import os
from pathlib import Path
from .core import metrics

SKIP = {'.git', 'node_modules', '.venv', 'venv', '.cache', 'outputs', 'checkpoints', 'data', 'dist', 'build'}


def audit_project(project):
    root = Path(project).resolve()
    if not root.is_dir():
        raise ValueError('Project must be an existing directory')
    records = []
    seen_dirs = 0
    truncated = False
    for directory, children, names in os.walk(root, followlinks=False):
        seen_dirs += 1
        children[:] = sorted(c for c in children if c not in SKIP and not Path(directory, c).is_symlink())
        for name in ('AGENTS.md', 'AGENTS.override.md'):
            path = Path(directory, name)
            if name in names and not path.is_symlink():
                size = path.stat().st_size
                record = {"path": str(path.relative_to(root)), "bytes": size}
                if size <= 256_000:
                    record.update(metrics(path.read_text(errors='replace')))
                records.append(record)
        if seen_dirs >= 1000 or len(records) >= 200:
            truncated = True
            break
    return {"project": str(root), "instruction_files": sorted(records, key=lambda r: -r['bytes']),
            "scan_truncated": truncated, "content_uploaded": False,
            "note": "Inventory, not actual active context. Ancestor/user instructions, plugins and tool schemas are not included; nested AGENTS files are not necessarily all loaded.",
            "suggestions": ["Keep common instructions short and scope specialized instructions to subdirectories.",
                            "Prefer focused searches and relevant file ranges to entire files or logs.",
                            "Use a smaller model for bounded routine tasks; validate on representative tasks."]}
