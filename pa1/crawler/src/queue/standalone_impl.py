"""Standalone mode frontier queue implementation using direct PostgreSQL access."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from api.service_protocol import FrontierQueueProvider, QueuedUrl
from core.frontier import FrontierEntry
from db.frontier_store import PostgresFrontierSwapStore
from db.pg_connect import get_connection, load_db_config


class StandaloneFrontierQueue(FrontierQueueProvider):
    """Frontier queue implementation for standalone mode (direct DB access).
    
    Workers use this implementation when running in standalone mode.
    All operations are synchronous but wrapped in async methods for protocol compatibility.
    """

    def __init__(self):
        """Initialize standalone queue provider with database connection pool."""
        self._db_config = load_db_config()
        self._conn = None

    async def next_url(self, worker_id: int) -> QueuedUrl | None:
        """Get next URL to process (QUEUED → LOCKED transition).
        
        Atomically claims a QUEUED URL and transitions it to LOCKED state.
        
        Args:
            worker_id: Worker requesting next URL
            
        Returns:
            QueuedUrl with lease token for tracking, or None if no QUEUED items
        """
        try:
            with get_connection(self._db_config) as conn:
                with conn.cursor() as cur:
                    # Atomic claim: SELECT + UPDATE in same transaction
                    cur.execute(
                        """
                        WITH picked AS (
                            SELECT id, url, priority, source_url, depth, discovered_at
                            FROM crawldb.frontier_queue
                            WHERE state = 'QUEUED'
                            ORDER BY priority DESC, discovered_at ASC
                            LIMIT 1
                            FOR UPDATE SKIP LOCKED
                        )
                        UPDATE crawldb.frontier_queue q
                        SET state = 'LOCKED', locked_at = NOW(), locked_by_worker_id = %s
                        FROM picked
                        WHERE q.id = picked.id
                        RETURNING picked.id, picked.url, picked.priority, picked.source_url, picked.depth;
                        """,
                        (worker_id,),
                    )
                    row = cur.fetchone()
                    conn.commit()

                if row is None:
                    return None

                entry_id, url, priority, source_url, depth = row
                # Use entry_id as lease token for tracking
                return QueuedUrl(
                    url=url,
                    lease_token=str(entry_id),
                    priority=priority,
                    source_url=source_url,
                    depth=depth,
                )
        except Exception as e:
            print(f"[standalone-queue] error claiming next URL: {e}")
            return None

    async def mark_complete(
        self, worker_id: int, url: str, lease_token: str | None = None
    ) -> bool:
        """Mark URL as successfully processed (LOCKED → COMPLETED).
        
        Args:
            worker_id: Worker that processed the URL
            url: Canonical URL that was processed
            lease_token: Lease token from next_url() call (optional verification)
            
        Returns:
            True if successfully marked as complete
        """
        try:
            with get_connection(self._db_config) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE crawldb.frontier_queue
                        SET state = 'COMPLETED', finished_at = NOW()
                        WHERE url = %s AND state = 'LOCKED' AND locked_by_worker_id = %s;
                        """,
                        (url, worker_id),
                    )
                    conn.commit()
                    return cur.rowcount > 0
        except Exception as e:
            print(f"[standalone-queue] error marking URL complete: {e}")
            return False

    async def mark_failed(
        self, worker_id: int, url: str, error: str, lease_token: str | None = None
    ) -> bool:
        """Mark URL as failed (LOCKED → FAILED).
        
        Args:
            worker_id: Worker that encountered the failure
            url: Canonical URL that failed
            error: Error message/description
            lease_token: Lease token from next_url() call (optional verification)
            
        Returns:
            True if successfully marked as failed
        """
        try:
            with get_connection(self._db_config) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE crawldb.frontier_queue
                        SET state = 'FAILED', finished_at = NOW()
                        WHERE url = %s AND state = 'LOCKED' AND locked_by_worker_id = %s;
                        """,
                        (url, worker_id),
                    )
                    conn.commit()
                    return cur.rowcount > 0
        except Exception as e:
            print(f"[standalone-queue] error marking URL failed: {e}")
            return False

    async def mark_duplicate(
        self,
        worker_id: int,
        url: str,
        duplicate_of_url: str,
        lease_token: str | None = None,
    ) -> bool:
        """Mark URL as duplicate (LOCKED → DUPLICATE).
        
        Still records the link edge to maintain the duplicate relationship.
        
        Args:
            worker_id: Worker that detected the duplicate
            url: Canonical URL that is a duplicate
            duplicate_of_url: The canonical URL this duplicates
            lease_token: Lease token from next_url() call (optional verification)
            
        Returns:
            True if successfully marked as duplicate
        """
        try:
            with get_connection(self._db_config) as conn:
                with conn.cursor() as cur:
                    # Get the ID of the duplicate_of URL
                    cur.execute(
                        "SELECT id FROM crawldb.frontier_queue WHERE url = %s LIMIT 1;",
                        (duplicate_of_url,),
                    )
                    dup_row = cur.fetchone()
                    duplicate_of_url_id = dup_row[0] if dup_row else None

                    # Mark this URL as duplicate
                    cur.execute(
                        """
                        UPDATE crawldb.frontier_queue
                        SET state = 'DUPLICATE', 
                            finished_at = NOW(),
                            duplicate_of_url_id = %s
                        WHERE url = %s AND state = 'LOCKED' AND locked_by_worker_id = %s;
                        """,
                        (duplicate_of_url_id, url, worker_id),
                    )
                    conn.commit()
                    return cur.rowcount > 0
        except Exception as e:
            print(f"[standalone-queue] error marking URL duplicate: {e}")
            return False

    async def add_discovered_urls(self, discovered: list[FrontierEntry]) -> int:
        """Add newly discovered URLs to the frontier queue.
        
        Bulk enqueue operation. Skips duplicates via ON CONFLICT.
        
        Args:
            discovered: List of FrontierEntry items to enqueue
            
        Returns:
            Number of URLs successfully added
        """
        if not discovered:
            return 0

        try:
            added = 0
            with get_connection(self._db_config) as conn:
                with conn.cursor() as cur:
                    for entry in discovered:
                        cur.execute(
                            """
                            INSERT INTO crawldb.frontier_queue (
                                url, priority, source_url, depth, state, discovered_at
                            )
                            VALUES (%s, %s, %s, %s, 'QUEUED', %s)
                            ON CONFLICT (url) DO NOTHING;
                            """,
                            (
                                entry.url,
                                entry.priority,
                                entry.source_url,
                                entry.depth,
                                entry.discovered_at,
                            ),
                        )
                        if cur.rowcount > 0:
                            added += 1
                conn.commit()
            return added
        except Exception as e:
            print(f"[standalone-queue] error adding discovered URLs: {e}")
            return 0

    async def get_frontier_stats(self) -> dict[str, object]:
        """Get current frontier queue statistics.
        
        Returns:
            Dict with counts per state
        """
        try:
            with get_connection(self._db_config) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT state, COUNT(*) as count
                        FROM crawldb.frontier_queue
                        GROUP BY state;
                        """
                    )
                    rows = cur.fetchall()

            stats: dict[str, object] = {
                "queued": 0,
                "locked": 0,
                "processing": 0,
                "completed": 0,
                "duplicate": 0,
                "failed": 0,
            }

            for state, count in rows:
                if state:
                    key = state.lower()
                    if key in stats:
                        stats[key] = count

            return stats
        except Exception as e:
            print(f"[standalone-queue] error getting frontier stats: {e}")
            return {}
