"""
Unit tests for plugin execution service.
"""

import pytest
from unittest.mock import MagicMock
from shu.services.plugin_execution import _coerce_params

class TestParamCoercion:
    def test_coerce_params(self):
        mock_plugin = MagicMock()
        mock_plugin.get_schema.return_value = {
            "properties": {
                "limit": {"type": "integer"},
                "threshold": {"type": "number"},
                "verbose": {"type": "boolean"},
                "name": {"type": "string"},
            }
        }
        
        params = {
            "limit": "48",
            "threshold": "0.5",
            "verbose": "true",
            "name": "test",
            "other": "ignore"
        }
        
        result = _coerce_params(mock_plugin, params)
        
        assert result["limit"] == 48
        assert result["threshold"] == 0.5
        assert result["verbose"] is True
        assert result["name"] == "test"
        assert result["other"] == "ignore"

    def test_coerce_params_no_schema(self):
        mock_plugin = MagicMock()
        mock_plugin.get_schema.return_value = None
        params = {"limit": "48"}
        result = _coerce_params(mock_plugin, params)
        assert result == params

    def test_coerce_params_invalid_types(self):
         mock_plugin = MagicMock()
         mock_plugin.get_schema.return_value = {
             "properties": {"limit": {"type": "integer"}}
         }
         params = {"limit": "abc"}
         result = _coerce_params(mock_plugin, params)
         assert result["limit"] == "abc" # Should remain string if not coercible
