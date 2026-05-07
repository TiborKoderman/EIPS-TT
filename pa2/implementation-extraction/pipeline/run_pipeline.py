"""End-to-end corpus preparation pipeline for PA2.

Runs in order:
  1. fill_cleaned_content  — extract article text from stored HTML
  2. segment_pages         — chunk cleaned text into short + long segments
  3. compute_embeddings    — embed all un-embedded segments with LaBSE
  4. build_link_graph      — build article-to-article link graph

Each stage is idempotent: already-processed records are skipped unless
--force is passed.  Run from the repo root:

  python pa2/implementation-extraction/pipeline/run_pipeline.py

  # process only new pages since last run (default)
  python pa2/implementation-extraction/pipeline/run_pipeline.py

  # reprocess everything from scratch
  python pa2/implementation-extraction/pipeline/run_pipeline.py --force

  # skip specific stages
  python pa2/implementation-extraction/pipeline/run_pipeline.py --skip-embed --skip-graph
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parents[2]


def _run(cmd: list[str], *, label: str) -> int:
    print(f"\n{'=' * 60}")
    print(f"[pipeline] {label}")
    print(f"{'=' * 60}")
    t0 = time.monotonic()
    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    elapsed = time.monotonic() - t0
    status = "OK" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    print(f"[pipeline] {label} → {status} in {elapsed:.1f}s")
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="PA2 end-to-end corpus preparation pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # DB connection (forwarded to every sub-script)
    p.add_argument("--host", default=os.getenv("PGHOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.getenv("PGPORT", "5432")))
    p.add_argument("--db", default=os.getenv("PGDATABASE", "crawldb"))
    p.add_argument("--user", default=os.getenv("PGUSER", "postgres"))
    p.add_argument("--password", default=os.getenv("PGPASSWORD", "postgres"))

    # Stage toggles
    p.add_argument("--skip-extract", action="store_true", help="Skip fill_cleaned_content")
    p.add_argument("--skip-segment", action="store_true", help="Skip segment_pages")
    p.add_argument("--skip-embed", action="store_true", help="Skip compute_embeddings")
    p.add_argument("--skip-graph", action="store_true", help="Skip build_link_graph")

    # Common options forwarded to sub-scripts
    p.add_argument(
        "--force",
        action="store_true",
        help="Reprocess already-processed records in every stage",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max pages per extraction/segmentation run (0 = all)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Embedding batch size passed to compute_embeddings",
    )
    p.add_argument(
        "--model-name",
        default="sentence-transformers/LaBSE",
        help="Embedding model (must match what's already in the DB)",
    )
    p.add_argument(
        "--embedding-model",
        default="sentence-transformers/LaBSE",
        help="embedding_model tag written into page_segment_* rows",
    )
    p.add_argument(
        "--extractor",
        choices=("bs4", "xpath"),
        default="xpath",
        help="Extractor backend for fill_cleaned_content",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass --dry-run to extract + segment stages (no DB writes)",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    python = sys.executable

    db_flags = [
        "--host", args.host,
        "--port", str(args.port),
        "--db", args.db,
        "--user", args.user,
        "--password", args.password,
    ]

    errors: list[str] = []

    # ── Stage 1: fill_cleaned_content ──────────────────────────────────────
    if not args.skip_extract:
        cmd = [python, str(PIPELINE_DIR / "fill_cleaned_content.py")] + db_flags + [
            "--extractor", args.extractor,
            "--commit-every", "50",
        ]
        if args.limit > 0:
            cmd += ["--limit", str(args.limit)]
        else:
            cmd += ["--limit", "0"]
        if args.force:
            cmd.append("--force")
        if args.dry_run:
            cmd.append("--dry-run")
        rc = _run(cmd, label="Stage 1: fill_cleaned_content")
        if rc != 0:
            errors.append("fill_cleaned_content")

    # ── Stage 2: segment_pages ──────────────────────────────────────────────
    if not args.skip_segment:
        cmd = [python, str(PIPELINE_DIR / "segment_pages.py")] + db_flags + [
            "--embedding-model", args.embedding_model,
        ]
        if args.limit > 0:
            cmd += ["--limit", str(args.limit)]
        else:
            cmd += ["--limit", "0"]
        if args.force:
            cmd.append("--force")
        if args.dry_run:
            cmd.append("--dry-run")
        rc = _run(cmd, label="Stage 2: segment_pages")
        if rc != 0:
            errors.append("segment_pages")

    # ── Stage 3: compute_embeddings ─────────────────────────────────────────
    if not args.skip_embed and not args.dry_run:
        cmd = [python, str(PIPELINE_DIR / "compute_embeddings.py"),
               "--db-host", args.host,
               "--db-name", args.db,
               "--db-user", args.user,
               "--db-pass", args.password,
               "--model-name", args.model_name,
               "--batch-size", str(args.batch_size),
               ]
        rc = _run(cmd, label="Stage 3: compute_embeddings (LaBSE)")
        if rc != 0:
            errors.append("compute_embeddings")

    # ── Stage 4: build_link_graph ───────────────────────────────────────────
    if not args.skip_graph and not args.dry_run:
        cmd = [python, str(PIPELINE_DIR / "build_link_graph.py")] + db_flags
        if args.force:
            cmd.append("--force")
        rc = _run(cmd, label="Stage 4: build_link_graph")
        if rc != 0:
            errors.append("build_link_graph")

    print(f"\n{'=' * 60}")
    if errors:
        print(f"[pipeline] COMPLETED WITH ERRORS in: {', '.join(errors)}")
        return 1
    print("[pipeline] ALL STAGES COMPLETED SUCCESSFULLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
