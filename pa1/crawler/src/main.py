"""Unified crawler entry point supporting standalone and websocket modes.

Usage:
    # Standalone mode (direct DB access):
    python main.py --mode standalone [CLI actions]
    export CRAWLER_MODE=standalone
    python main.py [CLI actions]

    # WebSocket mode (server-managed queue, default):
    python main.py --mode websocket
    python main.py  # defaults to websocket if CRAWLER_MODE not set
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure imports work when running this file directly from repository root.
CRAWLER_SRC = Path(__file__).resolve().parent
if str(CRAWLER_SRC) not in sys.path:
    sys.path.insert(0, str(CRAWLER_SRC))


def run_unified() -> int:
    """Main entry point routing to standalone or websocket mode."""
    
    # Read mode from CLI argument or environment variable
    # Environment variable takes precedence for subprocess invocations
    mode_env = os.environ.get("CRAWLER_MODE", "").strip().lower()
    
    # Check for --mode flag in sys.argv
    mode_cli = None
    if "--mode" in sys.argv:
        mode_idx = sys.argv.index("--mode")
        if mode_idx + 1 < len(sys.argv):
            mode_cli = sys.argv[mode_idx + 1].lower()
            # Remove --mode and its argument from sys.argv so downstream parsers don't see it
            sys.argv.pop(mode_idx)
            sys.argv.pop(mode_idx)
    
    # Determine effective mode
    mode = mode_cli or mode_env or "websocket"  # Default to websocket (daemon mode)
    
    # Validate and dispatch
    if mode == "standalone":
        # Standalone CLI mode: direct DB access for utilities and one-off commands
        from core.standalone_runner import run_cli
        return run_cli()
    
    elif mode == "websocket":
        # WebSocket mode: daemon runtime with server-managed queue
        from daemon.server import main as run_daemon_runtime
        return run_daemon_runtime()
    
    else:
        print(f"ERROR: Unknown CRAWLER_MODE '{mode}'")
        print(f"       Supported modes: 'standalone', 'websocket'")
        print(f"       Set via: --mode={{standalone,websocket}} or CRAWLER_MODE={{standalone,websocket}}")
        return 1


if __name__ == "__main__":
    raise SystemExit(run_unified())
