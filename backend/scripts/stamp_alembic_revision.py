#!/usr/bin/env python3
"""
One-off admin script to stamp the database's Alembic version to a given revision
when legacy revisions were squashed and are no longer present in the repo.

Use only in development. Prefer `alembic stamp <rev>`; this script is a fallback
when Alembic cannot locate an old revision id (e.g., '005') after a squash.

Usage:
  SHU_DATABASE_URL=postgresql://... python scripts/stamp_alembic_revision.py --rev 006
"""
import argparse
import os
import sys
from pathlib import Path

# Ensure src is on path (for env loaders if needed)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    import psycopg2
except Exception as e:
    print("psycopg2 not installed. Run: pip install -r requirements.txt")
    raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rev", required=True, help="Target revision to stamp (e.g., 006)")
    args = parser.parse_args()

    # Accept async URL and convert to sync
    db_url = os.getenv("SHU_DATABASE_URL")
    if not db_url:
        # Attempt to load from settings (loads .env via our config system)
        try:
            from shu.core.config import get_settings_instance
            db_url = get_settings_instance().database_url
        except Exception:
            pass
    if not db_url:
        print("SHU_DATABASE_URL is not set and settings were not available.")
        sys.exit(1)
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

    print(f"Stamping alembic_version to {args.rev} on {db_url}")

    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # Ensure alembic_version table exists
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS alembic_version (
                    version_num VARCHAR(32) NOT NULL
                );
                """
            )
            # If table has multiple rows (rare), clear it to a single row
            cur.execute("SELECT COUNT(*) FROM alembic_version;")
            count = cur.fetchone()[0]
            if count == 0:
                cur.execute("INSERT INTO alembic_version (version_num) VALUES (%s);", (args.rev,))
            else:
                # Normalize to a single row to avoid UniqueViolation when multiple rows exist
                cur.execute("DELETE FROM alembic_version;")
                cur.execute("INSERT INTO alembic_version (version_num) VALUES (%s);", (args.rev,))
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

