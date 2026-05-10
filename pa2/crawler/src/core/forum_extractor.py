"""XPath + regex forum thread extraction for Med.Over.Net."""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse

import html5lib

from article_extractor_xpath import (  # noqa: E402
    _clean_text,
    _first,
    _node_text,
    _remove_nodes,
    _strip_namespaces,
    _xpath_nodes,
    _xpath_values,
)


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

_THREAD_TITLE_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_THREAD_OG_TITLE_RE = re.compile(
    r'<meta\s+[^>]*property=[\'"]og:title[\'"][^>]*content=[\'"]([^\'"]+)[\'"]',
    re.IGNORECASE,
)
_THREAD_TITLE_SUFFIX_RE = re.compile(r"\s+-\s+Med\.Over\.Net(?:\s+-\s+.+)?$", re.IGNORECASE)
_POST_BLOCK_RE = re.compile(
    r'<div[^>]+class="[^"]*\bforum-post\b[^"]*"[^>]*>(?P<body>.*?)</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
_AUTHOR_RE = re.compile(
    r'title--author[^>]*>\s*(?:<a[^>]*>)?(?P<author>.*?)(?:</a>)?\s*</',
    re.IGNORECASE | re.DOTALL,
)
_DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\s+ob\s+\d{2}:\d{2}\b")
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ForumPost:
    author: str | None
    published_at: str | None
    body_paragraphs: list[str]
    signature: str | None


@dataclass(frozen=True)
class ForumThreadExtractionResult:
    is_thread: bool
    reason: str
    url: str
    title: str | None
    author: str | None
    published_at: str | None
    section_headings: list[str]
    body_paragraphs: list[str]
    cleaned_content: str
    posts: list[ForumPost]


def extract_forum_thread(url: str, html: str) -> ForumThreadExtractionResult:
    if not _is_forum_thread_url(url):
        return ForumThreadExtractionResult(
            is_thread=False,
            reason="URL does not match forum thread scope.",
            url=url,
            title=None,
            author=None,
            published_at=None,
            section_headings=[],
            body_paragraphs=[],
            cleaned_content="",
            posts=[],
        )

    try:
        doc = html5lib.parse(html or "", treebuilder="etree")
        _strip_namespaces(doc)
    except Exception:
        return ForumThreadExtractionResult(
            is_thread=False,
            reason="HTML parsing failed (html5lib).",
            url=url,
            title=None,
            author=None,
            published_at=None,
            section_headings=[],
            body_paragraphs=[],
            cleaned_content="",
            posts=[],
        )

    _remove_boilerplate(doc)

    title = _extract_thread_title(doc, html)
    posts = _extract_posts(doc)
    if not posts:
        posts = _extract_posts_with_regex(html)

    posts = [post for post in posts if post.body_paragraphs]
    section_headings = [_build_post_heading(index, post) for index, post in enumerate(posts)]
    body_paragraphs = [paragraph for post in posts for paragraph in post.body_paragraphs]
    cleaned_content = _compose_cleaned_text(title, posts)

    if not posts or len(cleaned_content) < 120:
        return ForumThreadExtractionResult(
            is_thread=False,
            reason="No stable forum thread content found.",
            url=url,
            title=title,
            author=posts[0].author if posts else None,
            published_at=posts[0].published_at if posts else None,
            section_headings=[heading for heading in section_headings if heading],
            body_paragraphs=body_paragraphs,
            cleaned_content=cleaned_content,
            posts=posts,
        )

    opening_post = posts[0]
    return ForumThreadExtractionResult(
        is_thread=True,
        reason="Forum thread extracted successfully.",
        url=url,
        title=title,
        author=opening_post.author,
        published_at=opening_post.published_at,
        section_headings=[heading for heading in section_headings if heading],
        body_paragraphs=body_paragraphs,
        cleaned_content=cleaned_content,
        posts=posts,
    )


def _is_forum_thread_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    return "/forum/tema/" in path


def _remove_boilerplate(doc: object) -> None:
    nodes_to_remove: list[object] = []
    for tag in _BLOCK_TAGS_TO_DROP:
        nodes_to_remove.extend(_xpath_nodes(doc, f"//{tag}"))

    nodes_to_remove.extend(_xpath_nodes(
        doc,
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' forum-post__bottom ')]",
    ))
    nodes_to_remove.extend(_xpath_nodes(
        doc,
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' modal__content ')]",
    ))
    nodes_to_remove.extend(_xpath_nodes(
        doc,
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' modal-content ')]",
    ))
    _remove_nodes(doc, nodes_to_remove)


def _extract_thread_title(doc: object, raw_html: str) -> str | None:
    title_node = _first(_xpath_nodes(doc, "//h1[1]"))
    title = _clean_text(_node_text(title_node))
    if title:
        return _THREAD_TITLE_SUFFIX_RE.sub("", title).strip()

    og_titles = _xpath_values(doc, "//meta[@property='og:title']/@content")
    if og_titles:
        og_title = _clean_text(str(og_titles[0]))
        if og_title:
            return _THREAD_TITLE_SUFFIX_RE.sub("", og_title).strip()

    for pattern in (_THREAD_OG_TITLE_RE, _THREAD_TITLE_RE):
        match = pattern.search(raw_html or "")
        if match:
            value = _clean_regex_text(match.group(1))
            if value:
                return _THREAD_TITLE_SUFFIX_RE.sub("", value).strip()

    return None


def _extract_posts(doc: object) -> list[ForumPost]:
    post_nodes = _xpath_nodes(
        doc,
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' forum-post ')]",
    )

    posts: list[ForumPost] = []
    for node in post_nodes:
        author = _extract_author(node)
        published_at = _extract_post_date(node)
        paragraphs = _extract_post_paragraphs(node)
        signature = _extract_signature(node)
        if paragraphs:
            posts.append(ForumPost(
                author=author,
                published_at=published_at,
                body_paragraphs=paragraphs,
                signature=signature,
            ))

    return posts


def _extract_posts_with_regex(raw_html: str) -> list[ForumPost]:
    posts: list[ForumPost] = []
    for match in _POST_BLOCK_RE.finditer(raw_html or ""):
        block = match.group("body")
        author_match = _AUTHOR_RE.search(block)
        author = _clean_regex_text(author_match.group("author")) if author_match else None

        date_match = _DATE_RE.search(block)
        published_at = _clean_regex_text(date_match.group(0)) if date_match else None

        content_match = re.search(
            r'forum-post__content[^>]*>(?P<content>.*?)</div>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        content_html = content_match.group("content") if content_match else block
        paragraphs = _extract_regex_paragraphs(content_html)
        if not paragraphs:
            continue

        signature_match = re.search(
            r'forum-post__signature[^>]*>(?P<signature>.*?)</div>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        signature = _clean_regex_text(signature_match.group("signature")) if signature_match else None

        posts.append(ForumPost(
            author=author,
            published_at=published_at,
            body_paragraphs=paragraphs,
            signature=signature,
        ))

    return posts


def _extract_author(node: object) -> str | None:
    values = _xpath_values(
        node,
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' title--author ')]//text()",
    )
    text = _clean_text(" ".join(str(value) for value in values))
    return text or None


def _extract_post_date(node: object) -> str | None:
    values = _xpath_values(
        node,
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' forum-post__name ')]//text()",
    )
    joined = " ".join(str(value) for value in values)
    match = _DATE_RE.search(joined)
    if not match:
        return None
    return _clean_text(match.group(0)) or None


def _extract_post_paragraphs(node: object) -> list[str]:
    content_root = _first(_xpath_nodes(
        node,
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' forum-post__content ')]",
    ))
    if content_root is None:
        return []

    paragraphs = [
        _clean_text(_node_text(paragraph))
        for paragraph in _xpath_nodes(content_root, ".//p")
    ]
    cleaned = [paragraph for paragraph in paragraphs if paragraph]
    if cleaned:
        return cleaned

    fallback = _clean_text(_node_text(content_root))
    return [fallback] if fallback else []


def _extract_signature(node: object) -> str | None:
    values = _xpath_values(
        node,
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' forum-post__signature ')]//text()",
    )
    text = _clean_text(" ".join(str(value) for value in values))
    return text or None


def _compose_cleaned_text(title: str | None, posts: list[ForumPost]) -> str:
    lines: list[str] = []
    if title:
        lines.append(title)

    for index, post in enumerate(posts):
        heading = _build_post_heading(index, post)
        if heading:
            lines.append(heading)
        lines.extend(post.body_paragraphs)

    return "\n\n".join(line for line in lines if line).strip()


def _build_post_heading(index: int, post: ForumPost) -> str:
    label = "Opening post" if index == 0 else f"Reply {index}"
    details = [label]
    if post.author:
        details.append(post.author)
    if post.published_at:
        details.append(post.published_at)
    return " | ".join(detail for detail in details if detail)


def _extract_regex_paragraphs(content_html: str) -> list[str]:
    paragraph_matches = re.findall(r"<p[^>]*>(.*?)</p>", content_html or "", flags=re.IGNORECASE | re.DOTALL)
    paragraphs = [_clean_regex_text(match) for match in paragraph_matches]
    paragraphs = [paragraph for paragraph in paragraphs if paragraph]
    if paragraphs:
        return paragraphs

    fallback = _clean_regex_text(content_html)
    return [fallback] if fallback else []


def _clean_regex_text(value: str) -> str:
    text = _TAG_RE.sub(" ", value or "")
    text = text.replace("&nbsp;", " ").replace("\xa0", " ")
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()
