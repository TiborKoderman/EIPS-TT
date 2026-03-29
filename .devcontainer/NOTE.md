# Devcontainer Note

Compose-based devcontainer mode has been removed from this repository.

Use one of these supported flows instead:

- Host mode: `bash scripts/bootstrap.sh` then `bash scripts/run-manager.sh`
- Local Docker mode: `docker compose --profile server --profile crawler build manager crawler` then `docker compose --profile server --profile crawler up -d manager crawler db`

See the root README for full setup steps.
