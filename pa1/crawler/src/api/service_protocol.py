"""Service protocol for crawler worker management endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from api.contracts import GlobalWorkerConfig, WorkerGroupSettings, WorkerLogEntry, WorkerRecord
from core.frontier import FrontierEntry


class WorkerControlService(Protocol):
    """Abstract worker-control operations used by Flask routes.

    The service boundary is API/daemon-oriented. Workers are execution units and
    do not directly coordinate with database persistence.
    """

    def get_daemon_status(self) -> dict[str, object]:
        ...

    def start_daemon(self) -> dict[str, object]:
        ...

    def stop_daemon(self) -> dict[str, object]:
        ...

    def list_workers(self) -> list[WorkerRecord]:
        ...

    def get_worker(self, worker_id: int) -> WorkerRecord | None:
        ...

    def start_worker(self, worker_id: int) -> bool:
        ...

    def pause_worker(self, worker_id: int) -> bool:
        ...

    def stop_worker(self, worker_id: int) -> bool:
        ...

    def spawn_worker(
        self,
        *,
        name: str | None,
        mode: str,
        seed_url: str | None,
        seed_urls: list[str] | None,
        group_id: int | None,
    ) -> WorkerRecord:
        ...

    def reload_workers(self) -> dict[str, object]:
        ...

    def get_worker_logs(self, worker_id: int, limit: int = 50) -> list[WorkerLogEntry]:
        ...

    def get_worker_detail(self, worker_id: int) -> dict[str, object] | None:
        ...

    def update_worker_runtime_config(self, worker_id: int, values: dict[str, str]) -> bool:
        ...

    def get_global_config(self) -> GlobalWorkerConfig:
        ...

    def update_global_config(self, payload: dict[str, object]) -> GlobalWorkerConfig:
        ...

    def list_groups(self) -> list[WorkerGroupSettings]:
        ...

    def update_group(self, group_id: int, payload: dict[str, object]) -> WorkerGroupSettings | None:
        ...

    def add_seed(self, worker_id: int | None, url: str) -> dict[str, object]:
        ...

    def claim_frontier_url(self, worker_id: int) -> dict[str, object]:
        ...

    def dequeue_frontier_urls(
        self,
        worker_ids: list[int] | None,
        *,
        limit: int = 1,
        daemon_id: str | None = None,
    ) -> dict[str, object]:
        ...

    def complete_frontier_url(
        self,
        worker_id: int,
        url: str,
        lease_token: str | None,
        status: str = "completed",
    ) -> dict[str, object]:
        ...

    def prune_local_frontier(self, worker_id: int, url: str, reason: str | None = None) -> dict[str, object]:
        ...

    def get_statistics(self) -> dict[str, object]:
        ...


@dataclass(frozen=True)
class QueuedUrl:
    """A URL queued for crawling with metadata."""

    url: str
    lease_token: str | None = None  # Lease token for tracking; None in standalone mode
    priority: int = 0
    source_url: str | None = None
    depth: int = 0


class FrontierQueueProvider(Protocol):
    """Abstract frontier queue operations for worker business logic.

    This protocol decouples workers from queue implementation (standalone DB vs WebSocket).
    Workers call methods on this abstraction; actual implementation varies by mode.

    Queue states (using frontier_queue_state enum):
    - QUEUED: Waiting in queue, not yet locked
    - LOCKED: Acquired by worker, but not yet processing
    - PROCESSING: Worker is actively processing
    - COMPLETED: Successfully processed
    - DUPLICATE: Marked as duplicate of another URL
    - FAILED: Processing failed
    """

    async def next_url(self, worker_id: int) -> QueuedUrl | None:
        """Get next URL to process.

        Atomically transitions URL state from QUEUED -> LOCKED.
        Returns None if no URLs available.

        Args:
            worker_id: Requesting worker ID

        Returns:
            QueuedUrl with lease token (for state tracking), or None
        """
        ...

    async def mark_complete(self, worker_id: int, url: str, lease_token: str | None = None) -> bool:
        """Mark URL processing as complete.

        Transitions LOCKED/PROCESSING -> COMPLETED.

        Args:
            worker_id: Worker that processed the URL
            url: Canonical URL that was processed
            lease_token: Lease token from original next_url() call (if applicable)

        Returns:
            True if successfully marked as complete
        """
        ...

    async def mark_failed(
        self, worker_id: int, url: str, error: str, lease_token: str | None = None
    ) -> bool:
        """Mark URL processing as failed.

        Transitions LOCKED/PROCESSING -> FAILED, stores error message.

        Args:
            worker_id: Worker that encountered the failure
            url: Canonical URL that failed
            error: Error message/description
            lease_token: Lease token from original next_url() call (if applicable)

        Returns:
            True if successfully marked as failed
        """
        ...

    async def mark_duplicate(
        self, worker_id: int, url: str, duplicate_of_url: str, lease_token: str | None = None
    ) -> bool:
        """Mark URL as duplicate of another URL.

        Transitions LOCKED/PROCESSING -> DUPLICATE.
        Still adds link edge to represent the duplicate relationship.

        Args:
            worker_id: Worker that detected the duplicate
            url: Canonical URL that is a duplicate
            duplicate_of_url: The canonical URL this duplicates
            lease_token: Lease token from original next_url() call (if applicable)

        Returns:
            True if successfully marked as duplicate
        """
        ...

    async def add_discovered_urls(self, discovered: list[FrontierEntry]) -> int:
        """Add newly discovered URLs to the frontier.

        Bulk enqueue operation for links extracted during crawl.

        Args:
            discovered: List of FrontierEntry items to enqueue

        Returns:
            Number of URLs successfully added (may be less if duplicates skipped)
        """
        ...

    async def get_frontier_stats(self) -> dict[str, object]:
        """Get current frontier queue statistics.

        Returns:
            Dict with keys like:
            - queued: Count of QUEUED entries
            - locked: Count of LOCKED entries
            - completed: Count of COMPLETED entries
            - failed: Count of FAILED entries
            - duplicate: Count of DUPLICATE entries
        """
        ...
