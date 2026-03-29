# EIPS-TT

Preferential crawler project with:

- `ManagerApp`: Blazor UI + server control plane
- `pa1/crawler`: websocket daemon runtime (Python)
- `db/migrations`: PostgreSQL schema history

## Development (Host)

1. Bootstrap environment:

```bash
cd /home/tibor/Repos/EIPS-TT
bash scripts/bootstrap.sh
```

2. Start manager in development mode:

```bash
cd /home/tibor/Repos/EIPS-TT
ASPNETCORE_ENVIRONMENT=Development ASPNETCORE_URLS=http://127.0.0.1:5175 \
	dotnet run --project ManagerApp/ManagerApp.csproj
```

3. Start crawler from Python (websocket daemon):

```bash
cd /home/tibor/Repos/EIPS-TT
source .venv/bin/activate
python pa1/crawler/src/main.py
```

Optional manual venv restore:

```bash
bash scripts/venv-restore.sh
source .venv/bin/activate
```

## Deployment (Docker)

1. Build images and start stack:

```bash
cd /home/tibor/Repos/EIPS-TT
docker compose --profile server --profile crawler build manager crawler
docker compose --profile server --profile crawler up -d manager crawler db
```

2. Apply migrations (if needed):

```bash
bash scripts/db-migrate.sh
```

3. Check status/logs:

```bash
docker compose ps
docker compose logs --tail=200 manager
docker compose logs --tail=200 crawler
```

4. Stop deployment:

```bash
docker compose --profile server --profile crawler down
```

## Notes

- Manager default URL is `http://127.0.0.1:5175` in these examples.
- Use `MANAGER_HOST_PORT=<port>` for host port override in Docker Compose.

## Module Docs

- [ManagerApp/README.md](ManagerApp/README.md)
- [pa1/crawler/crawler.md](pa1/crawler/crawler.md)
