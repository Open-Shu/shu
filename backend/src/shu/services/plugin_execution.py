from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shu.models.plugin_execution import CallableTool
from shu.plugins.base import Plugin
from shu.plugins.loader import PluginRecord
from shu.auth.models import User
from shu.models.provider_identity import ProviderIdentity
from shu.plugins.executor import EXECUTOR
from shu.services.plugin_identity import PluginIdentityError, ensure_user_identity_for_plugin
from shu.core.logging import get_logger
from shu.models.plugin_registry import PluginDefinition
from shu.plugins.registry import REGISTRY

logger = get_logger(__name__)


async def build_agent_tools(db_session: AsyncSession) -> List[CallableTool]:
    tools: List[CallableTool] = []
    
    manifest: Dict[str, PluginRecord] = {}
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

        chat_ops: List[str] = []
        try:
            chat_ops = list(getattr(rec, "chat_callable_ops", []) or [])
        except Exception:
            pass
        if not chat_ops:
            continue

        plugin: Optional[Plugin] = None
        try:
            plugin = await REGISTRY.resolve(name, db_session)
        except Exception:
            pass
        if not plugin:
            continue

        schema: Optional[Dict[str, Any]] = None
        try:
            schema = plugin.get_schema()
        except Exception:
            pass

        enum_labels: Optional[Dict[str, Any]] = None
        try:
            enum_labels = (
                (((schema or {}).get("properties") or {}).get("op") or {}).get("x-ui", {})
            ).get("enum_labels")
        except Exception:
            pass

        for op in chat_ops:
            tools.append(
                CallableTool(
                    name=name,
                    op=op,
                    plugin=plugin,
                    schema=schema,
                    enum_labels=enum_labels,
                )
            )

    return tools


def _coerce_params(plugin: Plugin, params: Dict[str, Any]) -> Dict[str, Any]:
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
                prop_type = prop_def.get("type")
                
                if prop_type == "integer":
                    # Handle pure digits
                    if value.lstrip('-').isdigit():
                        coerced[key] = int(value)
                elif prop_type == "number":
                    # Handle float format
                    try:
                        coerced[key] = float(value)
                    except ValueError:
                        pass
                elif prop_type == "boolean":
                    # Handle string booleans
                    if value.lower() == "true":
                        coerced[key] = True
                    elif value.lower() == "false":
                        coerced[key] = False

        return coerced
    except Exception as e:
        logger.warning(f"Parameter coercion failed: {e}")
        return params


async def execute_plugin(db_session: AsyncSession, plugin_name: str, operation: str, args_dict: Dict[str, Any], conversation_owner_id: str) -> Dict[str, Any]:
    """
        Execute a chat-callable plugin op using the internal executor and return a serializable dict.
    """
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

    r = await db_session.execute(
        select(PluginDefinition).where(PluginDefinition.name == plugin_name)
    )
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

    providers_map: Dict[str, List[Dict[str, Any]]] = {}
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
    try:
        return result.model_dump(mode='json')
    except Exception:
        return {
            "status": getattr(result, "status", None),
            "data": getattr(result, "data", None),
            "error": getattr(result, "error", None),
        }
