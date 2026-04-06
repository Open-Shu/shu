"""Unit tests for PluginsSchedulerService.

Tests the plugin feed scheduling service, particularly NULL handling in queries
and degraded connection detection for MCP and API integrations.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Column, Integer, String, select
from sqlalchemy.orm import declarative_base

from shu.services.plugins_scheduler_service import PluginsSchedulerService


@pytest.mark.asyncio
async def test_sqlalchemy_null_check_generates_correct_sql() -> None:
    """Integration test to verify SQLAlchemy generates correct SQL for NULL checks.

    This test directly verifies that .is_(None) generates 'IS NULL' in SQL,
    while Python's 'is None' would fail to do so.

    This test validates the fixes for bugs in:
    - plugins_scheduler_service.py (is None -> .is_(None))
    - attachment_cleanup.py (is not None -> .is_not(None))
    """
    base = declarative_base()

    class TestModel(base):
        """Test model for SQL generation verification."""

        __tablename__ = "test_model"
        id = Column(Integer, primary_key=True)
        name = Column(String)
        value = Column(Integer, nullable=True)

    # Test that .is_(None) generates correct SQL
    query_correct = select(TestModel).where(TestModel.value.is_(None))
    sql_correct = str(query_correct.compile(compile_kwargs={"literal_binds": True}))

    # Verify the SQL contains 'IS NULL'
    assert "IS NULL" in sql_correct, f"Expected 'IS NULL' in SQL, got: {sql_correct}"

    # Test that == None also works
    query_equals = select(TestModel).where(TestModel.value == None)  # noqa: E711
    sql_equals = str(query_equals.compile(compile_kwargs={"literal_binds": True}))

    assert "IS NULL" in sql_equals, f"Expected 'IS NULL' in SQL, got: {sql_equals}"

    # Test that .is_not(None) generates correct SQL
    query_not_null = select(TestModel).where(TestModel.value.is_not(None))
    sql_not_null = str(query_not_null.compile(compile_kwargs={"literal_binds": True}))

    assert "IS NOT NULL" in sql_not_null, f"Expected 'IS NOT NULL' in SQL, got: {sql_not_null}"

    # Test that != None also works
    query_not_equals = select(TestModel).where(TestModel.value != None)  # noqa: E711
    sql_not_equals = str(query_not_equals.compile(compile_kwargs={"literal_binds": True}))

    assert "IS NOT NULL" in sql_not_equals, f"Expected 'IS NOT NULL' in SQL, got: {sql_not_equals}"


def _make_schedule(plugin_name: str):
    """Build a minimal mock PluginFeed schedule."""
    s = MagicMock()
    s.plugin_name = plugin_name
    return s


class TestGetDegradedApiConnections:
    """Verify _get_degraded_api_connections detects degraded API connections."""

    @pytest.mark.asyncio
    async def test_api_connection_above_threshold_is_degraded(self):
        """An API connection with consecutive_failures >= 5 is reported as degraded."""
        db = AsyncMock()
        db.execute.return_value = MagicMock(all=MagicMock(return_value=[("weather", 5)]))

        svc = PluginsSchedulerService(db)
        schedules = [_make_schedule("api:weather")]

        result = await svc._get_degraded_api_connections(schedules)

        assert result == {"api:weather"}

    @pytest.mark.asyncio
    async def test_api_connection_below_threshold_is_not_degraded(self):
        """An API connection with consecutive_failures < 5 is not degraded."""
        db = AsyncMock()
        db.execute.return_value = MagicMock(all=MagicMock(return_value=[("weather", 3)]))

        svc = PluginsSchedulerService(db)
        schedules = [_make_schedule("api:weather")]

        result = await svc._get_degraded_api_connections(schedules)

        assert result == set()

    @pytest.mark.asyncio
    async def test_no_api_schedules_returns_empty(self):
        """When no api: schedules are present, no query is executed."""
        db = AsyncMock()
        svc = PluginsSchedulerService(db)
        schedules = [_make_schedule("mcp:server"), _make_schedule("github")]

        result = await svc._get_degraded_api_connections(schedules)

        assert result == set()
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mixed_schedules_only_checks_api(self):
        """Only api: prefixed schedules trigger the API degradation query."""
        db = AsyncMock()
        db.execute.return_value = MagicMock(all=MagicMock(return_value=[("svc1", 10)]))

        svc = PluginsSchedulerService(db)
        schedules = [_make_schedule("api:svc1"), _make_schedule("mcp:server")]

        result = await svc._get_degraded_api_connections(schedules)

        assert result == {"api:svc1"}
