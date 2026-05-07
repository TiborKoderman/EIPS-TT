"""XPath/Regex-based extraction + cleaning for PA2.

This module exists to satisfy the PA2 requirement of using **XPath + regex**
when extracting meaningful content from stored HTML pages.

Design goals:
- deterministic & explainable heuristics (good for the report)
- boilerplate removal (headers/menus/footers)
- high-quality plain text suitable for segmentation + embeddings

It intentionally returns the same `ArticleExtractionResult` dataclass as
`core.article_extractor` so the rest of the code can stay unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Iterable
from urllib.parse import urlparse

import html5lib
from elementpath import select

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from article_extractor import ArticleExtractionResult  # noqa: E402


_BLOCK_TAGS_TO_DROP: tuple[str, ...] = (
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "form",
    "button",
    "input",
    "select",
    "option",
    "textarea",
    "header",
    "footer",
    "nav",
    "aside",
)

_NOISE_KEYWORDS: tuple[str, ...] = (
    "header",
    "footer",
    "menu",
    "nav",
    "cookie",
    "share",
    "social",
    "related",
    "teaser",
    "recommend",
    "promo",
    "widget",
    "sidebar",
    "banner",
    "login",
    "register",
    "forum",
    "portali",
    "newsletter",
    "breadcrumbs",
    "comment",
)

# Candidate roots expressed as XPath queries.
# Order matters only for candidate generation; final selection is score-based.
_ARTICLE_ROOT_XPATHS: tuple[str, ...] = (
    "//main//article",
    "//article",
    "//main//*[@itemprop='articleBody']",
    "//*[@itemprop='articleBody']",
    # common CMS containers
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' article-content ')]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' entry-content ')]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' post-content ')]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' article__content ')]",
    "//main",
)

_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def extract_medover_article(url: str, html: str) -> ArticleExtractionResult:
    """Extract meaningful content using XPath + regex.

    The function is intentionally conservative: it can return `is_article=False`
    but still outputs `cleaned_content` to allow later experimentation.
    """

    if not _is_article_candidate_url(url):
        return ArticleExtractionResult(
            is_article=False,
            reason="URL does not match article scope (forum/listing/system page).",
            url=url,
            title=None,
            author=None,
            published_at=None,
            section_headings=[],
            body_paragraphs=[],
            cleaned_content="",
        )

    try:
        # html5lib returns an ElementTree-compatible element.
        doc = html5lib.parse(html or "", treebuilder="etree")
        _strip_namespaces(doc)
    except Exception:
        return ArticleExtractionResult(
            is_article=False,
            reason="HTML parsing failed (html5lib).",
            url=url,
            title=None,
            author=None,
            published_at=None,
            section_headings=[],
            body_paragraphs=[],
            cleaned_content="",
        )

    _remove_boilerplate(doc)

    title = _extract_title(doc)
    author, published_at = _extract_author_and_date(doc)

    root = _select_best_article_root(doc)
    if root is None:
        return ArticleExtractionResult(
            is_article=False,
            reason="No stable article content container found.",
            url=url,
            title=title,
            author=author,
            published_at=published_at,
            section_headings=[],
            body_paragraphs=[],
            cleaned_content="",
        )

    section_headings = _extract_section_headings(root)
    body_paragraphs = _extract_body_paragraphs(root)

    cleaned_content = _compose_cleaned_text(
        title=title,
        section_headings=section_headings,
        body_paragraphs=body_paragraphs,
    )

    if len(body_paragraphs) < 3 or len(cleaned_content) < 500:
        return ArticleExtractionResult(
            is_article=False,
            reason="Insufficient article body after cleanup.",
            url=url,
            title=title,
            author=author,
            published_at=published_at,
            section_headings=section_headings,
            body_paragraphs=body_paragraphs,
            cleaned_content=cleaned_content,
        )

    return ArticleExtractionResult(
        is_article=True,
        reason="Article extracted successfully.",
        url=url,
        title=title,
        author=author,
        published_at=published_at,
        section_headings=section_headings,
        body_paragraphs=body_paragraphs,
        cleaned_content=cleaned_content,
    )


def _is_article_candidate_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").lower().strip("/")
    if not path:
        return False
    blocked_segments = (
        "forum/",
        "forum",
        "kontakt",
        "prijava",
        "registracija",
        "kategorija",
        "iskanje",
        "tag/",
        "author/",
    )
    if any(segment in path for segment in blocked_segments):
        return False
    return True


def _extract_title(doc) -> str | None:
    h1_node = _first(_xpath_nodes(doc, "//h1[1]"))
    h1 = _clean_text(_node_text(h1_node)) if h1_node is not None else ""
    if h1:
        return h1

    og_titles = _xpath_values(doc, "//meta[@property='og:title']/@content")
    if og_titles:
        og = _clean_text(str(og_titles[0]))
        if og:
            return og

    title_node = _first(_xpath_nodes(doc, "//title[1]"))
    title = _clean_text(_node_text(title_node)) if title_node is not None else ""
    if title:
        return title.split(" - ")[0].strip() or title

    return None


def _extract_author_and_date(doc) -> tuple[str | None, str | None]:
    author = None
    published = None

    meta_author = _xpath_values(doc, "//meta[@name='author']/@content")
    if meta_author:
        author = _clean_text(str(meta_author[0])) or None

    for prop in ("article:published_time", "og:published_time"):
        meta_date = _xpath_values(doc, f"//meta[@property='{prop}']/@content")
        if meta_date:
            published = _clean_text(str(meta_date[0])) or None
            if published:
                break

    json_ld_author, json_ld_date = _extract_jsonld_author_date(doc)
    if not author and json_ld_author:
        author = json_ld_author
    if not published and json_ld_date:
        published = json_ld_date

    return author, published


def _extract_jsonld_author_date(doc) -> tuple[str | None, str | None]:
    scripts = _xpath_nodes(doc, "//script[@type='application/ld+json']")
    for node in scripts:
        raw = getattr(node, "text", None) if node is not None else None
        if not raw or not str(raw).strip():
            continue
        for parsed in _parse_jsonld_documents(str(raw)):
            node = _find_article_node(parsed)
            if node is None:
                continue
            author = _extract_author_from_node(node)
            published = _extract_date_from_node(node)
            if author or published:
                return author, published
    return None, None


def _parse_jsonld_documents(raw: str) -> list[object]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return parsed
    return [parsed]


def _find_article_node(node: object) -> dict[str, object] | None:
    if isinstance(node, dict):
        type_value = node.get("@type")
        if _is_article_type(type_value):
            return node
        graph = node.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                found = _find_article_node(item)
                if found is not None:
                    return found
    elif isinstance(node, list):
        for item in node:
            found = _find_article_node(item)
            if found is not None:
                return found
    return None


def _is_article_type(type_value: object) -> bool:
    if isinstance(type_value, str):
        return type_value.lower() in {"article", "newsarticle", "blogposting"}
    if isinstance(type_value, list):
        return any(
            isinstance(item, str) and item.lower() in {"article", "newsarticle", "blogposting"}
            for item in type_value
        )
    return False


def _extract_author_from_node(node: dict[str, object]) -> str | None:
    author = node.get("author")
    if isinstance(author, str):
        return _clean_text(author) or None
    if isinstance(author, dict):
        name = author.get("name")
        if isinstance(name, str):
            return _clean_text(name) or None
    if isinstance(author, list):
        names: list[str] = []
        for item in author:
            if isinstance(item, str):
                cleaned = _clean_text(item)
                if cleaned:
                    names.append(cleaned)
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                cleaned = _clean_text(str(item.get("name")))
                if cleaned:
                    names.append(cleaned)
        joined = ", ".join(names)
        return joined or None
    return None


def _extract_date_from_node(node: dict[str, object]) -> str | None:
    for key in ("datePublished", "dateModified"):
        value = node.get(key)
        if isinstance(value, str):
            cleaned = _clean_text(value)
            if cleaned:
                return cleaned
    return None


def _remove_boilerplate(doc) -> None:
    # 1) Drop known boilerplate tags via XPath
    xpath = " | ".join(f"//{tag}" for tag in _BLOCK_TAGS_TO_DROP)
    _remove_nodes(doc, _xpath_nodes(doc, xpath))

    # 2) Drop nodes with noisy class/id markers
    #    (We do it in Python to keep the XPath readable.)
    noisy: list[object] = []
    for node in list(doc.iter()):
        tag = str(getattr(node, "tag", "") or "").lower()
        if not tag:
            continue
        classes = getattr(node, "attrib", {}).get("class", "") if hasattr(node, "attrib") else ""
        node_id = getattr(node, "attrib", {}).get("id", "") if hasattr(node, "attrib") else ""
        marker = f"{tag} {classes} {node_id}".lower()
        if any(keyword in marker for keyword in _NOISE_KEYWORDS):
            noisy.append(node)
    _remove_nodes(doc, noisy)


def _select_best_article_root(doc):
    candidates: list[object] = []
    for xp in _ARTICLE_ROOT_XPATHS:
        try:
            candidates.extend(_xpath_nodes(doc, xp))
        except Exception:
            continue

    candidates = _dedupe_nodes(candidates)
    if not candidates:
        return None

    scored: list[tuple[int, object]] = []
    for node in candidates:
        text = _clean_text(_node_text(node))
        # Keep threshold modest so we still extract usable cleaned text for shorter pages.
        # Final article/non-article decision is made later (paragraph count + overall length).
        if len(text) < 200:
            continue
        paragraph_count = len(_xpath_nodes(node, ".//p"))
        heading_count = len(_xpath_nodes(node, ".//h2 | .//h3"))
        link_count = len(_xpath_nodes(node, ".//a"))
        score = len(text) + (paragraph_count * 120) + (heading_count * 40) - (link_count * 10)
        scored.append((score, node))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _extract_section_headings(root) -> list[str]:
    headings: list[str] = []
    for node in _xpath_nodes(root, ".//h2 | .//h3"):
        text = _clean_text(_node_text(node))
        if text and text not in headings:
            headings.append(text)
    return headings


def _extract_body_paragraphs(root) -> list[str]:
    paragraphs: list[str] = []
    for node in _xpath_nodes(root, ".//p"):
        text = _clean_text(_node_text(node))
        if not text:
            continue
        if len(text) < 40:
            continue
        if text not in paragraphs:
            paragraphs.append(text)
    return paragraphs


def _compose_cleaned_text(
    *,
    title: str | None,
    section_headings: Iterable[str],
    body_paragraphs: Iterable[str],
) -> str:
    lines: list[str] = []
    if title:
        lines.append(title.strip())

    heading_set = {heading.strip() for heading in section_headings if heading.strip()}
    for paragraph in body_paragraphs:
        text = paragraph.strip()
        if not text:
            continue
        if text in heading_set:
            continue
        lines.append(text)

    return "\n\n".join(lines).strip()


def _clean_text(value: str) -> str:
    value = (value or "").replace("\xa0", " ")
    value = _WHITESPACE_RE.sub(" ", value)
    return value.strip()


def _dedupe_nodes(nodes: Iterable[object]) -> list[object]:
    seen: set[int] = set()
    unique: list[object] = []
    for node in nodes:
        key = id(node)
        if key in seen:
            continue
        seen.add(key)
        unique.append(node)
    return unique


def _xpath_nodes(context: object, path: str) -> list[object]:
    """Evaluate XPath and return node results."""
    result: list[object] = []
    for item in select(context, path):  # type: ignore[arg-type]
        # elementpath can yield nodes, strings, numbers; we want nodes here.
        if hasattr(item, "tag"):
            result.append(item)
    return result


def _xpath_values(context: object, path: str) -> list[object]:
    """Evaluate XPath and return scalar/attribute/text results."""
    return list(select(context, path))  # type: ignore[arg-type]


def _first(items: list[object]) -> object | None:
    return items[0] if items else None


def _node_text(node: object | None) -> str:
    if node is None:
        return ""
    # ElementTree nodes: itertext() yields all descendant text.
    try:
        parts = [p for p in node.itertext()]  # type: ignore[attr-defined]
    except Exception:
        return ""
    return " ".join(parts)


def _strip_namespaces(root: object) -> None:
    """Remove HTML5lib XHTML namespace to make XPath queries simpler."""
    for el in getattr(root, "iter", lambda: [])():
        tag = getattr(el, "tag", None)
        if isinstance(tag, str) and "}" in tag:
            el.tag = tag.split("}", 1)[1]


def _remove_nodes(root: object, nodes: Iterable[object]) -> None:
    """Remove nodes from an ElementTree by building a parent map."""
    parent_map: dict[object, object] = {}
    for parent in getattr(root, "iter", lambda: [])():
        for child in list(getattr(parent, "__iter__", lambda: [])()):
            parent_map[child] = parent

    for node in nodes:
        parent = parent_map.get(node)
        if parent is None:
            continue
        try:
            parent.remove(node)  # type: ignore[attr-defined]
        except Exception:
            continue


