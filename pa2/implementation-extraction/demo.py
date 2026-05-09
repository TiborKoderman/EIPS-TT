"""PA2 demo retriever (Section 4).

Accepts a Slovenian query, embeds it with the same model used at indexing
time (default `sentence-transformers/LaBSE`), runs a vector similarity
search against `crawldb.page_segment_long` (or `_short`), optionally
reranks with a cross-encoder, and prints + saves the top-k segments.

Usage examples:

  # single query, default cosine, no rerank
  python pa2/implementation-extraction/demo.py \\
    --query "Kateri so simptomi visokega krvnega tlaka?" --top-k 5

  # batch, with rerank, save outputs to eval/runs
  python pa2/implementation-extraction/demo.py \\
    --queries-file pa2/implementation-extraction/eval/queries.json --rerank
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from typing import Any

import psycopg2

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
PA2_SRC_DIR = os.path.join(REPO_ROOT, "pa2", "crawler", "src")
if PA2_SRC_DIR not in sys.path:
    sys.path.insert(0, PA2_SRC_DIR)


_METRIC_OPS = {
    "cosine": "<=>",
    "l2": "<->",
    "ip": "<#>",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PA2 vector retrieval demo")

    parser.add_argument("--host", default=os.getenv("PGHOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PGPORT", "5432")))
    parser.add_argument("--db", default=os.getenv("PGDATABASE", "crawldb"))
    parser.add_argument("--user", default=os.getenv("PGUSER", "postgres"))
    parser.add_argument("--password", default=os.getenv("PGPASSWORD", "postgres"))

    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--query", help="Single query string")
    src.add_argument("--queries-file", help="JSON file: list of {label, query, expected} entries")

    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--metric",
        choices=tuple(_METRIC_OPS.keys()),
        default="cosine",
    )
    parser.add_argument(
        "--table",
        choices=("page_segment_long", "page_segment_short"),
        default="page_segment_long",
    )
    parser.add_argument(
        "--model-name",
        default="sentence-transformers/LaBSE",
        help="Embedding model (must match what was used to populate the index).",
    )
    parser.add_argument("--device", default=None, help="Torch device override for embedding/reranking (e.g. cpu, cuda)")
    parser.add_argument(
        "--embedding-model-tag",
        default=None,
        help="Optional WHERE filter on page_segment_*.embedding_model column.",
    )
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--rerank-model", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument(
        "--rerank-candidates",
        type=int,
        default=None,
        help="How many initial vector hits to fetch before reranking. Defaults to 4x top-k.",
    )
    parser.add_argument(
        "--intent-filter",
        choices=("all", "good", "bad"),
        default="all",
        help="Optional filter when loading a queries file.",
    )
    parser.add_argument(
        "--corpus-fingerprint",
        default=None,
        help="Optional manager-provided corpus fingerprint to persist in saved run metadata.",
    )
    parser.add_argument(
        "--graph-context",
        action="store_true",
        help="Augment results with linked articles from article_link_graph (knowledge graph expansion).",
    )
    parser.add_argument(
        "--graph-hops",
        type=int,
        default=1,
        help="Number of link hops to follow for graph context (default 1).",
    )
    parser.add_argument(
        "--save-runs",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", "runs"),
    )
    parser.add_argument("--no-save", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    queries = _load_queries(args)
    if not queries:
        print("No queries to run.")
        return 1

    from sentence_transformers import SentenceTransformer
    from device_utils import resolve_torch_device

    device = resolve_torch_device(args.device)
    print(f"[demo] loading embedding model: {args.model_name} on device={device}", file=sys.stderr)
    embedder = SentenceTransformer(args.model_name, device=device)

    reranker = None
    if args.rerank:
        from rerank_crossencoder import rerank as _rerank

        reranker = _rerank
        print(f"[demo] reranker enabled: {args.rerank_model}", file=sys.stderr)

    conn = psycopg2.connect(
        host=args.host, port=args.port,
        dbname=args.db, user=args.user, password=args.password,
    )

    run_payload: dict[str, Any] = {
        "timestamp": dt.datetime.utcnow().isoformat() + "Z",
        "embedding_model": args.model_name,
        "metric": args.metric,
        "table": args.table,
        "top_k": args.top_k,
        "rerank": args.rerank,
        "rerank_model": args.rerank_model if args.rerank else None,
        "rerank_candidates": args.rerank_candidates,
        "intent_filter": args.intent_filter,
        "corpus_fingerprint": args.corpus_fingerprint,
        "queries": [],
    }

    try:
        for entry in queries:
            label = entry.get("label", "")
            query = entry["query"]
            expected = entry.get("expected", "")

            print(f"\n=== {label or '(query)'} ===")
            print(f"Q: {query}")
            if expected:
                print(f"expected: {expected}")

            initial = _vector_search(
                conn,
                table=args.table,
                metric=args.metric,
                query_vec=embedder.encode([query])[0].tolist(),
                top_k=_initial_candidate_count(args),
                embedding_model_tag=args.embedding_model_tag,
            )

            final = initial
            rerank_results: list[dict] = []
            if reranker is not None and initial:
                reranked = reranker(
                    query,
                    initial,
                    text_field="segment_text",
                    score_field="score",
                    model_name=args.rerank_model,
                    device=device,
                    top_k=args.top_k,
                )
                rerank_results = [
                    {
                        "page_id": r.payload.get("page_id"),
                        "url": r.payload.get("url"),
                        "score": r.original_score,
                        "rerank_score": r.rerank_score,
                        "preview": _preview(r.text),
                        "segment_text": r.text,
                    }
                    for r in reranked
                ]
                final = [
                    {**r.payload, "rerank_score": r.rerank_score} for r in reranked
                ]

            for i, row in enumerate(final[: args.top_k], 1):
                rs = row.get("rerank_score")
                tag = f" rerank={rs:.4f}" if rs is not None else ""
                print(f"  {i}. score={row['score']:.4f}{tag} [{row.get('url','')}]")
                print(f"     {_preview(row['segment_text'])}")
                if args.graph_context and row.get("page_id"):
                    neighbors = _graph_neighbors(conn, row["page_id"], hops=args.graph_hops)
                    if neighbors:
                        print(f"     → related: {', '.join(n['url'] for n in neighbors[:5])}")

            run_payload["queries"].append({
                "label": label,
                "query": query,
                "expected": expected,
                "initial": [
                    {
                        "page_id": r.get("page_id"),
                        "url": r.get("url"),
                        "score": r.get("score"),
                        "preview": _preview(r["segment_text"]),
                    }
                    for r in initial[: args.top_k]
                ],
                "rerank": rerank_results,
            })
    finally:
        conn.close()

    if not args.no_save:
        os.makedirs(args.save_runs, exist_ok=True)
        ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        suffix = "rerank" if args.rerank else "baseline"
        out_path = os.path.join(args.save_runs, f"{ts}_{suffix}.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(run_payload, fh, ensure_ascii=False, indent=2)
        print(f"\n[demo] saved run: {out_path}", file=sys.stderr)

    return 0


def _graph_neighbors(conn, page_id: int, *, hops: int = 1) -> list[dict]:
    """Return articles linked from page_id via article_link_graph (BFS up to `hops`)."""
    try:
        visited: set[int] = {page_id}
        frontier: set[int] = {page_id}
        for _ in range(hops):
            if not frontier:
                break
            placeholders = ",".join(["%s"] * len(frontier))
            sql = f"""
                SELECT DISTINCT alg.to_page, p.url
                FROM crawldb.article_link_graph alg
                JOIN crawldb.page p ON p.id = alg.to_page
                WHERE alg.from_page IN ({placeholders})
                  AND alg.to_page <> ALL(%s::int[])
                LIMIT 20
            """
            with conn.cursor() as cur:
                cur.execute(sql, (*frontier, list(visited)))
                rows = cur.fetchall()
            new_frontier: set[int] = set()
            results = []
            for pid, url in rows:
                if pid not in visited:
                    visited.add(pid)
                    new_frontier.add(pid)
                    results.append({"page_id": pid, "url": url})
            frontier = new_frontier
        return results
    except Exception:
        return []


def _vector_search(
    conn,
    *,
    table: str,
    metric: str,
    query_vec: list[float],
    top_k: int,
    embedding_model_tag: str | None,
) -> list[dict]:
    op = _METRIC_OPS[metric]
    score_expr = f"(s.embedding {op} %s::vector)"
    where_extra = ""
    params: list[Any] = [query_vec, query_vec]
    if embedding_model_tag is not None:
        where_extra = "AND s.embedding_model = %s"
        params.append(embedding_model_tag)
    params.append(top_k)

    sql = f"""
        SELECT
            s.page_id,
            p.url,
            s.segment_text,
            {score_expr} AS score
        FROM crawldb.{table} s
        LEFT JOIN crawldb.page p ON p.id = s.page_id
        WHERE s.embedding IS NOT NULL
          {where_extra}
        ORDER BY s.embedding {op} %s::vector
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {"page_id": r[0], "url": r[1], "segment_text": r[2], "score": float(r[3])}
        for r in rows
    ]


def _load_queries(args: argparse.Namespace) -> list[dict]:
    if args.query:
        return [{"label": "ad-hoc", "query": args.query, "expected": ""}]
    with open(args.queries_file, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("queries", [])
    queries = [q for q in data if q.get("query")]
    if args.intent_filter != "all":
        queries = [
            q for q in queries
            if str(q.get("intent", "")).strip().lower() == args.intent_filter
        ]
    return queries


def _initial_candidate_count(args: argparse.Namespace) -> int:
    if not args.rerank:
        return args.top_k
    if args.rerank_candidates is not None:
        return max(args.rerank_candidates, args.top_k)
    return max(args.top_k * 4, args.top_k)


def _preview(text: str, n: int = 200) -> str:
    text = (text or "").replace("\n", " ")
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


if __name__ == "__main__":
    raise SystemExit(main())
