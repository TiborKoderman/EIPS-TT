# ManagerApp

Blazor Server management UI for the EIPS-TT crawler project.

This app provides:
- Dashboard statistics from PostgreSQL (`crawldb`)
- Page search and detail views
- Link graph visualization (D3 via JS interop)
- Worker monitoring controls (currently mock-backed)
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

Default connection (from `appsettings.json`):

`Host=localhost;Port=5432;Database=crawldb;Username=postgres;Password=postgres`

## Setup (Standalone for ManagerApp)

1. Start/prepare database (from repository root):

```bash
cd /home/tibor/Repos/EIPS-TT
bash scripts/db-migrate.sh
```

2. Build app:

```bash
cd /home/tibor/Repos/EIPS-TT/ManagerApp
dotnet build
```

3. Run app:

```bash
cd /home/tibor/Repos/EIPS-TT/ManagerApp
dotnet run
```

4. Open shown local URL (typically `https://localhost:7xxx` or `http://localhost:5xxx`).

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

Override via environment variable if needed:

```bash
ConnectionStrings__CrawldbConnection="Host=...;Port=...;Database=...;Username=...;Password=..."
```

## Live Updates

SignalR hub is mapped in `Program.cs` at:

- `/crawlerhub`

The hub supports broadcasts for:
- Worker status updates
- New crawled pages
- Statistics refreshes

## Current Functional Status

- DB-backed statistics/pages/graph services: implemented
- Worker service: mock implementation (API integration pending)
- Graph page: interactive rendering via `wwwroot/js/graph.js`

## Troubleshooting

- Build fails with SDK error:
  - install/use a .NET SDK that supports `net10.0`.
- DB connection errors:
  - verify PostgreSQL is running and `CrawldbConnection` is correct.
- No live updates visible:
  - ensure producer service publishes events to `/crawlerhub`.
