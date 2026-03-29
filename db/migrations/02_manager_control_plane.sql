CREATE SCHEMA IF NOT EXISTS manager;

CREATE TABLE IF NOT EXISTS manager.daemon (
    id                      serial PRIMARY KEY,
    name                    varchar(120) NOT NULL,
    mode                    varchar(32) NOT NULL DEFAULT 'local',
    endpoint_url            varchar(512),
    is_local                boolean NOT NULL DEFAULT true,
    is_enabled              boolean NOT NULL DEFAULT true,
    status                  varchar(32) NOT NULL DEFAULT 'stopped',
    auth_token_hash         varchar(256),
    metadata                jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at              timestamp NOT NULL DEFAULT now(),
    updated_at              timestamp NOT NULL DEFAULT now(),
    last_seen_at            timestamp,
    CONSTRAINT chk_manager_daemon_mode
        CHECK (mode IN ('local', 'remote')),
    CONSTRAINT unq_manager_daemon_name UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS manager.worker (
    id                      bigserial PRIMARY KEY,
    daemon_id               integer NOT NULL REFERENCES manager.daemon(id) ON DELETE CASCADE,
    external_worker_id      integer,
    name                    varchar(120) NOT NULL,
    status                  varchar(32) NOT NULL DEFAULT 'idle',
    current_url             varchar(3000),
    pages_processed         integer NOT NULL DEFAULT 0,
    error_count             integer NOT NULL DEFAULT 0,
    started_at              timestamp,
    last_heartbeat_at       timestamp,
    runtime_config          jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata                jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at              timestamp NOT NULL DEFAULT now(),
    updated_at              timestamp NOT NULL DEFAULT now(),
    CONSTRAINT unq_manager_worker_daemon_external UNIQUE (daemon_id, external_worker_id)
);

CREATE TABLE IF NOT EXISTS manager.global_setting (
    key                     varchar(160) PRIMARY KEY,
    value                   jsonb NOT NULL,
    updated_at              timestamp NOT NULL DEFAULT now(),
    updated_by              varchar(120)
);

CREATE TABLE IF NOT EXISTS manager.daemon_setting (
    daemon_id               integer NOT NULL REFERENCES manager.daemon(id) ON DELETE CASCADE,
    key                     varchar(160) NOT NULL,
    value                   jsonb NOT NULL,
    updated_at              timestamp NOT NULL DEFAULT now(),
    updated_by              varchar(120),
    PRIMARY KEY (daemon_id, key)
);

CREATE TABLE IF NOT EXISTS manager.api_token (
    id                      bigserial PRIMARY KEY,
    token_id                varchar(64) NOT NULL,
    daemon_id               integer REFERENCES manager.daemon(id) ON DELETE CASCADE,
    name                    varchar(120) NOT NULL,
    token_hash              varchar(256) NOT NULL,
    scope                   varchar(64) NOT NULL DEFAULT 'daemon',
    created_at              timestamp NOT NULL DEFAULT now(),
    expires_at              timestamp,
    last_used_at            timestamp,
    revoked_at              timestamp,
    CONSTRAINT unq_manager_api_token_id UNIQUE (token_id),
    CONSTRAINT unq_manager_api_token_hash UNIQUE (token_hash)
);

CREATE TABLE IF NOT EXISTS manager.command (
    id                      bigserial PRIMARY KEY,
    daemon_id               integer NOT NULL REFERENCES manager.daemon(id) ON DELETE CASCADE,
    worker_id               bigint REFERENCES manager.worker(id) ON DELETE SET NULL,
    command_type            varchar(64) NOT NULL,
    payload                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    status                  varchar(32) NOT NULL DEFAULT 'queued',
    error_message           text,
    created_at              timestamp NOT NULL DEFAULT now(),
    dispatched_at           timestamp,
    acknowledged_at         timestamp,
    completed_at            timestamp,
    CONSTRAINT chk_manager_command_status
        CHECK (status IN ('queued', 'dispatched', 'acknowledged', 'completed', 'failed', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_manager_worker_daemon_id ON manager.worker(daemon_id);
CREATE INDEX IF NOT EXISTS idx_manager_worker_status ON manager.worker(status);
CREATE INDEX IF NOT EXISTS idx_manager_daemon_status ON manager.daemon(status);
CREATE INDEX IF NOT EXISTS idx_manager_command_daemon_status ON manager.command(daemon_id, status);
CREATE INDEX IF NOT EXISTS idx_manager_command_created_at ON manager.command(created_at);
CREATE INDEX IF NOT EXISTS idx_manager_api_token_daemon_id ON manager.api_token(daemon_id);

INSERT INTO manager.daemon (name, mode, endpoint_url, is_local, is_enabled, status, metadata)
VALUES ('Local Daemon', 'local', 'http://127.0.0.1:8090', true, true, 'stopped', '{"bootstrapWorkerCount":1,"daemonId":"local-default"}'::jsonb)
ON CONFLICT (name) DO NOTHING;

UPDATE manager.daemon
SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"daemonId":"local-default"}'::jsonb,
        updated_at = now()
WHERE name = 'Local Daemon'
    AND COALESCE(metadata->>'daemonId', '') = '';

INSERT INTO manager.global_setting (key, value, updated_by)
VALUES
    ('daemon.default_worker_count', '1'::jsonb, 'migration-02'),
    ('daemon.default_mode', '"local"'::jsonb, 'migration-02')
ON CONFLICT (key) DO NOTHING;
