"""Preferential crawling relevance score policy."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

AUTH_PATH_SIGNALS = ("/login", "/signin", "/sign-in", "/auth")
REDIRECT_QUERY_KEYS = ("returnurl=", "return_url=", "redirect=", "redirect_uri=", "next=", "continue=")

# MedOverNet article path signals — highest article-yield paths
_ARTICLE_PATH_SIGNALS = (
    "/novica/",
    "/clanek/",
    "/artikel/",
    "/prispevek/",
    "/blog/",
    "/zdravje/",
    "/bolezni/",
    "/prehrana/",
    "/telovadba/",
    "/vadba/",
    "/fitnes/",
)

# Forum path signals — forum posts are second-priority content
_FORUM_PATH_SIGNALS = (
    "/forum/",
    "/tema/",
    "/vprasanje/",
)

# Deprioritise structural/noise paths (not blocked — they may link to content)
_LOW_PRIORITY_PATH_SIGNALS = (
    "/iskanje",
    "/search",
    "/kategorija/",
    "/category/",
    "/tag/",
    "/stran/",
    "/page/",
    "/author/",
    "/avtor/",
    "/kontakt",
    "/feed",
    "/rss",
    "/sitemap",
)


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
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    score = 0.0

    # Same-host affinity
    if parent_url:
        try:
            parent_host = urlsplit(parent_url).hostname
            if parent_host and parent_host.lower() == host:
                score += policy.same_host_boost
        except Exception:
            pass

    # Allowed domain suffix boost
    for suffix in policy.allowed_domain_suffixes:
        normalized = suffix.lower().lstrip(".")
        if host == normalized or host.endswith("." + normalized):
            score += policy.allowed_suffix_boost
            break

    # Article path signals — best content source
    if any(sig in path for sig in _ARTICLE_PATH_SIGNALS):
        score += 30.0

    # Forum path signals — valuable secondary content
    elif any(sig in path for sig in _FORUM_PATH_SIGNALS):
        score += 15.0

    # Topic keyword match in path
    for keyword in policy.keywords:
        key = keyword.strip().lower()
        if key and key in path:
            score += policy.keyword_boost

    # Noise path penalty
    if any(sig in path for sig in _LOW_PRIORITY_PATH_SIGNALS):
        score -= 10.0

    # Auth/redirect penalties
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
