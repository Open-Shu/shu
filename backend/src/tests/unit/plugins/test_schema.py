"""Unit tests for resolve_op_schema helper."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("SHU_DATABASE_URL", "test_db_url")
os.environ.setdefault("JWT_SECRET_KEY", "test_secret")

from shu.plugins.schema import (
    extract_op_title,
    resolve_all_ops,
    resolve_op_schema,
    validate_legacy_schema,
    validate_per_op_schemas,
)


def _make_plugin(
    name: str = "test-plugin",
    *,
    per_op_schema: dict[str, Any] | None | Exception = None,
    legacy_schema: dict[str, Any] | None | Exception = None,
    has_per_op: bool = True,
    has_legacy: bool = True,
) -> MagicMock:
    """Build a mock plugin with configurable schema methods."""
    plugin = MagicMock()
    plugin.name = name
    plugin.version = "1.0.0"

    if has_per_op:
        if isinstance(per_op_schema, Exception):
            plugin.get_schema_for_op.side_effect = per_op_schema
        else:
            plugin.get_schema_for_op.return_value = per_op_schema
    else:
        del plugin.get_schema_for_op

    if has_legacy:
        if isinstance(legacy_schema, Exception):
            plugin.get_schema.side_effect = legacy_schema
        else:
            plugin.get_schema.return_value = legacy_schema
    else:
        del plugin.get_schema

    return plugin


class TestResolveOpSchema:
    def test_returns_per_op_schema_when_available(self):
        expected = {"type": "object", "properties": {"repo": {"type": "string"}}}
        plugin = _make_plugin(per_op_schema=expected)

        result = resolve_op_schema(plugin, "fetch_activity")

        assert result == expected
        plugin.get_schema_for_op.assert_called_once_with("fetch_activity")
        plugin.get_schema.assert_not_called()

    def test_falls_back_to_legacy_when_per_op_returns_none(self):
        legacy = {"type": "object"}
        plugin = _make_plugin(per_op_schema=None, legacy_schema=legacy)

        result = resolve_op_schema(plugin, "some_op")

        assert result == legacy

    def test_falls_back_to_legacy_when_per_op_raises(self):
        legacy = {"type": "object"}
        plugin = _make_plugin(
            per_op_schema=RuntimeError("boom"),
            legacy_schema=legacy,
        )

        result = resolve_op_schema(plugin, "some_op")

        assert result == legacy

    def test_falls_back_to_legacy_when_per_op_missing(self):
        legacy = {"type": "object"}
        plugin = _make_plugin(has_per_op=False, legacy_schema=legacy)

        result = resolve_op_schema(plugin, "some_op")

        assert result == legacy

    def test_returns_none_when_both_unavailable(self):
        plugin = _make_plugin(has_per_op=False, legacy_schema=None)

        result = resolve_op_schema(plugin, "some_op")

        assert result is None

    def test_returns_none_when_legacy_raises(self):
        plugin = _make_plugin(
            per_op_schema=None,
            legacy_schema=ValueError("bad"),
        )

        result = resolve_op_schema(plugin, "some_op")

        assert result is None

    def test_returns_none_when_both_raise(self):
        plugin = _make_plugin(
            per_op_schema=RuntimeError("boom"),
            legacy_schema=ValueError("bad"),
        )

        result = resolve_op_schema(plugin, "some_op")

        assert result is None

    def test_no_legacy_call_when_per_op_succeeds(self):
        plugin = _make_plugin(
            per_op_schema={"type": "object"},
            legacy_schema={"type": "object"},
        )

        resolve_op_schema(plugin, "op1")

        plugin.get_schema.assert_not_called()


class TestExtractOpTitle:
    def test_returns_none_for_none_schema(self):
        assert extract_op_title(None, "op") is None

    def test_returns_title_field_from_per_op_schema(self):
        schema = {"title": "Check Availability", "type": "object"}
        assert extract_op_title(schema, "check") == "Check Availability"

    def test_returns_enum_label_from_combined_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "x-ui": {"enum_labels": {"book": "Book Service", "cancel": "Cancel"}},
                }
            },
        }
        assert extract_op_title(schema, "book") == "Book Service"

    def test_title_field_takes_precedence_over_enum_labels(self):
        schema = {
            "title": "Per-Op Title",
            "type": "object",
            "properties": {
                "op": {"x-ui": {"enum_labels": {"op1": "Combined Title"}}},
            },
        }
        assert extract_op_title(schema, "op1") == "Per-Op Title"

    def test_returns_none_when_no_title_or_labels(self):
        schema = {"type": "object", "properties": {}}
        assert extract_op_title(schema, "op") is None

    def test_returns_none_for_unknown_op_in_enum_labels(self):
        schema = {
            "type": "object",
            "properties": {
                "op": {"x-ui": {"enum_labels": {"known": "Known Op"}}},
            },
        }
        assert extract_op_title(schema, "unknown") is None


class TestValidatePerOpSchemas:
    def test_passes_when_all_ops_have_schemas(self):
        schema = {"type": "object", "title": "Test Op", "description": "A test operation."}
        plugin = _make_plugin(per_op_schema=schema)
        validate_per_op_schemas(plugin, ["op1", "op2"])

    def test_raises_when_op_returns_none(self):
        plugin = _make_plugin(per_op_schema=None)
        with pytest.raises(ImportError, match="returned None for ops"):
            validate_per_op_schemas(plugin, ["op1"])

    def test_raises_when_title_missing(self):
        plugin = _make_plugin(per_op_schema={"type": "object", "description": "Has description."})
        with pytest.raises(ImportError, match="missing 'title'"):
            validate_per_op_schemas(plugin, ["op1"])

    def test_raises_when_description_missing(self):
        plugin = _make_plugin(per_op_schema={"type": "object", "title": "Has Title"})
        with pytest.raises(ImportError, match="missing 'description'"):
            validate_per_op_schemas(plugin, ["op1"])

class TestValidateLegacySchema:
    def test_passes_with_valid_op_enum(self):
        plugin = _make_plugin(
            has_per_op=False,
            legacy_schema={
                "type": "object",
                "properties": {"op": {"type": "string", "enum": ["list", "get"]}},
            },
        )
        validate_legacy_schema(plugin)

    def test_raises_when_no_op_enum(self):
        plugin = _make_plugin(
            has_per_op=False,
            legacy_schema={"type": "object", "properties": {}},
        )
        with pytest.raises(ImportError, match="missing op enum"):
            validate_legacy_schema(plugin)

    def test_raises_when_schema_is_none(self):
        plugin = _make_plugin(has_per_op=False, legacy_schema=None)
        with pytest.raises(ImportError, match="missing op enum"):
            validate_legacy_schema(plugin)


class TestResolveAllOps:
    def test_resolves_all_declared_ops(self):
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        plugin = _make_plugin(per_op_schema=schema)

        result = resolve_all_ops(plugin, ["search", "list"])

        assert set(result.keys()) == {"search", "list"}
        assert result["search"].schema == schema
        assert result["list"].schema == schema

    def test_deduplicates_ops(self):
        plugin = _make_plugin(per_op_schema={"type": "object"})

        result = resolve_all_ops(plugin, ["op1", "op1", "op2"])

        assert list(result.keys()) == ["op1", "op2"]

    def test_populates_title_and_description(self):
        schema = {"type": "object", "title": "Search", "description": "Full-text search"}
        plugin = _make_plugin(per_op_schema=schema)

        result = resolve_all_ops(plugin, ["search"])

        assert result["search"].title == "Search"
        assert result["search"].description == "Full-text search"

    def test_none_schema_when_unresolvable(self):
        plugin = _make_plugin(per_op_schema=None, legacy_schema=None)

        result = resolve_all_ops(plugin, ["unknown"])

        assert result["unknown"].schema is None
        assert result["unknown"].title is None
        assert result["unknown"].description is None
