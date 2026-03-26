"""Link and media extraction utilities for crawled HTML pages."""

from __future__ import annotations

from dataclasses import dataclass
import re

from utils.url_canonicalizer import DefaultUrlCanonicalizer, UrlCanonicalizer


_HREF_RE = re.compile(r"<a\b[^>]*\bhref\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
_IMG_RE = re.compile(r"<img\b[^>]*\bsrc\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
_ONCLICK_URL_RE = re.compile(
    r"(?:location\.href|document\.location(?:\.href)?)\s*=\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedPageAssets:
    """Normalized links and image sources extracted from one page."""

    links: list[str]
    js_links: list[str]
    images: list[str]


class LinkExtractor:
    """Extract and canonicalize href/js/image links from HTML."""

    def __init__(self, canonicalizer: UrlCanonicalizer | None = None) -> None:
        self._canonicalizer = canonicalizer or DefaultUrlCanonicalizer()

    def extract(self, html: str, page_url: str) -> ExtractedPageAssets:
        href_links = self._normalize_matches(_HREF_RE.findall(html), page_url)
        js_links = self._normalize_matches(_ONCLICK_URL_RE.findall(html), page_url)
        image_links = self._normalize_matches(_IMG_RE.findall(html), page_url)
        return ExtractedPageAssets(
            links=href_links,
            js_links=js_links,
            images=image_links,
        )

    def _normalize_matches(self, values: list[str], page_url: str) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            candidate = raw.strip()
            if not candidate:
                continue
            if candidate.startswith("javascript:") or candidate.startswith("mailto:"):
                continue
            try:
                url = self._canonicalizer.canonicalize(candidate, base_url=page_url)
            except ValueError:
                continue
            if url in seen:
                continue
            seen.add(url)
            normalized.append(url)
        return normalized
