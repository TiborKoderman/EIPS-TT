-- Migration 07: pgvector + cleaned page content + segment embeddings
--
-- Purpose:
-- - Ensure pgvector extension is available
-- - Add cleaned plain-text column(s) to crawldb.page
-- - Create TWO segment tables (short: <=50 chars, long: ~250 words) to store text chunks
--   and their vector embeddings for similarity search.
--
-- Notes:
-- - This migration targets the existing schema name used in this project: `crawldb`.
-- - Embedding dimension is set to 768 to match common Transformer models (BERT-like).
--   If you later switch to a model with a different dimension, adjust the vector(N)
--   type in this migration (and rebuild indexes).

-- pgvector extension (requires a Postgres image with pgvector installed)
CREATE EXTENSION IF NOT EXISTS vector;

-- Add cleaned plain text content to crawldb.page
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'crawldb'
          AND table_name = 'page'
          AND column_name = 'cleaned_content'
    ) THEN
        ALTER TABLE crawldb.page
            ADD COLUMN cleaned_content text;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'crawldb'
          AND table_name = 'page'
          AND column_name = 'cleaned_content_hash'
    ) THEN
        ALTER TABLE crawldb.page
            ADD COLUMN cleaned_content_hash char(64);

        ALTER TABLE crawldb.page
            ADD CONSTRAINT chk_page_cleaned_content_hash_hex
            CHECK (cleaned_content_hash IS NULL OR cleaned_content_hash ~ '^[0-9a-f]{64}$');
    END IF;
END $$;

-- Short segments (<=50 characters; may cut mid-word)
CREATE TABLE IF NOT EXISTS crawldb.page_segment_short (
    id serial NOT NULL,
    page_id integer NOT NULL,
    page_type varchar(32) NOT NULL,
    segment_index integer NOT NULL,
    segment_text text NOT NULL,

    -- Retrieval/useful debugging metadata
    html_tag varchar(32),
    section_title text,
    heading text,
    heading_level smallint,

    -- Embedding storage
    embedding vector(768),
    embedding_model varchar(200) NOT NULL DEFAULT 'default',
    embedded_at timestamp,

    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp NOT NULL DEFAULT now(),

    CONSTRAINT pk_page_segment_short_id PRIMARY KEY (id),
    CONSTRAINT fk_page_page_segment_short FOREIGN KEY (page_id)
        REFERENCES crawldb.page(id)
        ON DELETE RESTRICT,
    CONSTRAINT chk_page_segment_short_page_type CHECK (page_type IN ('article', 'forum', 'listing', 'unknown')),
    CONSTRAINT unq_page_segment_short_per_page UNIQUE (page_id, segment_index, embedding_model)
);

-- Long segments (~250 words; keeps whole words)
CREATE TABLE IF NOT EXISTS crawldb.page_segment_long (
    id serial NOT NULL,
    page_id integer NOT NULL,
    page_type varchar(32) NOT NULL,
    segment_index integer NOT NULL,
    segment_text text NOT NULL,

    -- Retrieval/useful debugging metadata
    html_tag varchar(32),
    section_title text,
    heading text,
    heading_level smallint,

    -- Embedding storage
    embedding vector(768),
    embedding_model varchar(200) NOT NULL DEFAULT 'default',
    embedded_at timestamp,

    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp NOT NULL DEFAULT now(),

    CONSTRAINT pk_page_segment_long_id PRIMARY KEY (id),
    CONSTRAINT fk_page_page_segment_long FOREIGN KEY (page_id)
        REFERENCES crawldb.page(id)
        ON DELETE RESTRICT,
    CONSTRAINT chk_page_segment_long_page_type CHECK (page_type IN ('article', 'forum', 'listing', 'unknown')),
    CONSTRAINT unq_page_segment_long_per_page UNIQUE (page_id, segment_index, embedding_model)
);

-- Supporting indexes
CREATE INDEX IF NOT EXISTS idx_page_segment_short_page_id
    ON crawldb.page_segment_short(page_id);

CREATE INDEX IF NOT EXISTS idx_page_segment_long_page_id
    ON crawldb.page_segment_long(page_id);

CREATE INDEX IF NOT EXISTS idx_page_segment_short_page_type
    ON crawldb.page_segment_short(page_type);

CREATE INDEX IF NOT EXISTS idx_page_segment_long_page_type
    ON crawldb.page_segment_long(page_type);

CREATE INDEX IF NOT EXISTS idx_page_segment_short_page_id_segment_index
    ON crawldb.page_segment_short(page_id, segment_index);

CREATE INDEX IF NOT EXISTS idx_page_segment_long_page_id_segment_index
    ON crawldb.page_segment_long(page_id, segment_index);

CREATE INDEX IF NOT EXISTS idx_page_segment_short_embedding_model
    ON crawldb.page_segment_short(embedding_model);

CREATE INDEX IF NOT EXISTS idx_page_segment_long_embedding_model
    ON crawldb.page_segment_long(embedding_model);

-- Optional hybrid search support (keyword + vector)
CREATE INDEX IF NOT EXISTS idx_page_segment_short_tsv
    ON crawldb.page_segment_short
    USING gin (to_tsvector('simple', segment_text));

CREATE INDEX IF NOT EXISTS idx_page_segment_long_tsv
    ON crawldb.page_segment_long
    USING gin (to_tsvector('simple', segment_text));

-- Vector ANN indexes (cosine distance). Build only on rows that have an embedding.
-- NOTE: ivfflat performance depends on ANALYZE and `lists` tuning.
CREATE INDEX IF NOT EXISTS idx_page_segment_short_embedding_ivfflat
    ON crawldb.page_segment_short
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100)
    WHERE embedding IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_page_segment_long_embedding_ivfflat
    ON crawldb.page_segment_long
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100)
    WHERE embedding IS NOT NULL;
