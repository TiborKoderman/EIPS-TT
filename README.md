# EIPS-TT - Preferential Crawler (PA1)

## Manager GUI Docs

For standalone setup and operation of the Blazor manager app, see [ManagerApp/README.md](ManagerApp/README.md).

Single-compose setup with two modes:
- host mode: local `.venv` + `db` service
- devcontainer mode: `app` + `db` services

`docker-compose.yml` is shared by both modes. If you only want host mode, do not start `app`.

## Recommended Workflow

```bash
# restore python env from requirements.txt
bash scripts/venv-restore.sh

# start postgres and apply migrations
bash scripts/db-migrate.sh
```

When dependencies change:

```bash
# save current .venv packages back to requirements.txt
bash scripts/venv-dump.sh
```

## Host Mode (No Devcontainer)

```bash
bash scripts/venv-restore.sh
source .venv/bin/activate
docker compose up -d db
bash scripts/db-migrate.sh
python pa1/crawler/src/db/pg_connect.py
```

## Devcontainer Mode (Optional)

1. Open this repository folder in VS Code.
2. Run `Dev Containers: Rebuild and Reopen in Container`.
3. The container uses `/usr/local/bin/python`, while host mode uses `.venv/bin/python`.

Both interpreter paths are only active in their own context.

Non-VS Code equivalent (for quick container checks):

```bash
docker compose --profile devcontainer up -d app
```

## Database Connection

The repo does not assume `5432` is free. On first bootstrap it picks an available host port,
stores it in `.env.local`, and publishes PostgreSQL there.

The normal local workflow is:

```bash
./scripts/bootstrap.sh
./scripts/db-info.sh
./scripts/run-manager.sh
```

`./scripts/db-info.sh` prints the exact `Host`, `Port`, `Database`, `Username`, and `Password`
to use from DBeaver or any other external DB tool.

On this machine right now the repo DB is on `localhost:5433`, not `5432`, because `5432`
was already occupied by another local PostgreSQL instance.

## Utility Commands

```bash
# full setup shortcut (.venv + db + migrations)
bash scripts/bootstrap.sh

# reset schema, then re-apply migrations
bash scripts/reset-db.sh

# full DB reset (drops compose volumes), then re-apply migrations
bash scripts/reset-db.sh --clean

# print the exact external PostgreSQL connection settings and verify them
bash scripts/db-info.sh

# db logs
docker compose logs -f db
```

## Standalone Crawler Preset

For quick local crawler testing without ManagerApp orchestration, use:

```bash
bash scripts/crawler-standalone-preset.sh frontier
```

Supported modes:

- `frontier`: runs preferential frontier demo with sensible health/government seeds
- `crawl-once`: executes one crawl pipeline pass using preset runtime variables
- `api`: starts daemon API server with standalone preset runtime variables

Examples:

```bash
bash scripts/crawler-standalone-preset.sh crawl-once
bash scripts/crawler-standalone-preset.sh api
```

## Daemon Control Plane

The current architecture is:

- manager server hosts the HTTP and websocket control plane
- daemons are client-only processes that connect outbound to the manager
- workers live under a daemon; workers do not host their own web servers
- local development auto-starts one default local daemon from `ManagerApp`

That means you should not expect a daemon HTTP API on `127.0.0.1:8090`.
The manager listens on `http://127.0.0.1:5160` by default, and the local daemon connects to:

- manager websocket endpoint: `/api/daemon-channel`
- manager ingest endpoint: `/api/crawler/ingest`
- manager events endpoint: `/api/crawler/events`

For normal local development, just run:

```bash
./scripts/run-manager.sh
```

If you need to start a daemon manually for debugging, point it at the manager websocket:

```bash
CRAWLER_DAEMON_ID=local-default \
MANAGER_DAEMON_WS_URL=ws://127.0.0.1:5160/api/daemon-channel?daemonId=local-default \
.venv/bin/python pa1/crawler/src/daemon/main.py
```

Because the daemon initiates the connection, the daemon host does not need inbound port forwarding.
The manager/server side still needs to be reachable.

### Command dispatch path

Manager command actions (`start/pause/stop/reload`) are written to `manager.command` as queued rows.
A background dispatcher in `ManagerApp` sends queued commands over websocket to connected daemons, then marks them as dispatched.

Control flow:

1. UI/API action inserts queued command in `manager.command`
2. `CommandDispatchHostedService` polls queue
3. `DaemonChannelService` pushes command over `/api/daemon-channel`
4. Daemon executes command and reports via heartbeat/status APIs

## Optional Devcontainer Notes

See `.devcontainer/NOTE.md`.

## Docker Volume Upgrade Note

If `postgres:latest` starts crash-looping with a message about existing data in
`/var/lib/postgresql`, the repo's Docker volume was created with an older Postgres image layout.
Run this repo-local reset once:

```bash
bash scripts/reset-db.sh --clean
```

That recreates only this compose stack's database volume and keeps the project on `postgres:latest`.
