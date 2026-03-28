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
from urllib.parse import urlparse, urlsplit

from api.contracts import (
    GlobalWorkerConfig,
    SeedEntry,
    WorkerGroupSettings,
    WorkerLogEntry,
    WorkerRecord,
    default_seed_entries,
    utc_now_iso,
)
from core.config import load_crawler_config
from api.service_protocol import WorkerControlService
from core.downloader import DownloadResult, Downloader
from core.link_extractor import LinkExtractor
from core.politeness import PerIpRateLimiter
from core.relevance import RelevancePolicy, score_url
from core.robots import RobotsPolicy
from core.robots import RobotsPolicyManager
from db.pg_connect import get_connection
from utils.url_canonicalizer import DefaultUrlCanonicalizer


@dataclass
class FrontierLease:
    url: str
    worker_id: int
    token: str
    expires_at_monotonic: float
    source: str
    depth: int
    priority: int
    source_url: str | None


@dataclass
class FrontierMetadata:
    priority: int
    depth: int
    source_url: str | None
    discovered_at_monotonic: float


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
        self._lock = threading.RLock()
        self._daemon_running = False
        self._daemon_started_at: str | None = None
        self._daemon_mode = "single-instance"
        self._daemon_id = os.getenv("CRAWLER_DAEMON_ID", "local-default").strip() or "local-default"
        self._queue_event_order = 0
        self._workers: dict[int, WorkerRecord] = {
            1: WorkerRecord(
                id=1,
            name=self._worker_name(1),
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
        self._frontier_metadata: dict[str, FrontierMetadata] = {}
        self._worker_local_queues: dict[int, deque[str]] = defaultdict(deque)
        self._worker_local_known_urls: dict[int, set[str]] = defaultdict(set)
        self._worker_local_metadata: dict[int, dict[str, FrontierMetadata]] = defaultdict(dict)
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
        self._frontier_claim_url = os.getenv("MANAGER_FRONTIER_CLAIM_URL", "").strip() or None
        self._frontier_complete_url = os.getenv("MANAGER_FRONTIER_COMPLETE_URL", "").strip() or None
        self._frontier_status_url = os.getenv("MANAGER_FRONTIER_STATUS_URL", "").strip() or None
        self._manager_ingest_url = os.getenv("MANAGER_INGEST_API_URL", "").strip() or None
        self._manager_event_url = os.getenv("MANAGER_EVENT_API_URL", "").strip() or None
        self._manager_api_token = os.getenv("MANAGER_INGEST_API_TOKEN", "").strip() or None
        if self._frontier_relay_url is None:
            self._frontier_relay_url = self._derive_manager_api_url("/api/frontier/seed")
        if self._frontier_claim_url is None:
            self._frontier_claim_url = self._derive_manager_api_url("/api/frontier/claim")
        if self._frontier_complete_url is None:
            self._frontier_complete_url = self._derive_manager_api_url("/api/frontier/complete")
        if self._frontier_status_url is None:
            self._frontier_status_url = self._derive_manager_api_url("/api/frontier/status")
        if not self._frontier_relay_token:
            self._frontier_relay_token = (
                self._manager_api_token
                or os.getenv("MANAGER_DAEMON_WS_TOKEN", "").strip()
                or None
            )
        self._pending_manager_events: deque[dict[str, object | None]] = deque(maxlen=1000)
        self._manager_event_relay_degraded = False
        allow_local_fallback_raw = os.getenv(
            "CRAWLER_DAEMON_ALLOW_LOCAL_FALLBACK",
            os.getenv("CRAWLER_DAEMON_ALLOW_DB_FALLBACK", "true"),
        )
        self._allow_daemon_local_fallback = str(allow_local_fallback_raw).strip().lower() in {"1", "true", "yes", "on"}
        self._max_extracted_links_per_page = max(
            5,
            _coerce_int(os.getenv("CRAWLER_MAX_EXTRACTED_LINKS_PER_PAGE", "30"), 30),
        )
        self._site_next_available_monotonic: dict[str, float] = {}
        self._link_extractor = LinkExtractor()
        self._url_canonicalizer = DefaultUrlCanonicalizer()
        self._crawler_config = load_crawler_config()
        self._robots_policy_manager = RobotsPolicyManager(user_agent=self._global_config.user_agent)
        self._ip_rate_limiter = PerIpRateLimiter(min_interval_seconds=0.0)

        for worker in self._workers.values():
            self._append_log(worker.id, "Info", f"{worker.name} initialized in {worker.mode} mode.")

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
                    "daemonLocalFallbackEnabled": self._allow_daemon_local_fallback,
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
            seeded = self._enqueue_initial_seeds_locked()
            for worker in self._workers.values():
                if worker.status == "Stopped":
                    worker.status = "Idle"
                    worker.status_reason = "daemon-started"
                if worker.status_reason == "daemon-initialized":
                    worker.status_reason = "ready-awaiting-queue"
                worker.current_url = None
                self._append_log(worker.id, "Info", "Daemon started.")
                self._emit_manager_event(
                    "status-change",
                    worker_id=worker.id,
                    payload={
                        "status": worker.status,
                        "reason": "daemon-start",
                        "previousStatus": "Stopped" if worker.status == "Idle" else worker.status,
                    },
                )
            return {
                "running": True,
                "alreadyRunning": False,
                "startedAt": self._daemon_started_at,
                "note": f"Crawler daemon started. Seeded queue with {seeded} URL(s).",
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
            self._emit_manager_event(
                "status-change",
                worker_id=worker_id,
                payload={"status": "Active", "previousStatus": "Idle", "reason": "manual-start"},
            )
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
            self._emit_manager_event(
                "status-change",
                worker_id=worker_id,
                payload={"status": "Paused", "previousStatus": "Active", "reason": "manual-pause"},
            )
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
            self._emit_manager_event(
                "status-change",
                worker_id=worker_id,
                payload={"status": "Stopped", "previousStatus": "Active", "reason": "manual-stop"},
            )
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
        with self._lock:
            if not self._daemon_running:
                raise RuntimeError("Crawler daemon is not running. Start daemon before spawning workers.")
            worker_id = self._next_worker_id
            self._next_worker_id += 1
            worker_name = self._worker_name(worker_id)

            worker = WorkerRecord(
                id=worker_id,
                name=worker_name,
                status="Idle",
                status_reason="ready-awaiting-queue",
                current_url=None,
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
                    "seedUrl": None,
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
                relevance_allowed_domain_suffixes=list(cfg.relevance_allowed_domain_suffixes),
                relevance_same_host_boost=cfg.relevance_same_host_boost,
                relevance_allowed_suffix_boost=cfg.relevance_allowed_suffix_boost,
                relevance_keyword_boost=cfg.relevance_keyword_boost,
                relevance_depth_penalty=cfg.relevance_depth_penalty,
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

        relevance_suffixes: list[str] = []
        raw_relevance_suffixes = payload.get("relevanceAllowedDomainSuffixes")
        if isinstance(raw_relevance_suffixes, list):
            relevance_suffixes = [
                str(value).strip().lower().lstrip(".")
                for value in raw_relevance_suffixes
                if str(value).strip()
            ]
        elif "relevanceAllowedDomainSuffixesText" in payload:
            relevance_suffixes = [
                token.strip().lower().lstrip(".")
                for line in str(payload.get("relevanceAllowedDomainSuffixesText", "")).splitlines()
                for token in line.replace(";", ",").split(",")
                if token.strip()
            ]
        if not relevance_suffixes:
            relevance_suffixes = list(self._global_config.relevance_allowed_domain_suffixes)

        try:
            relevance_same_host_boost = float(
                payload.get("relevanceSameHostBoost", self._global_config.relevance_same_host_boost)
            )
        except (TypeError, ValueError):
            relevance_same_host_boost = self._global_config.relevance_same_host_boost

        try:
            relevance_allowed_suffix_boost = float(
                payload.get("relevanceAllowedSuffixBoost", self._global_config.relevance_allowed_suffix_boost)
            )
        except (TypeError, ValueError):
            relevance_allowed_suffix_boost = self._global_config.relevance_allowed_suffix_boost

        try:
            relevance_keyword_boost = float(
                payload.get("relevanceKeywordBoost", self._global_config.relevance_keyword_boost)
            )
        except (TypeError, ValueError):
            relevance_keyword_boost = self._global_config.relevance_keyword_boost

        try:
            relevance_depth_penalty = float(
                payload.get("relevanceDepthPenalty", self._global_config.relevance_depth_penalty)
            )
        except (TypeError, ValueError):
            relevance_depth_penalty = self._global_config.relevance_depth_penalty

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
                relevance_allowed_domain_suffixes=list(dict.fromkeys(relevance_suffixes)),
                relevance_same_host_boost=max(0.0, relevance_same_host_boost),
                relevance_allowed_suffix_boost=max(0.0, relevance_allowed_suffix_boost),
                relevance_keyword_boost=max(0.0, relevance_keyword_boost),
                relevance_depth_penalty=max(0.0, relevance_depth_penalty),
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
                relevance_allowed_domain_suffixes=list(cfg.relevance_allowed_domain_suffixes),
                relevance_same_host_boost=cfg.relevance_same_host_boost,
                relevance_allowed_suffix_boost=cfg.relevance_allowed_suffix_boost,
                relevance_keyword_boost=cfg.relevance_keyword_boost,
                relevance_depth_penalty=cfg.relevance_depth_penalty,
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
            queued = self._enqueue_frontier_url(url, depth=0, explicit_priority=100)
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
                local_queued = self._enqueue_local_frontier_url(worker_id, url, depth=0, explicit_priority=100)

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
                "depth": lease.depth,
                "priority": lease.priority,
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
                        "depth": lease.depth,
                        "priority": lease.priority,
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

            canonical = self._normalize_frontier_url(url) or url.strip()

            completed = self._complete_frontier_claim(worker_id, canonical, lease_token, status)
            return {
                "completed": completed,
                "workerId": worker_id,
                "url": canonical,
                "status": status,
            }

    def prune_local_frontier(self, worker_id: int, url: str, reason: str | None = None) -> dict[str, object]:
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                raise KeyError(f"Worker {worker_id} was not found.")
            canonical = self._normalize_frontier_url(url) or url.strip()
            pruned = self._prune_worker_local_url(worker_id, canonical, reason or "server-conflict")
            return {
                "pruned": pruned,
                "workerId": worker_id,
                "url": canonical,
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

        def run() -> None:
            while not stop_event.is_set():
                time.sleep(1.2)

                with self._lock:
                    current = self._workers.get(worker_id)
                    if current is None or current.status != "Active":
                        continue
                    lease = self._claim_next_frontier_url(worker_id)
                    if lease is None:
                        self._set_worker_runtime_state_locked(
                            current,
                            status="Active",
                            reason="waiting-for-queue",
                            current_url=None,
                        )
                        continue
                    self._set_worker_runtime_state_locked(
                        current,
                        status="Active",
                        reason="fetching",
                        current_url=lease.url,
                    )

                processing = self._download_and_extract_links(worker_id, lease.url)

                with self._lock:
                    current = self._workers.get(worker_id)
                    if current is None:
                        continue

                    current.current_url = processing["finalUrl"] or lease.url
                    if processing["ok"]:
                        self._set_worker_runtime_state_locked(
                            current,
                            status="Active",
                            reason="processing",
                            current_url=current.current_url,
                        )
                        current.pages_processed += 1
                        self._append_log(
                            worker_id,
                            "Info",
                            f"Fetched {current.current_url} (status={processing['downloadResult'].get('statusCode')}, links={len(processing['discoveredLinks'])}, images={len(processing.get('discoveredImages', []))}).",
                        )
                    else:
                        failure_stage = str(processing.get("stage") or "fetch")
                        self._set_worker_runtime_state_locked(
                            current,
                            status="Active",
                            reason=f"{failure_stage}-failed",
                            current_url=current.current_url,
                        )
                        current.error_count += 1
                        self._append_log(
                            worker_id,
                            "Warning",
                            f"{failure_stage.capitalize()} failed for {lease.url}: {processing['error']}",
                        )

                    for link in processing["discoveredLinks"]:
                        self._enqueue_frontier_url(
                            link,
                            source_url=str(processing["finalUrl"] or lease.url),
                            depth=max(lease.depth + 1, 1),
                        )

                    self._report_page_to_manager(
                        current,
                        lease.url,
                        processing["downloadResult"],
                        processing["discoveredLinks"],
                        processing.get("discoveredImages", []),
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

    def _normalize_frontier_url(self, raw_url: str, *, base_url: str | None = None) -> str | None:
        candidate = raw_url.strip()
        if not candidate:
            return None
        try:
            return self._url_canonicalizer.canonicalize(candidate, base_url=base_url)
        except Exception:
            return None

    def _build_relevance_policy(self) -> RelevancePolicy:
        with self._lock:
            cfg = self._global_config
            suffixes = list(cfg.relevance_allowed_domain_suffixes)
            keywords = tuple(cfg.topic_keywords)
            same_host_boost = cfg.relevance_same_host_boost
            allowed_suffix_boost = cfg.relevance_allowed_suffix_boost
            keyword_boost = cfg.relevance_keyword_boost
            depth_penalty = cfg.relevance_depth_penalty
            seed_entries = list(cfg.seed_entries)

        if not suffixes:
            suffixes = list(self._crawler_config.relevance_allowed_domain_suffixes)

        if not suffixes:
            for seed in seed_entries:
                if not seed.enabled or not seed.url.strip():
                    continue
                try:
                    parsed = urlsplit(seed.url)
                    host = (parsed.hostname or "").strip().lower()
                    if host:
                        suffixes.append(host)
                except Exception:
                    continue

        if not keywords:
            keywords = tuple(self._crawler_config.topic_keywords)

        return RelevancePolicy(
            allowed_domain_suffixes=tuple(dict.fromkeys(suffixes)),
            keywords=keywords,
            same_host_boost=same_host_boost,
            allowed_suffix_boost=allowed_suffix_boost,
            keyword_boost=keyword_boost,
            depth_penalty=depth_penalty,
        )

    @staticmethod
    def _choose_best_candidate(
        queue: deque[str],
        metadata: dict[str, FrontierMetadata],
    ) -> tuple[int, str, FrontierMetadata] | None:
        best: tuple[int, str, FrontierMetadata] | None = None
        for idx, url in enumerate(queue):
            meta = metadata.get(url)
            if meta is None:
                meta = FrontierMetadata(
                    priority=0,
                    depth=0,
                    source_url=None,
                    discovered_at_monotonic=0.0,
                )

            if best is None:
                best = (idx, url, meta)
                continue

            _, _, current = best
            if meta.priority > current.priority:
                best = (idx, url, meta)
                continue

            if meta.priority == current.priority and meta.discovered_at_monotonic < current.discovered_at_monotonic:
                best = (idx, url, meta)

        return best

    @staticmethod
    def _build_frontier_metadata(
        *,
        priority: int,
        depth: int,
        source_url: str | None,
        discovered_at_monotonic: float | None = None,
    ) -> FrontierMetadata:
        return FrontierMetadata(
            priority=priority,
            depth=depth,
            source_url=source_url,
            discovered_at_monotonic=discovered_at_monotonic or time.monotonic(),
        )

    @classmethod
    def _merge_frontier_metadata(
        cls,
        existing: FrontierMetadata,
        *,
        priority: int,
        depth: int,
        source_url: str | None,
    ) -> FrontierMetadata:
        return cls._build_frontier_metadata(
            priority=max(existing.priority, priority),
            depth=min(existing.depth, depth),
            source_url=existing.source_url or source_url,
            discovered_at_monotonic=existing.discovered_at_monotonic,
        )

    def _resolve_frontier_enqueue_candidate(
        self,
        raw_url: str,
        *,
        source_url: str | None,
        depth: int,
        explicit_priority: int | None,
        worker_id: int | None = None,
    ) -> tuple[str, int] | None:
        candidate = self._normalize_frontier_url(raw_url, base_url=source_url)
        if not candidate:
            return None

        self._purge_expired_frontier_state()
        if self._is_tombstoned(candidate):
            return None

        robots_allowed, robots_policy = self._robots_policy_allows_enqueue(candidate)
        if not robots_allowed:
            payload: dict[str, object | None] = {
                "url": candidate,
                "reason": "robots-disallow",
                "robotsUrl": robots_policy.robots_url if robots_policy else None,
            }
            if worker_id is not None:
                payload["workerId"] = worker_id

            self._emit_manager_event(
                "frontier-prune",
                worker_id=worker_id,
                payload=payload,
            )
            return None

        priority_value = explicit_priority
        if priority_value is None:
            policy = self._build_relevance_policy()
            priority_value = int(round(score_url(
                candidate,
                parent_url=source_url,
                depth=depth,
                policy=policy,
            )))

        return candidate, priority_value

    def _enqueue_frontier_url(
        self,
        raw_url: str,
        *,
        source_url: str | None = None,
        depth: int = 0,
        explicit_priority: int | None = None,
    ) -> bool:
        resolved = self._resolve_frontier_enqueue_candidate(
            raw_url,
            source_url=source_url,
            depth=depth,
            explicit_priority=explicit_priority,
        )
        if resolved is None:
            return False

        candidate, priority_value = resolved
        prevent_collisions = bool(self._global_config.avoid_duplicate_paths_across_daemons)

        if prevent_collisions and self._is_url_active_any_queue(candidate):
            existing = self._frontier_metadata.get(candidate)
            if existing is not None:
                self._frontier_metadata[candidate] = self._merge_frontier_metadata(
                    existing,
                    priority=priority_value,
                    depth=depth,
                    source_url=source_url,
                )
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
            relayed = self._relay_frontier_seed(
                candidate,
                priority=priority_value,
                depth=depth,
                source_url=source_url,
            )

        should_enqueue_local = queue_mode in {"local", "both"}
        if queue_mode == "server" and not relayed and self._allow_daemon_local_fallback:
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
            self._frontier_metadata[candidate] = self._build_frontier_metadata(
                priority=priority_value,
                depth=depth,
                source_url=source_url,
            )
        if should_enqueue_local:
            self._frontier_queue.append(candidate)
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
                "priority": priority_value,
                "depth": depth,
            },
        )
        return True

    def _enqueue_local_frontier_url(
        self,
        worker_id: int,
        raw_url: str,
        *,
        source_url: str | None = None,
        depth: int = 0,
        explicit_priority: int | None = None,
    ) -> bool:
        resolved = self._resolve_frontier_enqueue_candidate(
            raw_url,
            source_url=source_url,
            depth=depth,
            explicit_priority=explicit_priority,
            worker_id=worker_id,
        )
        if resolved is None:
            return False

        candidate, priority_value = resolved
        local_known = self._worker_local_known_urls[worker_id]
        prevent_collisions = self._collision_prevention_enabled_for_worker(worker_id)

        if candidate in local_known:
            existing = self._worker_local_metadata[worker_id].get(candidate)
            if existing is not None:
                self._worker_local_metadata[worker_id][candidate] = self._merge_frontier_metadata(
                    existing,
                    priority=priority_value,
                    depth=depth,
                    source_url=source_url,
                )
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
        self._worker_local_metadata[worker_id][candidate] = self._build_frontier_metadata(
            priority=priority_value,
            depth=depth,
            source_url=source_url,
        )
        self._emit_manager_event(
            "queue-change",
            worker_id=worker_id,
            payload={
                "action": "enqueue-local",
                "url": candidate,
                "workerId": worker_id,
                "inMemoryWorkerQueued": len(self._worker_local_queues[worker_id]),
                "priority": priority_value,
                "depth": depth,
            },
        )
        return True

    def _claim_next_frontier_url(self, worker_id: int) -> FrontierLease | None:
        self._purge_expired_frontier_state()
        queue_mode = self._global_config.queue_mode

        active = self._active_claim_by_worker.get(worker_id)
        if active is not None:
            active.expires_at_monotonic = time.monotonic() + self._lease_ttl_seconds
            self._frontier_leases[active.url] = active
            return active

        if queue_mode in {"server", "both"}:
            server_lease = self._claim_frontier_url_from_manager(worker_id)
            if server_lease is not None:
                return server_lease
            if queue_mode == "server" and not self._allow_daemon_local_fallback:
                return None

        local_lease = self._claim_from_worker_local_queue(worker_id)
        if local_lease is not None:
            return local_lease

        attempts = len(self._frontier_queue)
        while self._frontier_queue and attempts > 0:
            attempts -= 1
            best = self._choose_best_candidate(self._frontier_queue, self._frontier_metadata)
            if best is None:
                return None

            idx, candidate, metadata = best
            del self._frontier_queue[idx]

            if self._is_tombstoned(candidate):
                self._frontier_metadata.pop(candidate, None)
                continue

            if not self._is_site_ready_for_claim(candidate):
                self._frontier_queue.append(candidate)
                continue

            lease = self._frontier_leases.get(candidate)
            if lease is not None and lease.expires_at_monotonic > time.monotonic():
                continue

            self._known_frontier_urls.discard(candidate)
            self._frontier_metadata.pop(candidate, None)
            created = self._create_lease(
                candidate,
                worker_id,
                "global",
                metadata,
            )
            self._emit_manager_event(
                "queue-change",
                worker_id=worker_id,
                payload={
                    "action": "claim-global",
                    "url": candidate,
                    "workerId": worker_id,
                    "inMemoryQueued": len(self._frontier_queue),
                    "priority": metadata.priority,
                    "depth": metadata.depth,
                },
            )
            return created

        return None

    def _claim_from_worker_local_queue(self, worker_id: int) -> FrontierLease | None:
        queue = self._worker_local_queues.get(worker_id)
        if queue is None:
            return None

        attempts = len(queue)
        while queue and attempts > 0:
            attempts -= 1
            best = self._choose_best_candidate(queue, self._worker_local_metadata[worker_id])
            if best is None:
                return None

            idx, candidate, metadata = best
            del queue[idx]

            if self._is_tombstoned(candidate):
                self._worker_local_known_urls[worker_id].discard(candidate)
                self._worker_local_metadata[worker_id].pop(candidate, None)
                continue

            if not self._is_site_ready_for_claim(candidate):
                queue.append(candidate)
                continue

            lease = self._frontier_leases.get(candidate)
            if lease is not None and lease.expires_at_monotonic > time.monotonic() and lease.worker_id != worker_id:
                self._worker_local_known_urls[worker_id].discard(candidate)
                self._worker_local_metadata[worker_id].pop(candidate, None)
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
            self._worker_local_metadata[worker_id].pop(candidate, None)
            created = self._create_lease(
                candidate,
                worker_id,
                "local",
                metadata,
            )
            self._emit_manager_event(
                "queue-change",
                worker_id=worker_id,
                payload={
                    "action": "claim-local",
                    "url": candidate,
                    "workerId": worker_id,
                    "inMemoryWorkerQueued": len(queue),
                    "priority": metadata.priority,
                    "depth": metadata.depth,
                },
            )
            return created

        return None

    def _create_lease(
        self,
        url: str,
        worker_id: int,
        source: str,
        metadata: FrontierMetadata,
    ) -> FrontierLease:
        token = secrets.token_hex(12)
        lease = FrontierLease(
            url=url,
            worker_id=worker_id,
            token=token,
            expires_at_monotonic=time.monotonic() + self._lease_ttl_seconds,
            source=source,
            depth=metadata.depth,
            priority=metadata.priority,
            source_url=metadata.source_url,
        )
        self._frontier_leases[url] = lease
        self._active_claim_by_worker[worker_id] = lease
        self._reserve_site_slot(url)
        self._emit_manager_event(
            "frontier-lease",
            worker_id=worker_id,
            payload={
                "url": url,
                "workerId": worker_id,
                "source": source,
                "leaseTtlSeconds": self._lease_ttl_seconds,
                "priority": metadata.priority,
                "depth": metadata.depth,
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

        completed_on_manager = True
        if lease.source == "server":
            completed_on_manager = self._complete_frontier_claim_on_manager(
                worker_id,
                url,
                lease_token,
                status,
            )

        self._frontier_leases.pop(url, None)
        active = self._active_claim_by_worker.get(worker_id)
        if active is not None and active.url == url:
            self._active_claim_by_worker.pop(worker_id, None)

        self._frontier_metadata.pop(url, None)
        self._worker_local_metadata[worker_id].pop(url, None)

        self._completed_tombstones[url] = time.monotonic() + self._tombstone_ttl_seconds
        self._emit_manager_event(
            "frontier-complete",
            worker_id=worker_id,
            payload={
                "url": url,
                "workerId": worker_id,
                "status": status,
                "source": lease.source,
                "managerSynced": completed_on_manager,
                "tombstoneTtlSeconds": self._tombstone_ttl_seconds,
            },
        )
        return completed_on_manager

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
        self._worker_local_metadata[worker_id].pop(url, None)
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

        manager_synced = True
        if active.source == "server":
            manager_synced = self._complete_frontier_claim_on_manager(
                worker_id,
                active.url,
                active.token,
                "queued" if requeue else "failed",
            )

        self._frontier_leases.pop(active.url, None)
        if (
            active.source != "server"
            and requeue
            and not self._is_tombstoned(active.url)
            and active.url not in self._known_frontier_urls
        ):
            self._known_frontier_urls.add(active.url)
            self._frontier_queue.appendleft(active.url)
            self._frontier_metadata[active.url] = FrontierMetadata(
                priority=active.priority,
                depth=active.depth,
                source_url=active.source_url,
                discovered_at_monotonic=time.monotonic(),
            )

        self._prune_site_access_registry()

        self._emit_manager_event(
            "frontier-release",
            worker_id=worker_id,
            payload={
                "url": active.url,
                "workerId": worker_id,
                "requeued": requeue,
                "reason": reason,
                "source": active.source,
                "managerSynced": manager_synced,
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
            if lease.source == "server":
                self._emit_manager_event(
                    "frontier-lease-expired",
                    worker_id=lease.worker_id,
                    payload={
                        "url": url,
                        "workerId": lease.worker_id,
                        "source": lease.source,
                        "action": "manager-expiry",
                    },
                )
                continue

            if url not in self._known_frontier_urls and not self._is_tombstoned(url):
                self._known_frontier_urls.add(url)
                self._frontier_queue.appendleft(url)
                self._frontier_metadata[url] = FrontierMetadata(
                    priority=lease.priority,
                    depth=lease.depth,
                    source_url=lease.source_url,
                    discovered_at_monotonic=time.monotonic(),
                )
                self._emit_manager_event(
                    "frontier-lease-expired",
                    worker_id=lease.worker_id,
                    payload={
                        "url": url,
                        "workerId": lease.worker_id,
                        "source": lease.source,
                        "action": "requeued",
                    },
                )

        expired_tombstones = [url for url, expiry in self._completed_tombstones.items() if expiry <= now]
        for url in expired_tombstones:
            self._completed_tombstones.pop(url, None)

        self._prune_site_access_registry()

    @staticmethod
    def _site_key_for_url(url: str) -> str:
        try:
            parsed = urlparse(url)
            return (parsed.hostname or url).lower()
        except Exception:
            return url.lower()

    def _is_site_ready_for_claim(self, url: str) -> bool:
        site_key = self._site_key_for_url(url)
        ready_at = self._site_next_available_monotonic.get(site_key)
        if ready_at is None:
            return True
        return time.monotonic() >= ready_at

    def _reserve_site_slot(self, url: str) -> None:
        site_key = self._site_key_for_url(url)
        configured_delay = max(0.0, float(self._global_config.crawl_delay_milliseconds) / 1000.0)
        if configured_delay <= 0:
            self._site_next_available_monotonic.pop(site_key, None)
            return
        self._site_next_available_monotonic[site_key] = time.monotonic() + configured_delay

    def _prune_site_access_registry(self) -> None:
        active_site_keys = {
            self._site_key_for_url(url)
            for url in self._known_frontier_urls
        }
        active_site_keys.update(self._site_key_for_url(url) for url in self._frontier_leases)
        for queue in self._worker_local_queues.values():
            active_site_keys.update(self._site_key_for_url(url) for url in queue)

        stale_keys = [key for key in self._site_next_available_monotonic if key not in active_site_keys]
        for key in stale_keys:
            self._site_next_available_monotonic.pop(key, None)

    def _enqueue_initial_seeds_locked(self) -> int:
        seeds: list[str] = []

        for worker in self._workers.values():
            runtime_seed_lines = worker.runtime_config.get("Seed URLs", "")
            if runtime_seed_lines:
                seeds.extend(
                    line.strip()
                    for line in runtime_seed_lines.splitlines()
                    if line.strip()
                )

        if not seeds:
            seeds.extend(
                entry.url.strip()
                for entry in self._global_config.seed_entries
                if entry.enabled and entry.url.strip()
            )

        unique_seeds = list(dict.fromkeys(seed for seed in seeds if seed))
        queued = 0
        for idx, seed in enumerate(unique_seeds):
            prioritized = max(10, 100 - idx)
            if self._enqueue_frontier_url(seed, source_url=None, depth=0, explicit_priority=prioritized):
                queued += 1

        return queued

    def _set_worker_runtime_state_locked(
        self,
        worker: WorkerRecord,
        *,
        status: str,
        reason: str,
        current_url: str | None,
    ) -> None:
        prev_status = worker.status
        prev_reason = worker.status_reason
        prev_url = worker.current_url

        worker.status = status
        worker.status_reason = reason
        worker.current_url = current_url

        if prev_status != status or prev_reason != reason or prev_url != current_url:
            self._emit_manager_event(
                "status-change",
                worker_id=worker.id,
                payload={
                    "status": status,
                    "previousStatus": prev_status,
                    "reason": reason,
                    "previousReason": prev_reason,
                    "currentUrl": current_url,
                    "previousUrl": prev_url,
                },
            )

    def _is_tombstoned(self, url: str) -> bool:
        expiry = self._completed_tombstones.get(url)
        if expiry is None:
            return False
        if expiry <= time.monotonic():
            self._completed_tombstones.pop(url, None)
            return False
        return True

    def _claim_frontier_url_from_manager(self, worker_id: int) -> FrontierLease | None:
        if self._frontier_claim_url is None:
            return None

        response = self._post_manager_json_response(
            self._frontier_claim_url,
            {
                "workerId": worker_id,
                "daemonId": self._daemon_id,
            },
            token_override=self._frontier_relay_token,
        )
        data = self._extract_manager_response_data(response)
        if not isinstance(data, dict):
            return None
        if not bool(data.get("claimed")):
            return None

        raw_url = str(data.get("url") or "").strip()
        if not raw_url:
            return None
        normalized_url = self._normalize_frontier_url(raw_url) or raw_url

        token_raw = data.get("leaseToken")
        lease_token = str(token_raw).strip() if token_raw is not None else ""
        if not lease_token:
            lease_token = secrets.token_hex(12)

        lease_ttl_seconds = max(5, _coerce_int(data.get("leaseTtlSeconds"), self._lease_ttl_seconds))
        source_url = None
        source_url_raw = data.get("sourceUrl")
        if source_url_raw is None:
            source_url_raw = data.get("source_url")
        if source_url_raw is not None:
            source_url_candidate = str(source_url_raw).strip()
            source_url = self._normalize_frontier_url(source_url_candidate) or None
        priority = _coerce_int(data.get("priority"), 0)
        depth = max(0, _coerce_int(data.get("depth"), 0))

        lease = FrontierLease(
            url=normalized_url,
            worker_id=worker_id,
            token=lease_token,
            expires_at_monotonic=time.monotonic() + lease_ttl_seconds,
            source="server",
            depth=depth,
            priority=priority,
            source_url=source_url,
        )
        self._frontier_leases[normalized_url] = lease
        self._active_claim_by_worker[worker_id] = lease
        self._reserve_site_slot(normalized_url)
        self._emit_manager_event(
            "frontier-lease",
            worker_id=worker_id,
            payload={
                "url": normalized_url,
                "workerId": worker_id,
                "source": "server",
                "leaseTtlSeconds": lease_ttl_seconds,
                "priority": priority,
                "depth": depth,
            },
        )
        self._emit_manager_event(
            "queue-change",
            worker_id=worker_id,
            payload={
                "action": "claim-server",
                "url": normalized_url,
                "workerId": worker_id,
                "inMemoryQueued": len(self._frontier_queue),
                "localQueued": sum(len(queue) for queue in self._worker_local_queues.values()),
                "priority": priority,
                "depth": depth,
            },
        )
        return lease

    def _complete_frontier_claim_on_manager(
        self,
        worker_id: int,
        url: str,
        lease_token: str | None,
        status: str,
    ) -> bool:
        if self._frontier_complete_url is None:
            return False

        response = self._post_manager_json_response(
            self._frontier_complete_url,
            {
                "workerId": worker_id,
                "url": url,
                "leaseToken": lease_token,
                "status": status,
                "daemonId": self._daemon_id,
            },
            token_override=self._frontier_relay_token,
        )
        data = self._extract_manager_response_data(response)
        if isinstance(data, dict) and "completed" in data:
            return bool(data.get("completed"))
        if isinstance(data, bool):
            return data
        if isinstance(response, dict):
            return bool(response.get("ok"))
        return False

    def _relay_frontier_seed(
        self,
        url: str,
        *,
        priority: int,
        depth: int,
        source_url: str | None,
    ) -> bool:
        if self._frontier_relay_url is None:
            return False

        payload = json.dumps(
            {
                "url": url,
                "source": "daemon-frontier",
                "priority": priority,
                "depth": depth,
                "sourceUrl": source_url,
            }
        ).encode("utf-8")
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

    def _download_and_extract_links(self, worker_id: int, url: str) -> dict[str, Any]:
        downloader = Downloader(user_agent=self._global_config.user_agent)
        policy = self._resolve_robots_policy(url)
        robots_allowed = self._robots_allowed_for_url(policy, url)
        effective_delay_seconds = self._effective_delay_seconds(policy, worker_id)

        if not robots_allowed:
            return {
                "ok": False,
                "stage": "robots",
                "error": "Blocked by robots.txt policy.",
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
                    "robotsAllowed": False,
                    "robotsUrl": policy.robots_url if policy else None,
                    "robotsFetched": policy.fetched if policy else False,
                    "robotsCrawlDelaySeconds": policy.crawl_delay_seconds if policy else None,
                    "robotsSitemaps": policy.sitemaps if policy else [],
                    "robotsContent": policy.raw_content if policy else None,
                    "effectiveDelaySeconds": effective_delay_seconds,
                },
                "discoveredLinks": [],
                "discoveredImages": [],
            }

        self._ip_rate_limiter.wait_for_turn(url, effective_delay_seconds)

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
                    "robotsAllowed": robots_allowed,
                    "robotsUrl": policy.robots_url if policy else None,
                    "robotsFetched": policy.fetched if policy else False,
                    "robotsCrawlDelaySeconds": policy.crawl_delay_seconds if policy else None,
                    "robotsSitemaps": policy.sitemaps if policy else [],
                    "robotsContent": policy.raw_content if policy else None,
                    "effectiveDelaySeconds": effective_delay_seconds,
                },
                "discoveredLinks": [],
                "discoveredImages": [],
            }

        try:
            discovered_links: list[str] = []
            discovered_images: list[str] = []
            parser_payload: dict[str, object] | None = None
            if download.html_content:
                extracted = self._link_extractor.extract(download.html_content, download.final_url)
                discovered_links = extracted.links[: self._max_extracted_links_per_page]
                discovered_images = extracted.images[: self._max_extracted_links_per_page]
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
                "downloadResult": self._to_download_payload(
                    download,
                    parser_payload=parser_payload,
                    robots_policy=policy,
                    robots_allowed=robots_allowed,
                    effective_delay_seconds=effective_delay_seconds,
                ),
                "discoveredLinks": discovered_links,
                "discoveredImages": discovered_images,
            }
        except Exception as exc:
            return {
                "ok": False,
                "stage": "parse",
                "error": str(exc),
                "finalUrl": download.final_url,
                "downloadResult": self._to_download_payload(
                    download,
                    parser_payload={
                        "links": [],
                        "jsLinks": [],
                        "images": [],
                        "linksCount": 0,
                        "jsLinksCount": 0,
                        "imagesCount": 0,
                    },
                    robots_policy=policy,
                    robots_allowed=robots_allowed,
                    effective_delay_seconds=effective_delay_seconds,
                ),
                "discoveredLinks": [],
                "discoveredImages": [],
            }

    def _resolve_robots_policy(self, url: str) -> RobotsPolicy | None:
        if not self._global_config.respect_robots_txt:
            return None
        self._robots_policy_manager.user_agent = self._global_config.user_agent
        try:
            return self._robots_policy_manager.get_policy(url)
        except Exception:
            return None

    def _robots_allowed_for_url(self, policy: RobotsPolicy | None, url: str) -> bool:
        if not self._global_config.respect_robots_txt or policy is None:
            return True
        try:
            return policy.allows(self._global_config.user_agent, url)
        except Exception:
            return True

    def _effective_delay_seconds(self, policy: RobotsPolicy | None, worker_id: int | None = None) -> float:
        configured_delay = max(0.0, float(self._global_config.crawl_delay_milliseconds) / 1000.0)
        robots_delay = 0.0 if policy is None or policy.crawl_delay_seconds is None else max(0.0, float(policy.crawl_delay_seconds))
        group_rate_limit_delay = 0.0 if worker_id is None else self._group_rate_limit_delay_seconds(worker_id)
        return max(configured_delay, robots_delay, group_rate_limit_delay)

    def _group_rate_limit_delay_seconds(self, worker_id: int) -> float:
        with self._lock:
            worker_group_limits = [
                int(group.rate_limit_per_minute)
                for group in self._groups.values()
                if worker_id in group.worker_ids
                and group.enabled
                and group.rate_limit_per_minute is not None
                and int(group.rate_limit_per_minute) > 0
            ]

        if not worker_group_limits:
            return 0.0

        # Multiple group assignments should not bypass a stricter cap.
        strictest_limit_per_minute = min(worker_group_limits)
        return 60.0 / float(strictest_limit_per_minute)

    def _robots_policy_allows_enqueue(self, url: str) -> tuple[bool, RobotsPolicy | None]:
        policy = self._resolve_robots_policy(url)
        allowed = self._robots_allowed_for_url(policy, url)
        return allowed, policy

    @staticmethod
    def _to_download_payload(
        download: DownloadResult,
        *,
        parser_payload: dict[str, object] | None = None,
        robots_policy: RobotsPolicy | None = None,
        robots_allowed: bool = True,
        effective_delay_seconds: float | None = None,
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
            "robotsAllowed": robots_allowed,
            "robotsUrl": robots_policy.robots_url if robots_policy else None,
            "robotsFetched": robots_policy.fetched if robots_policy else False,
            "robotsCrawlDelaySeconds": robots_policy.crawl_delay_seconds if robots_policy else None,
            "robotsSitemaps": robots_policy.sitemaps if robots_policy else [],
            "robotsContent": robots_policy.raw_content if robots_policy else None,
            "effectiveDelaySeconds": effective_delay_seconds,
        }

    def _report_page_to_manager(
        self,
        worker: WorkerRecord,
        raw_url: str | None,
        download_result: dict[str, object | None],
        discovered_links: list[str],
        discovered_images: list[str],
    ) -> None:
        if self._manager_ingest_url is None or not raw_url:
            return

        payload = {
            "rawUrl": raw_url,
            "siteId": None,
            "sourcePageId": None,
            "discoveredUrls": discovered_links,
            "discoveredImageUrls": discovered_images,
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

        ordered_events = {
            "queue-change",
            "frontier-lease",
            "frontier-release",
            "frontier-complete",
            "frontier-prune",
            "frontier-lease-expired",
        }

        normalized_payload = payload
        if isinstance(payload, dict) and event_type in ordered_events:
            self._queue_event_order += 1
            normalized_payload = dict(payload)
            normalized_payload["queueOrder"] = self._queue_event_order

        event = {
            "type": event_type,
            "daemonId": self._daemon_id,
            "workerId": worker_id,
            "payload": normalized_payload,
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

    @staticmethod
    def _worker_name(worker_id: int) -> str:
        return f"Worker-{worker_id}"

    def _derive_manager_api_url(self, path: str) -> str | None:
        normalized_path = path if path.startswith("/") else f"/{path}"
        candidates = [
            self._manager_ingest_url,
            self._manager_event_url,
            os.getenv("MANAGER_HTTP_BASE_URL", "").strip() or None,
            os.getenv("MANAGER_SERVER_URL", "").strip() or None,
            os.getenv("MANAGER_DAEMON_WS_URL", "").strip() or None,
        ]
        for candidate in candidates:
            base = self._normalize_manager_http_base(candidate)
            if base:
                return f"{base}{normalized_path}"
        return None

    @staticmethod
    def _normalize_manager_http_base(raw_url: str | None) -> str | None:
        if raw_url is None:
            return None
        candidate = raw_url.strip()
        if not candidate:
            return None

        parsed = urlparse(candidate)
        if not parsed.netloc:
            return None

        if parsed.scheme == "wss":
            scheme = "https"
        elif parsed.scheme == "ws":
            scheme = "http"
        elif parsed.scheme in {"http", "https"}:
            scheme = parsed.scheme
        else:
            return None

        return f"{scheme}://{parsed.netloc}".rstrip("/")

    def _post_manager_json(self, url: str, payload: object) -> bool:
        return self._post_manager_json_response(url, payload) is not None

    def _post_manager_json_response(
        self,
        url: str,
        payload: object,
        *,
        token_override: str | None = None,
        timeout_seconds: float = 2.5,
    ) -> dict[str, object] | None:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        token = token_override if token_override is not None else self._manager_api_token
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = request.Request(url, data=data, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=timeout_seconds) as resp:
                raw = resp.read()
        except (error.HTTPError, error.URLError, TimeoutError, OSError):
            return None

        if not raw:
            return {"ok": True}

        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"ok": True}

        if isinstance(parsed, dict):
            if "ok" not in parsed:
                parsed["ok"] = True
            return parsed

        return {"ok": True, "data": parsed}

    @staticmethod
    def _extract_manager_response_data(response: dict[str, object] | None) -> object | None:
        if response is None:
            return None

        if "data" in response:
            return response.get("data")

        return response

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
