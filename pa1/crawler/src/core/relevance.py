"""Preferential crawling relevance score policy."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

AUTH_PATH_SIGNALS = ("/login", "/signin", "/sign-in", "/auth")
REDIRECT_QUERY_KEYS = ("returnurl=", "return_url=", "redirect=", "redirect_uri=", "next=", "continue=")


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

    path = parsed.path.lower()
    query = (parsed.query or "").lower()
    haystack = path
    for keyword in policy.keywords:
        key = keyword.strip().lower()
        if key and key in haystack:
            score += policy.keyword_boost

    has_auth_path = any(token in path for token in AUTH_PATH_SIGNALS)
    has_redirect_query = any(token in query for token in REDIRECT_QUERY_KEYS)
    if has_auth_path:
        score -= 15.0
    if has_redirect_query:
        score -= 20.0
    if has_auth_path and has_redirect_query:
        score -= 15.0

    score -= policy.depth_penalty * max(depth, 0)
    return score
