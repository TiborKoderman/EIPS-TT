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

Use this from DB tools:

`postgresql://postgres:postgres@localhost:5432/crawldb`

Credentials are local to your compose stack.

## Utility Commands

```bash
# full setup shortcut (.venv + db + migrations)
bash scripts/bootstrap.sh

# reset schema, then re-apply migrations
bash scripts/reset-db.sh

# full DB reset (drops compose volumes), then re-apply migrations
bash scripts/reset-db.sh --clean

# db logs
docker compose logs -f db
```

## Crawler API (Mock Scaffolding)

The crawler now includes a standalone Flask API server for worker management scaffolding.
This is additive and does not change existing `pa1/crawler/src/main.py` standalone behavior.

Architecture model:

- One API server process represents one crawler daemon instance.
- The daemon manages multiple workers internally (worker-level parallelism).
- There is no nested "worker API per worker" process.
- The API is primarily the manager/external control plane (local or remote).

```bash
source .venv/bin/activate
python pa1/crawler/src/api_server.py
```

Equivalent via the main utility entrypoint:

```bash
python pa1/crawler/src/main.py --run-api --api-host 127.0.0.1 --api-port 8090
```

Default API URL: `http://127.0.0.1:8090`

Optional environment variables:

- `CRAWLER_API_HOST` (default `127.0.0.1`)
- `CRAWLER_API_PORT` (default `8090`)
- `CRAWLER_API_DEBUG` (`true/false`, default `false`)
- `CRAWLER_API_TOKEN` (if set, Bearer auth is required)

The current implementation is mock-backed and marks responses with `isMock: true` and `source: "mock"` so the manager UI can clearly distinguish scaffold data from real crawler-backed data.

Typical flow:

1. `POST /api/daemon/start`
2. `PUT /api/config/global` and optional group/worker config endpoints
3. `POST /api/workers/spawn` (repeat as needed)
4. `POST /api/workers/{id}/start` to activate specific workers
5. Manager reads status/logs/statistics endpoints
6. `POST /api/daemon/reload` or `POST /api/daemon/stop` as needed

### Manager-controlled local daemon (default)

`ManagerApp` can auto-start a local daemon process on app startup and terminate it on app shutdown.
This is enabled by default for local development when `CrawlerApi:BaseUrl` points to localhost.

Relevant `ManagerApp` settings:

- `CrawlerApi:AutoStartLocalDaemon` (default `true`)
- `CrawlerApi:LauncherMode` (`Process` or `Docker`)
- `CrawlerApi:PythonExecutable`
- `CrawlerApi:LocalDaemonArgs`
- `CrawlerApi:DockerStartCommand`
- `CrawlerApi:DockerStopCommand`

### Reverse socket channel (no daemon-side port forwarding)

Daemons can establish an outbound websocket connection to the manager server:

- Manager endpoint: `/api/daemon-channel`
- Daemon env var: `MANAGER_DAEMON_WS_URL=ws://<manager-host>/api/daemon-channel?daemonId=<id>`

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
