#!/usr/bin/env python3
"""Command line entry point for the GitLab automation toolkit.

Thin shim: the real implementation lives in ``src/automation/cli.py`` so
the project can also be installed as the ``gitlab-automation`` console script.

    python main.py doctor
    python main.py validate
    python main.py stories create --dry-run
"""

from __future__ import annotations

import sys
from pathlib import Path

# src layout: make the package importable straight from a clone.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from automation.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())