"""Side-Call Service for Shu RAG Backend.

This service provides optimized LLM calls for small, fast side-calls
like prompt assist, title generation, and UI summaries.
"""

import json
import logging
import re
import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import ConfigurationManager
from ..core.exceptions import LLMProviderError
from ..llm.service import LLMService
from ..models.llm_provider import Message
from ..models.model_configuration import ModelConfiguration
from ..services.chat_types import ChatContext
from ..services.model_configuration_service import ModelConfigurationService
from ..services.system_settings_service import SystemSettingsService

logger = logging.getLogger(__name__)

# Constants for side-call configuration
SIDE_CALL_MODEL_SETTING_KEY = "side_call_model_config_id"


class SideCallResult:
    """Result of a side-call operation."""

    def __init__(
        self,
        content: str,
        success: bool = True,
        error_message: str | None = None,
        tokens_used: int = 0,
        cost: Decimal | None = None,
        response_time_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.content = content
        self.success = success
        self.error_message = error_message
        self.tokens_used = tokens_used
        self.cost = cost
        self.response_time_ms = response_time_ms
        self.metadata = metadata or {}


class SideCallService:
    """Service for managing optimized LLM side-calls."""

    def __init__(self, db: AsyncSession, config_manager: ConfigurationManager) -> None:
        self.db = db
        self.config_manager = config_manager
        self.llm_service = LLMService(db)
        self.system_settings_service = SystemSettingsService(db)
        self.model_config_service = ModelConfigurationService(db)

    async def call(
        self,
        message_sequence: list[dict[str, str]],
        system_prompt: str | None = None,
        user_id: str | None = None,
        config_overrides: dict | None = None,
        timeout_ms: int | None = None,
    ) -> SideCallResult:
        """Make an optimized side-call to the designated LLM.

        Args:
            message_sequence: Chat-style sequence of messages ({"role", "content"})
            system_prompt: Optional system prompt injected ahead of the sequence
            user_id: ID of the user making the request (None for system operations)
            config_overrides: Optional configuration overrides for this call
            timeout_ms: Optional timeout override in milliseconds

        Returns:
            SideCallResult with the response or error

        """
        start_time = time.time()

        if not message_sequence:
            raise ValueError("message_sequence must contain at least one message")

        try:
            # Get the designated side-call model configuration
            model_config = await self.get_side_call_model()
            if not model_config:
                return SideCallResult(
                    content="",
                    success=False,
                    error_message="No side-call model configured",
                    response_time_ms=int((time.time() - start_time) * 1000),
                )

            system_prompt, messages = await self._build_sequence_messages(
                sequence=message_sequence,
                system_prompt=system_prompt,
                model_config=model_config,
            )

            # Get LLM client
            client = await self.llm_service.get_client(model_config.llm_provider_id)

            # Find the model
            model = await self._find_model_for_config(model_config)

            chat_ctx = ChatContext.from_dicts(messages, system_prompt)

            llm_params = {
                "messages": chat_ctx,
                "model": model.model_name,
                "stream": False,
                "model_overrides": model_config.parameter_overrides or None,
                "llm_params": None,
            }

            # Convert timeout to per-request timeout seconds
            request_timeout = (timeout_ms / 1000.0) if timeout_ms else None

            # Apply per-call configuration overrides
            if config_overrides:
                if not llm_params["model_overrides"]:
                    llm_params["model_overrides"] = {}
                llm_params["model_overrides"].update(config_overrides)

            # Make the LLM call
            responses = await client.chat_completion(**llm_params, request_timeout=request_timeout)

            # Calculate metrics
            response_time_ms = int((time.time() - start_time) * 1000)
            event_metadata = responses[-1].metadata or {}
            tokens_used = (event_metadata.get("usage", {}) or {}).get("total_tokens", 0)

            # Record usage
            await self._record_usage(
                model_config=model_config,
                model=model,
                user_id=user_id,
                tokens_used=tokens_used,
                response_time_ms=response_time_ms,
                success=True,
            )

            return SideCallResult(
                content=responses[-1].content or "",
                success=True,
                tokens_used=tokens_used,
                response_time_ms=response_time_ms,
                metadata={
                    "model_config_id": model_config.id,
                    "model_name": model_config.model_name,
                },
            )

        except Exception as e:
            response_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Side-call failed for user {user_id}: {e}")

            # Record failed usage
            model_config = await self.get_side_call_model()
            if model_config:
                try:
                    model = await self._find_model_for_config(model_config)
                except LLMProviderError as config_error:
                    logger.error(
                        "Unable to resolve side-call model %s during failure handling: %s",
                        model_config.id,
                        config_error,
                    )
                except Exception as lookup_error:
                    logger.exception(
                        "Unexpected error resolving side-call model %s during failure handling: %s",
                        model_config.id,
                        lookup_error,
                    )
                else:
                    await self._record_usage(
                        model_config=model_config,
                        model=model,
                        user_id=user_id,
                        tokens_used=0,
                        response_time_ms=response_time_ms,
                        success=False,
                        error_message=str(e),
                    )

            return SideCallResult(
                content="",
                success=False,
                error_message=str(e),
                response_time_ms=response_time_ms,
            )

    async def propose_rag_query(
        self,
        current_user_query: str,
        prior_messages: list[Message] | None = None,
        user_id: str | None = None,
        timeout_ms: int = 1200,
    ) -> SideCallResult:
        """Propose a retrieval-friendly RAG query using a minimal side-call.

        Sends only the current user query plus a few short prior snippets to
        preserve topic continuity without leaking full conversation context.

        Returns SideCallResult where content is the rewritten query string.
        """
        try:
            raw_sequence: list[dict[str, str]] = []
            if prior_messages:
                for msg in prior_messages[-7:-1]:
                    if not msg or not getattr(msg, "content", None):
                        continue
                    role = getattr(msg, "role", None)
                    if role not in ("user", "assistant"):
                        continue
                    raw_sequence.append({"role": role, "content": str(msg.content)})

            # TODO: Make the base prompt and the way we are sending the details to the LLM configurable.
            system_prompt = """
                You rewrite the user's message into a retrieval query for a document search engine.
                Input: (A) USER_MESSAGE the most recent user message; (B) MESSAGE_HISTORY some context on what the user and assistant talked about.
                Task: output ONLY a retrieval query that assists in returning the most relevant documents based on the user's request.

                Rules:
                    - Preserve and expand the subject (entities, IDs, synonyms, hyphen/space variants).
                    - Include prior topic if current message uses pronouns like 'it/its/they'.
                    - Remove instructions about response formatting, tone, or follow-up actions.
                    - Exclude words like 'explain', 'summarize', 'respond', 'answer', 'in detail', etc.
                    - Prefer concise tokens over sentences; avoid stop-words.
                    - Output ONLY the query (no prose, no quotes, no bullet points).
            """

            message_sequence = [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": f"USER_MESSAGE:\n\n{current_user_query}"},
                        {
                            "type": "input_text",
                            "text": f"MESSAGE_HISTORY (JSON):\n\n{json.dumps(raw_sequence)}",
                        },
                        {
                            "type": "input_text",
                            "text": "TASK: Produce the ENHANCED_SEARCH_QUERY as plain text only.",
                        },
                    ],
                }
            ]

            return await self.call(
                message_sequence=message_sequence,
                system_prompt=system_prompt,
                user_id=user_id,
                timeout_ms=timeout_ms,
            )
        except Exception as e:
            logger.error("propose_rag_query failed: %s", e)
            return SideCallResult(content=current_user_query, success=False, error_message=str(e))

    async def distill_rag_query(
        self,
        current_user_query: str,
        user_id: str | None = None,
        timeout_ms: int = 1200,
    ) -> SideCallResult:
        """Distill a user's message down to retrieval-critical terms only.

        Removes instructions about output formatting and preserves just the entities,
        identifiers, and key subject phrases needed for document lookup.
        """
        try:
            trimmed_query = (current_user_query or "").strip()

            system_prompt = """
                You are a retrieval assistant, and are given the latest user request.
                Input: (A) USER_MESSAGE the most recent user message.
                Task: output ONLY the minimal factual query terms needed to search a knowledge base.

                Rules:
                    - Preserve entities, IDs, acronyms, and discriminating nouns.
                    - Remove instructions about response formatting, tone, or follow-up actions.
                    - Exclude words like 'explain', 'summarize', 'respond', 'answer', 'in detail', etc.
                    - Output a single plain-text line with no quotes, bullet points, or commentary.
                    - If nothing concrete remains, return an empty string.
            """

            message_sequence = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"USER_MESSAGE:\n\n{trimmed_query}",
                        },
                        {
                            "type": "input_text",
                            "text": "TASK: Produce the DISTILLED_QUERY as plain text only. Remove directives and keep only the subject matter terms.",
                        },
                    ],
                }
            ]

            return await self.call(
                message_sequence=message_sequence,
                system_prompt=system_prompt,
                user_id=user_id,
                timeout_ms=timeout_ms,
            )
        except Exception as e:
            logger.error("distill_rag_query failed: %s", e)
            return SideCallResult(content=current_user_query, success=False, error_message=str(e))

    async def get_side_call_model(self) -> ModelConfiguration | None:
        """Get the currently designated side-call model configuration.

        Returns:
            ModelConfiguration designated for side-calls, or None if not set

        """
        try:
            # Get the system setting for side-call model ID
            setting = await self.system_settings_service.get_setting(SIDE_CALL_MODEL_SETTING_KEY)
            if not setting or not setting.value.get("model_config_id"):
                logger.warning("No side-call model configured in system settings")
                return None

            model_config_id = setting.value["model_config_id"]

            # Get the model configuration
            model_config = await self.model_config_service.get_model_configuration(
                model_config_id, include_relationships=True
            )

            if not model_config or not model_config.is_active:
                logger.warning(f"Side-call model {model_config_id} not found or inactive")
                return None

            return model_config

        except Exception as e:
            logger.error(f"Failed to get side-call model: {e}")
            return None

    async def set_side_call_model(self, model_config_id: str, user_id: str) -> bool:
        """Set the designated side-call model configuration.

        Args:
            model_config_id: ID of the model configuration to designate
            user_id: ID of the user making the change

        Returns:
            True if successful, False otherwise

        """
        try:
            # Verify the model configuration exists and is suitable
            model_config = await self.model_config_service.get_model_configuration(
                model_config_id, include_relationships=True
            )

            if not model_config or not model_config.is_active:
                logger.error(f"Model configuration {model_config_id} not found or inactive")
                return False

            # Update the system setting
            await self.system_settings_service.upsert(
                SIDE_CALL_MODEL_SETTING_KEY,
                {
                    "model_config_id": model_config_id,
                    "updated_by": user_id,
                    "updated_at": datetime.utcnow().isoformat(),
                },
            )

            logger.info(f"Side-call model set to {model_config_id} by user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to set side-call model {model_config_id}: {e}")
            return False

    async def clear_side_call_model(self, user_id: str) -> bool:
        """Clear the designated side-call model configuration.

        Args:
            user_id: ID of the user making the change

        Returns:
            True if successful, False otherwise

        """
        try:
            await self.system_settings_service.delete(SIDE_CALL_MODEL_SETTING_KEY)
            logger.info(f"Side-call model cleared by user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to clear side-call model by user {user_id}: {e}")
            return False

    async def redact_sensitive_content(self, content: str) -> str:
        """Redact sensitive content from prompts before sending to LLM.

        Args:
            content: Original content that may contain sensitive information

        Returns:
            Content with sensitive information redacted

        """
        try:
            # Define patterns for sensitive information
            patterns = [
                # Email addresses
                (
                    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
                    "[EMAIL_REDACTED]",
                ),
                # Phone numbers (US format)
                (r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "[PHONE_REDACTED]"),
                # Social Security Numbers
                (r"\b\d{3}-\d{2}-\d{4}\b", "[SSN_REDACTED]"),
                # Credit card numbers
                (
                    r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
                    "[CREDIT_CARD_REDACTED]",
                ),
                # API keys (generic pattern)
                (r"\b[A-Za-z0-9]{20,}\b", "[API_KEY_REDACTED]"),
                # Passwords in context
                (
                    r'password["\s:]+["\']?([^"\'\s]+)["\']?',
                    'password="[PASSWORD_REDACTED]"',
                ),
            ]

            redacted_content = content
            for pattern, replacement in patterns:
                redacted_content = re.sub(pattern, replacement, redacted_content, flags=re.IGNORECASE)

            return redacted_content

        except Exception as e:
            logger.error(f"Failed to redact sensitive content: {e}")
            # Return original content if redaction fails
            return content

    async def _find_model_for_config(self, model_config: ModelConfiguration):
        """Find the LLM model for a model configuration.

        Args:
            model_config: Model configuration

        Returns:
            LLM model

        Raises:
            LLMProviderError: If model not found

        """
        if not model_config.model_name:
            raise LLMProviderError("Model configuration does not specify a model name")

        # Get the provider
        provider = model_config.llm_provider
        if not provider:
            raise LLMProviderError("Model configuration does not have a provider")

        # Find the model by name
        for model in provider.models:
            if model.model_name == model_config.model_name and model.is_active:
                return model

        raise LLMProviderError(
            f"Model '{model_config.model_name}' not found or inactive " f"for provider '{provider.name}'"
        )

    async def _build_sequence_messages(
        self,
        sequence: list[dict[str, str]],
        system_prompt: str | None = None,
        model_config: ModelConfiguration | None = None,
    ) -> tuple[str, list[dict[str, str]]]:
        """Normalize and redact message sequences provided directly by callers.

        Includes a system prompt if provided; otherwise falls back to the
        model configuration's default prompt when available.
        """
        messages: list[dict[str, str]] = []

        # Add system message: explicit prompt first, else model default prompt
        if system_prompt and system_prompt.strip():
            system_prompt = system_prompt.strip()
        elif model_config and getattr(model_config, "prompt", None) and getattr(model_config.prompt, "content", None):
            system_prompt = model_config.prompt.content

        for entry in sequence:
            role = entry.get("role")
            content = entry.get("content", "")
            if not role:
                continue
            redacted = await self.redact_sensitive_content(str(content))
            messages.append({"role": role, "content": redacted})

        return system_prompt, messages

    async def _record_usage(
        self,
        model_config: ModelConfiguration,
        model,
        user_id: str | None,
        tokens_used: int,
        response_time_ms: int,
        success: bool,
        error_message: str | None = None,
    ) -> None:
        """Record usage metrics for the side-call.

        Args:
            model_config: The model configuration used
            model: The model used
            user_id: ID of the user who made the request (None for system operations)
            tokens_used: Number of tokens used
            response_time_ms: Response time in milliseconds
            success: Whether the call was successful
            error_message: Error message if the call failed

        """
        try:
            # Calculate cost (simplified - in production this would use actual pricing)
            cost_per_token = Decimal("0.0001")  # Default cost per token
            total_cost = Decimal(str(tokens_used)) * cost_per_token

            # Record usage in LLMUsage table
            await self.llm_service.record_usage(
                provider_id=model_config.llm_provider_id,
                model_id=model.id,
                request_type="side_call",
                input_tokens=0,  # We don't track input/output separately for side-calls
                output_tokens=tokens_used,
                total_cost=total_cost,
                user_id=user_id,
                response_time_ms=response_time_ms,
                success=success,
                error_message=error_message,
                request_metadata={"side_call": True},
            )

        except Exception as e:
            logger.error(f"Failed to record side-call usage: {e}")
