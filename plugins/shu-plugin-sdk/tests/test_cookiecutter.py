"""Tests for the _cookiecutter template — verifies the template stays valid.

These tests act as a regression gate: if the SDK contract rules change, the
cookie-cutter template must be updated to stay in sync.
"""

from __future__ import annotations

from shu_plugin_sdk.contracts import assert_plugin_contract

from _cookiecutter.manifest import PLUGIN_MANIFEST
from _cookiecutter.plugin import EchoPlugin


def test_cookiecutter_passes_contract() -> None:
    """EchoPlugin with its bundled manifest satisfies the full plugin contract."""
    assert_plugin_contract(EchoPlugin, manifest=PLUGIN_MANIFEST)


def test_cookiecutter_has_schema_for_op() -> None:
    """EchoPlugin.get_schema_for_op returns a schema for 'echo' and None for unknown ops."""
    plugin = EchoPlugin()

    echo_schema = plugin.get_schema_for_op("echo")
    assert isinstance(echo_schema, dict), "expected a dict schema for 'echo'"
    assert "properties" in echo_schema, "schema for 'echo' should have 'properties'"

    assert plugin.get_schema_for_op("unknown") is None
    assert plugin.get_schema_for_op("__unknown_op__") is None
