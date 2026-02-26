from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shu.auth.models import User
from shu.core.logging import get_logger
from shu.models.plugin_execution import CallableTool
from shu.models.plugin_registry import PluginDefinition
from shu.models.provider_identity import ProviderIdentity
from shu.plugins.base import Plugin
from shu.plugins.executor import EXECUTOR
from shu.plugins.loader import PluginRecord
from shu.plugins.registry import REGISTRY
from shu.services.plugin_identity import PluginIdentityError, ensure_user_identity_for_plugin

logger = get_logger(__name__)


async def build_agent_tools(db_session: AsyncSession) -> list[CallableTool]:
    tools: list[CallableTool] = []

    manifest: dict[str, PluginRecord] = {}
    try:
        manifest = getattr(REGISTRY, "_manifest", {}) or {}
        if not manifest:
            REGISTRY.refresh()
            manifest = getattr(REGISTRY, "_manifest", {}) or {}
    except Exception:
        pass

    res = await db_session.execute(
        select(PluginDefinition).where(PluginDefinition.enabled == True)  # noqa: E712
    )
    enabled = {r.name for r in res.scalars().all()}

    for name, rec in (manifest or {}).items():
        if name not in enabled:
            continue

        chat_ops: list[str] = []
        try:
            chat_ops = list(getattr(rec, "chat_callable_ops", []) or [])
        except Exception:
            pass
        if not chat_ops:
            continue

        plugin: Plugin | None = None
        try:
            plugin = await REGISTRY.resolve(name, db_session)
        except Exception:
            pass
        if not plugin:
            continue

        schema: dict[str, Any] | None = None
        try:
            schema = plugin.get_schema()
        except Exception:
            pass

        enum_labels: dict[str, Any] | None = None
        try:
            enum_labels = ((((schema or {}).get("properties") or {}).get("op") or {}).get("x-ui", {})).get(
                "enum_labels"
            )
        except Exception:
            pass

        for op in chat_ops:
            # Use a per-op schema when the plugin provides get_schema_for_op().
            # Falls back to the shared schema for plugins that don't implement it.
            op_schema = schema
            try:
                get_op_schema = getattr(plugin, "get_schema_for_op", None)
                if callable(get_op_schema):
                    per_op = get_op_schema(op)
                    if per_op is not None:
                        op_schema = per_op
            except Exception:
                pass

            tools.append(
                CallableTool(
                    name=name,
                    op=op,
                    plugin=plugin,
                    schema=op_schema,
                    enum_labels=enum_labels,
                )
            )

    return tools


def _coerce_params(plugin: Plugin, params: dict[str, Any]) -> dict[str, Any]:
    """Coerce parameter types based on plugin schema."""
    try:
        schema = plugin.get_schema()
        if not schema:
            return params

        # Assume standard JSON schema structure with "properties"
        props = schema.get("properties", {})

        coerced = params.copy()
        for key, value in params.items():
            if key in props and isinstance(value, str):
                prop_def = props[key]
                raw_type = prop_def.get("type")
                # Normalize to a list to handle both "integer" and ["integer", "null"]
                prop_types = raw_type if isinstance(raw_type, list) else [raw_type]

                if "integer" in prop_types:
                    # Handle pure digits
                    if value.lstrip("-").isdigit():
                        coerced[key] = int(value)
                elif "number" in prop_types:
                    # Handle float format
                    try:
                        coerced[key] = float(value)
                    except ValueError:
                        pass
                elif "boolean" in prop_types:
                    # Handle string booleans
                    if value.lower() == "true":
                        coerced[key] = True
                    elif value.lower() == "false":
                        coerced[key] = False

        return coerced
    except Exception as e:
        logger.warning(f"Parameter coercion failed: {e}")
        return params


def _handle_plugin_execution_result_types(plugin_name: str, result: Any):
    # Handle different result types
    # Case 1: Already a dict (some plugins return dicts directly)
    if isinstance(result, dict):
        return result

    # Case 2: PluginResult/ToolResult object with model_dump method
    if hasattr(result, "model_dump"):
        try:
            return result.model_dump()
        except Exception as e:
            logger.warning(
                "chat.tools.execution.model_dump_failed plugin=%s error=%s, trying mode=python", plugin_name, str(e)
            )
            try:
                return result.model_dump(mode="python")
            except Exception as e2:
                logger.warning(
                    "chat.tools.execution.model_dump_python_failed plugin=%s error=%s, using manual fallback",
                    plugin_name,
                    str(e2),
                )

    # Case 3: Object without model_dump - manually extract attributes
    logger.warning(
        "chat.tools.execution.manual_extraction plugin=%s result_type=%s", plugin_name, type(result).__name__
    )
    return {
        "status": result.status if hasattr(result, "status") else None,
        "data": result.data if hasattr(result, "data") else None,
        "error": result.error if hasattr(result, "error") else None,
        "warnings": result.warnings if hasattr(result, "warnings") else None,
        "citations": result.citations if hasattr(result, "citations") else None,
        "diagnostics": result.diagnostics if hasattr(result, "diagnostics") else None,
    }


async def execute_plugin(
    db_session: AsyncSession,
    plugin_name: str,
    operation: str,
    args_dict: dict[str, Any],
    conversation_owner_id: str,
) -> dict[str, Any]:
    """Execute a chat-callable plugin op using the internal executor and return a serializable dict."""
    try:
        manifest = getattr(REGISTRY, "_manifest", {}) or {}
        if not manifest:
            REGISTRY.refresh()
            manifest = getattr(REGISTRY, "_manifest", {}) or {}
    except Exception:
        manifest = {}
    rec = manifest.get(plugin_name)
    if not rec:
        logger.warning("chat.tools.execution.plugin_missing plugin=%s", plugin_name)
        return {"status": "error", "error": {"message": f"plugin '{plugin_name}' not found"}}

    r = await db_session.execute(select(PluginDefinition).where(PluginDefinition.name == plugin_name))
    row = r.scalars().first()
    if not row or not row.enabled:
        logger.warning("chat.tools.execution.plugin_disabled plugin=%s", plugin_name)
        return {"status": "error", "error": {"message": f"plugin '{plugin_name}' not enabled"}}

    chat_ops = list(getattr(rec, "chat_callable_ops", []) or [])
    if operation not in chat_ops:
        logger.warning("chat.tools.execution.op_not_allowed plugin=%s op=%s", plugin_name, operation)
        return {"status": "error", "error": {"message": f"op '{operation}' not chat-callable"}}

    plugin = await REGISTRY.resolve(plugin_name, db_session)
    if not plugin:
        logger.warning("chat.tools.execution.plugin_not_loaded plugin=%s", plugin_name)
        return {"status": "error", "error": {"message": f"plugin '{plugin_name}' not loaded"}}

    limits = getattr(row, "limits", {}) or {}

    providers_map: dict[str, list[dict[str, Any]]] = {}
    try:
        q_pi = select(ProviderIdentity).where(ProviderIdentity.user_id == str(conversation_owner_id))
        pi_res = await db_session.execute(q_pi)
        for pi in pi_res.scalars().all():
            providers_map.setdefault(pi.provider_key, []).append(pi.to_dict())
    except Exception:
        providers_map = {}

    user_email_val = None
    try:
        r = await db_session.execute(select(User).where(User.id == str(conversation_owner_id)))
        p = r.scalars().first()
        if p and getattr(p, "email", None):
            user_email_val = p.email
    except Exception:
        user_email_val = None

    params = dict(args_dict or {})
    params = _coerce_params(plugin, params)
    params["op"] = operation

    try:
        await ensure_user_identity_for_plugin(
            db_session,
            plugin,
            plugin_name,
            str(conversation_owner_id),
            params,
        )
    except PluginIdentityError as pie:
        logger.warning(
            "chat.tools.identity_block user_id=%s plugin=%s code=%s details=%s",
            conversation_owner_id,
            plugin_name,
            pie.code,
            pie.details,
        )
        return {
            "status": "error",
            "error": {
                "message": str(pie),
                "code": pie.code,
                "details": pie.details or {},
            },
        }

    result = await EXECUTOR.execute(
        plugin=plugin,
        user_id=str(conversation_owner_id),
        user_email=user_email_val,
        agent_key=None,
        params=params,
        limits=limits,
        provider_identities=providers_map,
    )

    return _handle_plugin_execution_result_types(plugin_name, result)
