-- Frontier queue support using crawldb.page rows with
-- page_type_code = 'FRONTIER'.
--
-- priority + due time + claiming (lease) so multiple workers can safely pick the next URL

ALTER TABLE crawldb.page
  ADD COLUMN IF NOT EXISTS frontier_priority double precision,
  ADD COLUMN IF NOT EXISTS frontier_depth integer,
  ADD COLUMN IF NOT EXISTS frontier_created_at timestamp,
  ADD COLUMN IF NOT EXISTS frontier_next_fetch_time timestamp,
  ADD COLUMN IF NOT EXISTS frontier_source_page_id integer,
  ADD COLUMN IF NOT EXISTS frontier_claimed_by varchar(100),
  ADD COLUMN IF NOT EXISTS frontier_claimed_at timestamp;

UPDATE crawldb.page
SET frontier_priority = COALESCE(frontier_priority, 0.0),
    frontier_depth = COALESCE(frontier_depth, 0),
    frontier_created_at = COALESCE(frontier_created_at, NOW()),
    frontier_next_fetch_time = COALESCE(frontier_next_fetch_time, NOW())
WHERE page_type_code = 'FRONTIER';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'fk_frontier_source_page'
  ) THEN
    ALTER TABLE crawldb.page
      ADD CONSTRAINT fk_frontier_source_page
      FOREIGN KEY (frontier_source_page_id)
      REFERENCES crawldb.page(id)
      ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_frontier_pick
  ON crawldb.page (frontier_next_fetch_time ASC, frontier_priority DESC, id ASC)
  WHERE page_type_code = 'FRONTIER';



