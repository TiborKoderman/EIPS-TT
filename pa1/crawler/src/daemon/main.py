"""Daemon entrypoint (compatibility wrapper).

This is a compatibility wrapper that delegates to the unified main.py entry point
with CRAWLER_MODE=websocket.

New code should use:
    python pa1/crawler/src/main.py  # defaults to websocket mode
    
Or explicitly:
    python pa1/crawler/src/main.py --mode websocket
    
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

# Set CRAWLER_MODE=websocket for daemon runtime
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
