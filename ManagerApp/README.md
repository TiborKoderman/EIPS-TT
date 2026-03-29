# ManagerApp

Blazor Server management UI for the EIPS-TT crawler project.

This app provides:
- Dashboard statistics from PostgreSQL (`crawldb`)
- Page search and detail views
- Link graph visualization (D3 via JS interop)
- Worker monitoring controls backed by daemon websocket connections
- SignalR hub endpoint for live updates (`/crawlerhub`)

## Tech Stack

- .NET `net10.0` Blazor Server
- Entity Framework Core + Npgsql (PostgreSQL)
- SignalR
- Razor Components + static assets in `wwwroot`

## Project Layout

- `Program.cs`: app startup, DI registrations, DB wiring, SignalR mapping
- `Data/`: EF DbContext + entities
- `Services/`: app service abstractions and implementations
- `Hubs/`: SignalR hub (`CrawlerHub`)
- `Components/`: layout/pages/shared components
- `wwwroot/`: CSS, JS interop, static assets

## Prerequisites

- .NET SDK compatible with `net10.0`
- PostgreSQL reachable at the configured connection string

Default fallback connection (from `appsettings.json`):

`Host=localhost;Port=5432;Database=crawldb;Username=postgres;Password=postgres`

For local repo use, prefer the wrapper scripts instead of relying on that fallback directly,
because the repo may shift PostgreSQL to `5433` or another free host port and persist it in
`.env.local`.

## Setup

1. Start/prepare database (from repository root):

```bash
cd /home/tibor/Repos/EIPS-TT
bash scripts/db-migrate.sh
```

2. Bootstrap the repo-local DB + Python environment:

```bash
cd /home/tibor/Repos/EIPS-TT
./scripts/bootstrap.sh
./scripts/db-info.sh
```

3. Build app:

```bash
cd /home/tibor/Repos/EIPS-TT/ManagerApp
dotnet build
```

4. Run app:

```bash
cd /home/tibor/Repos/EIPS-TT
./scripts/run-manager.sh
```

5. Open `http://127.0.0.1:5160`.

## Docker Server Setup

Two containerized manager run modes are available:

1. `ManagerApp/Dockerfile`:
  - manager server only
2. `ManagerApp/Dockerfile.server-crawler`:
  - manager server plus bundled crawler runtime (python + `pa1/crawler`)

Recommended deployment uses the bundled mode via compose profile `server`:

```bash
cd /home/tibor/Repos/EIPS-TT
docker compose --profile server up -d manager db
```

The server listens on `http://127.0.0.1:5175` in this mode.

To also run an external crawler container against manager:

```bash
docker compose --profile server --profile crawler up -d manager crawler db
```

For GHCR images, the publish workflow writes:

- `ghcr.io/<owner>/eips-tt-server-crawler`
- `ghcr.io/<owner>/eips-tt-crawler`

## Recommended VS Code Dev Workflow

Use the dedicated ManagerApp tasks/debug profile to avoid port conflicts and keep one shared dev server:

- Task: `manager: watch (hot reload 5175)`
- Task: `manager: run (dev 5175)`
- Task: `manager: build`
- Debug config: `ManagerApp: Launch (dev 5175)`

These run on `http://localhost:5175` so you can keep one stable instance for manual testing and Copilot-driven iteration.

If you need to run manually from terminal with the same profile:

```bash
cd /home/tibor/Repos/EIPS-TT/ManagerApp
dotnet run --launch-profile copilot-dev
```

## Configuration

- Main config: `appsettings.json`
- Dev overrides: `appsettings.Development.json`
- Connection string key: `ConnectionStrings:CrawldbConnection`
- Daemon websocket auth key: `CrawlerApi:DaemonChannelToken`

When `CrawlerApi:DaemonChannelToken` is set, daemon websocket registration must include the same token.
Local daemon launch paths propagate it automatically via `MANAGER_DAEMON_WS_TOKEN`.

Override via environment variable if needed:

```bash
ConnectionStrings__CrawldbConnection="Host=...;Port=...;Database=...;Username=...;Password=..."
```

The manager also honors the same `DB_*` / `PG*` environment variables as the Python crawler:

```bash
DB_HOST=localhost
DB_PORT=5433
DB_USER=postgres
DB_PASSWORD=postgres
DB_NAME=crawldb
```

This is useful when another local PostgreSQL instance already occupies `5432`.

### Split UI vs external daemon channel URLs

You can keep the UI local-only and publish a separate external URL for daemon websocket/ingest registration.

Example runtime binding (UI local on `5160`, external listener on `5161`):

```bash
ASPNETCORE_URLS="http://127.0.0.1:5160;http://0.0.0.0:5161" dotnet run --project ManagerApp/ManagerApp.csproj
```

Then set crawler registration URLs so generated daemon scripts use the external endpoint:

```bash
CrawlerApi__ManagerBaseUrl="http://<public-host>:5161"
CrawlerApi__ManagerSocketUrl="ws://<public-host>:5161/api/daemon-channel"
```

This keeps local UI access on `127.0.0.1:5160` while allowing external daemons to connect to the dedicated published endpoint.

For normal local use, prefer the repo wrapper:

```bash
cd /home/tibor/Repos/EIPS-TT
./scripts/run-manager.sh
```

It reads the persisted `.env.local` DB settings created by `./scripts/bootstrap.sh`,
verifies the local PostgreSQL connection, and then starts the manager.

## Live Updates

SignalR hub is mapped in `Program.cs` at:

- `/crawlerhub`

The hub supports broadcasts for:
- Worker status updates
- New crawled pages
- Statistics refreshes

## Current Functional Status

- DB-backed statistics/pages/graph services: implemented
- Worker service: daemon API integration implemented
- Graph page: interactive rendering via `wwwroot/js/graph.js`

## Troubleshooting

- Build fails with SDK error:
  - install/use a .NET SDK that supports `net10.0`.
- DB connection errors:
  - run `./scripts/db-info.sh` and use the printed `Host`/`Port` in DBeaver or any other client.
  - this repo may be on `localhost:5433` instead of `5432` when another PostgreSQL instance already uses `5432`.
- No live updates visible:
  - ensure producer service publishes events to `/crawlerhub`.
