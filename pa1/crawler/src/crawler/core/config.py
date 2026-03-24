"""Configuration for crawler runtime behavior."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CrawlerConfig:
    """Crawler configuration loaded from environment."""

    user_agent: str = "fri-wier-IME-GRUPE"
    workers: int = 4
    min_request_interval_seconds: float = 5.0
    robots_timeout_seconds: float = 15.0


def load_crawler_config() -> CrawlerConfig:
    """Load crawler config from environment variables."""
    return CrawlerConfig(
        user_agent=os.getenv("CRAWLER_USER_AGENT", "fri-wier-EIPS-TT"),
        workers=int(os.getenv("CRAWLER_WORKERS", "4")),
        min_request_interval_seconds=float(
            os.getenv("CRAWLER_MIN_DELAY", "5")),
        robots_timeout_seconds=float(
            os.getenv("CRAWLER_ROBOTS_TIMEOUT", "15")),
    )
