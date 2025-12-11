#!/usr/bin/env python3
"""
Shu Database Setup Script

This script sets up a PostgreSQL database for Shu RAG Backend with all required extensions,
configurations, and initial setup. It can be used with local or remote PostgreSQL instances.

Usage:
    python scripts/setup_database.py --database-url postgresql://user:pass@localhost/shu
    python scripts/setup_database.py --interactive
    python scripts/setup_database.py --check-only
"""

import argparse
import os
import sys
import logging
from urllib.parse import urlparse
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import alembic.config
    import alembic.command
except ImportError as e:
    print(f"Error: Required packages not installed: {e}")
    print("Please run: pip install -r requirements.txt")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ShuDatabaseSetup:
    """Handles Shu database setup and configuration."""
    
    def __init__(self, database_url: str):
        # Convert async URL to sync URL for psycopg2
        if database_url.startswith("postgresql+asyncpg://"):
            self.database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        else:
            self.database_url = database_url
        self.parsed_url = urlparse(self.database_url)
        self.connection = None
        
    def connect(self):
        """Connect to the database."""
        try:
            self.connection = psycopg2.connect(self.database_url)
            self.connection.autocommit = True
            logger.info(f"Connected to database: {self.parsed_url.hostname}:{self.parsed_url.port}/{self.parsed_url.path[1:]}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from the database."""
        if self.connection:
            self.connection.close()
            logger.info("Disconnected from database")
    
    def execute_sql_file(self, file_path: str):
        """Execute SQL commands from a file."""
        try:
            with open(file_path, 'r') as f:
                sql_content = f.read()
            
            with self.connection.cursor() as cursor:
                cursor.execute(sql_content)
                
            logger.info(f"Successfully executed SQL file: {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to execute SQL file {file_path}: {e}")
            return False
    
    def check_requirements(self):
        """Check if the database meets Shu requirements."""
        try:
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SELECT * FROM check_requirements();")
                results = cursor.fetchall()
                
                print("\n=== Shu Database Requirements Check ===")
                all_ok = True
                
                for result in results:
                    status_emoji = "✅" if result['status'] == 'OK' else "⚠️" if result['status'] == 'WARNING' else "❌"
                    print(f"{status_emoji} {result['requirement']}: {result['status']}")
                    print(f"   {result['details']}")
                    
                    if result['status'] == 'ERROR':
                        all_ok = False
                
                print(f"\n{'✅ All requirements met!' if all_ok else '❌ Some requirements not met - please fix errors above'}")
                return all_ok
                
        except Exception as e:
            logger.error(f"Failed to check requirements: {e}")
            return False
    
    def run_migrations(self):
        """Run Alembic migrations to create tables."""
        try:
            # Get the alembic.ini path
            alembic_ini = Path(__file__).parent.parent / "alembic.ini"
            
            # Create Alembic configuration
            alembic_cfg = alembic.config.Config(str(alembic_ini))
            
            # Override the database URL directly in the Alembic config
            # Use the sync URL for Alembic
            alembic_cfg.set_main_option("sqlalchemy.url", self.database_url)
            
            logger.info(f"Running migrations with database URL: {self.database_url}")
            
            # Run migrations
            alembic.command.upgrade(alembic_cfg, "head")
            
            logger.info("Successfully ran database migrations")
            return True
        except Exception as e:
            logger.error(f"Failed to run migrations: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def setup_database(self, run_migrations=True):
        """Complete database setup process."""
        logger.info("Starting Shu database setup...")
        
        # Execute initialization SQL
        init_sql_path = Path(__file__).parent.parent / "init-db.sql"
        if not self.execute_sql_file(str(init_sql_path)):
            return False
        
        # Run migrations if requested
        if run_migrations:
            if not self.run_migrations():
                return False
        
        # Check requirements
        if not self.check_requirements():
            return False
        
        logger.info("Shu database setup completed successfully!")
        return True


def interactive_setup():
    """Interactive setup mode."""
    print("=== Shu Database Interactive Setup ===")
    print("Please provide your PostgreSQL connection details:")
    
    host = input("Host (default: localhost): ").strip() or "localhost"
    port = input("Port (default: 5432): ").strip() or "5432"
    database = input("Database name (default: shu): ").strip() or "shu"
    username = input("Username: ").strip()
    password = input("Password: ").strip()
    
    if not username:
        print("Username is required!")
        return None
    
    database_url = f"postgresql://{username}:{password}@{host}:{port}/{database}"
    return database_url


def main():
    parser = argparse.ArgumentParser(description="Shu Database Setup Script")
    parser.add_argument(
        "--database-url",
        help="PostgreSQL database URL (e.g., postgresql://user:pass@localhost/shu)"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run in interactive mode to input database connection details"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check requirements, don't perform setup"
    )
    parser.add_argument(
        "--skip-migrations",
        action="store_true",
        help="Skip running Alembic migrations"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Get database URL
    database_url = None
    
    if args.interactive:
        database_url = interactive_setup()
    elif args.database_url:
        database_url = args.database_url
    else:
        # Try to get from environment
        database_url = os.getenv('SHU_DATABASE_URL')
        if not database_url:
            print("Error: No database URL provided!")
            print("Use --database-url, --interactive, or set SHU_DATABASE_URL environment variable")
            sys.exit(1)
    
    if not database_url:
        sys.exit(1)
    
    # Set up database
    setup = ShuDatabaseSetup(database_url)
    
    if not setup.connect():
        sys.exit(1)
    
    try:
        if args.check_only:
            success = setup.check_requirements()
        else:
            success = setup.setup_database(run_migrations=not args.skip_migrations)
        
        if success:
            print("\nDatabase setup completed successfully!")
            print("Your Shu database is ready to use.")
            print("\nNext steps:")
            print("1. Start the Shu API server")
            print("2. Create a knowledge base")
            print("3. Sync some documents")
            print("4. Test queries")
        else:
            print("\nDatabase setup failed!")
            print("Please check the errors above and try again.")
            sys.exit(1)
            
    finally:
        setup.disconnect()


if __name__ == "__main__":
    main() 