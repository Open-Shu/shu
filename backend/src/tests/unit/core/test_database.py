"""Unit tests for database engine construction."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import ProgrammingError

from shu.core import database
from shu.core.exceptions import DatabaseSessionError


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


def _patch_engine_with_query_result(monkeypatch, *, query_result):
    """Wire get_async_engine() to return a mock whose conn.execute resolves to query_result.

    query_result may be:
      - a tuple/list/None to be returned as the .first() row
      - an Exception instance to be raised by conn.execute
    """
    conn = AsyncMock()
    if isinstance(query_result, Exception):
        conn.execute.side_effect = query_result
    else:
        result = MagicMock()
        result.first.return_value = query_result
        conn.execute.return_value = result

    begin_ctx = AsyncMock()
    begin_ctx.__aenter__.return_value = conn
    begin_ctx.__aexit__.return_value = None

    engine = MagicMock()
    engine.begin.return_value = begin_ctx
    monkeypatch.setattr(database, "get_async_engine", lambda: engine)
    return conn


class TestVerifySchemaVersion:
    """SHU-763: shu-api refuses to start unless alembic_version matches expected head.

    All cases stub the alembic ScriptDirectory + the model registry so we exercise
    the verification path itself rather than alembic resolution or import side
    effects.
    """

    def _patch_alembic_head(self, monkeypatch, head: str | None = "abc123") -> None:
        from pathlib import Path

        monkeypatch.setattr(
            Path,
            "exists",
            lambda self: True,
        )
        script_dir = MagicMock()
        script_dir.get_current_head.return_value = head
        monkeypatch.setattr("alembic.script.ScriptDirectory.from_config", lambda _cfg: script_dir)
        monkeypatch.setattr("alembic.config.Config", lambda _path: MagicMock())
        monkeypatch.setattr("shu.models.registry.register_all_models", lambda: None)

    @pytest.mark.asyncio
    async def test_returns_cleanly_when_versions_match(self, monkeypatch) -> None:
        self._patch_alembic_head(monkeypatch, head="abc123")
        _patch_engine_with_query_result(monkeypatch, query_result=("abc123",))

        await database.verify_schema_version()  # no exception = pass

    @pytest.mark.asyncio
    async def test_raises_on_version_mismatch(self, monkeypatch) -> None:
        self._patch_alembic_head(monkeypatch, head="abc123")
        _patch_engine_with_query_result(monkeypatch, query_result=("def456",))

        with pytest.raises(DatabaseSessionError, match="schema at revision 'def456'"):
            await database.verify_schema_version()

    @pytest.mark.asyncio
    async def test_raises_when_alembic_version_table_missing(self, monkeypatch) -> None:
        self._patch_alembic_head(monkeypatch, head="abc123")
        _patch_engine_with_query_result(
            monkeypatch,
            query_result=ProgrammingError("SELECT", {}, Exception("relation does not exist")),
        )

        with pytest.raises(DatabaseSessionError, match="alembic_version table missing"):
            await database.verify_schema_version()

    @pytest.mark.asyncio
    async def test_raises_when_version_row_absent(self, monkeypatch) -> None:
        self._patch_alembic_head(monkeypatch, head="abc123")
        _patch_engine_with_query_result(monkeypatch, query_result=None)

        with pytest.raises(DatabaseSessionError, match="alembic_version row missing"):
            await database.verify_schema_version()

    @pytest.mark.asyncio
    async def test_raises_when_version_value_is_null(self, monkeypatch) -> None:
        self._patch_alembic_head(monkeypatch, head="abc123")
        _patch_engine_with_query_result(monkeypatch, query_result=(None,))

        with pytest.raises(DatabaseSessionError, match="alembic_version row missing"):
            await database.verify_schema_version()
