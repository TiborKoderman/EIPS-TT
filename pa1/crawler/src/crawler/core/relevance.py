"""Preferential crawling: compute link relevance scores.

The score should be:
- higher for URLs on the same host (or under a configured allowed domain suffix)
- higher for paths that contain domain-specific keywords
- slightly higher for shallow depth (BFS-ish)

Scheduler/frontier uses this score as `frontier_priority`.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RelevancePolicy:
    """Policy for scoring relevance."""

    # If provided, URLs matching any suffix get a big boost.
    allowed_domain_suffixes: tuple[str, ...] = ()

    # Keywords that indicate relevance in path/query.
    keywords: tuple[str, ...] = ()

    # Weight configuration.
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
        s = suffix.lower().lstrip(".")
        if host == s or host.endswith("." + s):
            score += policy.allowed_suffix_boost
            break

    haystack = (parsed.path + "?" + (parsed.query or "")).lower()
    for kw in policy.keywords:
        if kw and kw.lower() in haystack:
            score += policy.keyword_boost

    score -= policy.depth_penalty * max(depth, 0)
    return score

