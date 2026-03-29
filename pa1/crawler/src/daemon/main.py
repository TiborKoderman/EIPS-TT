"""Daemon entrypoint compatibility wrapper.

New code should use:
    python pa1/crawler/src/main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure imports work when running this file directly from repository root.
CRAWLER_SRC = Path(__file__).resolve().parents[1]
if str(CRAWLER_SRC) not in sys.path:
    sys.path.insert(0, str(CRAWLER_SRC))

from main import main as run_main

if __name__ == "__main__":
    raise SystemExit(run_main())
