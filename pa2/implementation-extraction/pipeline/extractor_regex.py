"""Regex-only metadata extractor (PA2 Section 2.2 alternative path).

Purpose: satisfies the assignment's "use XPath AND regex" requirement by
providing a co-equal regex-based extractor that pulls article-level metadata
(title, author, published_at, body chars) directly from raw HTML without any
parser. Kept as an alternate technique to compare against the primary
XPath/HTML5 path in `extractor_xpath.py`.

The output dataclass intentionally mirrors a subset of `ArticleExtractionResult`
so a side-by-side comparison is trivial in the report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|svg|iframe)\b[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_BOILERPLATE_BLOCK_RE = re.compile(
    r"<(header|footer|nav|aside|form)\b[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")

_TITLE_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TITLE_OG_RE = re.compile(
    r'<meta\s+[^>]*property=[\'"]og:title[\'"][^>]*content=[\'"]([^\'"]+)[\'"]',
    re.IGNORECASE,
)
_TITLE_DOC_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

_DATE_META_RE = re.compile(
    r'<meta\s+[^>]*property=[\'"](?:article:published_time|og:published_time)[\'"][^>]*content=[\'"]([^\'"]+)[\'"]',
    re.IGNORECASE,
)
_DATE_ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})(?:T\d{2}:\d{2}:\d{2}[Zz+\-:0-9]*)?\b")

_AUTHOR_META_RE = re.compile(
    r'<meta\s+[^>]*name=[\'"]author[\'"][^>]*content=[\'"]([^\'"]+)[\'"]',
    re.IGNORECASE,
)

_ARTICLE_BLOCK_RE = re.compile(r"<article[^>]*>(.*?)</article>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class RegexExtractionResult:
    url: str
    title: str | None
    author: str | None
    published_at: str | None
    body_chars: int
    cleaned_content: str


def extract_with_regex(url: str, html: str) -> RegexExtractionResult:
    raw = html or ""

    title = _first_match(_TITLE_H1_RE, raw) or _first_match(_TITLE_OG_RE, raw) or _first_match(_TITLE_DOC_RE, raw)
    if title:
        title = _strip(title)

    author_match = _AUTHOR_META_RE.search(raw)
    author = _strip(author_match.group(1)) if author_match else None

    date_match = _DATE_META_RE.search(raw) or _DATE_ISO_RE.search(raw)
    published_at = _strip(date_match.group(1)) if date_match else None

    article_block_match = _ARTICLE_BLOCK_RE.search(raw)
    body_html = article_block_match.group(1) if article_block_match else raw

    cleaned = _strip_tags(body_html)
    return RegexExtractionResult(
        url=url,
        title=title,
        author=author,
        published_at=published_at,
        body_chars=len(cleaned),
        cleaned_content=cleaned,
    )


def _first_match(pattern: re.Pattern[str], raw: str) -> str | None:
    m = pattern.search(raw)
    if not m:
        return None
    return m.group(1)


def _strip_tags(html: str) -> str:
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _BOILERPLATE_BLOCK_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("\xa0", " ")
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _strip(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", _TAG_RE.sub(" ", value or "")).strip()


if __name__ == "__main__":
    import argparse
    import json
    import sys
    import urllib.request

    parser = argparse.ArgumentParser(description="Regex-only PA2 metadata extractor")
    parser.add_argument("--url", required=True)
    parser.add_argument("--user-agent", default="fri-wier-EIPS-TT")
    parser.add_argument("--print-cleaned", action="store_true")
    args = parser.parse_args()

    req = urllib.request.Request(args.url, headers={"User-Agent": args.user_agent})
    with urllib.request.urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    result = extract_with_regex(args.url, html)
    out: dict[str, object] = {
        "url": result.url,
        "title": result.title,
        "author": result.author,
        "published_at": result.published_at,
        "body_chars": result.body_chars,
    }
    if args.print_cleaned:
        out["cleaned_content"] = result.cleaned_content
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
