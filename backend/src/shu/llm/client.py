"""Unified LLM client for Shu RAG Backend.

This module provides a unified interface for interacting with multiple
LLM providers using OpenAI-compatible APIs.
"""

import asyncio
import hashlib
import json
import logging
import random
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpcore
import httpx
import jmespath
from sqlalchemy.ext.asyncio import AsyncSession

from shu.models.plugin_execution import CallableTool
from shu.services.error_sanitization import ErrorSanitizer, SanitizedError
from shu.services.plugin_execution import build_agent_tools
from shu.services.providers.adapter_base import (
    ProviderContentDeltaEventResult,
    ProviderErrorEventResult,
    ProviderEventResult,
    ProviderFinalEventResult,
    ProviderReasoningDeltaEventResult,
    get_adapter_from_provider,
)
from shu.services.providers.events import ProviderStreamEvent
from shu.services.providers.parameter_definitions import serialize_parameter_mapping

from ..core.config import get_settings_instance
from ..core.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from ..models.llm_provider import LLMProvider
from ..services.chat_types import ChatContext, ChatMessage
from ..utils.path_access import DotPath
from .param_mapping import build_provider_params

logger = logging.getLogger(__name__)


@dataclass
class RetryState:
    """State tracking for retry logic to detect infinite loops."""

    attempts: int = 0
    last_error_hash: str | None = None
    identical_error_count: int = 0
    max_attempts: int = 3

    def _compute_error_hash(self, error: Exception) -> str:
        """Compute a hash of the error to detect identical errors.

        Args:
            error: The exception to hash

        Returns:
            Hash string representing the error

        """
        # Create a string representation of the error
        error_str = f"{type(error).__name__}:{error!s}"

        # For HTTP errors, include status code and response body
        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code if error.response else None
            try:
                body = error.response.text if error.response else ""
            except Exception:
                body = ""
            error_str = f"{error_str}:status={status_code}:body={body[:200]}"

        # Compute hash, we need this hashing to be fast, not secure
        return hashlib.md5(error_str.encode()).hexdigest()  # noqa: S324 # nosec

    def record_error(self, error: Exception) -> None:
        """Record an error attempt and track identical errors.

        Args:
            error: The exception that occurred

        """
        self.attempts += 1
        error_hash = self._compute_error_hash(error)

        if error_hash == self.last_error_hash:
            self.identical_error_count += 1
        else:
            self.identical_error_count = 1
            self.last_error_hash = error_hash

    def should_retry(self, error: Exception) -> bool:
        """Determine if retry should occur based on error type and history.

        Returns False if:
        - Max attempts reached
        - Same error repeated 3 times (infinite loop detection)
        - Error is non-retryable (4xx except 429)

        Args:
            error: The exception to evaluate

        Returns:
            True if retry should occur, False otherwise

        """
        # Check if max attempts reached
        if self.attempts >= self.max_attempts:
            return False

        # Check for infinite loop (same error 3 times)
        if self.is_infinite_loop():
            return False

        # Check if error is non-retryable
        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code if error.response else None
            if status_code:
                # Non-retryable 4xx errors (except 429 rate limit)
                if 400 <= status_code < 500 and status_code != 429:
                    return False

        return True

    def is_infinite_loop(self) -> bool:
        """Check if we're in an infinite loop (same error 3+ times).

        Returns:
            True if infinite loop detected, False otherwise

        """
        return self.identical_error_count >= 3


class LLMResponse:
    """Response object for LLM completions."""

    def __init__(
        self,
        content: str,
        model: str,
        provider: str,
        usage: dict[str, int],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.content = content
        self.model = model
        self.provider = provider
        self.usage = usage
        self.metadata = metadata or {}
        self.timestamp = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """Convert response to dictionary."""
        return {
            "content": self.content,
            "model": self.model,
            "provider": self.provider,
            "usage": self.usage,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }


class UnifiedLLMClient:
    """Universal LLM client supporting OpenAI-compatible APIs."""

    def __init__(
        self,
        db_session: AsyncSession,
        provider: LLMProvider,
        conversation_owner_id: str | None = None,
        settings: Any | None = None,
    ) -> None:
        self.provider = provider
        self.conversation_owner_id = conversation_owner_id
        self.db_session = db_session
        self.provider_adapter = get_adapter_from_provider(db_session, provider, self.conversation_owner_id)

        # Inject settings instance for testability
        self.settings = settings if settings is not None else get_settings_instance()

        headers = self._build_client_headers()

        # Always use provider.api_endpoint as base URL (no special-casing)
        self.deployment_name = None

        # Use globally configured LLM timeouts
        try:
            self._llm_timeout = float(getattr(self.settings, "llm_global_timeout", 30))
            self._llm_stream_read_timeout = float(getattr(self.settings, "llm_streaming_read_timeout", 120))
        except Exception:
            self._llm_timeout = 30.0
            self._llm_stream_read_timeout = 120.0

        self.client = httpx.AsyncClient(
            base_url=self._apply_override("get_api_base_url", self.provider_adapter.get_api_base_url()),
            headers=headers,
            timeout=httpx.Timeout(
                connect=self._llm_timeout,
                read=self._llm_stream_read_timeout,
                write=self._llm_timeout,
                pool=self._llm_timeout,
            ),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

        # Retry configuration
        self._max_attempts = 3  # total attempts including initial request
        self._retry_base_delay = 0.5  # seconds
        self._retry_max_delay = 4.0  # seconds

        logger.info(f"Initialized LLM client for provider: {provider.name} ({provider.provider_type})")

    async def _build_tool_context(self, payload: dict[str, Any], tools_enabled: bool) -> dict[str, Any]:
        if not tools_enabled:
            return payload
        tools: list[CallableTool] = await build_agent_tools(self.db_session)
        return await self.provider_adapter.inject_tool_payload(tools, payload)

    def _build_client_headers(self):
        """Merge authorization headers with default client headers."""
        headers = {"Content-Type": "application/json"}
        auth_def = self.provider_adapter.get_authorization_header()
        if isinstance(auth_def, dict):
            header_templates = auth_def.get("headers") or {}
            for k, v in header_templates.items():
                headers[k] = str(v)
        return headers

    def _config_override(self, name: str) -> str | None:
        cfg = self.provider.config if isinstance(self.provider.config, dict) else {}
        val = cfg.get(name) if isinstance(cfg, dict) else None
        return val if isinstance(val, str) else None

    def _apply_override(self, name: str, value: Any, format_kwargs: dict[str, Any] | None = None) -> Any:
        override = self._config_override(name)
        candidate = override if override is not None else value

        fmt_kwargs = dict(format_kwargs or {})
        try:
            fmt_kwargs = self.provider_adapter.inject_override_parameters(fmt_kwargs)
        except Exception:
            fmt_kwargs = fmt_kwargs

        if isinstance(candidate, str) and fmt_kwargs:
            try:
                candidate = candidate.format(**fmt_kwargs)
            except Exception:
                # Silently ignore format failures to avoid breaking requests on bad overrides
                pass

        return candidate

    def _build_timeout(self, request_timeout: float, stream: bool) -> httpx.Timeout:
        """Build an httpx.Timeout ensuring streaming read windows never shrink below the configured streaming timeout.

        Connect/write/pool use the caller-provided timeout (or fall back to global defaults).
        """
        base_timeout = request_timeout or self._llm_timeout
        read_timeout = max(request_timeout or 0.0, self._llm_stream_read_timeout) if stream else base_timeout

        return httpx.Timeout(
            connect=base_timeout,
            read=read_timeout,
            write=base_timeout,
            pool=base_timeout,
        )

    async def chat_completion(
        self,
        messages: ChatContext,
        model: str,
        stream: bool = False,
        model_overrides: dict[str, Any] | None = None,
        llm_params: dict[str, Any] | None = None,
        request_timeout: float | None = None,
        return_as_stream: bool = False,
        tools_enabled: bool = False,
    ) -> list[ProviderEventResult] | AsyncGenerator[ProviderEventResult, None]:
        """Universal chat completion - works across providers.

        Args:
            messages: ChatContext containing system prompt and ChatMessages
            model: Model name to use
            stream: Whether to stream the response
            temperature: Response creativity (0.0 to 2.0)
            max_tokens: Maximum tokens to generate
            **kwargs: Additional provider-specific parameters

        Returns:
            ProviderStreamEvent object or async generator for streaming

        """
        start_time = datetime.now(UTC)

        payload = {}

        payload = await self.provider_adapter.inject_model_parameter(model, payload)
        payload = await self.provider_adapter.inject_streaming_parameter(stream, payload)
        payload = await self.provider_adapter.set_messages_in_payload(messages, payload)

        # Merge normalized params and map to provider payload
        payload_patch = build_provider_params(
            serialize_parameter_mapping(self.provider_adapter.get_parameter_mapping()),
            model_overrides,
            llm_params or {},
        )
        payload.update(payload_patch)

        payload = await self._build_tool_context(payload, tools_enabled)

        payload = await self.provider_adapter.post_process_payload(payload)

        # TODO: Remove this when we factor out all providers into their own classes.
        # Local provider short-circuit for tests/offline usage
        if self.provider.provider_type == "local":
            try:
                return self._local_stream(payload, model, start_time)
            except Exception as e:
                logger.error(f"Local provider error: {e}")
                raise LLMProviderError(str(e))

        try:
            if stream:
                return self._stream_with_retry(payload, model, request_timeout=request_timeout)

            responses = await self._complete_with_retry(payload, model, request_timeout=request_timeout)

            if return_as_stream:

                async def _single_event_stream(
                    events: list[ProviderEventResult],
                ) -> AsyncGenerator[ProviderStreamEvent, None]:
                    for event in events:
                        yield event

                return _single_event_stream(responses)

            return responses

        except httpx.TimeoutException as e:
            logger.error(f"LLM request timeout for provider {self.provider.name}: {e}")
            raise LLMTimeoutError(f"Request timeout: {e}")
        except httpx.HTTPStatusError as e:
            self._handle_http_status_error(e, model)
        except Exception as e:
            details = getattr(e, "details", None)
            logger.error(f"Unexpected LLM error for provider {self.provider.name}: {e} - details: {details}")
            raise LLMProviderError(f"Unexpected error: {e}", details=details)

    async def _complete_with_retry(
        self,
        payload: dict[str, Any],
        model: str,
        request_timeout: float | None = None,
    ) -> list[ProviderEventResult]:
        """Execute non-streaming completion with retry/backoff on retryable errors."""
        retry_state = RetryState(max_attempts=self._max_attempts)

        while True:
            attempt_start = datetime.now(UTC)
            try:
                return await self._complete_response(payload, model, attempt_start, request_timeout=request_timeout)
            except httpx.HTTPStatusError as e:
                await self._retry_or_raise_http_error(e, model, retry_state, has_progress=False)
                retry_state.record_error(e)

    async def _stream_with_retry(
        self,
        payload: dict[str, Any],
        model: str,
        request_timeout: float | None = None,
    ) -> AsyncGenerator[ProviderEventResult, None]:
        """Wrap streaming response with retry/backoff before any content is emitted."""
        retry_state = RetryState(max_attempts=self._max_attempts)

        while True:
            attempt_has_yielded = False
            try:
                async for chunk in self._stream_response(
                    payload, model, datetime.now(UTC), request_timeout=request_timeout
                ):
                    attempt_has_yielded = True
                    yield chunk
                return
            except httpx.HTTPStatusError as e:
                await self._retry_or_raise_http_error(e, model, retry_state, has_progress=attempt_has_yielded)
                retry_state.record_error(e)

    async def _complete_response(
        self,
        payload: dict[str, Any],
        model: str,
        start_time: datetime,
        request_timeout: float | None = None,
    ) -> list[ProviderEventResult]:
        """Handle non-streaming response."""
        logger.info("Running _complete_response to get provider result.")

        endpoint = self._apply_override(
            "get_chat_endpoint",
            self.provider_adapter.get_chat_endpoint(),
            {"model": model, "stream": False},
        )

        post_kwargs = {}
        if request_timeout is not None:
            post_kwargs["timeout"] = self._build_timeout(request_timeout, stream=False)
        logger.debug(
            "llm.request provider=%s endpoint=%s payload=%s",
            getattr(self.provider, "name", "unknown"),
            endpoint,
            str(payload)[:4000],
        )

        response = await self.client.post(endpoint, json=payload, **post_kwargs)
        response.raise_for_status()

        response_data = response.json()

        return await self.provider_adapter.handle_provider_completion(response_data)

    async def _stream_response(
        self,
        payload: dict[str, Any],
        model: str,
        start_time: datetime,
        request_timeout: float | None = None,
    ) -> AsyncGenerator[ProviderEventResult, None]:
        """Handle streaming response."""
        logger.info("Running _stream_response to get provider result.")

        endpoint = self._apply_override(
            "get_chat_endpoint",
            self.provider_adapter.get_chat_endpoint(),
            {"model": model, "stream": True},
        )

        provider_type = getattr(self.provider, "provider_type", "unknown")
        logger.debug(f"Streaming configuration for model {model} (provider: {provider_type}):")
        logger.debug(f"  - endpoint: {endpoint}")

        stream_kwargs = {}
        if request_timeout is not None:
            stream_kwargs["timeout"] = self._build_timeout(request_timeout, stream=True)

        final_event: ProviderFinalEventResult = None

        try:
            async with self.client.stream("POST", endpoint, json=payload, **stream_kwargs) as response:
                response.raise_for_status()

                # DEBUG: Log response headers for debugging
                logger.debug(f"Streaming response headers for model {model}: {dict(response.headers)}")

                chunk = {}
                line_count = 0

                async for line in response.aiter_lines():
                    line_count += 1

                    if not line:
                        continue

                    raw = line.strip()
                    if not raw:
                        continue

                    # Ignore SSE event and control lines that are not JSON payloads
                    if raw.startswith("event:"):
                        if line_count <= 5:
                            logger.debug(f"Ignoring SSE event line: {raw}")
                        continue
                    if raw.startswith(":") or raw.startswith("id:") or raw.startswith("retry:"):
                        continue

                    data = raw
                    if data.startswith("data:"):
                        data = data[5:].lstrip()

                    # DEBUG: Log first few lines of raw streaming data
                    if line_count <= 5:
                        logger.debug(f"Raw streaming line {line_count}: {data}")

                    if data in ("[DONE]", "DONE"):
                        logger.debug("Streaming ended with DONE marker")
                        break

                    # Only attempt JSON parse if it looks like JSON
                    if not (data.startswith("{") or data.startswith("[")):
                        if line_count <= 5:
                            logger.debug(f"Skipping non-JSON data line: {data}")
                        continue

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError as e:
                        # Downgrade to debug to avoid log spam for benign non-JSON lines
                        logger.debug(f"Failed to parse JSON chunk: {data[:200]}..., error: {e}")
                        continue

                    if not isinstance(chunk, dict):
                        logger.warning(f"Chunk is not a dict: {type(chunk)}")
                        continue

                    # DEBUG: Log chunk structure
                    if line_count <= 3:
                        logger.debug(f"Chunk structure: {list(chunk.keys())}")

                    if chunk.get("done") is True:
                        logger.debug("Streaming ended with done=True")
                        break

                    provider_event = await self.provider_adapter.handle_provider_event(chunk)
                    if provider_event:
                        if isinstance(
                            provider_event,
                            (ProviderContentDeltaEventResult, ProviderReasoningDeltaEventResult),
                        ):
                            yield provider_event
                        elif isinstance(provider_event, (ProviderFinalEventResult, ProviderErrorEventResult)):
                            final_event = provider_event

            for provider_event in await self.provider_adapter.finalize_provider_events():
                if isinstance(provider_event, (ProviderFinalEventResult, ProviderErrorEventResult)):
                    final_event = provider_event
                    continue
                yield provider_event

            yield final_event

        except (httpcore.RemoteProtocolError, httpx.RemoteProtocolError) as e:
            # Provider closed the stream early - this is often recoverable
            logger.warning("Streaming connection closed early: %s", e, exc_info=True)
            raise LLMProviderError(
                "Connection to AI provider was interrupted",
                details={"original_error": str(e), "error_type": type(e).__name__},
            ) from e
        except httpx.ReadTimeout as e:
            # Read timeout during streaming - provider stopped sending data
            logger.error("Streaming read timeout: %s", e, exc_info=True)
            raise LLMTimeoutError(
                "AI provider stopped responding. Please try again.",
                details={"original_error": str(e), "error_type": "ReadTimeout"},
            ) from e
        except httpx.ConnectTimeout as e:
            # Connection timeout - couldn't establish connection
            logger.error("Streaming connect timeout: %s", e, exc_info=True)
            raise LLMTimeoutError(
                "Could not connect to AI provider. Please try again.",
                details={"original_error": str(e), "error_type": "ConnectTimeout"},
            ) from e
        except httpx.TimeoutException as e:
            # Generic timeout
            logger.error("Streaming timeout: %s", e, exc_info=True)
            raise LLMTimeoutError(
                "Request to AI provider timed out. Please try again.",
                details={"original_error": str(e), "error_type": type(e).__name__},
            ) from e
        except httpx.HTTPStatusError:
            # Re-raise HTTP errors so _stream_with_retry can apply retry logic for 5xx,
            # and _retry_or_raise_http_error can convert to appropriate typed exceptions
            raise
        except Exception as e:
            # Unknown error - preserve original for debugging
            logger.error("Encountered streaming error: %s (type: %s)", e, type(e).__name__, exc_info=True)
            # Show details only in development environment
            if self.settings.environment == "development":
                user_message = f"Unexpected error from AI provider: {type(e).__name__}: {e}"
            else:
                user_message = "An unexpected error occurred with the AI provider. Please try again."
            raise LLMProviderError(
                user_message, details={"original_error": str(e), "error_type": type(e).__name__}
            ) from e

    def _get_retry_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with decorrelated jitter."""
        exponential = min(self._retry_base_delay * (2**attempt), self._retry_max_delay)
        return exponential + random.uniform(0, self._retry_base_delay)

    def _stringify_error_body(self, body: Any) -> str:
        """Convert provider error body to a compact string for logging."""
        if body is None:
            return ""
        if isinstance(body, (dict, list)):
            try:
                return json.dumps(body, separators=(",", ":"))
            except Exception:
                return str(body)
        return str(body)

    def _log_retry(self, details: dict[str, Any], attempt: int, delay: float) -> None:
        """Emit a warning before retrying a request."""
        body_str = self._stringify_error_body(details.get("body"))
        logger.warning(
            "Retrying LLM request for provider %s after HTTP %s (request_id=%s, attempt=%s/%s, delay=%.2fs, body=%s)",
            self.provider.name,
            details.get("status"),
            details.get("request_id"),
            attempt + 1,
            self._max_attempts,
            delay,
            body_str,
        )

    def _extract_http_error_details(self, e: httpx.HTTPStatusError, model: str) -> dict[str, Any]:
        """Normalize useful fields from an HTTP error response.

        Uses ErrorSanitizer to extract structured error information from
        provider responses in a consistent format.

        Args:
            e: The HTTP status error from httpx.
            model: The model name being used.

        Returns:
            Dictionary with normalized error details including:
                - status: HTTP status code
                - endpoint: Request endpoint URL
                - request_id: Provider request ID if available
                - provider_message: Error message from provider
                - provider_error_type: Error type/category from provider
                - provider_error_code: Error code from provider
                - body: Raw response body
                - model: Model name

        """
        status_code = e.response.status_code if e.response is not None else None
        request_id = e.response.headers.get("x-request-id") if e.response is not None else None
        endpoint = str(e.request.url) if getattr(e, "request", None) is not None else None

        # Use ErrorSanitizer to extract provider error details
        provider_error: dict[str, Any] = {}
        if e.response is not None:
            provider_error = ErrorSanitizer.extract_provider_error(e.response)

        return {
            "status": status_code,
            "endpoint": endpoint,
            "request_id": request_id,
            "provider_message": provider_error.get("message"),
            "provider_error_type": provider_error.get("error_type"),
            "provider_error_code": provider_error.get("error_code"),
            "body": provider_error.get("raw_body"),
            "model": model,
        }

    def _handle_http_status_error(
        self, e: httpx.HTTPStatusError, model: str, details: dict[str, Any] | None = None
    ) -> None:
        """Translate HTTP errors into domain errors with rich logging and guidance.

        Uses ErrorSanitizer to provide environment-aware error messages.
        Suggestions are stored in the details dict for optional display by
        endpoints that want to show them (e.g., /test endpoint), but are NOT
        included in the exception message to keep chat errors user-friendly.

        Args:
            e: The HTTP status error from httpx.
            model: The model name being used.
            details: Pre-extracted error details (optional).

        Raises:
            LLMAuthenticationError: For 401 authentication errors.
            LLMRateLimitError: For 429 rate limit errors.
            LLMConfigurationError: For 400 bad request errors.
            LLMProviderError: For other HTTP errors.

        """
        details = details or self._extract_http_error_details(e, model)
        status_code = details.get("status")
        body_str = self._stringify_error_body(details.get("body"))

        # Get environment for error sanitization
        environment = getattr(self.settings, "environment", "production")

        # Use ErrorSanitizer to get sanitized error with guidance
        sanitized = ErrorSanitizer.sanitize_error(details)

        # Always log full error details server-side (Requirement 4.5)
        if status_code and 500 <= status_code < 600:
            logger.error(
                "LLM upstream error for provider %s: HTTP %s (request_id=%s, endpoint=%s, body=%s)",
                self.provider.name,
                status_code,
                details.get("request_id"),
                details.get("endpoint"),
                body_str,
            )
        elif status_code == 401:
            logger.error(
                "LLM authentication error for provider %s (request_id=%s, provider_message=%s)",
                self.provider.name,
                details.get("request_id"),
                details.get("provider_message"),
            )
            # Use simple sanitized message - suggestions are in details for /test endpoint
            raise LLMAuthenticationError(
                sanitized.message,
                details=self._build_error_details(details, sanitized, environment),
            ) from e
        elif status_code == 429:
            logger.error(
                "LLM rate limit exceeded for provider %s (request_id=%s, provider_message=%s)",
                self.provider.name,
                details.get("request_id"),
                details.get("provider_message"),
            )
            # Use simple sanitized message - suggestions are in details for /test endpoint
            raise LLMRateLimitError(
                sanitized.message,
                details=self._build_error_details(details, sanitized, environment),
            ) from e
        elif status_code == 400:
            logger.error(
                "LLM configuration error (400) for provider %s: %s (request_id=%s)",
                self.provider.name,
                details.get("provider_message") or str(e),
                details.get("request_id"),
            )
            # Use simple sanitized message - suggestions are in details for /test endpoint
            raise LLMConfigurationError(
                sanitized.message,
                details=self._build_error_details(details, sanitized, environment),
            ) from e

        # Log other errors
        logger.error(
            "LLM HTTP error for provider %s: HTTP %s (request_id=%s, endpoint=%s, body=%s)",
            self.provider.name,
            status_code,
            details.get("request_id"),
            details.get("endpoint"),
            body_str,
        )

        if status_code is not None:
            # Use simple sanitized message - suggestions are in details for /test endpoint
            raise LLMProviderError(
                sanitized.message,
                details=self._build_error_details(details, sanitized, environment),
            ) from e
        raise LLMProviderError("HTTP error", details=self._build_error_details(details, sanitized, environment)) from e

    def _build_error_details(
        self, raw_details: dict[str, Any], sanitized: SanitizedError, environment: str
    ) -> dict[str, Any]:
        """Build error details dict based on environment.

        In development mode, includes full details for debugging.
        In production mode, includes only safe information.

        Args:
            raw_details: Raw error details from provider.
            sanitized: Sanitized error from ErrorSanitizer.
            environment: Current environment ('development' or 'production').

        Returns:
            Dictionary with error details appropriate for the environment.

        """
        is_development = environment.lower() in ("development", "dev", "local")

        result: dict[str, Any] = {
            "status_code": raw_details.get("status"),
            "error_type": sanitized.error_type,
            "error_code": sanitized.error_code,
            "suggestions": sanitized.suggestions,
        }

        if is_development:
            # Include full details in development (Requirement 4.3)
            result["endpoint"] = raw_details.get("endpoint")
            result["request_id"] = raw_details.get("request_id")
            result["provider_message"] = raw_details.get("provider_message")
            result["model"] = raw_details.get("model")
            result["body"] = raw_details.get("body")
        else:
            # Sanitize details in production (Requirement 4.4)
            if raw_details.get("provider_message"):
                result["provider_message"] = ErrorSanitizer.sanitize_string(raw_details.get("provider_message"))
            result["model"] = raw_details.get("model")

        return result

    async def _retry_or_raise_http_error(
        self, error: httpx.HTTPStatusError, model: str, retry_state: RetryState, has_progress: bool
    ) -> None:
        """Decide whether to retry a failed HTTP call or raise immediately.

        Args:
            error: The original httpx HTTPStatusError.
            model: Model identifier for logging details.
            retry_state: RetryState tracking retry attempts and error history.
            has_progress: True if any data has already been yielded to the caller.

        """
        details = self._extract_http_error_details(error, model)

        # Check for capability mismatch errors (vision, tools, etc.)
        if self._is_capability_mismatch_error(details):
            logger.warning(
                "Capability mismatch detected for provider %s: %s (not retrying)",
                self.provider.name,
                details.get("provider_message", ""),
            )
            self._handle_http_status_error(error, model, details)

        # Check if we should retry using RetryState
        if has_progress or not retry_state.should_retry(error):
            # Log infinite loop detection
            if retry_state.is_infinite_loop():
                logger.warning(
                    "Infinite loop detected for provider %s: same error repeated %d times (breaking retry loop)",
                    self.provider.name,
                    retry_state.identical_error_count,
                )
            self._handle_http_status_error(error, model, details)

        delay = self._get_retry_delay(retry_state.attempts)
        self._log_retry(details, retry_state.attempts, delay)
        await asyncio.sleep(delay)

    def _is_capability_mismatch_error(self, details: dict[str, Any]) -> bool:
        """Detect if an error is due to a capability mismatch (vision, tools, etc.).

        TODO: This should be moved to provider adapters. Each adapter should implement
        a method like `is_capability_mismatch_error(error_details)` since providers
        know their own error formats and capabilities better than generic pattern matching.
        This would be more reliable and maintainable than guessing from error messages.

        Args:
            details: Error details extracted from HTTP response.

        Returns:
            True if error is a capability mismatch, False otherwise.

        """
        provider_message = (details.get("provider_message") or "").lower()
        provider_error_type = (details.get("provider_error_type") or "").lower()
        provider_error_code = (details.get("provider_error_code") or "").lower()

        # Common patterns for vision capability mismatches
        vision_patterns = [
            "vision",
            "image",
            "multimodal",
            "does not support images",
            "cannot process images",
            "image_url not supported",
        ]

        # Common patterns for tool calling mismatches
        tool_patterns = [
            "tool",
            "function",
            "function_call",
            "tools not supported",
            "function calling not supported",
        ]

        # Check message, type, and code for capability mismatch patterns
        all_patterns = vision_patterns + tool_patterns
        for pattern in all_patterns:
            if pattern in provider_message or pattern in provider_error_type or pattern in provider_error_code:
                return True

        return False

    async def discover_available_models(self) -> list[dict[str, Any]]:
        """Discover available models from the provider's API.

        Returns:
            List of model dictionaries with model information

        """
        try:
            endpoint = self._apply_override("get_models_endpoint", self.provider_adapter.get_models_endpoint())

            response = await self.client.get(endpoint)

            response.raise_for_status()
            payload = response.json()

            model_information_path = self._apply_override(
                "get_model_information_path", self.provider_adapter.get_model_information_path()
            )

            return jmespath.search(model_information_path, payload)

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code if e.response is not None else None
            try:
                body_text = e.response.text if e.response is not None else None
            except Exception:
                body_text = None
            details = {
                "status": status_code,
                "endpoint": "definition:endpoints.models.path",
                "body": body_text,
            }
            logger.error(f"HTTP error during model discovery for provider {self.provider.name}: {status_code}")
            raise LLMProviderError("Model discovery failed (HTTP error)", details=details)

    async def validate_connection(self) -> bool:
        """Validate provider connection and authentication."""
        try:
            # First try to discover models (lighter operation)
            try:
                models = await self.discover_available_models()
                if models:
                    return True
            except Exception as e:
                logger.debug(
                    "Model discovery failed for provider %s, falling back to chat completion test: %s",
                    self.provider.name,
                    e,
                    exc_info=True,
                )
                pass  # Fall back to chat completion test

            # Fallback: Try a simple completion to test the connection
            test_messages = ChatContext(
                system_prompt=None,
                messages=[
                    ChatMessage(
                        id=None,
                        role="user",
                        content="Hello",
                        created_at=None,
                        attachments=[],
                        metadata=None,
                    )
                ],
            )

            # Use the first available model for testing
            if not self.provider.models:
                logger.warning(f"No models configured for provider {self.provider.name}")
                return False

            test_model = self.provider.models[0].model_name

            response: list[ProviderEventResult] = await self.chat_completion(
                messages=test_messages,
                model=test_model,
                stream=False,
                model_overrides=None,
                llm_params=None,
            )

            # Non-streaming returns List[ProviderEventResult]
            if isinstance(response, list) and len(response) > 0:
                # Check if any event indicates success (has content)
                return any(hasattr(evt, "content") and evt.content is not None for evt in response)
            return False

        except Exception as e:
            logger.error(f"Connection validation failed for provider {self.provider.name}: {e}")
            return False

    # --- Local provider implementation for tests/offline ---
    def _local_stream(
        self, payload: dict[str, Any], model: str, start_time: datetime
    ) -> AsyncGenerator[ProviderEventResult, None]:
        async def gen():
            input_path = self._apply_override("get_message_input_path", "messages")
            messages = DotPath.get(payload, input_path, default=payload.get("messages", []))
            last_user = next(
                (m["content"] for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"),
                "",
            )
            content = f"Echo: {last_user}" if last_user else "Echo: (no input)"
            for i in range(0, len(content), max(1, len(content) // 3)):
                chunk = content[i : i + max(1, len(content) // 3)]
                yield ProviderContentDeltaEventResult(content=chunk)
                await asyncio.sleep(0)  # yield control
            yield ProviderFinalEventResult(content=content)

        return gen()

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    async def _format_content(self, content: Any) -> str:
        """Format content from response data."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Lists are formatted as ordered lists
            return "\n\n" + "\n\n".join([f"[{it + 1}] {item}" for it, item in enumerate(content)])
        if isinstance(content, dict):
            # Dictionaries are formatted as unordered lists
            return "\n\n" + "\n\n".join([f"[{k}] {v}" for k, v in content.items()])
        return str(content)

    def __repr__(self) -> str:
        """Represent as string."""
        return f"<UnifiedLLMClient(provider='{self.provider.name}', type='{self.provider.provider_type}')>"
