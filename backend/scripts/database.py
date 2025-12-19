#!/usr/bin/env python3
"""Shu Database Management Script.

Unified script for all database lifecycle operations:
- migrate: Run Alembic migrations (default, used by Docker Compose)
- setup: Run init-db.sql + migrations + requirements check
- reset: Drop schema, recreate, and run setup
- create-db: Create a new database (e.g., shu_test)
- cleanup: Drop a database
- check: Show current revision and requirements status

Usage:
    python scripts/database.py                     # Default: run migrations
    python scripts/database.py migrate             # Run Alembic migrations
    python scripts/database.py setup               # Full setup with init-db.sql
    python scripts/database.py reset --force       # Reset database schema
    python scripts/database.py create-db shu_test  # Create new database
    python scripts/database.py cleanup shu_test    # Drop database
    python scripts/database.py check               # Check status

Options:
    --database-url URL    Override SHU_DATABASE_URL
    --force               Skip confirmation prompts for destructive operations
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Set
from urllib.parse import urlparse, urlunparse

import re

import alembic.command
import alembic.config
from alembic.script import ScriptDirectory
import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INIT_SQL_PATH = PROJECT_ROOT.parent / "init-db.sql"
ALEMBIC_INI_PATH = PROJECT_ROOT / "alembic.ini"

LOG_PREFIX = "[database]"


# =============================================================================
# URL Utilities
# =============================================================================


def _normalize_url(url: str) -> str:
    """Convert async URL to sync URL for psycopg2/Alembic."""
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


def _get_database_url(override: Optional[str] = None) -> str:
    """Get database URL from override or environment."""
    url = override or os.getenv("SHU_DATABASE_URL")
    if not url:
        print("Error: No database URL provided.", file=sys.stderr)
        print("Use --database-url or set SHU_DATABASE_URL environment variable.", file=sys.stderr)
        sys.exit(1)
    return _normalize_url(url)


def _get_admin_url(url: str) -> str:
    """Get URL for connecting to 'postgres' database (for CREATE/DROP DATABASE)."""
    parsed = urlparse(url)
    # Replace the database name with 'postgres'
    admin_parsed = parsed._replace(path="/postgres")
    return urlunparse(admin_parsed)


def _get_database_name(url: str) -> str:
    """Extract database name from URL."""
    parsed = urlparse(url)
    return parsed.path.lstrip("/")


def _validate_identifier(name: str) -> bool:
    """Validate that a string is a safe PostgreSQL identifier.

    Rejects names that could cause issues with identifier quoting:
    - Empty names
    - Names with null bytes
    - Names longer than 63 characters (PostgreSQL limit)
    """
    if not name:
        return False
    if len(name) > 63:
        return False
    if "\x00" in name:
        return False
    # Allow alphanumeric, underscore, and hyphen (common for db names like shu_test)
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_-]*$", name):
        return False
    return True


# =============================================================================
# Database Connection Helpers
# =============================================================================


def _connect(url: str, autocommit: bool = True) -> psycopg2.extensions.connection:
    """Create a database connection."""
    conn = psycopg2.connect(url)
    conn.autocommit = autocommit
    return conn


# =============================================================================
# Extension Management
# =============================================================================


def ensure_extensions(url: str) -> bool:
    """Ensure all required PostgreSQL extensions are installed."""
    extensions = ["vector", "uuid-ossp", "pg_trgm", "btree_gin"]
    optional_extensions = ["pg_stat_statements"]  # May not be available in all environments

    print(f"{LOG_PREFIX} Ensuring required extensions...", flush=True)
    conn = _connect(url)
    try:
        with conn.cursor() as cur:
            for ext in extensions:
                cur.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')
                print(f"{LOG_PREFIX}   {ext}: OK", flush=True)

            for ext in optional_extensions:
                try:
                    cur.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')
                    print(f"{LOG_PREFIX}   {ext}: OK", flush=True)
                except psycopg2.Error:
                    print(f"{LOG_PREFIX}   {ext}: skipped (not available)", flush=True)

        return True
    except psycopg2.Error as e:
        print(f"{LOG_PREFIX} Error ensuring extensions: {e}", file=sys.stderr)
        return False
    finally:
        conn.close()


# =============================================================================
# Init SQL Execution
# =============================================================================


def execute_init_sql(url: str) -> bool:
    """Execute init-db.sql to set up extensions, functions, and configuration.

    Uses a transaction so the setup is all-or-nothing. If any statement fails,
    the entire init is rolled back to avoid leaving the DB in a partial state.
    """
    if not INIT_SQL_PATH.is_file():
        print(f"{LOG_PREFIX} Warning: init-db.sql not found at {INIT_SQL_PATH}", file=sys.stderr)
        print(f"{LOG_PREFIX} Falling back to manual extension setup...", flush=True)
        return ensure_extensions(url)

    print(f"{LOG_PREFIX} Executing init-db.sql...", flush=True)
    conn = _connect(url, autocommit=False)  # Use transaction for all-or-nothing
    try:
        with open(INIT_SQL_PATH, "r") as f:
            sql_content = f.read()

        with conn.cursor() as cur:
            cur.execute(sql_content)

        conn.commit()
        print(f"{LOG_PREFIX} init-db.sql executed successfully", flush=True)
        return True
    except psycopg2.Error as e:
        conn.rollback()
        print(f"{LOG_PREFIX} Error executing init-db.sql: {e}", file=sys.stderr)
        print(f"{LOG_PREFIX} Transaction rolled back - database unchanged", file=sys.stderr)
        return False
    finally:
        conn.close()


# =============================================================================
# Alembic Migration Helpers
# =============================================================================


def _get_alembic_config(url: str) -> alembic.config.Config:
    """Get Alembic configuration with URL set."""
    if not ALEMBIC_INI_PATH.is_file():
        print(f"Error: alembic.ini not found at {ALEMBIC_INI_PATH}", file=sys.stderr)
        sys.exit(1)

    cfg = alembic.config.Config(str(ALEMBIC_INI_PATH))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _get_current_revision(url: str) -> Optional[str]:
    """Get the current Alembic revision from the database."""
    conn = _connect(url, autocommit=False)
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
    """Build a mapping from replaced revision IDs to their replacing revision."""
    replaces_map: Dict[str, str] = {}
    for script in script_dir.walk_revisions():
        replaces = getattr(script.module, "replaces", None)
        if replaces:
            for replaced_rev in replaces:
                replaces_map[replaced_rev] = script.revision
    return replaces_map


def _get_known_revisions(script_dir: ScriptDirectory) -> Set[str]:
    """Get all known revision IDs from the script directory."""
    return {script.revision for script in script_dir.walk_revisions()}


def _resolve_orphaned_revision(cfg: alembic.config.Config, url: str) -> None:
    """Handle orphaned revisions from squashed migrations."""
    current_rev = _get_current_revision(url)
    if not current_rev:
        print(f"{LOG_PREFIX} No existing revision found (fresh database)", flush=True)
        return

    script_dir = ScriptDirectory.from_config(cfg)
    known_revisions = _get_known_revisions(script_dir)

    if current_rev in known_revisions:
        print(f"{LOG_PREFIX} Current revision {current_rev} is known", flush=True)
        return

    replaces_map = _build_replaces_map(script_dir)

    if current_rev in replaces_map:
        squash_rev = replaces_map[current_rev]
        squash_script = script_dir.get_revision(squash_rev)
        down_rev = squash_script.down_revision
        print(f"{LOG_PREFIX} Orphaned revision {current_rev} was replaced by {squash_rev}", flush=True)

        if down_rev is None:
            print(f"{LOG_PREFIX} Stamping directly to {squash_rev}", flush=True)
            alembic.command.stamp(cfg, squash_rev, purge=True)
        else:
            print(f"{LOG_PREFIX} Stamping to {down_rev} so upgrade runs the squash migration", flush=True)
            alembic.command.stamp(cfg, down_rev, purge=True)
    else:
        print(
            f"{LOG_PREFIX} WARNING: Revision {current_rev} is unknown and not in any replaces list",
            file=sys.stderr,
            flush=True,
        )


# =============================================================================
# Requirements Check
# =============================================================================


def check_requirements(url: str) -> bool:
    """Check if database meets Shu requirements using check_requirements() function."""
    print(f"\n{LOG_PREFIX} Checking database requirements...", flush=True)

    conn = _connect(url, autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Check if function exists
            cur.execute(
                "SELECT EXISTS (SELECT FROM pg_proc WHERE proname = 'check_requirements')"
            )
            if not cur.fetchone()["exists"]:
                print(f"{LOG_PREFIX} check_requirements() function not found, skipping check", flush=True)
                return True

            cur.execute("SELECT * FROM check_requirements()")
            results = cur.fetchall()

            all_ok = True
            for result in results:
                status = result["status"]
                icon = "OK" if status == "OK" else "WARN" if status == "WARNING" else "ERR"
                print(f"{LOG_PREFIX}   [{icon}] {result['requirement']}: {result['details']}", flush=True)
                if status == "ERROR":
                    all_ok = False

            return all_ok
    except psycopg2.Error as e:
        print(f"{LOG_PREFIX} Error checking requirements: {e}", file=sys.stderr)
        return False
    finally:
        conn.close()


# =============================================================================
# Command: migrate
# =============================================================================


def cmd_migrate(url: str) -> bool:
    """Run Alembic migrations (upgrade to head)."""
    print(f"{LOG_PREFIX} Running Alembic migrations...", flush=True)

    # Ensure pgvector extension exists (backward compat with docker-compose usage)
    conn = _connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        conn.close()

    cfg = _get_alembic_config(url)
    _resolve_orphaned_revision(cfg, url)

    print(f"{LOG_PREFIX} Running upgrade head...", flush=True)
    alembic.command.upgrade(cfg, "head")
    print(f"{LOG_PREFIX} Migrations complete", flush=True)
    return True


# =============================================================================
# Command: setup
# =============================================================================


def cmd_setup(url: str) -> bool:
    """Full database setup: init-db.sql + migrations + requirements check."""
    print(f"{LOG_PREFIX} Starting full database setup...", flush=True)

    # Step 1: Execute init-db.sql
    if not execute_init_sql(url):
        return False

    # Step 2: Run migrations
    if not cmd_migrate(url):
        return False

    # Step 3: Check requirements
    if not check_requirements(url):
        print(f"{LOG_PREFIX} Warning: Some requirements not met", file=sys.stderr)
        # Don't fail on requirements check, just warn

    print(f"{LOG_PREFIX} Database setup completed successfully!", flush=True)
    return True


# =============================================================================
# Command: reset
# =============================================================================


def cmd_reset(url: str, force: bool = False) -> bool:
    """Reset database by dropping and recreating the public schema."""
    db_name = _get_database_name(url)

    if not force:
        print(f"WARNING: This will completely reset the '{db_name}' database!")
        print("All existing data will be lost.")
        response = input("Are you sure you want to continue? (yes/no): ").strip().lower()
        if response != "yes":
            print("Operation cancelled.")
            return False

    print(f"{LOG_PREFIX} Resetting database schema...", flush=True)

    conn = _connect(url)
    try:
        with conn.cursor() as cur:
            print(f"{LOG_PREFIX} Dropping public schema...", flush=True)
            cur.execute("DROP SCHEMA public CASCADE")
            print(f"{LOG_PREFIX} Creating public schema...", flush=True)
            cur.execute("CREATE SCHEMA public")
            cur.execute("GRANT ALL ON SCHEMA public TO PUBLIC")

        print(f"{LOG_PREFIX} Schema reset complete", flush=True)
    except psycopg2.Error as e:
        print(f"{LOG_PREFIX} Error resetting schema: {e}", file=sys.stderr)
        return False
    finally:
        conn.close()

    # Run full setup on the reset database
    return cmd_setup(url)


# =============================================================================
# Command: create-db
# =============================================================================


def cmd_create_db(url: str, db_name: str) -> bool:
    """Create a new database."""
    # Validate identifier to prevent SQL injection
    if not _validate_identifier(db_name):
        print(f"{LOG_PREFIX} Error: Invalid database name '{db_name}'", file=sys.stderr)
        print(f"{LOG_PREFIX} Names must start with a letter or underscore, contain only", file=sys.stderr)
        print(f"{LOG_PREFIX} alphanumeric characters, underscores, or hyphens, and be <= 63 chars", file=sys.stderr)
        return False

    admin_url = _get_admin_url(url)

    print(f"{LOG_PREFIX} Creating database '{db_name}'...", flush=True)

    conn = _connect(admin_url)
    try:
        with conn.cursor() as cur:
            # Check if database exists
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone():
                print(f"{LOG_PREFIX} Database '{db_name}' already exists", flush=True)
                return True

            # Create the database using safe identifier quoting
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
            print(f"{LOG_PREFIX} Database '{db_name}' created successfully", flush=True)
            return True
    except psycopg2.Error as e:
        print(f"{LOG_PREFIX} Error creating database: {e}", file=sys.stderr)
        return False
    finally:
        conn.close()


# =============================================================================
# Command: cleanup
# =============================================================================


def cmd_cleanup(url: str, db_name: str, force: bool = False) -> bool:
    """Drop a database."""
    # Validate identifier to prevent SQL injection
    if not _validate_identifier(db_name):
        print(f"{LOG_PREFIX} Error: Invalid database name '{db_name}'", file=sys.stderr)
        print(f"{LOG_PREFIX} Names must start with a letter or underscore, contain only", file=sys.stderr)
        print(f"{LOG_PREFIX} alphanumeric characters, underscores, or hyphens, and be <= 63 chars", file=sys.stderr)
        return False

    admin_url = _get_admin_url(url)

    if not force:
        print(f"WARNING: This will permanently delete the '{db_name}' database!")
        response = input("Are you sure you want to continue? (yes/no): ").strip().lower()
        if response != "yes":
            print("Operation cancelled.")
            return False

    print(f"{LOG_PREFIX} Dropping database '{db_name}'...", flush=True)

    conn = _connect(admin_url)
    try:
        with conn.cursor() as cur:
            # Terminate existing connections
            cur.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (db_name,),
            )

            # Drop the database using safe identifier quoting
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))
            print(f"{LOG_PREFIX} Database '{db_name}' dropped successfully", flush=True)
            return True
    except psycopg2.Error as e:
        print(f"{LOG_PREFIX} Error dropping database: {e}", file=sys.stderr)
        return False
    finally:
        conn.close()


# =============================================================================
# Command: check
# =============================================================================


def cmd_check(url: str) -> bool:
    """Check database status: current revision and requirements."""
    db_name = _get_database_name(url)
    print(f"{LOG_PREFIX} Checking database '{db_name}'...", flush=True)

    # Get current revision
    current_rev = _get_current_revision(url)
    if current_rev:
        print(f"{LOG_PREFIX} Current Alembic revision: {current_rev}", flush=True)
    else:
        print(f"{LOG_PREFIX} No Alembic revision (database not migrated)", flush=True)

    # Check requirements
    return check_requirements(url)


# =============================================================================
# CLI Entry Point
# =============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Shu Database Management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/database.py                      # Run migrations (default)
  python scripts/database.py migrate              # Run Alembic migrations
  python scripts/database.py setup                # Full setup with init-db.sql
  python scripts/database.py reset --force        # Reset database schema
  python scripts/database.py create-db shu_test   # Create test database
  python scripts/database.py cleanup shu_test --force  # Drop test database
  python scripts/database.py check                # Check status
        """,
    )

    parser.add_argument(
        "command",
        nargs="?",
        default="migrate",
        choices=["migrate", "setup", "reset", "create-db", "cleanup", "check"],
        help="Command to run (default: migrate)",
    )
    parser.add_argument(
        "db_name",
        nargs="?",
        help="Database name (for create-db and cleanup commands)",
    )
    parser.add_argument(
        "--database-url",
        help="Override SHU_DATABASE_URL",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompts for destructive operations",
    )

    args = parser.parse_args()

    # Get database URL
    url = _get_database_url(args.database_url)

    # Execute command
    success = False

    if args.command == "migrate":
        success = cmd_migrate(url)
    elif args.command == "setup":
        success = cmd_setup(url)
    elif args.command == "reset":
        success = cmd_reset(url, force=args.force)
    elif args.command == "create-db":
        if not args.db_name:
            print("Error: db_name is required for create-db command", file=sys.stderr)
            sys.exit(1)
        success = cmd_create_db(url, args.db_name)
    elif args.command == "cleanup":
        if not args.db_name:
            print("Error: db_name is required for cleanup command", file=sys.stderr)
            sys.exit(1)
        success = cmd_cleanup(url, args.db_name, force=args.force)
    elif args.command == "check":
        success = cmd_check(url)

    sys.exit(0 if success else 1)


if __name__ == "__main__":  # pragma: no cover
    main()

