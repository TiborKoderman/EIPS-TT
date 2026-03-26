"""Robots.txt handling for crawler workers."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests


@dataclass(frozen=True)
class RobotsPolicy:
    """Parsed robots policy for one site origin."""

    origin: str
    robots_url: str
    crawl_delay_seconds: float | None
    sitemaps: list[str]
    raw_content: str | None
    fetched: bool
    parser: RobotFileParser

    def allows(self, user_agent: str, url: str) -> bool:
        """Return True when user-agent may fetch URL according to robots.txt."""
        # If robots.txt could not be fetched, treat policy as unavailable and allow crawl.
        if not self.fetched:
            return True
        return self.parser.can_fetch(user_agent, url)


class RobotsPolicyManager:
    """Fetch and cache robots policies per origin."""

    def __init__(self, user_agent: str, timeout_seconds: float = 15.0) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._cache: Dict[str, RobotsPolicy] = {}

    def get_policy(self, url: str) -> RobotsPolicy:
        """Get cached policy for URL origin, fetching robots.txt once if needed."""
        origin = self._origin(url)
        with self._lock:
            cached = self._cache.get(origin)
        if cached is not None:
            return cached

        policy = self._fetch_policy(origin)
        with self._lock:
            existing = self._cache.get(origin)
            if existing is not None:
                return existing
            self._cache[origin] = policy
        return policy

    def _fetch_policy(self, origin: str) -> RobotsPolicy:
        robots_url = f"{origin}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        headers = {"User-Agent": self.user_agent}

        try:
            response = requests.get(
                robots_url,
                headers=headers,
                timeout=self.timeout_seconds,
                allow_redirects=True,
            )
        except requests.RequestException:
            return RobotsPolicy(
                origin=origin,
                robots_url=robots_url,
                crawl_delay_seconds=None,
                sitemaps=[],
                raw_content=None,
                fetched=False,
                parser=parser,
            )

        if response.status_code >= 400:
            return RobotsPolicy(
                origin=origin,
                robots_url=robots_url,
                crawl_delay_seconds=None,
                sitemaps=[],
                raw_content=response.text,
                fetched=False,
                parser=parser,
            )

        lines = response.text.splitlines()
        parser.parse(lines)
        crawl_delay = parser.crawl_delay(self.user_agent)
        if crawl_delay is None:
            crawl_delay = parser.crawl_delay("*")
        sitemap_urls = parser.site_maps() or []

        return RobotsPolicy(
            origin=origin,
            robots_url=robots_url,
            crawl_delay_seconds=float(crawl_delay) if crawl_delay is not None else None,
            sitemaps=sitemap_urls,
            raw_content=response.text,
            fetched=True,
            parser=parser,
        )

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid absolute URL for robots policy: {url}")
        return f"{parsed.scheme}://{parsed.netloc}"

