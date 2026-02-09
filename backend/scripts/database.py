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
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import alembic.command
import alembic.config
import psycopg2
from alembic.script import ScriptDirectory
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


# Default connection parameters
DEFAULT_HOST = "localhost"
DEFAULT_PORT = "5432"
DEFAULT_USER = "shu"
DEFAULT_PASSWORD = "password"
DEFAULT_DATABASE = "shu"

# Default admin credentials (for create-role, create-db)
DEFAULT_ADMIN_USER = "postgres"
DEFAULT_ADMIN_PASSWORD = "postgres"


def _normalize_url(url: str) -> str:
    """Convert async URL to sync URL for psycopg2/Alembic."""
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


def _build_database_url(
    host: str | None = None,
    port: str | None = None,
    user: str | None = None,
    password: str | None = None,
    database: str | None = None,
    base_url: str | None = None,
) -> str:
    """Build a database URL from components, with defaults.

    Priority:
    1. Individual component flags (--host, --user, etc.)
    2. Base URL (--database-url or SHU_DATABASE_URL)
    3. Defaults (localhost:5432, shu:password, database=shu)
    """
    # Start with defaults
    final_host = DEFAULT_HOST
    final_port = DEFAULT_PORT
    final_user = DEFAULT_USER
    final_password = DEFAULT_PASSWORD
    final_database = DEFAULT_DATABASE

    # Override from base URL if provided
    if base_url:
        parsed = urlparse(base_url)
        if parsed.hostname:
            final_host = parsed.hostname
        if parsed.port:
            final_port = str(parsed.port)
        if parsed.username:
            final_user = parsed.username
        if parsed.password is not None:
            final_password = parsed.password
        if parsed.path and parsed.path.lstrip("/"):
            final_database = parsed.path.lstrip("/")

    # Override from individual flags (highest priority)
    if host:
        final_host = host
    if port:
        final_port = port
    if user:
        final_user = user
    if password is not None:
        final_password = password
    if database:
        final_database = database

    # Build the URL
    if final_password:
        url = f"postgresql://{final_user}:{final_password}@{final_host}:{final_port}/{final_database}"
    else:
        url = f"postgresql://{final_user}@{final_host}:{final_port}/{final_database}"

    return url


def _get_database_url(
    url_override: str | None = None,
    host: str | None = None,
    port: str | None = None,
    user: str | None = None,
    password: str | None = None,
    database: str | None = None,
) -> str:
    """Get database URL from flags, environment, or defaults."""
    # Get base URL from override or environment
    base_url = url_override or os.getenv("SHU_DATABASE_URL")

    # Normalize async URL if provided
    if base_url:
        base_url = _normalize_url(base_url)

    # Build final URL with component overrides
    return _build_database_url(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        base_url=base_url,
    )


def _get_admin_url(
    url: str,
    admin_user: str | None = None,
    admin_password: str | None = None,
) -> str:
    """Get URL for connecting to 'postgres' database as admin user.

    Used for operations requiring superuser privileges (CREATE DATABASE, CREATE ROLE).
    Uses admin credentials (default: postgres:postgres), NOT the app user from the URL.
    """
    parsed = urlparse(url)

    # Use provided admin credentials, or defaults (NOT the app user from URL)
    user = admin_user or DEFAULT_ADMIN_USER
    password = admin_password if admin_password is not None else DEFAULT_ADMIN_PASSWORD

    # Build netloc with admin credentials
    if password:
        netloc = f"{user}:{password}@{parsed.hostname}"
    else:
        netloc = f"{user}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"

    # Connect to 'postgres' database
    admin_parsed = parsed._replace(netloc=netloc, path="/postgres")
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


def execute_init_sql(
    url: str,
    admin_user: str | None = None,
    admin_password: str | None = None,
) -> bool:
    """Execute init-db.sql to set up extensions, functions, and configuration.

    Uses a transaction so the setup is all-or-nothing. If any statement fails,
    the entire init is rolled back to avoid leaving the DB in a partial state.

    Requires superuser privileges to create extensions (e.g., pgvector).
    If admin credentials are provided, connects as admin to the target database.
    """
    if not INIT_SQL_PATH.is_file():
        print(f"{LOG_PREFIX} Warning: init-db.sql not found at {INIT_SQL_PATH}", file=sys.stderr)
        print(f"{LOG_PREFIX} Falling back to manual extension setup...", flush=True)
        return ensure_extensions(url)

    # Build connection URL - use admin credentials if provided
    if admin_user or admin_password:
        parsed = urlparse(url)
        user = admin_user or DEFAULT_ADMIN_USER
        password = admin_password if admin_password is not None else DEFAULT_ADMIN_PASSWORD
        if password:
            netloc = f"{user}:{password}@{parsed.hostname}"
        else:
            netloc = f"{user}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        connect_url = urlunparse(parsed._replace(netloc=netloc))
    else:
        connect_url = url

    print(f"{LOG_PREFIX} Executing init-db.sql...", flush=True)
    conn = _connect(connect_url, autocommit=False)  # Use transaction for all-or-nothing
    try:
        with open(INIT_SQL_PATH) as f:
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


def _get_current_revision(url: str) -> str | None:
    """Get the current Alembic revision from the database."""
    conn = _connect(url, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'alembic_version')")
            exists_row = cur.fetchone()
            if not exists_row or not exists_row[0]:
                return None
            cur.execute("SELECT version_num FROM alembic_version")
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _build_replaces_map(script_dir: ScriptDirectory) -> dict[str, str]:
    """Build a mapping from replaced revision IDs to their replacing revision."""
    replaces_map: dict[str, str] = {}
    for script in script_dir.walk_revisions():
        replaces = getattr(script.module, "replaces", None)
        if replaces:
            for replaced_rev in replaces:
                replaces_map[replaced_rev] = script.revision
    return replaces_map


def _get_known_revisions(script_dir: ScriptDirectory) -> set[str]:
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
            print(
                f"{LOG_PREFIX} Stamping to {down_rev} so upgrade runs the squash migration",
                flush=True,
            )
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
            cur.execute("SELECT EXISTS (SELECT FROM pg_proc WHERE proname = 'check_requirements')")
            row = cur.fetchone()
            if row is None or not row["exists"]:
                print(
                    f"{LOG_PREFIX} check_requirements() function not found, skipping check",
                    flush=True,
                )
                return True

            cur.execute("SELECT * FROM check_requirements()")
            results = cur.fetchall()

            all_ok = True
            for result in results:
                status = result["status"]
                if status == "OK":
                    icon = "OK"
                elif status == "WARNING":
                    icon = "WARN"
                elif status == "INFO":
                    icon = "INFO"
                else:
                    icon = "ERR"
                print(
                    f"{LOG_PREFIX}   [{icon}] {result['requirement']}: {result['details']}",
                    flush=True,
                )
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


def cmd_setup(
    url: str,
    admin_user: str | None = None,
    admin_password: str | None = None,
) -> bool:
    """Full database setup: init-db.sql + migrations + requirements check.

    Admin credentials are used for init-db.sql (requires superuser for extensions).
    Migrations run as the app user from the URL.
    """
    print(f"{LOG_PREFIX} Starting full database setup...", flush=True)

    # Step 1: Execute init-db.sql (needs superuser for extensions)
    if not execute_init_sql(url, admin_user, admin_password):
        return False

    # Step 2: Run migrations (as app user)
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


def cmd_reset(
    url: str,
    force: bool = False,
    admin_user: str | None = None,
    admin_password: str | None = None,
) -> bool:
    """Reset the database by dropping and recreating the public schema and then performing full setup.

    This is a destructive operation: the public schema is dropped with CASCADE (all data and dependent objects removed),
    a new public schema is created, and full privileges on that schema are granted to the current user.
    After the schema recreation, the function runs the full setup sequence (migrations, init SQL, requirements check).

    Parameters
    ----------
        url (str): Connection URL for the target database.
        force (bool): If True, skip the interactive confirmation prompt. Defaults to False.
        admin_user (Optional[str]): Optional admin/superuser name used by setup steps when elevated privileges are required.
        admin_password (Optional[str]): Optional admin/superuser password used by setup steps when elevated privileges are required.

    Returns
    -------
        success (bool): `True` if the reset and subsequent setup completed successfully, `False` on error or if cancelled.

    """
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
            # Grant full privileges on the public schema to the current user
            # (the Shu app role), rather than to PUBLIC. This avoids giving
            # CREATE on public to every role in shared/non-dev environments.
            cur.execute("GRANT ALL ON SCHEMA public TO CURRENT_USER")

        print(f"{LOG_PREFIX} Schema reset complete", flush=True)
    except psycopg2.Error as e:
        print(f"{LOG_PREFIX} Error resetting schema: {e}", file=sys.stderr)
        return False
    finally:
        conn.close()

    # Run full setup on the reset database
    return cmd_setup(url, admin_user, admin_password)


# =============================================================================
# Command: create-db
# =============================================================================


def cmd_create_role(
    url: str,
    role_name: str,
    role_password: str,
    admin_user: str | None = None,
    admin_password: str | None = None,
) -> bool:
    """Create a database role if it doesn't exist."""
    # Validate identifier to prevent SQL injection
    if not _validate_identifier(role_name):
        print(f"{LOG_PREFIX} Error: Invalid role name '{role_name}'", file=sys.stderr)
        print(
            f"{LOG_PREFIX} Names must start with a letter or underscore, contain only",
            file=sys.stderr,
        )
        print(
            f"{LOG_PREFIX} alphanumeric characters, underscores, or hyphens, and be <= 63 chars",
            file=sys.stderr,
        )
        return False

    admin_url = _get_admin_url(url, admin_user, admin_password)

    print(f"{LOG_PREFIX} Ensuring role '{role_name}' exists...", flush=True)

    conn = _connect(admin_url)
    try:
        with conn.cursor() as cur:
            # Check if role exists
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,))
            if cur.fetchone():
                print(f"{LOG_PREFIX} Role '{role_name}' already exists", flush=True)
                return True

            # Create the role with login privilege
            cur.execute(
                sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(role_name)),
                (role_password,),
            )
            print(f"{LOG_PREFIX} Role '{role_name}' created successfully", flush=True)
            return True
    except psycopg2.Error as e:
        print(f"{LOG_PREFIX} Error creating role: {e}", file=sys.stderr)
        return False
    finally:
        conn.close()


# =============================================================================
# Command: create-db
# =============================================================================


def cmd_create_db(
    url: str,
    db_name: str,
    admin_user: str | None = None,
    admin_password: str | None = None,
) -> bool:
    """Create a new database and grant permissions to the app role."""
    # Validate identifier to prevent SQL injection
    if not _validate_identifier(db_name):
        print(f"{LOG_PREFIX} Error: Invalid database name '{db_name}'", file=sys.stderr)
        print(
            f"{LOG_PREFIX} Names must start with a letter or underscore, contain only",
            file=sys.stderr,
        )
        print(
            f"{LOG_PREFIX} alphanumeric characters, underscores, or hyphens, and be <= 63 chars",
            file=sys.stderr,
        )
        return False

    admin_url = _get_admin_url(url, admin_user, admin_password)

    # Get the app role name from the target URL
    parsed = urlparse(url)
    role_name = parsed.username or DEFAULT_USER

    print(f"{LOG_PREFIX} Creating database '{db_name}'...", flush=True)

    conn = _connect(admin_url)
    db_created = False
    try:
        with conn.cursor() as cur:
            # Check if database exists
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone():
                print(f"{LOG_PREFIX} Database '{db_name}' already exists", flush=True)
            else:
                # Create the database using safe identifier quoting
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
                print(f"{LOG_PREFIX} Database '{db_name}' created successfully", flush=True)
                db_created = True
    except psycopg2.Error as e:
        print(f"{LOG_PREFIX} Error creating database: {e}", file=sys.stderr)
        return False
    finally:
        conn.close()

    # Grant permissions on the public schema (required for PostgreSQL 15+)
    # Need to connect to the new database to do this
    db_admin_url = _get_admin_url(url, admin_user, admin_password)
    # Replace 'postgres' database with the target database
    db_admin_url = db_admin_url.replace("/postgres", f"/{db_name}")

    conn = _connect(db_admin_url)
    try:
        with conn.cursor() as cur:
            # Grant usage and create on public schema to the app role
            cur.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(sql.Identifier(role_name)))
            # Grant all privileges on all tables (for future tables too)
            cur.execute(
                sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {}").format(
                    sql.Identifier(role_name)
                )
            )
            cur.execute(
                sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {}").format(
                    sql.Identifier(role_name)
                )
            )
            if db_created:
                print(f"{LOG_PREFIX} Granted schema permissions to role '{role_name}'", flush=True)
        return True
    except psycopg2.Error as e:
        print(f"{LOG_PREFIX} Error granting permissions: {e}", file=sys.stderr)
        return False
    finally:
        conn.close()


# =============================================================================
# Command: cleanup
# =============================================================================


def cmd_cleanup(
    url: str,
    db_name: str,
    force: bool = False,
    admin_user: str | None = None,
    admin_password: str | None = None,
) -> bool:
    """Drop a database."""
    # Validate identifier to prevent SQL injection
    if not _validate_identifier(db_name):
        print(f"{LOG_PREFIX} Error: Invalid database name '{db_name}'", file=sys.stderr)
        print(
            f"{LOG_PREFIX} Names must start with a letter or underscore, contain only",
            file=sys.stderr,
        )
        print(
            f"{LOG_PREFIX} alphanumeric characters, underscores, or hyphens, and be <= 63 chars",
            file=sys.stderr,
        )
        return False

    admin_url = _get_admin_url(url, admin_user, admin_password)

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
# Command: bootstrap
# =============================================================================


def cmd_bootstrap(
    url: str,
    admin_user: str | None = None,
    admin_password: str | None = None,
) -> bool:
    """Bootstrap a fresh database: create role, create database, run setup.

    This is the one-command solution for local dev and docker compose deployments.
    Requires superuser (postgres) credentials.
    """
    # Extract role and database info from the target URL
    parsed = urlparse(url)
    role_name = parsed.username or DEFAULT_USER
    role_password = parsed.password or DEFAULT_PASSWORD
    db_name = _get_database_name(url)

    print(f"{LOG_PREFIX} Bootstrapping database '{db_name}' with role '{role_name}'...", flush=True)

    # Step 1: Create role
    if not cmd_create_role(url, role_name, role_password, admin_user, admin_password):
        return False

    # Step 2: Create database
    if not cmd_create_db(url, db_name, admin_user, admin_password):
        return False

    # Step 3: Run setup (init-db.sql needs admin for extensions, migrations run as app user)
    if not cmd_setup(url, admin_user, admin_password):
        return False

    print(f"{LOG_PREFIX} Bootstrap complete!", flush=True)
    return True


# =============================================================================
# CLI Entry Point
# =============================================================================


def main() -> None:
    default_url = (
        f"postgresql+asyncpg://{DEFAULT_USER}:{DEFAULT_PASSWORD}@{DEFAULT_HOST}:{DEFAULT_PORT}/{DEFAULT_DATABASE}"
    )

    parser = argparse.ArgumentParser(
        description="Shu Database Management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Commands:
  bootstrap              Full initialization: create role, create database, run setup
                         (recommended for fresh local dev or docker compose)
  migrate                Run Alembic migrations to latest revision
  setup                  Run init-db.sql + migrations + requirements check
  reset                  Drop and recreate schema, then run setup (requires --force)
  create-role            Create the database role if it doesn't exist
  create-db <name>       Create a new database with the given name
  cleanup <name>         Drop a database (requires --force)
  check                  Show current revision and check requirements

Defaults:
  App connection:   {default_url}
  Admin connection: postgres:{DEFAULT_ADMIN_PASSWORD}@localhost:5432/postgres

Examples:
  # Bootstrap a fresh database (creates role, db, runs migrations):
  python scripts/database.py bootstrap

  # Run migrations only (assumes role and db exist):
  python scripts/database.py migrate

  # Full setup (init-db.sql + migrations):
  python scripts/database.py setup

  # Check database status:
  python scripts/database.py check

  # Override connection parameters:
  python scripts/database.py bootstrap --host db.example.com
  python scripts/database.py migrate --database shu_dev

  # Override admin credentials (for create-role, create-db, bootstrap):
  python scripts/database.py bootstrap --admin-user postgres --admin-password secret

  # Creating and cleaning up databases manually:
  python scripts/database.py create-db shu_test
  python scripts/database.py cleanup shu_test --force

  # Destructive operations require --force:
  python scripts/database.py reset --force
        """,
    )

    parser.add_argument(
        "command",
        nargs="?",
        default="migrate",
        choices=[
            "bootstrap",
            "migrate",
            "setup",
            "reset",
            "create-role",
            "create-db",
            "cleanup",
            "check",
        ],
        metavar="COMMAND",
        help="Command to run (default: migrate)",
    )
    parser.add_argument(
        "db_name",
        nargs="?",
        metavar="DB_NAME",
        help="Database name (for create-db and cleanup commands)",
    )

    # Connection options
    conn_group = parser.add_argument_group("connection options")
    conn_group.add_argument(
        "--database-url",
        metavar="URL",
        help="Full PostgreSQL connection URL (overrides defaults)",
    )
    conn_group.add_argument(
        "--host",
        "-H",
        metavar="HOST",
        help=f"Database host (default: {DEFAULT_HOST})",
    )
    conn_group.add_argument(
        "--port",
        "-P",
        metavar="PORT",
        help=f"Database port (default: {DEFAULT_PORT})",
    )
    conn_group.add_argument(
        "--user",
        "-U",
        metavar="USER",
        help=f"Database user (default: {DEFAULT_USER})",
    )
    conn_group.add_argument(
        "--password",
        "-W",
        metavar="PASS",
        help=f"Database password (default: {DEFAULT_PASSWORD})",
    )
    conn_group.add_argument(
        "--database",
        "-d",
        metavar="NAME",
        help=f"Database name (default: {DEFAULT_DATABASE})",
    )

    # Admin options (for superuser operations)
    admin_group = parser.add_argument_group("admin options (for bootstrap, create-role, create-db, cleanup)")
    admin_group.add_argument(
        "--admin-user",
        metavar="USER",
        help=f"PostgreSQL superuser for admin operations (default: {DEFAULT_ADMIN_USER})",
    )
    admin_group.add_argument(
        "--admin-password",
        metavar="PASS",
        help=f"PostgreSQL superuser password (default: {DEFAULT_ADMIN_PASSWORD})",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation for destructive operations (reset, cleanup)",
    )

    args = parser.parse_args()

    # Get database URL with component overrides
    url = _get_database_url(
        url_override=args.database_url,
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
    )

    # Execute command
    success = False

    if args.command == "bootstrap":
        success = cmd_bootstrap(url, args.admin_user, args.admin_password)
    elif args.command == "migrate":
        success = cmd_migrate(url)
    elif args.command == "setup":
        success = cmd_setup(url, args.admin_user, args.admin_password)
    elif args.command == "reset":
        success = cmd_reset(url, force=args.force, admin_user=args.admin_user, admin_password=args.admin_password)
    elif args.command == "create-role":
        # Extract role info from URL
        parsed = urlparse(url)
        role_name = parsed.username or DEFAULT_USER
        role_password = parsed.password or DEFAULT_PASSWORD
        success = cmd_create_role(url, role_name, role_password, args.admin_user, args.admin_password)
    elif args.command == "create-db":
        if not args.db_name:
            print("Error: db_name is required for create-db command", file=sys.stderr)
            sys.exit(1)
        success = cmd_create_db(url, args.db_name, args.admin_user, args.admin_password)
    elif args.command == "cleanup":
        if not args.db_name:
            print("Error: db_name is required for cleanup command", file=sys.stderr)
            sys.exit(1)
        success = cmd_cleanup(
            url,
            args.db_name,
            force=args.force,
            admin_user=args.admin_user,
            admin_password=args.admin_password,
        )
    elif args.command == "check":
        success = cmd_check(url)

    sys.exit(0 if success else 1)


if __name__ == "__main__":  # pragma: no cover
    main()
