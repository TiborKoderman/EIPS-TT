"""In-memory preferential frontier with optional persistent swap backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import heapq
from itertools import count
from typing import Protocol

from core.preferential import PreferentialScorer
from utils.url_canonicalizer import DefaultUrlCanonicalizer, UrlCanonicalizer


@dataclass(frozen=True)
class FrontierEntry:
    """One queued URL candidate with crawl metadata."""

    url: str
    priority: int
    source_url: str | None
    depth: int
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class FrontierSwapStore(Protocol):
    """Persistent overflow store used when in-memory frontier reaches capacity."""

    def enqueue(self, entry: FrontierEntry) -> None:
        ...

    def dequeue_batch(self, limit: int = 1000) -> list[FrontierEntry]:
        ...

    def count(self) -> int:
        ...


class FrontierRules:
    """Simple URL acceptance policy rules."""

    def __init__(self, *, allowed_hosts: list[str] | None = None) -> None:
        self._allowed_hosts = [host.lower() for host in (allowed_hosts or [])]

    def allow(self, url: str) -> bool:
        if not self._allowed_hosts:
            return True
        from urllib.parse import urlsplit

        host = (urlsplit(url).hostname or "").lower()
        return any(host == allowed or host.endswith("." + allowed) for allowed in self._allowed_hosts)


class UrlFrontier:
    """Priority frontier that always pops highest score URL first."""

    def __init__(
        self,
        *,
        scorer: PreferentialScorer | None = None,
        canonicalizer: UrlCanonicalizer | None = None,
        rules: FrontierRules | None = None,
        swap_store: FrontierSwapStore | None = None,
        max_in_memory: int = 50_000,
    ) -> None:
        self._scorer = scorer or PreferentialScorer()
        self._canonicalizer = canonicalizer or DefaultUrlCanonicalizer()
        self._rules = rules or FrontierRules()
        self._swap_store = swap_store
        self._max_in_memory = max(1_000, max_in_memory)

        self._heap: list[tuple[int, int, FrontierEntry]] = []
        self._serial = count()
        self._known_urls: set[str] = set()
        self._spilled = 0

    def add(
        self,
        raw_url: str,
        *,
        base_url: str | None = None,
        source_url: str | None = None,
        anchor_text: str | None = None,
        depth: int = 0,
    ) -> bool:
        try:
            canonical = self._canonicalizer.canonicalize(raw_url, base_url=base_url)
        except ValueError:
            return False

        if canonical in self._known_urls:
            return False
        if not self._rules.allow(canonical):
            return False

        breakdown = self._scorer.score(canonical, anchor_text=anchor_text)
        entry = FrontierEntry(
            url=canonical,
            priority=breakdown.score,
            source_url=source_url,
            depth=depth,
        )
        self._known_urls.add(canonical)

        if len(self._heap) >= self._max_in_memory and self._swap_store is not None:
            self._swap_store.enqueue(entry)
            self._spilled += 1
            return True

        heapq.heappush(self._heap, (-entry.priority, next(self._serial), entry))
        return True

    def pop_next(self) -> FrontierEntry | None:
        if not self._heap and self._swap_store is not None:
            for entry in self._swap_store.dequeue_batch(limit=min(5000, self._max_in_memory)):
                heapq.heappush(self._heap, (-entry.priority, next(self._serial), entry))

        if not self._heap:
            return None

        _, _, entry = heapq.heappop(self._heap)
        return entry

    def stats(self) -> dict[str, int]:
        swap_count = self._swap_store.count() if self._swap_store is not None else 0
        return {
            "inMemory": len(self._heap),
            "swap": swap_count,
            "known": len(self._known_urls),
            "spilled": self._spilled,
        }
