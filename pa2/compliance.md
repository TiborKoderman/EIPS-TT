# PA2 Compliance Audit

Source-of-truth for grading: `docs/Assignment2.md`. This file enumerates every gradable subsection and records, **per requirement**, the current implementation status on the `assignment2` branch.

Status legend:

- DONE — meets the assignment requirement; evidence listed.
- PARTIAL — partially implemented; concrete gap noted.
- MISSING — not implemented.

Last verified: 2026-05-10. Verified artifact: `pa2/extraction-db/crawldb_pa2.dump` (PostgreSQL custom format, gzip, 1,794,589,312 bytes, tracked through Git LFS). Dump state: 10,429 HTML pages, 5,893 pages with `cleaned_content`, 438,793 short segments (articles + forum threads), 36,816 long segments, all embedded with LaBSE. IVFFlat indexes rebuilt: lists=662 (short), lists=192 (long). `html_content` excluded from dump to keep artifact manageable.

## Section 2 — Identifying and extracting information

### 2.1 XPath extraction

- **Requirement:** use XPath expressions to filter relevant content from HTML pages.
- **Status:** DONE
- **Evidence:** `pa2/crawler/src/core/article_extractor_xpath.py` — multi-selector XPath extractor (`//main//article`, `//article`, `//*[@itemprop='articleBody']`, CMS class containers), score-based root selection, deterministic boilerplate removal via XPath drops + noise-keyword class/id filter.
- **Verified:** the PA2 dump contains 10,413 HTML pages. URL gate breakdown from the dump: 9,062 forum threads, 436 forum/category index pages, 128 tag/author/login/search-style pages, and 787 potential articles. Of those 787 potential articles, 779 yielded non-empty `cleaned_content`; the remaining 8 failed the content gate and are intentionally unsegmented.

### 2.2 Regex extraction

- **Requirement:** use regular expressions as a co-equal extraction technique.
- **Status:** DONE
- **Evidence:** `pa2/crawler/src/core/article_extractor_regex.py` — pure-regex extractor for title (`<h1>` / `og:title` / `<title>`), `published_at` (article:published_time meta + ISO fallback), author (meta name=author), and regex-stripped article body. Compared against XPath in report §"Regex-Based Extraction".

### 2.3 Cleaned plain text + chunking

- **Requirement:** prepare plain text (boilerplate removed); define a thematic chunking strategy with stated advantages/disadvantages.
- **Status:** DONE
- **Evidence:**
  - Cleaned text: `pa2/crawler/src/core/article_extractor_xpath.py` + `pa2/crawler/src/fill_cleaned_content.py` → `crawldb.page.cleaned_content` (migration 07).
  - Chunking: `pa2/crawler/src/core/segmenter.py` — `build_short_char_chunks` (fixed ≤50 chars, no boundary respect) and `build_long_word_chunks` (~250 words, paragraph + sentence aware, 50-word overlap).
  - Report: §"Short segments" and §"Logical & Hybrid Overlapping Segments" cover criteria, advantages, disadvantages for both strategies.
- **Verified:** 438,793 short + 36,816 long segments in DB (articles + forum threads). Spot-check: long segments read as coherent Slovenian paragraphs.

## Section 3 — Storing extracted information in a vector database

### 3.1 pgvector + cleaned_content + page_segment table (vector(768))

- **Requirement:** enable pgvector, add `cleaned_content` to `page`, create a `page_segment` table with `vector(768)`.
- **Status:** DONE
- **Evidence:** `db/migrations/07_page_cleaned_content_and_segments.sql` — `CREATE EXTENSION IF NOT EXISTS vector`, `ALTER TABLE crawldb.page ADD COLUMN cleaned_content text` (+ `cleaned_content_hash`), creates `crawldb.page_segment_short` and `crawldb.page_segment_long` both with `vector(768)`, FK `ON DELETE RESTRICT`, plus `embedding_model`, `metadata` JSONB, `created_at`, `embedded_at`.
- **Verified:** `\d crawldb.page` shows `cleaned_content`, `cleaned_content_hash`. Both segment tables exist with correct schema.

### 3.2 Embedding generation

- **Requirement:** experiment with different embedding models and store 768-d vectors per segment.
- **Status:** DONE (primary); alt-model noted in report only (schema-incompatible 384-d model)
- **Evidence:**
  - Primary: `pa2/crawler/src/compute_embeddings.py` with `sentence-transformers/LaBSE` (768-d, multilingual). All 475,609 segments embedded (438,793 short + 36,816 long).
  - Verified: `SELECT COUNT(*) FROM crawldb.page_segment_long WHERE embedding IS NULL` = 0; `page_segment_short` = 0.
  - `embedding_model` column set to `sentence-transformers/LaBSE` on all rows.
  - Alt-model: `paraphrase-multilingual-MiniLM-L12-v2` (384-d) is schema-incompatible with `vector(768)`; qualitative comparison documented in report §"Methods Tried but Not Used". No separate DB table created.

### 3.3 ANN index + similarity metric

- **Requirement:** use HNSW or IVFFlat for ANN; pick and justify a similarity metric.
- **Status:** DONE
- **Evidence:**
  - IVFFlat indexes on both tables using `vector_cosine_ops` (cosine distance).
  - `lists` tuned post-load: short=662 (≈√438793), long=192 (≈√36816). Indexes rebuilt 2026-05-10 + ANALYZE run.
  - Report §"Similarity Metric and ANN Index" justifies cosine choice (LaBSE produces unit-norm vectors; magnitude-invariant angle metric is correct) and IVFFlat vs HNSW tradeoff (batch-only use case, memory constraint).
- **Verified:** `\d crawldb.page_segment_long` shows `idx_page_segment_long_embedding_ivfflat` with `lists=192`.

## Section 4 — Information retrieval

### 4 (demo program)

- **Requirement:** demo accepts query, computes embedding, runs similarity search, displays top-k. Must include 3 expected-to-work and 3 expected-to-fail queries.
- **Status:** DONE
- **Evidence:**
  - `pa2/implementation-extraction/demo.py` — `--query` / `--queries-file`, `--metric cosine|l2|ip`, `--table page_segment_long|short`, `--top-k`, `--rerank`, `--model-name`; saves runs to `eval/runs/<ts>_{baseline,rerank}.json`.
  - `eval/queries.json` — 3 good Slovenian queries (krvni tlak, sladkorna bolezen, nespečnost), 3 bad (ambiguous "Boli me", out-of-domain legal, acronym "ATM").
  - `eval/runs/20260506T143228Z_baseline.json` — 6 queries × 5 results; verified all 6 queries return 5 segments.
  - Good queries return relevant Slovenian medical content; bad queries return off-topic or low-confidence results as expected.

### 4.1 Reranker

- **Requirement:** rerank initial top-k with a stronger model; test on the 3 poor queries.
- **Status:** DONE
- **Evidence:**
  - `pa2/crawler/src/rerank_crossencoder.py` — `BAAI/bge-reranker-v2-m3` cross-encoder.
  - `eval/runs/20260506T143525Z_rerank.json` — before/after run saved.
  - Improvement observed: insomnia query rank-1 promoted from generic article (cosine 0.465) to melatonin-specific article (rerank 0.991).
  - Remaining failure: legal query — all rerank scores ≈0.0001; zero relevant content in corpus. Documented in report §"Remaining Reranker Failure".

## Section 6 — Submission

### 6 (directory layout)

- **Requirement:** `pa2/{report-extraction.pdf, README.md, implementation-extraction/demo.py, extraction-db/}`.
- **Status:** DONE
- **Evidence:**
  - `pa2/report-extraction.pdf` — 5 pages, 131 kB, built from `pa2/report/report.tex`.
  - `pa2/README.md` — setup, DB restore, manual extraction step order, demo flags, PA1 notes.
  - `pa2/implementation-extraction/demo.py` — present.
  - `pa2/extraction-db/crawldb_pa2.dump` — pg_dump custom-format, gzip-compressed, 710 MiB / 743,512,347 bytes, tracked through Git LFS. Contains the `vector` extension, `crawldb` schema, lookup tables, `crawldb.page`, `crawldb.page_segment_short`, `crawldb.page_segment_long`, and `crawldb.article_link_graph`; excludes raw `link`, `frontier_queue`, `image`, and `page_data` tables.
- **Pending:** manual step — confirm GitHub user `opbieps` has read access.

### 6.1 Report content fields

- **Requirement:** filtering, text division (criteria + impl + adv/disadv), embedding choice + why, similarity metric choice + why, query examples + responses, reranker + improvement examples, one remaining reranker failure, limitations, "methods tried but not used".
- **Status:** DONE
- **Evidence:** `pa2/report/report.tex` covers:
  - §"Website Filtering" — URL gate + content gate
  - §"Boilerplate Removal" — XPath approach
  - §"Regex-Based Extraction" — regex co-equal technique
  - §"Short segments" + §"Logical & Hybrid Overlapping Segments" — both strategies with adv/disadv
  - §"Database Design and Schema Justification" — schema rationale
  - §"Selected Model and Multilingualism" — LaBSE choice
  - §"Similarity Metric and ANN Index" — cosine vs L2 vs IP; IVFFlat vs HNSW
  - §"Demo Program" — query examples table with real scores (baseline run)
  - §"Cross-Encoder Reranker" — model choice + real before/after results
  - §"Remaining Reranker Failure" — legal query case
  - §"Limitations" — artifact scope, IVFFlat probe sensitivity, short-segment quality, Slovenian-only coverage
  - §"Methods Tried but Not Used" — regex-only, MiniLM alt model, HNSW, sentence-only segmentation

## Manual extraction workflow (restored, 2026-05-09)

- **Status:** DONE
- **Evidence:** extraction is manual again under the teammate-style layout: `pa2/crawler/src/fill_cleaned_content.py`, `pa2/crawler/src/segment_pages_to_db.py`, `pa2/crawler/src/compute_embeddings.py`, and `pa2/crawler/src/build_link_graph.py`. The single-command `run_pipeline.py` orchestrator was removed; each stage is invoked explicitly from the README.

## Assignment database dumps (new, 2026-05-08)

- **Status:** DONE
- **Evidence:**
  - `scripts/dump-assignment-dbs.sh` creates separate custom-format dumps for PA1 and PA2.
  - PA1 mode writes `pa1/db`, excludes PA2 vector/link-graph tables and operational `frontier_queue`, and excludes `image`/`page_data` table data as required by Assignment 1.
  - PA2 mode writes `pa2/extraction-db/crawldb_pa2.dump`, includes only the extraction restore payload, and excludes raw crawl `link`, `frontier_queue`, `image`, and `page_data` tables.
  - `.gitattributes` tracks `pa2/extraction-db/crawldb_pa2.dump` with Git LFS so GitHub/GitLab commits contain a pointer instead of a 710 MiB regular Git blob.
  - `.gitignore` ignores generic dump outputs while explicitly allowing required assignment artifacts (`pa1/db` and `pa2/extraction-db/crawldb_pa2.dump`).

### PA1 dump artifact

- **Verified:** `pa1/db` is PostgreSQL custom format with gzip compression, 66,625,839 bytes (64 MiB), under the 100 MB Assignment 1 limit.
- **Contents:** `crawldb` schema/data without `image` or `page_data` table data. Row counts in the artifact: 25,716 pages total (`HTML` 1,001, `BINARY` 16,786, `DUPLICATE` 27, `FRONTIER` 7,902), plus `site`, `page_type`, `data_type`, and `link` data.
- **Note:** this branch deliberately keeps the existing PA1 artifact instead of the accidental 921 MiB plain-text dump that had been introduced on the earlier `assignment2` history.

### PA2 dump artifact

- **Verified:** `pg_restore --list pa2/extraction-db/crawldb_pa2.dump` succeeds and shows PostgreSQL custom format with gzip compression. Tables: article_link_graph, data_type, page, page_segment_long, page_segment_short, page_type, site. vector extension included. IVFFlat indexes included.
- **Contents:** 10,432 pages total (`HTML` 10,429, `BINARY` 1, `DUPLICATE` 2), 5,893 pages with non-empty `cleaned_content`, 438,793 short segments with embeddings (article=51,189, forum=387,399, unknown=205), 36,816 long segments with embeddings (article=2,263, forum=34,402, unknown=151), 48,686 article-link graph edges, 6 data types, 4 page types, 1 site. `html_content` column is NULL in dump (excluded to keep artifact under ~2 GB).

## PA1 crawler improvements (new, 2026-05-08)

- **Status:** DONE
- **Evidence:**
  - `pa1/crawler/src/core/preferential.py` — rewrote scorer with MedOverNet article path signals (`/novica/`, `/clanek/`, `/zdravje/`, etc.) +50 boost, forum path signals +30, noise path −20, topic-keyword match +20. Fitness/wellness keywords updated to match actual domain focus.
  - `pa1/crawler/src/core/relevance.py` — same article/forum path signals wired into `score_url` (+30 article, +15 forum, −10 noise).
  - `pa1/crawler/src/core/config.py` — topic keywords updated to fitness/wellness focus; `relevance_allowed_domain_suffixes` defaults to `medover.net,zurnal24.si`.
  - `pa1/crawler/src/api/worker_service.py` — default group topic_keywords updated.
  - `ManagerApp/Components/Pages/WorkerConfig.razor` — presets updated (seeds include forum URL, keywords updated, allowed suffixes include medover.net/zurnal24.si).
  - `ManagerApp/Components/Pages/Workers.razor` — icon-only nav buttons changed to labeled buttons (Strategy / Config / Daemons).

## PA1 discrepancies still open (defense readiness)

- **PA1 artifact page count:** the committed `pa1/db` custom dump has 1,001 HTML pages, below the PA1 5,000-page guideline. The PA2 extraction dump contains 10,413 HTML pages and is the source used for PA2 retrieval evaluation.
- **LSH bonus (PA1 §2.1 BONUS):** Not implemented. Current dedup is exact SHA-256 only. Explicitly not claimed in README.
- **PA2 page payload presence:** all 10,413 HTML rows in the PA2 dump have page records; 779 yield non-empty `cleaned_content` after article filtering and are segmented.
- **Frontier / link / image / page_data tables:** migration 07 only touches `crawldb.page` (new columns) and creates new `page_segment_*` tables. PA1 semantics preserved. The PA2 dump intentionally excludes raw crawl `link`, `frontier_queue`, `image`, and `page_data` tables to keep the submission artifact focused on extraction and retrieval.

## Branch + merge accounting

- Base branch: `test_something` → forked to `assignment2` 2026-05-06.
- Files brought in from `extractor` branch (latest commit 45c9d34, 2026-05-05):
  - `db/migrations/07_page_cleaned_content_and_segments.sql`
  - `pa2/crawler/src/core/article_extractor.py`
  - `pa2/crawler/src/core/article_extractor_xpath.py`
  - `pa2/crawler/src/core/segmenter.py`
  - `pa2/crawler/src/fill_cleaned_content.py`
  - `pa2/crawler/src/segment_pages_to_db.py`
  - `pa2/crawler/src/compute_embeddings.py`
  - `pa2/report/report.tex` (base sections only; we extended with all missing sections)
- Import paths restored to the original `pa2/crawler/src/core/` package layout, with later forum/rerank/device/link-graph functionality kept in `pa2/crawler/src`.
- All behavioral changes from extractor's latest review commit (overlap_words=50, embedded_at fix, metadata enrichment) confirmed present in our versions.
- Files from `extractor` deliberately NOT brought in: `article_extraction_cli.py`, `article_extraction_validate.py` (debug tools), `insert_demo.py`, validation artifacts (temp), architecture docs (folded into README).
- Files we authored fresh: `article_extractor_regex.py`, `forum_extractor.py`, `demo.py`, `rerank_crossencoder.py`, `device_utils.py`, `build_link_graph.py`, `eval/queries.json`, `README.md`, `compliance.md`, `TODO.md`.
