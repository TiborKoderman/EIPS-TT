#!/usr/bin/env python3
"""Minimal entrypoint for utility smoke tests."""

from __future__ import annotations

import argparse

from db.page_store import PostgresPageStore
from db.pg_connect import get_connection, load_db_config
from utils.url_canonicalizer import DefaultUrlCanonicalizer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PA1 utility smoke entrypoint (canonicalization + dedup workflow)."
    )
    parser.add_argument(
        "--canonicalize",
        dest="url_to_canonicalize",
        help="Print canonical form of this URL and exit.",
    )
    parser.add_argument(
        "--ingest-demo",
        action="store_true",
        help="Insert/demo one HTML page in crawldb.page using dedup workflow.",
    )
    parser.add_argument(
        "--url",
        default="https://example.com/index.html?utm_source=test#fragment",
        help="URL for --ingest-demo.",
    )
    parser.add_argument(
        "--html",
        default="<html><body>hello</body></html>",
        help="HTML payload for --ingest-demo.",
    )
    parser.add_argument(
        "--site-id",
        type=int,
        default=None,
        help="Optional crawldb.site.id for --ingest-demo.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    canonicalizer = DefaultUrlCanonicalizer()

    if args.url_to_canonicalize:
        print(canonicalizer.canonicalize(args.url_to_canonicalize))
        return 0

    if args.ingest_demo:
        cfg = load_db_config()
        with get_connection(cfg) as conn:
            store = PostgresPageStore(conn)
            result = store.ingest_html_from_frontier(
                raw_url=args.url,
                html_content=args.html,
                site_id=args.site_id,
            )
        print(
            f"status={result.status} page_id={result.page_id} "
            f"canonical_url={result.canonical_url} duplicate_of={result.duplicate_of_page_id}"
        )
        return 0

    print("No action selected. Use --canonicalize <URL> or --ingest-demo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
