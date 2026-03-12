"""
PBAC tests for plugin execution.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.services.plugin_execution import get_allowed_plugin_names


def _mock_db_with_enabled(enabled_names: set[str]) -> AsyncMock:
    """Return a mock db whose execute returns PluginDefinition rows for enabled_names."""
    rows = [MagicMock(name=n) for n in enabled_names]
    # MagicMock(name=...) sets the MagicMock's own name, not the `.name` attr
    for row, n in zip(rows, enabled_names):
        row.name = n
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


class TestGetAllowedPluginNames:
    """Tests for get_allowed_plugin_names."""

    @pytest.mark.asyncio
    async def test_admin_sees_all_enabled_plugins(self):
        """Admin user bypasses PBAC and sees all enabled plugins in the manifest."""
        db = _mock_db_with_enabled({"gmail", "github"})
        mock_cache = MagicMock()
        mock_cache.is_admin = AsyncMock(return_value=True)
        mock_cache.get_denied_resources = AsyncMock(return_value=set())

        with patch("shu.services.plugin_execution.POLICY_CACHE", mock_cache):
            result = await get_allowed_plugin_names("admin-1", {"gmail", "github", "slack"}, db)

        assert result == {"gmail", "github"}

    @pytest.mark.asyncio
    async def test_user_sees_only_allowed_plugins(self):
        """Non-admin user sees only plugins allowed by PBAC."""
        db = _mock_db_with_enabled({"gmail", "github"})
        mock_cache = MagicMock()
        mock_cache.get_denied_resources = AsyncMock(return_value={"github"})

        with patch("shu.services.plugin_execution.POLICY_CACHE", mock_cache):
            result = await get_allowed_plugin_names("user-1", {"gmail", "github"}, db)

        assert result == {"gmail"}

    @pytest.mark.asyncio
    async def test_manifest_filters_to_enabled_only(self):
        """Plugins not enabled in DB are excluded even if in manifest."""
        db = _mock_db_with_enabled({"gmail"})
        mock_cache = MagicMock()
        mock_cache.get_denied_resources = AsyncMock(return_value=set())

        with patch("shu.services.plugin_execution.POLICY_CACHE", mock_cache):
            result = await get_allowed_plugin_names("user-1", {"gmail", "slack"}, db)

        assert result == {"gmail"}

    @pytest.mark.asyncio
    async def test_empty_manifest_returns_empty(self):
        """Empty manifest yields no plugins regardless of DB state."""
        db = _mock_db_with_enabled({"gmail"})
        mock_cache = MagicMock()
        mock_cache.get_denied_resources = AsyncMock(return_value=set())

        with patch("shu.services.plugin_execution.POLICY_CACHE", mock_cache):
            result = await get_allowed_plugin_names("user-1", set(), db)

        assert result == set()

    @pytest.mark.asyncio
    async def test_all_denied_returns_empty(self):
        """When PBAC denies all candidates, the result is empty."""
        db = _mock_db_with_enabled({"gmail", "github"})
        mock_cache = MagicMock()
        mock_cache.get_denied_resources = AsyncMock(return_value={"gmail", "github"})

        with patch("shu.services.plugin_execution.POLICY_CACHE", mock_cache):
            result = await get_allowed_plugin_names("user-1", {"gmail", "github"}, db)

        assert result == set()


class TestExecutorPbac:
    """Plugin executor PBAC enforcement."""

    @pytest.mark.asyncio
    async def test_executor_denies_when_pbac_fails(self):
        """Executor raises 404 when POLICY_CACHE.check returns False."""
        from fastapi import HTTPException

        from shu.plugins.executor import Executor

        mock_cache = MagicMock()
        mock_cache.check = AsyncMock(return_value=False)

        executor = Executor(settings=MagicMock(enable_api_rate_limiting=False))
        plugin = MagicMock()
        plugin.name = "gmail"
        db = AsyncMock()

        with patch("shu.plugins.executor.POLICY_CACHE", mock_cache), \
             pytest.raises(HTTPException) as exc_info:
            await executor.execute(
                plugin=plugin,
                user_id="user-1",
                user_email="user@test.com",
                agent_key=None,
                params={},
                db_session=db,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_executor_allows_when_pbac_passes(self):
        """Executor proceeds to execution when POLICY_CACHE.check returns True."""
        from shu.plugins.executor import Executor

        mock_cache = MagicMock()
        mock_cache.check = AsyncMock(return_value=True)

        settings = MagicMock(
            enable_api_rate_limiting=False,
            plugin_quota_daily_requests_default=0,
            plugin_quota_monthly_requests_default=0,
        )
        executor = Executor(settings=settings)
        plugin = MagicMock()
        plugin.name = "gmail"
        plugin.get_schema.return_value = None
        plugin.get_output_schema.return_value = None
        mock_result = MagicMock()
        mock_result.data = {"ok": True}
        plugin.execute = AsyncMock(return_value=mock_result)
        db = AsyncMock()

        with patch("shu.plugins.executor.POLICY_CACHE", mock_cache):
            result = await executor.execute(
                plugin=plugin,
                user_id="user-1",
                user_email="user@test.com",
                agent_key=None,
                params={},
                db_session=db,
            )

        mock_cache.check.assert_called_once_with("user-1", "plugin.execute", "plugin:gmail", db)
        assert result is not None
