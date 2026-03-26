"""Daemon worker-control service with real queue and telemetry behavior."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import secrets
from datetime import datetime, timezone
from collections import defaultdict, deque
import os
from pathlib import Path
from urllib import error, request
import json
from dataclasses import dataclass
from typing import Any

from api.contracts import (
    GlobalWorkerConfig,
    SeedEntry,
    WorkerGroupSettings,
    WorkerLogEntry,
    WorkerRecord,
    default_seed_entries,
    utc_now_iso,
)
from api.service_protocol import WorkerControlService
from core.downloader import DownloadResult, Downloader
from core.frontier import FrontierEntry
from core.link_extractor import LinkExtractor
from db.frontier_store import PostgresFrontierSwapStore
from db.pg_connect import get_connection


@dataclass
class FrontierLease:
    url: str
    worker_id: int
    token: str
    expires_at_monotonic: float
    source: str


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


class DaemonWorkerService(WorkerControlService):
    """Daemon worker manager with API-driven orchestration and telemetry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._daemon_running = False
        self._daemon_started_at: str | None = None
        self._daemon_mode = "single-instance"
        self._daemon_id = os.getenv("CRAWLER_DAEMON_ID", "local-default").strip() or "local-default"
        self._workers: dict[int, WorkerRecord] = {
            1: WorkerRecord(
                id=1,
                name="Worker-1",
                status="Idle",
                status_reason="daemon-initialized",
                current_url=None,
                pages_processed=0,
                error_count=0,
                started_at=None,
                runtime_config={
                    "Retries": "3",
                    "Render JS": "true",
                    "Depth Limit": "6",
                },
            ),
        }
        # Bootstrap next_worker_id from both in-memory workers and persisted history
        self._next_worker_id = self._bootstrap_worker_id_counter()
        self._global_config = GlobalWorkerConfig()
        self._groups: dict[int, WorkerGroupSettings] = {
            1: WorkerGroupSettings(
                id=1,
                name="Local Daemon",
                description="Default local crawler daemon instance.",
                enabled=True,
                max_pages_per_worker=5000,
                rate_limit_per_minute=240,
                queue_mode="server",
                strategy_mode="balanced",
                topic_keywords=["medicine", "health", "clinic"],
                avoid_duplicate_paths_across_daemons=True,
                worker_ids=[1],
            ),
        }
        self._logs: dict[int, deque[WorkerLogEntry]] = defaultdict(lambda: deque(maxlen=500))
        self._local_processes: dict[int, subprocess.Popen[bytes]] = {}
        self._thread_workers: dict[int, tuple[threading.Thread, threading.Event]] = {}
        self._frontier_queue: deque[str] = deque()
        self._known_frontier_urls: set[str] = set()
        self._worker_local_queues: dict[int, deque[str]] = defaultdict(deque)
        self._worker_local_known_urls: dict[int, set[str]] = defaultdict(set)
        self._active_claim_by_worker: dict[int, FrontierLease] = {}
        self._frontier_leases: dict[str, FrontierLease] = {}
        self._completed_tombstones: dict[str, float] = {}
        self._lease_ttl_seconds = max(
            5,
            _coerce_int(os.getenv("CRAWLER_FRONTIER_LEASE_TTL_SECONDS", "30"), 30),
        )
        self._tombstone_ttl_seconds = max(
            30,
            _coerce_int(os.getenv("CRAWLER_FRONTIER_TOMBSTONE_TTL_SECONDS", "600"), 600),
        )

        self._frontier_relay_url = os.getenv("MANAGER_FRONTIER_INGEST_URL", "").strip() or None
        self._frontier_relay_token = os.getenv("MANAGER_FRONTIER_INGEST_TOKEN", "").strip() or None
        self._manager_ingest_url = os.getenv("MANAGER_INGEST_API_URL", "").strip() or None
        self._manager_event_url = os.getenv("MANAGER_EVENT_API_URL", "").strip() or None
        self._manager_api_token = os.getenv("MANAGER_INGEST_API_TOKEN", "").strip() or None
        self._pending_manager_events: deque[dict[str, object | None]] = deque(maxlen=1000)
        self._manager_event_relay_degraded = False
        self._allow_daemon_db_fallback = os.getenv(
            "CRAWLER_DAEMON_ALLOW_DB_FALLBACK", "true"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self._max_extracted_links_per_page = max(
            5,
            _coerce_int(os.getenv("CRAWLER_MAX_EXTRACTED_LINKS_PER_PAGE", "30"), 30),
        )
        self._frontier_swap_refill_batch_size = max(
            20,
            _coerce_int(os.getenv("CRAWLER_FRONTIER_SWAP_REFILL_BATCH_SIZE", "250"), 250),
        )
        self._frontier_db_sync_enabled = _coerce_bool(
            os.getenv("CRAWLER_FRONTIER_DB_SYNC_ENABLED", "true"),
            True,
        )
        self._frontier_remove_terminal_rows = _coerce_bool(
            os.getenv("CRAWLER_FRONTIER_REMOVE_TERMINAL_ROWS", "true"),
            True,
        )
        self._frontier_swap_conn = None
        self._frontier_swap_store: PostgresFrontierSwapStore | None = None
        self._link_extractor = LinkExtractor()

        for worker in self._workers.values():
            self._append_log(worker.id, "Info", f"{worker.name} initialized in {worker.mode} mode.")
        self._init_frontier_swap_store()

        self._simulator_thread = threading.Thread(target=self._simulation_loop, daemon=True)
        self._simulator_thread.start()

    def _bootstrap_worker_id_counter(self) -> int:
        """Calculate next available worker ID, considering both in-memory and persisted workers."""
        max_memory_id = max(self._workers.keys()) if self._workers else 0
        max_persisted_id = self._get_max_worker_id_from_db()
        # Use the higher of the two, then add 1 to avoid conflicts.
        return max(max_memory_id, max_persisted_id) + 1

    def _get_max_worker_id_from_db(self) -> int:
        """Query database for maximum external_worker_id ever used."""
        try:
            conn = get_connection()
            if conn is None:
                return 0
            try:
                with conn.cursor() as cur:
                    # Check all manager tables that persist worker identity.
                    cur.execute(
                        """
                        SELECT GREATEST(
                            COALESCE((SELECT MAX(external_worker_id) FROM manager.worker), 0),
                            COALESCE((SELECT MAX(external_worker_id) FROM manager.worker_log), 0),
                            COALESCE((SELECT MAX(external_worker_id) FROM manager.worker_metric), 0),
                            COALESCE((SELECT MAX(external_worker_id) FROM manager.seed_url), 0)
                        );
                        """
                    )
                    result = cur.fetchone()
                    return int(result[0]) if result and result[0] else 0
            finally:
                conn.close()
        except Exception:
            # Fall back to in-memory IDs if DB isn't reachable.
            return 0

    def get_daemon_status(self) -> dict[str, object]:
        with self._lock:
            return {
                "running": self._daemon_running,
                "startedAt": self._daemon_started_at,
                "mode": self._daemon_mode,
                "workerCount": len(self._workers),
                "activeWorkers": sum(1 for w in self._workers.values() if w.status == "Active"),
                "localProcessCount": len(self._local_processes),
                "frontier": {
                    "inMemoryQueued": len(self._frontier_queue),
                    "knownUrls": len(self._known_frontier_urls),
                    "localQueued": sum(len(queue) for queue in self._worker_local_queues.values()),
                    "activeLeases": len(self._frontier_leases),
                    "tombstones": len(self._completed_tombstones),
                    "leaseTtlSeconds": self._lease_ttl_seconds,
                    "relayEnabled": self._frontier_relay_url is not None,
                },
                "architecture": {
                    "pipelineOrder": "server-api -> daemon -> workers",
                    "workerDirectDb": False,
                    "daemonDbFallbackEnabled": self._allow_daemon_db_fallback,
                },
            }

    def start_daemon(self) -> dict[str, object]:
        with self._lock:
            if self._daemon_running:
                return {
                    "running": True,
                    "alreadyRunning": True,
                    "startedAt": self._daemon_started_at,
                    "note": "Crawler daemon was already running.",
                }
            self._daemon_running = True
            self._daemon_started_at = utc_now_iso()
            for worker in self._workers.values():
                if worker.status == "Stopped":
                    worker.status = "Idle"
                    worker.status_reason = "daemon-started"
                self._append_log(worker.id, "Info", "Daemon started.")
                self._emit_manager_event(
                    "status-change",
                    worker_id=worker.id,
                    payload={"status": worker.status, "reason": "daemon-start"},
                )
            return {
                "running": True,
                "alreadyRunning": False,
                "startedAt": self._daemon_started_at,
                "note": "Crawler daemon started.",
            }

    def stop_daemon(self) -> dict[str, object]:
        with self._lock:
            if not self._daemon_running:
                return {
                    "running": False,
                    "alreadyStopped": True,
                    "stoppedAt": utc_now_iso(),
                    "note": "Crawler daemon was already stopped.",
                }

            self._daemon_running = False
            for worker in self._workers.values():
                worker.status = "Stopped"
                worker.status_reason = "daemon-stopped"
                worker.current_url = None
                worker.pid = None
                self._release_claim_for_worker(worker.id, requeue=True, reason="daemon-stop")
                self._append_log(worker.id, "Info", "Daemon stopped; worker transitioned to Stopped (reason=daemon-stop).")
                self._emit_manager_event(
                    "status-change",
                    worker_id=worker.id,
                    payload={"status": "Stopped", "reason": "daemon-stop"},
                )

            for worker_id in list(self._local_processes):
                self._terminate_process_if_running(worker_id)
            for worker_id in list(self._thread_workers):
                self._stop_thread_worker(worker_id, reason="daemon-stop")

            return {
                "running": False,
                "alreadyStopped": False,
                "stoppedAt": utc_now_iso(),
                "note": "Crawler daemon stopped and worker processes terminated.",
            }

    def list_workers(self) -> list[WorkerRecord]:
        with self._lock:
            return [self._copy_worker(worker) for worker in self._workers.values()]

    def get_worker(self, worker_id: int) -> WorkerRecord | None:
        with self._lock:
            worker = self._workers.get(worker_id)
            return self._copy_worker(worker) if worker else None

    def start_worker(self, worker_id: int) -> bool:
        with self._lock:
            if not self._daemon_running:
                return False
            worker = self._workers.get(worker_id)
            if worker is None:
                return False

            if worker.status == "Active" and self._is_worker_execution_alive_locked(worker_id):
                return True

            if not self._can_start_worker_locked(worker_id):
                worker.status = "Idle"
                worker.status_reason = "max-concurrency-reached"
                self._append_log(
                    worker_id,
                    "Warning",
                    f"Worker start blocked by maxConcurrentWorkers={self._global_config.max_concurrent_workers}.",
                )
                self._emit_manager_event(
                    "status-change",
                    worker_id=worker_id,
                    payload={"status": worker.status, "reason": worker.status_reason},
                )
                return False

            if worker.mode == "thread":
                self._launch_thread_worker(worker_id, worker.current_url)
                return True

            if worker.mode == "local":
                self._launch_local_worker_process(worker_id, worker.current_url)
                return worker.status == "Active"

            worker.status = "Active"
            worker.status_reason = "manually-started"
            worker.started_at = utc_now_iso()
            self._append_log(worker_id, "Info", "Worker started via API.")
            self._emit_manager_event("status-change", worker_id=worker_id, payload={"status": "Active", "reason": "manual-start"})
            return True

    def pause_worker(self, worker_id: int) -> bool:
        with self._lock:
            if not self._daemon_running:
                return False
            worker = self._workers.get(worker_id)
            if worker is None:
                return False
            worker.status = "Paused"
            worker.status_reason = "manually-paused"
            self._release_claim_for_worker(worker_id, requeue=True, reason="worker-paused")
            self._append_log(worker_id, "Info", "Worker paused via API.")
            self._emit_manager_event("status-change", worker_id=worker_id, payload={"status": "Paused", "reason": "manual-pause"})
            return True

    def stop_worker(self, worker_id: int) -> bool:
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                return False
            worker.status = "Stopped"
            worker.status_reason = "manually-stopped"
            worker.current_url = None
            self._release_claim_for_worker(worker_id, requeue=True, reason="worker-stopped")
            self._terminate_process_if_running(worker_id)
            self._stop_thread_worker(worker_id, reason="manual-stop")
            self._append_log(worker_id, "Info", "Worker stopped via API.")
            self._emit_manager_event("status-change", worker_id=worker_id, payload={"status": "Stopped", "reason": "manual-stop"})
            return True

    def spawn_worker(
        self,
        *,
        name: str | None,
        mode: str,
        seed_url: str | None,
        seed_urls: list[str] | None,
        group_id: int | None,
    ) -> WorkerRecord:
        requested_mode = (mode or "thread").strip().lower()
        if requested_mode not in {"local", "thread"}:
            requested_mode = "thread"

        normalized_seed_urls = [url.strip() for url in (seed_urls or []) if url and url.strip()]
        if not normalized_seed_urls:
            normalized_seed_urls = [
                entry.url
                for entry in self._global_config.seed_entries
                if entry.enabled and entry.url.strip()
            ]
        effective_seed_url = seed_url
        if not effective_seed_url and normalized_seed_urls:
            effective_seed_url = normalized_seed_urls[0]

        with self._lock:
            if not self._daemon_running:
                raise RuntimeError("Crawler daemon is not running. Start daemon before spawning workers.")
            worker_id = self._next_worker_id
            self._next_worker_id += 1
            worker_name = name or f"Worker-{worker_id}"

            worker = WorkerRecord(
                id=worker_id,
                name=worker_name,
                status="Idle",
                status_reason="spawned-awaiting-start",
                current_url=effective_seed_url,
                pages_processed=0,
                error_count=0,
                started_at=None,
                mode=requested_mode,
                runtime_config={
                    "Retries": "3",
                    "Render JS": "false",
                    "Depth Limit": "5",
                    "Seed URLs": "\n".join(normalized_seed_urls),
                },
            )
            self._workers[worker_id] = worker
            if group_id is None:
                raise RuntimeError("groupId is required when spawning a worker.")
            if group_id not in self._groups:
                raise RuntimeError(f"Group {group_id} not found.")
            target_group_id = group_id
            self._groups[target_group_id].worker_ids.append(worker_id)

            self._append_log(worker_id, "Info", f"Worker spawned in {requested_mode} mode.")
            if effective_seed_url:
                self._append_log(worker_id, "Info", f"Initial seed assigned: {effective_seed_url}")
            if len(normalized_seed_urls) > 1:
                self._append_log(worker_id, "Info", f"Configured {len(normalized_seed_urls)} seed URLs.")

            self._append_log(
                worker_id,
                "Info",
                "Worker spawned in Idle state; call start-worker to begin crawling.",
            )

            self._emit_manager_event(
                "worker-spawned",
                worker_id=worker_id,
                payload={
                    "name": worker.name,
                    "mode": worker.mode,
                    "groupId": target_group_id,
                    "seedUrl": effective_seed_url,
                },
            )

            return self._copy_worker(worker)

    def reload_workers(self) -> dict[str, object]:
        reloaded = 0
        with self._lock:
            if not self._daemon_running:
                return {
                    "reloadedWorkers": 0,
                    "timestampUtc": utc_now_iso(),
                    "note": "Daemon is stopped; nothing to reload.",
                }
            for worker in self._workers.values():
                if worker.status == "Active":
                    worker.status = "Idle"
                    worker.status_reason = "daemon-reload"
                    worker.current_url = None
                    self._release_claim_for_worker(worker.id, requeue=True, reason="reload")
                    reloaded += 1
                    self._append_log(worker.id, "Info", "Reload: worker state set to Idle.")
            for worker_id in list(self._local_processes):
                self._terminate_process_if_running(worker_id)
            for worker_id in list(self._thread_workers):
                self._stop_thread_worker(worker_id, reason="daemon-reload")
        return {
            "reloadedWorkers": reloaded,
            "timestampUtc": utc_now_iso(),
            "note": "Reload applied. Active workers were reset to Idle.",
        }

    def get_worker_logs(self, worker_id: int, limit: int = 50) -> list[WorkerLogEntry]:
        max_items = max(1, min(limit, 500))
        with self._lock:
            if worker_id not in self._workers:
                return []
            entries = list(self._logs[worker_id])[-max_items:]
            return [WorkerLogEntry(**entry.__dict__) for entry in entries]

    def get_worker_detail(self, worker_id: int) -> dict[str, object] | None:
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                return None

            group_name = None
            for group in self._groups.values():
                if worker_id in group.worker_ids:
                    group_name = group.name
                    break

            logs = [entry.to_view_model() for entry in list(self._logs[worker_id])[-50:]]
            return {
                "worker": worker.to_view_model(),
                "groupName": group_name,
                "runtimeConfig": dict(worker.runtime_config),
                "recentLogs": logs,
            }

    def update_worker_runtime_config(self, worker_id: int, values: dict[str, str]) -> bool:
        normalized = {str(k): str(v) for k, v in values.items()}
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                return False
            worker.runtime_config.update(normalized)
            self._append_log(worker_id, "Info", "Runtime config updated via API.")
            return True

    def get_global_config(self) -> GlobalWorkerConfig:
        with self._lock:
            cfg = self._global_config
            return GlobalWorkerConfig(
                max_concurrent_workers=cfg.max_concurrent_workers,
                request_timeout_seconds=cfg.request_timeout_seconds,
                crawl_delay_milliseconds=cfg.crawl_delay_milliseconds,
                respect_robots_txt=cfg.respect_robots_txt,
                user_agent=cfg.user_agent,
                seed_urls=list(cfg.seed_urls),
                seed_entries=[
                    SeedEntry(url=entry.url, enabled=entry.enabled, label=entry.label)
                    for entry in cfg.seed_entries
                ],
                queue_mode=cfg.queue_mode,
                strategy_mode=cfg.strategy_mode,
                score_function=cfg.score_function,
                score_weight_pages=cfg.score_weight_pages,
                score_weight_errors=cfg.score_weight_errors,
                topic_keywords=list(cfg.topic_keywords),
                max_frontier_in_memory=cfg.max_frontier_in_memory,
                avoid_duplicate_paths_across_daemons=cfg.avoid_duplicate_paths_across_daemons,
            )

    def update_global_config(self, payload: dict[str, object]) -> GlobalWorkerConfig:
        seed_urls: list[str] = []
        seed_entries: list[SeedEntry] = []
        raw_seed_urls = payload.get("seedUrls")
        if isinstance(raw_seed_urls, list) and len(raw_seed_urls) > 0:
            seed_urls = [str(url).strip() for url in raw_seed_urls if str(url).strip()]
        elif "seedUrlsText" in payload:
            seed_urls = [
                line.strip()
                for line in str(payload.get("seedUrlsText", "")).splitlines()
                if line.strip()
            ]

        raw_seed_entries = payload.get("seedEntries")
        if isinstance(raw_seed_entries, list):
            for entry in raw_seed_entries:
                if not isinstance(entry, dict):
                    continue
                url = str(entry.get("url", "")).strip()
                if not url:
                    continue
                seed_entries.append(
                    SeedEntry(
                        url=url,
                        enabled=_coerce_bool(entry.get("enabled"), True),
                        label=str(entry.get("label", "")).strip(),
                    )
                )

        if not seed_entries and seed_urls:
            seed_entries = [SeedEntry(url=url, enabled=True, label="") for url in seed_urls]
        if not seed_entries:
            seed_entries = default_seed_entries()

        queue_mode = str(payload.get("queueMode", self._global_config.queue_mode)).strip().lower()
        if queue_mode not in {"local", "server", "both"}:
            queue_mode = "both"

        strategy_mode = str(payload.get("strategyMode", self._global_config.strategy_mode)).strip().lower()
        if strategy_mode not in {"balanced", "coverage", "focused", "freshness"}:
            strategy_mode = "balanced"

        score_function = str(payload.get("scoreFunction", self._global_config.score_function)).strip().lower()
        if score_function not in {"rendezvous", "round_robin", "weighted"}:
            score_function = "rendezvous"

        try:
            score_weight_pages = float(payload.get("scoreWeightPages", self._global_config.score_weight_pages))
        except (TypeError, ValueError):
            score_weight_pages = self._global_config.score_weight_pages

        try:
            score_weight_errors = float(payload.get("scoreWeightErrors", self._global_config.score_weight_errors))
        except (TypeError, ValueError):
            score_weight_errors = self._global_config.score_weight_errors

        topic_keywords: list[str] = []
        raw_topic_keywords = payload.get("topicKeywords")
        if isinstance(raw_topic_keywords, list):
            topic_keywords = [str(value).strip() for value in raw_topic_keywords if str(value).strip()]
        elif "topicKeywordsText" in payload:
            topic_keywords = [
                line.strip()
                for line in str(payload.get("topicKeywordsText", "")).splitlines()
                if line.strip()
            ]

        if not topic_keywords:
            topic_keywords = list(self._global_config.topic_keywords)

        with self._lock:
            self._global_config = GlobalWorkerConfig(
                max_concurrent_workers=_coerce_int(
                    payload.get("maxConcurrentWorkers"),
                    self._global_config.max_concurrent_workers,
                ),
                request_timeout_seconds=_coerce_int(
                    payload.get("requestTimeoutSeconds"),
                    self._global_config.request_timeout_seconds,
                ),
                crawl_delay_milliseconds=_coerce_int(
                    payload.get("crawlDelayMilliseconds"),
                    self._global_config.crawl_delay_milliseconds,
                ),
                respect_robots_txt=_coerce_bool(
                    payload.get("respectRobotsTxt"),
                    self._global_config.respect_robots_txt,
                ),
                user_agent=str(payload.get("userAgent", self._global_config.user_agent)),
                seed_urls=seed_urls,
                seed_entries=seed_entries,
                queue_mode=queue_mode,
                strategy_mode=strategy_mode,
                score_function=score_function,
                score_weight_pages=score_weight_pages,
                score_weight_errors=score_weight_errors,
                topic_keywords=topic_keywords,
                max_frontier_in_memory=_coerce_int(
                    payload.get("maxFrontierInMemory"),
                    self._global_config.max_frontier_in_memory,
                ),
                avoid_duplicate_paths_across_daemons=_coerce_bool(
                    payload.get("avoidDuplicatePathsAcrossDaemons"),
                    self._global_config.avoid_duplicate_paths_across_daemons,
                ),
            )
            cfg = self._global_config
            return GlobalWorkerConfig(
                max_concurrent_workers=cfg.max_concurrent_workers,
                request_timeout_seconds=cfg.request_timeout_seconds,
                crawl_delay_milliseconds=cfg.crawl_delay_milliseconds,
                respect_robots_txt=cfg.respect_robots_txt,
                user_agent=cfg.user_agent,
                seed_urls=list(cfg.seed_urls),
                seed_entries=[
                    SeedEntry(url=entry.url, enabled=entry.enabled, label=entry.label)
                    for entry in cfg.seed_entries
                ],
                queue_mode=cfg.queue_mode,
                strategy_mode=cfg.strategy_mode,
                score_function=cfg.score_function,
                score_weight_pages=cfg.score_weight_pages,
                score_weight_errors=cfg.score_weight_errors,
                topic_keywords=list(cfg.topic_keywords),
                max_frontier_in_memory=cfg.max_frontier_in_memory,
                avoid_duplicate_paths_across_daemons=cfg.avoid_duplicate_paths_across_daemons,
            )

    def list_groups(self) -> list[WorkerGroupSettings]:
        with self._lock:
            return [
                WorkerGroupSettings(
                    id=group.id,
                    name=group.name,
                    description=group.description,
                    enabled=group.enabled,
                    max_pages_per_worker=group.max_pages_per_worker,
                    rate_limit_per_minute=group.rate_limit_per_minute,
                    queue_mode=group.queue_mode,
                    strategy_mode=group.strategy_mode,
                    topic_keywords=list(group.topic_keywords),
                    avoid_duplicate_paths_across_daemons=group.avoid_duplicate_paths_across_daemons,
                    worker_ids=list(group.worker_ids),
                )
                for group in self._groups.values()
            ]

    def update_group(self, group_id: int, payload: dict[str, object]) -> WorkerGroupSettings | None:
        with self._lock:
            group = self._groups.get(group_id)
            if group is None:
                return None

            if "name" in payload:
                group.name = str(payload["name"])
            if "description" in payload:
                group.description = str(payload["description"])
            if "enabled" in payload:
                group.enabled = bool(payload["enabled"])
            if "maxPagesPerWorker" in payload:
                group.max_pages_per_worker = _coerce_int(
                    payload["maxPagesPerWorker"],
                    group.max_pages_per_worker or 0,
                )
            if "rateLimitPerMinute" in payload:
                group.rate_limit_per_minute = _coerce_int(
                    payload["rateLimitPerMinute"],
                    group.rate_limit_per_minute or 0,
                )
            if "queueMode" in payload:
                queue_mode = str(payload["queueMode"]).strip().lower()
                if queue_mode in {"local", "server", "both"}:
                    group.queue_mode = queue_mode
            if "strategyMode" in payload:
                strategy_mode = str(payload["strategyMode"]).strip().lower()
                if strategy_mode in {"balanced", "coverage", "focused", "freshness"}:
                    group.strategy_mode = strategy_mode
            if "topicKeywords" in payload and isinstance(payload["topicKeywords"], list):
                group.topic_keywords = [
                    str(value).strip()
                    for value in payload["topicKeywords"]
                    if str(value).strip()
                ]
            elif "topicKeywordsText" in payload:
                group.topic_keywords = [
                    line.strip()
                    for line in str(payload["topicKeywordsText"]).splitlines()
                    if line.strip()
                ]
            if "avoidDuplicatePathsAcrossDaemons" in payload:
                group.avoid_duplicate_paths_across_daemons = _coerce_bool(
                    payload["avoidDuplicatePathsAcrossDaemons"],
                    group.avoid_duplicate_paths_across_daemons
                    if group.avoid_duplicate_paths_across_daemons is not None
                    else True,
                )
            if "workerIds" in payload and isinstance(payload["workerIds"], list):
                group.worker_ids = [int(value) for value in payload["workerIds"]]

            return WorkerGroupSettings(
                id=group.id,
                name=group.name,
                description=group.description,
                enabled=group.enabled,
                max_pages_per_worker=group.max_pages_per_worker,
                rate_limit_per_minute=group.rate_limit_per_minute,
                queue_mode=group.queue_mode,
                strategy_mode=group.strategy_mode,
                topic_keywords=list(group.topic_keywords),
                avoid_duplicate_paths_across_daemons=group.avoid_duplicate_paths_across_daemons,
                worker_ids=list(group.worker_ids),
            )

    def add_seed(self, worker_id: int | None, url: str) -> dict[str, object]:
        if not url:
            raise ValueError("Seed URL must not be empty.")
        with self._lock:
            if not self._daemon_running:
                raise RuntimeError("Crawler daemon is not running.")

            queue_mode = self._global_config.queue_mode
            queued = self._enqueue_frontier_url(url)
            if worker_id is None:
                return {
                    "appliedTo": "daemon-frontier",
                    "workerId": None,
                    "url": url,
                    "queued": queued,
                    "inMemoryQueued": len(self._frontier_queue),
                    "localQueued": sum(len(queue) for queue in self._worker_local_queues.values()),
                }

            worker = self._workers.get(worker_id)
            if worker is None:
                raise KeyError(f"Worker {worker_id} was not found.")

            local_queued = False
            if queue_mode in {"local", "both"}:
                local_queued = self._enqueue_local_frontier_url(worker_id, url)

            self._append_log(
                worker_id,
                "Info",
                f"Seed routed (global={queued}, local={local_queued}) for worker {worker_id}: {url}",
            )
            return {
                "appliedTo": "daemon-frontier+worker-local" if local_queued else "daemon-frontier",
                "workerId": worker_id,
                "url": url,
                "queued": queued or local_queued,
                "queuedGlobal": queued,
                "queuedLocal": local_queued,
                "inMemoryQueued": len(self._frontier_queue),
                "inMemoryWorkerQueued": len(self._worker_local_queues.get(worker_id, [])),
            }

    def claim_frontier_url(self, worker_id: int) -> dict[str, object]:
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                raise KeyError(f"Worker {worker_id} was not found.")

            lease = self._claim_next_frontier_url(worker_id)
            if lease is None:
                return {
                    "claimed": False,
                    "workerId": worker_id,
                    "url": None,
                }

            return {
                "claimed": True,
                "workerId": worker_id,
                "url": lease.url,
                "leaseToken": lease.token,
                "leaseTtlSeconds": self._lease_ttl_seconds,
                "source": lease.source,
            }

    def dequeue_frontier_urls(
        self,
        worker_ids: list[int] | None,
        *,
        limit: int = 1,
        daemon_id: str | None = None,
    ) -> dict[str, object]:
        bounded_limit = max(1, min(limit, 100))

        with self._lock:
            requested_ids = worker_ids or []
            if requested_ids:
                missing = [wid for wid in requested_ids if wid not in self._workers]
                if missing:
                    raise KeyError(f"Worker(s) not found: {', '.join(str(item) for item in missing)}")
                candidates = list(dict.fromkeys(requested_ids))
            else:
                candidates = [worker.id for worker in self._workers.values()]

            if not candidates:
                return {
                    "daemonId": daemon_id or "local-default",
                    "requestedWorkerIds": requested_ids,
                    "items": [],
                    "remainingInMemory": len(self._frontier_queue),
                }

            claims: list[dict[str, object]] = []
            claimed_workers: set[int] = set()
            for worker_id in candidates:
                if len(claims) >= bounded_limit:
                    break

                lease = self._claim_next_frontier_url(worker_id)
                if lease is None:
                    continue

                if worker_id in claimed_workers:
                    continue

                claimed_workers.add(worker_id)
                claims.append(
                    {
                        "workerId": worker_id,
                        "url": lease.url,
                        "leaseToken": lease.token,
                        "leaseTtlSeconds": self._lease_ttl_seconds,
                        "source": lease.source,
                    }
                )

            return {
                "daemonId": daemon_id or "local-default",
                "requestedWorkerIds": requested_ids,
                "items": claims,
                "remainingInMemory": len(self._frontier_queue),
                "activeLeases": len(self._frontier_leases),
            }

    def complete_frontier_url(
        self,
        worker_id: int,
        url: str,
        lease_token: str | None,
        status: str = "completed",
    ) -> dict[str, object]:
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                raise KeyError(f"Worker {worker_id} was not found.")

            completed = self._complete_frontier_claim(worker_id, url, lease_token, status)
            return {
                "completed": completed,
                "workerId": worker_id,
                "url": url,
                "status": status,
            }

    def prune_local_frontier(self, worker_id: int, url: str, reason: str | None = None) -> dict[str, object]:
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                raise KeyError(f"Worker {worker_id} was not found.")
            pruned = self._prune_worker_local_url(worker_id, url, reason or "server-conflict")
            return {
                "pruned": pruned,
                "workerId": worker_id,
                "url": url,
                "reason": reason or "server-conflict",
            }

    def get_statistics(self) -> dict[str, object]:
        with self._lock:
            status_counts: dict[str, int] = defaultdict(int)
            total_pages = 0
            for worker in self._workers.values():
                status_counts[worker.status] += 1
                total_pages += worker.pages_processed

            return {
                "daemonRunning": self._daemon_running,
                "totalWorkers": len(self._workers),
                "statusCounts": dict(status_counts),
                "totalPagesProcessed": total_pages,
                "activeProcesses": len(self._local_processes),
                "source": "daemon",
                "parallelismModel": {
                    "instanceLevel": "single-daemon",
                    "workerLevel": "multi-worker",
                },
                "timestampUtc": utc_now_iso(),
            }

    def _launch_local_worker_process(self, worker_id: int, seed_url: str | None) -> None:
        existing = self._local_processes.get(worker_id)
        if existing is not None and existing.poll() is None:
            worker = self._workers[worker_id]
            worker.pid = existing.pid
            worker.status = "Active"
            worker.status_reason = "local-process-running"
            worker.started_at = worker.started_at or utc_now_iso()
            return

        repo_root = Path(__file__).resolve().parents[3]
        main_path = repo_root / "pa1" / "crawler" / "src" / "main.py"
        if not main_path.exists():
            self._append_log(worker_id, "Warning", "Local worker spawn skipped: main.py not found.")
            return

        command = [sys.executable, str(main_path), "--check-url", seed_url or "https://example.com/"]
        try:
            process = subprocess.Popen(command, cwd=str(repo_root))
            self._local_processes[worker_id] = process
            worker = self._workers[worker_id]
            worker.pid = process.pid
            worker.status = "Active"
            worker.status_reason = "local-process-started"
            worker.started_at = utc_now_iso()
            self._append_log(worker_id, "Info", f"Local process started with PID={process.pid}.")
        except Exception as exc:
            worker = self._workers[worker_id]
            worker.status = "Error"
            worker.error_count += 1
            self._append_log(worker_id, "Error", f"Local process failed to start: {exc}")

    def _terminate_process_if_running(self, worker_id: int) -> None:
        process = self._local_processes.pop(worker_id, None)
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        except Exception:
            process.kill()
        worker = self._workers.get(worker_id)
        if worker is not None:
            worker.pid = None

    def _is_worker_execution_alive_locked(self, worker_id: int) -> bool:
        process = self._local_processes.get(worker_id)
        if process is not None and process.poll() is None:
            return True

        thread_entry = self._thread_workers.get(worker_id)
        if thread_entry is not None:
            thread, _ = thread_entry
            if thread.is_alive():
                return True
            self._thread_workers.pop(worker_id, None)

        return False

    def _can_start_worker_locked(self, worker_id: int) -> bool:
        max_workers = max(1, self._global_config.max_concurrent_workers)
        active_running = 0

        for candidate in self._workers.values():
            if candidate.id == worker_id:
                continue
            if candidate.status != "Active":
                continue
            if self._is_worker_execution_alive_locked(candidate.id):
                active_running += 1

        return active_running < max_workers

    def _launch_thread_worker(self, worker_id: int, seed_url: str | None) -> None:
        existing = self._thread_workers.get(worker_id)
        if existing is not None:
            thread, _ = existing
            if thread.is_alive():
                worker = self._workers[worker_id]
                worker.status = "Active"
                worker.status_reason = "thread-running"
                worker.started_at = worker.started_at or utc_now_iso()
                return
            self._thread_workers.pop(worker_id, None)

        worker = self._workers[worker_id]
        stop_event = threading.Event()
        if seed_url:
            queued_seed = self._enqueue_frontier_url(seed_url)
            if not queued_seed:
                self._append_log(
                    worker_id,
                    "Warning",
                    "Initial seed was not queued; verify queue mode and server relay availability.",
                )

        def run() -> None:
            while not stop_event.is_set():
                time.sleep(1.2)

                with self._lock:
                    current = self._workers.get(worker_id)
                    if current is None or current.status != "Active":
                        continue
                    lease = self._claim_next_frontier_url(worker_id)
                    if lease is None:
                        current.current_url = None
                        current.status_reason = "waiting-for-frontier"
                        continue
                    current.current_url = lease.url
                    current.status_reason = "fetching"

                processing = self._download_and_extract_links(lease.url)

                with self._lock:
                    current = self._workers.get(worker_id)
                    if current is None:
                        continue

                    current.current_url = processing["finalUrl"] or lease.url
                    if processing["ok"]:
                        current.status_reason = "processing"
                        current.pages_processed += 1
                        self._append_log(
                            worker_id,
                            "Info",
                            f"Fetched {current.current_url} (status={processing['downloadResult'].get('statusCode')}, links={len(processing['discoveredLinks'])}).",
                        )
                    else:
                        failure_stage = str(processing.get("stage") or "fetch")
                        current.status_reason = f"{failure_stage}-failed"
                        current.error_count += 1
                        self._append_log(
                            worker_id,
                            "Warning",
                            f"{failure_stage.capitalize()} failed for {lease.url}: {processing['error']}",
                        )

                    for link in processing["discoveredLinks"]:
                        self._enqueue_frontier_url(link)

                    self._report_page_to_manager(
                        current,
                        lease.url,
                        processing["downloadResult"],
                        processing["discoveredLinks"],
                    )
                    self._emit_manager_event(
                        "worker-metric",
                        worker_id=worker_id,
                        payload={
                            "pages_processed_total": current.pages_processed,
                            "errors_total": current.error_count,
                            "frontier_in_memory": len(self._frontier_queue),
                            "frontier_leases": len(self._frontier_leases),
                            "page_processed": 1 if processing["ok"] else 0,
                            "page_error": 0 if processing["ok"] else 1,
                        },
                    )
                    self._complete_frontier_claim(
                        worker_id,
                        lease.url,
                        lease.token,
                        "completed" if processing["ok"] else "failed",
                    )

        thread = threading.Thread(target=run, daemon=True, name=f"worker-thread-{worker_id}")
        self._thread_workers[worker_id] = (thread, stop_event)
        worker.status = "Active"
        worker.status_reason = "thread-started"
        worker.started_at = utc_now_iso()
        worker.pid = None
        thread.start()
        self._append_log(worker_id, "Info", "Thread worker started.")

    def _stop_thread_worker(self, worker_id: int, reason: str = "stopped") -> None:
        entry = self._thread_workers.pop(worker_id, None)
        if entry is None:
            return

        thread, stop_event = entry
        stop_event.set()
        thread.join(timeout=2)
        worker = self._workers.get(worker_id)
        if worker is not None:
            worker.pid = None
            worker.status_reason = reason
        self._append_log(worker_id, "Info", f"Thread worker stopped (reason={reason}).")

    def _append_local_log(self, worker_id: int, level: str, message: str, *, emit_manager_event: bool) -> None:
        self._logs[worker_id].append(
            WorkerLogEntry(timestamp_utc=utc_now_iso(), level=level, message=message)
        )
        if emit_manager_event:
            self._emit_manager_event(
                "worker-log",
                worker_id=worker_id,
                payload={"level": level, "message": message},
            )

    def _append_log(self, worker_id: int, level: str, message: str) -> None:
        self._append_local_log(worker_id, level, message, emit_manager_event=True)

    def _simulation_loop(self) -> None:
        while True:
            time.sleep(1.5)
            with self._lock:
                for worker_id, process in list(self._local_processes.items()):
                    if process.poll() is not None:
                        worker = self._workers.get(worker_id)
                        if worker is not None and worker.status == "Active":
                            worker.status = "Idle"
                            worker.status_reason = "local-process-finished"
                            worker.pid = None
                            self._append_log(worker_id, "Info", "Local process finished (reason=process-exit).")
                        self._local_processes.pop(worker_id, None)

    def _init_frontier_swap_store(self) -> None:
        if not self._frontier_db_sync_enabled:
            return
        try:
            self._frontier_swap_conn = get_connection()
            self._frontier_swap_store = PostgresFrontierSwapStore(self._frontier_swap_conn)
            self._append_log(1, "Info", "Frontier DB swap/log enabled.")
        except Exception as exc:
            self._frontier_swap_store = None
            self._frontier_swap_conn = None
            self._append_log(1, "Warning", f"Frontier DB swap unavailable; using memory-only queue: {exc}")

    def _persist_frontier_row(
        self,
        *,
        url: str,
        priority: int = 0,
        source_url: str | None = None,
        depth: int = 0,
        state: str = "queued",
    ) -> None:
        if self._frontier_swap_conn is None:
            return
        try:
            with self._frontier_swap_conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO crawldb.frontier_queue (
                        url,
                        priority,
                        source_url,
                        depth,
                        state,
                        discovered_at,
                        dequeued_at
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW(), CASE WHEN %s = 'queued' THEN NULL ELSE NOW() END)
                    ON CONFLICT (url)
                    DO UPDATE
                        SET priority = GREATEST(crawldb.frontier_queue.priority, EXCLUDED.priority),
                            source_url = COALESCE(EXCLUDED.source_url, crawldb.frontier_queue.source_url),
                            depth = LEAST(crawldb.frontier_queue.depth, EXCLUDED.depth),
                            state = EXCLUDED.state,
                            dequeued_at = CASE WHEN EXCLUDED.state = 'queued' THEN NULL ELSE NOW() END;
                    """,
                    (url, priority, source_url, depth, state, state),
                )
            self._frontier_swap_conn.commit()
        except Exception:
            try:
                self._frontier_swap_conn.rollback()
            except Exception:
                pass

    def _mark_frontier_state(self, *, url: str, state: str) -> None:
        if self._frontier_swap_conn is None:
            return
        try:
            with self._frontier_swap_conn.cursor() as cur:
                normalized_state = state.strip().lower()
                if self._frontier_remove_terminal_rows and normalized_state in {"done", "failed", "completed"}:
                    cur.execute(
                        """
                        DELETE FROM crawldb.frontier_queue
                        WHERE url = %s;
                        """,
                        (url,),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE crawldb.frontier_queue
                        SET state = %s,
                            dequeued_at = CASE WHEN %s = 'queued' THEN NULL ELSE NOW() END
                        WHERE url = %s;
                        """,
                        (state, state, url),
                    )
            self._frontier_swap_conn.commit()
        except Exception:
            try:
                self._frontier_swap_conn.rollback()
            except Exception:
                pass

    def _hydrate_frontier_from_swap_store_if_needed(self) -> None:
        if self._frontier_swap_store is None or self._frontier_queue:
            return
        try:
            refill = self._frontier_swap_store.dequeue_batch(limit=self._frontier_swap_refill_batch_size)
        except Exception:
            return

        loaded = 0
        for entry in refill:
            if self._is_tombstoned(entry.url):
                continue
            if self._is_url_active_any_queue(entry.url):
                continue
            self._known_frontier_urls.add(entry.url)
            self._frontier_queue.append(entry.url)
            loaded += 1

        if loaded > 0:
            self._emit_manager_event(
                "queue-change",
                payload={
                    "action": "swap-refill",
                    "loaded": loaded,
                    "inMemoryQueued": len(self._frontier_queue),
                },
            )

    def _is_url_active_any_queue(self, url: str) -> bool:
        if url in self._known_frontier_urls:
            return True
        if url in self._frontier_leases:
            return True
        return any(url in known for known in self._worker_local_known_urls.values())

    def _collision_prevention_enabled_for_worker(self, worker_id: int) -> bool:
        for group in self._groups.values():
            if worker_id in group.worker_ids and group.avoid_duplicate_paths_across_daemons is not None:
                return bool(group.avoid_duplicate_paths_across_daemons)
        return bool(self._global_config.avoid_duplicate_paths_across_daemons)

    def _enqueue_frontier_url(self, raw_url: str) -> bool:
        candidate = raw_url.strip()
        if not candidate:
            return False
        self._purge_expired_frontier_state()
        prevent_collisions = bool(self._global_config.avoid_duplicate_paths_across_daemons)
        if self._is_tombstoned(candidate):
            return False
        if prevent_collisions and self._is_url_active_any_queue(candidate):
            self._emit_manager_event(
                "frontier-prune",
                payload={
                    "url": candidate,
                    "reason": "already-active",
                },
            )
            return False

        queue_mode = self._global_config.queue_mode
        relayed = False
        if queue_mode in {"server", "both"}:
            relayed = self._relay_frontier_seed(candidate)

        should_enqueue_local = queue_mode in {"local", "both"}
        if queue_mode == "server" and not relayed and self._allow_daemon_db_fallback:
            should_enqueue_local = True

        if not relayed and not should_enqueue_local:
            self._emit_manager_event(
                "frontier-prune",
                payload={
                    "url": candidate,
                    "reason": "server-relay-unavailable",
                    "queueMode": queue_mode,
                },
            )
            return False

        if should_enqueue_local:
            self._known_frontier_urls.add(candidate)
        spilled_to_db = False
        if should_enqueue_local:
            entry = FrontierEntry(
                url=candidate,
                priority=0,
                source_url=None,
                depth=0,
            )
            max_in_memory = max(1, self._global_config.max_frontier_in_memory)
            if len(self._frontier_queue) >= max_in_memory and self._frontier_swap_store is not None:
                try:
                    self._frontier_swap_store.enqueue(entry)
                    spilled_to_db = True
                except Exception:
                    self._frontier_queue.append(candidate)
            else:
                self._frontier_queue.append(candidate)

        if self._frontier_db_sync_enabled:
            self._persist_frontier_row(
                url=candidate,
                priority=0,
                source_url=None,
                depth=0,
                state="queued",
            )
        self._emit_manager_event(
            "queue-change",
            payload={
                "action": "enqueue",
                "url": candidate,
                "queueMode": queue_mode,
                "inMemoryQueued": len(self._frontier_queue),
                "localQueued": sum(len(queue) for queue in self._worker_local_queues.values()),
                "knownUrls": len(self._known_frontier_urls),
                "relayed": relayed,
                "spilledToDb": spilled_to_db,
            },
        )
        return True

    def _enqueue_local_frontier_url(self, worker_id: int, raw_url: str) -> bool:
        candidate = raw_url.strip()
        if not candidate:
            return False
        self._purge_expired_frontier_state()
        local_known = self._worker_local_known_urls[worker_id]
        prevent_collisions = self._collision_prevention_enabled_for_worker(worker_id)
        if self._is_tombstoned(candidate):
            return False
        if candidate in local_known:
            return False
        if prevent_collisions and self._is_url_active_any_queue(candidate):
            self._emit_manager_event(
                "frontier-prune",
                worker_id=worker_id,
                payload={
                    "url": candidate,
                    "reason": "already-active",
                    "workerId": worker_id,
                },
            )
            return False

        self._worker_local_queues[worker_id].append(candidate)
        local_known.add(candidate)
        if self._frontier_db_sync_enabled:
            self._persist_frontier_row(
                url=candidate,
                priority=0,
                source_url=None,
                depth=0,
                state="queued",
            )
        self._emit_manager_event(
            "queue-change",
            worker_id=worker_id,
            payload={
                "action": "enqueue-local",
                "url": candidate,
                "workerId": worker_id,
                "inMemoryWorkerQueued": len(self._worker_local_queues[worker_id]),
            },
        )
        return True

    def _claim_next_frontier_url(self, worker_id: int) -> FrontierLease | None:
        self._purge_expired_frontier_state()

        active = self._active_claim_by_worker.get(worker_id)
        if active is not None:
            active.expires_at_monotonic = time.monotonic() + self._lease_ttl_seconds
            self._frontier_leases[active.url] = active
            return active

        local_lease = self._claim_from_worker_local_queue(worker_id)
        if local_lease is not None:
            return local_lease

        self._hydrate_frontier_from_swap_store_if_needed()

        while self._frontier_queue:
            candidate = self._frontier_queue.popleft()
            if self._is_tombstoned(candidate):
                continue

            lease = self._frontier_leases.get(candidate)
            if lease is not None and lease.expires_at_monotonic > time.monotonic():
                continue

            self._known_frontier_urls.discard(candidate)
            created = self._create_lease(candidate, worker_id, "global")
            self._emit_manager_event(
                "queue-change",
                worker_id=worker_id,
                payload={
                    "action": "claim-global",
                    "url": candidate,
                    "workerId": worker_id,
                    "inMemoryQueued": len(self._frontier_queue),
                },
            )
            return created

        return None

    def _claim_from_worker_local_queue(self, worker_id: int) -> FrontierLease | None:
        queue = self._worker_local_queues.get(worker_id)
        if queue is None:
            return None

        while queue:
            candidate = queue.popleft()
            if self._is_tombstoned(candidate):
                self._worker_local_known_urls[worker_id].discard(candidate)
                continue

            lease = self._frontier_leases.get(candidate)
            if lease is not None and lease.expires_at_monotonic > time.monotonic() and lease.worker_id != worker_id:
                self._worker_local_known_urls[worker_id].discard(candidate)
                self._emit_manager_event(
                    "frontier-prune",
                    worker_id=worker_id,
                    payload={
                        "url": candidate,
                        "reason": "lease-owned-by-other-worker",
                        "ownerWorkerId": lease.worker_id,
                    },
                )
                continue

            self._worker_local_known_urls[worker_id].discard(candidate)
            created = self._create_lease(candidate, worker_id, "local")
            self._emit_manager_event(
                "queue-change",
                worker_id=worker_id,
                payload={
                    "action": "claim-local",
                    "url": candidate,
                    "workerId": worker_id,
                    "inMemoryWorkerQueued": len(queue),
                },
            )
            return created

        return None

    def _create_lease(self, url: str, worker_id: int, source: str) -> FrontierLease:
        token = secrets.token_hex(12)
        lease = FrontierLease(
            url=url,
            worker_id=worker_id,
            token=token,
            expires_at_monotonic=time.monotonic() + self._lease_ttl_seconds,
            source=source,
        )
        self._frontier_leases[url] = lease
        self._active_claim_by_worker[worker_id] = lease
        if self._frontier_db_sync_enabled:
            self._mark_frontier_state(url=url, state="processing")
        self._emit_manager_event(
            "frontier-lease",
            worker_id=worker_id,
            payload={
                "url": url,
                "workerId": worker_id,
                "source": source,
                "leaseTtlSeconds": self._lease_ttl_seconds,
            },
        )
        return lease

    def _complete_frontier_claim(
        self,
        worker_id: int,
        url: str,
        lease_token: str | None,
        status: str,
    ) -> bool:
        self._purge_expired_frontier_state()
        lease = self._frontier_leases.get(url)
        if lease is None or lease.worker_id != worker_id:
            return False
        if lease_token and lease.token != lease_token:
            return False

        self._frontier_leases.pop(url, None)
        active = self._active_claim_by_worker.get(worker_id)
        if active is not None and active.url == url:
            self._active_claim_by_worker.pop(worker_id, None)

        self._completed_tombstones[url] = time.monotonic() + self._tombstone_ttl_seconds
        if self._frontier_db_sync_enabled:
            db_state = "done" if status == "completed" else "failed"
            self._mark_frontier_state(url=url, state=db_state)
        self._emit_manager_event(
            "frontier-complete",
            worker_id=worker_id,
            payload={
                "url": url,
                "workerId": worker_id,
                "status": status,
                "tombstoneTtlSeconds": self._tombstone_ttl_seconds,
            },
        )
        return True

    def _prune_worker_local_url(self, worker_id: int, url: str, reason: str) -> bool:
        queue = self._worker_local_queues.get(worker_id)
        if queue is None:
            return False

        removed = False
        filtered = deque()
        for item in queue:
            if item == url:
                removed = True
                continue
            filtered.append(item)
        self._worker_local_queues[worker_id] = filtered
        self._worker_local_known_urls[worker_id].discard(url)
        if removed:
            self._emit_manager_event(
                "frontier-prune",
                worker_id=worker_id,
                payload={
                    "url": url,
                    "workerId": worker_id,
                    "reason": reason,
                },
            )
        return removed

    def _release_claim_for_worker(self, worker_id: int, requeue: bool, reason: str) -> None:
        active = self._active_claim_by_worker.pop(worker_id, None)
        if active is None:
            return

        self._frontier_leases.pop(active.url, None)
        if requeue and not self._is_tombstoned(active.url) and active.url not in self._known_frontier_urls:
            self._known_frontier_urls.add(active.url)
            self._frontier_queue.appendleft(active.url)
            if self._frontier_db_sync_enabled:
                self._mark_frontier_state(url=active.url, state="queued")
        elif self._frontier_db_sync_enabled:
            self._mark_frontier_state(url=active.url, state="failed")

        self._emit_manager_event(
            "frontier-release",
            worker_id=worker_id,
            payload={
                "url": active.url,
                "workerId": worker_id,
                "requeued": requeue,
                "reason": reason,
            },
        )

    def _purge_expired_frontier_state(self) -> None:
        now = time.monotonic()

        expired_leases = [
            (url, lease)
            for url, lease in self._frontier_leases.items()
            if lease.expires_at_monotonic <= now
        ]
        for url, lease in expired_leases:
            self._frontier_leases.pop(url, None)
            active = self._active_claim_by_worker.get(lease.worker_id)
            if active is not None and active.url == url:
                self._active_claim_by_worker.pop(lease.worker_id, None)
            if url not in self._known_frontier_urls and not self._is_tombstoned(url):
                self._known_frontier_urls.add(url)
                self._frontier_queue.appendleft(url)
                if self._frontier_db_sync_enabled:
                    self._mark_frontier_state(url=url, state="queued")
                self._emit_manager_event(
                    "frontier-lease-expired",
                    worker_id=lease.worker_id,
                    payload={
                        "url": url,
                        "workerId": lease.worker_id,
                        "action": "requeued",
                    },
                )

        expired_tombstones = [url for url, expiry in self._completed_tombstones.items() if expiry <= now]
        for url in expired_tombstones:
            self._completed_tombstones.pop(url, None)

    def _is_tombstoned(self, url: str) -> bool:
        expiry = self._completed_tombstones.get(url)
        if expiry is None:
            return False
        if expiry <= time.monotonic():
            self._completed_tombstones.pop(url, None)
            return False
        return True

    def _relay_frontier_seed(self, url: str) -> bool:
        if self._frontier_relay_url is None:
            return False

        payload = json.dumps({"url": url, "source": "daemon-frontier"}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._frontier_relay_token:
            headers["Authorization"] = f"Bearer {self._frontier_relay_token}"

        req = request.Request(self._frontier_relay_url, data=payload, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=2.0):
                return True
        except (error.URLError, TimeoutError, OSError) as exc:
            self._append_log(1, "Warning", f"Frontier relay failed, using daemon-local queue: {exc}")
            return False

    def _download_and_extract_links(self, url: str) -> dict[str, Any]:
        downloader = Downloader(user_agent=self._global_config.user_agent)
        try:
            download = downloader.fetch(
                url,
                render_html=False,
                download_pdf_content=False,
                download_binary_content=False,
                store_large_binary_content=False,
            )
        except Exception as exc:
            return {
                "ok": False,
                "stage": "fetch",
                "error": str(exc),
                "finalUrl": url,
                "downloadResult": {
                    "requestedUrl": url,
                    "finalUrl": url,
                    "statusCode": 0,
                    "contentType": None,
                    "dataTypeCode": None,
                    "pageTypeCode": "HTML",
                    "htmlContent": None,
                    "usedRenderer": False,
                    "contentLength": None,
                },
                "discoveredLinks": [],
            }

        try:
            discovered_links: list[str] = []
            parser_payload: dict[str, object] | None = None
            if download.html_content:
                extracted = self._link_extractor.extract(download.html_content, download.final_url)
                discovered_links = extracted.links[: self._max_extracted_links_per_page]
                parser_payload = {
                    "links": extracted.links,
                    "jsLinks": extracted.js_links,
                    "images": extracted.images,
                    "linksCount": len(extracted.links),
                    "jsLinksCount": len(extracted.js_links),
                    "imagesCount": len(extracted.images),
                }

            return {
                "ok": True,
                "stage": "complete",
                "error": None,
                "finalUrl": download.final_url,
                "downloadResult": self._to_download_payload(download, parser_payload=parser_payload),
                "discoveredLinks": discovered_links,
            }
        except Exception as exc:
            return {
                "ok": False,
                "stage": "parse",
                "error": str(exc),
                "finalUrl": download.final_url,
                "downloadResult": self._to_download_payload(download, parser_payload={
                    "links": [],
                    "jsLinks": [],
                    "images": [],
                    "linksCount": 0,
                    "jsLinksCount": 0,
                    "imagesCount": 0,
                }),
                "discoveredLinks": [],
            }

    @staticmethod
    def _to_download_payload(
        download: DownloadResult,
        *,
        parser_payload: dict[str, object] | None = None,
    ) -> dict[str, object | None]:
        return {
            "requestedUrl": download.requested_url,
            "finalUrl": download.final_url,
            "statusCode": download.status_code,
            "contentType": download.content_type,
            "dataTypeCode": download.data_type_code,
            "pageTypeCode": download.page_type_code,
            "htmlContent": download.html_content,
            "usedRenderer": download.used_renderer,
            "contentLength": download.content_length,
            "parsedPayload": parser_payload,
        }

    def _report_page_to_manager(
        self,
        worker: WorkerRecord,
        raw_url: str | None,
        download_result: dict[str, object | None],
        discovered_links: list[str],
    ) -> None:
        if self._manager_ingest_url is None or not raw_url:
            return

        payload = {
            "rawUrl": raw_url,
            "siteId": None,
            "sourcePageId": None,
            "discoveredUrls": discovered_links,
            "downloadResult": download_result,
        }

        relayed = self._post_manager_json(self._manager_ingest_url, payload)
        if not relayed:
            target_url = str(download_result.get("finalUrl") or raw_url)
            self._append_local_log(
                worker.id,
                "Warning",
                f"Failed to relay page result to manager for {target_url}; data remains local to daemon logs.",
                emit_manager_event=False,
            )

        self._emit_manager_event(
            "page-reported",
            worker_id=worker.id,
            payload={
                "url": download_result.get("finalUrl") or raw_url,
                "pagesProcessed": worker.pages_processed,
                "status": worker.status,
                "reportedAt": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _emit_manager_event(self, event_type: str, worker_id: int | None = None, payload: object | None = None) -> bool:
        if self._manager_event_url is None:
            return False

        event = {
            "type": event_type,
            "daemonId": self._daemon_id,
            "workerId": worker_id,
            "payload": payload,
        }

        pending_events = list(self._pending_manager_events)
        pending_events.append(event)
        for index, pending_event in enumerate(pending_events):
            delivered = self._post_manager_json(self._manager_event_url, pending_event)
            if delivered:
                continue

            self._pending_manager_events = deque(pending_events[index:], maxlen=1000)
            if not self._manager_event_relay_degraded:
                self._manager_event_relay_degraded = True
                self._append_local_log(
                    worker_id or 1,
                    "Warning",
                    "Manager event relay unavailable; worker logs and metrics will sync when the manager API responds again.",
                    emit_manager_event=False,
                )
            return False

        self._pending_manager_events.clear()
        if self._manager_event_relay_degraded:
            self._manager_event_relay_degraded = False
            self._append_local_log(
                worker_id or 1,
                "Info",
                "Manager event relay restored; queued worker logs and metrics were synced.",
                emit_manager_event=False,
            )
        return True

    def _post_manager_json(self, url: str, payload: object) -> bool:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._manager_api_token:
            headers["Authorization"] = f"Bearer {self._manager_api_token}"

        req = request.Request(url, data=data, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=2.5):
                return True
        except (error.URLError, TimeoutError, OSError):
            return False

    @staticmethod
    def _copy_worker(worker: WorkerRecord | None) -> WorkerRecord:
        if worker is None:
            raise ValueError("Worker must not be None when copying.")
        return WorkerRecord(
            id=worker.id,
            name=worker.name,
            status=worker.status,
            status_reason=worker.status_reason,
            current_url=worker.current_url,
            pages_processed=worker.pages_processed,
            error_count=worker.error_count,
            started_at=worker.started_at,
            mode=worker.mode,
            pid=worker.pid,
            runtime_config=dict(worker.runtime_config),
        )
