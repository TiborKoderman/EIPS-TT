-- Migration 06: Frontier Queue Redesign with Enum States and Duplicate Tracking
-- Purpose: Convert frontier_queue state column to use enum type for type safety,
--          add duplicate tracking, implement memory caching flag, optimize indexes
--          for heap-like queue behavior with priority ordering.

-- Create enum type for frontier queue states
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'frontier_queue_state') THEN
        CREATE TYPE crawldb.frontier_queue_state AS ENUM (
            'QUEUED',       -- Waiting in queue, not yet locked
            'LOCKED',       -- Acquired by worker, but not yet processing
            'PROCESSING',   -- Worker is actively processing
            'COMPLETED',    -- Successfully processed
            'DUPLICATE',    -- Marked as duplicate of another URL
            'FAILED'        -- Processing failed
        );
    END IF;
END $$;

-- Add new columns to frontier_queue for enhanced tracking
DO $$
BEGIN
    -- Add duplicate_of_url_id to track duplicate relationships
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'crawldb'
          AND table_name = 'frontier_queue'
          AND column_name = 'duplicate_of_url_id'
    ) THEN
        ALTER TABLE crawldb.frontier_queue
            ADD COLUMN duplicate_of_url_id bigint;

        -- Add foreign key constraint for duplicate relationships (self-referential)
        ALTER TABLE crawldb.frontier_queue
            ADD CONSTRAINT fk_frontier_duplicate_of
            FOREIGN KEY (duplicate_of_url_id)
            REFERENCES crawldb.frontier_queue(id)
            ON DELETE SET NULL;
    END IF;
END $$;

-- Add memory_cached flag for memory cache tracking
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'crawldb'
          AND table_name = 'frontier_queue'
          AND column_name = 'memory_cached'
    ) THEN
        ALTER TABLE crawldb.frontier_queue
            ADD COLUMN memory_cached boolean NOT NULL DEFAULT false;
    END IF;
END $$;

-- Add locked_at and locked_by for lease tracking
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'crawldb'
          AND table_name = 'frontier_queue'
          AND column_name = 'locked_at'
    ) THEN
        ALTER TABLE crawldb.frontier_queue
            ADD COLUMN locked_at timestamp;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'crawldb'
          AND table_name = 'frontier_queue'
          AND column_name = 'locked_by_worker_id'
    ) THEN
        ALTER TABLE crawldb.frontier_queue
            ADD COLUMN locked_by_worker_id integer;
    END IF;
END $$;

-- Migrate existing state strings to enum, mapping old states to new states
DO $$
BEGIN
    -- Rename current state column to avoid conflicts
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'crawldb'
          AND table_name = 'frontier_queue'
          AND column_name = 'state'
          AND data_type = 'character varying'
    ) THEN
        ALTER TABLE crawldb.frontier_queue
            RENAME COLUMN state TO state_str;

        -- Create new state column with enum type
        ALTER TABLE crawldb.frontier_queue
            ADD COLUMN state crawldb.frontier_queue_state NOT NULL DEFAULT 'QUEUED';

        -- Migrate data: map old state strings to new enum values
        UPDATE crawldb.frontier_queue
        SET state = CASE state_str
            WHEN 'queued' THEN 'QUEUED'::crawldb.frontier_queue_state
            WHEN 'in_memory' THEN 'QUEUED'::crawldb.frontier_queue_state
            WHEN 'locked' THEN 'LOCKED'::crawldb.frontier_queue_state
            WHEN 'processing' THEN 'PROCESSING'::crawldb.frontier_queue_state
            WHEN 'completed' THEN 'COMPLETED'::crawldb.frontier_queue_state
            WHEN 'done' THEN 'COMPLETED'::crawldb.frontier_queue_state
            WHEN 'failed' THEN 'FAILED'::crawldb.frontier_queue_state
            ELSE 'QUEUED'::crawldb.frontier_queue_state
        END;

        -- Drop old state_str column and its constraints
        ALTER TABLE crawldb.frontier_queue
            DROP CONSTRAINT IF EXISTS chk_frontier_queue_state;
        ALTER TABLE crawldb.frontier_queue
            DROP COLUMN state_str;
    END IF;
END $$;

-- Ensure state column is enum type (for idempotency)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'crawldb'
          AND table_name = 'frontier_queue'
          AND column_name = 'state'
          AND data_type = 'character varying'
    ) THEN
        ALTER TABLE crawldb.frontier_queue
            ALTER COLUMN state TYPE crawldb.frontier_queue_state
            USING state::crawldb.frontier_queue_state;
    END IF;
END $$;

-- Drop old indexes and create optimized indexes for heap-like priority queue behavior
DROP INDEX IF EXISTS crawldb.idx_frontier_queue_state_priority;
DROP INDEX IF EXISTS crawldb.idx_frontier_queue_finished_at;

-- Primary working index: scans for next item efficiently
-- QUEUED items first (highest priority), ordered by priority DESC, then discovered_at ASC
CREATE INDEX IF NOT EXISTS idx_frontier_queue_priority_heap
    ON crawldb.frontier_queue(state, priority DESC, discovered_at ASC)
    WHERE state IN ('QUEUED', 'LOCKED');

-- Secondary index for duplicate detection
CREATE INDEX IF NOT EXISTS idx_frontier_queue_duplicate
    ON crawldb.frontier_queue(state, duplicate_of_url_id)
    WHERE state = 'DUPLICATE';

-- Tertiary index for completed/failed tracking
CREATE INDEX IF NOT EXISTS idx_frontier_queue_finished
    ON crawldb.frontier_queue(state, finished_at DESC)
    WHERE state IN ('COMPLETED', 'FAILED');

-- Cache visibility index
CREATE INDEX IF NOT EXISTS idx_frontier_queue_memory_cached
    ON crawldb.frontier_queue(memory_cached, state);

-- Lock tracking index for lease management
CREATE INDEX IF NOT EXISTS idx_frontier_queue_locked_at
    ON crawldb.frontier_queue(locked_at DESC)
    WHERE state = 'LOCKED';

-- Constraint to enforce non-null priorities
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_priority_non_null'
          AND conrelid = 'crawldb.frontier_queue'::regclass
    ) THEN
        ALTER TABLE crawldb.frontier_queue
            ADD CONSTRAINT chk_priority_non_null CHECK (priority IS NOT NULL);
    END IF;
END $$;

-- Ensure URLs are properly indexed for lookups
CREATE INDEX IF NOT EXISTS idx_frontier_queue_url_hash
    ON crawldb.frontier_queue(url);

COMMENT ON TABLE crawldb.frontier_queue IS
    'Frontier URL queue with enum states, priority-based heap ordering, and memory caching support for efficient crawling.';

COMMENT ON COLUMN crawldb.frontier_queue.state IS
    'Queue state: QUEUED (waiting), LOCKED (acquired by worker), PROCESSING (active), COMPLETED/DUPLICATE/FAILED (terminal).';

COMMENT ON COLUMN crawldb.frontier_queue.duplicate_of_url_id IS
    'If state=DUPLICATE, references the URL ID this was a duplicate of. Link edge still recorded.';

COMMENT ON COLUMN crawldb.frontier_queue.memory_cached IS
    'Flag for memory cache layer to track which items are in-memory vs. database-only.';

COMMENT ON COLUMN crawldb.frontier_queue.locked_at IS
    'Timestamp when item transitioned to LOCKED state; used for lease expiration.';

COMMENT ON COLUMN crawldb.frontier_queue.locked_by_worker_id IS
    'Worker ID that holds the current lock; used for reassignment on worker failure.';

-- Vacuum and optimize
VACUUM ANALYZE crawldb.frontier_queue;
