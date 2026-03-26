"""Standalone entrypoint for crawler worker-management API."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Keep direct execution working from repository root.
CRAWLER_SRC = Path(__file__).resolve().parent
if str(CRAWLER_SRC) not in sys.path:
    sys.path.insert(0, str(CRAWLER_SRC))

from api.app import create_app


def main() -> int:
    app = create_app()

    host = os.getenv("CRAWLER_API_HOST", "127.0.0.1")
    port = int(os.getenv("CRAWLER_API_PORT", "8090"))
    debug = os.getenv("CRAWLER_API_DEBUG", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    app.run(host=host, port=port, debug=debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
