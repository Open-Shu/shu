import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add the src directory to the path so we can import our models
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def get_target_metadata():
    """Get target metadata lazily to avoid Settings validation issues."""
    # Import the models and database configuration only when needed
    from shu.core.database import Base

    return Base.metadata


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_database_url():
    """Get database URL from environment or config."""
    # First try to get the URL from the Alembic configuration
    # This allows the setup script to override it directly
    alembic_url = config.get_main_option("sqlalchemy.url")
    if alembic_url:
        return alembic_url

    # Fall back to the Settings class for normal operation
    try:
        from shu.core.config import get_settings_instance

        settings = get_settings_instance()
        database_url = settings.database_url
    except Exception:
        # If Settings fails, try to get from environment directly
        import os

        database_url = os.getenv("SHU_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "Could not determine database URL. Please set SHU_DATABASE_URL environment variable or configure Alembic directly."
            )

    # Convert async URL to sync URL for Alembic
    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    return database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=get_target_metadata(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Override the sqlalchemy.url from the config
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_database_url()
    configuration["sqlalchemy.echo"] = "True"

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=get_target_metadata(),
            transactional_ddl=False,
        )

        # Run migrations without an explicit transaction block to avoid
        # aborting the whole upgrade on benign cleanup differences.
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
