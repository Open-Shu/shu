"""Static contract validation for Shu plugins."""

from __future__ import annotations

import importlib
import inspect
import re
import warnings

import jsonschema

KNOWN_CAPABILITIES: frozenset[str] = frozenset(
    {
        "http",
        "auth",
        "kb",
        "storage",
        "secrets",
        "cursor",
        "cache",
        "log",
        "utils",
        "identity",
        "ocr",
    }
)

_MODULE_PATTERN = re.compile(r"^[\w.]+:[\w]+$")


def _discover_manifest(plugin_cls: type) -> dict:
    """Discover the PLUGIN_MANIFEST for a plugin class via module inspection.

    Strips the final segment from ``plugin_cls.__module__`` (e.g. ``plugins.foo.plugin``
    becomes ``plugins.foo``), imports ``{package}.manifest``, and returns
    ``PLUGIN_MANIFEST``.

    Args:
        plugin_cls: The plugin class whose manifest should be discovered.

    Returns:
        The ``PLUGIN_MANIFEST`` dict from the plugin's manifest module.

    Raises:
        AssertionError: If the manifest module or ``PLUGIN_MANIFEST`` cannot be found.
    """
    module_parts = plugin_cls.__module__.rsplit(".", 1)
    if len(module_parts) < 2:
        raise AssertionError(
            f"Cannot discover manifest for {plugin_cls!r}: module '{plugin_cls.__module__}' "
            "has no parent package. Pass the manifest explicitly via the `manifest` argument: "
            "assert_plugin_contract(MyPlugin, manifest=PLUGIN_MANIFEST)"
        )
    package = module_parts[0]
    manifest_module_name = f"{package}.manifest"
    try:
        manifest_module = importlib.import_module(manifest_module_name)
    except ImportError as exc:
        raise AssertionError(
            f"Cannot discover manifest for {plugin_cls!r}: failed to import "
            f"'{manifest_module_name}'. Ensure the manifest module exists, or pass "
            "the manifest explicitly via the `manifest` argument: "
            "assert_plugin_contract(MyPlugin, manifest=PLUGIN_MANIFEST)"
        ) from exc
    if not hasattr(manifest_module, "PLUGIN_MANIFEST"):
        raise AssertionError(
            f"Cannot discover manifest for {plugin_cls!r}: '{manifest_module_name}' "
            "does not define 'PLUGIN_MANIFEST'. Pass the manifest explicitly via the "
            "`manifest` argument: assert_plugin_contract(MyPlugin, manifest=PLUGIN_MANIFEST)"
        )
    return manifest_module.PLUGIN_MANIFEST  # type: ignore[no-any-return]


def assert_plugin_contract(
    plugin_cls: type,
    manifest: dict | None = None,
) -> None:
    """Validate a plugin's manifest and schemas against the Shu plugin contract.

    Raises ``AssertionError`` on hard contract violations. Emits ``warnings.warn``
    for soft issues (e.g. missing ``additionalProperties: false``, missing
    ``get_schema_for_op``).

    Args:
        plugin_cls: The plugin class to validate.
        manifest: The plugin manifest dict. If ``None``, the manifest is discovered
            automatically from the plugin's package (``{package}.manifest.PLUGIN_MANIFEST``).

    Raises:
        AssertionError: If any hard contract rule is violated.
    """
    if manifest is None:
        manifest = _discover_manifest(plugin_cls)

    _validate_manifest_keys(manifest)
    _validate_capabilities(manifest)
    _validate_module_string(manifest)

    plugin = _instantiate_plugin(plugin_cls)
    _validate_execute_is_async(plugin)
    _validate_name_version_match(plugin, manifest)

    schema = _validate_get_schema(plugin)
    op_enum = _validate_op_enum(schema)
    _validate_schema_required_fields(schema, "get_schema()")
    _warn_schema_array_no_items(schema, "get_schema()", plugin_cls)

    output_schema = _validate_get_output_schema(plugin)
    _validate_output_schema_not_passthrough(output_schema)
    _validate_output_schema_draft7(output_schema)
    _validate_schema_required_fields(output_schema, "get_output_schema()")
    _warn_output_schema_unbounded(output_schema, plugin_cls)
    _warn_nested_output_schema_unbounded(output_schema, plugin_cls)
    _warn_schema_array_no_items(output_schema, "get_output_schema()", plugin_cls)
    _warn_output_schema_untyped_properties(output_schema, plugin_cls)

    _validate_op_cross_references(manifest, op_enum)

    _validate_get_schema_for_op(plugin, op_enum)


# ---------------------------------------------------------------------------
# Internal validation helpers
# ---------------------------------------------------------------------------


def _validate_manifest_keys(manifest: dict) -> None:
    """Assert that all required manifest keys are present."""
    required_keys = ("name", "version", "module", "capabilities")
    for key in required_keys:
        assert key in manifest, (
            f"Manifest is missing required key '{key}'. "
            f"All of {required_keys} must be present in PLUGIN_MANIFEST."
        )


def _validate_capabilities(manifest: dict) -> None:
    """Assert that all declared capabilities are in the known allowlist."""
    declared: list[str] = manifest.get("capabilities", [])
    unknown = set(declared) - KNOWN_CAPABILITIES
    assert not unknown, (
        f"Manifest 'capabilities' contains unknown value(s): {sorted(unknown)}. "
        f"Known capabilities are: {sorted(KNOWN_CAPABILITIES)}"
    )


def _validate_module_string(manifest: dict) -> None:
    """Assert that the 'module' field matches the required dotted.path:ClassName format."""
    module_str: str = manifest.get("module", "")
    assert _MODULE_PATTERN.match(module_str), (
        f"Manifest 'module' value '{module_str}' is not a valid 'dotted.path:ClassName' string. "
        "Expected format: 'package.subpackage.module:ClassName'"
    )


def _instantiate_plugin(plugin_cls: type) -> object:
    """Assert that the plugin class can be instantiated with no arguments."""
    try:
        return plugin_cls()
    except Exception as exc:
        raise AssertionError(
            f"Failed to instantiate plugin class {plugin_cls!r} with no arguments: {exc}. "
            "Plugin classes must be instantiable without constructor arguments."
        ) from exc


def _validate_get_schema(plugin: object) -> dict:
    """Assert that get_schema() returns a valid JSON Schema Draft 7 dict."""
    assert hasattr(plugin, "get_schema"), (
        f"Plugin {plugin.__class__!r} does not implement 'get_schema()'. "
        "This method is required by the Shu Plugin Protocol."
    )
    schema = plugin.get_schema()  # type: ignore[union-attr]
    assert schema is not None, (
        f"Plugin {plugin.__class__!r}.get_schema() returned None. "
        "It must return a valid JSON Schema dict."
    )
    _assert_valid_draft7(schema, context="get_schema()")
    return schema  # type: ignore[return-value]


def _validate_op_enum(schema: dict) -> list[str]:
    """Assert that the schema has a properties.op.enum field and return the op list."""
    op_enum = schema.get("properties", {}).get("op", {}).get("enum")
    assert isinstance(op_enum, list) and len(op_enum) > 0, (
        "get_schema() must return a schema with 'properties.op.enum' containing a non-empty list "
        "of operation names. The Shu Loader requires this field to discover plugin operations."
    )
    return op_enum  # type: ignore[return-value]


def _validate_get_output_schema(plugin: object) -> dict:
    """Assert that get_output_schema() returns a non-None, non-empty value."""
    assert hasattr(plugin, "get_output_schema"), (
        f"Plugin {plugin.__class__!r} does not implement 'get_output_schema()'. "
        "This method is required by the Shu Plugin Protocol."
    )
    output_schema = plugin.get_output_schema()  # type: ignore[union-attr]
    assert output_schema is not None, (
        f"Plugin {plugin.__class__!r}.get_output_schema() returned None. "
        "A passthrough (None/empty) output schema allows unbounded data into the LLM context."
    )
    assert output_schema != {}, (
        f"Plugin {plugin.__class__!r}.get_output_schema() returned {{}} (empty dict). "
        "A passthrough output schema allows unbounded data into the LLM context."
    )
    return output_schema  # type: ignore[return-value]


def _validate_output_schema_not_passthrough(output_schema: dict) -> None:
    """Assert that the output schema is not a bare object with no properties."""
    is_bare_object = (
        output_schema.get("type") == "object"
        and not output_schema.get("properties")
    )
    assert not is_bare_object, (
        "get_output_schema() returned a bare {'type': 'object'} schema with no 'properties' "
        "defined. This is a passthrough schema that allows unbounded data into the LLM context. "
        "Define explicit 'properties' to constrain the output."
    )


def _validate_output_schema_draft7(output_schema: dict) -> None:
    """Assert that the output schema is valid JSON Schema Draft 7."""
    _assert_valid_draft7(output_schema, context="get_output_schema()")


def _warn_output_schema_unbounded(output_schema: dict, plugin_cls: type) -> None:
    """Warn if the output schema does not set additionalProperties: false."""
    if output_schema.get("additionalProperties") is not False:
        warnings.warn(
            f"Plugin {plugin_cls.__name__}.get_output_schema() does not set "
            "'additionalProperties: false'. Without this constraint, any additional fields "
            "returned by the plugin will flow unfiltered into the LLM context, potentially "
            "exhausting token budgets or producing malformed prompts. "
            "Add 'additionalProperties: false' to your output schema.",
            UserWarning,
            stacklevel=3,
        )


def _validate_execute_is_async(plugin: object) -> None:
    """Assert that the plugin's execute method exists and is a coroutine function."""
    assert hasattr(plugin, "execute"), (
        f"Plugin {plugin.__class__!r} does not implement 'execute()'. "
        "This method is required by the Shu Plugin Protocol."
    )
    assert inspect.iscoroutinefunction(plugin.execute), (  # type: ignore[union-attr]
        f"Plugin {plugin.__class__.__name__}.execute() is not an async function. "
        "It must be defined as 'async def execute(self, params, context, host)'. "
        "A synchronous execute() will break Shu's async execution pipeline."
    )


def _validate_name_version_match(plugin: object, manifest: dict) -> None:
    """Assert that the plugin's name and version class attributes match the manifest."""
    plugin_name = getattr(plugin, "name", None)
    manifest_name = manifest.get("name")
    assert plugin_name == manifest_name, (
        f"Plugin class attribute 'name' ({plugin_name!r}) does not match "
        f"manifest 'name' ({manifest_name!r}). They must be identical so the "
        "Shu Loader can register and look up the plugin correctly."
    )

    plugin_version = getattr(plugin, "version", None)
    manifest_version = manifest.get("version")
    assert plugin_version == manifest_version, (
        f"Plugin class attribute 'version' ({plugin_version!r}) does not match "
        f"manifest 'version' ({manifest_version!r}). They must be identical."
    )


def _warn_nested_output_schema_unbounded(output_schema: dict, plugin_cls: type) -> None:
    """Warn if any nested object property in the output schema lacks additionalProperties: false."""
    properties = output_schema.get("properties", {})
    unbounded = []
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        # Only warn when the nested property itself defines 'properties' — that's
        # the case where additionalProperties: false provides value. A bare
        # {"type": "object"} with no properties key has nothing to constrain.
        if "properties" in prop_schema and prop_schema.get("additionalProperties") is not False:
            unbounded.append(prop_name)
    if unbounded:
        warnings.warn(
            f"Plugin {plugin_cls.__name__}.get_output_schema() has nested object "
            f"propert{'ies' if len(unbounded) > 1 else 'y'} {sorted(unbounded)} without "
            "'additionalProperties: false'. Unbounded nested objects can allow unexpected "
            "data into the LLM context. Add 'additionalProperties: false' to each nested "
            "object schema.",
            UserWarning,
            stacklevel=3,
        )


def _validate_schema_required_fields(schema: dict, context: str) -> None:
    """Assert that every field listed in 'required' is defined in 'properties'.

    A required field that is absent from properties is a silent no-op in JSON
    Schema — validators never check for it, so it provides false confidence.
    """
    properties = set(schema.get("properties", {}).keys())
    required: list[str] = schema.get("required", [])
    phantom = [f for f in required if f not in properties]
    assert not phantom, (
        f"{context} declares {phantom} in 'required' but "
        f"{'they are' if len(phantom) > 1 else 'it is'} not defined in 'properties'. "
        "These required constraints are silently ignored by JSON Schema validators — "
        "add the fields to 'properties' or remove them from 'required'."
    )


def _warn_schema_array_no_items(schema: dict, context: str, plugin_cls: type) -> None:
    """Warn if any property has an array type but no 'items' schema.

    Without 'items', array elements can be any type, which may allow unexpected
    data (nested objects, large blobs) to flow into the LLM context.
    """
    properties = schema.get("properties", {})
    missing_items = []
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        prop_type = prop_schema.get("type")
        is_array = prop_type == "array" or (
            isinstance(prop_type, list) and "array" in prop_type
        )
        if is_array and "items" not in prop_schema:
            missing_items.append(prop_name)
    if missing_items:
        warnings.warn(
            f"Plugin {plugin_cls.__name__}.{context} has array "
            f"propert{'ies' if len(missing_items) > 1 else 'y'} {sorted(missing_items)} "
            "without 'items' defined. Without 'items', array elements can be any type. "
            "Add 'items' to constrain what array elements are allowed.",
            UserWarning,
            stacklevel=3,
        )


def _warn_output_schema_untyped_properties(output_schema: dict, plugin_cls: type) -> None:
    """Warn if any property in the output schema has no 'type' field.

    Untyped properties accept any JSON value (string, number, object, array, null),
    which can allow unexpected data into the LLM context.
    """
    properties = output_schema.get("properties", {})
    untyped = [
        name
        for name, prop_schema in properties.items()
        if isinstance(prop_schema, dict) and "type" not in prop_schema
    ]
    if untyped:
        warnings.warn(
            f"Plugin {plugin_cls.__name__}.get_output_schema() has "
            f"propert{'ies' if len(untyped) > 1 else 'y'} {sorted(untyped)} with no 'type' "
            "field. Untyped properties accept any JSON value. "
            "Add an explicit 'type' to each output property.",
            UserWarning,
            stacklevel=3,
        )


def _validate_op_cross_references(manifest: dict, op_enum: list[str]) -> None:
    """Assert that all ops in chat_callable_ops, allowed_feed_ops, and op_auth exist in the op enum."""
    op_set = set(op_enum)

    for field in ("chat_callable_ops", "allowed_feed_ops"):
        declared_ops: list[str] = manifest.get(field, [])
        unknown_ops = set(declared_ops) - op_set
        assert not unknown_ops, (
            f"Manifest '{field}' references op(s) {sorted(unknown_ops)} that are not in "
            f"the 'op' enum from get_schema(): {sorted(op_set)}. "
            "All ops referenced in the manifest must be declared in the schema's op enum."
        )

    op_auth: dict = manifest.get("op_auth", {})
    unknown_auth_ops = set(op_auth.keys()) - op_set
    assert not unknown_auth_ops, (
        f"Manifest 'op_auth' references op(s) {sorted(unknown_auth_ops)} that are not in "
        f"the 'op' enum from get_schema(): {sorted(op_set)}. "
        "All ops in op_auth must be declared in the schema's op enum."
    )


def _validate_get_schema_for_op(plugin: object, op_enum: list[str]) -> None:
    """Validate get_schema_for_op if present; warn if absent."""
    if not hasattr(plugin, "get_schema_for_op"):
        warnings.warn(
            f"Plugin {plugin.__class__.__name__} does not implement 'get_schema_for_op(op_name)'. "
            "This method is the forward-compatible replacement for get_schema() and allows Shu "
            "to validate op-specific parameters precisely. Consider adding it to your plugin.",
            UserWarning,
            stacklevel=3,
        )
        return

    for op in op_enum:
        result = plugin.get_schema_for_op(op)  # type: ignore[union-attr]
        assert result is None or isinstance(result, dict), (
            f"Plugin {plugin.__class__.__name__}.get_schema_for_op('{op}') returned "
            f"{result!r}. It must return a dict (valid JSON Schema) or None."
        )
        if result is not None:
            _assert_valid_draft7(result, context=f"get_schema_for_op('{op}')")

    # Verify that get_schema_for_op is not returning schemas for ops outside the enum.
    # We also test unknown-op behaviour: the method must return None for unknown ops.
    unknown_test_op = "__unknown_op__"
    unknown_result = plugin.get_schema_for_op(unknown_test_op)  # type: ignore[union-attr]
    assert unknown_result is None, (
        f"Plugin {plugin.__class__.__name__}.get_schema_for_op('{unknown_test_op}') returned "
        f"{unknown_result!r} instead of None. It must return None for unknown op names."
    )


def _assert_valid_draft7(schema: dict, *, context: str) -> None:
    """Assert that *schema* is valid JSON Schema Draft 7.

    Args:
        schema: The schema dict to validate.
        context: Human-readable label for the schema (used in the error message).

    Raises:
        AssertionError: If the schema is not valid JSON Schema Draft 7.
    """
    try:
        validator_cls = jsonschema.Draft7Validator
        validator_cls.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise AssertionError(
            f"{context} returned an invalid JSON Schema (Draft 7): {exc.message}"
        ) from exc
