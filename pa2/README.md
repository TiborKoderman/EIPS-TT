# PA2 — MedOverNet RAG Extraction and Retrieval

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
├── crawler/
│   └── src/
│       ├── core/
│       │   ├── article_extractor.py        # BS4-based base extractor
│       │   ├── article_extractor_xpath.py  # XPath extraction (Section 2.1)
│       │   ├── article_extractor_regex.py  # regex extraction (Section 2.2)
│       │   ├── forum_extractor.py          # forum thread extraction
│       │   └── segmenter.py                # short + long chunking strategies
│       ├── fill_cleaned_content.py         # manually populate page.cleaned_content
│       ├── segment_pages_to_db.py          # manually populate segment tables
│       ├── compute_embeddings.py           # manually compute LaBSE embeddings
│       ├── build_link_graph.py             # manually build article_link_graph
│       └── rerank_crossencoder.py          # BAAI/bge-reranker-v2-m3 reranker
└── implementation-extraction/
    ├── demo.py                      # retrieval demo (Section 4)
    ├── eval/
    │   ├── queries.json             # 3 good + 3 bad Slovenian queries
    │   └── runs/                    # saved JSON run outputs
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

> **Note:** The dump is tracked through Git LFS. It contains the `vector` extension, `crawldb` schema, lookup tables, `crawldb.page` (without `html_content` to keep size manageable), `crawldb.page_segment_short` (438,793 segments), `crawldb.page_segment_long` (36,816 segments), and `crawldb.article_link_graph` — all with pre-computed LaBSE embeddings and tuned IVFFlat indexes. It intentionally excludes raw `link`, `frontier_queue`, `image`, and `page_data` tables. The dump is ~1.8 GB; if Git LFS is unavailable, download from the cloud link in `pa2/extraction-db/`.

### Apply migration to a fresh PA1 dump

```bash
psql -h localhost -p 5432 -U postgres -d crawldb \
  -f db/migrations/07_page_cleaned_content_and_segments.sql
```

---

## Running the manual extraction steps

Run each stage in order from the repo root with the virtualenv active.

### Stage 1 — Fill cleaned content (XPath extraction)

```bash
python pa2/crawler/src/fill_cleaned_content.py \
  --host localhost --port 5432 --db crawldb --user postgres --password postgres \
  --extractor xpath --limit 0
```

`--limit 0` processes all HTML pages. Populates `crawldb.page.cleaned_content`.

### Stage 2 — Segment pages

```bash
python pa2/crawler/src/segment_pages_to_db.py \
  --host localhost --port 5432 --db crawldb --user postgres --password postgres \
  --limit 0
```

Populates `crawldb.page_segment_short` (≤50 char chunks) and `crawldb.page_segment_long` (~250-word hybrid chunks with 50-word overlap).

### Stage 3 — Compute embeddings (LaBSE)

```bash
CUDA_VISIBLE_DEVICES="" \
python pa2/crawler/src/compute_embeddings.py \
  --db-host localhost --db-name crawldb --db-user postgres --db-pass postgres \
  --model-name sentence-transformers/LaBSE --batch-size 100
```

Populates the `embedding` column on both segment tables. Runtime: ~22 min on GPU (475 k segments via docker with `ul-fri-nlp-peft` image on P106-100), ~2 hours on CPU.

For GPU (recommended):

```bash
docker run --rm --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=1 -e PGPASSWORD=postgres \
  -e HF_HOME=/hf-cache --network=host \
  -v /path/to/pa2/crawler/src:/app:ro \
  -v ~/.cache/huggingface:/hf-cache \
  --entrypoint bash ul-fri-nlp-peft:latest \
  -c "pip install sentence-transformers psycopg2-binary pgvector --quiet && python /app/compute_embeddings.py --batch-size 512 --device cuda --db-host localhost"
```

### Optional — build article link graph

```bash
python pa2/crawler/src/build_link_graph.py \
  --host localhost --port 5432 --db crawldb --user postgres --password postgres
```

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
| `--rerank-candidates` | `4 * top-k` | Number of vector hits to fetch before reranking |
| `--intent-filter` | `all` | Filter query-file entries by `good` or `bad` intent |
| `--device` | auto | Torch device override, e.g. `cpu` |
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
- **PA2 dump:** `pa2/extraction-db/crawldb_pa2.dump` is a separate custom-format extraction artifact tracked through Git LFS (~1.8 GB). It contains 10,429 HTML pages, 5,893 pages with `cleaned_content`, 438,793 short segments, and 36,816 long segments with LaBSE embeddings (IVFFlat indexes: lists=662 short, lists=192 long). `html_content` is excluded from the dump to keep size manageable.
- **LSH deduplication bonus (PA1 §2.1):** Not implemented. Current deduplication is exact SHA-256 only. Not claimed.
- **PA1 tables untouched:** Migration 07 only adds columns to `crawldb.page` and creates new `page_segment_*` tables. Frontier, link, image, and page_data tables are unmodified.
