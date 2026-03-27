"""Daemon entrypoint (compatibility wrapper).

This wrapper delegates to the crawler websocket entrypoint in ``main.py``.

New code should use:
    python pa1/crawler/src/main.py
    
See pa1/crawler/src/main.py for comprehensive entry point documentation.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

# Ensure imports work when running this file directly from repository root.
CRAWLER_SRC = Path(__file__).resolve().parents[1]
if str(CRAWLER_SRC) not in sys.path:
    sys.path.insert(0, str(CRAWLER_SRC))

# Force websocket daemon mode for compatibility callers using daemon/main.py.
os.environ["CRAWLER_MODE"] = "websocket"

# Load the unified entry point module (avoiding 'main' name collision)
main_module_path = CRAWLER_SRC / "main.py"
spec = importlib.util.spec_from_file_location("unified_main", main_module_path)
if spec is None or spec.loader is None:
    print("ERROR: Could not load unified entry point", file=sys.stderr)
    sys.exit(1)

unified_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unified_main)

if __name__ == "__main__":
    raise SystemExit(unified_main.run_unified())
