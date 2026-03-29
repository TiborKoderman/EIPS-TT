#!/usr/bin/env python3
"""Export document-link candidates from stored HTML pages for debugging.

This script parses crawldb.page rows where page_type_code='HTML', extracts outgoing
links/images, keeps URLs that look like document binaries (PDF/DOC/DOCX/PPT/PPTX),
and writes a CSV report including the source HTML page and current target status.

The script is read-only: it does not insert/update/delete anything in the DB.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

import psycopg2

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
@dataclass(frozen=True)
class HtmlPageRow:
    page_id: int
    url: str
    html_content: str


@dataclass(frozen=True)
class CandidateRow:
    source_page_id: int
    source_page_url: str
    candidate_url: str
    inferred_doc_type: str
    discovered_in: str  # link | image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export doc-link candidates from stored HTML pages.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of HTML pages scanned (0 = all).")
    parser.add_argument(
        "--output",
        default=os.path.join("scripts", "debug", "stored-html-doc-link-candidates.csv"),
        help="Output CSV path.",
    )
    parser.add_argument(
        "--only-unfetched",
        action="store_true",
        help="Write only candidates whose target has no observed fetch result yet.",
    )
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
    params: tuple[Any, ...] = ()
    if limit > 0:
        sql += " LIMIT %s"
        params = (limit,)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [HtmlPageRow(page_id=row[0], url=row[1], html_content=row[2]) for row in rows]


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


def extract_doc_candidates(
    pages: list[HtmlPageRow],
) -> tuple[list[CandidateRow], int, int, int]:
    canonicalizer = DefaultUrlCanonicalizer()
    parse_errors = 0
    extracted_links = 0
    extracted_images = 0
    candidates: list[CandidateRow] = []
    seen: set[tuple[int, str]] = set()

    for page in pages:
        try:
            parsed = parse_outgoing_urls(
                page.html_content,
                page_url=page.url,
                canonicalizer=canonicalizer,
            )
        except Exception:
            parse_errors += 1
            continue

        extracted_links += len(parsed.links)
        extracted_images += len(parsed.images)

        for link in parsed.links:
            if link == page.url:
                continue
            doc_type = infer_doc_type_from_url(link)
            if not doc_type:
                continue
            key = (page.page_id, link)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                CandidateRow(
                    source_page_id=page.page_id,
                    source_page_url=page.url,
                    candidate_url=link,
                    inferred_doc_type=doc_type,
                    discovered_in="link",
                )
            )

        for image in parsed.images:
            if image == page.url:
                continue
            doc_type = infer_doc_type_from_url(image)
            if not doc_type:
                continue
            key = (page.page_id, image)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                CandidateRow(
                    source_page_id=page.page_id,
                    source_page_url=page.url,
                    candidate_url=image,
                    inferred_doc_type=doc_type,
                    discovered_in="image",
                )
            )

    return candidates, parse_errors, extracted_links, extracted_images


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def load_target_page_map(
    conn: psycopg2.extensions.connection,
    urls: list[str],
) -> dict[str, tuple[int, str, int | None, str | None]]:
    if not urls:
        return {}

    result: dict[str, tuple[int, str, int | None, str | None]] = {}
    sql = """
        SELECT id, url, page_type_code, http_status_code, accessed_time::text
        FROM crawldb.page
        WHERE url = ANY(%s)
    """

    with conn.cursor() as cur:
        for batch in chunked(urls, 5000):
            cur.execute(sql, (batch,))
            for row in cur.fetchall():
                page_id = row[0]
                url = row[1]
                page_type = row[2] or ""
                http_status = row[3]
                accessed_time = row[4]
                result[url] = (page_id, page_type, http_status, accessed_time)

    return result


def load_frontier_state_map(
    conn: psycopg2.extensions.connection,
    urls: list[str],
) -> dict[str, str]:
    if not urls:
        return {}

    result: dict[str, str] = {}
    sql = """
        SELECT url, state::text
        FROM crawldb.frontier_queue
        WHERE url = ANY(%s)
    """

    with conn.cursor() as cur:
        for batch in chunked(urls, 5000):
            cur.execute(sql, (batch,))
            for row in cur.fetchall():
                result[row[0]] = row[1]

    return result


def write_csv(
    output_path: str,
    candidates: list[CandidateRow],
    target_pages: dict[str, tuple[int, str, int | None, str | None]],
    frontier_states: dict[str, str],
    only_unfetched: bool,
) -> tuple[int, int]:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    written = 0
    unfetched = 0
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "source_page_id",
                "source_page_url",
                "candidate_url",
                "inferred_doc_type",
                "discovered_in",
                "target_page_id",
                "target_page_type",
                "target_http_status",
                "target_accessed_time",
                "frontier_state",
                "has_page_row",
                "has_fetch_result",
            ]
        )

        for candidate in candidates:
            target = target_pages.get(candidate.candidate_url)
            frontier_state = frontier_states.get(candidate.candidate_url, "")
            target_page_id = target[0] if target else ""
            target_page_type = target[1] if target else ""
            target_http_status = target[2] if target else ""
            target_accessed_time = target[3] if target else ""
            has_page_row = target is not None
            has_fetch_result = target is not None and (
                target[2] is not None or bool(target[3])
            )

            if only_unfetched and has_fetch_result:
                continue

            if not has_fetch_result:
                unfetched += 1

            writer.writerow(
                [
                    candidate.source_page_id,
                    candidate.source_page_url,
                    candidate.candidate_url,
                    candidate.inferred_doc_type,
                    candidate.discovered_in,
                    target_page_id,
                    target_page_type,
                    target_http_status,
                    target_accessed_time,
                    frontier_state,
                    str(has_page_row).lower(),
                    str(has_fetch_result).lower(),
                ]
            )
            written += 1

    return written, unfetched


def main() -> int:
    args = parse_args()

    conn = db_connect()
    conn.autocommit = True
    try:
        pages = fetch_html_pages(conn, args.limit)
        candidates, parse_errors, extracted_links, extracted_images = extract_doc_candidates(pages)

        unique_target_urls = sorted({candidate.candidate_url for candidate in candidates})
        target_pages = load_target_page_map(conn, unique_target_urls)
        frontier_states = load_frontier_state_map(conn, unique_target_urls)

        output_path = os.path.abspath(args.output)
        written, unfetched = write_csv(
            output_path=output_path,
            candidates=candidates,
            target_pages=target_pages,
            frontier_states=frontier_states,
            only_unfetched=args.only_unfetched,
        )

        print("Stored HTML document-link export summary")
        print(f"  HTML pages scanned: {len(pages)}")
        print(f"  Parse errors: {parse_errors}")
        print(f"  Extracted links total: {extracted_links}")
        print(f"  Extracted images total: {extracted_images}")
        print(f"  Document candidates found: {len(candidates)}")
        print(f"  Unique candidate URLs: {len(unique_target_urls)}")
        print(f"  Output rows written: {written}")
        print(f"  Output rows currently unfetched: {unfetched}")
        print(f"  Output CSV: {output_path}")
        print(f"  Doc types tracked: {', '.join(DOC_TYPES)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())