"""
Orchestrator v0 for Morning Briefing: sequential plugin runs then a single LLM synthesis.
"""
from __future__ import annotations
from typing import Dict, Any, List, Optional
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from shu.services.chat_types import ChatContext

from shu.services.message_context_builder import MessageContextBuilder

from .config import get_agent_config
from .workflow.runner import SequentialRunner, Step
from .plugins.registry import registry as plugin_registry
from .plugins.kb_insights import KBInsightsPlugin
from .plugins.base import PluginResult, PluginInput

from ..llm.service import LLMService
from ..llm.client import UnifiedLLMClient
from ..core.config import get_settings_instance, ConfigurationManager
from ..core.logging import get_logger
from ..services.chat_service import ChatService
from ..schemas.query import RagRewriteMode
from ..services.model_configuration_service import ModelConfigurationService
from ..models import Conversation
from ..models.plugin_registry import PluginDefinition
from ..plugins.registry import REGISTRY
from ..plugins.executor import EXECUTOR
from ..services.plugin_identity import (
    ensure_user_identity_for_plugin,
    get_provider_identities_map,
    resolve_user_email_for_execution,
    PluginIdentityError,
)

logger = get_logger(__name__)


class MorningBriefingOrchestrator:
    def __init__(self, db, config_manager: ConfigurationManager):
        self.db = db
        self.config_manager = config_manager
        self.settings = get_settings_instance()
        self.message_context_builder = MessageContextBuilder.init(db, config_manager, self)

    def _ensure_plugins_registered(self):
        # Idempotent registration
        if not plugin_registry.get_registered("kb_insights"):
            plugin_registry.register(KBInsightsPlugin(self.db))

    async def run(self, *, user_id: str, model_configuration_id: str, params: Dict[str, Any], current_user) -> Dict[str, Any]:
        agent_key = "morning_briefing"
        cfg = get_agent_config(agent_key)
        self._ensure_plugins_registered()

        # Build steps strictly from config allowlist (plugins) then a single llm synth
        steps: List[Step] = []
        for t in cfg.allowed_plugins:
            steps.append(Step(kind="plugin", name=t.name, params=params.get(t.name, {})))
        steps.append(Step(kind="llm", name="synthesize_briefing", params={}))

        runner = SequentialRunner(max_total_seconds=cfg.max_total_seconds, max_plugin_calls=cfg.max_plugin_calls)

        async def _runner_plugin_adapter(plugin_name: str, plugin_params: Dict[str, Any]) -> Optional[PluginResult]:
            return await self._execute_plugin_step(
                plugin_name=plugin_name,
                params=plugin_params,
                user_id=user_id,
                agent_key=agent_key,
            )

        # Let plugins populate system messages with their summaries
        ctx = {
            "db": self.db,
            "messages": [],
            "allowlist": [t.name for t in cfg.allowed_plugins],
            "plugin_runner": _runner_plugin_adapter,
        }
        run_result = await runner.run(user_id=user_id, agent_key=agent_key, steps=steps, ctx=ctx)

        # Load model configuration (required) with relationships
        mc_service = ModelConfigurationService(self.db)
        model_config = await mc_service.get_model_configuration(model_configuration_id, include_relationships=True, current_user=current_user)
        if not model_config:
            return {
                "agent_key": agent_key,
                "artifacts": run_result.get("artifacts", {}),
                "briefing": "Model configuration is missing or access denied."
            }

        # Build base LLM context using ChatService (prompts + RAG from attached KBs)
        chat_service = ChatService(self.db, self.config_manager)
        fake_conversation = Conversation(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title="Morning Briefing",
            model_configuration_id=model_config.id,
        )
        # Attach relationship for access in _build_message_context without extra DB roundtrip
        fake_conversation.model_configuration = model_config

        base_messages, _source_meta = await self.message_context_builder.build_message_context(
            conversation=fake_conversation,
            user_message="Generate my morning briefing based on current context and recent activity.",
            current_user=current_user,
            model=None,  # TODO: Fix this, we probably missed adding the model, but morning briefings needs an overhaul anyway.
            knowledge_base_id=None,
            rag_rewrite_mode=RagRewriteMode.RAW_QUERY,
            attachment_ids=None,
            conversation_messages=[]
        )

        # Compose final messages by appending plugin summaries and a synthesis instruction
        llm_messages = self._compose_prompt_with_base(base_messages, run_result)

        # Call LLM using the provider/model from the model configuration; fall back server-side if needed
        llm_output = await self._call_llm_with_model_config(llm_messages, model_config)

        return {
            "agent_key": agent_key,
            "artifacts": run_result.get("artifacts", {}),
            "briefing": llm_output,
        }

    async def _execute_plugin_step(
        self,
        *,
        plugin_name: str,
        params: Dict[str, Any],
        user_id: str,
        agent_key: str,
    ) -> Optional[PluginResult]:
        if plugin_name == "kb_insights":
            tool = plugin_registry.get_registered("kb_insights")
            if not tool:
                # Should not happen because we register in __init__, but guard anyway
                tool = KBInsightsPlugin(self.db)
                plugin_registry.register(tool)
            return await tool.execute(user_id=user_id, agent_key=agent_key, payload=PluginInput(params=params))

        return await self._run_manifest_plugin(
            plugin_name=plugin_name,
            params=params,
            user_id=user_id,
            agent_key=agent_key,
        )

    async def _run_manifest_plugin(
        self,
        *,
        plugin_name: str,
        params: Dict[str, Any],
        user_id: str,
        agent_key: str,
    ) -> PluginResult:
        plugin = await REGISTRY.resolve(plugin_name, self.db)
        if not plugin:
            return self._plugin_error_result(plugin_name, "plugin not enabled or not found")

        res = await self.db.execute(select(PluginDefinition).where(PluginDefinition.name == plugin_name))
        definition = res.scalars().first()
        if not definition or not definition.enabled:
            return self._plugin_error_result(plugin_name, "plugin not enabled")
        limits = getattr(definition, "limits", {}) or {}

        # Provide reasonable defaults for read-only ops
        payload = dict(params or {})
        payload.setdefault("op", "list")
        if plugin_name == "gmail_digest":
            payload.setdefault("since_hours", 72)
            payload.setdefault("max_results", 50)
        elif plugin_name == "calendar_events":
            payload.setdefault("since_hours", 48)
            payload.setdefault("max_results", 50)
        elif plugin_name == "gchat_digest":
            payload.setdefault("since_hours", 168)
            payload.setdefault("max_spaces", 20)
            payload.setdefault("max_messages_per_space", 100)

        providers_map = await get_provider_identities_map(self.db, user_id)
        user_email = await resolve_user_email_for_execution(self.db, user_id, payload)

        try:
            await ensure_user_identity_for_plugin(self.db, plugin, plugin_name, user_id, payload)
        except PluginIdentityError as pie:
            return self._plugin_error_result(plugin_name, str(pie), error=str(pie))

        result = await EXECUTOR.execute(
            plugin=plugin,
            user_id=str(user_id),
            user_email=user_email,
            agent_key=agent_key,
            params=payload,
            limits=limits,
            provider_identities=providers_map,
        )

        ok = getattr(result, "status", None) == "success"
        data = getattr(result, "data", None) or {}
        normalized_data = self._normalize_plugin_data(plugin_name, data)
        summary = self._build_plugin_summary(plugin_name, normalized_data, result)
        error_payload = getattr(result, "error", None) or {}
        error_msg = None if ok else (error_payload.get("message") or str(error_payload) or "plugin error")

        return PluginResult(
            ok=ok,
            name=plugin_name,
            summary=summary,
            data=normalized_data,
            error=error_msg,
        )

    def _plugin_error_result(self, plugin_name: str, message: str, *, error: Optional[str] = None) -> PluginResult:
        return PluginResult(
            ok=False,
            name=plugin_name,
            summary=message,
            error=error or message,
            data=None,
        )

    def _normalize_plugin_data(self, plugin_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        if plugin_name == "gmail_digest":
            return self._normalize_gmail_data(data)
        if plugin_name == "calendar_events":
            return self._normalize_calendar_data(data)
        if plugin_name == "gchat_digest":
            return self._normalize_gchat_data(data)
        return data or {}

    def _normalize_gmail_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        messages = data.get("messages") or []

        def _header(msg: Dict[str, Any], key: str) -> Optional[str]:
            for h in msg.get("headers", []) or []:
                if (h.get("name") or "").lower() == key.lower():
                    return h.get("value")
            return None

        def _to_iso(val: Optional[str]) -> Optional[str]:
            try:
                if val is None:
                    return None
                return datetime.fromtimestamp(int(val) / 1000.0, tz=timezone.utc).isoformat()
            except Exception:
                return val

        normalized = []
        for msg in messages:
            normalized.append(
                {
                    "id": msg.get("id"),
                    "thread_id": msg.get("thread_id") or msg.get("threadId"),
                    "subject": msg.get("subject") or _header(msg, "Subject"),
                    "sender": msg.get("sender") or _header(msg, "From"),
                    "date": msg.get("date") or _to_iso(msg.get("internalDate")),
                    "snippet": msg.get("snippet"),
                    "body_text": msg.get("body_text") or msg.get("snippet"),
                }
            )
        return {"messages": normalized, "count": len(normalized)}

    def _normalize_calendar_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        events = data.get("events") or []

        def _resolve_time(entry: Optional[Dict[str, Any]]) -> Optional[str]:
            if not isinstance(entry, dict):
                return None
            return entry.get("dateTime") or entry.get("date")

        normalized = []
        for ev in events:
            normalized.append(
                {
                    "id": ev.get("id"),
                    "title": ev.get("summary") or "(no title)",
                    "start": _resolve_time(ev.get("start")),
                    "end": _resolve_time(ev.get("end")),
                    "attendees": [a.get("email") for a in (ev.get("attendees") or []) if isinstance(a, dict)],
                    "location": ev.get("location"),
                }
            )
        return {"events": normalized, "count": len(normalized)}

    def _normalize_gchat_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        messages = data.get("messages") or []
        normalized = []
        for msg in messages:
            sender = msg.get("sender") or {}
            normalized.append(
                {
                    "id": msg.get("name"),
                    "text": msg.get("text"),
                    "date": msg.get("createTime"),
                    "space_name": msg.get("space_display_name") or msg.get("space"),
                    "sender_name": sender.get("displayName") or sender.get("email") or sender.get("name"),
                    "sender_email": sender.get("email"),
                }
            )
        return {"messages": normalized, "count": len(normalized), "last_ts": data.get("last_ts")}

    def _build_plugin_summary(self, plugin_name: str, normalized_data: Dict[str, Any], raw_result: Any) -> str:
        status = getattr(raw_result, "status", None)
        if status != "success":
            err = getattr(raw_result, "error", {}) or {}
            if isinstance(err, dict):
                return err.get("message") or f"{plugin_name} failed"
            return str(err) or f"{plugin_name} failed"

        if plugin_name == "gmail_digest":
            count = len(normalized_data.get("messages") or [])
            return f"Collected {count} Gmail messages"
        if plugin_name == "calendar_events":
            count = len(normalized_data.get("events") or [])
            return f"Retrieved {count} calendar events"
        if plugin_name == "gchat_digest":
            count = len(normalized_data.get("messages") or [])
            return f"Collected {count} Google Chat messages"
        return f"{plugin_name} completed"

    def _compose_prompt_with_base(self, base_messages: List[Dict[str, str]], run_result: Dict[str, Any]) -> List[Dict[str, str]]:
        messages = list(base_messages or [])
        artifacts = run_result.get("artifacts", {})
        runner_messages = run_result.get("messages", [])

        # Include plugin summaries that the runner already formatted
        messages.extend(runner_messages)

        # Extract structured details for LLM (subjects/senders, event titles/times, KB titles)
        def to_dict(obj):
            try:
                if hasattr(obj, "model_dump"):
                    return obj.model_dump()
                if hasattr(obj, "dict"):
                    return obj.dict()
            except Exception:
                pass
            return obj if isinstance(obj, dict) else None

        def truncate(s: str, n: int) -> str:
            return (s[: n - 1] + "…") if isinstance(s, str) and len(s) > n else (s or "")

        gmail = to_dict(artifacts.get("gmail_digest")) or {}
        cal = to_dict(artifacts.get("calendar_events")) or {}
        kb = to_dict(artifacts.get("kb_insights")) or {}
        gchat = to_dict(artifacts.get("gchat_digest")) or {}

        # Gmail details
        gmail_msgs = []
        try:
            items = (gmail.get("data") or {}).get("messages") or []
            for m in items[:10]:
                subj = truncate(m.get("subject") or "(no subject)", 140)
                sndr = truncate(m.get("sender") or "(unknown sender)", 100)
                date = (m.get("date") or "")[:10]
                gmail_msgs.append(f"- {date} | {subj} — from {sndr}")
        except Exception:
            pass

        # Calendar details
        cal_lines = []
        try:
            events = (cal.get("data") or {}).get("events") or []
            for e in events[:10]:
                start = e.get("start") or ""
                title = truncate(e.get("title") or "(no title)", 120)
                cal_lines.append(f"- {start} | {title}")
        except Exception:
            pass

        # KB details
        kb_lines = []
        try:
            resources = (kb.get("data") or {}).get("resources") or []
            for r in resources[:10]:
                title = truncate(r.get("title") or "(untitled)", 120)
                kb_lines.append(f"- {title}")
        except Exception:
            pass

        # Google Chat details
        gchat_lines = []
        try:
            chats = (gchat.get("data") or {}).get("messages") or []
            for c in chats[:10]:
                date = (c.get("date") or "")[:10]
                space = truncate(c.get("space_name") or "(unknown space)", 80)
                sender = truncate(c.get("sender_name") or c.get("sender_email") or "(unknown)", 80)
                text = truncate(c.get("text") or "", 140)
                gchat_lines.append(f"- {date} | {space} — {sender}: {text}")
        except Exception:
            pass

        # Build a context block with details and full email contents
        detail_blocks: List[str] = []
        if gmail_msgs:
            detail_blocks.append("Gmail (recent):\n" + "\n".join(gmail_msgs))
        if cal_lines:
            detail_blocks.append("Calendar (upcoming):\n" + "\n".join(cal_lines))
        if kb_lines:
            detail_blocks.append("Knowledge Base (recent updates):\n" + "\n".join(kb_lines))
        if gchat_lines:
            detail_blocks.append("Google Chat (recent):\n" + "\n".join(gchat_lines))

        # Include full email contents for the digest window
        gmail_full_blocks: List[str] = []
        try:
            items = (gmail.get("data") or {}).get("messages") or []
            for m in items:
                subj = truncate(m.get("subject") or "(no subject)", 5000)  # no practical cap
                sndr = truncate(m.get("sender") or "(unknown sender)", 200)
                date = m.get("date") or ""
                body = m.get("body_text") or m.get("snippet") or ""
                gmail_full_blocks.append(f"Subject: {subj}\nFrom: {sndr}\nDate: {date}\n\n{body}")
        except Exception:
            pass
        if gmail_full_blocks:
            detail_blocks.append("Gmail (full messages):\n\n" + "\n\n---\n\n".join(gmail_full_blocks))

        # Append a user message asking to synthesize, with summaries and details
        parts: List[str] = []
        for name, res in artifacts.items():
            d = to_dict(res)
            if isinstance(d, dict) and d.get("ok"):
                parts.append(f"- {name}: {d.get('summary')}")
        if not parts:
            parts.append("No plugin data available.")

        synthesis_request = (
            "Synthesize the morning briefing. Use full email contents below (subjects, senders, bodies), "
            "calendar items (titles/times), and recent knowledge-base changes. "
            "Use clear section headers and keep it concise where possible. "
            "Also identify and flag likely spam/bulk/low-priority emails under a 'Likely Spam' section with brief reasons "
            "(e.g., marketing keywords, noreply sender, unsubscribe footer, mailing-list/bulk labels)."
        )
        content = synthesis_request + "\n\n" + "\n".join(parts)
        if detail_blocks:
            content += "\n\nContext Details:\n" + "\n\n".join(detail_blocks)
        messages.append({"role": "user", "content": content})
        return messages

    async def _call_llm_with_model_config(self, messages: List[Dict[str, str]], model_config) -> str:
        """
        Use provider/model from Model Configuration; if that fails, apply server-side fallback.
        Now uses server-side streaming to reduce header-wait time and avoid 30s read timeouts.
        """
        llm_service = LLMService(self.db)

        client: Optional[UnifiedLLMClient] = None
        resolved_model_name: Optional[str] = None

        # Preferred: model configuration provider/model
        try:
            if getattr(model_config, "llm_provider_id", None):
                client = await llm_service.get_client(model_config.llm_provider_id)
                resolved_model_name = model_config.model_name
        except Exception as e:
            logger.warning("Failed to init client from model configuration; will try fallback", extra={"error": str(e)})

        if client is None or not resolved_model_name:
            return "Morning Briefing requires a configured LLM provider and model."

        # Log prompt diagnostics (chars and message count) before the call
        try:
            total_chars = sum(len(m.get("content", "")) for m in messages if isinstance(m, dict))
            logger.info("Morning Briefing LLM prompt diagnostics", extra={
                "message_count": len(messages),
                "char_count": total_chars,
            })
        except Exception:
            pass

        try:
            logger.info("Morning Briefing LLM call", extra={"model": resolved_model_name, "via_model_config": True, "stream": True})
            # Stream from provider, but buffer to a final string for current API shape
            stream_gen = await client.chat_completion(messages=ChatContext.from_dicts(messages), model=resolved_model_name, stream=True)
            final_content: Optional[str] = None
            async for event in stream_gen:
                if event.type == "final_message":
                    final_content = event.content
            # TODO: Stream Morning Briefing output live once UI supports it; for now we only consume the final event.
            return final_content or ""
        except Exception as e:
            try:
                err_type = e.__class__.__name__
                err_str = str(e) or repr(e)
            except Exception:
                err_type, err_str = "UnknownError", "no message"
            logger.error("LLM synthesis failed", extra={"error": err_str, "error_type": err_type, "model": resolved_model_name})
            return "Morning Briefing is unavailable due to LLM error."
        finally:
            try:
                await client.close()
            except Exception:
                pass
