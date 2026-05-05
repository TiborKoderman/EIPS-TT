# PA2: Segmentation, Extraction & Vector Storage Pipeline

This document describes the complete pipeline built to extract meaningful text from crawled HTML pages, intelligently segment it into cohesive chunks, and store those chunks alongside their vector embeddings in PostgreSQL.

## 1. The Pipeline Overview

The complete process bridges the gap between raw data collected in PA1 (stored in `crawldb.page`) and the RAG-ready vector database required in PA2. It consists of two sequential scripts:

1. **`segment_pages_to_db.py`**: Reads raw HTML, runs XPath-based extraction (to remove boilerplate), segments the cleaned text into both `short` and `long` chunks using logical natural-language boundaries, and saves the text + extracted metadata back into the database.
2. **`compute_embeddings.py`**: Scans the database for any new segments that lack an embedding, loads the multilingual `sentence-transformers/LaBSE` model, and computes the 768-dimensional `vector` arrays for fast similarity search.

---

## 2. Text Extraction & Cleanup

The extraction phase is handled iteratively via the `article_extractor_xpath.py` script. The primary objective is to clean the HTML pages and distill them into pure semantic text (`cleaned_content`) while optionally grabbing metadata.

### Process:
- **Boilerplate Removal:** Utilizing XPath queries, we explicitly strip structural noise such as `<header>`, `<footer>`, `<nav>`, `<aside>`, and elements containing noise classes/ids (like `widget`, `social`, `comment`).
- **Structured Content Identification:** The script explicitly attempts to capture exact `title` (`heading`), author context, and publication strings to satisfy the assignment's structured data expectations.
- **Result:** The clean NLP-ready string is mapped to the new column `crawldb.page.cleaned_content`, and its integrity is verified with a `cleaned_content_hash`.

---

## 3. Text Segmentation Strategies

Effective segmentation is crucial to avoid losing document context while keeping embedding operations accurate. To fulfill the homework guidelines, we developed two comparative segmenting strategies stored strictly in separate tables (`page_segment_short` and `page_segment_long`).

### 3.1 Short Segments (Fixed Slicing)
- **Constraints:** Up to 50 characters maximum.
- **Logic:** Arbitrarily cuts the text at exact character limits (irrespective of word bounds or grammar).
- **Purpose:** Acts as a density-focused control setup to pinpoint exceedingly precise keywords, but suffers immensely from "context loss" around the slice edges.

### 3.2 Long Segments (Hybrid Logical Chunking with Overlap)
- **Constraints:** Target of ~250 whole words.
- **Logic:** Instead of naive counting, this dynamically splits text by observing paragraph (`\n\n`) and sentence (`.!?`) boundaries.
- **Context Overlap:** To prevent any abrupt cutoff or loss of context between chunks, it retains an *overlap of 50 words*, sliding the context horizontally across the content.
- **Purpose:** Demonstrates an industry-grade approach to RAG text chunking, packing maximum descriptive context into a vector query.

---

## 4. The Database Subsystem (PGVector)

All this data has to efficiently interact with AI similarity operators. The database uses a Postgres backend with the `pgvector` extension enabled.

### Key Schema Upgrades
- `cleaned_content_hash`: Prevents duplicate extraction rounds on previously embedded HTML entries (Idempotency).
- **Metadata Hybrid Storage:** Data such as the author or publication date is serialized into a flexible `JSONB` metadata column alongside each segment chunk.
- **Vector Column:** Extracted text is transformed and safely mapped into a `vector(768)` row.
- **ANN Indexes (HNSW / IVFFlat):** The database optimizes vector retrieval by grouping similar context arrays into fast approximated maps instead of brutally scanning millions of rows sequentially.

---

## 5. How to Run the Pipeline on the Entire Dataset

To securely run the comprehensive NLP extraction + vectorization across all 5000+ PA1 pages without facing "Out of Memory" errors, we implemented a chunk-based execution loop.

### Command

```powershell
# Windows (PowerShell)
.\scripts\run-cleaned-segmentation-embeddings.ps1
```

### What happens under the hood?
1. The PowerShell script spins up `segment_pages_to_db.py` fetching 500 pages at a time.
2. It loops seamlessly until 0 new pages remain unprocessed.
3. Next, it triggers `compute_embeddings.py` which pulls down the `LaBSE` transformer model and processes all segments in manageable batch arrays of `100`, updating the specific `embedding` variable inside the SQL store.

