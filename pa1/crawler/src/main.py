"""WebSocket daemon entry point.

Usage:
    python main.py
    python main.py --mode websocket
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure imports work when running this file directly from repository root.
CRAWLER_SRC = Path(__file__).resolve().parent
if str(CRAWLER_SRC) not in sys.path:
    sys.path.insert(0, str(CRAWLER_SRC))


def run_unified() -> int:
    """Main entry point routing to websocket daemon runtime."""

    # Backward-compatibility shim: accept --mode websocket and --run-api.
    mode = os.environ.get("CRAWLER_MODE", "").strip().lower()

    if "--mode" in sys.argv:
        mode_idx = sys.argv.index("--mode")
        if mode_idx + 1 >= len(sys.argv):
            print("ERROR: --mode requires a value.")
            return 1
        mode = sys.argv[mode_idx + 1].strip().lower()
        del sys.argv[mode_idx : mode_idx + 2]

    if "--run-api" in sys.argv:
        sys.argv.remove("--run-api")

    if mode and mode != "websocket":
        print(f"ERROR: Unsupported crawler mode '{mode}'.")
        print("       Only websocket daemon mode is supported.")
        print("       Use: python main.py")
        return 1

    from daemon.server import main as run_daemon_runtime

    return run_daemon_runtime()


if __name__ == "__main__":
    raise SystemExit(run_unified())
