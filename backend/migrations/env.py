import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool, text

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


def get_migration_database_url():
    """Get database URL from environment or config.

    SHU-761: migrations should run as ``shu_admin`` (BYPASSRLS), so this
    prefers ``SHU_DB_ADMIN_URL``. ``SHU_DATABASE_URL`` is kept as a fallback
    because it's the only URL that exists pre-Stage-C (before shu_admin is
    created). The role guard in ``run_migrations_online`` then enforces that
    the actually-connecting role is never ``shu_app``.
    """
    alembic_url = config.get_main_option("sqlalchemy.url")
    if alembic_url:
        return alembic_url

    try:
        from shu.core.config import get_settings_instance

        settings = get_settings_instance()
        database_url = settings.db_admin_url or settings.database_url
    except Exception:
        import os

        database_url = os.getenv("SHU_DB_ADMIN_URL") or os.getenv("SHU_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "Could not determine database URL. Set SHU_DB_ADMIN_URL (preferred) "
                "or SHU_DATABASE_URL, or configure Alembic directly."
            ) from None

    # asyncpg dialect doesn't speak to Alembic; convert to the sync driver.
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
    url = get_migration_database_url()
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
    configuration["sqlalchemy.url"] = get_migration_database_url()
    configuration["sqlalchemy.echo"] = "True"

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # shu_app is the non-bypassing app role; migrations under it would be
        # blocked by RLS or fail privilege checks. Refuse loudly rather than
        # discovering it mid-upgrade. Other roles (shu_admin, superuser used
        # pre-Stage-C) are allowed — we know the forbidden set, not the full
        # allowed set, so this is an asymmetric check by design.
        current_user = connection.execute(text("SELECT current_user")).scalar()
        if current_user == "shu_app":
            raise RuntimeError(
                "Migrations must not run as the 'shu_app' role — that role lacks BYPASSRLS "
                "and migration privileges. Point SHU_DB_ADMIN_URL at the 'shu_admin' role."
            )

        # SQLAlchemy 2.0 autobegin: the role-check ``execute`` above opened
        # an implicit transaction on this connection. Alembic's
        # MigrationContext.__init__ inspects ``connection.in_transaction()``;
        # if True it sets ``_in_external_transaction=True`` and then
        # ``begin_transaction()`` returns a no-op nullcontext, leaving
        # ``_transaction`` as None — which makes ``autocommit_block()``
        # assert. Rolling back the role-check tx (no writes to lose)
        # leaves alembic to manage transactions itself.
        connection.rollback()

        context.configure(
            connection=connection,
            target_metadata=get_target_metadata(),
        )

        # Postgres supports transactional DDL, so the standard alembic
        # pattern is the right one: one outer transaction wraps the run,
        # and `op.get_context().autocommit_block()` (used by 009's
        # CREATE INDEX CONCURRENTLY block) can interrupt it — commit
        # the current tx, run the body in autocommit, open a fresh tx
        # on exit. Note that `transactional_ddl=False` would defeat
        # this: with the override, `begin_transaction()` returns a
        # nullcontext and `_transaction` stays None, then
        # `autocommit_block()` asserts on entry. The override has been
        # removed.
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
