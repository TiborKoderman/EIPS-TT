"""Service protocol for crawler worker management endpoints."""

from __future__ import annotations

from typing import Protocol

from api.contracts import GlobalWorkerConfig, WorkerGroupSettings, WorkerLogEntry, WorkerRecord


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
