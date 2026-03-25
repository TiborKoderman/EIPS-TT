# Devcontainer Note

This repository is now **host-first** by default:
- Python runs from local `.venv`
- PostgreSQL runs from root `docker-compose.yml`
- Setup is `bash scripts/bootstrap.sh`

The `.devcontainer` is optional. Use it only if you want a fully containerized editor/runtime.
Both modes use the same root `docker-compose.yml`:
- host mode starts only `db`
- devcontainer mode starts `app` + `db`

## Why this change
- Avoid duplicated config across workspace/devcontainer files
- Avoid nested mount paths and accidental `.devcontainer/.devcontainer` behavior
- Keep setup fast and predictable after cloning
- Reduce container build context via `.dockerignore`

## If you use Dev Containers in VS Code
1. Open the repository folder directly (not a `.code-workspace` file).
2. Run: `Dev Containers: Rebuild and Reopen in Container`.
3. First container build can still take longer than host setup.

For everyday work, the recommended path is still:

```bash
bash scripts/bootstrap.sh
source .venv/bin/activate
```
