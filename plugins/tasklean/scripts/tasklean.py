#!/usr/bin/env python3
"""Relocatable entry point; works without pip installation."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tasklean.cli import main
raise SystemExit(main())
