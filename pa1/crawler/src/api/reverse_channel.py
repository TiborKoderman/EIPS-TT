"""Reverse websocket channel client for daemon-initiated manager connectivity."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

try:
    import websocket  # type: ignore
except Exception:  # pragma: no cover
    websocket = None


class ReverseChannelClient:
    """Maintains outbound websocket connection from daemon to manager server."""

    def __init__(
        self,
        *,
        daemon_id: str,
        manager_ws_url: str | None,
        status_provider: Callable[[], dict[str, object]],
        command_handler: Callable[[dict[str, object]], tuple[bool, str | None]],
        logger: Callable[[str], None],
    ) -> None:
        self._daemon_id = daemon_id
        self._manager_ws_url = manager_ws_url
        self._status_provider = status_provider
        self._command_handler = command_handler
        self._logger = logger
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def enabled(self) -> bool:
        return bool(self._manager_ws_url and websocket is not None)

    def start(self) -> None:
        if not self.enabled:
            if self._manager_ws_url and websocket is None:
                self._logger("reverse channel disabled: websocket-client package not installed")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run_loop(self) -> None:
        assert self._manager_ws_url is not None
        assert websocket is not None
        while not self._stop_event.is_set():
            ws: Any = None
            try:
                ws = websocket.create_connection(self._manager_ws_url, timeout=10)
                self._logger(f"reverse channel connected to {self._manager_ws_url}")
                self._send(ws, {
                    "type": "register",
                    "daemonId": self._daemon_id,
                    "status": self._status_provider(),
                })

                while not self._stop_event.is_set():
                    self._send(ws, {
                        "type": "heartbeat",
                        "daemonId": self._daemon_id,
                        "status": self._status_provider(),
                    })
                    ws.settimeout(1.0)
                    try:
                        message = ws.recv()
                    except Exception:
                        message = None
                    if message:
                        if isinstance(message, bytes):
                            message = message.decode("utf-8", errors="ignore")
                        self._handle_message(ws, str(message))
                    time.sleep(4.0)
            except Exception as exc:
                self._logger(f"reverse channel reconnecting after error: {exc}")
                time.sleep(3.0)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    def _handle_message(self, ws: Any, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return

        if payload.get("type") == "command":
            command_id = payload.get("commandId")
            if command_id is not None:
                self._send(ws, {
                    "type": "command-ack",
                    "daemonId": self._daemon_id,
                    "commandId": command_id,
                })

            ok, error = self._command_handler(payload)
            if command_id is not None:
                self._send(ws, {
                    "type": "command-result",
                    "daemonId": self._daemon_id,
                    "commandId": command_id,
                    "status": "completed" if ok else "failed",
                    "error": error,
                })

    @staticmethod
    def _send(ws: Any, payload: dict[str, object]) -> None:
        ws.send(json.dumps(payload))


def build_reverse_channel_url_from_env() -> str | None:
    raw = os.getenv("MANAGER_DAEMON_WS_URL", "").strip()
    return raw or None
