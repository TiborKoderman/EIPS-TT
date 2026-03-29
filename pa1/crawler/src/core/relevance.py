"""Preferential crawling relevance score policy."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RelevancePolicy:
    """Policy knobs for scoring candidate frontier URLs."""

    allowed_domain_suffixes: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    same_host_boost: float = 10.0
    allowed_suffix_boost: float = 20.0
    keyword_boost: float = 5.0
    depth_penalty: float = 0.2


def score_url(
    url: str,
    *,
    parent_url: str | None = None,
    depth: int = 0,
    policy: RelevancePolicy | None = None,
) -> float:
    """Score URL relevance using host affinity, domain suffix and keyword boosts."""

    policy = policy or RelevancePolicy()

    try:
        parsed = urlsplit(url)
    except Exception:
        return 0.0

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return 0.0

    host = parsed.hostname.lower()
    score = 0.0

    if parent_url:
        try:
            parent_host = urlsplit(parent_url).hostname
            if parent_host and parent_host.lower() == host:
                score += policy.same_host_boost
        except Exception:
            pass

    for suffix in policy.allowed_domain_suffixes:
        normalized = suffix.lower().lstrip(".")
        if host == normalized or host.endswith("." + normalized):
            score += policy.allowed_suffix_boost
            break

    haystack = (parsed.path + "?" + (parsed.query or "")).lower()
    for keyword in policy.keywords:
        key = keyword.strip().lower()
        if key and key in haystack:
            score += policy.keyword_boost

    score -= policy.depth_penalty * max(depth, 0)
    return score
