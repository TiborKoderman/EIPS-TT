#!/usr/bin/env python3
"""PostgreSQL connection helpers for the crawler project.

Run this file directly to validate DB connectivity:
    python pa1/crawler/src/db/pg_connect.py
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import psycopg2
from psycopg2.extensions import connection as PgConnection


@dataclass(frozen=True)
class DbConfig:
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = "postgres"
    database: str = "postgres"


def load_db_config() -> DbConfig:
    """Load DB config from env vars with sane local/devcontainer defaults."""
    return DbConfig(
        host=os.getenv("DB_HOST", os.getenv("PGHOST", "localhost")),
        port=int(os.getenv("DB_PORT", os.getenv("PGPORT", "5432"))),
        user=os.getenv("DB_USER", os.getenv("PGUSER", "postgres")),
        password=os.getenv("DB_PASSWORD", os.getenv("PGPASSWORD", "postgres")),
        database=os.getenv("DB_NAME", os.getenv("PGDATABASE", "postgres")),
    )


def get_connection(config: DbConfig | None = None) -> PgConnection:
    """Create and return a new PostgreSQL connection."""
    cfg = config or load_db_config()
    return psycopg2.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        dbname=cfg.database,
    )


def test_connection() -> bool:
    """Open a connection and run a simple query to verify it works."""
    cfg = load_db_config()
    try:
        with get_connection(cfg) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user, version();")
                database, user, version = cur.fetchone()
        print("Database connection OK")
        print(f"Host: {cfg.host}:{cfg.port}")
        print(f"Database: {database}")
        print(f"User: {user}")
        print(f"Server: {version.split(',')[0]}")
        return True
    except Exception as exc:  # pragma: no cover - convenience diagnostic path
        print(f"Database connection FAILED: {exc}")
        return False


if __name__ == "__main__":
    raise SystemExit(0 if test_connection() else 1)
