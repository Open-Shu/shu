"""Tests for shu_plugin_sdk.contracts.assert_plugin_contract."""

from __future__ import annotations

import warnings

import pytest

from shu_plugin_sdk.contracts import assert_plugin_contract

# ---------------------------------------------------------------------------
# Shared valid manifest (used by all fixture plugins in this file)
# ---------------------------------------------------------------------------

_VALID_MANIFEST = {
    "name": "test_plugin",
    "version": "1",
    "module": "plugins.test_plugin.plugin:TestPlugin",
    "capabilities": ["log"],
    "chat_callable_ops": ["read"],
    "allowed_feed_ops": ["ingest"],
    "op_auth": {},
}

_VALID_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["read", "ingest"]},
        "query": {"type": "string"},
    },
    "required": ["op"],
    "additionalProperties": False,
}

_VALID_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "result": {"type": "string"},
    },
    "required": ["result"],
    "additionalProperties": False,
}

_OP_SCHEMAS: dict[str, dict] = {
    "read": {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["read"]},
            "query": {"type": "string"},
        },
        "required": ["op"],
        "additionalProperties": False,
    },
    "ingest": {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["ingest"]},
        },
        "required": ["op"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Fixture plugin classes
# ---------------------------------------------------------------------------


class ValidPlugin:
    """Fully contract-compliant plugin used as the happy-path fixture."""

    name = "test_plugin"
    version = "1"

    def get_schema(self) -> dict:
        """Return combined input schema with op enum."""
        return _VALID_INPUT_SCHEMA

    def get_output_schema(self) -> dict:
        """Return constrained output schema."""
        return _VALID_OUTPUT_SCHEMA

    def get_schema_for_op(self, op_name: str) -> dict | None:
        """Return per-op schema or None for unknown ops."""
        return _OP_SCHEMAS.get(op_name)

    async def execute(self, params: dict, context: object, host: object) -> object:
        """Stub execute — not called during contract validation."""
        raise NotImplementedError


class ValidPluginNoSchemaForOp:
    """Contract-compliant plugin that does NOT implement get_schema_for_op.

    Used to verify that a UserWarning is emitted encouraging adoption of the
    forward-compatible per-op schema interface.
    """

    name = "test_plugin"
    version = "1"

    def get_schema(self) -> dict:
        """Return combined input schema with op enum."""
        return _VALID_INPUT_SCHEMA

    def get_output_schema(self) -> dict:
        """Return constrained output schema."""
        return _VALID_OUTPUT_SCHEMA

    async def execute(self, params: dict, context: object, host: object) -> object:
        """Stub execute — not called during contract validation."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Positive tests (happy path)
# ---------------------------------------------------------------------------


def test_valid_plugin_passes() -> None:
    """A fully compliant plugin passes contract validation with no warnings."""
    with warnings.catch_warnings(record=True) as warning_list:
        warnings.simplefilter("always")
        assert_plugin_contract(ValidPlugin, manifest=_VALID_MANIFEST)
    assert len(warning_list) == 0, (
        f"Expected no warnings for a fully compliant plugin, got: "
        f"{[str(w.message) for w in warning_list]}"
    )


def test_valid_plugin_warns_missing_schema_for_op() -> None:
    """A plugin without get_schema_for_op emits a UserWarning encouraging adoption."""
    with pytest.warns(UserWarning, match="get_schema_for_op"):
        assert_plugin_contract(ValidPluginNoSchemaForOp, manifest=_VALID_MANIFEST)


# ---------------------------------------------------------------------------
# Negative tests — manifest key validation (rules 1–4)
# ---------------------------------------------------------------------------


def _manifest_without(*keys: str) -> dict:
    """Return a copy of _VALID_MANIFEST with the given keys removed."""
    return {k: v for k, v in _VALID_MANIFEST.items() if k not in keys}


def test_missing_manifest_key_name() -> None:
    """Missing 'name' key raises AssertionError that mentions 'name'."""
    with pytest.raises(AssertionError, match="name"):
        assert_plugin_contract(ValidPlugin, manifest=_manifest_without("name"))


def test_missing_manifest_key_version() -> None:
    """Missing 'version' key raises AssertionError that mentions 'version'."""
    with pytest.raises(AssertionError, match="version"):
        assert_plugin_contract(ValidPlugin, manifest=_manifest_without("version"))


def test_missing_manifest_key_module() -> None:
    """Missing 'module' key raises AssertionError that mentions 'module'."""
    with pytest.raises(AssertionError, match="module"):
        assert_plugin_contract(ValidPlugin, manifest=_manifest_without("module"))


def test_missing_manifest_key_capabilities() -> None:
    """Missing 'capabilities' key raises AssertionError that mentions 'capabilities'."""
    with pytest.raises(AssertionError, match="capabilities"):
        assert_plugin_contract(ValidPlugin, manifest=_manifest_without("capabilities"))


def test_unknown_capability() -> None:
    """An unknown capability value raises AssertionError that names the bad value."""
    bad_manifest = {**_VALID_MANIFEST, "capabilities": ["unknown_cap"]}
    with pytest.raises(AssertionError, match="unknown_cap"):
        assert_plugin_contract(ValidPlugin, manifest=bad_manifest)


def test_invalid_module_format() -> None:
    """A module string without 'dotted.path:ClassName' format raises AssertionError."""
    bad_manifest = {**_VALID_MANIFEST, "module": "no_colon_here"}
    with pytest.raises(AssertionError):
        assert_plugin_contract(ValidPlugin, manifest=bad_manifest)


def test_plugin_not_instantiable() -> None:
    """A plugin class whose __init__ raises is caught and re-raised as AssertionError."""

    class BrokenPlugin:
        def __init__(self) -> None:
            raise RuntimeError("cannot instantiate")

        def get_schema(self) -> dict:
            return _VALID_INPUT_SCHEMA

        def get_output_schema(self) -> dict:
            return _VALID_OUTPUT_SCHEMA

    with pytest.raises(AssertionError, match="instantiate|BrokenPlugin"):
        assert_plugin_contract(BrokenPlugin, manifest=_VALID_MANIFEST)


# ---------------------------------------------------------------------------
# Negative tests — schema validation (rules 5–9)
# ---------------------------------------------------------------------------


def _plugin_with(*, input_schema: dict | None = None, output_schema: dict | None = None) -> type:
    """Build a plugin class with the given schemas, falling back to valid defaults."""

    class _Plugin:
        name = "test_plugin"
        version = "1"

        def get_schema(self) -> dict:
            return input_schema if input_schema is not None else _VALID_INPUT_SCHEMA

        def get_output_schema(self) -> dict:
            return output_schema if output_schema is not None else _VALID_OUTPUT_SCHEMA

        def get_schema_for_op(self, op_name: str) -> dict | None:
            return _OP_SCHEMAS.get(op_name)

        async def execute(self, params: dict, context: object, host: object) -> object:
            raise NotImplementedError

    return _Plugin


def test_invalid_input_schema() -> None:
    """get_schema() returning an invalid JSON Schema raises AssertionError."""
    Plugin = _plugin_with(input_schema={"type": "not-a-valid-type"})
    with pytest.raises(AssertionError):
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)


def test_missing_op_enum() -> None:
    """get_schema() without properties.op.enum raises AssertionError."""
    # Valid JSON Schema but missing the required op enum path
    Plugin = _plugin_with(input_schema={"type": "object", "properties": {"query": {"type": "string"}}})
    with pytest.raises(AssertionError, match="op.*enum|enum"):
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)


def test_passthrough_output_schema_empty_dict() -> None:
    """get_output_schema() returning {} raises AssertionError."""
    Plugin = _plugin_with(output_schema={})
    with pytest.raises(AssertionError):
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)


def test_passthrough_output_schema_bare_object() -> None:
    """get_output_schema() returning bare {'type': 'object'} raises AssertionError."""
    Plugin = _plugin_with(output_schema={"type": "object"})
    with pytest.raises(AssertionError):
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)


def test_invalid_output_schema() -> None:
    """get_output_schema() returning an invalid JSON Schema raises AssertionError."""
    Plugin = _plugin_with(output_schema={"type": "not-valid"})
    with pytest.raises(AssertionError):
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)


def test_op_in_chat_callable_not_in_enum() -> None:
    """chat_callable_ops referencing an op not in the schema enum raises AssertionError."""
    bad_manifest = {**_VALID_MANIFEST, "chat_callable_ops": ["missing_op"]}
    with pytest.raises(AssertionError, match="missing_op"):
        assert_plugin_contract(ValidPlugin, manifest=bad_manifest)


def test_op_in_allowed_feed_not_in_enum() -> None:
    """allowed_feed_ops referencing an op not in the schema enum raises AssertionError."""
    bad_manifest = {**_VALID_MANIFEST, "allowed_feed_ops": ["missing_op"]}
    with pytest.raises(AssertionError, match="missing_op"):
        assert_plugin_contract(ValidPlugin, manifest=bad_manifest)


def test_op_in_op_auth_not_in_enum() -> None:
    """op_auth key referencing an op not in the schema enum raises AssertionError."""
    bad_manifest = {**_VALID_MANIFEST, "op_auth": {"missing_op": {"provider": "google"}}}
    with pytest.raises(AssertionError, match="missing_op"):
        assert_plugin_contract(ValidPlugin, manifest=bad_manifest)


# ---------------------------------------------------------------------------
# Soft warning tests (rules 10–11)
# ---------------------------------------------------------------------------


def test_warns_missing_additional_properties() -> None:
    """Output schema without additionalProperties: false emits a UserWarning."""
    # Output schema is valid but lacks additionalProperties: false
    Plugin = _plugin_with(
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "string"}},
            # intentionally no additionalProperties: false
        }
    )
    with pytest.warns(UserWarning, match="additionalProperties"):
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)


def test_schema_for_op_valid_schemas() -> None:
    """A plugin with get_schema_for_op returning valid schemas passes with no errors."""
    with warnings.catch_warnings(record=True) as warning_list:
        warnings.simplefilter("always")
        assert_plugin_contract(ValidPlugin, manifest=_VALID_MANIFEST)
    assert len(warning_list) == 0, (
        f"Expected no warnings, got: {[str(w.message) for w in warning_list]}"
    )


def test_schema_for_op_invalid_schema() -> None:
    """get_schema_for_op returning an invalid JSON Schema for a known op raises AssertionError."""

    class InvalidOpSchemaPlugin:
        name = "test_plugin"
        version = "1"

        def get_schema(self) -> dict:
            return _VALID_INPUT_SCHEMA

        def get_output_schema(self) -> dict:
            return _VALID_OUTPUT_SCHEMA

        def get_schema_for_op(self, op_name: str) -> dict | None:
            if op_name == "read":
                # Invalid: 'not-a-type' is not a valid JSON Schema type
                return {"type": "not-a-type"}
            return None

        async def execute(self, params: dict, context: object, host: object) -> object:
            raise NotImplementedError

    with pytest.raises(AssertionError):
        assert_plugin_contract(InvalidOpSchemaPlugin, manifest=_VALID_MANIFEST)


def test_schema_for_op_inconsistent_ops() -> None:
    """get_schema_for_op returning non-None for an unknown op raises AssertionError."""

    class AlwaysReturnsSchema:
        name = "test_plugin"
        version = "1"

        def get_schema(self) -> dict:
            return _VALID_INPUT_SCHEMA

        def get_output_schema(self) -> dict:
            return _VALID_OUTPUT_SCHEMA

        def get_schema_for_op(self, op_name: str) -> dict:
            # Always returns a schema — including for unknown ops like '__unknown_op__'
            return {
                "type": "object",
                "properties": {"op": {"type": "string"}},
                "additionalProperties": False,
            }

        async def execute(self, params: dict, context: object, host: object) -> object:
            raise NotImplementedError

    with pytest.raises(AssertionError, match="__unknown_op__"):
        assert_plugin_contract(AlwaysReturnsSchema, manifest=_VALID_MANIFEST)


# ---------------------------------------------------------------------------
# execute() async validation (rule 12)
# ---------------------------------------------------------------------------


def test_sync_execute_raises() -> None:
    """A plugin with a synchronous execute() raises AssertionError."""

    class SyncExecutePlugin:
        name = "test_plugin"
        version = "1"

        def get_schema(self) -> dict:
            return _VALID_INPUT_SCHEMA

        def get_output_schema(self) -> dict:
            return _VALID_OUTPUT_SCHEMA

        def get_schema_for_op(self, op_name: str) -> dict | None:
            return _OP_SCHEMAS.get(op_name)

        def execute(self, params: dict, context: object, host: object) -> object:
            return {}

    with pytest.raises(AssertionError, match="async"):
        assert_plugin_contract(SyncExecutePlugin, manifest=_VALID_MANIFEST)


def test_missing_execute_raises() -> None:
    """A plugin with no execute() at all raises AssertionError."""

    class NoExecutePlugin:
        name = "test_plugin"
        version = "1"

        def get_schema(self) -> dict:
            return _VALID_INPUT_SCHEMA

        def get_output_schema(self) -> dict:
            return _VALID_OUTPUT_SCHEMA

        def get_schema_for_op(self, op_name: str) -> dict | None:
            return _OP_SCHEMAS.get(op_name)

    with pytest.raises(AssertionError):
        assert_plugin_contract(NoExecutePlugin, manifest=_VALID_MANIFEST)


# ---------------------------------------------------------------------------
# name/version attribute match (rule 13)
# ---------------------------------------------------------------------------


def test_name_mismatch_raises() -> None:
    """Plugin class attribute 'name' that differs from manifest raises AssertionError."""

    class WrongNamePlugin(ValidPlugin):
        name = "completely_wrong_name"

    with pytest.raises(AssertionError, match="completely_wrong_name"):
        assert_plugin_contract(WrongNamePlugin, manifest=_VALID_MANIFEST)


def test_version_mismatch_raises() -> None:
    """Plugin class attribute 'version' that differs from manifest raises AssertionError."""

    class WrongVersionPlugin(ValidPlugin):
        version = "99"

    with pytest.raises(AssertionError, match="99"):
        assert_plugin_contract(WrongVersionPlugin, manifest=_VALID_MANIFEST)


# ---------------------------------------------------------------------------
# Nested object additionalProperties warning (rule 14)
# ---------------------------------------------------------------------------


def test_warns_nested_object_missing_additional_properties() -> None:
    """A nested object property without additionalProperties: false emits a UserWarning."""
    Plugin = _plugin_with(
        output_schema={
            "type": "object",
            "properties": {
                "result": {"type": "string"},
                "metadata": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    # intentionally no additionalProperties: false
                },
            },
            "additionalProperties": False,
        }
    )
    with pytest.warns(UserWarning, match="metadata"):
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)


def test_no_warn_nested_object_with_additional_properties_false() -> None:
    """A nested object property with additionalProperties: false emits no warning."""
    Plugin = _plugin_with(
        output_schema={
            "type": "object",
            "properties": {
                "result": {"type": "string"},
                "metadata": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        }
    )
    with warnings.catch_warnings(record=True) as warning_list:
        warnings.simplefilter("always")
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)
    assert len(warning_list) == 0, (
        f"Expected no warnings, got: {[str(w.message) for w in warning_list]}"
    )


# ---------------------------------------------------------------------------
# required-not-in-properties (rule 15)
# ---------------------------------------------------------------------------


def test_input_schema_phantom_required_field_raises() -> None:
    """get_schema() with a required field absent from properties raises AssertionError."""
    Plugin = _plugin_with(
        input_schema={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["read", "ingest"]},
                "query": {"type": "string"},
            },
            "required": ["op", "ghost_field"],  # ghost_field not in properties
            "additionalProperties": False,
        }
    )
    with pytest.raises(AssertionError, match="ghost_field"):
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)


def test_output_schema_phantom_required_field_raises() -> None:
    """get_output_schema() with a required field absent from properties raises AssertionError."""
    Plugin = _plugin_with(
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result", "ghost_field"],  # ghost_field not in properties
            "additionalProperties": False,
        }
    )
    with pytest.raises(AssertionError, match="ghost_field"):
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)


# ---------------------------------------------------------------------------
# array-without-items warning (rule 16)
# ---------------------------------------------------------------------------


def test_warns_output_array_property_missing_items() -> None:
    """Output schema array property without 'items' emits a UserWarning."""
    Plugin = _plugin_with(
        output_schema={
            "type": "object",
            "properties": {
                "result": {"type": "string"},
                "tags": {"type": "array"},  # no items
            },
            "required": ["result"],
            "additionalProperties": False,
        }
    )
    with pytest.warns(UserWarning, match="tags"):
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)


def test_warns_input_array_property_missing_items() -> None:
    """Input schema array property without 'items' emits a UserWarning."""
    Plugin = _plugin_with(
        input_schema={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["read", "ingest"]},
                "filters": {"type": "array"},  # no items
            },
            "required": ["op"],
            "additionalProperties": False,
        }
    )
    with pytest.warns(UserWarning, match="filters"):
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)


def test_no_warn_array_with_items() -> None:
    """Array property with 'items' defined does not emit a UserWarning."""
    Plugin = _plugin_with(
        output_schema={
            "type": "object",
            "properties": {
                "result": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["result"],
            "additionalProperties": False,
        }
    )
    with warnings.catch_warnings(record=True) as warning_list:
        warnings.simplefilter("always")
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)
    assert len(warning_list) == 0, (
        f"Expected no warnings, got: {[str(w.message) for w in warning_list]}"
    )


# ---------------------------------------------------------------------------
# untyped output property warning (rule 17)
# ---------------------------------------------------------------------------


def test_warns_untyped_output_property() -> None:
    """Output schema property with no 'type' field emits a UserWarning."""
    Plugin = _plugin_with(
        output_schema={
            "type": "object",
            "properties": {
                "result": {"type": "string"},
                "metadata": {"description": "anything goes"},  # no type
            },
            "required": ["result"],
            "additionalProperties": False,
        }
    )
    with pytest.warns(UserWarning, match="metadata"):
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)


def test_no_warn_all_output_properties_typed() -> None:
    """Output schema where every property has a 'type' emits no warning."""
    Plugin = _plugin_with(
        output_schema={
            "type": "object",
            "properties": {
                "result": {"type": "string"},
                "count": {"type": "integer"},
                "data": {"type": ["string", "null"]},
            },
            "required": ["result"],
            "additionalProperties": False,
        }
    )
    with warnings.catch_warnings(record=True) as warning_list:
        warnings.simplefilter("always")
        assert_plugin_contract(Plugin, manifest=_VALID_MANIFEST)
    assert len(warning_list) == 0, (
        f"Expected no warnings, got: {[str(w.message) for w in warning_list]}"
    )
