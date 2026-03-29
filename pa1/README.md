# PA1: Preferential Web Crawler

This project implements a preferential crawler for MedOverNet with:

- `ManagerApp`: Blazor UI and server control plane
- `pa1/crawler`: Python websocket daemon workers
- PostgreSQL `crawldb`: pages, links, frontier queue, and metadata

## Prerequisites

- Docker and Docker Compose
- .NET SDK (for running `ManagerApp` locally)
- Python 3 with a local `.venv`

## Install and Setup

Run the bootstrap script from repository root:

```bash
cd /home/tibor/Repos/EIPS-TT
bash scripts/bootstrap.sh
```

What this does:

- restores Python `.venv`
- starts the PostgreSQL container
- applies DB migrations

## Run the crawler (host development)

1. Start manager:

```bash
cd /home/tibor/Repos/EIPS-TT
ASPNETCORE_ENVIRONMENT=Development ASPNETCORE_URLS=http://127.0.0.1:5175 \
	dotnet run --project ManagerApp/ManagerApp.csproj
```

2. In a second terminal, start crawler daemon:

```bash
cd /home/tibor/Repos/EIPS-TT
source .venv/bin/activate
python pa1/crawler/src/main.py
```

3. Open manager UI:

- `http://127.0.0.1:5175`

## Run with Docker (server + crawler + db)

```bash
cd /home/tibor/Repos/EIPS-TT
docker compose --profile server --profile crawler up -d --build manager crawler db
```

Optional DB migration re-run:

```bash
bash scripts/db-migrate.sh
```

Check logs:

```bash
docker compose logs --tail=200 manager
docker compose logs --tail=200 crawler
docker compose logs --tail=200 db
```

## Import `pa1/db` into pgAdmin

`pa1/db` is a PostgreSQL custom dump (`pg_restore` format), so in pgAdmin use **Restore** (not plain SQL import).

### Recommended safe test target (separate PostgreSQL instance)

The following creates a second PostgreSQL container on port `55432` with its own Docker volume,
so your existing local DB is untouched:

```bash
cd /home/tibor/Repos/EIPS-TT

docker rm -f pa1-pgadmin-restore-test 2>/dev/null || true
docker volume rm pa1_pgadmin_restore_test_data 2>/dev/null || true
docker volume create pa1_pgadmin_restore_test_data

docker run -d --name pa1-pgadmin-restore-test \
	-e POSTGRES_USER=postgres \
	-e POSTGRES_PASSWORD=postgres \
	-e POSTGRES_DB=crawldb \
	-p 55432:5432 \
	-v pa1_pgadmin_restore_test_data:/var/lib/postgresql/data \
	postgres:latest
```

### pgAdmin restore steps

1. Add/register server in pgAdmin:
	 - Host: `localhost`
	 - Port: `55432`
	 - Username: `postgres`
	 - Password: `postgres`
2. Create database `crawldb` if it does not already exist.
3. Right click `crawldb` -> **Restore...**
4. Set:
	 - **Filename**: absolute path to `/home/tibor/Repos/EIPS-TT/pa1/db`
	 - **Format**: `Custom or tar`
	 - **Role name**: `postgres`
5. If target DB already contains objects, enable **Clean before restore**.
6. Start restore.

### Optional CLI validation (equivalent to pgAdmin Restore)

```bash
cd /home/tibor/Repos/EIPS-TT
PGPASSWORD=postgres pg_restore -h 127.0.0.1 -p 55432 -U postgres -d crawldb --clean --if-exists pa1/db
```

Quick verification query:

```bash
PGPASSWORD=postgres psql -h 127.0.0.1 -p 55432 -U postgres -d crawldb -Atc \
"SELECT page_type_code||'='||COUNT(*) FROM crawldb.page GROUP BY page_type_code ORDER BY page_type_code;"
```

Expected output for this dump:

- `BINARY=16786`
- `DUPLICATE=27`
- `FRONTIER=7902`
- `HTML=1001`

Note: this assignment dump intentionally excludes table data for some large payload tables, so image/binary payload rows may not be present after restore.

### Cleanup isolated test instance

```bash
docker rm -f pa1-pgadmin-restore-test
docker volume rm pa1_pgadmin_restore_test_data
```

## Related docs

- [ManagerApp/README.md](../ManagerApp/README.md)
- [pa1/crawler/crawler.md](crawler/crawler.md)
