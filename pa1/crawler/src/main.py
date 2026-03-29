"""WebSocket daemon entry point.

Usage:
    python pa1/crawler/src/main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure imports work when running this file directly from repository root.
CRAWLER_SRC = Path(__file__).resolve().parent
if str(CRAWLER_SRC) not in sys.path:
    sys.path.insert(0, str(CRAWLER_SRC))


def main() -> int:
    """Main entry point routing to websocket daemon runtime."""

    from daemon.server import main as run_daemon_runtime

    return run_daemon_runtime()


if __name__ == "__main__":
    raise SystemExit(main())
