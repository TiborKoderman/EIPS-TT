"""Preferential scoring strategies for frontier ordering."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


LOW_PRIORITY_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx"}

# MedOverNet article URL patterns — highest priority
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

# MedOverNet forum URL patterns — second-highest priority
_FORUM_PATH_SIGNALS = (
    "/forum/",
    "/tema/",
    "/vprasanje/",
)

# Noise paths to deprioritise (not block — some may link to articles)
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
    "/prijava",
    "/registracija",
    "/account",
    "/admin",
    "/feed",
    "/rss",
    "/sitemap",
)


@dataclass(frozen=True)
class ScoreBreakdown:
    """Detailed score result for one URL candidate."""

    score: int
    level: str
    reason: str


class PreferentialScorer:
    """Score links by relevance to crawl topic and scope.

    Scoring tiers (cumulative):
      base=50, article-path+50, forum-path+30, topic-keyword+20,
      preferred-host+20, noise-path-20, binary-ext-35
    """

    def __init__(
        self,
        *,
        topic_keywords: list[str] | None = None,
        preferred_hosts: list[str] | None = None,
    ) -> None:
        self._topic_keywords = [kw.lower() for kw in (topic_keywords or [
            # fitness / wellness (primary domain focus)
            "fitness", "fitnes",
            "exercise", "telovadba",
            "training", "trening",
            "workout", "wellness",
            "nutrition", "prehrana",
            "vadba", "kondicija",
            "rekreacija", "šport", "sport",
            "shujsati", "hujsanje", "kalorij", "beljakovine",
            # secondary: health topics that intersect fitness
            "dieta", "zdravje", "vitamin",
        ])]
        self._preferred_hosts = [host.lower() for host in (preferred_hosts or [])]

    def score(self, url: str, *, anchor_text: str | None = None) -> ScoreBreakdown:
        value = 50
        reasons: list[str] = ["base=50"]
        lowered_url = url.lower()
        lowered_anchor = (anchor_text or "").lower()

        try:
            parsed = urlsplit(url)
        except Exception:
            return ScoreBreakdown(score=0, level="low", reason="unparseable")

        path = (parsed.path or "").lower()
        host_lower = (parsed.hostname or "").lower()

        # Article path signals — best content for PA2 corpus
        if any(sig in path for sig in _ARTICLE_PATH_SIGNALS):
            value += 50
            reasons.append("article-path+50")
        # Forum path signals — valuable for PA2 forum extraction
        elif any(sig in path for sig in _FORUM_PATH_SIGNALS):
            value += 30
            reasons.append("forum-path+30")

        # Topic keyword match in URL or anchor text
        if any(kw in lowered_url or kw in lowered_anchor for kw in self._topic_keywords):
            value += 20
            reasons.append("topic-keyword+20")

        # Preferred host (e.g. medover.net / zurnal24.si)
        if any(preferred in host_lower for preferred in self._preferred_hosts):
            value += 20
            reasons.append("preferred-host+20")

        # Noise paths
        if any(sig in path for sig in _LOW_PRIORITY_PATH_SIGNALS):
            value -= 20
            reasons.append("noise-path-20")

        # Binary / document extensions
        if any(path.endswith(ext) for ext in LOW_PRIORITY_EXTENSIONS):
            value -= 35
            reasons.append("binary-ext-35")

        if value >= 100:
            level = "high"
        elif value >= 60:
            level = "medium"
        else:
            level = "low"

        return ScoreBreakdown(score=value, level=level, reason=", ".join(reasons))
