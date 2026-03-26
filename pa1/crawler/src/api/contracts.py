"""Typed contracts used by the crawler management API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


VALID_WORKER_STATUSES = {"Active", "Idle", "Paused", "Stopped", "Error"}


def utc_now_iso() -> str:
    """Return current UTC time in RFC3339-like ISO format."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkerRecord:
    """Current worker state exposed to the manager GUI."""

    id: int
    name: str
    status: str = "Idle"
    current_url: str | None = None
    pages_processed: int = 0
    error_count: int = 0
    started_at: str | None = None
    mode: str = "mock"
    pid: int | None = None
    runtime_config: dict[str, str] = field(default_factory=dict)

    def to_view_model(self) -> dict[str, object | None]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "currentUrl": self.current_url,
            "pagesProcessed": self.pages_processed,
            "errorCount": self.error_count,
            "startedAt": self.started_at,
            "mode": self.mode,
            "pid": self.pid,
        }


@dataclass
class WorkerLogEntry:
    """Structured worker log item."""

    timestamp_utc: str
    level: str
    message: str

    def to_view_model(self) -> dict[str, str]:
        return {
            "timestampUtc": self.timestamp_utc,
            "level": self.level,
            "message": self.message,
        }


@dataclass
class GlobalWorkerConfig:
    """Global runtime settings shared by workers."""

    max_concurrent_workers: int = 4
    request_timeout_seconds: int = 20
    crawl_delay_milliseconds: int = 300
    respect_robots_txt: bool = True
    user_agent: str = "EIPS-TT-Crawler/1.0"
    seed_urls: list[str] = field(default_factory=list)

    def to_view_model(self) -> dict[str, object]:
        return {
            "maxConcurrentWorkers": self.max_concurrent_workers,
            "requestTimeoutSeconds": self.request_timeout_seconds,
            "crawlDelayMilliseconds": self.crawl_delay_milliseconds,
            "respectRobotsTxt": self.respect_robots_txt,
            "userAgent": self.user_agent,
            "seedUrls": list(self.seed_urls),
            "seedUrlsText": "\n".join(self.seed_urls),
        }


@dataclass
class WorkerGroupSettings:
    """Group-level worker settings for segmented crawling."""

    id: int
    name: str
    description: str
    enabled: bool = True
    max_pages_per_worker: int | None = None
    rate_limit_per_minute: int | None = None
    worker_ids: list[int] = field(default_factory=list)

    def to_view_model(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "maxPagesPerWorker": self.max_pages_per_worker,
            "rateLimitPerMinute": self.rate_limit_per_minute,
            "workerIds": list(self.worker_ids),
        }
