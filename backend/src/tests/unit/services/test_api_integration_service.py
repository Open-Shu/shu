"""Unit tests for ApiIntegrationService.

Covers create (YAML parsing, validation, auth credential storage),
sync (tool merging with ingest_defaults, stale marking, health tracking),
delete (feed-guard, secret purge), update_tool_config, and
PluginRecord generation.

All tests mock the AsyncSession, PBAC enforcement, plugin secrets,
and the OpenAPI parser to isolate service logic.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.exceptions import ConflictError, NotFoundError, ValidationError
from shu.models.api_server_connection import ApiServerConnection
from shu.schemas.api_integration_admin import ApiSyncResult
from shu.schemas.integration_common import ToolConfigUpdate
from shu.services.api_integration_service import ApiIntegrationService, DEGRADED_THRESHOLD


VALID_YAML = """\
api_integration_version: 1
name: my-api
description: Test API
openapi_definition: https://example.com/openapi.json
"""

VALID_YAML_WITH_INGEST = """\
api_integration_version: 1
name: my-api
description: Test API
openapi_definition: https://example.com/openapi.json
ingest_defaults:
  list-items:
    field_mapping:
      title: name
      content: body
      source_id: id
"""


def _make_connection(**overrides) -> MagicMock:
    """Build a mock ApiServerConnection with sensible defaults."""
    defaults = {
        "id": "conn-1",
        "name": "test-api",
        "url": "https://example.com/openapi.json",
        "spec_type": "openapi",
        "import_source": None,
        "tool_configs": None,
        "discovered_tools": None,
        "timeouts": None,
        "response_size_limit_bytes": None,
        "enabled": True,
        "last_synced_at": None,
        "last_error": None,
        "consecutive_failures": 0,
        "auth_config": None,
        "base_url": None,
    }
    defaults.update(overrides)
    conn = MagicMock(spec=ApiServerConnection)
    for k, v in defaults.items():
        setattr(conn, k, v)
    return conn


def _mock_db() -> AsyncMock:
    """Create a mock AsyncSession with common async methods."""
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    db.execute = AsyncMock()
    return db


def _patch_pbac():
    """Patch enforce_pbac and POLICY_CACHE to permit everything."""
    return (
        patch("shu.services.api_integration_service.enforce_pbac", new_callable=AsyncMock),
        patch(
            "shu.services.api_integration_service.POLICY_CACHE",
            **{"get_denied_resources": AsyncMock(return_value=set())},
        ),
    )


def _patch_secrets():
    """Patch plugin_secrets functions used by ApiIntegrationService."""
    return (
        patch("shu.services.api_integration_service.set_secret", new_callable=AsyncMock),
        patch("shu.services.api_integration_service.get_secret", new_callable=AsyncMock),
        patch("shu.services.api_integration_service.delete_secret", new_callable=AsyncMock),
    )


def _patch_registry():
    """Patch the REGISTRY import inside _invalidate_registry."""
    mock_registry = MagicMock()
    mock_registry._cache = {}
    mock_registry.sync = AsyncMock()
    return patch("shu.services.api_integration_service.REGISTRY", mock_registry, create=True)


def _scalar_one_or_none(value):
    """Build an execute result whose .scalar_one_or_none() returns value."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


class TestCreateConnection:
    """Verify YAML parsing, validation, and auth credential storage on create."""

    @pytest.mark.asyncio
    async def test_valid_yaml_creates_connection(self):
        """Valid YAML content creates a connection and commits."""
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(None)

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch, get_patch, del_patch:
            service = ApiIntegrationService(db)
            result = await service.create_connection(VALID_YAML, auth_credential=None, user_id="admin")

        db.add.assert_called_once()
        db.commit.assert_awaited()
        db.refresh.assert_awaited()

    @pytest.mark.asyncio
    async def test_invalid_yaml_raises_validation_error(self):
        """Malformed YAML raises ValidationError."""
        db = _mock_db()

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch, get_patch, del_patch:
            service = ApiIntegrationService(db)
            with pytest.raises(ValidationError, match="Invalid YAML"):
                await service.create_connection("{{bad: yaml: :", auth_credential=None, user_id="admin")

    @pytest.mark.asyncio
    async def test_non_mapping_yaml_raises_validation_error(self):
        """YAML that parses to a non-dict raises ValidationError."""
        db = _mock_db()

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch, get_patch, del_patch:
            service = ApiIntegrationService(db)
            with pytest.raises(ValidationError, match="must be a mapping"):
                await service.create_connection("- just\n- a\n- list\n", auth_credential=None, user_id="admin")

    @pytest.mark.asyncio
    async def test_auth_credential_stored_as_secret(self):
        """When auth_credential is provided, it is stored via set_secret."""
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(None)

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch as mock_set, get_patch, del_patch:
            service = ApiIntegrationService(db)
            await service.create_connection(VALID_YAML, auth_credential="Bearer tok123", user_id="admin")

        mock_set.assert_awaited_once_with(
            "api:my-api", "auth_credential", value="Bearer tok123", user_id="admin", scope="system"
        )

    @pytest.mark.asyncio
    async def test_no_auth_credential_skips_secret(self):
        """No auth credential means no call to set_secret."""
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(None)

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch as mock_set, get_patch, del_patch:
            service = ApiIntegrationService(db)
            await service.create_connection(VALID_YAML, auth_credential=None, user_id="admin")

        mock_set.assert_not_awaited()


class TestSyncConnection:
    """Verify tool discovery, merging, ingest_defaults application, and stale marking."""

    @pytest.mark.asyncio
    async def test_first_sync_applies_ingest_defaults(self):
        """First sync on a connection with ingest_defaults applies them to matching tools."""
        import yaml as _yaml

        parsed_source = _yaml.safe_load(VALID_YAML_WITH_INGEST)
        conn = _make_connection(
            import_source=parsed_source,
            tool_configs=None,
            last_synced_at=None,
        )
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        parse_result = MagicMock()
        parse_result.discovered_tools = [
            {"name": "list-items", "description": "List items"},
            {"name": "get-item", "description": "Get item"},
        ]
        parse_result.errors = []
        parse_result.base_url = "https://api.example.com"

        pbac_patch, cache_patch = _patch_pbac()
        with (
            pbac_patch,
            cache_patch,
            patch("shu.services.api_integration_service.fetch_and_parse", new_callable=AsyncMock, return_value=parse_result),
            _patch_registry(),
        ):
            service = ApiIntegrationService(db)
            result = await service.sync_connection("conn-1", user_id="admin")

        assert "list-items" in result.tools
        assert "get-item" in result.tools

        configs = conn.tool_configs
        assert configs["list-items"]["feed_eligible"] is True
        assert "ingest" in configs["list-items"]
        assert configs["list-items"]["ingest"]["field_mapping"]["title"] == "name"

        assert configs["get-item"]["chat_callable"] is False
        assert configs["get-item"]["feed_eligible"] is False

    @pytest.mark.asyncio
    async def test_resync_preserves_existing_tool_configs(self):
        """Re-sync preserves admin-configured tool_configs for existing tools."""
        existing_configs = {
            "existing-op": {
                "chat_callable": False,
                "feed_eligible": True,
                "enabled": True,
                "ingest": {"field_mapping": {"title": "t", "content": "c", "source_id": "id"}},
            },
        }
        conn = _make_connection(
            tool_configs=existing_configs,
            last_synced_at="2025-01-01T00:00:00Z",
        )
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        parse_result = MagicMock()
        parse_result.discovered_tools = [
            {"name": "existing-op", "description": "Old op"},
            {"name": "new-op", "description": "New op"},
        ]
        parse_result.errors = []
        parse_result.base_url = None

        pbac_patch, cache_patch = _patch_pbac()
        with (
            pbac_patch,
            cache_patch,
            patch("shu.services.api_integration_service.fetch_and_parse", new_callable=AsyncMock, return_value=parse_result),
            _patch_registry(),
        ):
            service = ApiIntegrationService(db)
            result = await service.sync_connection("conn-1", user_id="admin")

        assert conn.tool_configs["existing-op"] == existing_configs["existing-op"]
        assert "new-op" in result.added
        assert conn.tool_configs["new-op"] == {"chat_callable": False, "feed_eligible": False, "enabled": True}

    @pytest.mark.asyncio
    async def test_stale_tools_marked_not_removed(self):
        """Tools present before but absent from sync get stale=true instead of being removed."""
        existing_configs = {
            "keep-tool": {"chat_callable": True, "feed_eligible": False, "enabled": True},
            "gone-tool": {"chat_callable": True, "feed_eligible": False, "enabled": True},
        }
        conn = _make_connection(
            tool_configs=existing_configs,
            last_synced_at="2025-01-01T00:00:00Z",
        )
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        parse_result = MagicMock()
        parse_result.discovered_tools = [{"name": "keep-tool", "description": "Still here"}]
        parse_result.errors = []
        parse_result.base_url = None

        pbac_patch, cache_patch = _patch_pbac()
        with (
            pbac_patch,
            cache_patch,
            patch("shu.services.api_integration_service.fetch_and_parse", new_callable=AsyncMock, return_value=parse_result),
            _patch_registry(),
        ):
            service = ApiIntegrationService(db)
            result = await service.sync_connection("conn-1", user_id="admin")

        assert "gone-tool" in result.stale
        assert "gone-tool" in conn.tool_configs
        assert conn.tool_configs["gone-tool"]["stale"] is True
        assert "stale" not in conn.tool_configs["keep-tool"]


class TestMergeDiscoveredTools:
    """Verify _merge_discovered_tools logic in isolation."""

    def test_new_tools_get_defaults(self):
        """Newly discovered tools get the default chat_callable config."""
        conn = _make_connection(tool_configs=None, last_synced_at=None, import_source={})
        service = ApiIntegrationService(AsyncMock())

        result = service._merge_discovered_tools(conn, [{"name": "alpha"}, {"name": "beta"}])

        assert conn.tool_configs["alpha"] == {"chat_callable": False, "feed_eligible": False, "enabled": True}
        assert conn.tool_configs["beta"] == {"chat_callable": False, "feed_eligible": False, "enabled": True}
        assert sorted(result.added) == ["alpha", "beta"]

    def test_existing_tools_preserved(self):
        """Existing tool configs are not overwritten by re-discovery."""
        existing = {"tool-a": {"chat_callable": False, "feed_eligible": True, "enabled": True}}
        conn = _make_connection(tool_configs=existing, last_synced_at="2025-01-01T00:00:00Z")
        service = ApiIntegrationService(AsyncMock())

        result = service._merge_discovered_tools(conn, [{"name": "tool-a"}])

        assert conn.tool_configs["tool-a"]["chat_callable"] is False
        assert conn.tool_configs["tool-a"]["feed_eligible"] is True
        assert "tool-a" not in result.added

    def test_removed_tools_marked_stale(self):
        """Tools absent from discovery get stale=true (not removed)."""
        existing = {
            "present": {"chat_callable": True, "feed_eligible": False, "enabled": True},
            "absent": {"chat_callable": True, "feed_eligible": False, "enabled": True},
        }
        conn = _make_connection(tool_configs=existing, last_synced_at="2025-01-01T00:00:00Z")
        service = ApiIntegrationService(AsyncMock())

        result = service._merge_discovered_tools(conn, [{"name": "present"}])

        assert conn.tool_configs["absent"]["stale"] is True
        assert "absent" in result.stale
        assert "stale" not in conn.tool_configs["present"]

    def test_stale_flag_cleared_on_rediscovery(self):
        """A previously stale tool that reappears has the stale flag removed."""
        existing = {
            "tool-x": {"chat_callable": True, "feed_eligible": False, "enabled": True, "stale": True},
        }
        conn = _make_connection(tool_configs=existing, last_synced_at="2025-01-01T00:00:00Z")
        service = ApiIntegrationService(AsyncMock())

        result = service._merge_discovered_tools(conn, [{"name": "tool-x"}])

        assert "stale" not in conn.tool_configs["tool-x"]
        assert "tool-x" not in result.stale


class TestDeleteConnection:
    """Verify deletion is blocked by active feeds (409) and succeeds otherwise."""

    @pytest.mark.asyncio
    async def test_blocked_when_feeds_exist(self):
        """Active feeds referencing the plugin name prevent deletion with ConflictError."""
        conn = _make_connection(name="my-api")
        db = _mock_db()

        get_conn_result = _scalar_one_or_none(conn)
        feed_result = MagicMock()
        feed_result.all.return_value = [("feed-1",), ("feed-2",)]
        db.execute = AsyncMock(side_effect=[get_conn_result, feed_result])

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch, get_patch, del_patch:
            service = ApiIntegrationService(db)
            with pytest.raises(ConflictError) as exc_info:
                await service.delete_connection("conn-1", user_id="admin")

        assert "feed-1" in str(exc_info.value.details)
        assert "feed-2" in str(exc_info.value.details)
        db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_succeeds_when_no_feeds(self):
        """No active feeds allows the connection and its secrets to be deleted."""
        conn = _make_connection()
        db = _mock_db()

        get_conn_result = _scalar_one_or_none(conn)
        feed_result = MagicMock()
        feed_result.all.return_value = []
        defn_scalars = MagicMock()
        defn_scalars.first.return_value = None
        defn_result = MagicMock()
        defn_result.scalars.return_value = defn_scalars
        expected = [get_conn_result, feed_result, defn_result]
        call_count = 0

        async def _execute_side_effect(*args, **kwargs):
            nonlocal call_count
            idx = call_count
            call_count += 1
            return expected[idx] if idx < len(expected) else _scalar_one_or_none(None)

        db.execute = AsyncMock(side_effect=_execute_side_effect)

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch, get_patch, del_patch as mock_del, _patch_registry():
            service = ApiIntegrationService(db)
            await service.delete_connection("conn-1", user_id="admin")

        db.delete.assert_any_await(conn)
        db.commit.assert_awaited()
        mock_del.assert_awaited_once_with("api:test-api", "auth_credential", user_id=None, scope="system")

    @pytest.mark.asyncio
    async def test_plugin_definition_deleted_when_exists(self):
        """If a PluginDefinition exists for the connection, it is also deleted."""
        conn = _make_connection()
        mock_defn = MagicMock()
        db = _mock_db()

        get_conn_result = _scalar_one_or_none(conn)
        feed_result = MagicMock()
        feed_result.all.return_value = []
        defn_scalars = MagicMock()
        defn_scalars.first.return_value = mock_defn
        defn_result = MagicMock()
        defn_result.scalars.return_value = defn_scalars
        expected = [get_conn_result, feed_result, defn_result]
        call_count = 0

        async def _execute_side_effect(*args, **kwargs):
            nonlocal call_count
            idx = call_count
            call_count += 1
            return expected[idx] if idx < len(expected) else _scalar_one_or_none(None)

        db.execute = AsyncMock(side_effect=_execute_side_effect)

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch, get_patch, del_patch, _patch_registry():
            service = ApiIntegrationService(db)
            await service.delete_connection("conn-1", user_id="admin")

        db.delete.assert_any_await(mock_defn)
        db.delete.assert_any_await(conn)


class TestUpdateToolConfig:
    """Verify per-tool config updates, bootstrap from discovered, and unknown tool rejection."""

    @pytest.mark.asyncio
    async def test_updates_existing_tool_config(self):
        """Updating a tool already in tool_configs replaces its entry."""
        conn = _make_connection(
            tool_configs={"search": {"chat_callable": True, "feed_eligible": False, "enabled": True}},
            discovered_tools=[{"name": "search", "description": "Search"}],
        )
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        pbac_patch, cache_patch = _patch_pbac()
        with pbac_patch, cache_patch, _patch_registry():
            service = ApiIntegrationService(db)
            data = ToolConfigUpdate(chat_callable=False, feed_eligible=False, enabled=True)
            result = await service.update_tool_config("conn-1", "search", data, "admin")

        assert result.tool_configs["search"]["chat_callable"] is False
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_bootstraps_config_for_discovered_but_unconfigured_tool(self):
        """A tool in discovered_tools but not in tool_configs gets a default config first."""
        conn = _make_connection(
            tool_configs={"other": {"chat_callable": True, "feed_eligible": False, "enabled": True}},
            discovered_tools=[
                {"name": "other", "description": "Other"},
                {"name": "new-tool", "description": "New"},
            ],
        )
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        pbac_patch, cache_patch = _patch_pbac()
        with pbac_patch, cache_patch, _patch_registry():
            service = ApiIntegrationService(db)
            data = ToolConfigUpdate(chat_callable=True, feed_eligible=False, enabled=False)
            result = await service.update_tool_config("conn-1", "new-tool", data, "admin")

        assert "new-tool" in result.tool_configs
        assert result.tool_configs["new-tool"]["enabled"] is False
        assert result.tool_configs["other"] == {"chat_callable": True, "feed_eligible": False, "enabled": True}

    @pytest.mark.asyncio
    async def test_raises_not_found_for_unknown_tool(self):
        """A tool not in tool_configs or discovered_tools raises NotFoundError."""
        conn = _make_connection(
            tool_configs={"search": {"chat_callable": True, "feed_eligible": False, "enabled": True}},
            discovered_tools=[{"name": "search", "description": "Search"}],
        )
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        pbac_patch, cache_patch = _patch_pbac()
        with pbac_patch, cache_patch:
            service = ApiIntegrationService(db)
            data = ToolConfigUpdate(chat_callable=True, enabled=True)
            with pytest.raises(NotFoundError, match="nonexistent"):
                await service.update_tool_config("conn-1", "nonexistent", data, "admin")


class TestGeneratePluginRecord:
    """Verify PluginRecord generation from tool_configs."""

    def test_correct_api_prefix(self):
        """Plugin name uses api: prefix."""
        conn = _make_connection(
            name="my-api",
            tool_configs={"search": {"chat_callable": True, "feed_eligible": False, "enabled": True}},
        )
        service = ApiIntegrationService(AsyncMock())
        record = service.generate_plugin_record(conn)

        assert record.name == "api:my-api"

    def test_chat_and_feed_ops_populated_correctly(self):
        """Enabled chat_callable and feed_eligible tools are split into the correct op lists."""
        conn = _make_connection(
            name="my-api",
            tool_configs={
                "search": {"chat_callable": True, "feed_eligible": False, "enabled": True},
                "fetch-docs": {"chat_callable": False, "feed_eligible": True, "enabled": True},
                "disabled-tool": {"chat_callable": True, "feed_eligible": False, "enabled": False},
                "ingest-news": {"chat_callable": False, "feed_eligible": True, "enabled": True},
            },
        )
        service = ApiIntegrationService(AsyncMock())
        record = service.generate_plugin_record(conn)

        assert record.chat_callable_ops == ["search"]
        assert sorted(record.allowed_feed_ops) == ["fetch-docs", "ingest-news"]
        assert record.default_feed_op == "fetch-docs"

    def test_stale_tools_excluded(self):
        """Stale tools are excluded from ops lists."""
        conn = _make_connection(
            name="my-api",
            tool_configs={
                "active": {"chat_callable": True, "feed_eligible": False, "enabled": True},
                "stale-tool": {"chat_callable": True, "feed_eligible": False, "enabled": True, "stale": True},
            },
        )
        service = ApiIntegrationService(AsyncMock())
        record = service.generate_plugin_record(conn)

        assert record.chat_callable_ops == ["active"]

    def test_no_enabled_tools_returns_none_ops(self):
        """All tools disabled produces None for all op lists."""
        conn = _make_connection(
            name="empty",
            tool_configs={"only": {"chat_callable": True, "feed_eligible": False, "enabled": False}},
        )
        service = ApiIntegrationService(AsyncMock())
        record = service.generate_plugin_record(conn)

        assert record.chat_callable_ops is None
        assert record.allowed_feed_ops is None
        assert record.default_feed_op is None

    def test_no_tool_configs_returns_none_ops(self):
        """Null tool_configs produces None for all op lists."""
        conn = _make_connection(name="blank", tool_configs=None)
        service = ApiIntegrationService(AsyncMock())
        record = service.generate_plugin_record(conn)

        assert record.chat_callable_ops is None
        assert record.allowed_feed_ops is None

    def test_display_name_format(self):
        """Display name uses 'Name (API)' format."""
        conn = _make_connection(name="weather", tool_configs={})
        service = ApiIntegrationService(AsyncMock())
        record = service.generate_plugin_record(conn)

        assert record.display_name == "weather (API)"


class TestHealthTracking:
    """Verify _record_success and _record_failure health tracking."""

    def test_record_success_resets_failures(self):
        """Successful sync resets consecutive_failures and clears last_error."""
        conn = _make_connection(consecutive_failures=3, last_error="previous error")
        service = ApiIntegrationService(AsyncMock())

        service._record_success(conn)

        assert conn.consecutive_failures == 0
        assert conn.last_error is None

    def test_record_failure_increments_counter(self):
        """Failed sync increments consecutive_failures and records last_error."""
        conn = _make_connection(consecutive_failures=2, last_error=None)
        service = ApiIntegrationService(AsyncMock())

        service._record_failure(conn, "connection refused")

        assert conn.consecutive_failures == 3
        assert conn.last_error == "connection refused"

    def test_record_failure_truncates_long_error(self):
        """Error messages longer than 500 chars are truncated."""
        conn = _make_connection(consecutive_failures=0)
        service = ApiIntegrationService(AsyncMock())

        long_error = "x" * 1000
        service._record_failure(conn, long_error)

        assert len(conn.last_error) == 500

    def test_degraded_threshold_logging(self):
        """Crossing DEGRADED_THRESHOLD logs a degraded message."""
        conn = _make_connection(
            consecutive_failures=DEGRADED_THRESHOLD - 1,
            last_error=None,
        )
        service = ApiIntegrationService(AsyncMock())

        with patch("shu.services.api_integration_service.logger") as mock_logger:
            service._record_failure(conn, "timeout")

        assert conn.consecutive_failures == DEGRADED_THRESHOLD
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0]
        assert "degraded" in call_args[0]

    def test_below_threshold_no_degraded_log(self):
        """Below DEGRADED_THRESHOLD, no degraded log is emitted."""
        conn = _make_connection(consecutive_failures=1, last_error=None)
        service = ApiIntegrationService(AsyncMock())

        with patch("shu.services.api_integration_service.logger") as mock_logger:
            service._record_failure(conn, "timeout")

        mock_logger.info.assert_not_called()

    def test_above_threshold_no_repeat_degraded_log(self):
        """Once already past threshold, further failures don't re-log degraded."""
        conn = _make_connection(consecutive_failures=DEGRADED_THRESHOLD + 1, last_error=None)
        service = ApiIntegrationService(AsyncMock())

        with patch("shu.services.api_integration_service.logger") as mock_logger:
            service._record_failure(conn, "timeout")

        mock_logger.info.assert_not_called()
