"""Run one PA3 question in RAG mode, LLM-only mode, or both modes."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
from typing import Any

from generator import GenerationConfig, GenerationError, OllamaGenerator
from retriever import RetrievalConfig, RetrievalError, Retriever


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PA3 RAG question-answering pipeline")
    parser.add_argument("--query", required=True, help="Vprasanje v slovenscini.")
    parser.add_argument("--mode", choices=("with", "without", "both"), default="both")
    parser.add_argument("--model", default="gemma3:4b", help="Ollama generation model.")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--db", default="crawldb")
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default="postgres")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rerank-candidates", type=int, default=20)
    parser.add_argument("--ivfflat-probes", type=int, default=10)
    parser.add_argument("--device", default=None)
    parser.add_argument("--no-rerank", action="store_true", help="Diagnostic vector-only retrieval.")
    parser.add_argument("--json-output", type=Path, default=None, help="Optional saved JSON result.")
    return parser


def run_question(args: argparse.Namespace) -> dict[str, Any]:
    generator = OllamaGenerator(GenerationConfig(
        model=args.model,
        base_url=args.ollama_url,
        temperature=args.temperature,
        timeout_seconds=args.timeout,
    ))
    result: dict[str, Any] = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "query": args.query,
        "mode": args.mode,
        "generation": {
            "provider": "ollama",
            "model": args.model,
            "temperature": args.temperature,
        },
    }

    hits = []
    if args.mode in {"with", "both"}:
        retriever = Retriever(RetrievalConfig(
            host=args.host,
            port=args.port,
            database=args.db,
            user=args.user,
            password=args.password,
            top_k=args.top_k,
            rerank_candidates=args.rerank_candidates,
            ivfflat_probes=args.ivfflat_probes,
            device=args.device,
            rerank=not args.no_rerank,
        ))
        hits = retriever.retrieve(args.query)
        result["retrieval"] = {
            "settings": retriever.settings(),
            "hits": [hit.to_dict() for hit in hits],
        }
        result["answer_with_context"] = generator.answer_with_context(args.query, hits)

    if args.mode in {"without", "both"}:
        result["answer_without_context"] = generator.answer_without_context(args.query)
    return result


def print_result(result: dict[str, Any]) -> None:
    print(f"Vprasanje: {result['query']}")
    retrieval = result.get("retrieval")
    if retrieval:
        print("\nPridobljeni dokazi (po rerankingu):")
        for hit in retrieval["hits"]:
            rerank = hit["rerank_score"]
            rerank_text = f", rerank={rerank:.4f}" if rerank is not None else ""
            print(
                f"[{hit['final_rank']}] vector_rank={hit['vector_rank']}, "
                f"cosine_distance={hit['cosine_distance']:.4f}{rerank_text}"
            )
            print(f"    {hit['url']}")
            preview = " ".join(hit["segment_text"].split())
            print(f"    {preview[:240]}{'...' if len(preview) > 240 else ''}")
    if "answer_with_context" in result:
        print("\nOdgovor z dokumenti:")
        print(result["answer_with_context"])
    if "answer_without_context" in result:
        print("\nOdgovor brez dokumentov:")
        print(result["answer_without_context"])


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_question(args)
    except (RetrievalError, GenerationError, ValueError) as exc:
        print(f"Napaka: {exc}", file=sys.stderr)
        return 2

    print_result(result)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nJSON rezultat: {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
