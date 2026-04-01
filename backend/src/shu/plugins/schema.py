"""Shared schema resolution helper for plugin operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shu.core.logging import get_logger
from shu.plugins.base import Plugin

logger = get_logger(__name__)


def resolve_op_schema(plugin: Plugin, op: str) -> dict[str, Any] | None:
    """Resolve the JSON Schema for a specific plugin operation.

    Tries the per-op interface first (``get_schema_for_op``), then falls back
    to the legacy ``get_schema()`` with a deprecation warning.  Returns
    ``None`` when no schema can be obtained.
    """
    get_schema_for_op = getattr(plugin, "get_schema_for_op", None)
    if callable(get_schema_for_op):
        try:
            schema = get_schema_for_op(op)
            if schema is not None:
                return schema
        except Exception:
            logger.exception(
                "resolve_op_schema.get_schema_for_op_failed plugin=%s op=%s",
                plugin.name,
                op,
            )

    try:
        schema = plugin.get_schema()
    except Exception:
        logger.exception(
            "resolve_op_schema.get_schema_fallback_failed plugin=%s op=%s",
            plugin.name,
            op,
        )
        return None

    if schema is not None:
        logger.warning(
            "resolve_op_schema.deprecated_get_schema plugin=%s op=%s; implement get_schema_for_op()",
            plugin.name,
            op,
        )

    return schema


def validate_per_op_schemas(plugin: Plugin, declared_ops: list[str]) -> None:
    """Validate that a per-op plugin produces a schema for each declared op.

    Each schema must be non-None and include ``title`` and ``description``.
    Raises ``ImportError`` on violations.
    """
    missing: list[str] = []
    missing_title: list[str] = []
    missing_description: list[str] = []
    for op in declared_ops:
        schema = plugin.get_schema_for_op(op)
        if schema is None:
            missing.append(op)
            continue
        if not schema.get("title"):
            missing_title.append(op)
        if not schema.get("description"):
            missing_description.append(op)
    if missing:
        raise ImportError(f"Plugin '{plugin.name}' get_schema_for_op returned None for ops: {missing}")
    if missing_title:
        raise ImportError(f"Plugin '{plugin.name}' per-op schema missing 'title' for ops: {missing_title}")
    if missing_description:
        raise ImportError(f"Plugin '{plugin.name}' per-op schema missing 'description' for ops: {missing_description}")


def validate_legacy_schema(plugin: Plugin) -> None:
    """Validate that a legacy plugin's combined schema declares at least one op.

    Checks ``properties.op.enum`` has at least one value.
    Raises ``ImportError`` if the schema is missing or invalid.
    """
    in_schema = None
    if hasattr(plugin, "get_schema"):
        in_schema = plugin.get_schema()
    props = (in_schema or {}).get("properties") if isinstance(in_schema, dict) else None
    op_def = (props or {}).get("op") if isinstance(props, dict) else None
    enum_vals = (op_def or {}).get("enum") if isinstance(op_def, dict) else None
    if not (isinstance(enum_vals, (list, tuple)) and len(enum_vals) >= 1):
        raise ImportError(f"Plugin '{plugin.name}' missing op enum in input schema")


@dataclass
class ResolvedOp:
    """A resolved operation with its schema, title, and description."""

    op: str
    schema: dict[str, Any] | None
    title: str | None
    description: str | None


def resolve_all_ops(plugin: Plugin, ops: list[str]) -> dict[str, ResolvedOp]:
    """Resolve schemas for all declared ops on a plugin.

    Returns a dict keyed by op name with resolved schema, title, and description.
    Deduplicates ops while preserving order.
    """
    result: dict[str, ResolvedOp] = {}
    for op in dict.fromkeys(ops):
        schema = resolve_op_schema(plugin, op)
        result[op] = ResolvedOp(
            op=op,
            schema=schema,
            title=extract_op_title(schema, op),
            description=(schema or {}).get("description"),
        )
    return result


def extract_op_title(schema: dict[str, Any] | None, op: str) -> str | None:
    """Extract a display title for *op* from a schema dict.

    Checks the standard JSON Schema ``title`` field first (per-op schemas),
    then falls back to ``properties.op.x-ui.enum_labels`` (combined schemas
    from the deprecated ``get_schema()`` path).
    """
    if not schema:
        return None
    title = schema.get("title")
    if title:
        return title
    try:
        return ((schema.get("properties") or {}).get("op") or {}).get("x-ui", {}).get("enum_labels", {}).get(str(op))
    except Exception:
        return None
