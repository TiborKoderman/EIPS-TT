"""Daemon entrypoint under pa1/crawler/src/daemon."""

from __future__ import annotations

import sys
from pathlib import Path


CRAWLER_SRC = Path(__file__).resolve().parents[1]
if str(CRAWLER_SRC) not in sys.path:
    sys.path.insert(0, str(CRAWLER_SRC))

from daemon.server import main


if __name__ == "__main__":
    raise SystemExit(main())
