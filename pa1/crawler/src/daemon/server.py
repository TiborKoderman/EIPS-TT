"""Standalone daemon runtime with websocket-only manager control."""

from __future__ import annotations

import os
import threading
import time

from api.reverse_channel import ReverseChannelClient, build_reverse_channel_url_from_env
from api.worker_service import DaemonWorkerService
from daemon.handlers import build_snapshot, handle_reverse_command, handle_reverse_request


def main() -> int:
    service = DaemonWorkerService()
    daemon_id = os.getenv("CRAWLER_DAEMON_ID", "local-default").strip() or "local-default"
    reverse_channel = ReverseChannelClient(
        daemon_id=daemon_id,
        manager_ws_url=build_reverse_channel_url_from_env(),
        snapshot_provider=lambda: build_snapshot(service),
        command_handler=lambda payload: handle_reverse_command(service, payload),
        request_handler=lambda action, payload: handle_reverse_request(service, action, payload),
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


if __name__ == "__main__":
    raise SystemExit(main())
