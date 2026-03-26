"""Database helpers for URL + duplicate-aware page ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from core.downloader import DownloadResult
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

    def find_page_id_by_url(self, url: str) -> int | None:
        """Find page id by canonicalized URL."""

    def find_page_id_by_content_hash(self, content_hash: str) -> int | None:
        """Find page id by exact content hash."""


@dataclass(frozen=True)
class IngestPageResult:
    """Outcome of handling one page popped from the frontier."""

    page_id: int
    status: str
    url: str
    duplicate_of_page_id: int | None
    content_hash: str | None


class PostgresPageStore:
    """PostgreSQL implementation of the page store workflow."""

    def __init__(self, conn: PgConnection):
        self._conn = conn

    def find_page_id_by_url(self, url: str) -> int | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM crawldb.page
                WHERE url = %s
                LIMIT 1;
                """,
                (url,),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def find_page_snapshot_by_url(self, url: str) -> tuple[int, str | None] | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, page_type_code
                FROM crawldb.page
                WHERE url = %s
                LIMIT 1;
                """,
                (url,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return int(row[0]), row[1]

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

    def ensure_frontier_page(self, *, site_id: int | None, url: str) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO crawldb.page (
                    site_id,
                    page_type_code,
                    url,
                    html_content,
                    http_status_code,
                    accessed_time,
                    content_hash,
                    duplicate_of_page_id
                )
                VALUES (%s, 'FRONTIER', %s, NULL, NULL, NULL, NULL, NULL)
                ON CONFLICT (url)
                DO UPDATE SET url = EXCLUDED.url
                RETURNING id;
                """,
                (site_id, url),
            )
            row = cur.fetchone()
        if row is None:
            raise RuntimeError("Failed to ensure FRONTIER page.")
        return int(row[0])

    def update_page(
        self,
        *,
        page_id: int,
        site_id: int | None,
        page_type_code: str,
        http_status_code: int | None,
        html_content: str | None,
        content_hash: str | None,
        duplicate_of_page_id: int | None,
        accessed_time: datetime | None = None,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE crawldb.page
                SET site_id = COALESCE(%s, site_id),
                    page_type_code = %s,
                    html_content = %s,
                    http_status_code = %s,
                    accessed_time = COALESCE(%s, NOW()),
                    content_hash = %s,
                    duplicate_of_page_id = %s
                WHERE id = %s;
                """,
                (
                    site_id,
                    page_type_code,
                    html_content,
                    http_status_code,
                    accessed_time,
                    content_hash,
                    duplicate_of_page_id,
                    page_id,
                ),
            )

    def insert_html_page(
        self,
        *,
        site_id: int | None,
        url: str,
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
                    html_content,
                    http_status_code,
                    accessed_time,
                    content_hash,
                    duplicate_of_page_id
                )
                VALUES (%s, 'HTML', %s, %s, %s, COALESCE(%s, NOW()), %s, NULL)
                RETURNING id;
                """,
                (
                    site_id,
                    url,
                    html_content,
                    http_status_code,
                    accessed_time,
                    content_hash,
                ),
            )
            row = cur.fetchone()
        if row is None:
            raise RuntimeError("Failed to insert HTML page.")
        return int(row[0])

    def insert_duplicate_page(
        self,
        *,
        site_id: int | None,
        url: str,
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
                    html_content,
                    http_status_code,
                    accessed_time,
                    content_hash,
                    duplicate_of_page_id
                )
                VALUES (%s, 'DUPLICATE', %s, NULL, %s, COALESCE(%s, NOW()), %s, %s)
                RETURNING id;
                """,
                (
                    site_id,
                    url,
                    http_status_code,
                    accessed_time,
                    content_hash,
                    duplicate_of_page_id,
                ),
            )
            row = cur.fetchone()
        if row is None:
            raise RuntimeError("Failed to insert DUPLICATE page.")
        return int(row[0])

    def insert_binary_page(
        self,
        *,
        site_id: int | None,
        url: str,
        http_status_code: int | None,
        accessed_time: datetime | None = None,
    ) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO crawldb.page (
                    site_id,
                    page_type_code,
                    url,
                    html_content,
                    http_status_code,
                    accessed_time,
                    content_hash,
                    duplicate_of_page_id
                )
                VALUES (%s, 'BINARY', %s, NULL, %s, COALESCE(%s, NOW()), NULL, NULL)
                RETURNING id;
                """,
                (
                    site_id,
                    url,
                    http_status_code,
                    accessed_time,
                ),
            )
            row = cur.fetchone()
        if row is None:
            raise RuntimeError("Failed to insert BINARY page.")
        return int(row[0])

    def insert_page_data(
        self,
        *,
        page_id: int,
        data_type_code: str,
        binary_data: bytes | None,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO crawldb.page_data (page_id, data_type_code, data)
                VALUES (%s, %s, %s);
                """,
                (page_id, data_type_code, binary_data),
            )

    def add_discovered_links(
        self,
        *,
        source_page_id: int,
        discovered_urls: list[str],
        site_id: int | None,
        canonicalizer: UrlCanonicalizer,
    ) -> None:
        seen: set[str] = set()
        for raw_url in discovered_urls:
            url = canonicalizer.canonicalize(raw_url)
            if not url or url in seen:
                continue
            seen.add(url)
            target_page_id = self.ensure_frontier_page(site_id=site_id, url=url)
            if target_page_id == source_page_id:
                continue
            self.add_link(source_page_id, target_page_id)

    def ingest_download_result(
        self,
        *,
        raw_url: str,
        download_result: DownloadResult,
        site_id: int | None,
        source_page_id: int | None = None,
        discovered_urls: list[str] | None = None,
        canonicalizer: UrlCanonicalizer | None = None,
    ) -> IngestPageResult:
        canonicalizer = canonicalizer or DefaultUrlCanonicalizer()
        hasher = Sha256ContentHasher()
        url = canonicalizer.canonicalize(raw_url, base_url=download_result.final_url)
        existing_page = self.find_page_snapshot_by_url(url)

        if download_result.page_type_code == "HTML":
            result = self.ingest_html_from_frontier(
                raw_url=raw_url,
                html_content=download_result.html_content or "",
                site_id=site_id,
                source_page_id=source_page_id,
                discovered_urls=discovered_urls,
                base_url=download_result.final_url,
                http_status_code=download_result.status_code,
                canonicalizer=canonicalizer,
                hasher=hasher,
            )
            return result

        if existing_page is not None:
            page_id, existing_page_type = existing_page
            self.update_page(
                page_id=page_id,
                site_id=site_id,
                page_type_code="BINARY",
                http_status_code=download_result.status_code,
                html_content=None,
                content_hash=None,
                duplicate_of_page_id=None,
            )
            status = "promoted_binary" if existing_page_type == "FRONTIER" else "known_url"
        else:
            page_id = self.insert_binary_page(
                site_id=site_id,
                url=url,
                http_status_code=download_result.status_code,
            )
            status = "inserted_binary"

        if download_result.data_type_code:
            self.insert_page_data(
                page_id=page_id,
                data_type_code=download_result.data_type_code,
                binary_data=download_result.binary_content,
            )

        if source_page_id is not None:
            self.add_link(source_page_id, page_id)
        if discovered_urls:
            self.add_discovered_links(
                source_page_id=page_id,
                discovered_urls=discovered_urls,
                site_id=site_id,
                canonicalizer=canonicalizer,
            )

        self._conn.commit()
        return IngestPageResult(
            page_id=page_id,
            status=status,
            url=url,
            duplicate_of_page_id=None,
            content_hash=None,
        )

    def ingest_html_from_frontier(
        self,
        *,
        raw_url: str,
        html_content: str,
        site_id: int | None,
        source_page_id: int | None = None,
        discovered_urls: list[str] | None = None,
        base_url: str | None = None,
        http_status_code: int | None = 200,
        canonicalizer: UrlCanonicalizer | None = None,
        hasher: ContentHasher | None = None,
    ) -> IngestPageResult:
        """Store a crawled page with URL and dedup logic."""
        canonicalizer = canonicalizer or DefaultUrlCanonicalizer()
        hasher = hasher or Sha256ContentHasher()
        url = canonicalizer.canonicalize(raw_url, base_url=base_url)
        existing_page = self.find_page_snapshot_by_url(url)

        if existing_page is not None and existing_page[1] != "FRONTIER":
            page_id = existing_page[0]
            content_hash = hasher.hash_content(html_content)
            self.update_page(
                page_id=page_id,
                site_id=site_id,
                page_type_code="HTML",
                http_status_code=http_status_code,
                html_content=html_content,
                content_hash=content_hash,
                duplicate_of_page_id=None,
            )
            duplicate_of_page_id = None
            status = "known_url"
        else:
            classification: PageClassification
            if existing_page is not None and existing_page[1] == "FRONTIER":
                content_hash = hasher.hash_content(html_content)
                duplicate_of_page_id = self.find_page_id_by_content_hash(content_hash)
                if duplicate_of_page_id is not None:
                    classification = PageClassification(
                        status="duplicate_content",
                        url=url,
                        content_hash=content_hash,
                        existing_page_id=existing_page[0],
                        duplicate_of_page_id=duplicate_of_page_id,
                    )
                else:
                    classification = PageClassification(
                        status="new_html",
                        url=url,
                        content_hash=content_hash,
                        existing_page_id=existing_page[0],
                        duplicate_of_page_id=None,
                    )
            else:
                classification = classify_page(
                    url=url,
                    html_content=html_content,
                    find_page_id_by_url=self.find_page_id_by_url,
                    find_page_id_by_content_hash=self.find_page_id_by_content_hash,
                    hasher=hasher,
                )

            if classification.status == "known_url":
                page_id = classification.existing_page_id
                if page_id is None:
                    raise RuntimeError("Known URL classification missing page id.")
                status = "known_url"
            elif classification.status == "duplicate_content":
                if classification.duplicate_of_page_id is None:
                    raise RuntimeError("Duplicate classification missing duplicate_of_page_id.")
                if existing_page is not None and existing_page[1] == "FRONTIER":
                    page_id = existing_page[0]
                    self.update_page(
                        page_id=page_id,
                        site_id=site_id,
                        page_type_code="DUPLICATE",
                        http_status_code=http_status_code,
                        html_content=None,
                        content_hash=classification.content_hash,
                        duplicate_of_page_id=classification.duplicate_of_page_id,
                    )
                    status = "promoted_duplicate"
                else:
                    page_id = self.insert_duplicate_page(
                        site_id=site_id,
                        url=classification.url,
                        http_status_code=http_status_code,
                        duplicate_of_page_id=classification.duplicate_of_page_id,
                        content_hash=classification.content_hash,
                    )
                    status = classification.status
            else:
                if existing_page is not None and existing_page[1] == "FRONTIER":
                    page_id = existing_page[0]
                    self.update_page(
                        page_id=page_id,
                        site_id=site_id,
                        page_type_code="HTML",
                        http_status_code=http_status_code,
                        html_content=html_content,
                        content_hash=classification.content_hash,
                        duplicate_of_page_id=None,
                    )
                    status = "promoted_html"
                else:
                    page_id = self.insert_html_page(
                        site_id=site_id,
                        url=classification.url,
                        http_status_code=http_status_code,
                        html_content=html_content,
                        content_hash=classification.content_hash,
                    )
                    status = classification.status

            duplicate_of_page_id = classification.duplicate_of_page_id
            content_hash = classification.content_hash

        if source_page_id is not None:
            self.add_link(source_page_id, page_id)
        if discovered_urls:
            self.add_discovered_links(
                source_page_id=page_id,
                discovered_urls=discovered_urls,
                site_id=site_id,
                canonicalizer=canonicalizer,
            )

        self._conn.commit()
        return IngestPageResult(
            page_id=page_id,
            status=status,
            url=url,
            duplicate_of_page_id=duplicate_of_page_id,
            content_hash=content_hash or None,
        )
