"""Persistent swap store for frontier overflow entries."""

from __future__ import annotations

from datetime import datetime

from psycopg2.extensions import connection as PgConnection

from core.frontier import FrontierEntry, FrontierSwapStore


class PostgresFrontierSwapStore(FrontierSwapStore):
    """Stores overflow frontier rows in crawldb.frontier_queue."""

    def __init__(self, conn: PgConnection):
        self._conn = conn

    def enqueue(self, entry: FrontierEntry) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO crawldb.frontier_queue (
                    url,
                    priority,
                    source_url,
                    depth,
                    state,
                    discovered_at
                )
                VALUES (%s, %s, %s, %s, 'queued', %s)
                ON CONFLICT (url)
                DO NOTHING;
                """,
                (
                    entry.url,
                    entry.priority,
                    entry.source_url,
                    entry.depth,
                    entry.discovered_at,
                ),
            )
        self._conn.commit()

    def dequeue_batch(self, limit: int = 1000) -> list[FrontierEntry]:
        rows: list[tuple[str, int, str | None, int, datetime]] = []
        with self._conn.cursor() as cur:
            cur.execute(
                """
                WITH picked AS (
                    SELECT url, priority, source_url, depth, discovered_at
                    FROM crawldb.frontier_queue
                    WHERE state = 'queued'
                    ORDER BY priority DESC, discovered_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE crawldb.frontier_queue q
                SET state = 'in_memory', dequeued_at = NOW()
                FROM picked
                WHERE q.url = picked.url
                RETURNING picked.url, picked.priority, picked.source_url, picked.depth, picked.discovered_at;
                """,
                (limit,),
            )
            rows = cur.fetchall()
        self._conn.commit()

        return [
            FrontierEntry(
                url=url,
                priority=priority,
                source_url=source_url,
                depth=depth,
                discovered_at=discovered_at,
            )
            for (url, priority, source_url, depth, discovered_at) in rows
        ]

    def count(self) -> int:
        with self._conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM crawldb.frontier_queue WHERE state = 'queued';")
            row = cur.fetchone()
        return int(row[0]) if row else 0
