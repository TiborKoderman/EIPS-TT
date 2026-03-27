DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'crawldb'
          AND table_name = 'frontier_queue'
          AND column_name = 'finished_at'
    ) THEN
        ALTER TABLE crawldb.frontier_queue
            ADD COLUMN finished_at timestamp;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_frontier_queue_state'
          AND conrelid = 'crawldb.frontier_queue'::regclass
    ) THEN
        ALTER TABLE crawldb.frontier_queue
            DROP CONSTRAINT chk_frontier_queue_state;
    END IF;
END $$;

ALTER TABLE crawldb.frontier_queue
    ADD CONSTRAINT chk_frontier_queue_state
    CHECK (state IN (
        'queued',
        'in_memory',
        'locked',
        'processing',
        'completed',
        'done',
        'failed'
    ));

CREATE INDEX IF NOT EXISTS idx_frontier_queue_state_priority
    ON crawldb.frontier_queue(state, priority DESC, discovered_at ASC);

CREATE INDEX IF NOT EXISTS idx_frontier_queue_finished_at
    ON crawldb.frontier_queue(finished_at);
