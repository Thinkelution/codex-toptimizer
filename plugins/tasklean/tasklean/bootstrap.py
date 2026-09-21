"""Stable absolute entry point usable from an unpacked source tree or a wheel."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tasklean.cli import main
sys.exit(main())
