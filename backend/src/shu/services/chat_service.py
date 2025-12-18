"""
Chat service for Shu RAG Backend.

Handles conversation management, message processing, and LLM integration.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Any, Union, AsyncGenerator, Tuple, Set
from sqlalchemy import select, and_, desc, asc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.llm_provider import Conversation, Message, LLMModel
from ..models.attachment import Attachment, MessageAttachment
from ..models.model_configuration import ModelConfiguration
from ..models.model_configuration_kb_prompt import ModelConfigurationKBPrompt
from ..models.llm_provider import LLMProvider
from ..core.config import ConfigurationManager, get_settings_instance
from ..core.exceptions import ShuException, ValidationError, LLMProviderError, ConversationNotFoundError, MessageNotFoundError
from ..llm.service import LLMService
from ..auth.rbac import rbac
from ..auth.models import User
from ..schemas.query import RagRewriteMode
from ..services.query_service import QueryService
from ..services.prompt_service import PromptService
from ..services.context_window_manager import ContextWindowManager
from ..services.context_preferences_resolver import ContextPreferencesResolver
from ..services.message_context_builder import MessageContextBuilder
from ..services.chat_types import ChatContext
from ..services.message_utils import serialize_message_for_sse
from .conversation_lock_service import acquire_conversation_lock, release_conversation_lock
from .chat_streaming import EnsembleStreamingHelper, ProviderResponseEvent

logger = logging.getLogger(__name__)
settings = get_settings_instance()


@dataclass
class PreparedTurnContext:
    """Data container for a prepared user turn and its LLM context."""

    conversation: Conversation
    user_message: Message
    conversation_messages: List[Message]
    knowledge_base_id: Optional[str]


@dataclass
class ModelExecutionInputs:
    """Resolved inputs for executing a single model configuration."""

    model_configuration: ModelConfiguration
    provider_id: str
    model: LLMModel
    context_messages: ChatContext
    source_metadata: List[Dict]
    knowledge_base_id: Optional[str]


class ChatService:
    """Service for managing chat conversations and messages."""

    def __init__(self, db_session: AsyncSession, config_manager: ConfigurationManager):
        self.db_session = db_session
        self.config_manager = config_manager
        self.llm_service = LLMService(db_session)
        self.prompt_service = PromptService(db_session)
        self.query_service = QueryService(db_session, config_manager)
        self.context_window_manager = ContextWindowManager(
            llm_service=self.llm_service,
            db_session=self.db_session,
            config_manager=self.config_manager,
        )
        self.context_preferences_resolver = ContextPreferencesResolver(
            db_session=self.db_session,
            config_manager=self.config_manager,
        )
        self.message_context_builder = MessageContextBuilder(
            db_session=self.db_session,
            config_manager=self.config_manager,
            llm_service=self.llm_service,
            prompt_service=self.prompt_service,
            query_service=self.query_service,
            context_window_manager=self.context_window_manager,
            context_preferences_resolver=self.context_preferences_resolver,
            conversation_message_fetcher=self.get_conversation_messages,
            diagnostics_target=self,
        )
        self.streaming_helper = EnsembleStreamingHelper(
            self,
            self.message_context_builder,
            db_session=self.db_session,
            config_manager=config_manager,
        )

    async def _prepare_turn_context(
        self,
        conversation: Conversation,
        user_message: str,
        knowledge_base_id: Optional[str],
        attachment_ids: Optional[List[str]] = None,
    ) -> PreparedTurnContext:
        """Insert the user message and assemble shared context for an ensemble turn."""
        user_msg = await self.add_message(
            conversation_id=conversation.id,
            role="user",
            content=user_message,
            attachment_ids=attachment_ids,
        )

        # Capture message history after inserting the user turn so all ensemble variants
        # share the exact same context when rendered.
        conversation_messages = await self.get_conversation_messages(
            conversation_id=conversation.id,
            limit=50,
        )

        return PreparedTurnContext(
            conversation=conversation,
            user_message=user_msg,
            conversation_messages=conversation_messages,
            knowledge_base_id=knowledge_base_id,
        )

    async def _resolve_ensemble_configurations(
        self,
        conversation: Conversation,
        ensemble_model_configuration_ids: Optional[List[str]],
        current_user: Optional[User]
    ) -> List[ModelConfiguration]:
        """
        Resolve the model configurations participating in an ensemble turn.

        Returns the conversation's active configuration last, prepended by any additional
        configurations requested by the caller (deduplicated). We do this to ensure the main
        model receives the highest variant index.
        """
        if not conversation.model_configuration:
            raise LLMProviderError("Conversation is missing a model configuration")

        resolved: List[ModelConfiguration] = []
        seen: Set[str] = {conversation.model_configuration.id}

        if ensemble_model_configuration_ids:
            for model_config_id in ensemble_model_configuration_ids:
                if not model_config_id or model_config_id in seen:
                    continue
                model_config = await self._load_active_model_configuration(
                    model_config_id,
                    current_user=current_user
                )
                resolved.append(model_config)
                seen.add(model_config.id)

        resolved.append(conversation.model_configuration)

        return resolved

    async def _build_model_execution_inputs(
        self,
        *,
        base_conversation: Conversation,
        turn_context: PreparedTurnContext,
        model_configuration: ModelConfiguration,
        current_user: User,
        rag_rewrite_mode: RagRewriteMode,
        recent_messages_limit: Optional[int] = None,
    ) -> ModelExecutionInputs:
        """
        Build context and resolve provider/model for a specific model configuration.
        """
        provider_id = model_configuration.llm_provider_id
        if not provider_id:
            raise LLMProviderError("Model configuration is missing provider reference")

        provider = await self.llm_service.get_provider_by_id(provider_id)
        if not provider or not provider.is_active:
            raise LLMProviderError(f"Provider '{provider_id}' is not active or not found")

        model_name = model_configuration.model_name
        if not model_name:
            raise LLMProviderError("Model configuration is missing model name")

        model = await self.llm_service.get_model_by_name(model_name, provider_id=provider_id)
        if not model or not model.is_active:
            raise LLMProviderError(f"Model '{model_name}' is not active for provider '{provider_id}'")

        chat_context, source_metadata = await self.message_context_builder.build_message_context(
            conversation=base_conversation,
            user_message=turn_context.user_message.content,
            current_user=current_user,
            model=model,
            knowledge_base_id=turn_context.knowledge_base_id,
            rag_rewrite_mode=rag_rewrite_mode,
            conversation_messages=turn_context.conversation_messages,
            model_configuration_override=model_configuration,
            recent_messages_limit=recent_messages_limit,
        )

        return ModelExecutionInputs(
            model_configuration=model_configuration,
            provider_id=provider_id,
            model=model,
            context_messages=chat_context,
            source_metadata=source_metadata,
            knowledge_base_id=turn_context.knowledge_base_id,
        )

    @staticmethod
    def _build_model_configuration_metadata(
        model_config: ModelConfiguration,
        model: Optional[LLMModel] = None
    ) -> Dict[str, Any]:
        provider = getattr(model_config, "llm_provider", None)
        provider_snapshot = None
        if provider:
            provider_snapshot = {
                "id": getattr(provider, "id", None),
                "name": getattr(provider, "name", None),
                "provider_type": getattr(provider, "provider_type", None),
            }

        return {
            "model_configuration": {
                "id": getattr(model_config, "id", None),
                "name": getattr(model_config, "name", None),
                "display_name": getattr(model_config, "name", None),
                "model_name": getattr(model, "model_name", None) or getattr(model_config, "model_name", None),
                "provider": provider_snapshot,
            }
        }

    @staticmethod
    def normalize_summary_query(summary_query: Optional[str]) -> List[str]:
        """Normalize user-provided summary keyword filters."""
        if not summary_query:
            return []

        normalized: List[str] = []
        min_token_length = max(settings.conversation_summary_search_min_token_length, 1)
        max_tokens = max(settings.conversation_summary_search_max_tokens, 1)

        for raw_token in summary_query.strip().split():
            token = raw_token.strip().lower()

            # Short token searches are inefficient, so we remove those.
            if len(token) < min_token_length:
                continue

            normalized.append(token)

            # We don't allow for more than the configured number of tokens, in order to limit search time.
            if len(normalized) >= max_tokens:
                break

        return normalized

    async def _ensure_provider_active(self, provider_id: str):
        """Validate that a provider exists and is active, returning the provider."""
        provider = await self.llm_service.get_provider_by_id(provider_id)
        if not provider:
            raise LLMProviderError(f"Provider with ID '{provider_id}' not found")
        if not provider.is_active:
            raise LLMProviderError(f"Provider '{provider.name}' is not active")
        return provider

    async def _load_active_model_configuration(
        self,
        model_configuration_id: str,
        current_user: Optional[User] = None
    ):
        """Fetch a model configuration and ensure both it and its provider are active."""
        from .model_configuration_service import ModelConfigurationService

        model_config_service = ModelConfigurationService(self.db_session)
        model_config = await model_config_service.get_model_configuration(
            model_configuration_id,
            include_relationships=True,
            current_user=current_user
        )

        if not model_config:
            raise ShuException(
                message=f"Model configuration with ID '{model_configuration_id}' not found",
                error_code="MODEL_CONFIGURATION_NOT_FOUND",
                status_code=404,
                details={"model_configuration_id": model_configuration_id}
            )

        if not model_config.is_active:
            raise LLMProviderError(f"Model configuration '{model_config.name}' is not active")

        if not model_config.llm_provider:
            raise LLMProviderError("Model configuration is missing provider relationship")

        await self._ensure_provider_active(model_config.llm_provider_id)
        return model_config

    async def _resolve_conversation_model(self, conversation: Conversation) -> Tuple[str, LLMModel]:
        """
        Resolve the active provider and model to use for a conversation.

        Requires the conversation to be linked to a model configuration.
        """
        model_config = conversation.model_configuration if conversation.model_configuration_id else None
        if not model_config:
            raise LLMProviderError("Conversation is missing a model configuration")

        await self._ensure_provider_active(model_config.llm_provider_id)

        model_name = model_config.model_name
        if not model_name:
            raise LLMProviderError("Model configuration is missing a model name")

        model_record = await self.llm_service.get_model_by_name(
            model_name,
            provider_id=model_config.llm_provider_id
        )

        if not model_record or not model_record.is_active:
            raise LLMProviderError(
                f"Model '{model_name}' not found or inactive for provider '{model_config.llm_provider_id}'"
            )

        return model_config.llm_provider_id, model_record

    async def create_conversation(
        self,
        user_id: str,
        model_configuration_id: str,
        title: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        current_user: Optional[User] = None
    ) -> Conversation:
        """
        Create a new chat conversation using a model configuration.

        This is the preferred method for creating conversations as it uses
        the Model Configuration abstraction (Base Model + Prompt + Optional KBs).

        Args:
            user_id: ID of the user creating the conversation
            model_configuration_id: ID of the model configuration to use
            title: Optional conversation title
            context: Optional conversation context metadata

        Returns:
            Created conversation

        Raises:
            ValidationError: If user_id or model_configuration_id is invalid
            LLMProviderError: If model configuration is not found or inactive
        """
        if not user_id:
            raise ValidationError("User ID is required")

        if not model_configuration_id:
            raise ValidationError("Model configuration ID is required")

        model_config = await self._load_active_model_configuration(
            model_configuration_id,
            current_user=current_user
        )

        # Generate title if not provided
        if not title:
            title = f"Chat with {model_config.name} - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"

        conversation = Conversation(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title=title,
            model_configuration_id=model_configuration_id,
        )

        self.db_session.add(conversation)
        await self.db_session.commit()
        await self.db_session.refresh(conversation)

        # Load the model configuration relationship for API response
        conversation = await self.get_conversation_by_id(conversation.id)

        logger.info(f"Created conversation '{title}' for user {user_id} with model config '{model_config.name}'")
        return conversation

    async def get_conversation_by_id(self, conversation_id: str, include_inactive: bool = False) -> Optional[Conversation]:
        """Get conversation by ID with messages and model configuration.

        By default, inactive (soft-deleted) conversations are excluded. Set include_inactive=True
        to fetch regardless of is_active.
        """
        stmt = select(Conversation).where(
            Conversation.id == conversation_id
        ).options(
            selectinload(Conversation.messages).selectinload(Message.model),
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.llm_provider),
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.prompt),
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.knowledge_bases),
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.kb_prompt_assignments).selectinload(ModelConfigurationKBPrompt.prompt)
        )

        if not include_inactive:
            stmt = stmt.where(Conversation.is_active == True)

        result = await self.db_session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_conversations(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        include_inactive: bool = False,
        summary_terms: Optional[List[str]] = None
    ) -> List[Conversation]:
        """Get conversations for a user."""
        stmt = select(Conversation).where(
            Conversation.user_id == user_id
        ).options(
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.llm_provider),
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.prompt),
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.knowledge_bases)
        )

        if not include_inactive:
            stmt = stmt.where(Conversation.is_active == True)

        if summary_terms:
            stmt = stmt.where(Conversation.summary_text.isnot(None))
            for term in summary_terms:
                stmt = stmt.where(Conversation.summary_text.ilike(f"%{term}%"))

        stmt = stmt.order_by(desc(Conversation.updated_at)).limit(limit).offset(offset)

        result = await self.db_session.execute(stmt)
        return result.scalars().all()

    async def update_conversation(
        self,
        conversation_id: str,
        title: Optional[str] = None,
        is_active: Optional[bool] = None,
        *,
        meta_updates: Optional[Dict[str, Any]] = None
    ) -> Conversation:
        """Update conversation details."""
        conversation = await self.get_conversation_by_id(conversation_id)
        if not conversation:
            raise ConversationNotFoundError(f"Conversation with ID '{conversation_id}' not found")

        # Update fields
        if title is not None:
            conversation.title = title
        if is_active is not None:
            conversation.is_active = is_active

        if meta_updates:
            current_meta = dict(conversation.meta or {})
            current_meta.update(meta_updates)
            conversation.meta = current_meta

        conversation.updated_at = datetime.now(timezone.utc)

        await self.db_session.commit()
        await self.db_session.refresh(conversation)

        logger.info(f"Updated conversation '{conversation.title}'")
        return conversation


    async def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation (soft delete by setting is_active=False)."""
        conversation = await self.get_conversation_by_id(conversation_id)
        if not conversation:
            raise ConversationNotFoundError(f"Conversation with ID '{conversation_id}' not found")

        conversation.is_active = False
        conversation.updated_at = datetime.now(timezone.utc)

        await self.db_session.commit()

        logger.info(f"Deleted conversation '{conversation.title}'")
        return True

    async def _link_attachments_to_message(
        self,
        conversation_id: str,
        message_id: str,
        attachment_ids: List[str],
    ) -> None:
        """Validate and link attachments to a message.

        Args:
            conversation_id: ID of the conversation the message belongs to
            message_id: ID of the message to link attachments to
            attachment_ids: List of attachment IDs to link

        Raises:
            ValidationError: If any attachment does not belong to the conversation
        """
        if not attachment_ids:
            return

        # TODO: We shuold probably drop the `conversation_id` column on the attachments, if we identify it by message. For now, we just evaluate that there is parity.

        # Validate attachment ownership and conversation membership
        q = select(Attachment).where(Attachment.id.in_(attachment_ids))
        res = await self.db_session.execute(q)
        att_list = res.scalars().all()

        validated_ids = set()
        for att in att_list:
            if att.conversation_id != conversation_id:
                raise ValidationError("Attachment does not belong to this conversation")
            validated_ids.add(att.id)

        # Create MessageAttachment links for validated attachments
        for att_id in validated_ids:
            link = MessageAttachment(
                id=str(uuid.uuid4()),
                message_id=message_id,
                attachment_id=att_id,
            )
            self.db_session.add(link)

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        model_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        parent_message_id: Optional[str] = None,
        variant_index: Optional[int] = None,
        message_id: Optional[str] = None,
        attachment_ids: Optional[List[str]] = None,
    ) -> Message:
        """
        Add a message to a conversation.

        Args:
            conversation_id: ID of the conversation
            role: Message role ('user', 'assistant', 'system')
            content: Message content
            model_id: Optional model ID for assistant messages
            metadata: Optional message metadata

        Returns:
            Created message

        Raises:
            ConversationNotFoundError: If conversation doesn't exist
            ValidationError: If role or content is invalid
        """
        if role not in ['user', 'assistant', 'system']:
            raise ValidationError(f"Invalid message role: {role}")

        if not content or not content.strip():
            raise ValidationError("Message content cannot be empty")
        
        if message_id is None:
            message_id = str(uuid.uuid4())

        # Verify conversation exists
        conversation = await self.get_conversation_by_id(conversation_id)
        if not conversation:
            raise ConversationNotFoundError(f"Conversation with ID '{conversation_id}' not found")

        # Validate model if provided
        if model_id:
            model = await self.llm_service.get_model_by_id(model_id)
            if not model:
                raise LLMProviderError(f"Model with ID '{model_id}' not found")

        metadata_dict: Dict[str, Any] = dict(metadata or {})

        # Snapshot the conversation's model configuration for assistant messages so that
        # downstream consumers can understand which configuration generated the response.
        if role == "assistant" and getattr(conversation, "model_configuration", None):
            model_config = conversation.model_configuration
            provider = getattr(model_config, "llm_provider", None)
            metadata_dict.setdefault(
                "model_configuration",
                {
                    "id": getattr(model_config, "id", None),
                    "name": getattr(model_config, "name", None),
                    "model_name": getattr(model_config, "model_name", None),
                    "provider": {
                        "id": getattr(provider, "id", None),
                        "name": getattr(provider, "name", None),
                        "provider_type": getattr(provider, "provider_type", None),
                    }
                    if provider
                    else None,
                },
            )

        message = Message(
            id=message_id,
            conversation_id=conversation_id,
            role=role,
            content=content.strip(),
            model_id=model_id,
            message_metadata=metadata_dict,
            parent_message_id=parent_message_id,
            variant_index=variant_index,
        )

        self.db_session.add(message)
        # Ensure INSERT happens to satisfy FK for message_attachments
        await self.db_session.flush()

        # Link validated attachments to this message
        if attachment_ids:
            await self._link_attachments_to_message(
                conversation_id=conversation_id,
                message_id=message.id,
                attachment_ids=attachment_ids,
            )

        # Update conversation timestamp
        conversation.updated_at = datetime.now(timezone.utc)

        await self.db_session.commit()
        await self.db_session.refresh(message)

        # Eagerly load relationships to avoid MissingGreenlet errors
        stmt = select(Message).where(Message.id == message.id).options(
            selectinload(Message.model),
            selectinload(Message.conversation),
            selectinload(Message.attachments)
        )
        result = await self.db_session.execute(stmt)
        message_with_relationships = result.scalar_one()

        logger.info(f"Added {role} message to conversation '{conversation.title}'")
        return message_with_relationships

    async def get_conversation_messages(
        self,
        conversation_id: str,
        limit: int = 100,
        offset: int = 0,
        order_desc: bool = False,
    ) -> List[Message]:
        """Get messages for a conversation.

        Also performs a lightweight one-time backfill for message variant lineage on
        legacy conversations: if an assistant message has no parent_message_id but
        later assistant messages reference it via parent_message_id, set the original
        message's parent_message_id to its own id and variant_index to 0. This keeps
        frontend grouping consistent after page reloads.
        """
        order_clause = desc(Message.created_at) if order_desc else asc(Message.created_at)

        stmt = select(Message).where(
            Message.conversation_id == conversation_id
        ).options(
            selectinload(Message.model),
            selectinload(Message.attachments)
        ).order_by(order_clause).limit(limit).offset(offset)

        result = await self.db_session.execute(stmt)
        messages = result.scalars().all()

        # Backfill lineage for legacy conversations (safe: never override explicit lineage)
        try:
            dirty = False

            # Build assistant variant groups by explicit parent when present, else self-root
            groups_by_root: Dict[str, List[Message]] = {}
            for msg in messages:
                if getattr(msg, 'role', None) != 'assistant':
                    continue
                root_id = getattr(msg, 'parent_message_id', None) or msg.id
                # Only set parent_message_id when it's missing
                if getattr(msg, 'parent_message_id', None) is None:
                    msg.parent_message_id = root_id
                    dirty = True
                groups_by_root.setdefault(root_id, []).append(msg)

            # For each group, sort by created_at and backfill missing variant_index
            for root_id, group in groups_by_root.items():
                sorted_msgs = sorted(group, key=lambda m: getattr(m, 'created_at'))
                for idx, msg in enumerate(sorted_msgs):
                    # Only update variant_index when missing
                    if getattr(msg, 'variant_index', None) is None:
                        msg.variant_index = idx
                        dirty = True

            if dirty:
                await self.db_session.commit()
        except Exception as e:
            logger.warning(f"Lineage backfill failed for conversation {conversation_id}: {e}")

        return messages

    async def count_conversation_messages(self, conversation_id: str) -> int:
        """Return total number of persisted messages for a conversation."""
        stmt = select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
        result = await self.db_session.execute(stmt)
        return result.scalar_one()

    async def get_message_by_id(self, message_id: str) -> Optional[Message]:
        """Get message by ID."""
        stmt = select(Message).where(
            Message.id == message_id
        ).options(selectinload(Message.model))

        result = await self.db_session.execute(stmt)
        return result.scalar_one_or_none()

    async def send_message(
        self,
        conversation_id: str,
        user_message: str,
        current_user,  # User object for access control
        knowledge_base_id: Optional[str] = None,
        rag_rewrite_mode: RagRewriteMode = RagRewriteMode.RAW_QUERY,
        client_temp_id: Optional[str] = None,
        ensemble_model_configuration_ids: Optional[List[str]] = None,
        attachment_ids: Optional[List[str]] = None,
    ) -> AsyncGenerator["ProviderResponseEvent", None]:
        """
        Send a message and get LLM response using the conversation model or an ensemble.

        Args:
            conversation_id: ID of the conversation
            user_message: User's message content
            knowledge_base_id: Optional specific knowledge base for RAG (overrides model config KBs)
            rag_rewrite_mode: Strategy for preparing retrieval queries / disabling RAG
            ensemble_model_configuration_ids: Optional additional model configuration IDs to execute

        Returns:
            Async generator of ProviderResponseEvent for streaming responses to the caller

        Raises:
            ConversationNotFoundError: If conversation doesn't exist
            LLMProviderError: If LLM provider/model is not available
        """
        # lock_id = str(uuid.uuid4())
        # TODO: This is risky for now and causes issues with concurrent operations. We need some more effort to get this to work right.
        # await acquire_conversation_lock(
        #     db_session=self.db_session,
        #     conversation_id=conversation_id,
        #     lock_id=lock_id,
        #     owner_user_id=getattr(current_user, "id", None)
        # )
        # try:

        conversation = await self.get_conversation_by_id(conversation_id)
        if not conversation:
            raise ConversationNotFoundError(f"Conversation with ID '{conversation_id}' not found")

        stmt = select(Conversation).where(Conversation.id == conversation_id).options(
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.llm_provider).selectinload(LLMProvider.models),
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.prompt),
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.knowledge_bases),
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.kb_prompt_assignments).selectinload(ModelConfigurationKBPrompt.prompt)
        )
        result = await self.db_session.execute(stmt)
        conversation = result.scalar_one()

        should_use_rag = rag_rewrite_mode != RagRewriteMode.NO_RAG
        if knowledge_base_id and should_use_rag:
            has_access = await rbac.can_access_knowledge_base(current_user, knowledge_base_id, self.db_session)
            if not has_access:
                raise ShuException(
                    f"Access denied to knowledge base '{knowledge_base_id}'",
                    "KNOWLEDGE_BASE_ACCESS_DENIED"
                )

        turn_context = await self._prepare_turn_context(
            conversation=conversation,
            user_message=user_message,
            knowledge_base_id=knowledge_base_id,
            attachment_ids=attachment_ids,
        )

        model_configurations = await self._resolve_ensemble_configurations(
            conversation,
            ensemble_model_configuration_ids,
            current_user,
        )

        max_models = max(1, getattr(settings, "chat_ensemble_max_models", 1))
        if len(model_configurations) > max_models:
            raise ValidationError(
                f"Ensemble limit exceeded: requested {len(model_configurations)} models, "
                f"but only {max_models} are allowed."
            )

        preference_bundle = await self.context_preferences_resolver.resolve_user_context_preferences(
            user_id=conversation.user_id,
            current_user=current_user,
        )

        execution_inputs: List[ModelExecutionInputs] = [
            await self._build_model_execution_inputs(
                base_conversation=conversation,
                turn_context=turn_context,
                model_configuration=model_config,
                current_user=current_user,
                rag_rewrite_mode=rag_rewrite_mode,
                recent_messages_limit=preference_bundle["memory_depth"],
            )
            for model_config in model_configurations
        ]

        async def _gen():
            # Emit persisted user message early so client can replace placeholder deterministically
            try:
                yield ProviderResponseEvent(
                    type="user_message",
                    content=serialize_message_for_sse(turn_context.user_message),
                    client_temp_id=client_temp_id,
                )
            except Exception as ser_e:
                logger.warning(f"Failed to serialize user message for SSE: {ser_e}")
            async for event in self.streaming_helper.stream_ensemble_responses(
                ensemble_inputs=execution_inputs,
                conversation_id=conversation_id,
            ):
                yield event
        return _gen()

        # finally:
        #     await release_conversation_lock(self.db_session, conversation_id, lock_id)

    async def _handle_exception(self, conversation_id: str, model: LLMModel, e: Exception | ShuException) -> Message:
        logger.error("LLM completion failed: %s", e)

        # Record failed usage
        try:
            await self.llm_service.record_usage(
                provider_id=model.provider_id,
                model_id=model.id,
                request_type="chat",
                input_tokens=0,
                output_tokens=0,
                total_cost=Decimal('0'),
                success=False,
                error_message=str(e)
            )
        except Exception as usage_error:
            logger.warning("Failed to record LLM usage: %s", usage_error)

        # Create error message
        error_message = await self.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=f"I apologize, but I encountered an error: {str(e)}",
            model_id=model.id,
            metadata={"error": e.details if isinstance(e, ShuException) else str(e)}
        )

        return error_message

    async def regenerate_message(
        self,
        message_id: str,
        current_user,
        parent_message_id: Optional[str] = None,
        rag_rewrite_mode: RagRewriteMode = RagRewriteMode.RAW_QUERY,
    ) -> AsyncGenerator["ProviderResponseEvent", None]:
        """
        Regenerate an assistant message by rebuilding context up to the preceding user turn.
        """
        # Load target message
        target = await self.get_message_by_id(message_id)
        if not target:
            raise MessageNotFoundError(f"Message with ID '{message_id}' not found")
        if target.role != 'assistant':
            raise ValidationError("Only assistant messages can be regenerated")

        # Load conversation with ownership check
        conversation = await self.get_conversation_by_id(target.conversation_id)
        if not conversation:
            raise ConversationNotFoundError(f"Conversation with ID '{target.conversation_id}' not found")
        # Basic RBAC: ensure owner
        if hasattr(current_user, 'id') and conversation.user_id != current_user.id:
            raise ShuException("You don't have access to this conversation", "UNAUTHORIZED", status_code=403)

        # Reload conversation with full relationships to avoid async lazy loads
        stmt = select(Conversation).where(Conversation.id == conversation.id).options(
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.llm_provider).selectinload(LLMProvider.models),
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.prompt),
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.knowledge_bases),
            selectinload(Conversation.model_configuration).selectinload(ModelConfiguration.kb_prompt_assignments).selectinload(ModelConfigurationKBPrompt.prompt)
        )
        result = await self.db_session.execute(stmt)
        conversation = result.scalar_one()

        # Fetch messages to find preceding user turn and reconstruct trimmed history
        all_msgs = await self.get_conversation_messages(conversation_id=conversation.id, limit=500)

        target_idx, root_turn_idx = self._locate_regeneration_indices(
            all_msgs,
            target=target,
            parent_message_id=parent_message_id,
        )
        # Find index of target and walk back to preceding user message
        idx = target_idx

        # Find preceding user message index
        preceding_user_idx = None
        for i in range(idx - 1, -1, -1):
            if all_msgs[i].role == 'user':
                preceding_user_idx = i
                break

        # Build context prior to the target assistant message using the shared helper
        history_end = (preceding_user_idx + 1) if preceding_user_idx is not None else 0
        if root_turn_idx is not None:
            history_end = min(history_end, root_turn_idx)
        history_messages = all_msgs[:history_end]
        preceding_user_message = all_msgs[preceding_user_idx] if preceding_user_idx is not None else None
        preceding_user_content = preceding_user_message.content if preceding_user_message else ""
        # Resolve provider/model via model configuration or cached model reference
        provider_id, model = await self._resolve_conversation_model(conversation)

        chat_context, source_metadata = await self.message_context_builder.build_message_context(
            conversation=conversation,
            user_message=preceding_user_content,
            current_user=current_user,
            model=model,
            knowledge_base_id=None,
            rag_rewrite_mode=rag_rewrite_mode,
            conversation_messages=history_messages,
        )

        # Use the explicit parent_message_id from the frontend, or fall back to target.id
        root_id = parent_message_id or target.id
        logger.info(f"REGENERATE DEBUG: message_id={message_id}, parent_message_id={parent_message_id}, target.id={target.id}, root_id={root_id}")

        execution_inputs = [
            ModelExecutionInputs(
                model_configuration=conversation.model_configuration,
                provider_id=provider_id,
                model=model,
                context_messages=chat_context,
                source_metadata=source_metadata,
                knowledge_base_id=None,
            )
        ]

        # Determine per-model parameter overrides from configuration (regenerate path)
        base_stream = self.streaming_helper.stream_ensemble_responses(
            ensemble_inputs=execution_inputs,
            conversation_id=conversation.id,
            parent_message_id_override=root_id,
        )

        async def regen_stream() -> AsyncGenerator[Dict[str, Any], None]:
            async for event in base_stream:
                if event.type == "final_message":
                    if target.parent_message_id is None:
                        target.parent_message_id = root_id
                        if target.id == root_id:
                            target.variant_index = 0

                    msg_payload = event.content or {}
                    msg_id = msg_payload.get("id")
                    new_assistant: Optional[Message] = None
                    if msg_id:
                        new_assistant = await self.get_message_by_id(msg_id)

                    if new_assistant:
                        stmt = select(Message.variant_index).where(Message.parent_message_id == root_id)
                        res = await self.db_session.execute(stmt)
                        existing = [vi for (vi,) in res.all() if vi is not None]
                        next_idx = (max(existing) + 1) if existing else 1

                        new_assistant.parent_message_id = root_id
                        new_assistant.variant_index = next_idx
                        meta = dict(getattr(new_assistant, "message_metadata", {}) or {})
                        meta["regenerated"] = True
                        meta["regenerated_from_message_id"] = target.id
                        new_assistant.message_metadata = meta

                        await self.db_session.commit()
                        await self.db_session.refresh(new_assistant)

                        event.variant_index = next_idx
                        event.content = serialize_message_for_sse(new_assistant)
                    else:
                        await self.db_session.commit()

                yield event

        return regen_stream()

    def _locate_regeneration_indices(
        self,
        messages: List[Message],
        target: Message,
        parent_message_id: Optional[str],
    ) -> Tuple[int, Optional[int]]:
        """
        Locate the target assistant message and the start of its variant group.

        Returns:
            Tuple of (target_index, root_variant_index or None)
        """
        root_parent_id = parent_message_id or getattr(target, "parent_message_id", None) or target.id
        root_turn_idx: Optional[int] = None
        sibling_candidates: List[Tuple[int, Message]] = []
        target_idx: Optional[int] = None

        for idx, msg in enumerate(messages):
            if msg.id == target.id:
                target_idx = idx
            parent_id = getattr(msg, "parent_message_id", None) or msg.id
            if parent_id == root_parent_id:
                sibling_candidates.append((idx, msg))
                if getattr(msg, "variant_index", None) == 0 and root_turn_idx is None:
                    root_turn_idx = idx

        if root_turn_idx is None and sibling_candidates:
            earliest_idx, _ = min(
                sibling_candidates,
                key=lambda item: getattr(item[1], "created_at", datetime.min),
            )
            root_turn_idx = earliest_idx

        if target_idx is None:
            raise MessageNotFoundError("Target message not found in conversation history")

        return target_idx, root_turn_idx

    async def switch_conversation_model(
        self,
        conversation_id: str,
        new_model_configuration_id: Optional[str] = None,
        current_user: Optional[User] = None
    ) -> Conversation:
        """
        Switch the LLM model for a conversation while preserving context.

        Args:
            conversation_id: ID of the conversation
            new_provider_id: Deprecated (ignored)
            new_model_id: Deprecated (ignored)
            new_model_configuration_id: New model configuration ID to apply

        Returns:
            Updated conversation
        """
        try:
            conversation = await self.get_conversation_by_id(conversation_id)
            if not conversation:
                raise ConversationNotFoundError(f"Conversation with ID '{conversation_id}' not found")

            if not new_model_configuration_id:
                raise LLMProviderError("model_configuration_id must be provided when switching models")

            model_config = await self._load_active_model_configuration(
                new_model_configuration_id,
                current_user=current_user
            )

            if model_config:
                conversation.model_configuration_id = model_config.id
                conversation.model_configuration = model_config
            else:
                conversation.model_configuration_id = None
                conversation.model_configuration = None

            provider_id, model_record = await self._resolve_conversation_model(conversation)
            conversation.updated_at = datetime.now(timezone.utc)

            await self.db_session.commit()
            updated_conversation = await self.get_conversation_by_id(conversation_id)

            logger.info(
                f"Switched conversation {conversation_id} to model_id={model_record.id} "
                f"(provider_id={provider_id}, model_configuration_id={new_model_configuration_id or 'unchanged'})"
            )
            return updated_conversation

        except Exception as e:
            logger.error(f"Failed to switch conversation model: {e}")
            raise
