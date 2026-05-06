r"""Fill crawldb.page.cleaned_content for article pages only (PA2 extraction step).

This script is intentionally narrow in scope:
- reads stored HTML pages from crawldb.page,
- runs article extraction,
- writes cleaned_content (+ cleaned_content_hash) back to crawldb.page.

It does NOT create segments, embeddings, or retrieval artifacts.

Usage (repo root):
  python ./pa2/crawler/src/fill_cleaned_content.py --limit 100 --dry-run
  python ./pa2/crawler/src/fill_cleaned_content.py --limit 500 --commit-every 25
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from typing import Callable, Any

import psycopg2

SRC_DIR = os.path.abspath(os.path.dirname(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core.article_extractor import extract_medover_article as extract_medover_article_bs4  # noqa: E402

ExtractorFn = Callable[[str, str], Any]


@dataclass(frozen=True)
class ScriptStats:
    scanned_pages: int = 0
    article_detected: int = 0
    updated_pages: int = 0
    skipped_non_article: int = 0
    skipped_empty_cleaned_content: int = 0
    skipped_already_filled: int = 0
    failed_extraction: int = 0
    failed_db_updates: int = 0


@dataclass(frozen=True)
class HtmlPageRow:
    page_id: int
    url: str
    html_content: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fill page.cleaned_content for extracted articles")
    parser.add_argument("--host", default=os.getenv("PGHOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PGPORT", "5432")))
    parser.add_argument("--db", default=os.getenv("PGDATABASE", "postgres"))
    parser.add_argument("--user", default=os.getenv("PGUSER", "postgres"))
    parser.add_argument("--password", default=os.getenv("PGPASSWORD", "postgres"))

    parser.add_argument("--limit", type=int, default=200, help="Maximum number of pages to scan")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Also process pages that already have cleaned_content",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run extraction and stats only, without DB writes",
    )
    parser.add_argument(
        "--commit-every",
        type=int,
        default=25,
        help="Commit interval for DB updates",
    )
    parser.add_argument(
        "--extractor",
        choices=("bs4", "xpath"),
        default="bs4",
        help="Extractor backend: bs4 (default) or xpath",
    )
    parser.add_argument(
        "--verbose-failures",
        action="store_true",
        help="Print per-page extraction/update failures",
    )
    return parser


def resolve_extractor(name: str) -> ExtractorFn:
    if name == "xpath":
        # Lazy import: xpath backend requires optional parser deps (e.g. html5lib).
        from core.article_extractor_xpath import extract_medover_article as extract_medover_article_xpath  # noqa: E402

        return extract_medover_article_xpath
    return extract_medover_article_bs4


def fetch_html_pages(
    conn: psycopg2.extensions.connection,
    *,
    limit: int,
    force: bool,
) -> list[HtmlPageRow]:
    where = ""
    if not force:
        where = "AND (cleaned_content IS NULL OR length(cleaned_content) = 0)"

    sql = f"""
        SELECT id, url, html_content
        FROM crawldb.page
        WHERE page_type_code = 'HTML'
          AND url IS NOT NULL
          AND html_content IS NOT NULL
          AND length(html_content) > 0
          {where}
        ORDER BY id
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
    return [HtmlPageRow(page_id=row[0], url=row[1], html_content=row[2]) for row in rows]


def upsert_cleaned_content(
    conn: psycopg2.extensions.connection,
    *,
    page_id: int,
    cleaned_content: str,
    cleaned_hash: str | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE crawldb.page
            SET cleaned_content = %s,
                cleaned_content_hash = %s
            WHERE id = %s
            """,
            (cleaned_content, cleaned_hash, page_id),
        )


def sha256_hex(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def main() -> int:
    args = build_parser().parse_args()
    extractor = resolve_extractor(args.extractor)

    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        dbname=args.db,
        user=args.user,
        password=args.password,
    )
    conn.autocommit = False

    stats = ScriptStats()
    pending_writes = 0

    try:
        pages = fetch_html_pages(conn, limit=args.limit, force=args.force)
        scanned = 0
        article_detected = 0
        updated = 0
        skipped_non_article = 0
        skipped_empty = 0
        skipped_filled = 0
        failed_extraction = 0
        failed_db_updates = 0

        for page in pages:
            scanned += 1

            # Extra safety guard if helper behavior changes.
            if not args.force and not _should_process_current_cleaned_value(conn, page.page_id):
                skipped_filled += 1
                continue

            try:
                result = extractor(page.url, page.html_content)
            except Exception as exc:
                failed_extraction += 1
                if args.verbose_failures:
                    print(f"[extract-failed] page_id={page.page_id} url={page.url} error={exc}")
                continue

            if not result.is_article:
                skipped_non_article += 1
                continue

            article_detected += 1
            cleaned = (result.cleaned_content or "").strip()
            if not cleaned:
                skipped_empty += 1
                continue

            if args.dry_run:
                updated += 1
                continue

            try:
                upsert_cleaned_content(
                    conn,
                    page_id=page.page_id,
                    cleaned_content=cleaned,
                    cleaned_hash=sha256_hex(cleaned),
                )
                pending_writes += 1
                updated += 1
            except Exception as exc:
                failed_db_updates += 1
                conn.rollback()
                pending_writes = 0
                if args.verbose_failures:
                    print(f"[update-failed] page_id={page.page_id} url={page.url} error={exc}")
                continue

            if pending_writes >= args.commit_every:
                conn.commit()
                pending_writes = 0

        if not args.dry_run and pending_writes > 0:
            conn.commit()

        stats = ScriptStats(
            scanned_pages=scanned,
            article_detected=article_detected,
            updated_pages=updated,
            skipped_non_article=skipped_non_article,
            skipped_empty_cleaned_content=skipped_empty,
            skipped_already_filled=skipped_filled,
            failed_extraction=failed_extraction,
            failed_db_updates=failed_db_updates,
        )

    finally:
        conn.close()

    print(
        json.dumps(
            {
                "script": "fill_cleaned_content",
                "extractor": args.extractor,
                "dry_run": args.dry_run,
                "limit": args.limit,
                "force": args.force,
                "stats": stats.__dict__,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _should_process_current_cleaned_value(conn: psycopg2.extensions.connection, page_id: int) -> bool:
    """Return True when cleaned_content is NULL/empty for this page.

    Kept as a secondary safety check (main filter is already in fetch_html_pages with force=False).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cleaned_content
            FROM crawldb.page
            WHERE id = %s
            """,
            (page_id,),
        )
        row = cur.fetchone()
    if not row:
        return False
    value = row[0]
    return value is None or len(str(value)) == 0


if __name__ == "__main__":
    raise SystemExit(main())
