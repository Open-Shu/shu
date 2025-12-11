#!/usr/bin/env python3
"""
Database migration runner for Shu RAG Backend.

This script handles database migrations using Alembic.
"""

import os
import sys
import subprocess
from pathlib import Path
import argparse

# Add the src directory to the path
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))

def run_command(cmd, cwd=None):
    """Run a command and return its output."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd or project_root,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {cmd}")
        print(f"Error output: {e.stderr}")
        raise

def create_migration(message):
    """Create a new migration."""
    cmd = f"alembic revision --autogenerate -m '{message}'"
    print(f"Creating migration: {message}")
    output = run_command(cmd)
    print(output)

def upgrade_database(revision="head"):
    """Upgrade database to a specific revision."""
    cmd = f"alembic upgrade {revision}"
    print(f"Upgrading database to revision: {revision}")
    output = run_command(cmd)
    print(output)

def downgrade_database(revision):
    """Downgrade database to a specific revision."""
    cmd = f"alembic downgrade {revision}"
    print(f"Downgrading database to revision: {revision}")
    output = run_command(cmd)
    print(output)

def show_history():
    """Show migration history."""
    cmd = "alembic history --verbose"
    print("Migration history:")
    output = run_command(cmd)
    print(output)

def show_current():
    """Show current database revision."""
    cmd = "alembic current"
    print("Current database revision:")
    output = run_command(cmd)
    print(output)

def check_database_url():
    """Check if database URL is configured."""
    try:
        from shu.core.config import get_settings_instance
        settings = get_settings_instance()
        if not settings.database_url:
            print("Error: SHU_DATABASE_URL not configured")
            print("Please set SHU_DATABASE_URL environment variable or configure it in .env file")
            sys.exit(1)
        print(f"Database URL: {settings.database_url}")
    except Exception as e:
        print(f"Error checking database configuration: {e}")
        sys.exit(1)

def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Shu Database Migration Tool")
    parser.add_argument("command", choices=[
        "create", "upgrade", "downgrade", "history", "current", "check"
    ], help="Migration command to run")
    parser.add_argument("--message", "-m", help="Migration message (for create command)")
    parser.add_argument("--revision", "-r", help="Target revision (for upgrade/downgrade)")
    
    args = parser.parse_args()
    
    # Check database configuration
    if args.command != "check":
        check_database_url()
    
    try:
        if args.command == "create":
            if not args.message:
                print("Error: Migration message is required for create command")
                print("Use: python scripts/run_migrations.py create -m 'Your migration message'")
                sys.exit(1)
            create_migration(args.message)
        
        elif args.command == "upgrade":
            revision = args.revision or "head"
            upgrade_database(revision)
        
        elif args.command == "downgrade":
            if not args.revision:
                print("Error: Revision is required for downgrade command")
                print("Use: python scripts/run_migrations.py downgrade -r revision_id")
                sys.exit(1)
            downgrade_database(args.revision)
        
        elif args.command == "history":
            show_history()
        
        elif args.command == "current":
            show_current()
        
        elif args.command == "check":
            check_database_url()
            print("Database configuration is valid")
        
        print("Migration command completed successfully")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 