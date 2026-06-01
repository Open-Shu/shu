"""Unit tests for database engine construction."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import ProgrammingError

from shu.core import database
from shu.core.exceptions import DatabaseSessionError
from shu.core.tenant import (
    CrossTenantInsertError,
    MissingTenantContextError,
    tenant_context,
)


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


class TestResolveAlembicHead:
    """Smoke test against the real bundled migrations directory."""

    def test_returns_single_head_from_real_migrations(self) -> None:
        head = database._resolve_alembic_head()
        assert head, "expected a non-empty head revision"


class TestVerifySchemaVersion:
    """shu-api refuses to start unless alembic_version matches the bundled head."""

    def _stub_head(self, monkeypatch, value: str = "abc123") -> None:
        monkeypatch.setattr(database, "_resolve_alembic_head", lambda: value)

    @pytest.mark.asyncio
    async def test_returns_cleanly_when_versions_match(self, monkeypatch) -> None:
        self._stub_head(monkeypatch, "abc123")
        _patch_engine_with_query_result(monkeypatch, query_result=("abc123",))

        await database.verify_schema_version()  # no exception = pass

    @pytest.mark.asyncio
    async def test_raises_on_version_mismatch(self, monkeypatch) -> None:
        self._stub_head(monkeypatch, "abc123")
        _patch_engine_with_query_result(monkeypatch, query_result=("def456",))

        with pytest.raises(DatabaseSessionError, match="schema at revision 'def456'"):
            await database.verify_schema_version()

    @pytest.mark.asyncio
    async def test_raises_when_alembic_version_table_missing(self, monkeypatch) -> None:
        self._stub_head(monkeypatch, "abc123")
        _patch_engine_with_query_result(
            monkeypatch,
            query_result=ProgrammingError("SELECT", {}, Exception("relation does not exist")),
        )

        with pytest.raises(DatabaseSessionError, match="alembic_version table missing"):
            await database.verify_schema_version()

    @pytest.mark.asyncio
    async def test_raises_when_version_row_absent(self, monkeypatch) -> None:
        self._stub_head(monkeypatch, "abc123")
        _patch_engine_with_query_result(monkeypatch, query_result=None)

        with pytest.raises(DatabaseSessionError, match="alembic_version row missing"):
            await database.verify_schema_version()

    @pytest.mark.asyncio
    async def test_raises_when_version_value_is_null(self, monkeypatch) -> None:
        self._stub_head(monkeypatch, "abc123")
        _patch_engine_with_query_result(monkeypatch, query_result=(None,))

        with pytest.raises(DatabaseSessionError, match="alembic_version row missing"):
            await database.verify_schema_version()


# =============================================================================
# SHU-761 tenant-isolation hooks
# =============================================================================


def _pg_conn_mock() -> MagicMock:
    """MagicMock shaped like a SQLAlchemy connection on PostgreSQL.

    ``_set_tenant_on_begin`` reads ``conn.dialect.name`` to decide whether
    to fire (SQLite test sessions hit the same hook and would error on
    ``set_config``). Stamping the dialect on the mock matches what the
    code reads on a real PG connection.
    """
    conn = MagicMock()
    conn.dialect.name = "postgresql"
    return conn


class TestSetTenantOnBeginHook:
    """Coverage for the engine ``begin`` listener that emits set_config."""

    def test_emits_set_config_with_context_value(self) -> None:
        """16.2: when tenant_context is set the hook runs the
        ``SELECT set_config('app.tenant_id', :tid, true)`` with the
        current context as the bind parameter."""
        conn = _pg_conn_mock()
        token = tenant_context.set("tenant-X")
        try:
            database._set_tenant_on_begin(conn)
        finally:
            tenant_context.reset(token)

        conn.execute.assert_called_once()
        stmt, params = conn.execute.call_args.args
        # Compare on the rendered SQL string rather than identity — we don't
        # care which exact text() instance the hook constructed.
        assert "set_config" in str(stmt)
        assert "app.tenant_id" in str(stmt)
        assert params == {"tid": "tenant-X"}

    def test_inert_when_context_unset(self, caplog: pytest.LogCaptureFixture) -> None:
        """16.3: with no context set, hook executes nothing and emits a
        DEBUG line so the missing-context site is greppable in dev logs."""
        conn = _pg_conn_mock()
        token = tenant_context.set(None)
        try:
            with caplog.at_level(logging.DEBUG, logger="shu.core.database"):
                database._set_tenant_on_begin(conn)
        finally:
            tenant_context.reset(token)

        conn.execute.assert_not_called()
        assert any(
            "transaction begun without tenant context" in r.getMessage() for r in caplog.records
        )

    def test_inert_when_context_empty_string(self, caplog: pytest.LogCaptureFixture) -> None:
        """SHU-825: a falsy ('') context must be treated exactly like None — no
        set_config. Writing '' would make ``current_setting('app.tenant_id', true)``
        return '' (not NULL), and the RLS policy's ``::uuid`` cast then 500s on ''
        instead of the harmless NULL -> 0-rows path."""
        conn = _pg_conn_mock()
        token = tenant_context.set("")
        try:
            with caplog.at_level(logging.DEBUG, logger="shu.core.database"):
                database._set_tenant_on_begin(conn)
        finally:
            tenant_context.reset(token)

        conn.execute.assert_not_called()
        assert any("transaction begun without tenant context" in r.getMessage() for r in caplog.records)

    def test_inert_on_non_postgres_dialect(self) -> None:
        """SQLite test fixtures must not trip the hook — set_config doesn't
        exist on SQLite and would raise OperationalError mid-test."""
        conn = MagicMock()
        conn.dialect.name = "sqlite"
        database._set_tenant_on_begin(conn)
        conn.execute.assert_not_called()

    def test_inert_on_admin_engine_connection(self) -> None:
        """The listener is class-level on ``Engine`` so it fires on every
        engine. For the admin engine (BYPASSRLS) the stamp is wasted work
        and contradicts the cross_tenant_query contract that says
        ``app.tenant_id`` is intentionally not set on the admin path. Hook
        must early-skip when the connection belongs to the admin engine.
        """
        # Stand up a stub admin engine and stamp its sync_engine onto the
        # mock connection so the early-skip path triggers.
        fake_admin_engine = MagicMock()
        fake_admin_sync_engine = MagicMock()
        fake_admin_engine.sync_engine = fake_admin_sync_engine

        conn = _pg_conn_mock()
        conn.engine = fake_admin_sync_engine

        token = tenant_context.set("tenant-X")
        try:
            with patch.object(database, "_admin_engine", fake_admin_engine):
                database._set_tenant_on_begin(conn)
        finally:
            tenant_context.reset(token)

        conn.execute.assert_not_called()


class TestRejectUnsafeSetGuard:
    """Coverage for ``_reject_unsafe_set`` — the debug-only guard against
    raw ``SET app.tenant_id`` (the legitimate path is set_config in the
    begin hook)."""

    # The guard reads ``get_settings().debug`` directly per call, so each
    # test patches the settings stub to control the on/off branch. No
    # module-global flip / teardown required.

    @staticmethod
    def _settings_with_debug(debug: bool):
        return SimpleNamespace(debug=debug)

    @pytest.mark.parametrize(
        "stmt",
        [
            "SET app.tenant_id = 'tenant-X'",
            "set app.tenant_id = 'tenant-X'",
            "SET SESSION app.tenant_id = 'tenant-X'",
            "SET LOCAL app.tenant_id = 'tenant-X'",
            "  SET   app.tenant_id = 'leading-whitespace'",
        ],
    )
    def test_raises_on_direct_set_against_app_tenant_id(self, stmt: str) -> None:
        with patch.object(database, "get_settings", return_value=self._settings_with_debug(True)):
            with pytest.raises(RuntimeError, match="Direct SET on app.tenant_id is forbidden"):
                database._reject_unsafe_set(MagicMock(), MagicMock(), stmt, {}, MagicMock(), False)

    @pytest.mark.parametrize(
        "stmt",
        [
            "SELECT 1",
            "SET other_setting = 'x'",
            # Tenant-id appearing inside set_config (the legitimate path) is
            # not caught by the regex — sanity that the guard isn't too eager.
            "SELECT set_config('app.tenant_id', 'x', true)",
        ],
    )
    def test_passes_on_unrelated_or_legitimate_statements(self, stmt: str) -> None:
        # No raise = pass. The mock conn / cursor are irrelevant to the
        # statement-only check.
        with patch.object(database, "get_settings", return_value=self._settings_with_debug(True)):
            database._reject_unsafe_set(MagicMock(), MagicMock(), stmt, {}, MagicMock(), False)

    def test_short_circuits_when_debug_disabled(self) -> None:
        """Production runs with debug=False — the guard must not pay the
        regex cost on every cursor exec."""
        with patch.object(database, "get_settings", return_value=self._settings_with_debug(False)):
            # Should not raise even though the statement matches.
            database._reject_unsafe_set(
                MagicMock(), MagicMock(), "SET app.tenant_id = 'x'", {}, MagicMock(), False
            )


class TestStampTenantIdListener:
    """Coverage for the ``before_flush`` listener that auto-stamps tenant_id
    on new tenant-scoped objects."""

    @staticmethod
    def _tenant_scoped_obj(tenant_id: str | None = None) -> object:
        """Concrete instance with ``__table__.columns`` containing tenant_id.

        Built ad-hoc rather than via MagicMock because the listener reads
        ``type(obj).__table__.columns`` — a class-level attribute MagicMock
        doesn't synthesize on demand. ``__table__`` is a SimpleNamespace
        rather than a real ``Table`` because the listener only does
        ``"tenant_id" in table.columns``.
        """

        class _TenantScopedFake:
            __table__ = SimpleNamespace(columns={"tenant_id": object(), "id": object()})

            def __init__(self, tid: str | None) -> None:
                self.tenant_id = tid

        return _TenantScopedFake(tenant_id)

    @staticmethod
    def _global_obj() -> object:
        class _GlobalFake:
            __table__ = SimpleNamespace(columns={"id": object()})

        return _GlobalFake()

    def test_stamps_tenant_id_from_context_when_missing(self) -> None:
        obj = self._tenant_scoped_obj(tenant_id=None)
        session = MagicMock()
        session.new = [obj]

        token = tenant_context.set("tenant-X")
        try:
            database._stamp_tenant_id(session, MagicMock(), [])
        finally:
            tenant_context.reset(token)

        assert obj.tenant_id == "tenant-X"

    def test_raises_when_context_and_field_both_missing(self) -> None:
        """The whole point of this listener: never silently insert a
        tenant-scoped row without a tenant_id."""
        obj = self._tenant_scoped_obj(tenant_id=None)
        session = MagicMock()
        session.new = [obj]

        token = tenant_context.set(None)
        try:
            with pytest.raises(MissingTenantContextError):
                database._stamp_tenant_id(session, MagicMock(), [])
        finally:
            tenant_context.reset(token)

    def test_raises_on_mismatch_between_explicit_and_context(self) -> None:
        """An explicit ``tenant_id`` that disagrees with context is almost
        always a bug — fail loud at construction site rather than letting
        RLS WITH CHECK silently reject downstream."""
        obj = self._tenant_scoped_obj(tenant_id="tenant-A")
        session = MagicMock()
        session.new = [obj]

        token = tenant_context.set("tenant-B")
        try:
            with pytest.raises(CrossTenantInsertError):
                database._stamp_tenant_id(session, MagicMock(), [])
        finally:
            tenant_context.reset(token)

    def test_skips_global_tables(self) -> None:
        """Tables without a ``tenant_id`` column (llm_providers, tenants
        itself, etc.) must flow through untouched."""
        obj = self._global_obj()
        session = MagicMock()
        session.new = [obj]

        token = tenant_context.set("tenant-X")
        try:
            database._stamp_tenant_id(session, MagicMock(), [])
        finally:
            tenant_context.reset(token)

        # The listener must not synthesize tenant_id on a global table — it
        # doesn't belong on the row and the FK would reject the insert.
        assert not hasattr(obj, "tenant_id")

    # ------------------------------------------------------------------
    # Update-path coverage (session.dirty)
    #
    # Loading a row under tenant X then mutating its tenant_id to Y is
    # caught at the Python layer (not just at the DB by RLS WITH CHECK)
    # so the stack trace lands at the mutation site instead of a generic
    # row-rejected error at flush.
    # ------------------------------------------------------------------

    def test_dirty_raises_on_tenant_id_mutated_to_other_tenant(self) -> None:
        """An UPDATE that flips a row's tenant_id to a value other than the
        session's context is a cross-tenant write — same shape as the
        insert mismatch case."""
        obj = self._tenant_scoped_obj(tenant_id="tenant-A")
        session = MagicMock()
        session.new = []
        session.dirty = [obj]

        token = tenant_context.set("tenant-B")
        try:
            with pytest.raises(CrossTenantInsertError, match="update path"):
                database._stamp_tenant_id(session, MagicMock(), [])
        finally:
            tenant_context.reset(token)

    def test_dirty_passes_when_tenant_id_still_matches_context(self) -> None:
        """The legitimate UPDATE case: a row loaded under tenant X, mutated
        on unrelated columns, flushed back under tenant X. tenant_id is
        still X — listener must let it through."""
        obj = self._tenant_scoped_obj(tenant_id="tenant-X")
        session = MagicMock()
        session.new = []
        session.dirty = [obj]

        token = tenant_context.set("tenant-X")
        try:
            database._stamp_tenant_id(session, MagicMock(), [])
        finally:
            tenant_context.reset(token)
        # No raise = pass. Sanity that tenant_id wasn't mutated either.
        assert obj.tenant_id == "tenant-X"

    def test_dirty_skips_global_tables(self) -> None:
        """Same exclusion as the insert path — global tables flow through."""
        obj = self._global_obj()
        session = MagicMock()
        session.new = []
        session.dirty = [obj]

        token = tenant_context.set("tenant-X")
        try:
            database._stamp_tenant_id(session, MagicMock(), [])
        finally:
            tenant_context.reset(token)
        assert not hasattr(obj, "tenant_id")
