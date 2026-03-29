"""Content-hash deduplication helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Callable, Literal, Protocol


class ContentHasher(Protocol):
    """Interface for page-content hashing."""

    def hash_content(self, html_content: str) -> str:
        """Return a stable hash for HTML content."""


@dataclass(frozen=True)
class Sha256ContentHasher:
    """Default content hasher for duplicate detection."""

    def hash_content(self, html_content: str) -> str:
        return hashlib.sha256(html_content.encode("utf-8")).hexdigest()


PageStatus = Literal["known_url", "new_html", "duplicate_content"]


@dataclass(frozen=True)
class PageClassification:
    """Result of deciding what to do with a newly discovered page."""

    status: PageStatus
    url: str
    content_hash: str
    existing_page_id: int | None
    duplicate_of_page_id: int | None


def classify_page(
    *,
    url: str,
    html_content: str,
    find_page_id_by_url: Callable[[str], int | None],
    find_page_id_by_content_hash: Callable[[str], int | None],
    hasher: ContentHasher,
) -> PageClassification:
    """Classify incoming page as known URL, new HTML, or duplicate content."""
    existing_page_id = find_page_id_by_url(url)
    if existing_page_id is not None:
        return PageClassification(
            status="known_url",
            url=url,
            content_hash="",
            existing_page_id=existing_page_id,
            duplicate_of_page_id=None,
        )

    content_hash = hasher.hash_content(html_content)
    duplicate_of_page_id = find_page_id_by_content_hash(content_hash)
    if duplicate_of_page_id is not None:
        return PageClassification(
            status="duplicate_content",
            url=url,
            content_hash=content_hash,
            existing_page_id=None,
            duplicate_of_page_id=duplicate_of_page_id,
        )

    return PageClassification(
        status="new_html",
        url=url,
        content_hash=content_hash,
        existing_page_id=None,
        duplicate_of_page_id=None,
    )
