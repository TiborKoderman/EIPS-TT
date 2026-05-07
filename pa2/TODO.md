# PA2 TODO (assignment2 branch)

Tracks every actionable item for `docs/Assignment2.md` compliance. See `pa2/compliance.md` for the per-requirement audit.

## A. Branch + DB prep

- [x] Fork `test_something` → `assignment2`
- [x] Create spec layout `pa2/{implementation-extraction/{demo.py,pipeline/,eval/{runs/}},extraction-db/,report/}`
- [x] Port migration `db/migrations/07_page_cleaned_content_and_segments.sql` from `extractor`
- [x] Extend `requirements.txt` with `html5lib`, `elementpath`, `sentence-transformers`, `pgvector`, `torch`, `transformers`
- [x] Restore `pa1/db` plain-SQL dump into local pgvector container (db `crawldb`) — 4,652 HTML pages confirmed
- [x] Apply migration 07 against the restored DB

## B. Extraction (Section 2)

- [x] XPath extractor ported (`pipeline/extractor_xpath.py`) — Section 2.1 DONE
- [x] Regex-only extractor authored (`pipeline/extractor_regex.py`) — Section 2.2 DONE
- [x] Run `pipeline/fill_cleaned_content.py` (full DB) — all 4,652 HTML pages processed; 779 have non-empty `cleaned_content`

## C. Segmentation + schema (Sections 2.3, 3.1)

- [x] Migration 07 creates dual `page_segment_short` + `page_segment_long` with `vector(768)` and IVFFlat cosine indexes — Section 3.1 DONE
- [x] `pipeline/segmenter.py` ported — short ≤50 char, long ~250 word, 50-word overlap
- [x] Run `pipeline/segment_pages.py` — 51,394 short + 2,414 long segments populated
- [x] Verified: `SELECT COUNT(*) FROM crawldb.page_segment_long` = 2,414; `page_segment_short` = 51,394

## D. Embeddings + ANN (Sections 3.2, 3.3)

- [x] `pipeline/compute_embeddings.py` ported with `--model-name` flag (default `sentence-transformers/LaBSE`)
- [x] Run `pipeline/compute_embeddings.py` — all 53,808 segments embedded with LaBSE
- [x] Verified: `SELECT COUNT(*) FROM crawldb.page_segment_long WHERE embedding IS NULL` = 0
- [x] IVFFlat `lists` corrected: short=227 (√51394≈227), long=49 (√2414≈49); indexes recreated + ANALYZE run
- [ ] (Section 3.2 alt-model experiment) qualitative comparison with secondary model — noted in report as "methods tried but not used"; no separate DB run performed (384-d model incompatible with vector(768) schema)

## E. Demo retriever (Section 4)

- [x] `pa2/implementation-extraction/demo.py` — flags: `--query`, `--queries-file`, `--top-k`, `--metric`, `--table`, `--rerank`, `--model-name`, saves runs
- [x] `eval/queries.json` — 3 good + 3 bad Slovenian queries
- [x] Baseline run: `eval/runs/20260506T143228Z_baseline.json` — 6 queries × top-5 results saved
- [x] Verified: all 6 queries return 5 results; good queries return relevant Slovenian medical segments

## F. Reranker (Section 4.1)

- [x] `pipeline/rerank_crossencoder.py` — `BAAI/bge-reranker-v2-m3`
- [x] Reranked run: `eval/runs/20260506T143525Z_rerank.json` — 6 queries × top-5 results with rerank scores
- [x] Reranker demonstrably improves good queries (insomnia: rank-1 cosine 0.465 → rerank 0.991 on exact topic article)
- [x] Remaining failure documented: legal query — all rerank scores ≈0.0001 (no legal content in corpus)

## G. Report (Section 6.1)

- [x] `pa2/report/report.tex` — complete: intro, website filtering, boilerplate removal, regex comparison, segmentation, schema, similarity metric rationale, ANN index rationale, query examples table (real results), reranker section (real before/after), remaining failure, limitations, methods tried but not used, conclusion
- [x] Build `pa2/report-extraction.pdf` via `latexmk` — 5 pages, 131 kB

## H. Submission packaging (Section 6)

- [x] `pa2/extraction-db/crawldb_pa2.dump` — pg_dump custom-format, 427 MB, includes page + both segment tables + all indexes
- [x] `pa2/README.md` — setup, DB restore, pipeline run order, demo usage, PA1 notes
- [x] `tree pa2/` matches Section 6 spec layout (extra subdirs `pipeline/`, `eval/` inside `implementation-extraction/` are permitted)
- [ ] (manual user step) confirm GitHub user `opbieps` has read access on the private repo

## I. PA1 discrepancies (defense readiness)

- [x] HTML page count verified: 4,652 (dump truncated at COPY row 926,047 — below 5,000 guideline; documented in README and report §Limitations)
- [x] `html_content` non-null verified: all 4,652 HTML rows have content; 779 yield non-empty `cleaned_content` after article filtering
- [x] LSH bonus: not implemented — explicitly noted as not-claimed in README
- [x] PA1 tables untouched: migration 07 only adds columns to `crawldb.page` and creates new `page_segment_*` tables
