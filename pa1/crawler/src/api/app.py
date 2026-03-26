"""Flask application factory for crawler worker management API."""

from __future__ import annotations

import os
from functools import wraps

from flask import Flask, jsonify, request
from flask_httpauth import HTTPTokenAuth

from api.contracts import utc_now_iso
from api.worker_service import DaemonWorkerService
from api.service_protocol import WorkerControlService


def create_app(service: WorkerControlService | None = None) -> Flask:
    """Create and configure Flask app with worker-management routes."""
    app = Flask(__name__)
    worker_service = service or DaemonWorkerService()

    auth = HTTPTokenAuth(scheme="Bearer")
    configured_token = os.getenv("CRAWLER_API_TOKEN", "").strip()
    auth_enabled = bool(configured_token)

    @auth.verify_token
    def verify_token(token: str) -> str | None:
        if not auth_enabled:
            return "anonymous"
        if token == configured_token:
            return "api-client"
        return None

    def maybe_auth(handler):
        @wraps(handler)
        def wrapped(*args, **kwargs):
            if auth_enabled:
                return auth.login_required(handler)(*args, **kwargs)
            return handler(*args, **kwargs)

        return wrapped

    def envelope(payload: object, *, status_code: int = 200):
        return (
            jsonify(
                {
                    "ok": True,
                    "source": "daemon",
                    "timestampUtc": utc_now_iso(),
                    "data": payload,
                }
            ),
            status_code,
        )

    def error_response(message: str, *, status_code: int = 400):
        return (
            jsonify(
                {
                    "ok": False,
                    "source": "daemon",
                    "timestampUtc": utc_now_iso(),
                    "error": message,
                }
            ),
            status_code,
        )

    @app.get("/api/health")
    def health():
        daemon = worker_service.get_daemon_status()
        return envelope(
            {
                "service": "crawler-api",
                "authRequired": auth_enabled,
                "workerCount": len(worker_service.list_workers()),
                "daemon": daemon,
            }
        )

    @app.get("/api/meta")
    def meta():
        return envelope(
            {
                "note": "Control plane is API-first: server API -> daemon queue -> workers.",
                "persistence": {
                    "workerDirectDb": False,
                    "daemonPolicy": "relay-to-server-first, optional local-db-fallback",
                },
                "endpoints": [
                    "GET /api/workers",
                    "POST /api/workers/<id>/start",
                    "POST /api/workers/<id>/pause",
                    "POST /api/workers/<id>/stop",
                    "POST /api/workers/spawn",
                    "POST /api/workers/reload",
                    "GET /api/daemon",
                    "POST /api/daemon/start",
                    "POST /api/daemon/stop",
                    "POST /api/daemon/reload",
                    "GET /api/workers/<id>/logs",
                    "GET /api/workers/<id>/status",
                    "GET/PUT /api/workers/<id>/config",
                    "GET/PUT /api/config/global",
                    "GET/PUT /api/config/groups/<id>",
                    "POST /api/frontier/seed",
                    "POST /api/frontier/claim",
                    "POST /api/frontier/complete",
                    "POST /api/frontier/prune",
                    "GET /api/frontier/status",
                    "GET /api/statistics",
                ],
            }
        )

    @app.get("/api/daemon")
    @maybe_auth
    def daemon_status():
        return envelope(worker_service.get_daemon_status())

    @app.post("/api/daemon/start")
    @maybe_auth
    def start_daemon():
        return envelope(worker_service.start_daemon())

    @app.post("/api/daemon/stop")
    @maybe_auth
    def stop_daemon():
        return envelope(worker_service.stop_daemon())

    @app.post("/api/daemon/reload")
    @maybe_auth
    def reload_daemon_workers():
        return envelope(worker_service.reload_workers())

    @app.get("/api/workers")
    @maybe_auth
    def list_workers():
        workers = [worker.to_view_model() for worker in worker_service.list_workers()]
        return envelope(workers)

    @app.get("/api/workers/<int:worker_id>/status")
    @maybe_auth
    def get_worker_status(worker_id: int):
        worker = worker_service.get_worker(worker_id)
        if worker is None:
            return error_response(f"Worker {worker_id} not found.", status_code=404)
        return envelope(worker.to_view_model())

    @app.get("/api/workers/<int:worker_id>/logs")
    @maybe_auth
    def get_worker_logs(worker_id: int):
        limit = request.args.get("limit", default=50, type=int) or 50
        logs = [entry.to_view_model() for entry in worker_service.get_worker_logs(worker_id, limit=limit)]
        if not logs and worker_service.get_worker(worker_id) is None:
            return error_response(f"Worker {worker_id} not found.", status_code=404)
        return envelope(logs)

    @app.get("/api/workers/<int:worker_id>/detail")
    @maybe_auth
    def get_worker_detail(worker_id: int):
        detail = worker_service.get_worker_detail(worker_id)
        if detail is None:
            return error_response(f"Worker {worker_id} not found.", status_code=404)
        return envelope(detail)

    @app.post("/api/workers/<int:worker_id>/start")
    @maybe_auth
    def start_worker(worker_id: int):
        if worker_service.get_worker(worker_id) is None:
            return error_response(f"Worker {worker_id} not found.", status_code=404)
        if not worker_service.start_worker(worker_id):
            return error_response("Crawler daemon is not running.", status_code=409)
        worker = worker_service.get_worker(worker_id)
        return envelope(worker.to_view_model() if worker else {"id": worker_id})

    @app.post("/api/workers/<int:worker_id>/pause")
    @maybe_auth
    def pause_worker(worker_id: int):
        if worker_service.get_worker(worker_id) is None:
            return error_response(f"Worker {worker_id} not found.", status_code=404)
        if not worker_service.pause_worker(worker_id):
            return error_response("Crawler daemon is not running.", status_code=409)
        worker = worker_service.get_worker(worker_id)
        return envelope(worker.to_view_model() if worker else {"id": worker_id})

    @app.post("/api/workers/<int:worker_id>/stop")
    @maybe_auth
    def stop_worker(worker_id: int):
        if not worker_service.stop_worker(worker_id):
            return error_response(f"Worker {worker_id} not found.", status_code=404)
        worker = worker_service.get_worker(worker_id)
        return envelope(worker.to_view_model() if worker else {"id": worker_id})

    @app.post("/api/workers/spawn")
    @maybe_auth
    def spawn_worker():
        payload = request.get_json(silent=True) or {}
        raw_seed_urls = payload.get("seedUrls")
        seed_urls: list[str] | None = None
        if isinstance(raw_seed_urls, list):
            seed_urls = [str(url).strip() for url in raw_seed_urls if str(url).strip()]

        try:
            worker = worker_service.spawn_worker(
                name=payload.get("name"),
                mode=str(payload.get("mode", "thread")),
                seed_url=payload.get("seedUrl"),
                seed_urls=seed_urls,
                group_id=payload.get("groupId"),
            )
            return envelope(worker.to_view_model(), status_code=201)
        except RuntimeError as exc:
            return error_response(str(exc), status_code=409)

    @app.post("/api/workers/reload")
    @maybe_auth
    def reload_workers():
        return envelope(worker_service.reload_workers())

    @app.get("/api/workers/<int:worker_id>/config")
    @maybe_auth
    def get_worker_config(worker_id: int):
        detail = worker_service.get_worker_detail(worker_id)
        if detail is None:
            return error_response(f"Worker {worker_id} not found.", status_code=404)
        return envelope(detail.get("runtimeConfig", {}))

    @app.put("/api/workers/<int:worker_id>/config")
    @maybe_auth
    def update_worker_config(worker_id: int):
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return error_response("Invalid payload.", status_code=400)
        if not worker_service.update_worker_runtime_config(
            worker_id, {str(k): str(v) for k, v in payload.items()}
        ):
            return error_response(f"Worker {worker_id} not found.", status_code=404)
        detail = worker_service.get_worker_detail(worker_id)
        return envelope(detail.get("runtimeConfig", {}) if detail else {})

    @app.get("/api/config/global")
    @maybe_auth
    def get_global_config():
        return envelope(worker_service.get_global_config().to_view_model())

    @app.put("/api/config/global")
    @maybe_auth
    def update_global_config():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return error_response("Invalid payload.", status_code=400)
        config = worker_service.update_global_config(payload)
        return envelope(config.to_view_model())

    @app.get("/api/config/groups")
    @maybe_auth
    def list_groups():
        groups = [group.to_view_model() for group in worker_service.list_groups()]
        return envelope(groups)

    @app.put("/api/config/groups/<int:group_id>")
    @maybe_auth
    def update_group(group_id: int):
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return error_response("Invalid payload.", status_code=400)
        group = worker_service.update_group(group_id, payload)
        if group is None:
            return error_response(f"Group {group_id} not found.", status_code=404)
        return envelope(group.to_view_model())

    @app.post("/api/frontier/seed")
    @maybe_auth
    def add_seed():
        payload = request.get_json(silent=True) or {}
        url = str(payload.get("url", "")).strip()
        worker_id = payload.get("workerId")
        if not url:
            return error_response("Payload must include a non-empty 'url'.", status_code=400)
        try:
            result = worker_service.add_seed(int(worker_id) if worker_id is not None else None, url)
            return envelope(result, status_code=201)
        except KeyError as exc:
            return error_response(str(exc), status_code=404)
        except RuntimeError as exc:
            return error_response(str(exc), status_code=409)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)

    @app.post("/api/frontier/claim")
    @maybe_auth
    def claim_frontier_url():
        payload = request.get_json(silent=True) or {}
        worker_id = payload.get("workerId")
        if worker_id is None:
            return error_response("Payload must include 'workerId'.", status_code=400)
        try:
            result = worker_service.claim_frontier_url(int(worker_id))
            return envelope(result)
        except KeyError as exc:
            return error_response(str(exc), status_code=404)

    @app.post("/api/frontier/complete")
    @maybe_auth
    def complete_frontier_url():
        payload = request.get_json(silent=True) or {}
        worker_id = payload.get("workerId")
        url = str(payload.get("url", "")).strip()
        lease_token = payload.get("leaseToken")
        status = str(payload.get("status", "completed")).strip().lower() or "completed"
        if worker_id is None or not url:
            return error_response("Payload must include 'workerId' and non-empty 'url'.", status_code=400)
        try:
            result = worker_service.complete_frontier_url(int(worker_id), url, str(lease_token) if lease_token else None, status)
            return envelope(result)
        except KeyError as exc:
            return error_response(str(exc), status_code=404)

    @app.post("/api/frontier/prune")
    @maybe_auth
    def prune_frontier_url():
        payload = request.get_json(silent=True) or {}
        worker_id = payload.get("workerId")
        url = str(payload.get("url", "")).strip()
        reason = str(payload.get("reason", "server-conflict")).strip() or "server-conflict"
        if worker_id is None or not url:
            return error_response("Payload must include 'workerId' and non-empty 'url'.", status_code=400)
        try:
            result = worker_service.prune_local_frontier(int(worker_id), url, reason)
            return envelope(result)
        except KeyError as exc:
            return error_response(str(exc), status_code=404)

    @app.get("/api/frontier/status")
    @maybe_auth
    def frontier_status():
        daemon = worker_service.get_daemon_status()
        return envelope(daemon.get("frontier", {}))

    @app.get("/api/statistics")
    @maybe_auth
    def statistics():
        return envelope(worker_service.get_statistics())

    return app
