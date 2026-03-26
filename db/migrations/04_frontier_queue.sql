CREATE TABLE IF NOT EXISTS crawldb.frontier_queue (
    id              bigserial PRIMARY KEY,
    canonical_url   varchar(3000) NOT NULL,
    priority        integer NOT NULL DEFAULT 0,
    source_url      varchar(3000),
    depth           integer NOT NULL DEFAULT 0,
    state           varchar(24) NOT NULL DEFAULT 'queued',
    discovered_at   timestamp NOT NULL DEFAULT now(),
    dequeued_at     timestamp,
    CONSTRAINT unq_frontier_queue_canonical_url UNIQUE (canonical_url),
    CONSTRAINT chk_frontier_queue_state CHECK (state IN ('queued', 'in_memory', 'processing', 'done', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_frontier_queue_state_priority
    ON crawldb.frontier_queue(state, priority DESC, discovered_at ASC);
