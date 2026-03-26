"""HTML parsing helpers for frontier discovery.
extracting:
  - links from <a href="..."> attributes
  - JS navigation targets from inline JS snippets (onclick, location.href etc.)
  - image URLs from <img src="..."> attributes

All extracted URLs are normalized to absolute form relative to the page URL.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from utils.url_canonicalizer import DefaultUrlCanonicalizer, UrlCanonicalizer


_JS_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # location.href = '...'
    re.compile(
        r"(?i)(?:window\.)?(?:document\.)?location\.href\s*=\s*(['\"])(?P<url>.+?)\1"
    ),
    # location = '...'
    re.compile(
        r"(?i)(?:window\.)?(?:document\.)?location\s*=\s*(['\"])(?P<url>.+?)\1"
    ),
    # location.assign('...') / location.replace('...')
    re.compile(
        r"(?i)(?:window\.)?(?:document\.)?location\.(?:assign|replace)\(\s*(['\"])(?P<url>.+?)\1\s*\)"
    ),
    # window.open('...', ...)
    re.compile(
        r"(?i)window\.open\(\s*(['\"])(?P<url>.+?)\1"
    ),
)


def _is_probably_url(value: str) -> bool:
    if not value:
        return False
    value = value.strip()
    if value.startswith("#"):
        return False
    if value.lower().startswith(("javascript:", "mailto:", "tel:")):
        return False
    return True


def _unique_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


@dataclass(frozen=True)
class ParseResult:
    """Outgoing URL candidates discovered on a page."""

    links: list[str]
    images: list[str]


def parse_outgoing_urls(
    html: str,
    *,
    page_url: str,
    canonicalizer: UrlCanonicalizer | None = None,
) -> ParseResult:
    """Parse outgoing links+images from HTML.

    Args:
        html: HTML source
        page_url: final URL of the page
        canonicalizer: Optional canonicalization strategy. When provided, the
            returned URLs will be canonical absolute URLs.

    Returns:
        ParseResult(links=[...], images=[...])
    """
    canonicalizer = canonicalizer or DefaultUrlCanonicalizer()
    soup = BeautifulSoup(html or "", "html.parser")

    raw_links: list[str] = []
    raw_images: list[str] = []

    for a in soup.find_all("a"):
        href = a.get("href")
        if href and _is_probably_url(href):
            raw_links.append(href.strip())

        onclick = a.get("onclick")
        if onclick:
            raw_links.extend(_extract_js_urls(onclick))

    # JS navigation can also appear on other elements (button/div)
    for el in soup.find_all(onclick=True):
        onclick = el.get("onclick")
        if onclick:
            raw_links.extend(_extract_js_urls(onclick))

    for img in soup.find_all("img"):
        src = img.get("src")
        if src and _is_probably_url(src):
            raw_images.append(src.strip())

    # normalize to absolute + canonical
    abs_links: list[str] = []
    for raw in raw_links:
        try:
            absolute = urljoin(page_url, raw)
            if not _is_probably_url(absolute):
                continue
            abs_links.append(canonicalizer.canonicalize(absolute))
        except Exception:
            # ignore bad URLs (invalid schemes, parsing errors, etc.)
            continue

    abs_images: list[str] = []
    for raw in raw_images:
        try:
            absolute = urljoin(page_url, raw)
            if not _is_probably_url(absolute):
                continue
            abs_images.append(canonicalizer.canonicalize(absolute))
        except Exception:
            continue

    return ParseResult(
        links=_unique_preserve_order(abs_links),
        images=_unique_preserve_order(abs_images),
    )


def _extract_js_urls(js_snippet: str) -> list[str]:
    """Extract URL-like strings from common inline JS navigation patterns."""
    if not js_snippet:
        return []
    found: list[str] = []
    for pattern in _JS_URL_PATTERNS:
        for match in pattern.finditer(js_snippet):
            url = match.group("url").strip()
            if _is_probably_url(url):
                found.append(url)
    return found
