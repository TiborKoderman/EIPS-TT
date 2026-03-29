#!/usr/bin/env python3
"""Backfill missing document data_type_code rows for existing BINARY pages.

This script classifies existing crawldb.page BINARY URLs into
PDF/DOC/DOCX/PPT/PPTX using URL/query filename hints and optional
HEAD/GET response headers, then inserts missing crawldb.page_data rows
with data_type_code and NULL payload.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from typing import Iterable, Mapping
from urllib.parse import unquote, urlparse

import psycopg2
import requests

DOC_TYPES = ("PDF", "DOC", "DOCX", "PPT", "PPTX")
LIKELY_DOC_HINTS = (
    "pdf",
    "doc",
    "docx",
    "ppt",
    "pptx",
    "download",
    "attachment",
    "file",
    "office",
)
EXT_TO_TYPE = {
    ".pdf": "PDF",
    ".docx": "DOCX",
    ".pptx": "PPTX",
    ".doc": "DOC",
    ".ppt": "PPT",
}
CONTENT_TYPE_TO_DOC = {
    "application/pdf": "PDF",
    "application/msword": "DOC",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
    "application/vnd.ms-powerpoint": "PPT",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPTX",
}


@dataclass(frozen=True)
class BinaryPage:
    page_id: int
    url: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill doc type metadata for existing BINARY rows.")
    parser.add_argument("--head-check", action="store_true", help="Use HTTP HEAD/GET fallback for unresolved URLs.")
    parser.add_argument("--head-limit", type=int, default=500, help="Maximum unresolved URLs to probe over HTTP.")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout seconds for header checks.")
    return parser.parse_args()


def db_connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ.get("DB_NAME", "crawldb"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "postgres"),
    )


def normalize_candidate(value: str) -> str:
    trimmed = value.strip().strip('"\'()[]{}<>;')
    trimmed = trimmed.split("#", 1)[0].split("?", 1)[0]
    return trimmed


def extract_candidates(url: str) -> list[str]:
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
            if not decoded:
                continue
            candidates.append(decoded)
            candidates.append(decoded.rsplit("/", 1)[-1])

    return candidates


def infer_doc_type_from_url(url: str) -> str | None:
    for candidate in extract_candidates(url):
        normalized = normalize_candidate(candidate).lower()
        if not normalized:
            continue
        for ext, dtype in sorted(EXT_TO_TYPE.items(), key=lambda item: len(item[0]), reverse=True):
            if normalized.endswith(ext):
                return dtype
    return None


def extract_filename_from_disposition(content_disposition: str) -> str | None:
    if not content_disposition:
        return None

    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", content_disposition, re.IGNORECASE)
    if not match:
        return None

    return unquote(match.group(1).strip().strip("\"'"))


def infer_doc_type_from_headers(url: str, timeout: float) -> str | None:
    session = requests.Session()

    def infer_from_response_headers(headers: Mapping[str, str]) -> str | None:
        content_type = headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type in CONTENT_TYPE_TO_DOC:
            return CONTENT_TYPE_TO_DOC[content_type]

        disposition = headers.get("Content-Disposition", "")
        filename = extract_filename_from_disposition(disposition)
        if filename:
            for ext, dtype in sorted(EXT_TO_TYPE.items(), key=lambda item: len(item[0]), reverse=True):
                if filename.lower().endswith(ext):
                    return dtype
        return None

    try:
        response = session.head(url, allow_redirects=True, timeout=timeout)
        inferred = infer_from_response_headers(response.headers)
        if inferred:
            return inferred
    except requests.RequestException:
        pass

    try:
        response = session.get(url, allow_redirects=True, timeout=timeout, stream=True)
        inferred = infer_from_response_headers(response.headers)
        response.close()
        if inferred:
            return inferred
    except requests.RequestException:
        return None

    return None


def is_likely_doc_url(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in LIKELY_DOC_HINTS)


def load_binary_pages_without_doc_type(conn: psycopg2.extensions.connection) -> list[BinaryPage]:
    sql = """
        SELECT p.id, p.url
        FROM crawldb.page p
        WHERE p.page_type_code = 'BINARY'
          AND p.url IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM crawldb.page_data pd
              WHERE pd.page_id = p.id
                AND pd.data_type_code IN ('PDF', 'DOC', 'DOCX', 'PPT', 'PPTX')
          )
        ORDER BY p.id;
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return [BinaryPage(page_id=row[0], url=row[1]) for row in rows if row[1]]


def ensure_doc_data_types(conn: psycopg2.extensions.connection) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO crawldb.data_type(code) VALUES (%s) ON CONFLICT (code) DO NOTHING",
            [(dtype,) for dtype in DOC_TYPES],
        )


def insert_missing_page_data(
    conn: psycopg2.extensions.connection,
    inferred_by_page_id: dict[int, str],
) -> int:
    inserted = 0
    sql = """
        INSERT INTO crawldb.page_data(page_id, data_type_code, data)
        SELECT %s, %s, NULL
        WHERE NOT EXISTS (
            SELECT 1
            FROM crawldb.page_data
            WHERE page_id = %s AND data_type_code = %s
        );
    """

    with conn.cursor() as cur:
        for page_id, data_type_code in inferred_by_page_id.items():
            cur.execute(sql, (page_id, data_type_code, page_id, data_type_code))
            inserted += cur.rowcount

    return inserted


def summarize(
    inferred_by_page_id: dict[int, str],
    resolved_by_head: int,
    inserted: int,
    total_candidates: int,
    probed_over_http: int,
    likely_doc_candidates: int,
) -> None:
    by_type: dict[str, int] = {key: 0 for key in DOC_TYPES}
    for dtype in inferred_by_page_id.values():
        by_type[dtype] = by_type.get(dtype, 0) + 1

    print("Backfill summary")
    print(f"  Binary pages without doc type row: {total_candidates}")
    print(f"  Resolved as document type: {len(inferred_by_page_id)}")
    print(f"  Likely document URL candidates: {likely_doc_candidates}")
    print(f"  Probed over HTTP headers: {probed_over_http}")
    print(f"  Resolved via HTTP headers: {resolved_by_head}")
    print(f"  Inserted new page_data rows: {inserted}")
    print("  Breakdown:")
    for dtype in DOC_TYPES:
        print(f"    {dtype}: {by_type.get(dtype, 0)}")


def main() -> int:
    args = parse_args()

    conn = db_connect()
    conn.autocommit = False

    try:
        ensure_doc_data_types(conn)
        candidates = load_binary_pages_without_doc_type(conn)

        inferred_by_page_id: dict[int, str] = {}
        unresolved: list[BinaryPage] = []

        for page in candidates:
            inferred = infer_doc_type_from_url(page.url)
            if inferred:
                inferred_by_page_id[page.page_id] = inferred
            else:
                unresolved.append(page)

        resolved_by_head = 0
        probed_over_http = 0
        likely_doc_candidates = 0
        if args.head_check and unresolved:
            likely_pages = [page for page in unresolved if is_likely_doc_url(page.url)]
            likely_doc_candidates = len(likely_pages)
            for page in likely_pages[: max(0, args.head_limit)]:
                probed_over_http += 1
                inferred = infer_doc_type_from_headers(page.url, timeout=args.timeout)
                if inferred:
                    inferred_by_page_id[page.page_id] = inferred
                    resolved_by_head += 1

        inserted = insert_missing_page_data(conn, inferred_by_page_id)
        conn.commit()

        summarize(
            inferred_by_page_id=inferred_by_page_id,
            resolved_by_head=resolved_by_head,
            inserted=inserted,
            total_candidates=len(candidates),
            probed_over_http=probed_over_http,
            likely_doc_candidates=likely_doc_candidates,
        )

        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
