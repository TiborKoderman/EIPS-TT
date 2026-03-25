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
    canonical_url: str
    content_hash: str
    existing_page_id: int | None
    duplicate_of_page_id: int | None


def classify_page(
    *,
    canonical_url: str,
    html_content: str,
    find_page_id_by_url: Callable[[str], int | None],
    find_page_id_by_content_hash: Callable[[str], int | None],
    hasher: ContentHasher,
) -> PageClassification:
    """Classify incoming page as known URL, new HTML, or duplicate content."""
    existing_page_id = find_page_id_by_url(canonical_url)
    if existing_page_id is not None:
        return PageClassification(
            status="known_url",
            canonical_url=canonical_url,
            content_hash="",
            existing_page_id=existing_page_id,
            duplicate_of_page_id=None,
        )

    content_hash = hasher.hash_content(html_content)
    duplicate_of_page_id = find_page_id_by_content_hash(content_hash)
    if duplicate_of_page_id is not None:
        return PageClassification(
            status="duplicate_content",
            canonical_url=canonical_url,
            content_hash=content_hash,
            existing_page_id=None,
            duplicate_of_page_id=duplicate_of_page_id,
        )

    return PageClassification(
        status="new_html",
        canonical_url=canonical_url,
        content_hash=content_hash,
        existing_page_id=None,
        duplicate_of_page_id=None,
    )
