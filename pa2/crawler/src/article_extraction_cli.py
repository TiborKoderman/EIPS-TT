"""CLI za hiter ročni QA article ekstrakcije.

Uporabi, ko želiš pred batch validacijo podrobno preveriti eno stran.
Izhod je takojšen: JSON rezultat + opcijski izpis cleaned texta.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests

from core.article_extractor import extract_medover_article


def build_parser() -> argparse.ArgumentParser:
    """Definira CLI argumente za preverjanje ene strani."""
    parser = argparse.ArgumentParser(description="MedOverNet article extraction preview")
    parser.add_argument("--url", type=str, help="Article URL to fetch and extract.")
    parser.add_argument("--html-file", type=str, help="Local HTML file to extract from.")
    parser.add_argument(
        "--output-json",
        type=str,
        help="Optional output path for JSON extraction result.",
    )
    parser.add_argument(
        "--print-cleaned",
        action="store_true",
        help="Print cleaned text output to stdout.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP timeout in seconds when --url is used.",
    )
    parser.add_argument(
        "--user-agent",
        type=str,
        default="fri-wier-EIPS-TT",
        help="User-Agent for HTTP fetch when --url is used.",
    )
    return parser


def main() -> int:
    """Zažene extraction workflow za URL ali lokalni HTML file."""
    args = build_parser().parse_args()
    if not args.url and not args.html_file:
        print("Provide --url or --html-file.")
        return 2

    if args.url:
        if "..." in args.url:
            # Pogosta copy/paste napaka iz primerov; fail-fast z jasnim sporočilom.
            print("Provided URL contains '...'. Use a full article URL.")
            return 2
        headers = {"User-Agent": args.user_agent}
        # Ekspliciten User-Agent zagotovi enako obnašanje kot glavni crawler.
        response = requests.get(args.url, timeout=args.timeout, headers=headers)
        response.raise_for_status()
        url = args.url
        html = response.text
    else:
        html_path = Path(args.html_file)
        if not html_path.exists():
            # Jasna validacijska napaka namesto stack trace za manjkajočo datoteko.
            print(f"HTML file does not exist: {html_path}")
            return 2
        html = html_path.read_text(encoding="utf-8", errors="ignore")
        url = f"file://{html_path.resolve()}"

    result = extract_medover_article(url=url, html=html)
    payload = {
        "is_article": result.is_article,
        "reason": result.reason,
        "url": result.url,
        "title": result.title,
        "author": result.author,
        "published_at": result.published_at,
        "section_headings": result.section_headings,
        "paragraph_count": len(result.body_paragraphs),
        "cleaned_content_chars": len(result.cleaned_content),
        "cleaned_content": result.cleaned_content,
    }

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved JSON result to: {out}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.print_cleaned:
        print("\n=== CLEANED CONTENT ===\n")
        print(result.cleaned_content)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
