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
    download_binary_content: bool = False
    store_large_binary_content: bool = False
    large_binary_threshold_bytes: int = 5_000_000
    frontier_in_memory_limit: int = 50_000
    topic_keywords: tuple[str, ...] = (
        "medicine",
        "health",
        "doctor",
        "clinic",
        "hospital",
        "treatment",
        "disease",
    )


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
        download_binary_content=_parse_bool(
            os.getenv("CRAWLER_DOWNLOAD_BINARY_CONTENT", "false")),
        store_large_binary_content=_parse_bool(
            os.getenv("CRAWLER_STORE_LARGE_BINARY_CONTENT", "false")),
        large_binary_threshold_bytes=int(
            os.getenv("CRAWLER_LARGE_BINARY_THRESHOLD_BYTES", "5000000")),
        frontier_in_memory_limit=int(
            os.getenv("CRAWLER_FRONTIER_IN_MEMORY_LIMIT", "50000")),
        topic_keywords=tuple(
            kw.strip()
            for kw in os.getenv(
                "CRAWLER_TOPIC_KEYWORDS",
                "medicine,health,doctor,clinic,hospital,treatment,disease",
            ).split(",")
            if kw.strip()
        ),
    )


def _parse_bool(raw_value: str) -> bool:
    return raw_value.strip().lower() in {"1", "true", "yes", "y", "on"}
