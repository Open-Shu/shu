"""
Unit tests for plugin execution service.
"""

from unittest.mock import MagicMock

from shu.services.plugin_execution import _coerce_params, _sanitize_plugin_name, _unsanitize_plugin_name


class TestParamCoercion:
    def test_coerce_params(self):
        schema = {
            "properties": {
                "limit": {"type": "integer"},
                "threshold": {"type": "number"},
                "verbose": {"type": "boolean"},
                "name": {"type": "string"},
            }
        }
        mock_plugin = MagicMock()
        mock_plugin.name = "test-plugin"
        mock_plugin.get_schema_for_op.return_value = schema

        params = {
            "limit": "48",
            "threshold": "0.5",
            "verbose": "true",
            "name": "test",
            "other": "ignore",
        }

        result = _coerce_params(mock_plugin, params, "some_op")

        assert result["limit"] == 48
        assert result["threshold"] == 0.5
        assert result["verbose"] is True
        assert result["name"] == "test"
        assert result["other"] == "ignore"

    def test_coerce_params_no_schema(self):
        mock_plugin = MagicMock()
        mock_plugin.name = "test-plugin"
        mock_plugin.get_schema_for_op.return_value = None
        mock_plugin.get_schema.return_value = None
        params = {"limit": "48"}
        result = _coerce_params(mock_plugin, params, "some_op")
        assert result == params

    def test_coerce_params_invalid_types(self):
        mock_plugin = MagicMock()
        mock_plugin.name = "test-plugin"
        mock_plugin.get_schema_for_op.return_value = {"properties": {"limit": {"type": "integer"}}}
        params = {"limit": "abc"}
        result = _coerce_params(mock_plugin, params, "some_op")
        assert result["limit"] == "abc"  # Should remain string if not coercible


class TestPluginNameSanitization:
    """Verify _sanitize_plugin_name and _unsanitize_plugin_name handle mcp: and api: prefixes."""

    def test_sanitize_mcp_prefix(self):
        assert _sanitize_plugin_name("mcp:server") == "mcp-server"

    def test_unsanitize_mcp_prefix(self):
        assert _unsanitize_plugin_name("mcp-server") == "mcp:server"

    def test_sanitize_api_prefix(self):
        assert _sanitize_plugin_name("api:weather") == "api-weather"

    def test_unsanitize_api_prefix(self):
        assert _unsanitize_plugin_name("api-weather") == "api:weather"

    def test_sanitize_native_plugin_unchanged(self):
        assert _sanitize_plugin_name("github") == "github"

    def test_unsanitize_native_plugin_unchanged(self):
        assert _unsanitize_plugin_name("github") == "github"

    def test_sanitize_api_roundtrip(self):
        original = "api:my-service"
        assert _unsanitize_plugin_name(_sanitize_plugin_name(original)) == original

    def test_sanitize_mcp_roundtrip(self):
        original = "mcp:my-server"
        assert _unsanitize_plugin_name(_sanitize_plugin_name(original)) == original
