#!/usr/bin/env python3
"""Compose helper: ensure pgvector extension and run Alembic migrations.

This script is intended to be used from docker-compose to initialize a fresh
PostgreSQL database before starting the Shu API. It assumes SHU_DATABASE_URL is
set and points at the target database.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Optional, Set

import alembic.command
import alembic.config
from alembic.script import ScriptDirectory
import psycopg2


def _get_database_url() -> str:
    url = os.getenv("SHU_DATABASE_URL")
    if not url:
        print("Error: SHU_DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)

    # Alembic and psycopg2 expect a sync driver URL
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)

    return url


def ensure_pgvector(url: str) -> None:
    """Ensure the pgvector extension exists on the target database.

    The base image (ankane/pgvector) already has the extension installed, but
    this call is safe and idempotent and keeps behavior aligned with the
    Kubernetes dev jobs.
    """

    print("[db_migrate] Ensuring pgvector extension ...", flush=True)
    conn = psycopg2.connect(url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        conn.close()
    print("[db_migrate] pgvector extension ensured", flush=True)


def _get_current_revision(url: str) -> Optional[str]:
    """Get the current alembic revision from the database, if any."""
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'alembic_version')"
            )
            exists_row = cur.fetchone()
            if not exists_row or not exists_row[0]:
                return None
            cur.execute("SELECT version_num FROM alembic_version")
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _build_replaces_map(script_dir: ScriptDirectory) -> Dict[str, str]:
    """Build a mapping from replaced revision IDs to their replacing revision.

    Returns: Dict mapping replaced_rev_id -> replacing_rev_id
    """
    replaces_map: Dict[str, str] = {}
    for script in script_dir.walk_revisions():
        # The 'replaces' attribute is on the module, not the Script object
        replaces = getattr(script.module, "replaces", None)
        if replaces:
            for replaced_rev in replaces:
                replaces_map[replaced_rev] = script.revision
    return replaces_map


def _get_known_revisions(script_dir: ScriptDirectory) -> Set[str]:
    """Get all known revision IDs from the script directory."""
    return {script.revision for script in script_dir.walk_revisions()}


def resolve_orphaned_revision(cfg: alembic.config.Config, url: str) -> None:
    """Check if DB has an orphaned revision and stamp to the squash's down_revision.

    When migrations are squashed and the original files deleted, Alembic can't
    find the old revision ID. This function detects that case and stamps the
    database to the down_revision of the squash migration, so that `upgrade head`
    will run the squash migration (which is idempotent and handles partial states).
    """
    current_rev = _get_current_revision(url)
    if not current_rev:
        print("[db_migrate] No existing revision found (fresh database)", flush=True)
        return

    script_dir = ScriptDirectory.from_config(cfg)
    known_revisions = _get_known_revisions(script_dir)

    if current_rev in known_revisions:
        print(f"[db_migrate] Current revision {current_rev} is known", flush=True)
        return

    # Current revision is not in the script directory - check if it was replaced
    replaces_map = _build_replaces_map(script_dir)

    if current_rev in replaces_map:
        squash_rev = replaces_map[current_rev]
        squash_script = script_dir.get_revision(squash_rev)
        down_rev = squash_script.down_revision
        print(
            f"[db_migrate] Orphaned revision {current_rev} was replaced by {squash_rev}",
            flush=True
        )
        # Handle edge case where squash replaces base migrations (down_revision is None)
        if down_rev is None:
            print(
                f"[db_migrate] Squash migration has no down_revision, stamping directly to {squash_rev}",
                flush=True
            )
            alembic.command.stamp(cfg, squash_rev, purge=True)
            print(f"[db_migrate] Stamped database to {squash_rev}", flush=True)
        else:
            print(
                f"[db_migrate] Stamping to {down_rev} so upgrade head runs the idempotent squash migration",
                flush=True
            )
            # Stamp to down_revision so `upgrade head` runs the squash migration
            # The squash migration is idempotent and handles partial states
            alembic.command.stamp(cfg, down_rev, purge=True)
            print(f"[db_migrate] Stamped database to {down_rev}", flush=True)
    else:
        print(
            f"[db_migrate] WARNING: Current revision {current_rev} is unknown and not in any replaces list",
            file=sys.stderr,
            flush=True
        )


def run_alembic_migrations(url: str) -> None:
    """Run Alembic upgrade to head using the backend's alembic.ini."""

    project_root = Path(__file__).resolve().parent.parent
    alembic_ini = project_root / "alembic.ini"
    if not alembic_ini.is_file():
        print(f"Error: alembic.ini not found at {alembic_ini}", file=sys.stderr)
        sys.exit(1)

    cfg = alembic.config.Config(str(alembic_ini))
    cfg.set_main_option("sqlalchemy.url", url)

    # Handle orphaned revisions from squashed migrations
    resolve_orphaned_revision(cfg, url)

    print("[db_migrate] Running Alembic migrations (upgrade head) ...", flush=True)
    alembic.command.upgrade(cfg, "head")
    print("[db_migrate] Migrations complete", flush=True)


def main() -> None:
    url = _get_database_url()
    ensure_pgvector(url)
    run_alembic_migrations(url)


if __name__ == "__main__":  # pragma: no cover
    main()

