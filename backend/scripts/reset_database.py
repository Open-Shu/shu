#!/usr/bin/env python3
"""
Shu Database Reset Script

This script resets the PostgreSQL database by dropping and recreating the schema.
Use this when you need to start fresh with a clean database.

Usage:
    python scripts/reset_database.py
    python scripts/reset_database.py --database-url postgresql://user:pass@localhost/shu
"""

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    import psycopg2
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Error: Required packages not installed: {e}")
    print("Please run: pip install -r requirements.txt")
    sys.exit(1)


def reset_database(database_url: str):
    """Reset the database schema."""
    print(f"Resetting database schema...")
    
    try:
        result = urlparse(database_url)
        
        # Connect to the database
        conn = psycopg2.connect(
            database=result.path[1:],
            user=result.username,
            password=result.password,
            host=result.hostname,
            port=result.port
        )
        
        # Drop and recreate schema
        cur = conn.cursor()
        print("  Dropping existing schema...")
        cur.execute('DROP SCHEMA public CASCADE')
        
        print("  Creating new schema...")
        cur.execute('CREATE SCHEMA public')
        
        print("  Creating vector extension...")
        cur.execute('CREATE EXTENSION IF NOT EXISTS vector')
        
        conn.commit()
        cur.close()
        conn.close()
        
        print("Database schema reset successfully!")
        return True
        
    except Exception as e:
        print(f"Error resetting database: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Shu Database Reset Script")
    parser.add_argument(
        "--database-url",
        help="PostgreSQL database URL (e.g., postgresql://user:pass@localhost/shu)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt"
    )
    
    args = parser.parse_args()
    
    # Get database URL
    database_url = args.database_url
    if not database_url:
        # Try to load from environment
        load_dotenv(override=True)
        database_url = os.getenv('SHU_DATABASE_URL')
        
        if not database_url:
            print("Error: No database URL provided. Use --database-url or set SHU_DATABASE_URL environment variable.")
            sys.exit(1)
    
    # Safety confirmation
    if not args.force:
        print("WARNING: This will completely reset your database!")
        print("   All existing data will be lost.")
        response = input("Are you sure you want to continue? (yes/no): ").strip().lower()
        if response != 'yes':
            print("Operation cancelled.")
            sys.exit(0)
    
    # Reset the database
    if reset_database(database_url):
        print("\nDatabase reset completed!")
        print("Run 'python scripts/setup_database.py' to recreate the tables.")
    else:
        print("\nDatabase reset failed!")
        sys.exit(1)


if __name__ == "__main__":
    main() 