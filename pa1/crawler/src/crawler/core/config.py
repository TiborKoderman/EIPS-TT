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
    download_timeout_seconds: float = 20.0
    render_timeout_seconds: float = 25.0
    download_pdf_content: bool = False
    pdf_binary_download_hosts: tuple[str, ...] = ()


def load_crawler_config() -> CrawlerConfig:
    """Load crawler config from environment variables."""
    return CrawlerConfig(
        user_agent=os.getenv("CRAWLER_USER_AGENT", "fri-wier-EIPS-TT"),
        workers=int(os.getenv("CRAWLER_WORKERS", "4")),
        min_request_interval_seconds=float(
            os.getenv("CRAWLER_MIN_DELAY", "5")),
        robots_timeout_seconds=float(
            os.getenv("CRAWLER_ROBOTS_TIMEOUT", "15")),
        download_timeout_seconds=float(
            os.getenv("CRAWLER_DOWNLOAD_TIMEOUT", "20")),
        render_timeout_seconds=float(
            os.getenv("CRAWLER_RENDER_TIMEOUT", "25")),
        download_pdf_content=_parse_bool(
            os.getenv("CRAWLER_DOWNLOAD_PDF_CONTENT", "false")),
        pdf_binary_download_hosts=_parse_csv_hosts(os.getenv("CRAWLER_PDF_HOSTS", "")),
    )


def _parse_bool(raw_value: str) -> bool:
    return raw_value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_csv_hosts(raw_value: str) -> tuple[str, ...]:
    if not raw_value:
        return ()
    parts = [p.strip().lower() for p in raw_value.split(",")]
    return tuple(p for p in parts if p)

