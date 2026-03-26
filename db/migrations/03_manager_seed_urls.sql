CREATE TABLE IF NOT EXISTS manager.seed_url (
    id                  bigserial PRIMARY KEY,
    daemon_id           integer NOT NULL REFERENCES manager.daemon(id) ON DELETE CASCADE,
    external_worker_id  integer,
    url                 varchar(3000) NOT NULL,
    created_at          timestamp NOT NULL DEFAULT now(),
    is_active           boolean NOT NULL DEFAULT true,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT unq_manager_seed_url UNIQUE (daemon_id, external_worker_id, url)
);

CREATE INDEX IF NOT EXISTS idx_manager_seed_url_daemon_worker
    ON manager.seed_url(daemon_id, external_worker_id);

CREATE INDEX IF NOT EXISTS idx_manager_seed_url_active
    ON manager.seed_url(is_active);
