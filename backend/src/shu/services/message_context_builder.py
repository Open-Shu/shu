import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from shu.llm.service import LLMService
from shu.services.prompt_service import PromptService
from shu.services.query_service import QueryService

from ..auth.models import User
from ..auth.rbac import rbac
from ..core.config import ConfigurationManager
from ..models.llm_provider import Conversation, LLMModel, Message
from .chat_types import ChatContext, ChatMessage
from ..models.model_configuration import ModelConfiguration
from ..models.prompt import EntityType
from ..schemas.query import QueryRequest, RagRewriteMode
from .context_preferences_resolver import ContextPreferencesResolver
from .context_window_manager import ContextWindowManager
from .message_utils import collapse_assistant_variants
from .rag_query_processing import execute_rag_queries
from .providers.adapter_base import get_adapter_from_provider
from .side_call_service import SideCallService

logger = logging.getLogger(__name__)


class MessageContextBuilder:
    """Helper responsible for constructing the LLM message context payload."""

    def __init__(
        self,
        *,
        db_session: AsyncSession,
        config_manager: ConfigurationManager,
        llm_service: LLMService,
        prompt_service: PromptService,
        query_service: QueryService,
        context_window_manager: ContextWindowManager,
        context_preferences_resolver: ContextPreferencesResolver,
        conversation_message_fetcher: Callable[[str, int], Any],
        diagnostics_target: Any,
    ) -> None:
        self.db_session = db_session
        self.config_manager = config_manager
        self.llm_service = llm_service
        self.prompt_service = prompt_service
        self.query_service = query_service
        self.context_window_manager = context_window_manager
        self.context_preferences_resolver = context_preferences_resolver
        self.fetch_conversation_messages = conversation_message_fetcher
        self.diagnostics_target = diagnostics_target

    @classmethod
    def init(cls, db_session: AsyncSession, config_manager: ConfigurationManager, diagnostics_target: Any):
        llm_service = LLMService(db_session)
        side_call_service = SideCallService(db_session, config_manager)
        return MessageContextBuilder(
            db_session=db_session,
            config_manager=config_manager,
            llm_service=llm_service,
            prompt_service=PromptService(db_session),
            query_service=QueryService(db_session, config_manager),
            context_window_manager=ContextWindowManager(
                llm_service=llm_service,
                db_session=db_session,
                config_manager=config_manager,
                side_call_service=side_call_service,
            ),
            context_preferences_resolver=ContextPreferencesResolver(
                db_session=db_session,
                config_manager=config_manager,
            ),
            conversation_message_fetcher=None,
            diagnostics_target=diagnostics_target,
        )

    async def build_message_context(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        current_user: User,
        model: LLMModel,
        knowledge_base_id: Optional[str] = None,
        rag_rewrite_mode: RagRewriteMode = RagRewriteMode.RAW_QUERY,
        conversation_messages: Optional[List[Message]] = None,
        model_configuration_override: Optional[ModelConfiguration] = None,
        recent_messages_limit: Optional[int] = None,
    ) -> Tuple[ChatContext, List[Dict]]:
        """Build message context using model configuration with conditional RAG + attachments."""
        system_sections: List[str] = []
        active_model_config = model_configuration_override or getattr(conversation, "model_configuration", None)

        base_prompt = await self._get_base_system_prompt(conversation, active_model_config)
        if base_prompt:
            system_sections.append(base_prompt)

        mb_section = await self._get_morning_briefing_section(conversation)
        if mb_section:
            system_sections.append(mb_section)

        if conversation_messages is not None:
            recent_messages_raw = list(conversation_messages)
        else:
            recent_messages_raw = await self.fetch_conversation_messages(
                conversation_id=conversation.id,
                limit=50,
            )
        recent_messages = collapse_assistant_variants(recent_messages_raw)

        # Check if vision is enabled (Adapter Capability AND Model Config Override)
        vision_enabled = await self._is_vision_enabled(model, active_model_config)

        recent_chat_messages = await self._hydrate_chat_messages(
            conversation, recent_messages, vision_enabled=vision_enabled
        )

        rag_sections, all_source_metadata = await self._get_rag_sections(
            conversation=conversation,
            user_message=user_message,
            current_user=current_user,
            knowledge_base_id=knowledge_base_id,
            rag_rewrite_mode=rag_rewrite_mode,
            model=model,
            conversation_messages=recent_messages,
            model_configuration_override=active_model_config,
            recent_messages_limit=recent_messages_limit,
        )
        system_sections.extend(rag_sections)

        combined_system = "\n\n".join([s for s in system_sections if s and s.strip()])

        chat_messages: List[ChatMessage] = []
        for msg in recent_chat_messages:
            if msg.role in ["user", "assistant"]:
                chat_messages.append(msg)

        effective_max_tokens = self.context_preferences_resolver.resolve_max_context_tokens(
            active_model_config=active_model_config,
        )

        chat_messages = await self.context_window_manager.manage_context_window(
            chat_messages,
            conversation=conversation,
            max_tokens=effective_max_tokens,
            recent_message_limit_override=recent_messages_limit,
        )

        return ChatContext(system_prompt=combined_system, messages=chat_messages), all_source_metadata

    async def _hydrate_chat_messages(
        self,
        conversation: Conversation,
        messages: List[Message],
        vision_enabled: bool = True,
    ) -> List[ChatMessage]:
        """Load conversation attachments and return ChatMessage objects with attachments.
        
        Args:
            conversation: The conversation to load attachments for
            messages: List of messages to hydrate
            vision_enabled: If False, image attachments are filtered out
        """
        if not messages:
            return []
        try:
            from .attachment_service import AttachmentService

            att_service = AttachmentService(self.db_session)
            rows = await att_service.get_conversation_attachments_with_links(conversation.id, conversation.user_id)
            if not rows:
                return [ChatMessage.from_message(m, []) for m in messages]

            msg_by_id = {m.id: m for m in messages if getattr(m, "id", None)}
            msg_to_atts: Dict[str, List[Any]] = {}
            for msg_id, att in rows:
                if msg_id:
                    # Filter out image attachments if vision is disabled
                    if not vision_enabled and att.mime_type and att.mime_type.startswith("image/"):
                        continue
                    msg_to_atts.setdefault(msg_id, []).append(att)

            chat_messages: List[ChatMessage] = []
            for m in messages:
                atts = msg_to_atts.get(getattr(m, "id", ""), [])
                chat_messages.append(ChatMessage.from_message(m, atts))
            return chat_messages
        except Exception as e:
            logger.warning(f"Failed to attach conversation attachments to messages: {e}")
            return [ChatMessage.from_message(m, []) for m in messages]

    async def _get_base_system_prompt(
        self,
        conversation: Conversation,
        model_config: Optional[ModelConfiguration] = None,
    ) -> Optional[str]:
        if model_config is None and conversation.model_configuration_id:
            model_config = conversation.model_configuration
        if model_config and model_config.prompt:
            return model_config.prompt.content

        if model_config:
            try:
                model = await self.llm_service.get_model_by_name(
                    model_config.model_name,
                    provider_id=model_config.llm_provider_id,
                )
            except Exception:
                model = None

            if model:
                prompts = await self.prompt_service.get_entity_prompts(
                    entity_id=model.id,
                    entity_type=EntityType.LLM_MODEL
                )
                if prompts:
                    first_prompt = prompts[0]
                    return getattr(first_prompt, "content", None) or first_prompt.content

        return None

    async def _get_morning_briefing_section(self, conversation: Conversation) -> Optional[str]:
        try:
            mb_msg = None
            for m in reversed(getattr(conversation, 'messages', []) or []):
                meta = getattr(m, 'message_metadata', None) or {}
                if isinstance(meta, dict) and 'morning_briefing' in meta:
                    mb_msg = m
                    break
            if not mb_msg:
                return None

            settings = self.config_manager.settings
            per_section = getattr(settings, 'chat_attachment_max_chars_per_file', 5000)
            total_cap = getattr(settings, 'chat_attachment_max_total_chars', 15000)
            added = 0

            def clip(val: Any) -> str:
                nonlocal added
                try:
                    remaining = max(0, total_cap - added)
                    s = json.dumps(val, ensure_ascii=False) if not isinstance(val, str) else val
                    snippet = s[:min(remaining, per_section)]
                    added += len(snippet)
                    return snippet
                except Exception:
                    return ""

            mb = (mb_msg.message_metadata or {}).get('morning_briefing') or {}
            gmail_snip = clip(mb.get('gmail_digest', {}))
            cal_snip = clip(mb.get('calendar_events', {}))
            kb_snip = clip(mb.get('kb_insights', {}))
            gchat_snip = clip(mb.get('gchat_digest', {}))

            if not any([gmail_snip, cal_snip, kb_snip, gchat_snip]):
                return None

            return (
                "Morning Briefing Context:\n\n"
                + ("Gmail:\n" + gmail_snip + "\n\n" if gmail_snip else "")
                + ("Calendar:\n" + cal_snip + "\n\n" if cal_snip else "")
                + ("Knowledge Base:\n" + kb_snip + "\n\n" if kb_snip else "")
                + ("Google Chat:\n" + gchat_snip if gchat_snip else "")
            )
        except Exception as e:
            logger.warning(f"Failed to include morning briefing context: {e}")
            return None

    async def _get_rag_sections(
        self,
        conversation: Conversation,
        user_message: str,
        current_user: User,
        knowledge_base_id: Optional[str],
        rag_rewrite_mode: RagRewriteMode,
        model: LLMModel,
        conversation_messages: List[Message],
        model_configuration_override: Optional[ModelConfiguration] = None,
        recent_messages_limit: Optional[int] = None,
    ) -> Tuple[List[str], List[Dict]]:
        sections: List[str] = []
        all_source_metadata: List[Dict] = []

        should_use_rag = rag_rewrite_mode != RagRewriteMode.NO_RAG
        if not (should_use_rag and user_message):
            return sections, all_source_metadata

        kb_ids: List[str] = []
        kb_source_config: Optional[ModelConfiguration] = model_configuration_override or getattr(conversation, "model_configuration", None)
        if knowledge_base_id:
            kb_ids = [knowledge_base_id]
        else:
            if kb_source_config and getattr(kb_source_config, 'knowledge_bases', None):
                try:
                    accessible: List[str] = []
                    for kb in kb_source_config.knowledge_bases:
                        if not kb.is_active:
                            continue
                        has_access = await rbac.can_access_knowledge_base(current_user, kb.id, self.db_session)
                        if has_access:
                            accessible.append(kb.id)
                    kb_ids = accessible
                except Exception as e:
                    logger.warning(f"Error accessing model configuration knowledge bases: {e}")

        if not kb_ids:
            return sections, all_source_metadata

        def build_query_request(kb_id: str, rag_config: Dict[str, Any], query_text: str) -> QueryRequest:
            query_type = rag_config.get("search_type") or self.config_manager.get_rag_search_type()
            logger.info(
                "Chat RAG query",
                extra={
                    "kb_id": kb_id,
                    "query_type": query_type,
                    "rag_config_search_type": rag_config.get("search_type"),
                    "title_weighting": rag_config.get("title_weighting_enabled"),
                }
            )
            return QueryRequest(
                query=query_text,
                query_type=query_type,
                limit=rag_config.get("max_results") or self.config_manager.get_rag_max_results(),
                similarity_threshold=rag_config.get("search_threshold") or self.config_manager.get_rag_search_threshold(),
                include_metadata=True,
                rag_rewrite_mode=rag_rewrite_mode,
            )

        rewritten_query, rewrite_diagnostics, query_results = await execute_rag_queries(
            db_session=self.db_session,
            config_manager=self.config_manager,
            query_service=self.query_service,
            current_user=current_user,
            query_text=user_message,
            knowledge_base_ids=kb_ids,
            request_builder=build_query_request,
            prior_messages=conversation_messages,
            rag_rewrite_mode=rag_rewrite_mode,
        )

        if rewrite_diagnostics:
            self._append_rag_diagnostic("rag_query_processing", rewrite_diagnostics)

        for result in query_results:
            kb_id = result.get("knowledge_base_id")

            query_response_dict = result.get("response") or {}
            rag_config = result.get("rag_config") or {}

            results = query_response_dict.get("results", [])
            escalation = query_response_dict.get("escalation")

            if isinstance(escalation, dict) and escalation.get("enabled"):
                esc_copy = {
                    "enabled": bool(escalation.get("enabled")),
                    "knowledge_base_id": kb_id,
                    "docs": [
                        {
                            "document_id": d.get("document_id"),
                            "title": d.get("title"),
                            "token_cap_enforced": bool(d.get("token_cap_enforced")),
                        }
                        for d in (escalation.get("docs") or []) if isinstance(d, dict)
                    ],
                }
                self._append_rag_diagnostic("escalations", esc_copy)

            escalated_doc_ids: set[str] = set()
            try:
                escalated_metadata: List[Dict[str, Any]] = []
                if isinstance(escalation, dict) and escalation.get("enabled"):
                    parts: List[str] = []
                    for d in (escalation.get("docs") or []):
                        if not isinstance(d, dict):
                            continue
                        doc_id = d.get("document_id")
                        if doc_id:
                            escalated_doc_ids.add(doc_id)
                        title = d.get("title") or "Document"
                        snippet = d.get("content") or ""
                        parts.append(f"{title}\n{'-' * len(title)}\n{snippet}")
                        if doc_id:
                            metadata_entry = {
                                "source_index": len(all_source_metadata) + len(escalated_metadata) + 1,
                                "document_title": title,
                                "source_url": f"/documents/{kb_id}/{doc_id}" if kb_id else "",
                                "source_id": doc_id,
                                "document_id": doc_id,
                                "file_type": d.get("file_type", ""),
                                "similarity_score": 1.0,
                                "knowledge_base_id": kb_id,
                                "full_document": True,
                                "token_cap_enforced": bool(d.get("token_cap_enforced")),
                            }
                            escalated_metadata.append(metadata_entry)
                    if parts:
                        sections.append("Full Document Escalations:\n\n" + "\n\n".join(parts))
                        all_source_metadata.extend(escalated_metadata)
            except Exception as e:
                logger.warning(f"Failed to process escalations: {e}")

            filtered_results = []
            for r in results:
                try:
                    rid = r.get("document_id")
                except AttributeError:
                    rid = None
                if not rid or rid not in escalated_doc_ids:
                    filtered_results.append(r)

            if filtered_results:
                rag_content, source_metadata = await self._build_enhanced_rag_context(
                    results=filtered_results,
                    rag_config=rag_config,
                    knowledge_base_id=kb_id,
                )
                all_source_metadata.extend(source_metadata)

                kb_prompt_content = None
                if kb_source_config:
                    kb_prompt = kb_source_config.get_kb_prompt(kb_id)
                    if kb_prompt:
                        kb_prompt_content = kb_prompt.content
                if not kb_prompt_content:
                    kb_prompts = await self.prompt_service.get_entity_prompts(
                        entity_id=kb_id, entity_type=EntityType.KNOWLEDGE_BASE
                    )
                    if kb_prompts:
                        kb_prompt_content = kb_prompts[0].content

                if kb_prompt_content:
                    sections.append(f"{kb_prompt_content}\n\nContext:\n{rag_content}")
                else:
                    sections.append(f"Context:\n{rag_content}")

        return sections, all_source_metadata

    async def _is_vision_enabled(self, model: LLMModel, model_config: Optional[ModelConfiguration] = None) -> bool:
        """Check if vision is enabled for the model/configuration.
        
        Defaults to False if not explicitly supported.
        Checks:
        1. Provider capabilities (including configuration overrides)
        2. Model Configuration functionalities (models must explicitly opt-in via 'supports_vision')
        """
        try:
            provider = await self.llm_service.get_provider_by_id(model.provider_id)
            if not provider:
                return False

            adapter = get_adapter_from_provider(self.db_session, provider)
            caps = adapter.get_field_with_override("get_capabilities")
            vision_supported_by_provider = caps.get("vision", {}).get("value", False)
            
            if not vision_supported_by_provider:
                return False

            if model_config:
                funcs = getattr(model_config, "functionalities", {}) or {}
                return funcs.get("supports_vision", False)
            
            return False

        except Exception as e:
            logger.debug(f"Failed to check vision capability: {e}")
            return False

    def _append_rag_diagnostic(self, key: str, value: Any) -> None:
        target = self.diagnostics_target
        if not target:
            return
        data = getattr(target, "_pending_rag_diagnostics", None)
        if not isinstance(data, dict):
            data = {}
        target._pending_rag_diagnostics = data
        data.setdefault(key, []).append(value)

    async def _build_enhanced_rag_context(
        self,
        results: List[Dict],
        rag_config: Dict,
        knowledge_base_id: Optional[str] = None,
    ) -> Tuple[str, List[Dict]]:
        if not results:
            return "", []

        context_format = rag_config.get("context_format", "detailed")
        include_references = rag_config.get("include_references", True)

        context_parts = []
        source_metadata = []

        for i, result in enumerate(results, 1):
            # Extract metadata from QueryResult (handle both dict and object formats)
            if isinstance(result, dict):
                document_title = result.get("document_title", "Unknown Document")
                source_url = result.get("source_url", "")
                source_id = result.get("source_id", "")
                document_id = result.get("document_id", "")
                file_type = result.get("file_type", "")
                similarity_score = result.get("similarity_score", 0.0)
                chunk_content = result.get("content", "")
            else:
                document_title = getattr(result, "document_title", "Unknown Document")
                source_url = getattr(result, "source_url", "")
                source_id = getattr(result, "source_id", "")
                document_id = getattr(result, "document_id", "")
                file_type = getattr(result, "file_type", "")
                similarity_score = getattr(result, "similarity_score", 0.0)
                chunk_content = getattr(result, "content", "")

            # Generate source URL if missing. Prefer the internal document preview route when KB metadata exists.
            if not source_url:
                if knowledge_base_id and document_id:
                    source_url = f"/documents/{knowledge_base_id}/{document_id}"
                elif source_id:
                    source_url = f"https://drive.google.com/file/d/{source_id}/view"
                elif document_id:
                    source_url = f"https://drive.google.com/file/d/{document_id}/view"

            # Store metadata for citation tracking
            metadata_entry = {
                "source_index": i,
                "document_title": document_title,
                "source_url": source_url,
                "source_id": source_id,
                "document_id": document_id,
                "file_type": file_type,
                "similarity_score": similarity_score,
                "chunk_id": getattr(result, "chunk_id", "")
            }

            # Add knowledge base ID if provided
            if knowledge_base_id:
                metadata_entry["knowledge_base_id"] = knowledge_base_id

            source_metadata.append(metadata_entry)

            # Format context based on configuration
            if context_format == "detailed":
                context_part = f"**Source {i}: {document_title}**\n"

                if file_type:
                    context_part += f"*File Type:* {file_type}\n"

                if source_url:
                    context_part += f"*URL:* {source_url}\n"

                if similarity_score > 0:
                    context_part += f"*Relevance Score:* {similarity_score:.3f}\n"

                context_part += f"\n{chunk_content}\n"

            else:  # simple format
                context_part = f"**Source {i}: {document_title}**\n{chunk_content}\n"

            context_parts.append(context_part)

        # Join context parts
        rag_content = "\n---\n\n".join(context_parts)

        # Note: Always return source metadata for post-processing analysis
        # The decision to add system references will be made after getting the LLM response
        return rag_content, source_metadata

    async def _post_process_references(
        self,
        response_content: str,
        source_metadata: List[Dict],
        knowledge_base_id: Optional[str] = None,
        force_references: bool = False
    ) -> tuple[str, List[Dict]]:
        """
        Post-process LLM response to intelligently add system references.

        Analyzes the response to see what citations already exist and adds
        system references only when needed to avoid duplication.

        Args:
            response_content: The LLM response content
            source_metadata: Available source metadata from RAG
            knowledge_base_id: KB ID to get include_references setting
            force_references: If True, override KB setting and force references

        Returns:
            Tuple of (processed_content, final_source_metadata)
        """
        if not source_metadata:
            return response_content, []

        # Get KB configuration for include_references setting
        kb_include_references = True  # Default
        if knowledge_base_id:
            # Single KB specified
            try:
                from .knowledge_base_service import KnowledgeBaseService
                kb_service = KnowledgeBaseService(self.db_session)
                rag_config_response = await kb_service.get_rag_config(knowledge_base_id)
                rag_config = rag_config_response.model_dump()
                kb_include_references = rag_config.get("include_references", True)
            except Exception as e:
                # If we can't get the config, default to True
                logger.warning(f"Failed to get KB config for {knowledge_base_id}: {e}")
        elif source_metadata:
            # Multiple KBs from model config - check if any sources have KB info
            try:
                from .knowledge_base_service import KnowledgeBaseService
                kb_service = KnowledgeBaseService(self.db_session)

                # Get unique KB IDs from source metadata
                kb_ids = set()
                for meta in source_metadata:
                    if 'knowledge_base_id' in meta:
                        kb_ids.add(meta['knowledge_base_id'])

                # Check include_references setting for each KB
                # If ANY KB has include_references=False, respect that
                for kb_id in kb_ids:
                    rag_config_response = await kb_service.get_rag_config(kb_id)
                    rag_config = rag_config_response.model_dump()
                    kb_setting = rag_config.get("include_references", True)
                    if not kb_setting:
                        kb_include_references = False
                        break

            except Exception as e:
                logger.warning(f"Failed to get KB configs from source metadata: {e}")

        # Override KB setting if force_references is True (for LLM Tester)
        if force_references:
            kb_include_references = True

        # Use the new post-processing logic
        from ..utils.prompt_utils import should_add_system_references
        should_add, reason, sources_to_add = should_add_system_references(
            response_content,
            source_metadata,
            kb_include_references
        )
        logger.info(
            "DEBUG: Reference post-processing decision: should_add=%s, reason=%s, kb_include_references=%s, sources_count=%s",
            should_add,
            reason,
            kb_include_references,
            len(source_metadata),
        )

        if not should_add:
            # No system references needed
            return response_content, []

        # Add system references with deduplication by document title
        reference_list = []
        seen_titles = set()
        for meta in sources_to_add:
            title = meta["document_title"]
            url = meta.get("source_url", "")

            # Skip if we've already added this document title
            if title in seen_titles:
                continue
            seen_titles.add(title)

            if url:
                reference_list.append(f"- [{title}]({url})")
            else:
                reference_list.append(f"- {title}")

        if reference_list:
            references_text = "\n".join(reference_list)

            # Smart reference addition based on what's already in the response
            from ..utils.prompt_utils import analyze_response_references
            analysis = analyze_response_references(response_content, source_metadata)

            if analysis["reference_section_indicators"]:
                # Response already has references, append to existing section
                if reason == "missing_sources":
                    # Add missing sources to existing references
                    processed_content = response_content + f"\n{references_text}"
                else:
                    processed_content = response_content
            else:
                # No references section, add one
                processed_content = f"{response_content}\n\n**KB References:**\n{references_text}"

            return processed_content, sources_to_add
        else:
            return response_content, []
