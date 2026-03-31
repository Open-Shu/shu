"""
Unit tests for plugin execution service.
"""

from unittest.mock import MagicMock

from shu.services.plugin_execution import _coerce_params


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
