"""Standalone daemon runtime with websocket-only manager control."""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from api.reverse_channel import ReverseChannelClient, build_reverse_channel_url_from_env
from api.worker_service import DaemonWorkerService


def main() -> int:
    service = DaemonWorkerService()
    daemon_id = os.getenv("CRAWLER_DAEMON_ID", "local-default").strip() or "local-default"
    reverse_channel = ReverseChannelClient(
        daemon_id=daemon_id,
        manager_ws_url=build_reverse_channel_url_from_env(),
        snapshot_provider=lambda: _build_snapshot(service),
        command_handler=lambda payload: _handle_reverse_command(service, payload),
        request_handler=lambda action, payload: _handle_reverse_request(service, action, payload),
        logger=lambda msg: print(f"[reverse-channel] {msg}"),
    )
    reverse_channel.start()
    _start_parent_watchdog_if_configured()

    try:
        while True:
            time.sleep(1.0)
    finally:
        reverse_channel.stop()

    return 0


def _start_parent_watchdog_if_configured() -> None:
    raw_parent_pid = os.getenv("MANAGER_PARENT_PID", "").strip()
    if not raw_parent_pid:
        return

    try:
        parent_pid = int(raw_parent_pid)
    except ValueError:
        return

    if parent_pid <= 1:
        return

    def watch() -> None:
        while True:
            if os.getppid() == 1:
                print("[daemon-watchdog] parent process is gone; exiting daemon")
                os._exit(0)

            try:
                os.kill(parent_pid, 0)
            except OSError:
                print("[daemon-watchdog] parent PID no longer alive; exiting daemon")
                os._exit(0)

            time.sleep(1.0)

    threading.Thread(target=watch, daemon=True, name="daemon-parent-watchdog").start()


def _build_snapshot(service: DaemonWorkerService) -> dict[str, object]:
    daemon_status = service.get_daemon_status()
    workers = [_serialize_worker(worker) for worker in service.list_workers()]
    return {
        "daemon": daemon_status,
        "workers": workers,
        "globalConfig": service.get_global_config().to_view_model(),
        "groups": [group.to_view_model() for group in service.list_groups()],
        "frontierStatus": daemon_status.get("frontier", {}),
    }


def _serialize_worker(worker: Any) -> dict[str, object]:
    view = worker.to_view_model()
    view["mode"] = getattr(worker, "mode", "thread")
    view["pid"] = getattr(worker, "pid", None)
    view["runtimeConfig"] = dict(getattr(worker, "runtime_config", {}) or {})
    return view


def _coerce_worker_id(payload: dict[str, object]) -> int | None:
    worker_id_raw = payload.get("workerId")
    if worker_id_raw is None:
        return None

    try:
        return int(str(worker_id_raw))
    except ValueError:
        return None


def _handle_reverse_command(service: DaemonWorkerService, payload: dict[str, object]) -> tuple[bool, str | None]:
    command = str(payload.get("command", "")).strip().lower()
    nested_payload = payload.get("payload")
    nested = nested_payload if isinstance(nested_payload, dict) else {}
    worker_id = _coerce_worker_id(payload) or _coerce_worker_id(nested)

    if command == "start-daemon":
        service.start_daemon()
        return True, None
    if command == "stop-daemon":
        service.stop_daemon()
        return True, None
    if command == "reload-daemon":
        service.reload_workers()
        return True, None
    if command == "start-worker" and worker_id is not None:
        ok = service.start_worker(worker_id)
        return ok, None if ok else f"Failed to start worker {worker_id}."
    if command == "pause-worker" and worker_id is not None:
        ok = service.pause_worker(worker_id)
        return ok, None if ok else f"Failed to pause worker {worker_id}."
    if command == "stop-worker" and worker_id is not None:
        ok = service.stop_worker(worker_id)
        return ok, None if ok else f"Failed to stop worker {worker_id}."

    return False, f"Unsupported or invalid command: {command}"


def _handle_reverse_request(
    service: DaemonWorkerService,
    action: str,
    payload: dict[str, object],
) -> tuple[bool, object | None, str | None]:
    normalized_action = action.strip().lower()
    worker_id = _coerce_worker_id(payload)

    if normalized_action == "get-daemon-status":
        return True, service.get_daemon_status(), None

    if normalized_action == "start-daemon":
        return True, service.start_daemon(), None

    if normalized_action == "stop-daemon":
        return True, service.stop_daemon(), None

    if normalized_action == "reload-daemon":
        return True, service.reload_workers(), None

    if normalized_action == "get-workers":
        return True, [_serialize_worker(worker) for worker in service.list_workers()], None

    if normalized_action == "get-worker":
        if worker_id is None:
            return False, None, "Missing workerId."
        worker = service.get_worker(worker_id)
        return (True, _serialize_worker(worker), None) if worker is not None else (False, None, f"Worker {worker_id} not found.")

    if normalized_action == "start-worker":
        if worker_id is None:
            return False, None, "Missing workerId."
        started = service.start_worker(worker_id)
        if not started:
            return False, None, f"Failed to start worker {worker_id}."
        worker = service.get_worker(worker_id)
        return True, _serialize_worker(worker) if worker is not None else {"id": worker_id}, None

    if normalized_action == "pause-worker":
        if worker_id is None:
            return False, None, "Missing workerId."
        paused = service.pause_worker(worker_id)
        if not paused:
            return False, None, f"Failed to pause worker {worker_id}."
        worker = service.get_worker(worker_id)
        return True, _serialize_worker(worker) if worker is not None else {"id": worker_id}, None

    if normalized_action == "stop-worker":
        if worker_id is None:
            return False, None, "Missing workerId."
        stopped = service.stop_worker(worker_id)
        if not stopped:
            return False, None, f"Failed to stop worker {worker_id}."
        worker = service.get_worker(worker_id)
        return True, _serialize_worker(worker) if worker is not None else {"id": worker_id}, None

    if normalized_action == "get-worker-detail":
        if worker_id is None:
            return False, None, "Missing workerId."
        detail = service.get_worker_detail(worker_id)
        return (True, detail, None) if detail is not None else (False, None, f"Worker {worker_id} not found.")

    if normalized_action == "get-global-config":
        return True, service.get_global_config().to_view_model(), None

    if normalized_action == "save-global-config":
        return True, service.update_global_config(payload).to_view_model(), None

    if normalized_action == "get-groups":
        return True, [group.to_view_model() for group in service.list_groups()], None

    if normalized_action == "save-group":
        group_id_raw = payload.get("id") or payload.get("groupId")
        try:
            group_id = int(str(group_id_raw))
        except (TypeError, ValueError):
            return False, None, "Missing valid group id."
        group = service.update_group(group_id, payload)
        return (True, group.to_view_model(), None) if group is not None else (False, None, f"Group {group_id} not found.")

    if normalized_action == "spawn-worker":
        try:
            seed_urls_raw = payload.get("seedUrls")
            seed_urls = seed_urls_raw if isinstance(seed_urls_raw, list) else None
            worker = service.spawn_worker(
                name=str(payload.get("name")).strip() if payload.get("name") is not None else None,
                mode=str(payload.get("mode", "thread")),
                seed_url=str(payload.get("seedUrl")).strip() if payload.get("seedUrl") is not None else None,
                seed_urls=[str(url).strip() for url in seed_urls or [] if str(url).strip()] or None,
                group_id=int(str(payload.get("groupId"))) if payload.get("groupId") is not None else None,
            )
            return True, _serialize_worker(worker), None
        except (RuntimeError, ValueError) as exc:
            return False, None, str(exc)

    if normalized_action == "add-seed":
        url = str(payload.get("url", "")).strip()
        if not url:
            return False, None, "Missing seed URL."
        return True, service.add_seed(worker_id, url), None

    if normalized_action == "claim-frontier":
        if worker_id is None:
            return False, None, "Missing workerId."
        return True, service.claim_frontier_url(worker_id), None

    if normalized_action == "complete-frontier":
        if worker_id is None:
            return False, None, "Missing workerId."
        url = str(payload.get("url", "")).strip()
        if not url:
            return False, None, "Missing frontier URL."
        lease_token_raw = payload.get("leaseToken")
        lease_token = str(lease_token_raw) if lease_token_raw is not None else None
        return True, service.complete_frontier_url(
            worker_id,
            url,
            lease_token,
            str(payload.get("status", "completed")).strip() or "completed",
        ), None

    if normalized_action == "prune-frontier":
        if worker_id is None:
            return False, None, "Missing workerId."
        url = str(payload.get("url", "")).strip()
        if not url:
            return False, None, "Missing frontier URL."
        reason = str(payload.get("reason", "server-conflict")).strip() or "server-conflict"
        return True, service.prune_local_frontier(worker_id, url, reason), None

    if normalized_action == "get-frontier-status":
        return True, service.get_daemon_status().get("frontier", {}), None

    if normalized_action == "dequeue-frontier":
        worker_ids_raw = payload.get("workerIds")
        worker_ids = None
        if isinstance(worker_ids_raw, list):
            worker_ids = []
            for item in worker_ids_raw:
                try:
                    worker_ids.append(int(str(item)))
                except ValueError:
                    continue
        limit = 20
        try:
            limit = int(str(payload.get("limit", 20)))
        except ValueError:
            pass
        daemon_id = str(payload.get("daemonId")).strip() if payload.get("daemonId") is not None else None
        return True, service.dequeue_frontier_urls(worker_ids, limit=limit, daemon_id=daemon_id), None

    return False, None, f"Unsupported request action: {action}"
