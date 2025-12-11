#!/usr/bin/env python3
"""
Script to set up the test database for Shu custom test framework.
This creates a separate test database to avoid affecting development data.
"""

import asyncio
import sys
import os
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

# Database URLs
DEV_DATABASE_URL = "postgresql+asyncpg://shu:@localhost:5432/shu"
TEST_DATABASE_URL = "postgresql+asyncpg://shu:@localhost:5432/shu_test"

def create_test_database():
    """Create the test database."""
    print("Setting up test database...")
    
    # Connect to the main database to create the test database
    engine = create_engine(DEV_DATABASE_URL.replace("+asyncpg", ""))
    
    try:
        # Check if test database exists
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = 'shu_test'"))
            db_exists = result.fetchone() is not None
            
            if db_exists:
                print("Test database 'shu_test' already exists")
                return True
            else:
                # Create test database if it doesn't exist
                conn.execute(text("COMMIT"))  # Close any open transaction
                conn.execute(text("CREATE DATABASE shu_test"))
                print("Test database 'shu_test' created successfully")
                return True
    except Exception as e:
        print(f"Error creating test database: {e}")
        print("   Make sure PostgreSQL is running and you have permission to create databases")
        return False
    finally:
        engine.dispose()

def enable_pgvector_extension():
    """Enable the pgvector extension in the test database."""
    print("Enabling pgvector extension in test database...")
    # Use sync SQLAlchemy engine for extension creation
    test_engine = create_engine(TEST_DATABASE_URL.replace("+asyncpg", ""))
    try:
        with test_engine.connect() as conn:
            dbname = conn.execute(text("SELECT current_database()")).scalar()
            print(f"   Connected to database: {dbname}")
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            conn.commit()
            # Check if extension is enabled
            ext = conn.execute(text("SELECT extname FROM pg_extension WHERE extname = 'vector'"))
            if ext.fetchone() is None:
                print("pgvector extension was not enabled. Check your PostgreSQL setup.")
                return False
        print("pgvector extension enabled in test database")
        return True
    except Exception as e:
        print(f"Error enabling pgvector extension: {e}")
        print("   Make sure the pgvector extension is installed in your PostgreSQL instance.")
        return False
    finally:
        test_engine.dispose()

async def create_test_tables():
    """Create tables in the test database using project models."""
    print("Creating test database tables...")
    
    # Try to import project models
    try:
        # Add the project root to the path
        project_root = Path(__file__).parent.parent
        sys.path.insert(0, str(project_root))
        
        # Import from the project
        from shu.models.base import Base
        
        # Create tables in the test database
        test_engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            print("Test database tables created successfully")
            return True
        except Exception as e:
            print(f"Error creating test tables: {e}")
            return False
        finally:
            await test_engine.dispose()
            
    except ImportError as e:
        print(f"Could not import project models: {e}")
        print("   Tables will be created automatically when you run tests")
        return True
    except Exception as e:
        print(f"Unexpected error creating tables: {e}")
        return False

async def setup_test_database():
    """Set up the test database with tables."""
    if not create_test_database():
        return False

    if not enable_pgvector_extension():
        return False
    
    if not await create_test_tables():
        return False
    
    print("Test database setup complete!")
    return True

def cleanup_test_database():
    """Clean up the test database."""
    print("Cleaning up test database...")
    
    engine = create_engine(DEV_DATABASE_URL.replace("+asyncpg", ""))
    try:
        with engine.connect() as conn:
            # Check if database exists
            result = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = 'shu_test'"))
            if result.fetchone() is None:
                print("Test database 'shu_test' does not exist")
                return
            
            # Terminate all connections to the test database
            conn.execute(text("COMMIT"))
            conn.execute(text("""
                SELECT pg_terminate_backend(pid) 
                FROM pg_stat_activity 
                WHERE datname = 'shu_test' AND pid <> pg_backend_pid()
            """))
            
            # Drop the database
            conn.execute(text("DROP DATABASE IF EXISTS shu_test"))
        print("Test database cleaned up successfully")
    except Exception as e:
        print(f"Error cleaning up test database: {e}")
        print("   You may need to manually close connections to the test database")
    finally:
        engine.dispose()

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Set up test database for Shu RAG Backend")
    parser.add_argument("--cleanup", action="store_true", help="Clean up test database instead of setting it up")
    parser.add_argument("--no-tables", action="store_true", help="Skip table creation (tables will be created by tests)")
    
    args = parser.parse_args()
    
    if args.cleanup:
        cleanup_test_database()
    else:
        if args.no_tables:
            # Just create the database, skip table creation
            create_test_database()
        else:
            # Create database and tables
            asyncio.run(setup_test_database()) 