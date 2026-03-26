"""Standalone entrypoint for crawler worker-management API."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Keep direct execution working from repository root.
CRAWLER_SRC = Path(__file__).resolve().parent
if str(CRAWLER_SRC) not in sys.path:
    sys.path.insert(0, str(CRAWLER_SRC))

from api.app import create_app
from api.mock_worker_service import MockWorkerService
from api.reverse_channel import ReverseChannelClient, build_reverse_channel_url_from_env


def main() -> int:
    service = MockWorkerService()
    app = create_app(service)

    host = os.getenv("CRAWLER_API_HOST", "127.0.0.1")
    port = int(os.getenv("CRAWLER_API_PORT", "8090"))
    debug = os.getenv("CRAWLER_API_DEBUG", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    daemon_id = os.getenv("CRAWLER_DAEMON_ID", "local-default")
    reverse_channel = ReverseChannelClient(
        daemon_id=daemon_id,
        manager_ws_url=build_reverse_channel_url_from_env(),
        status_provider=service.get_daemon_status,
        command_handler=lambda payload: _handle_reverse_command(service, payload),
        logger=lambda msg: print(f"[reverse-channel] {msg}"),
    )
    reverse_channel.start()

    try:
        app.run(host=host, port=port, debug=debug)
    finally:
        reverse_channel.stop()
    return 0


def _handle_reverse_command(service: MockWorkerService, payload: dict[str, object]) -> tuple[bool, str | None]:
    command = str(payload.get("command", "")).strip().lower()
    worker_id_raw = payload.get("workerId")
    nested_payload = payload.get("payload")
    if worker_id_raw is None and isinstance(nested_payload, dict):
        worker_id_raw = nested_payload.get("workerId")
    worker_id: int | None = None
    if worker_id_raw is not None:
        try:
            worker_id = int(str(worker_id_raw))
        except ValueError:
            worker_id = None

    if command == "start-daemon":
        service.start_daemon()
        return True, None
    elif command == "stop-daemon":
        service.stop_daemon()
        return True, None
    elif command == "reload-daemon":
        service.reload_workers()
        return True, None
    elif command == "start-worker" and worker_id is not None:
        return service.start_worker(worker_id), None
    elif command == "pause-worker" and worker_id is not None:
        return service.pause_worker(worker_id), None
    elif command == "stop-worker" and worker_id is not None:
        return service.stop_worker(worker_id), None

    return False, f"Unsupported or invalid command: {command}"


if __name__ == "__main__":
    raise SystemExit(main())
