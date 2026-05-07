"""Cross-encoder reranker (PA2 Section 4.1).

Wraps `BAAI/bge-reranker-v2-m3` (multilingual, ~568 MB) so that the demo
program can re-score the initial pgvector top-k results and surface the
true best matches for Slovenian queries.

Why this model: multilingual cross-encoder with strong reported quality on
Slavic languages; loadable via sentence-transformers CrossEncoder API.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RerankResult:
    text: str
    original_score: float
    rerank_score: float
    payload: dict


_MODEL = None
_MODEL_NAME = None


def _load(model_name: str):
    global _MODEL, _MODEL_NAME
    if _MODEL is None or _MODEL_NAME != model_name:
        from sentence_transformers import CrossEncoder

        _MODEL = CrossEncoder(model_name)
        _MODEL_NAME = model_name
    return _MODEL


def rerank(
    query: str,
    candidates: list[dict],
    *,
    text_field: str = "segment_text",
    score_field: str = "score",
    model_name: str = "BAAI/bge-reranker-v2-m3",
    top_k: int | None = None,
) -> list[RerankResult]:
    """Rerank `candidates` by a cross-encoder relevance score.

    `candidates` is a list of dicts containing at least `text_field` and
    `score_field`. Returns the same list reordered, wrapped in RerankResult.
    """
    if not candidates:
        return []

    model = _load(model_name)
    pairs = [(query, c[text_field]) for c in candidates]
    raw_scores = model.predict(pairs)

    enriched: list[RerankResult] = [
        RerankResult(
            text=c[text_field],
            original_score=float(c.get(score_field, 0.0)),
            rerank_score=float(s),
            payload=c,
        )
        for c, s in zip(candidates, raw_scores)
    ]
    enriched.sort(key=lambda r: r.rerank_score, reverse=True)
    if top_k is not None:
        enriched = enriched[:top_k]
    return enriched


if __name__ == "__main__":
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Cross-encoder rerank smoke test")
    parser.add_argument("--query", required=True)
    parser.add_argument("--candidates-json", required=True, help="Path to JSON list of {segment_text,score} dicts")
    parser.add_argument("--model-name", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    with open(args.candidates_json, "r", encoding="utf-8") as fh:
        candidates = json.load(fh)

    results = rerank(args.query, candidates, model_name=args.model_name, top_k=args.top_k)
    out = [
        {
            "text": r.text,
            "original_score": r.original_score,
            "rerank_score": r.rerank_score,
        }
        for r in results
    ]
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
