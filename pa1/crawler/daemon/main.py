"""Daemon runtime entrypoint.

Keeps daemon lifecycle code under pa1/crawler/daemon while reusing the API server implementation in src.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    crawler_src = repo_root / "pa1" / "crawler" / "src"
    if str(crawler_src) not in sys.path:
        sys.path.insert(0, str(crawler_src))

    from api_server import main as api_main

    return api_main()


if __name__ == "__main__":
    raise SystemExit(main())
