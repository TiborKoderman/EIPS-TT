"""Mock worker-control service for API development and UI integration."""

from __future__ import annotations

import random
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from api.contracts import (
    GlobalWorkerConfig,
    WorkerGroupSettings,
    WorkerLogEntry,
    WorkerRecord,
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
                worker_ids=[1],
            ),
        }
        self._logs: dict[int, deque[WorkerLogEntry]] = defaultdict(lambda: deque(maxlen=500))
        self._local_processes: dict[int, subprocess.Popen[bytes]] = {}

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

            for worker_id in list(self._local_processes):
                self._terminate_process_if_running(worker_id)

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
            worker.status = "Active"
            worker.started_at = utc_now_iso()
            self._append_log(worker_id, "Info", "Worker started via API.")
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
            return True

    def stop_worker(self, worker_id: int) -> bool:
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                return False
            worker.status = "Stopped"
            worker.current_url = None
            self._terminate_process_if_running(worker_id)
            self._append_log(worker_id, "Info", "Worker stopped via API.")
            return True

    def spawn_worker(
        self,
        *,
        name: str | None,
        mode: str,
        seed_url: str | None,
        group_id: int | None,
    ) -> WorkerRecord:
        requested_mode = (mode or "mock").strip().lower()
        if requested_mode not in {"mock", "local"}:
            requested_mode = "mock"

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
                current_url=seed_url,
                pages_processed=0,
                error_count=0,
                started_at=None,
                mode=requested_mode,
                runtime_config={
                    "Retries": "3",
                    "Render JS": "false",
                    "Depth Limit": "5",
                },
            )
            self._workers[worker_id] = worker
            target_group_id = group_id if group_id in self._groups else 1
            self._groups[target_group_id].worker_ids.append(worker_id)

            self._append_log(worker_id, "Info", f"Worker spawned in {requested_mode} mode.")
            if seed_url:
                self._append_log(worker_id, "Info", f"Initial seed assigned: {seed_url}")

            if requested_mode == "local":
                self._launch_local_worker_process(worker_id, seed_url)

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
            )

    def update_global_config(self, payload: dict[str, object]) -> GlobalWorkerConfig:
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
            )
            return self.get_global_config()

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
            if "workerIds" in payload and isinstance(payload["workerIds"], list):
                group.worker_ids = [int(value) for value in payload["workerIds"]]

            return WorkerGroupSettings(
                id=group.id,
                name=group.name,
                description=group.description,
                enabled=group.enabled,
                max_pages_per_worker=group.max_pages_per_worker,
                rate_limit_per_minute=group.rate_limit_per_minute,
                worker_ids=list(group.worker_ids),
            )

    def add_seed(self, worker_id: int | None, url: str) -> dict[str, object]:
        if not url:
            raise ValueError("Seed URL must not be empty.")
        with self._lock:
            if not self._daemon_running:
                raise RuntimeError("Crawler daemon is not running.")
            if worker_id is None:
                for worker in self._workers.values():
                    if worker.status == "Active":
                        worker.current_url = url
                        self._append_log(worker.id, "Info", f"Seed assigned globally: {url}")
                return {
                    "appliedTo": "active-workers",
                    "workerId": None,
                    "url": url,
                }

            worker = self._workers.get(worker_id)
            if worker is None:
                raise KeyError(f"Worker {worker_id} was not found.")
            worker.current_url = url
            self._append_log(worker_id, "Info", f"Seed assigned: {url}")
            return {
                "appliedTo": "single-worker",
                "workerId": worker_id,
                "url": url,
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

    def _append_log(self, worker_id: int, level: str, message: str) -> None:
        self._logs[worker_id].append(
            WorkerLogEntry(timestamp_utc=utc_now_iso(), level=level, message=message)
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

                    worker.pages_processed += 1
                    worker.current_url = random.choice(sample_urls)

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
