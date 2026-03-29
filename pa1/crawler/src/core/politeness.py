"""Thread-safe politeness controls (per-IP crawl pacing)."""

from __future__ import annotations

import socket
import threading
import time
from urllib.parse import urlparse


class PerIpRateLimiter:
    """Ensure at least N seconds between requests to the same IP."""

    def __init__(self, min_interval_seconds: float = 5.0) -> None:
        self.min_interval_seconds = float(min_interval_seconds)
        self._registry_lock = threading.Lock()
        self._ip_locks: dict[str, threading.Lock] = {}
        self._last_request_monotonic: dict[str, float] = {}

    def wait_for_turn(self, url: str, robots_crawl_delay_seconds: float | None = None) -> None:
        """Block until URL can be requested under per-IP delay policy."""
        host = self._host_from_url(url)
        ip_key = self._resolve_ip_key(host)
        lock = self._lock_for_ip(ip_key)
        effective_delay = max(
            self.min_interval_seconds,
            float(robots_crawl_delay_seconds) if robots_crawl_delay_seconds is not None else 0.0,
        )

        with lock:
            now = time.monotonic()
            last = self._last_request_monotonic.get(ip_key)
            if last is not None:
                remaining = effective_delay - (now - last)
                if remaining > 0:
                    time.sleep(remaining)
                    now = time.monotonic()
            self._last_request_monotonic[ip_key] = now

    def _lock_for_ip(self, ip_key: str) -> threading.Lock:
        with self._registry_lock:
            if ip_key not in self._ip_locks:
                self._ip_locks[ip_key] = threading.Lock()
            return self._ip_locks[ip_key]

    @staticmethod
    def _host_from_url(url: str) -> str:
        parsed = urlparse(url)
        if not parsed.hostname:
            raise ValueError(f"URL missing hostname: {url}")
        return parsed.hostname

    @staticmethod
    def _resolve_ip_key(hostname: str) -> str:
        try:
            infos = socket.getaddrinfo(hostname, None)
            if infos:
                return infos[0][4][0]
        except socket.gaierror:
            pass
        return hostname

