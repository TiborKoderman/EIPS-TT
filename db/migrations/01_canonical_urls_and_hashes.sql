ALTER TABLE crawldb.page
  ADD COLUMN IF NOT EXISTS canonical_url varchar(3000),
  ADD COLUMN IF NOT EXISTS content_hash char(64),
  ADD COLUMN IF NOT EXISTS duplicate_of_page_id integer;

UPDATE crawldb.page
SET canonical_url = url
WHERE canonical_url IS NULL
  AND url IS NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'unq_page_canonical_url'
  ) THEN
    ALTER TABLE crawldb.page
      ADD CONSTRAINT unq_page_canonical_url UNIQUE (canonical_url);
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'fk_page_duplicate_of_page'
  ) THEN
    ALTER TABLE crawldb.page
      ADD CONSTRAINT fk_page_duplicate_of_page
      FOREIGN KEY (duplicate_of_page_id)
      REFERENCES crawldb.page(id)
      ON DELETE SET NULL;
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'chk_page_content_hash_hex'
  ) THEN
    ALTER TABLE crawldb.page
      ADD CONSTRAINT chk_page_content_hash_hex
      CHECK (content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'chk_page_canonical_url_no_fragment'
  ) THEN
    ALTER TABLE crawldb.page
      ADD CONSTRAINT chk_page_canonical_url_no_fragment
      CHECK (canonical_url IS NULL OR position('#' in canonical_url) = 0);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_page_content_hash ON crawldb.page (content_hash);
CREATE INDEX IF NOT EXISTS idx_page_duplicate_of_page_id ON crawldb.page (duplicate_of_page_id);
