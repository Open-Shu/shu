"""
Base Caller Service for Shu RAG Backend.

This module provides a base class for optimized LLM calls for various purposes
like side-calls, OCR processing, and other auxiliary LLM operations.
"""

import logging
import time
import re
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Tuple
from decimal import Decimal
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.model_configuration import ModelConfiguration
from ..services.system_settings_service import SystemSettingsService
from ..services.model_configuration_service import ModelConfigurationService
from ..llm.service import LLMService
from ..core.config import ConfigurationManager
from ..core.exceptions import LLMProviderError
from ..services.chat_types import ChatContext

logger = logging.getLogger(__name__)


class CallerResult:
    """Result of a caller operation (side-call, OCR, etc.)."""

    def __init__(
        self,
        content: str,
        success: bool = True,
        error_message: Optional[str] = None,
        tokens_used: int = 0,
        cost: Optional[Decimal] = None,
        response_time_ms: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.content = content
        self.success = success
        self.error_message = error_message
        self.tokens_used = tokens_used
        self.cost = cost
        self.response_time_ms = response_time_ms
        self.metadata = metadata or {}


# Alias for backward compatibility
SideCallResult = CallerResult


class BaseCallerService(ABC):
    """
    Abstract base class for optimized LLM caller services.
    
    Provides common infrastructure for managing designated model configurations
    stored in system settings and making LLM calls.
    """

    # Subclasses must define this
    SETTING_KEY: str = ""
    REQUEST_TYPE: str = "caller"

    def __init__(self, db: AsyncSession, config_manager: ConfigurationManager):
        self.db = db
        self.config_manager = config_manager
        self.llm_service = LLMService(db)
        self.system_settings_service = SystemSettingsService(db)
        self.model_config_service = ModelConfigurationService(db)

    async def _get_designated_model(self) -> Optional[ModelConfiguration]:
        """
        Get the currently designated model configuration for this caller type.

        Returns:
            ModelConfiguration designated for this caller, or None if not set
        """
        if not self.SETTING_KEY:
            raise NotImplementedError("SETTING_KEY must be defined in subclass")
            
        try:
            setting = await self.system_settings_service.get_setting(self.SETTING_KEY)
            if not setting or not setting.value.get("model_config_id"):
                logger.warning(f"No {self.REQUEST_TYPE} model configured in system settings")
                return None

            model_config_id = setting.value["model_config_id"]

            model_config = await self.model_config_service.get_model_configuration(model_config_id, include_relationships=True)

            if not model_config or not model_config.is_active:
                logger.warning(f"{self.REQUEST_TYPE} model {model_config_id} not found or inactive")
                return None

            return model_config

        except Exception as e:
            logger.error(f"Failed to get {self.REQUEST_TYPE} model: {e}")
            return None

    async def _set_designated_model(self, model_config_id: str, user_id: str) -> bool:
        """
        Set the designated model configuration for this caller type.

        Args:
            model_config_id: ID of the model configuration to designate
            user_id: ID of the user making the change

        Returns:
            True if successful, False otherwise
        """
        if not self.SETTING_KEY:
            raise NotImplementedError("SETTING_KEY must be defined in subclass")
            
        try:
            model_config = await self.model_config_service.get_model_configuration(model_config_id, include_relationships=True)

            if not model_config or not model_config.is_active:
                logger.error(f"Model configuration {model_config_id} not found or inactive")
                return False

            # Allow subclasses to add validation
            validation_error = await self._validate_model_for_designation(model_config)
            if validation_error:
                logger.error(validation_error)
                return False

            await self.system_settings_service.upsert(
                self.SETTING_KEY,
                {
                    "model_config_id": model_config_id,
                    "updated_by": user_id,
                    "updated_at": datetime.utcnow().isoformat(),
                },
            )

            logger.info(f"{self.REQUEST_TYPE} model set to {model_config_id} by user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to set {self.REQUEST_TYPE} model {model_config_id}: {e}")
            return False

    async def _validate_model_for_designation(self, model_config: ModelConfiguration) -> Optional[str]:
        """
        Validate that a model configuration is suitable for this caller type.
        
        Override in subclasses to add specific validation logic (e.g., vision capability).
        
        Returns:
            Error message if validation fails, None if valid
        """
        return None

    async def _clear_designated_model(self, user_id: str) -> bool:
        """
        Clear the designated model configuration for this caller type.

        Args:
            user_id: ID of the user making the change

        Returns:
            True if successful, False otherwise
        """
        if not self.SETTING_KEY:
            raise NotImplementedError("SETTING_KEY must be defined in subclass")
            
        try:
            await self.system_settings_service.delete(self.SETTING_KEY)
            logger.info(f"{self.REQUEST_TYPE} model cleared by user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to clear {self.REQUEST_TYPE} model by user {user_id}: {e}")
            return False

    async def _call(
        self,
        message_sequence: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        user_id: str = "system",
        config_overrides: Optional[Dict] = None,
        timeout_ms: Optional[int] = None,
        model_config: Optional[ModelConfiguration] = None,
    ) -> CallerResult:
        """
        Make an optimized call to the designated LLM.

        Args:
            message_sequence: Chat-style sequence of messages ({"role", "content"})
            system_prompt: Optional system prompt injected ahead of the sequence
            user_id: ID of the user making the request
            config_overrides: Optional configuration overrides for this call
            timeout_ms: Optional timeout override in milliseconds
            model_config: Optional pre-fetched model configuration

        Returns:
            CallerResult with the response or error
        """
        start_time = time.time()

        if not message_sequence:
            raise ValueError("message_sequence must contain at least one message")

        try:
            # Get the designated model configuration if not provided
            if not model_config:
                model_config = await self._get_designated_model()
            if not model_config:
                return CallerResult(
                    content="",
                    success=False,
                    error_message=f"No {self.REQUEST_TYPE} model configured",
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

            return CallerResult(
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
            logger.error(f"{self.REQUEST_TYPE} call failed for user {user_id}: {e}")

            # Record failed usage
            if not model_config:
                model_config = await self._get_designated_model()
            if model_config:
                try:
                    model = await self._find_model_for_config(model_config)
                except LLMProviderError as config_error:
                    logger.error("Unable to resolve %s model %s during failure handling: %s", self.REQUEST_TYPE, model_config.id, config_error)
                except Exception as lookup_error:
                    logger.exception("Unexpected error resolving %s model %s during failure handling: %s", self.REQUEST_TYPE, model_config.id, lookup_error)
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

            return CallerResult(
                content="",
                success=False,
                error_message=str(e),
                response_time_ms=response_time_ms,
            )

    async def redact_sensitive_content(self, content: str) -> str:
        """
        Redact sensitive content from prompts before sending to LLM.

        Args:
            content: Original content that may contain sensitive information

        Returns:
            Content with sensitive information redacted
        """
        try:
            patterns = [
                (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL_REDACTED]"),
                (r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "[PHONE_REDACTED]"),
                (r"\b\d{3}-\d{2}-\d{4}\b", "[SSN_REDACTED]"),
                (r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b", "[CREDIT_CARD_REDACTED]"),
                (r"\b[A-Za-z0-9]{20,}\b", "[API_KEY_REDACTED]"),
                (r'password["\s:]+["\']?([^"\'\s]+)["\']?', 'password="[PASSWORD_REDACTED]"'),
            ]

            redacted_content = content
            for pattern, replacement in patterns:
                redacted_content = re.sub(pattern, replacement, redacted_content, flags=re.IGNORECASE)

            return redacted_content

        except Exception as e:
            logger.error(f"Failed to redact sensitive content: {e}")
            return content

    async def _find_model_for_config(self, model_config: ModelConfiguration):
        """
        Find the LLM model for a model configuration.

        Args:
            model_config: Model configuration

        Returns:
            LLM model

        Raises:
            LLMProviderError: If model not found
        """
        if not model_config.model_name:
            raise LLMProviderError("Model configuration does not specify a model name")

        provider = model_config.llm_provider
        if not provider:
            raise LLMProviderError("Model configuration does not have a provider")

        for model in provider.models:
            if model.model_name == model_config.model_name and model.is_active:
                return model

        raise LLMProviderError(
            f"Model '{model_config.model_name}' not found or inactive "
            f"for provider '{provider.name}'"
        )

    async def _build_sequence_messages(
        self,
        sequence: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        model_config: Optional[ModelConfiguration] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Normalize and redact message sequences provided directly by callers.

        Includes a system prompt if provided; otherwise falls back to the
        model configuration's default prompt when available.
        """
        messages: List[Dict[str, Any]] = []

        if system_prompt and system_prompt.strip():
            system_prompt = system_prompt.strip()
        elif model_config and getattr(model_config, "prompt", None) and getattr(model_config.prompt, "content", None):
            system_prompt = model_config.prompt.content

        for entry in sequence:
            role = entry.get("role")
            content = entry.get("content", "")
            if not role:
                continue
            # Only redact string content
            if isinstance(content, str):
                content = await self.redact_sensitive_content(content)
            messages.append({"role": role, "content": content})

        return system_prompt, messages

    async def _record_usage(
        self,
        model_config: ModelConfiguration,
        model,
        user_id: str,
        tokens_used: int,
        response_time_ms: int,
        success: bool,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Record usage metrics for the caller.

        Args:
            model_config: The model configuration used
            model: The model used
            user_id: ID of the user who made the request
            tokens_used: Number of tokens used
            response_time_ms: Response time in milliseconds
            success: Whether the call was successful
            error_message: Error message if the call failed
        """
        try:
            cost_per_token = Decimal("0.0001")
            total_cost = Decimal(str(tokens_used)) * cost_per_token

            await self.llm_service.record_usage(
                provider_id=model_config.llm_provider_id,
                model_id=model.id,
                request_type=self.REQUEST_TYPE,
                input_tokens=0,
                output_tokens=tokens_used,
                total_cost=total_cost,
                user_id=user_id,
                response_time_ms=response_time_ms,
                success=success,
                error_message=error_message,
                request_metadata={self.REQUEST_TYPE: True},
            )

        except Exception as e:
            logger.error(f"Failed to record {self.REQUEST_TYPE} usage: {e}")
