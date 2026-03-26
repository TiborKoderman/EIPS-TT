"""DB-backed URL frontier using crawldb.page as the queue.

We represent one queued URL as a row in crawldb.page with page_type_code='FRONTIER'.
The URL stored in `canonical_url` (and also `url` for compatibility) is always a
canonical absolute URL.

This module is intentionally small and pragmatic:
-- enqueue(): insert-if-new by canonical_url
-- claim_next(): atomically claim a due URL (priority + due time)
-- mark_done(): delete frontier row after it is processed
-- reschedule_with_backoff(): push it into the future after errors

The actual persistence of fetched pages is handled by PostgresPageStore.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from psycopg2.extensions import connection as PgConnection

from utils.url_canonicalizer import DefaultUrlCanonicalizer, UrlCanonicalizer


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FrontierUrl:
    """A leased URL from the frontier."""

    frontier_page_id: int
    canonical_url: str
    priority: float
    depth: int
    created_at: datetime
    next_fetch_time: datetime
    source_page_id: int | None


class PostgresFrontierStore:
    """Frontier queue implemented on top of crawldb.page rows."""

    def __init__(self, conn: PgConnection):
        self._conn = conn

    def enqueue(
        self,
        raw_url: str,
        *,
        base_url: str | None = None,
        source_page_id: int | None = None,
        depth: int = 0,
        priority: float = 0.0,
        next_fetch_time: datetime | None = None,
        canonicalizer: UrlCanonicalizer | None = None,
    ) -> str:
        """Enqueue URL into frontier.

        Returns canonical_url.

        Notes:
        - If canonical_url already exists in crawldb.page (any page_type_code),
          we do not enqueue again.
        - If it doesn't exist, we insert a FRONTIER row.
        """
        canonicalizer = canonicalizer or DefaultUrlCanonicalizer()
        canonical_url = canonicalizer.canonicalize(raw_url, base_url=base_url)

        now = _utc_now()
        due = next_fetch_time or now

        # Concurrency-safe: rely on UNIQUE(canonical_url) to prevent duplicates.
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
                    duplicate_of_page_id,
                    frontier_priority,
                    frontier_depth,
                    frontier_created_at,
                    frontier_next_fetch_time,
                    frontier_source_page_id,
                    frontier_claimed_by,
                    frontier_claimed_at
                )
                VALUES (
                    NULL,
                    'FRONTIER',
                    %s,
                    %s,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    NULL,
                    NULL
                )
                ON CONFLICT DO NOTHING;
                """,
                (
                    canonical_url,
                    canonical_url,
                    priority,
                    depth,
                    now,
                    due,
                    source_page_id,
                ),
            )

        # link creation is handled later when we know the final page_id.
        self._conn.commit()
        return canonical_url


    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> FrontierUrl | None:
        """Atomically claim a due URL from the frontier.

        Uses SKIP LOCKED so multiple worker threads/processes can safely claim.
        """
        now = now or _utc_now()

        with self._conn.cursor() as cur:
            cur.execute(
                """
                WITH candidate AS (
                    SELECT id
                    FROM crawldb.page
                    WHERE page_type_code = 'FRONTIER'
                      AND frontier_claimed_by IS NULL
                      AND frontier_next_fetch_time <= %s
                    ORDER BY frontier_priority DESC, frontier_next_fetch_time ASC, id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE crawldb.page p
                SET frontier_claimed_by = %s,
                    frontier_claimed_at = %s
                FROM candidate
                WHERE p.id = candidate.id
                RETURNING p.id, p.canonical_url, p.frontier_priority,
                          COALESCE(p.frontier_depth, 0),
                          COALESCE(p.frontier_created_at, NOW()),
                          p.frontier_next_fetch_time,
                          p.frontier_source_page_id;
                """,
                (now, worker_id, now),
            )
            row = cur.fetchone()

        self._conn.commit()
        if row is None:
            return None

        (
            frontier_page_id,
            canonical_url,
            priority,
            depth,
            created_at,
            next_fetch_time,
            source_page_id,
        ) = row
        return FrontierUrl(
            frontier_page_id=frontier_page_id,
            canonical_url=canonical_url,
            priority=float(priority or 0.0),
            depth=int(depth or 0),
            created_at=created_at,
            next_fetch_time=next_fetch_time,
            source_page_id=source_page_id,
        )

    def mark_done(self, frontier_page_id: int) -> None:
        """Remove a frontier row after successful processing."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM crawldb.page
                WHERE id = %s
                  AND page_type_code = 'FRONTIER';
                """,
                (frontier_page_id,),
            )
        self._conn.commit()

    def reschedule_with_backoff(
        self,
        frontier_page_id: int,
        *,
        error: str,
        base_delay_seconds: int = 30,
        max_delay_seconds: int = 60 * 30,
        now: datetime | None = None,
    ) -> None:
        """Reschedule a failed URL into the future using exponential backoff."""
        now = now or _utc_now()

        with self._conn.cursor() as cur:
            # Since we keep schema minimal (no retry counter), we use a simple fixed backoff.
            delay = min(max_delay_seconds, base_delay_seconds)
            due = now + timedelta(seconds=delay)

            cur.execute(
                """
                UPDATE crawldb.page
                SET frontier_next_fetch_time = %s,
                    frontier_claimed_by = NULL,
                    frontier_claimed_at = NULL
                WHERE id = %s AND page_type_code = 'FRONTIER';
                """,
                (due, frontier_page_id),
            )

        self._conn.commit()



