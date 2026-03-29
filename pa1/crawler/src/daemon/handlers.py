"""Daemon control handlers extracted from runtime loop for clarity and reuse."""

from __future__ import annotations

from typing import Any

from api.worker_service import DaemonWorkerService


def _run_worker_action(
    service: DaemonWorkerService,
    *,
    action: str,
    worker_id: int,
) -> tuple[bool, str | None]:
    if action == "start":
        ok = service.start_worker(worker_id)
    elif action == "pause":
        ok = service.pause_worker(worker_id)
    elif action == "stop":
        ok = service.stop_worker(worker_id)
    else:
        return False, f"Unsupported worker action: {action}"

    return ok, None if ok else f"Failed to {action} worker {worker_id}."


def _run_worker_action_with_view(
    service: DaemonWorkerService,
    *,
    action: str,
    worker_id: int,
) -> tuple[bool, dict[str, object] | None, str | None]:
    ok, error = _run_worker_action(service, action=action, worker_id=worker_id)
    if not ok:
        return False, None, error
    worker = service.get_worker(worker_id)
    return True, serialize_worker(worker) if worker is not None else {"id": worker_id}, None


def build_snapshot(service: DaemonWorkerService) -> dict[str, object]:
    daemon_status = service.get_daemon_status()
    workers = [serialize_worker(worker) for worker in service.list_workers()]
    return {
        "daemon": daemon_status,
        "workers": workers,
        "globalConfig": service.get_global_config().to_view_model(),
        "groups": [group.to_view_model() for group in service.list_groups()],
        "frontierStatus": daemon_status.get("frontier", {}),
    }


def serialize_worker(worker: Any) -> dict[str, object]:
    view = worker.to_view_model()
    view["mode"] = getattr(worker, "mode", "thread")
    view["pid"] = getattr(worker, "pid", None)
    view["runtimeConfig"] = dict(getattr(worker, "runtime_config", {}) or {})
    return view


def coerce_worker_id(payload: dict[str, object]) -> int | None:
    worker_id_raw = payload.get("workerId")
    if worker_id_raw is None:
        return None

    try:
        return int(str(worker_id_raw))
    except ValueError:
        return None


def handle_reverse_command(service: DaemonWorkerService, payload: dict[str, object]) -> tuple[bool, str | None]:
    command = str(payload.get("command", "")).strip().lower()
    nested_payload = payload.get("payload")
    nested = nested_payload if isinstance(nested_payload, dict) else {}
    worker_id = coerce_worker_id(payload) or coerce_worker_id(nested)

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
        ok, err = _run_worker_action(service, action="start", worker_id=worker_id)
        return ok, err
    if command == "pause-worker" and worker_id is not None:
        ok, err = _run_worker_action(service, action="pause", worker_id=worker_id)
        return ok, err
    if command == "stop-worker" and worker_id is not None:
        ok, err = _run_worker_action(service, action="stop", worker_id=worker_id)
        return ok, err

    return False, f"Unsupported or invalid command: {command}"


def handle_reverse_request(
    service: DaemonWorkerService,
    action: str,
    payload: dict[str, object],
) -> tuple[bool, object | None, str | None]:
    normalized_action = action.strip().lower()
    worker_id = coerce_worker_id(payload)

    if normalized_action == "get-daemon-status":
        return True, service.get_daemon_status(), None

    if normalized_action == "start-daemon":
        return True, service.start_daemon(), None

    if normalized_action == "stop-daemon":
        return True, service.stop_daemon(), None

    if normalized_action == "reload-daemon":
        return True, service.reload_workers(), None

    if normalized_action == "get-workers":
        return True, [serialize_worker(worker) for worker in service.list_workers()], None

    if normalized_action == "get-worker":
        if worker_id is None:
            return False, None, "Missing workerId."
        worker = service.get_worker(worker_id)
        return (True, serialize_worker(worker), None) if worker is not None else (False, None, f"Worker {worker_id} not found.")

    if normalized_action == "start-worker":
        if worker_id is None:
            return False, None, "Missing workerId."
        return _run_worker_action_with_view(service, action="start", worker_id=worker_id)

    if normalized_action == "pause-worker":
        if worker_id is None:
            return False, None, "Missing workerId."
        return _run_worker_action_with_view(service, action="pause", worker_id=worker_id)

    if normalized_action == "stop-worker":
        if worker_id is None:
            return False, None, "Missing workerId."
        return _run_worker_action_with_view(service, action="stop", worker_id=worker_id)

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
            return True, serialize_worker(worker), None
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
