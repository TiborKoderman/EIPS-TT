# EIPS-TT

Preferential crawler project with a Blazor manager (`ManagerApp`) and websocket crawler daemon (`pa1/crawler`).

## Components

- `ManagerApp`: server UI + control plane + queue ownership
- `pa1/crawler`: websocket daemon and worker runtime
- `db/migrations`: PostgreSQL schema/migrations

## Local Installation (Normal Host Mode)

```bash
cd /home/tibor/Repos/EIPS-TT
bash scripts/bootstrap.sh
bash scripts/db-info.sh
bash scripts/run-manager.sh
```

Open the manager at `http://127.0.0.1:5160` (or `ASPNETCORE_URLS` override).

### Manual `.venv` restore

Use this when you need to rebuild Python dependencies manually:

```bash
cd /home/tibor/Repos/EIPS-TT
bash scripts/venv-restore.sh
source .venv/bin/activate
```

### Start ManagerApp in dev mode with dotnet

Using script wrapper (recommended):

```bash
cd /home/tibor/Repos/EIPS-TT
ASPNETCORE_ENVIRONMENT=Development ASPNETCORE_URLS=http://127.0.0.1:5175 bash scripts/run-manager.sh
```

Direct `dotnet run` command:

```bash
cd /home/tibor/Repos/EIPS-TT
ASPNETCORE_ENVIRONMENT=Development dotnet run --project ManagerApp/ManagerApp.csproj --urls http://127.0.0.1:5175
```

## Ground-Up Reset

```bash
cd /home/tibor/Repos/EIPS-TT
bash scripts/reset-db.sh --clean
bash scripts/bootstrap.sh
bash scripts/run-manager.sh
```

## Current Crawl Defaults

- default enabled seed: `https://medover.zurnal24.si/`
- websocket preset/default run target: `4` concurrent workers
- topic keywords include medical + fitness English and Slovenian translations
- worker/server politeness enforces minimum 5s and extends via robots-reported delay

## Docker Setup (Local, No Publishing Required)

`docker-compose.yml` now includes:

- `db` (always available for host scripts)
- `manager` profile (`server`): packaged server+crawler image
- `crawler` profile (`crawler`): standalone crawler image connecting to manager

### Build both images locally

```bash
cd /home/tibor/Repos/EIPS-TT
docker compose --profile server --profile crawler build manager crawler
```

### Start DB only

```bash
docker compose up -d db
```

### Apply database migrations

```bash
cd /home/tibor/Repos/EIPS-TT
bash scripts/db-migrate.sh
```

### Start packaged server+crawler locally

```bash
docker compose --profile server up -d manager db
```

Server is exposed at `http://127.0.0.1:5175` by default.
If port `5175` is already in use, set a host override:

```bash
MANAGER_HOST_PORT=5177 docker compose --profile server up -d manager db
```

### Start server + external crawler container locally

```bash
docker compose --profile server --profile crawler up -d manager crawler db
```

### Inspect status and logs

```bash
docker compose ps
docker compose logs --tail=200 manager
docker compose logs --tail=200 crawler
```

### Stop local docker runtime

```bash
docker compose --profile server --profile crawler down
```

## Docker Images

- crawler image: `ghcr.io/<owner>/eips-tt-crawler`
- combined server+crawler image: `ghcr.io/<owner>/eips-tt-server-crawler`

## Publishing Docker Images to GitHub (GHCR)

### Automated release

Workflow: `.github/workflows/docker-release.yml`

- pushes on `master`
- pushes on tags `v*`
- supports manual `workflow_dispatch`

### Local/manual build and publish

```bash
cd /home/tibor/Repos/EIPS-TT
GHCR_OWNER=<github-owner> bash scripts/docker-release.sh v0.1.0
GHCR_OWNER=<github-owner> PUSH=1 bash scripts/docker-release.sh v0.1.0
```

If `PUSH=1` is set, the script pushes both images to GHCR.

## Additional Module Docs

- manager details: [ManagerApp/README.md](ManagerApp/README.md)
- crawler runtime details: [pa1/crawler/crawler.md](pa1/crawler/crawler.md)
- daemon state semantics: [docs/daemon-worker-runtime.md](docs/daemon-worker-runtime.md)
