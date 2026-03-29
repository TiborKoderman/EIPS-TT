#!/usr/bin/env python3
"""Re-parse stored HTML pages and backfill discovered links/images.

This script avoids full recrawling by reusing crawldb.page.html_content for pages
already stored as HTML. It extracts links/images again and updates DB structures:

- crawldb.page: upsert discovered targets (FRONTIER for generic links, BINARY for media/docs)
- crawldb.link: insert source -> target edges
- crawldb.frontier_queue: enqueue newly discovered FRONTIER targets
- crawldb.page_data: ensure doc data_type rows (PDF/DOC/DOCX/PPT/PPTX) exist with NULL data

It does not fetch network content and does not modify existing HTML payload rows.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

import psycopg2
from psycopg2.extras import execute_values

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CRAWLER_SRC_DIR = os.path.join(ROOT_DIR, "pa1", "crawler", "src")
if CRAWLER_SRC_DIR not in sys.path:
    sys.path.insert(0, CRAWLER_SRC_DIR)

from core.parser import parse_outgoing_urls  # noqa: E402
from utils.url_canonicalizer import DefaultUrlCanonicalizer  # noqa: E402

DOC_TYPES = ("PDF", "DOC", "DOCX", "PPT", "PPTX")
DOC_EXT_TO_TYPE = {
    ".pdf": "PDF",
    ".docx": "DOCX",
    ".pptx": "PPTX",
    ".doc": "DOC",
    ".ppt": "PPT",
}
IMAGE_EXTENSIONS = {
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
    ".gif",
    ".svg",
    ".bmp",
    ".tiff",
    ".tif",
    ".ico",
    ".avif",
}


@dataclass(frozen=True)
class HtmlPageRow:
    page_id: int
    url: str
    html_content: str


@dataclass
class LinkCandidate:
    url: str
    page_type_code: str  # FRONTIER | BINARY
    doc_type_code: str | None = None


@dataclass
class ReparseStats:
    html_pages_scanned: int = 0
    parse_errors: int = 0
    extracted_links: int = 0
    extracted_images: int = 0
    unique_candidates: int = 0
    page_rows_inserted: int = 0
    page_rows_promoted_to_binary: int = 0
    link_edges_attempted: int = 0
    frontier_rows_attempted: int = 0
    page_data_rows_inserted: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-parse stored HTML and backfill discovered links.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of HTML pages scanned (0 = all).")
    parser.add_argument("--dry-run", action="store_true", help="Analyze only; do not write DB changes.")
    parser.add_argument("--commit-every", type=int, default=100, help="Commit interval in pages.")
    return parser.parse_args()


def db_connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ.get("DB_NAME", "crawldb"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "postgres"),
    )


def fetch_html_pages(conn: psycopg2.extensions.connection, limit: int) -> list[HtmlPageRow]:
    sql = """
        SELECT id, url, html_content
        FROM crawldb.page
        WHERE page_type_code = 'HTML'
          AND url IS NOT NULL
          AND html_content IS NOT NULL
          AND length(html_content) > 0
        ORDER BY id
    """
    if limit > 0:
        sql += " LIMIT %s"
        params: tuple[Any, ...] = (limit,)
    else:
        params = ()

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [HtmlPageRow(page_id=row[0], url=row[1], html_content=row[2]) for row in rows]


def ensure_doc_data_types(conn: psycopg2.extensions.connection) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO crawldb.data_type(code) VALUES (%s) ON CONFLICT (code) DO NOTHING",
            [(dtype,) for dtype in DOC_TYPES],
        )


def infer_doc_type_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    candidates: list[str] = []

    if parsed.path:
        candidates.append(parsed.path)
        candidates.append(parsed.path.rsplit("/", 1)[-1])

    if parsed.query:
        for token in parsed.query.split("&"):
            if not token:
                continue
            _, _, value = token.partition("=")
            raw = value or token
            decoded = unquote(raw.replace("+", " ")).strip()
            if decoded:
                candidates.append(decoded)
                candidates.append(decoded.rsplit("/", 1)[-1])

    for candidate in candidates:
        normalized = candidate.strip().strip('"\'()[]{}<>;').lower()
        normalized = normalized.split("#", 1)[0].split("?", 1)[0]
        for ext, dtype in sorted(DOC_EXT_TO_TYPE.items(), key=lambda item: len(item[0]), reverse=True):
            if normalized.endswith(ext):
                return dtype

    return None


def is_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in IMAGE_EXTENSIONS)


def merge_candidate(store: dict[str, LinkCandidate], url: str, *, page_type: str, doc_type: str | None = None) -> None:
    existing = store.get(url)
    if existing is None:
        store[url] = LinkCandidate(url=url, page_type_code=page_type, doc_type_code=doc_type)
        return

    # Prefer binary classification over frontier when both appear.
    if existing.page_type_code == "FRONTIER" and page_type == "BINARY":
        existing.page_type_code = "BINARY"

    # Preserve strongest available document type classification.
    if doc_type and not existing.doc_type_code:
        existing.doc_type_code = doc_type


def extract_candidates(page: HtmlPageRow, canonicalizer: DefaultUrlCanonicalizer, stats: ReparseStats) -> dict[str, LinkCandidate]:
    candidates: dict[str, LinkCandidate] = {}

    try:
        parsed = parse_outgoing_urls(
            page.html_content,
            page_url=page.url,
            canonicalizer=canonicalizer,
        )
    except Exception:
        stats.parse_errors += 1
        return candidates

    stats.extracted_links += len(parsed.links)
    stats.extracted_images += len(parsed.images)

    for link in parsed.links:
        if link == page.url:
            continue
        doc_type = infer_doc_type_from_url(link)
        if doc_type:
            merge_candidate(candidates, link, page_type="BINARY", doc_type=doc_type)
        elif is_image_url(link):
            merge_candidate(candidates, link, page_type="BINARY")
        else:
            merge_candidate(candidates, link, page_type="FRONTIER")

    for image in parsed.images:
        if image == page.url:
            continue
        doc_type = infer_doc_type_from_url(image)
        if doc_type:
            merge_candidate(candidates, image, page_type="BINARY", doc_type=doc_type)
        else:
            merge_candidate(candidates, image, page_type="BINARY")

    stats.unique_candidates += len(candidates)
    return candidates


def load_existing_pages(conn: psycopg2.extensions.connection) -> dict[str, tuple[int, str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, url, page_type_code FROM crawldb.page WHERE url IS NOT NULL")
        rows = cur.fetchall()
    return {row[1]: (row[0], row[2] or "") for row in rows}


def load_site_cache(conn: psycopg2.extensions.connection) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, domain FROM crawldb.site WHERE domain IS NOT NULL")
        rows = cur.fetchall()

    cache: dict[str, int] = {}
    for site_id, domain in rows:
        if not domain:
            continue
        key = domain.strip().lower()
        if key and key not in cache:
            cache[key] = site_id
    return cache


def resolve_site_id(
    conn: psycopg2.extensions.connection,
    site_cache: dict[str, int],
    url: str,
    dry_run: bool,
) -> int | None:
    host = (urlparse(url).hostname or "").strip().lower()
    if not host:
        return None

    cached = site_cache.get(host)
    if cached:
        return cached

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM crawldb.site WHERE lower(domain) = lower(%s) ORDER BY id LIMIT 1", (host,))
        row = cur.fetchone()
        if row:
            site_cache[host] = row[0]
            return row[0]

        if dry_run:
            return None

        cur.execute("INSERT INTO crawldb.site(domain) VALUES (%s) RETURNING id", (host,))
        inserted_id = cur.fetchone()[0]
        site_cache[host] = inserted_id
        return inserted_id


def ensure_page_for_candidate(
    conn: psycopg2.extensions.connection,
    site_cache: dict[str, int],
    page_cache: dict[str, tuple[int, str]],
    candidate: LinkCandidate,
    stats: ReparseStats,
    dry_run: bool,
) -> tuple[int | None, str]:
    known = page_cache.get(candidate.url)
    if known:
        page_id, current_type = known
        if candidate.page_type_code == "BINARY" and current_type == "FRONTIER":
            stats.page_rows_promoted_to_binary += 1
            if not dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE crawldb.page
                        SET page_type_code = 'BINARY',
                            html_content = NULL,
                            content_hash = NULL,
                            duplicate_of_page_id = NULL
                        WHERE id = %s AND page_type_code = 'FRONTIER'
                        """,
                        (page_id,),
                    )
                page_cache[candidate.url] = (page_id, "BINARY")
                current_type = "BINARY"
        return page_id, current_type

    stats.page_rows_inserted += 1
    if dry_run:
        return None, candidate.page_type_code

    site_id = resolve_site_id(conn, site_cache, candidate.url, dry_run=False)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO crawldb.page(site_id, page_type_code, url, html_content, http_status_code, accessed_time, content_hash, duplicate_of_page_id)
            VALUES (%s, %s, %s, NULL, NULL, NULL, NULL, NULL)
            ON CONFLICT (url) DO NOTHING
            RETURNING id
            """,
            (site_id, candidate.page_type_code, candidate.url),
        )
        row = cur.fetchone()
        if row:
            page_id = row[0]
            page_cache[candidate.url] = (page_id, candidate.page_type_code)
            return page_id, candidate.page_type_code

        cur.execute("SELECT id, page_type_code FROM crawldb.page WHERE url = %s", (candidate.url,))
        existing = cur.fetchone()
        if not existing:
            return None, candidate.page_type_code

        page_id, page_type_code = existing[0], existing[1] or ""
        page_cache[candidate.url] = (page_id, page_type_code)
        if candidate.page_type_code == "BINARY" and page_type_code == "FRONTIER":
            cur.execute(
                """
                UPDATE crawldb.page
                SET page_type_code = 'BINARY',
                    html_content = NULL,
                    content_hash = NULL,
                    duplicate_of_page_id = NULL
                WHERE id = %s AND page_type_code = 'FRONTIER'
                """,
                (page_id,),
            )
            page_cache[candidate.url] = (page_id, "BINARY")
            stats.page_rows_promoted_to_binary += 1
            return page_id, "BINARY"

        return page_id, page_type_code


def insert_links(
    conn: psycopg2.extensions.connection,
    source_page_id: int,
    target_ids: list[int],
    stats: ReparseStats,
    dry_run: bool,
) -> None:
    if not target_ids:
        return

    unique_ids = sorted(set(target_ids))
    stats.link_edges_attempted += len(unique_ids)
    if dry_run:
        return

    rows = [(source_page_id, target_id) for target_id in unique_ids]
    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO crawldb.link(from_page, to_page) VALUES %s ON CONFLICT DO NOTHING",
            rows,
            page_size=500,
        )


def upsert_frontier_rows(
    conn: psycopg2.extensions.connection,
    source_url: str,
    frontier_urls: list[str],
    stats: ReparseStats,
    dry_run: bool,
) -> None:
    if not frontier_urls:
        return

    unique_urls = sorted(set(frontier_urls))
    stats.frontier_rows_attempted += len(unique_urls)
    if dry_run:
        return

    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    rows = [(url, 0, source_url, 1, now) for url in unique_urls]

    sql = """
        INSERT INTO crawldb.frontier_queue(url, priority, source_url, depth, state, discovered_at)
        VALUES %s
        ON CONFLICT (url) DO UPDATE SET
            priority = GREATEST(crawldb.frontier_queue.priority, EXCLUDED.priority),
            source_url = COALESCE(crawldb.frontier_queue.source_url, EXCLUDED.source_url),
            depth = LEAST(crawldb.frontier_queue.depth, EXCLUDED.depth),
            discovered_at = LEAST(crawldb.frontier_queue.discovered_at, EXCLUDED.discovered_at),
            state = CASE
                WHEN crawldb.frontier_queue.state IN (
                    'COMPLETED'::crawldb.frontier_queue_state,
                    'FAILED'::crawldb.frontier_queue_state,
                    'DUPLICATE'::crawldb.frontier_queue_state
                ) THEN crawldb.frontier_queue.state
                ELSE 'QUEUED'::crawldb.frontier_queue_state
            END
    """

    with conn.cursor() as cur:
        execute_values(
            cur,
            sql,
            rows,
            template="(%s, %s, %s, %s, 'QUEUED'::crawldb.frontier_queue_state, %s)",
            page_size=300,
        )


def upsert_doc_page_data(
    conn: psycopg2.extensions.connection,
    doc_rows: list[tuple[int, str]],
    stats: ReparseStats,
    dry_run: bool,
) -> None:
    if not doc_rows:
        return

    unique_doc_rows = sorted(set(doc_rows))
    if dry_run:
        stats.page_data_rows_inserted += len(unique_doc_rows)
        return

    sql = """
        WITH incoming(page_id, data_type_code) AS (VALUES %s)
        INSERT INTO crawldb.page_data(page_id, data_type_code, data)
        SELECT i.page_id, i.data_type_code, NULL
        FROM incoming i
        WHERE NOT EXISTS (
            SELECT 1
            FROM crawldb.page_data pd
            WHERE pd.page_id = i.page_id
              AND pd.data_type_code = i.data_type_code
        )
    """

    with conn.cursor() as cur:
        execute_values(cur, sql, unique_doc_rows, page_size=300)
        stats.page_data_rows_inserted += max(0, cur.rowcount)


def process_page(
    conn: psycopg2.extensions.connection,
    site_cache: dict[str, int],
    page_cache: dict[str, tuple[int, str]],
    page: HtmlPageRow,
    canonicalizer: DefaultUrlCanonicalizer,
    stats: ReparseStats,
    dry_run: bool,
) -> None:
    stats.html_pages_scanned += 1
    candidates = extract_candidates(page, canonicalizer, stats)
    if not candidates:
        return

    target_ids: list[int] = []
    frontier_urls: list[str] = []
    doc_rows: list[tuple[int, str]] = []

    for candidate in candidates.values():
        page_id, resolved_page_type = ensure_page_for_candidate(
            conn,
            site_cache,
            page_cache,
            candidate,
            stats,
            dry_run,
        )
        if page_id is None:
            continue

        target_ids.append(page_id)

        if resolved_page_type == "FRONTIER":
            frontier_urls.append(candidate.url)

        if candidate.doc_type_code:
            doc_rows.append((page_id, candidate.doc_type_code))

    insert_links(conn, page.page_id, target_ids, stats, dry_run)
    upsert_frontier_rows(conn, page.url, frontier_urls, stats, dry_run)
    upsert_doc_page_data(conn, doc_rows, stats, dry_run)


def print_summary(stats: ReparseStats, dry_run: bool) -> None:
    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"Re-parse summary ({mode})")
    print(f"  HTML pages scanned: {stats.html_pages_scanned}")
    print(f"  Parse errors: {stats.parse_errors}")
    print(f"  Extracted links: {stats.extracted_links}")
    print(f"  Extracted images: {stats.extracted_images}")
    print(f"  Unique candidates: {stats.unique_candidates}")
    print(f"  Page rows inserted: {stats.page_rows_inserted}")
    print(f"  FRONTIER->BINARY promotions: {stats.page_rows_promoted_to_binary}")
    print(f"  Link edges attempted: {stats.link_edges_attempted}")
    print(f"  Frontier rows attempted: {stats.frontier_rows_attempted}")
    print(f"  page_data rows inserted (doc types): {stats.page_data_rows_inserted}")


def main() -> int:
    args = parse_args()
    conn = db_connect()
    conn.autocommit = False

    stats = ReparseStats()
    canonicalizer = DefaultUrlCanonicalizer()

    try:
        ensure_doc_data_types(conn)
        pages = fetch_html_pages(conn, args.limit)
        page_cache = load_existing_pages(conn)
        site_cache = load_site_cache(conn)

        if args.dry_run:
            conn.rollback()

        for idx, page in enumerate(pages, start=1):
            process_page(
                conn,
                site_cache,
                page_cache,
                page,
                canonicalizer,
                stats,
                args.dry_run,
            )

            if not args.dry_run and idx % max(1, args.commit_every) == 0:
                conn.commit()

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()

        print_summary(stats, args.dry_run)
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
