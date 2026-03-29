"""Link and media extraction utilities for crawled HTML pages."""

from __future__ import annotations

from dataclasses import dataclass
from bs4 import BeautifulSoup

from core.parser import _extract_js_urls, parse_outgoing_urls
from utils.url_canonicalizer import DefaultUrlCanonicalizer, UrlCanonicalizer


@dataclass(frozen=True)
class ExtractedPageAssets:
    """Normalized page assets extracted from one page.

    `links` contains all crawl candidates discovered on the page.
    `js_links` is the subset of those links that came from inline JS navigation.
    """

    links: list[str]
    js_links: list[str]
    images: list[str]


class LinkExtractor:
    """Compatibility wrapper around the shared HTML parser."""

    def __init__(self, canonicalizer: UrlCanonicalizer | None = None) -> None:
        self._canonicalizer = canonicalizer or DefaultUrlCanonicalizer()

    def extract(self, html: str, page_url: str) -> ExtractedPageAssets:
        parsed = parse_outgoing_urls(
            html,
            page_url=page_url,
            canonicalizer=self._canonicalizer,
        )
        js_links = self._extract_js_links(html, page_url)
        return ExtractedPageAssets(
            links=parsed.links,
            js_links=js_links,
            images=parsed.images,
        )

    def _extract_js_links(self, html: str, page_url: str) -> list[str]:
        soup = BeautifulSoup(html or "", "html.parser")
        normalized: list[str] = []
        seen: set[str] = set()

        for element in soup.find_all(onclick=True):
            onclick = element.get("onclick")
            if not onclick:
                continue

            for raw_url in _extract_js_urls(onclick):
                try:
                    normalized_url = self._canonicalizer.canonicalize(raw_url, base_url=page_url)
                except ValueError:
                    continue

                if normalized_url in seen:
                    continue
                seen.add(normalized_url)
                normalized.append(normalized_url)

        return normalized
