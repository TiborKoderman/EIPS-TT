"""Compatibility daemon entrypoint under pa1/crawleer/daemon.

Delegates to pa1/crawler/daemon/main.py.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    crawler_daemon = repo_root / "pa1" / "crawler" / "daemon"
    if str(crawler_daemon) not in sys.path:
        sys.path.insert(0, str(crawler_daemon))

    from main import main as daemon_main

    return daemon_main()


if __name__ == "__main__":
    raise SystemExit(main())
