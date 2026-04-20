"""Unit tests for database engine construction."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from shu.core import database


def _stub_settings(*, use_pgbouncer: bool) -> SimpleNamespace:
    return SimpleNamespace(
        database_pool_size=20,
        database_max_overflow=30,
        database_pool_timeout=30,
        database_pool_recycle=3600,
        debug=False,
        use_pgbouncer=use_pgbouncer,
    )


class TestGetAsyncEnginePgBouncer:
    """Verifies the PgBouncer flag routes through to asyncpg's connect_args."""

    def setup_method(self) -> None:
        # Reset the module-level singleton so each test constructs a fresh engine.
        database._async_engine = None

    def teardown_method(self) -> None:
        database._async_engine = None

    def test_pgbouncer_enabled_disables_statement_cache(self) -> None:
        with (
            patch.object(database, "create_async_engine", return_value=MagicMock()) as mock_engine,
            patch.object(database, "get_database_url", return_value="postgresql+asyncpg://u:p@h/db"),
            patch.object(database, "get_settings", return_value=_stub_settings(use_pgbouncer=True)),
        ):
            database.get_async_engine()

        assert mock_engine.call_args.kwargs["connect_args"] == {"statement_cache_size": 0}

    def test_pgbouncer_disabled_passes_empty_connect_args(self) -> None:
        with (
            patch.object(database, "create_async_engine", return_value=MagicMock()) as mock_engine,
            patch.object(database, "get_database_url", return_value="postgresql+asyncpg://u:p@h/db"),
            patch.object(database, "get_settings", return_value=_stub_settings(use_pgbouncer=False)),
        ):
            database.get_async_engine()

        assert mock_engine.call_args.kwargs["connect_args"] == {}
