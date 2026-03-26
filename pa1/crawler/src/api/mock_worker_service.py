"""Mock worker-control service for API development and UI integration."""

from __future__ import annotations

import random
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from collections import defaultdict, deque
import os
from pathlib import Path
from urllib import error, request
import json

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


class MockWorkerService(WorkerControlService):
    """In-memory worker manager with optional local process spawning."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._daemon_running = False
        self._daemon_started_at: str | None = None
        self._daemon_mode = "single-instance"
        self._workers: dict[int, WorkerRecord] = {
            1: WorkerRecord(
                id=1,
                name="Worker-1",
                status="Idle",
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
        self._next_worker_id = max(self._workers) + 1
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

        self._frontier_relay_url = os.getenv("MANAGER_FRONTIER_INGEST_URL", "").strip() or None
        self._frontier_relay_token = os.getenv("MANAGER_FRONTIER_INGEST_TOKEN", "").strip() or None
        self._manager_ingest_url = os.getenv("MANAGER_INGEST_API_URL", "").strip() or None
        self._manager_event_url = os.getenv("MANAGER_EVENT_API_URL", "").strip() or None
        self._manager_api_token = os.getenv("MANAGER_INGEST_API_TOKEN", "").strip() or None
        self._allow_daemon_db_fallback = os.getenv(
            "CRAWLER_DAEMON_ALLOW_DB_FALLBACK", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}

        for worker in self._workers.values():
            self._append_log(worker.id, "Info", f"{worker.name} initialized in {worker.mode} mode.")

        self._simulator_thread = threading.Thread(target=self._simulation_loop, daemon=True)
        self._simulator_thread.start()

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
                worker.current_url = None
                worker.pid = None
                self._append_log(worker.id, "Info", "Daemon stopped; worker transitioned to Stopped.")
                self._emit_manager_event(
                    "status-change",
                    worker_id=worker.id,
                    payload={"status": "Stopped", "reason": "daemon-stop"},
                )

            for worker_id in list(self._local_processes):
                self._terminate_process_if_running(worker_id)
            for worker_id in list(self._thread_workers):
                self._stop_thread_worker(worker_id)

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
            if worker.mode == "thread" and worker_id not in self._thread_workers:
                self._launch_thread_worker(worker_id, worker.current_url)
                return True
            worker.status = "Active"
            worker.started_at = utc_now_iso()
            self._append_log(worker_id, "Info", "Worker started via API.")
            self._emit_manager_event("status-change", worker_id=worker_id, payload={"status": "Active"})
            return True

    def pause_worker(self, worker_id: int) -> bool:
        with self._lock:
            if not self._daemon_running:
                return False
            worker = self._workers.get(worker_id)
            if worker is None:
                return False
            worker.status = "Paused"
            self._append_log(worker_id, "Info", "Worker paused via API.")
            self._emit_manager_event("status-change", worker_id=worker_id, payload={"status": "Paused"})
            return True

    def stop_worker(self, worker_id: int) -> bool:
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                return False
            worker.status = "Stopped"
            worker.current_url = None
            self._terminate_process_if_running(worker_id)
            self._stop_thread_worker(worker_id)
            self._append_log(worker_id, "Info", "Worker stopped via API.")
            self._emit_manager_event("status-change", worker_id=worker_id, payload={"status": "Stopped"})
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
        requested_mode = (mode or "mock").strip().lower()
        if requested_mode not in {"mock", "local", "thread"}:
            requested_mode = "mock"

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

            if requested_mode == "local":
                self._launch_local_worker_process(worker_id, effective_seed_url)
            elif requested_mode == "thread":
                self._launch_thread_worker(worker_id, effective_seed_url)

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
                    worker.current_url = None
                    reloaded += 1
                    self._append_log(worker.id, "Info", "Reload: worker state set to Idle.")
            for worker_id in list(self._local_processes):
                self._terminate_process_if_running(worker_id)
            for worker_id in list(self._thread_workers):
                self._stop_thread_worker(worker_id)
        return {
            "reloadedWorkers": reloaded,
            "timestampUtc": utc_now_iso(),
            "note": "Mock reload applied. Active workers were reset to Idle.",
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
            queue_mode = "server"

        strategy_mode = str(payload.get("strategyMode", self._global_config.strategy_mode)).strip().lower()
        if strategy_mode not in {"balanced", "coverage", "focused", "freshness"}:
            strategy_mode = "balanced"

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

            queued = self._enqueue_frontier_url(url)
            if worker_id is None:
                return {
                    "appliedTo": "daemon-frontier",
                    "workerId": None,
                    "url": url,
                    "queued": queued,
                    "inMemoryQueued": len(self._frontier_queue),
                }

            worker = self._workers.get(worker_id)
            if worker is None:
                raise KeyError(f"Worker {worker_id} was not found.")
            self._append_log(worker_id, "Info", f"Seed routed to daemon frontier: {url}")
            return {
                "appliedTo": "daemon-frontier",
                "workerId": worker_id,
                "url": url,
                "queued": queued,
                "inMemoryQueued": len(self._frontier_queue),
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
                "isMock": True,
                "source": "mock",
                "parallelismModel": {
                    "instanceLevel": "single-daemon",
                    "workerLevel": "multi-worker",
                },
                "timestampUtc": utc_now_iso(),
            }

    def _launch_local_worker_process(self, worker_id: int, seed_url: str | None) -> None:
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

    def _launch_thread_worker(self, worker_id: int, seed_url: str | None) -> None:
        worker = self._workers[worker_id]
        stop_event = threading.Event()
        if seed_url:
            self._enqueue_frontier_url(seed_url)

        def run() -> None:
            sample_urls = [
                "https://example.com/thread/news",
                "https://example.com/thread/about",
                "https://gov.si/thread",
                "https://e-uprava.gov.si/thread",
            ]
            if seed_url:
                sample_urls.insert(0, seed_url)

            while not stop_event.is_set():
                time.sleep(1.2)
                with self._lock:
                    current = self._workers.get(worker_id)
                    if current is None or current.status != "Active":
                        continue
                    current.pages_processed += 1
                    next_url = self._pop_frontier_url()
                    current.current_url = next_url or random.choice(sample_urls)
                    self._report_page_to_manager(current, current.current_url)
                    if random.random() < 0.05:
                        current.error_count += 1
                        self._append_log(worker_id, "Warning", "Thread worker fetch retry scheduled.")

        thread = threading.Thread(target=run, daemon=True, name=f"worker-thread-{worker_id}")
        self._thread_workers[worker_id] = (thread, stop_event)
        worker.status = "Active"
        worker.started_at = utc_now_iso()
        worker.pid = None
        thread.start()
        self._append_log(worker_id, "Info", "Thread worker started.")

    def _stop_thread_worker(self, worker_id: int) -> None:
        entry = self._thread_workers.pop(worker_id, None)
        if entry is None:
            return

        thread, stop_event = entry
        stop_event.set()
        thread.join(timeout=2)
        worker = self._workers.get(worker_id)
        if worker is not None:
            worker.pid = None
        self._append_log(worker_id, "Info", "Thread worker stopped.")

    def _append_log(self, worker_id: int, level: str, message: str) -> None:
        self._logs[worker_id].append(
            WorkerLogEntry(timestamp_utc=utc_now_iso(), level=level, message=message)
        )
        self._emit_manager_event(
            "worker-log",
            worker_id=worker_id,
            payload={"level": level, "message": message},
        )

    def _simulation_loop(self) -> None:
        sample_urls = [
            "https://example.com/news",
            "https://example.com/about",
            "https://example.com/contact",
            "https://gov.si/novice",
            "https://e-uprava.gov.si/",
        ]
        while True:
            time.sleep(1.5)
            with self._lock:
                for worker in self._workers.values():
                    if worker.status != "Active":
                        continue
                    if worker.mode == "thread":
                        continue

                    worker.pages_processed += 1
                    next_url = self._pop_frontier_url()
                    worker.current_url = next_url or random.choice(sample_urls)
                    self._report_page_to_manager(worker, worker.current_url)

                    if random.random() < 0.03:
                        worker.error_count += 1
                        self._append_log(worker.id, "Warning", "Transient fetch failure, retry scheduled.")

                    if random.random() < 0.12:
                        self._append_log(
                            worker.id,
                            "Info",
                            f"Fetched {worker.current_url} (pages={worker.pages_processed}).",
                        )

                for worker_id, process in list(self._local_processes.items()):
                    if process.poll() is not None:
                        worker = self._workers.get(worker_id)
                        if worker is not None and worker.status == "Active":
                            worker.status = "Idle"
                            worker.pid = None
                            self._append_log(worker_id, "Info", "Local process finished.")
                        self._local_processes.pop(worker_id, None)

    def _enqueue_frontier_url(self, raw_url: str) -> bool:
        candidate = raw_url.strip()
        if not candidate:
            return False
        if candidate in self._known_frontier_urls:
            return False

        queue_mode = self._global_config.queue_mode
        relayed = False
        if queue_mode in {"server", "both"}:
            relayed = self._relay_frontier_seed(candidate)

        should_enqueue_local = queue_mode in {"local", "both"}
        if queue_mode == "server" and not relayed and self._allow_daemon_db_fallback:
            should_enqueue_local = True

        self._known_frontier_urls.add(candidate)
        if should_enqueue_local:
            self._frontier_queue.append(candidate)
        self._emit_manager_event(
            "queue-change",
            payload={
                "action": "enqueue",
                "url": candidate,
                "queueMode": queue_mode,
                "inMemoryQueued": len(self._frontier_queue),
                "knownUrls": len(self._known_frontier_urls),
                "relayed": relayed,
            },
        )
        return True

    def _pop_frontier_url(self) -> str | None:
        if not self._frontier_queue:
            return None
        value = self._frontier_queue.popleft()
        self._emit_manager_event(
            "queue-change",
            payload={
                "action": "dequeue",
                "url": value,
                "inMemoryQueued": len(self._frontier_queue),
                "knownUrls": len(self._known_frontier_urls),
            },
        )
        return value

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

    def _report_page_to_manager(self, worker: WorkerRecord, url: str | None) -> None:
        if self._manager_ingest_url is None or not url:
            return

        payload = {
            "rawUrl": url,
            "siteId": None,
            "sourcePageId": None,
            "downloadResult": {
                "requestedUrl": url,
                "finalUrl": url,
                "statusCode": 200,
                "contentType": "text/html",
                "dataTypeCode": None,
                "pageTypeCode": "HTML",
                "htmlContent": f"<html><body><h1>{worker.name}</h1><p>{url}</p></body></html>",
                "usedRenderer": False,
                "contentLength": None,
            },
        }

        self._post_manager_json(self._manager_ingest_url, payload)
        self._emit_manager_event(
            "page-reported",
            worker_id=worker.id,
            payload={
                "url": url,
                "pagesProcessed": worker.pages_processed,
                "status": worker.status,
                "reportedAt": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _emit_manager_event(self, event_type: str, worker_id: int | None = None, payload: object | None = None) -> None:
        if self._manager_event_url is None:
            return

        self._post_manager_json(
            self._manager_event_url,
            {
                "type": event_type,
                "daemonId": "local-default",
                "workerId": worker_id,
                "payload": payload,
            },
        )

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
            current_url=worker.current_url,
            pages_processed=worker.pages_processed,
            error_count=worker.error_count,
            started_at=worker.started_at,
            mode=worker.mode,
            pid=worker.pid,
            runtime_config=dict(worker.runtime_config),
        )
