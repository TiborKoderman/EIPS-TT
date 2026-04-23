"""Batch validacijsko orodje za MedOverNet article ekstrakcijo."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any

import requests

from core.article_extractor_xpath import extract_medover_article
from core.article_extractor import ArticleExtractionResult


def build_parser() -> argparse.ArgumentParser:
    """Definira argumente za batch validacijo na vzorcu URL-jev."""
    parser = argparse.ArgumentParser(
        description="Validate article extractor on a sample URL list."
    )
    parser.add_argument(
        "--input",
        required=True,
        help=(
            "Path to CSV with columns: url[,expected_is_article]. "
            "Header row is required."
        ),
    )
    parser.add_argument(
        "--output-json",
        required=True,
        help="Path for full validation report JSON.",
    )
    parser.add_argument(
        "--output-csv",
        help="Optional path for per-URL result CSV.",
    )
    parser.add_argument(
        "--user-agent",
        default="fri-wier-EIPS-TT",
        help="User-Agent used for URL fetches.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP timeout in seconds.",
    )
    return parser


def main() -> int:
    """Zažene batch validacijo in shrani povzetek + per-URL rezultate."""
    args = build_parser().parse_args()
    rows = _load_input_rows(Path(args.input))
    results: list[dict[str, Any]] = []
    headers = {"User-Agent": args.user_agent}

    for row in rows:
        url = row["url"]
        expected = row.get("expected_is_article")
        record: dict[str, Any] = {
            "url": url,
            "expected_is_article": expected,
        }

        try:
            response = requests.get(url, headers=headers, timeout=args.timeout)
            response.raise_for_status()
        except Exception as exc:
            record.update(
                {
                    "fetch_ok": False,
                    "http_status": getattr(getattr(exc, "response", None), "status_code", None),
                    "error": str(exc),
                    "is_article": None,
                    "reason": None,
                    "title": None,
                    "author": None,
                    "published_at": None,
                    "paragraph_count": 0,
                    "cleaned_content_chars": 0,
                    "matched_expected": None,
                }
            )
            results.append(record)
            continue

        extraction: ArticleExtractionResult = extract_medover_article(url=url, html=response.text)
        matched_expected = None
        if expected is not None:
            matched_expected = extraction.is_article == expected

        record.update(
            {
                "fetch_ok": True,
                "http_status": response.status_code,
                "error": None,
                "is_article": extraction.is_article,
                "reason": extraction.reason,
                "title": extraction.title,
                "author": extraction.author,
                "published_at": extraction.published_at,
                "section_headings": extraction.section_headings,
                "body_paragraphs": extraction.body_paragraphs,
                "paragraph_count": len(extraction.body_paragraphs),
                "cleaned_content_chars": len(extraction.cleaned_content),
                "cleaned_content": extraction.cleaned_content,
                "matched_expected": matched_expected,
            }
        )
        results.append(record)

    summary = _build_summary(results)
    report = {
        "summary": summary,
        "results": results,
    }

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved validation report JSON: {output_json}")
    _print_summary(summary)

    if args.output_csv:
        output_csv = Path(args.output_csv)
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        _write_results_csv(output_csv, results)
        print(f"Saved validation details CSV: {output_csv}")

    return 0


def _load_input_rows(path: Path) -> list[dict[str, Any]]:
    """Prebere vhodni CSV (obvezna glava, obvezen stolpec `url`)."""
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("Input CSV must have a header row.")
        if "url" not in reader.fieldnames:
            raise ValueError("Input CSV must contain 'url' column.")

        rows: list[dict[str, Any]] = []
        for raw in reader:
            url = (raw.get("url") or "").strip()
            if not url:
                continue
            expected_raw = (raw.get("expected_is_article") or "").strip()
            expected: bool | None = None
            if expected_raw:
                expected = _parse_bool(expected_raw)
            rows.append(
                {
                    "url": url,
                    "expected_is_article": expected,
                }
            )
        return rows


def _parse_bool(value: str) -> bool:
    """Pretvori tekstovne bool vrednosti iz CSV v Python bool."""
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"Invalid boolean value for expected_is_article: {value}")


def _build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Izračuna ključne metrike za hitro oceno kakovosti ekstrakcije."""
    total = len(results)
    fetch_ok = [row for row in results if row["fetch_ok"]]
    fetch_failed = [row for row in results if not row["fetch_ok"]]
    predicted_articles = [row for row in fetch_ok if row["is_article"] is True]
    predicted_non_articles = [row for row in fetch_ok if row["is_article"] is False]

    char_counts = [row["cleaned_content_chars"] for row in predicted_articles]
    paragraph_counts = [row["paragraph_count"] for row in predicted_articles]

    labeled = [row for row in fetch_ok if row["expected_is_article"] is not None]
    matched = [row for row in labeled if row["matched_expected"] is True]

    return {
        "total_urls": total,
        "fetch_ok": len(fetch_ok),
        "fetch_failed": len(fetch_failed),
        "predicted_articles": len(predicted_articles),
        "predicted_non_articles": len(predicted_non_articles),
        "avg_cleaned_content_chars_for_articles": round(mean(char_counts), 2) if char_counts else 0,
        "avg_paragraph_count_for_articles": round(mean(paragraph_counts), 2) if paragraph_counts else 0,
        "labeled_samples": len(labeled),
        "labeled_matches": len(matched),
        "labeled_accuracy": round(len(matched) / len(labeled), 4) if labeled else None,
    }


def _print_summary(summary: dict[str, Any]) -> None:
    """Izpiše povzetek metrik v terminal (human-readable)."""
    print("Validation summary:")
    print(f"  total_urls: {summary['total_urls']}")
    print(f"  fetch_ok: {summary['fetch_ok']}")
    print(f"  fetch_failed: {summary['fetch_failed']}")
    print(f"  predicted_articles: {summary['predicted_articles']}")
    print(f"  predicted_non_articles: {summary['predicted_non_articles']}")
    print(
        "  avg_cleaned_content_chars_for_articles: "
        f"{summary['avg_cleaned_content_chars_for_articles']}"
    )
    print(
        "  avg_paragraph_count_for_articles: "
        f"{summary['avg_paragraph_count_for_articles']}"
    )
    print(f"  labeled_samples: {summary['labeled_samples']}")
    print(f"  labeled_matches: {summary['labeled_matches']}")
    print(f"  labeled_accuracy: {summary['labeled_accuracy']}")


def _write_results_csv(path: Path, results: list[dict[str, Any]]) -> None:
    """Shrani per-URL rezultate v CSV za ročni pregled/filteriranje."""
    fields = [
        "url",
        "expected_is_article",
        "fetch_ok",
        "http_status",
        "error",
        "is_article",
        "matched_expected",
        "reason",
        "paragraph_count",
        "cleaned_content_chars",
        "title",
        "author",
        "published_at",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field) for field in fields})


if __name__ == "__main__":
    raise SystemExit(main())
