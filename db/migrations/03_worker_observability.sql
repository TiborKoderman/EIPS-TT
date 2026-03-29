CREATE TABLE IF NOT EXISTS manager.worker_log (
    id                  bigserial PRIMARY KEY,
    daemon_identifier   varchar(120) NOT NULL,
    external_worker_id  integer,
    level               varchar(16) NOT NULL DEFAULT 'Info',
    message             text NOT NULL,
    payload             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamp NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_manager_worker_log_created_at
    ON manager.worker_log(created_at);

CREATE INDEX IF NOT EXISTS idx_manager_worker_log_worker_created_at
    ON manager.worker_log(external_worker_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_manager_worker_log_level_created_at
    ON manager.worker_log(level, created_at DESC);

CREATE TABLE IF NOT EXISTS manager.worker_metric (
    id                  bigserial PRIMARY KEY,
    daemon_identifier   varchar(120) NOT NULL,
    external_worker_id  integer,
    metric_name         varchar(80) NOT NULL,
    metric_value        double precision NOT NULL,
    payload             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamp NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_manager_worker_metric_created_at
    ON manager.worker_metric(created_at);

CREATE INDEX IF NOT EXISTS idx_manager_worker_metric_worker_created_at
    ON manager.worker_metric(external_worker_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_manager_worker_metric_name_created_at
    ON manager.worker_metric(metric_name, created_at DESC);
