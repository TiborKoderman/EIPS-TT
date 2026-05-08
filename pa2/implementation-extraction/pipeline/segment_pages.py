r"""Extract cleaned text + build segments from stored HTML pages and persist to Postgres.

This script connects to the existing crawldb schema, fills:
- crawldb.page.cleaned_content (+ helper columns)
- crawldb.page_segment_short / crawldb.page_segment_long

It is meant as the missing step between PA1 (stored HTML) and PA2 (embeddings).

Usage example (from repo root):
  python ./pa2/crawler/src/segment_pages_to_db.py --limit 50

Connection parameters default to the docker-compose defaults but can be overridden
via env vars or CLI args.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Literal

import psycopg2
from psycopg2.extras import execute_values

SRC_DIR = os.path.abspath(os.path.dirname(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from extractor_xpath import extract_medover_article  # noqa: E402
from forum_extractor import extract_forum_thread  # noqa: E402
from segmenter import build_forum_long_chunks, build_long_word_chunks, build_short_char_chunks  # noqa: E402


@dataclass(frozen=True)
class HtmlPageRow:
    page_id: int
    url: str
    html_content: str


PageType = Literal["article", "forum", "listing", "unknown"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract cleaned text + segments into crawldb")
    parser.add_argument("--host", default=os.getenv("PGHOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PGPORT", "5432")))
    parser.add_argument("--db", default=os.getenv("PGDATABASE", "crawldb"))
    parser.add_argument("--user", default=os.getenv("PGUSER", "postgres"))
    parser.add_argument("--password", default=os.getenv("PGPASSWORD", "postgres"))

    parser.add_argument("--limit", type=int, default=100, help="How many pages to process")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute cleaned_content/segments even if cleaned_content already exists",
    )
    parser.add_argument(
        "--embedding-model",
        default="default",
        help=(
            "Value stored into page_segment_*.embedding_model. "
            "You can set this to the model you plan to embed with later "
            "(e.g. sentence-transformers/all-MiniLM-L6-v2)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write anything to DB; just print stats.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        dbname=args.db,
        user=args.user,
        password=args.password,
    )
    conn.autocommit = False

    processed = 0
    updated_pages = 0
    inserted_short = 0
    inserted_long = 0

    try:
        pages = fetch_html_pages(conn, limit=args.limit, force=args.force)
        for page in pages:
            processed += 1
            result = extract_page_content(page.url, page.html_content)
            page_type = get_page_type(page.url, result)

            cleaned = result.cleaned_content or ""
            cleaned_hash = sha256_hex(cleaned) if cleaned else None

            short_chunks = build_short_char_chunks(
                cleaned,
                chunk_chars=50,
                step_chars=50,
                min_chars=1,
            )
            if page_type == "forum":
                long_chunks = build_forum_long_chunks(
                    build_forum_post_blocks(result),
                    words_per_chunk=220,
                    overlap_words=30,
                    min_words=1,
                )
            else:
                long_chunks = build_long_word_chunks(
                    cleaned,
                    words_per_chunk=250,
                    overlap_words=50,
                    min_words=1,
                )

            if args.dry_run:
                updated_pages += 1
                inserted_short += len(short_chunks)
                inserted_long += len(long_chunks)
                continue

            upsert_cleaned_content(
                conn,
                page_id=page.page_id,
                cleaned_content=cleaned,
                cleaned_hash=cleaned_hash,
            )

            # Replace segments for this page+model (idempotent behavior)
            delete_existing_segments(conn, page.page_id, args.embedding_model)
            inserted_short += insert_segments(
                conn,
                table="crawldb.page_segment_short",
                page_id=page.page_id,
                page_type=page_type,
                chunks=short_chunks,
                embedding_model=args.embedding_model,
                extraction_result=result,
            )
            inserted_long += insert_segments(
                conn,
                table="crawldb.page_segment_long",
                page_id=page.page_id,
                page_type=page_type,
                chunks=long_chunks,
                embedding_model=args.embedding_model,
                extraction_result=result,
            )

            updated_pages += 1

            if processed % 25 == 0:
                conn.commit()

        if not args.dry_run:
            conn.commit()

    finally:
        conn.close()

    print(
        json.dumps(
            {
                "processed": processed,
                "updated_pages": updated_pages,
                "inserted_short_segments": inserted_short,
                "inserted_long_segments": inserted_long,
                "dry_run": args.dry_run,
            },
            indent=2,
        )
    )

    return 0


def fetch_html_pages(conn: psycopg2.extensions.connection, *, limit: int, force: bool) -> list[HtmlPageRow]:
    where = ""
    if not force:
        where = "AND (cleaned_content IS NULL OR length(cleaned_content) = 0)"

    limit_clause = "" if limit == 0 else f"LIMIT {int(limit)}"

    sql = f"""
        SELECT id, url, html_content
        FROM crawldb.page
        WHERE page_type_code = 'HTML'
          AND url IS NOT NULL
          AND html_content IS NOT NULL
          AND length(html_content) > 0
          {where}
        ORDER BY id
        {limit_clause}
    """

    with conn.cursor() as cur:
        cur.execute(sql)
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
            (
                cleaned_content,
                cleaned_hash,
                page_id,
            ),
        )


def delete_existing_segments(conn: psycopg2.extensions.connection, page_id: int, embedding_model: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM crawldb.page_segment_short WHERE page_id = %s AND embedding_model = %s",
            (page_id, embedding_model),
        )
        cur.execute(
            "DELETE FROM crawldb.page_segment_long WHERE page_id = %s AND embedding_model = %s",
            (page_id, embedding_model),
        )


def insert_segments(
    conn: psycopg2.extensions.connection,
    *,
    table: str,
    page_id: int,
    page_type: PageType,
    chunks: list[Any],
    embedding_model: str,
    extraction_result: Any = None,
) -> int:
    if not chunks:
        return 0

    heading_val = extraction_result.title if extraction_result else None
    author_val = extraction_result.author if extraction_result else None
    published_time_val = extraction_result.published_at if extraction_result and hasattr(extraction_result, 'published_at') else None

    rows = []
    for chunk in chunks:
        meta_dict = {
            "char_count": chunk.char_count,
            "token_count": chunk.token_count,
            "page_type": page_type,
        }
        if author_val:
            meta_dict["author"] = author_val
        if published_time_val:
            meta_dict["published_time"] = published_time_val
        post_count = len(getattr(extraction_result, "posts", []) or [])
        if post_count > 0:
            meta_dict["post_count"] = post_count

        rows.append((
            page_id,
            page_type,
            chunk.index,
            chunk.text,
            None,  # html_tag
            None,  # section_title
            heading_val,  # heading
            None,  # heading_level
            None,  # embedding
            embedding_model,
            None,  # embedded_at
            json.dumps(meta_dict, ensure_ascii=False),
        ))

    sql = f"""
        INSERT INTO {table}(
            page_id,
            page_type,
            segment_index,
            segment_text,
            html_tag,
            section_title,
            heading,
            heading_level,
            embedding,
            embedding_model,
            embedded_at,
            metadata
        )
        VALUES %s
        ON CONFLICT (page_id, segment_index, embedding_model) DO UPDATE
        SET segment_text = EXCLUDED.segment_text,
            page_type = EXCLUDED.page_type,
            metadata = EXCLUDED.metadata,
            created_at = now()
    """

    with conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=500)
        return len(rows)


def sha256_hex(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def extract_page_content(url: str, html_content: str) -> Any:
    if is_forum_thread_url(url):
        return extract_forum_thread(url, html_content)
    return extract_medover_article(url=url, html=html_content)


def is_forum_thread_url(url: str) -> bool:
    return "/forum/tema/" in (url or "").lower()


def build_forum_post_blocks(extraction_result: Any) -> list[str]:
    posts = getattr(extraction_result, "posts", []) or []
    blocks: list[str] = []
    for index, post in enumerate(posts):
        lines: list[str] = []
        label = "Opening post" if index == 0 else f"Reply {index}"
        header_parts = [label]
        if getattr(post, "author", None):
            header_parts.append(post.author)
        if getattr(post, "published_at", None):
            header_parts.append(post.published_at)
        lines.append(" | ".join(header_parts))

        lines.extend(getattr(post, "body_paragraphs", []) or [])
        block = "\n\n".join(line for line in lines if line).strip()
        if block:
            blocks.append(block)

    return blocks


def get_page_type(url: str, extraction_result: Any) -> PageType:
    """Determine page type based on URL and extraction results."""
    url_lower = (url or "").lower()
    if is_forum_thread_url(url_lower):
        return "forum"
    if getattr(extraction_result, "is_article", False):
        return "article"
    if "/forum/" in url_lower and any(
        token in url_lower for token in ("/forum/kategorija/", "/forum/page/", "/forum/tag/", "/forum/search")
    ):
        return "listing"
    if "/kategorija/" in url_lower or "/iskanje/" in url_lower or "/tag/" in url_lower or "/search" in url_lower:
        return "listing"
    return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
