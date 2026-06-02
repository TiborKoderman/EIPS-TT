"""Batch evaluation of PA3 RAG and LLM-only answering modes."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
from typing import Any

from generator import GenerationConfig, GenerationError, OllamaGenerator
from retriever import RetrievalConfig, RetrievalError, Retriever


BASE_DIR = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate PA3 RAG versus LLM-only mode")
    parser.add_argument("--queries-file", type=Path, default=BASE_DIR / "queries.json")
    parser.add_argument("--output-dir", type=Path, default=BASE_DIR / "runs")
    parser.add_argument("--model", default="gemma3:4b")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--db", default="crawldb")
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default="postgres")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rerank-candidates", type=int, default=20)
    parser.add_argument("--ivfflat-probes", type=int, default=10)
    parser.add_argument("--device", default=None)
    parser.add_argument("--intent-filter", choices=("all", "good", "bad"), default="all")
    parser.add_argument("--continue-on-error", action="store_true")
    return parser


def load_queries(path: Path, intent_filter: str) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    queries = data["queries"] if isinstance(data, dict) else data
    if intent_filter != "all":
        queries = [entry for entry in queries if entry.get("intent") == intent_filter]
    return queries


def manual_evaluation_template() -> dict[str, Any]:
    return {
        "retrieval_relevance_0_to_2": None,
        "rag_answer_quality_0_to_2": None,
        "llm_only_answer_quality_0_to_2": None,
        "groundedness_comment": "",
        "transparency_comment": "",
        "failure_type": None,
        "comment": "",
    }


def main() -> int:
    args = build_parser().parse_args()
    entries = load_queries(args.queries_file, args.intent_filter)
    if not entries:
        print("Ni izbranih evalvacijskih vprasanj.", file=sys.stderr)
        return 2

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
        rerank=True,
    ))
    generator = OllamaGenerator(GenerationConfig(
        model=args.model,
        base_url=args.ollama_url,
        temperature=args.temperature,
        timeout_seconds=args.timeout,
    ))
    payload: dict[str, Any] = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "description": "PA3 comparison of RAG (with context) and Ollama-only answers.",
        "retrieval": retriever.settings(),
        "generation": {
            "provider": "ollama",
            "model": args.model,
            "temperature": args.temperature,
            "base_url": args.ollama_url,
        },
        "queries": [],
    }

    for index, entry in enumerate(entries, start=1):
        query = entry["query"]
        print(f"[{index}/{len(entries)}] {entry['label']}: {query}")
        result: dict[str, Any] = {
            **entry,
            "manual_evaluation": manual_evaluation_template(),
        }
        try:
            hits = retriever.retrieve(query)
            result["retrieved_context"] = [hit.to_dict() for hit in hits]
            result["answer_with_context"] = generator.answer_with_context(query, hits)
            result["answer_without_context"] = generator.answer_without_context(query)
        except (RetrievalError, GenerationError) as exc:
            result["error"] = str(exc)
            payload["queries"].append(result)
            if args.continue_on_error:
                continue
            print(f"Napaka pri '{entry['label']}': {exc}", file=sys.stderr)
            return 2
        payload["queries"].append(result)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output_dir / f"{timestamp}_evaluation.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Shranjeni rezultati: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
