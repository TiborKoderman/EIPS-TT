"""Compatibility wrapper for daemon server entrypoint.

Preferred path is pa1/crawler/src/daemon/main.py.
"""

from __future__ import annotations

from daemon.server import main


if __name__ == "__main__":
    raise SystemExit(main())
