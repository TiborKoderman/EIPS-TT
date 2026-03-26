"""Typed contracts used by the crawler management API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


VALID_WORKER_STATUSES = {"Active", "Idle", "Paused", "Stopped", "Error"}


def utc_now_iso() -> str:
    """Return current UTC time in RFC3339-like ISO format."""
    return datetime.now(timezone.utc).isoformat()


def default_seed_entries() -> list["SeedEntry"]:
    """Provide sensible initial seeds for local development and crawling tests."""
    return [
        SeedEntry(url="https://www.gov.si/", enabled=True, label="SI Government"),
        SeedEntry(url="https://nijz.si/", enabled=True, label="NIJZ Public Health"),
        SeedEntry(url="https://zdravljenjenadom.si/", enabled=False, label="Health Info"),
        SeedEntry(url="https://www.kclj.si/", enabled=True, label="UKC Ljubljana"),
        SeedEntry(url="https://www.who.int/", enabled=False, label="WHO"),
        SeedEntry(url="https://www.ema.europa.eu/", enabled=False, label="EMA"),
    ]


@dataclass
class WorkerRecord:
    """Current worker state exposed to the manager GUI."""

    id: int
    name: str
    status: str = "Idle"
    status_reason: str = "initialized"
    current_url: str | None = None
    pages_processed: int = 0
    error_count: int = 0
    started_at: str | None = None
    mode: str = "thread"
    pid: int | None = None
    runtime_config: dict[str, str] = field(default_factory=dict)

    def to_view_model(self) -> dict[str, object | None]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "statusReason": self.status_reason,
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
class SeedEntry:
    """One seed URL item with enable flag and optional metadata."""

    url: str
    enabled: bool = True
    label: str = ""

    def to_view_model(self) -> dict[str, object]:
        return {
            "url": self.url,
            "enabled": self.enabled,
            "label": self.label,
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
    seed_entries: list[SeedEntry] = field(default_factory=default_seed_entries)
    queue_mode: str = "both"
    strategy_mode: str = "balanced"
    score_function: str = "rendezvous"
    score_weight_pages: float = 1.0
    score_weight_errors: float = 1.0
    topic_keywords: list[str] = field(
        default_factory=lambda: [
            "medicine",
            "health",
            "doctor",
            "clinic",
            "hospital",
            "treatment",
            "disease",
        ]
    )
    max_frontier_in_memory: int = 50000
    avoid_duplicate_paths_across_daemons: bool = True

    def to_view_model(self) -> dict[str, object]:
        enabled_seed_urls = [entry.url for entry in self.seed_entries if entry.enabled and entry.url.strip()]
        return {
            "maxConcurrentWorkers": self.max_concurrent_workers,
            "requestTimeoutSeconds": self.request_timeout_seconds,
            "crawlDelayMilliseconds": self.crawl_delay_milliseconds,
            "respectRobotsTxt": self.respect_robots_txt,
            "userAgent": self.user_agent,
            "seedUrls": enabled_seed_urls,
            "seedUrlsText": "\n".join(enabled_seed_urls),
            "seedEntries": [entry.to_view_model() for entry in self.seed_entries],
            "queueMode": self.queue_mode,
            "strategyMode": self.strategy_mode,
            "scoreFunction": self.score_function,
            "scoreWeightPages": self.score_weight_pages,
            "scoreWeightErrors": self.score_weight_errors,
            "topicKeywords": list(self.topic_keywords),
            "topicKeywordsText": "\n".join(self.topic_keywords),
            "maxFrontierInMemory": self.max_frontier_in_memory,
            "avoidDuplicatePathsAcrossDaemons": self.avoid_duplicate_paths_across_daemons,
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
    queue_mode: str | None = None
    strategy_mode: str | None = None
    topic_keywords: list[str] = field(default_factory=list)
    avoid_duplicate_paths_across_daemons: bool | None = None
    worker_ids: list[int] = field(default_factory=list)

    def to_view_model(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "maxPagesPerWorker": self.max_pages_per_worker,
            "rateLimitPerMinute": self.rate_limit_per_minute,
            "queueMode": self.queue_mode,
            "strategyMode": self.strategy_mode,
            "topicKeywords": list(self.topic_keywords),
            "topicKeywordsText": "\n".join(self.topic_keywords),
            "avoidDuplicatePathsAcrossDaemons": self.avoid_duplicate_paths_across_daemons,
            "workerIds": list(self.worker_ids),
        }
