"""Preferential scoring strategies for frontier ordering."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


LOW_PRIORITY_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx"}


@dataclass(frozen=True)
class ScoreBreakdown:
    """Detailed score result for one URL candidate."""

    score: int
    level: str
    reason: str


class PreferentialScorer:
    """Score links by relevance to crawl topic and scope."""

    def __init__(
        self,
        *,
        topic_keywords: list[str] | None = None,
        preferred_hosts: list[str] | None = None,
    ) -> None:
        self._topic_keywords = [kw.lower() for kw in (topic_keywords or [
            "medicine",
            "health",
            "medover",
            "symptom",
            "clinic",
            "hospital",
            "treatment",
            "disease",
            "doctor",
        ])]
        self._preferred_hosts = [host.lower() for host in (preferred_hosts or [])]

    def score(self, url: str, *, anchor_text: str | None = None) -> ScoreBreakdown:
        value = 50
        reasons: list[str] = ["base=50"]
        lowered_url = url.lower()
        lowered_anchor = (anchor_text or "").lower()

        if any(keyword in lowered_url or keyword in lowered_anchor for keyword in self._topic_keywords):
            value += 40
            reasons.append("topic+40")

        host = urlsplit(url).hostname or ""
        host_lower = host.lower()
        if any(preferred in host_lower for preferred in self._preferred_hosts):
            value += 20
            reasons.append("preferred-host+20")

        path = urlsplit(url).path.lower()
        if any(path.endswith(ext) for ext in LOW_PRIORITY_EXTENSIONS):
            value -= 35
            reasons.append("binary-ext-35")

        if "/news" in lowered_url:
            value -= 15
            reasons.append("news-15")

        if value >= 80:
            level = "high"
        elif value >= 40:
            level = "medium"
        else:
            level = "low"

        return ScoreBreakdown(score=value, level=level, reason=", ".join(reasons))
