"""Build article-to-article link graph from crawldb.link (PA2 bonus).

Creates crawldb.article_link_graph — a filtered view of the raw link table
containing only links between pages that have non-empty cleaned_content
(i.e., actual articles, not forum/category pages).

This enables knowledge-graph-style context expansion in retrieval:
given a top-k result, fetch its direct neighbors for related-topic hints.

Usage:
  python pa2/crawler/src/build_link_graph.py
  python pa2/crawler/src/build_link_graph.py --stats
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg2

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS crawldb.article_link_graph (
    from_page integer NOT NULL REFERENCES crawldb.page(id) ON DELETE CASCADE,
    to_page   integer NOT NULL REFERENCES crawldb.page(id) ON DELETE CASCADE,
    PRIMARY KEY (from_page, to_page)
);

CREATE INDEX IF NOT EXISTS idx_alg_from ON crawldb.article_link_graph (from_page);
CREATE INDEX IF NOT EXISTS idx_alg_to   ON crawldb.article_link_graph (to_page);
"""

_POPULATE = """
INSERT INTO crawldb.article_link_graph (from_page, to_page)
SELECT DISTINCT l.from_page, l.to_page
FROM crawldb.link l
JOIN crawldb.page fp ON fp.id = l.from_page
    AND fp.cleaned_content IS NOT NULL
    AND length(fp.cleaned_content) > 0
JOIN crawldb.page tp ON tp.id = l.to_page
    AND tp.cleaned_content IS NOT NULL
    AND length(tp.cleaned_content) > 0
WHERE l.from_page <> l.to_page
ON CONFLICT DO NOTHING;
"""

_STATS = """
SELECT
    (SELECT COUNT(*) FROM crawldb.article_link_graph)          AS total_edges,
    (SELECT COUNT(DISTINCT from_page) FROM crawldb.article_link_graph) AS source_articles,
    (SELECT COUNT(DISTINCT to_page)   FROM crawldb.article_link_graph) AS target_articles,
    (SELECT COUNT(*) FROM crawldb.page
     WHERE cleaned_content IS NOT NULL AND length(cleaned_content) > 0) AS total_articles;
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build article-to-article link graph")
    p.add_argument("--host",     default=os.getenv("PGHOST", "localhost"))
    p.add_argument("--port",     type=int, default=int(os.getenv("PGPORT", "5432")))
    p.add_argument("--db",       default=os.getenv("PGDATABASE", "crawldb"))
    p.add_argument("--user",     default=os.getenv("PGUSER", "postgres"))
    p.add_argument("--password", default=os.getenv("PGPASSWORD", "postgres"))
    p.add_argument("--stats",    action="store_true", help="Print stats only, skip rebuild")
    p.add_argument("--force",    action="store_true", help="Truncate and rebuild from scratch")
    return p


def main() -> int:
    args = build_parser().parse_args()
    conn = psycopg2.connect(
        host=args.host, port=args.port,
        dbname=args.db, user=args.user, password=args.password,
    )
    conn.autocommit = True

    with conn.cursor() as cur:
        if args.stats:
            cur.execute(_STATS)
            row = cur.fetchone()
            if row:
                print(f"edges:           {row[0]:,}")
                print(f"source articles: {row[1]:,}")
                print(f"target articles: {row[2]:,}")
                print(f"total articles:  {row[3]:,}")
            return 0

        print("Creating article_link_graph table...")
        cur.execute(_CREATE_TABLE)

        if args.force:
            cur.execute("TRUNCATE crawldb.article_link_graph;")
            print("Truncated existing graph.")

        print("Populating from crawldb.link (article-only edges)...")
        cur.execute(_POPULATE)
        print("Done. Rows inserted (excluding conflicts).")

        cur.execute(_STATS)
        row = cur.fetchone()
        if row:
            print(f"Graph: {row[0]:,} edges between {row[1]:,} source → {row[2]:,} target articles")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
