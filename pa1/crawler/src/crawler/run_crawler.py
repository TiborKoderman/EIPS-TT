"""Multi-threaded crawler runner using DB-backed frontier.

This ties together existing components:
- RobotsPolicyManager (Allow/Disallow/Crawl-delay/Sitemap)
- PerIpRateLimiter (>= 5s per IP)
- Downloader
- PostgresFrontierStore (FRONTIER rows)
- PostgresPageStore (HTML/BINARY/DUPLICATE persistence + dedup)

Usage (example):
  python -m crawler.run_crawler --seed https://example.com/ --workers 4
"""

from __future__ import annotations

import argparse
import threading
from typing import Sequence

from crawler.core.config import load_crawler_config
from crawler.core.politeness import PerIpRateLimiter
from crawler.core.robots import RobotsPolicyManager
from crawler.core.relevance import RelevancePolicy
from crawler.core.scheduler import CrawlWorkerDeps, worker_loop
from db.frontier_store import PostgresFrontierStore
from db.pg_connect import get_connection, load_db_config


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run multi-threaded crawler")
    p.add_argument("--seed", action="append", default=[], help="Seed URL (repeatable)")
    p.add_argument("--workers", type=int, default=None, help="Override worker count")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_crawler_config()
    if args.workers is not None:
        config = type(config)(**{**config.__dict__, "workers": args.workers})  # dataclass copy

    db_cfg = load_db_config()

    def conn_factory():
        return get_connection(db_cfg)

    # seed
    if args.seed:
        with conn_factory() as conn:
            frontier = PostgresFrontierStore(conn)
            for seed in args.seed:
                frontier.enqueue(seed, depth=0, priority=100.0)

    deps = CrawlWorkerDeps(
        config=config,
        robots=RobotsPolicyManager(user_agent=config.user_agent, timeout_seconds=config.robots_timeout_seconds),
        rate_limiter=PerIpRateLimiter(min_interval_seconds=config.min_request_interval_seconds),
        relevance_policy=RelevancePolicy(),
    )

    stop = threading.Event()
    threads: list[threading.Thread] = []
    for i in range(config.workers):
        t = threading.Thread(
            target=worker_loop,
            kwargs=dict(
                conn_factory=conn_factory,
                deps=deps,
                worker_id=f"worker-{i}",
                stop_event=stop,
            ),
            daemon=True,
        )
        t.start()
        threads.append(t)

    # naive: run until frontier empties; workers will idle.
    for t in threads:
        t.join()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

