ALTER TABLE crawldb.page
  ADD COLUMN IF NOT EXISTS content_hash char(64),
  ADD COLUMN IF NOT EXISTS duplicate_of_page_id integer;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'crawldb'
      AND table_name = 'page'
      AND column_name = 'canonical_url'
  ) THEN
    EXECUTE '
      UPDATE crawldb.page
      SET url = canonical_url
      WHERE url IS NULL
        AND canonical_url IS NOT NULL
    ';
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'crawldb'
      AND table_name = 'page'
      AND column_name = 'duplicate_of'
  ) THEN
    EXECUTE '
      UPDATE crawldb.page
      SET duplicate_of_page_id = COALESCE(duplicate_of_page_id, duplicate_of)
      WHERE duplicate_of IS NOT NULL
    ';
  END IF;
END $$;

ALTER TABLE crawldb.page DROP CONSTRAINT IF EXISTS unq_page_canonical_url;
ALTER TABLE crawldb.page DROP CONSTRAINT IF EXISTS chk_page_canonical_url_no_fragment;
ALTER TABLE crawldb.page DROP CONSTRAINT IF EXISTS page_duplicate_of_fkey;
ALTER TABLE crawldb.page DROP COLUMN IF EXISTS canonical_url;
ALTER TABLE crawldb.page DROP COLUMN IF EXISTS duplicate_of;

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
    WHERE conname = 'chk_page_url_no_fragment'
  ) THEN
    ALTER TABLE crawldb.page
      ADD CONSTRAINT chk_page_url_no_fragment
      CHECK (url IS NULL OR position('#' in url) = 0);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_page_content_hash ON crawldb.page (content_hash);
CREATE INDEX IF NOT EXISTS idx_page_duplicate_of_page_id ON crawldb.page (duplicate_of_page_id);
