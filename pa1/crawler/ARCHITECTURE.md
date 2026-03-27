# Crawler architecture (frontier, parsing, relevance, preferential crawling)

This document explains how the crawler in `pa1/crawler/src/` is structured and how the **preferential, multi-worker crawl loop** works end-to-end.

## TL;DR pipeline

Per URL lifecycle (single worker):

1. **Claim** next URL from the **DB-backed frontier** (highest score first) using `FOR UPDATE SKIP LOCKED`.
2. **Pre-check**: if the URL was already crawled (page already `HTML/BINARY/DUPLICATE`), do **not** re-download; only create/update link metadata.
3. **Robots + politeness** gating:
   - parse/cache `robots.txt` rules
   - rate limit per IP/domain (≥ 5s or `Crawl-delay`)
4. **Download / render** (HTML always; PDF only where allowed/required by config; other binary types are not stored as bytes).
5. **Dedup** using exact content hashing.
6. **Persist** page data (`page` + `page_data` where applicable), set final `page_type`.
7. **Parse** outgoing links + images from HTML.
8. **Score** discovered URLs (preferential policy) and **enqueue** them into the frontier.

Key components:

- Frontier/queue + concurrency: `src/db/frontier_store.py`, `src/db/page_store.py`
- Worker loop / orchestration: `src/crawler/core/scheduler.py`, `src/crawler/run_crawler.py`
- Downloading: `src/crawler/core/downloader.py`, `src/crawler/core/renderer.py`
- Parsing: `src/crawler/core/parser.py`
- Canonicalization: `src/utils/url_canonicalizer.py`
- Relevance + scoring: `src/crawler/core/relevance.py`
- Robots + politeness: `src/crawler/core/robots.py`, `src/crawler/core/politeness.py`
- Dedup/hashing utilities: `src/utils/dedup.py`

---

## Entry points and control flow

### `src/main.py`
Top-level entry to start the crawler from CLI / scripts. It constructs configuration and starts the crawler runner.

### `src/crawler/run_crawler.py`
Creates N workers (threads in this implementation). Each worker has:

- its **own DB connection** (important for Postgres locks / transactions)
- shared, thread-safe helpers for robots cache + rate limiter

### `src/crawler/core/scheduler.py`
Contains the **worker loop** and the orchestration around:

- claiming the next item from frontier
- enforcing robots/politeness
- downloading
- storing results
- parsing and enqueuing new URLs

---

## URL frontier = queue in the existing `page` table

The assignment allows using table `page` as the frontier storage. In this codebase we do that: a URL is considered “in the queue” when its `page_type` indicates frontier membership.

### Frontier semantics

- A URL with `page_type = 'FRONTIER'` is waiting to be processed.
- One worker at a time can take ownership of a frontier row (“lease/claim”).
- Preferential crawling is implemented by **ordering the frontier by score** (highest first).

### How ordering (preferential strategy) works

When selecting a next URL, the store performs:

- `WHERE page_type = 'FRONTIER'`
- `ORDER BY score DESC` (and typically a tiebreaker like `created_at ASC`)

This makes more relevant URLs get crawled first.

### Thread/process safety (no two workers get the same URL)

When multiple workers are running, safe claiming is achieved by a **single SQL transaction** that:

1. selects a candidate row with

   `FOR UPDATE SKIP LOCKED`

2. immediately marks it as **in-progress** (either by `page_type='PROCESSING'` or an `in_progress=true` flag)

Because Postgres locks the selected row and other workers *skip locked rows*, it is impossible for two workers to process the same frontier URL.

### No-duplicate URL inserts (even with multiple workers)

Canonical URL duplicates are prevented by:

- canonicalizing every discovered URL (see section “Canonicalization”)
- relying on DB uniqueness on `page.url` (recommended) and `INSERT ... ON CONFLICT DO NOTHING`

This ensures that even if multiple workers discover the same URL at the same time, only one insert succeeds.

---

## Canonicalization and URL normalization

- Normalization of relative URLs happens in the parser layer using `urljoin(current_url, extracted_url)`.
- Canonicalization happens in `src/utils/url_canonicalizer.py`.

---

## Parsing: extracting links and images

Implemented in: `src/crawler/core/parser.py`

#### Outgoing URLs
We extract links from:

- `<a href="...">`
- simple `onclick` patterns that contain navigation (e.g. `location.href = '...'` / `document.location = '...'`)

Each extracted URL is:

1. normalized (relative → absolute)
2. canonicalized
3. filtered (e.g., skip empty URLs, non-http schemes)

#### Images
Images are detected only from:

- `<img src="...">`

### Output of parsing
The parser outputs structured data used by:

- link insertion (store `from_page → to_page`)
- frontier enqueue (new URLs)

---

## Relevance + preferential scoring

Implemented in: `src/crawler/core/relevance.py`

- we assign each discovered URL a **score** (priority)
- the frontier always returns the **highest score first**

### Scoring policy (typical factors)

The scoring function is domain-specific, but commonly combines:

- **keyword boosts** (URL path contains topic keywords)
- **scope boosts** (URL matches allowed sections like `/zdravje/`)
- **penalty for irrelevant types** (pdf/news/other)
- **depth penalty** (prefer closer pages)
- **same-domain bonus**

The score is stored into the `page` row while it is in the frontier.

Then `get_next_frontier_url()` (frontier claim) orders by `score DESC`, so the crawl focuses on relevant parts of the site first.

---

## Downloading + content-type handling

Implemented in:

- `src/crawler/core/downloader.py`
- `src/crawler/core/renderer.py` (when JS rendering/headless is enabled)

### HTML vs BINARY vs DUPLICATE vs FRONTIER

These are **page types** (assignment’s page_type codes):

- `FRONTIER`: URL waiting in queue
- `HTML`: downloaded and stored as HTML
- `BINARY`: non-HTML content (PDF/images/office docs etc.)
- `DUPLICATE`: content duplicate (store link to original, no html content)

### PDF downloading “only where required”

Per instructions: download HTML content only, **and PDF where required for the domain**.

This project therefore should:

- optionally allow PDF downloads via a **config allowlist / flag**
- otherwise, mark PDF URLs as `BINARY` (store metadata but not bytes)

---

## Deduplication (exact hash)

Implemented in: `src/utils/dedup.py` (and integrated via `src/db/page_store.py`)

- computes a hash (e.g. SHA-256) of HTML
- if the same hash already exists for another page:
  - mark new page as `DUPLICATE`
  - store pointer/link to the original

### Important nuance from the assignment

> If your crawler gets a URL from a frontier that has already been parsed, this is not treated as a duplicate.

Meaning:

- duplication is about **content equivalence**, not “URL already known”
- if URL is already `HTML` (already crawled), you should not re-download it; just record/link structure

---

## Worker model and concurrency

### Multi-worker setup

Workers run in parallel (threads). Each worker repeatedly:

- claims from frontier (DB transaction)
- processes URL
- enqueues new URLs

### Why DB locks (not Python locks) solve the frontier problem

Because the frontier is in PostgreSQL:

- worker threads/processes can be on different machines
- `FOR UPDATE SKIP LOCKED` is the robust concurrency primitive

### Politeness and robots caching are still shared resources

Even with DB-based frontier, you still need in-process synchronization for:

- robots cache (avoid fetching robots.txt from multiple threads at once)
- rate limiting per IP/domain

That’s handled by thread-safe structures in `robots.py` and `politeness.py`.

---

## Configuration: what we can tune and what it affects

Implemented in: `src/crawler/core/config.py`

Typical knobs:

- `CRAWLER_WORKERS`: number of parallel workers
  - higher = faster, but higher risk of being blocked if politeness not respected
- `CRAWLER_MIN_DELAY`: minimum delay between requests to same IP/domain
  - must be **≥ 5 seconds** (assignment requirement)
- `USER_AGENT`: the name of our agent for robots.txt
- timeouts and retry counts
  - too low → many failures
  - too high → workers get “stuck”
- PDF policy:
  - whether PDFs are allowed/required and for which hosts

---