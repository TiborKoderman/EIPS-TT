"""Database helpers for canonical URL + duplicate-aware page ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from psycopg2.extensions import connection as PgConnection

from utils.dedup import (
    ContentHasher,
    PageClassification,
    Sha256ContentHasher,
    classify_page,
)
from utils.url_canonicalizer import DefaultUrlCanonicalizer, UrlCanonicalizer


class PageStore(Protocol):
    """Interface for page persistence helpers."""

    def find_page_id_by_url(self, canonical_url: str) -> int | None:
        """Find page id by canonical URL."""

    def find_page_id_by_content_hash(self, content_hash: str) -> int | None:
        """Find page id by exact content hash."""


@dataclass(frozen=True)
class IngestPageResult:
    """Outcome of handling one page popped from the frontier."""

    page_id: int
    status: str
    canonical_url: str
    duplicate_of_page_id: int | None
    content_hash: str | None


class PostgresPageStore:
    """PostgreSQL implementation of the page store workflow."""

    def __init__(self, conn: PgConnection):
        self._conn = conn

    def find_page_id_by_url(self, canonical_url: str) -> int | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM crawldb.page
                WHERE canonical_url = %s
                LIMIT 1;
                """,
                (canonical_url,),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def find_page_id_by_content_hash(self, content_hash: str) -> int | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM crawldb.page
                WHERE content_hash = %s
                AND page_type_code = 'HTML'
                LIMIT 1;
                """,
                (content_hash,),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def add_link(self, from_page_id: int, to_page_id: int) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO crawldb.link (from_page, to_page)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING;
                """,
                (from_page_id, to_page_id),
            )

    def insert_html_page(
        self,
        *,
        site_id: int | None,
        canonical_url: str,
        http_status_code: int | None,
        html_content: str,
        content_hash: str,
        accessed_time: datetime | None = None,
    ) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO crawldb.page (
                    site_id,
                    page_type_code,
                    url,
                    canonical_url,
                    html_content,
                    http_status_code,
                    accessed_time,
                    content_hash,
                    duplicate_of_page_id
                )
                VALUES (%s, 'HTML', %s, %s, %s, %s, COALESCE(%s, NOW()), %s, NULL)
                RETURNING id;
                """,
                (
                    site_id,
                    canonical_url,
                    canonical_url,
                    html_content,
                    http_status_code,
                    accessed_time,
                    content_hash,
                ),
            )
            row = cur.fetchone()
        if row is None:
            raise RuntimeError("Failed to insert HTML page.")
        return row[0]

    def insert_duplicate_page(
        self,
        *,
        site_id: int | None,
        canonical_url: str,
        http_status_code: int | None,
        duplicate_of_page_id: int,
        content_hash: str,
        accessed_time: datetime | None = None,
    ) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO crawldb.page (
                    site_id,
                    page_type_code,
                    url,
                    canonical_url,
                    html_content,
                    http_status_code,
                    accessed_time,
                    content_hash,
                    duplicate_of_page_id
                )
                VALUES (%s, 'DUPLICATE', %s, %s, NULL, %s, COALESCE(%s, NOW()), %s, %s)
                RETURNING id;
                """,
                (
                    site_id,
                    canonical_url,
                    canonical_url,
                    http_status_code,
                    accessed_time,
                    content_hash,
                    duplicate_of_page_id,
                ),
            )
            row = cur.fetchone()
        if row is None:
            raise RuntimeError("Failed to insert DUPLICATE page.")
        return row[0]

    def ingest_html_from_frontier(
        self,
        *,
        raw_url: str,
        html_content: str,
        site_id: int | None,
        source_page_id: int | None = None,
        base_url: str | None = None,
        http_status_code: int | None = 200,
        canonicalizer: UrlCanonicalizer | None = None,
        hasher: ContentHasher | None = None,
    ) -> IngestPageResult:
        """Store a crawled page with canonical URL and dedup logic."""
        canonicalizer = canonicalizer or DefaultUrlCanonicalizer()
        hasher = hasher or Sha256ContentHasher()
        canonical_url = canonicalizer.canonicalize(raw_url, base_url=base_url)

        classification: PageClassification = classify_page(
            canonical_url=canonical_url,
            html_content=html_content,
            find_page_id_by_url=self.find_page_id_by_url,
            find_page_id_by_content_hash=self.find_page_id_by_content_hash,
            hasher=hasher,
        )

        if classification.status == "known_url":
            page_id = classification.existing_page_id
            if page_id is None:
                raise RuntimeError("Known URL classification missing page id.")
        elif classification.status == "duplicate_content":
            if classification.duplicate_of_page_id is None:
                raise RuntimeError("Duplicate classification missing duplicate_of_page_id.")
            page_id = self.insert_duplicate_page(
                site_id=site_id,
                canonical_url=classification.canonical_url,
                http_status_code=http_status_code,
                duplicate_of_page_id=classification.duplicate_of_page_id,
                content_hash=classification.content_hash,
            )
        else:
            page_id = self.insert_html_page(
                site_id=site_id,
                canonical_url=classification.canonical_url,
                http_status_code=http_status_code,
                html_content=html_content,
                content_hash=classification.content_hash,
            )

        if source_page_id is not None:
            self.add_link(source_page_id, page_id)

        self._conn.commit()
        return IngestPageResult(
            page_id=page_id,
            status=classification.status,
            canonical_url=classification.canonical_url,
            duplicate_of_page_id=classification.duplicate_of_page_id,
            content_hash=classification.content_hash or None,
        )
