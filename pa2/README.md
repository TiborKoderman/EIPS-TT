# PA2 — MedOverNet RAG Extraction Pipeline

**Assignment:** IEPS Programming Assignment 2  
**Authors:** Dino Džaferagić, Tibor Koderman, Jerneja Krajcar  
**Corpus:** MedOverNet (`https://medover.zurnal24.si/`) — Slovenian medical/wellness portal

---

## Directory layout

```
pa2/
├── README.md                        # this file
├── report-extraction.pdf            # compiled report
├── compliance.md                    # per-requirement audit against Assignment2.md
├── TODO.md                          # actionable checklist
├── extraction-db/
│   └── crawldb_pa2.dump             # Git LFS pg_dump custom-format export
├── report/
│   └── report.tex                   # LaTeX source
└── implementation-extraction/
    ├── demo.py                      # retrieval demo (Section 4)
    ├── eval/
    │   ├── queries.json             # 3 good + 3 bad Slovenian queries
    │   └── runs/                    # saved JSON run outputs
    └── pipeline/
        ├── article_extractor.py     # BS4-based base extractor (ArticleExtractionResult)
        ├── extractor_xpath.py       # XPath extraction (Section 2.1, primary)
        ├── extractor_regex.py       # regex extraction (Section 2.2, co-equal alternative)
        ├── fill_cleaned_content.py  # populate page.cleaned_content from HTML
        ├── segmenter.py             # two chunking strategies (short ≤50 char, long ~250 word)
        ├── segment_pages.py         # populate page_segment_short + page_segment_long
        ├── compute_embeddings.py    # LaBSE 768-d embeddings for both segment tables
        └── rerank_crossencoder.py   # BAAI/bge-reranker-v2-m3 cross-encoder reranker
```

---

## Prerequisites

- Docker with the `eips-tt-db-1` pgvector container running (`docker compose up -d db`)
- Python 3.10+ with the project virtualenv activated (`.venv/`)
- `latexmk` for building the PDF report

```bash
# activate virtualenv
source .venv/bin/activate
```

---

## Database restore

The PA2 database is a pgvector-extended PostgreSQL instance. To restore from the dump:

```bash
# 1. Start the container
docker compose up -d db

# 2. Restore the dump into the crawldb database
pg_restore -h localhost -p 5432 -U postgres -d crawldb \
  --no-owner --no-acl \
  pa2/extraction-db/crawldb_pa2.dump

# Verify
docker compose exec db psql -U postgres -d crawldb \
  -c "SELECT COUNT(*) FROM crawldb.page_segment_long WHERE embedding IS NOT NULL;"
```

> **Note:** The dump is tracked through Git LFS. It contains the `vector` extension, `crawldb` schema, lookup tables, `crawldb.page` (HTML content), `crawldb.page_segment_short`, `crawldb.page_segment_long`, and `crawldb.article_link_graph` with pre-computed LaBSE embeddings. It intentionally excludes raw `link`, `frontier_queue`, `image`, and `page_data` tables.

### Apply migration to a fresh PA1 dump

```bash
psql -h localhost -p 5432 -U postgres -d crawldb \
  -f db/migrations/07_page_cleaned_content_and_segments.sql
```

---

## Running the pipeline

Run each stage in order from the repo root with the virtualenv active.

### Stage 1 — Fill cleaned content (XPath extraction)

```bash
python pa2/implementation-extraction/pipeline/fill_cleaned_content.py \
  --host localhost --port 5432 --db crawldb --user postgres --password postgres \
  --extractor xpath --limit 0
```

`--limit 0` processes all HTML pages. Populates `crawldb.page.cleaned_content`.

### Stage 2 — Segment pages

```bash
python pa2/implementation-extraction/pipeline/segment_pages.py \
  --host localhost --port 5432 --db crawldb --user postgres --password postgres \
  --limit 0
```

Populates `crawldb.page_segment_short` (≤50 char chunks) and `crawldb.page_segment_long` (~250-word hybrid chunks with 50-word overlap).

### Stage 3 — Compute embeddings (LaBSE)

```bash
CUDA_VISIBLE_DEVICES="" \
python pa2/implementation-extraction/pipeline/compute_embeddings.py \
  --db-host localhost --db-name crawldb --db-user postgres --db-pass postgres \
  --model-name sentence-transformers/LaBSE --batch-size 100
```

Populates the `embedding` column on both segment tables. Runtime: ~3 hours on CPU (53 k segments).  
Set `CUDA_VISIBLE_DEVICES` to a valid GPU ID to use GPU acceleration.

---

## Running the demo retriever

```bash
# single query
python pa2/implementation-extraction/demo.py \
  --query "Kateri so simptomi visokega krvnega tlaka?" \
  --top-k 5 \
  --host localhost --port 5432 --db crawldb --user postgres --password postgres

# batch, all 6 evaluation queries
python pa2/implementation-extraction/demo.py \
  --queries-file pa2/implementation-extraction/eval/queries.json \
  --top-k 5 \
  --host localhost --port 5432 --db crawldb --user postgres --password postgres

# with cross-encoder reranker
python pa2/implementation-extraction/demo.py \
  --queries-file pa2/implementation-extraction/eval/queries.json \
  --top-k 5 --rerank \
  --host localhost --port 5432 --db crawldb --user postgres --password postgres
```

Run outputs are saved to `pa2/implementation-extraction/eval/runs/<timestamp>_{baseline,rerank}.json`.

### Demo flags

| Flag | Default | Description |
|---|---|---|
| `--query` | — | Single query string (mutually exclusive with `--queries-file`) |
| `--queries-file` | — | JSON file with list of `{label, query, expected}` entries |
| `--top-k` | 5 | Number of results to return |
| `--metric` | `cosine` | Distance metric: `cosine`, `l2`, `ip` |
| `--table` | `page_segment_long` | Which segment table to query |
| `--model-name` | `sentence-transformers/LaBSE` | Embedding model |
| `--rerank` | off | Enable cross-encoder reranking (BAAI/bge-reranker-v2-m3) |
| `--rerank-model` | `BAAI/bge-reranker-v2-m3` | Reranker model name |
| `--no-save` | off | Do not save run output to `eval/runs/` |

---

## Building the report PDF

```bash
latexmk -pdf pa2/report/report.tex -outdir pa2/report
cp pa2/report/report.pdf pa2/report-extraction.pdf
```

---

## PA1 notes

- **PA1 dump:** `pa1/db` remains the Assignment 1 custom-format artifact, is 64 MiB, and excludes `image`/`page_data` table data as required. It contains 1,001 HTML rows, below the PA1 5,000-page guideline.
- **PA2 dump:** `pa2/extraction-db/crawldb_pa2.dump` is a separate custom-format extraction artifact tracked through Git LFS. It contains 10,413 HTML pages, 779 cleaned articles, 51,394 short segments, and 2,414 long segments with LaBSE embeddings.
- **LSH deduplication bonus (PA1 §2.1):** Not implemented. Current deduplication is exact SHA-256 only. Not claimed.
- **PA1 tables untouched:** Migration 07 only adds columns to `crawldb.page` and creates new `page_segment_*` tables. Frontier, link, image, and page_data tables are unmodified.
