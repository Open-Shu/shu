"""
Unified LLM client for Shu RAG Backend.

This module provides a unified interface for interacting with multiple
LLM providers using OpenAI-compatible APIs.
"""

import json
import httpx
import httpcore
import asyncio
import random
from typing import Dict, Any, List, Optional, AsyncGenerator, Union
import logging
from datetime import datetime, timezone
import jmespath
from sqlalchemy.ext.asyncio import AsyncSession

from shu.models.plugin_execution import CallableTool
from shu.services.plugin_execution import build_agent_tools
from shu.services.providers.events import ProviderStreamEvent
from shu.services.providers.adapter_base import ProviderContentDeltaEventResult, ProviderErrorEventResult, ProviderEventResult, ProviderFinalEventResult, ProviderReasoningDeltaEventResult, ProviderToolCallEventResult, get_adapter_from_provider

from ..models.llm_provider import LLMProvider
from ..services.chat_types import ChatContext, ChatMessage
from ..core.exceptions import (
    LLMProviderError, LLMConfigurationError, LLMRateLimitError,
    LLMTimeoutError, LLMAuthenticationError
)
from ..core.config import get_settings_instance
from .param_mapping import build_provider_params
from shu.services.providers.parameter_definitions import serialize_parameter_mapping
from ..utils.path_access import DotPath


logger = logging.getLogger(__name__)


class LLMResponse:
    """Response object for LLM completions."""

    def __init__(
        self,
        content: str,
        model: str,
        provider: str,
        usage: Dict[str, int],
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.content = content
        self.model = model
        self.provider = provider
        self.usage = usage
        self.metadata = metadata or {}
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary."""
        return {
            "content": self.content,
            "model": self.model,
            "provider": self.provider,
            "usage": self.usage,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat()
        }


class UnifiedLLMClient:
    """Universal LLM client supporting OpenAI-compatible APIs."""

    def __init__(self, db_session: AsyncSession, provider: LLMProvider, conversation_owner_id: Optional[str] = None):

        self.provider = provider
        self.conversation_owner_id = conversation_owner_id
        self.db_session = db_session
        self.provider_adapter = get_adapter_from_provider(db_session, provider, self.conversation_owner_id)

        headers = self._build_client_headers()

        # Always use provider.api_endpoint as base URL (no special-casing)
        self.deployment_name = None

        # Use globally configured LLM timeouts
        try:
            from ..core.config import get_settings_instance
            _settings = get_settings_instance()
            self._llm_timeout = float(getattr(_settings, "llm_global_timeout", 30))
            self._llm_stream_read_timeout = float(getattr(_settings, "llm_streaming_read_timeout", 120))
        except Exception:
            self._llm_timeout = 30.0
            self._llm_stream_read_timeout = 120.0

        self.client = httpx.AsyncClient(
            base_url=self._apply_override("get_api_base_url", self.provider_adapter.get_api_base_url()),
            headers=headers,
            timeout=httpx.Timeout(connect=self._llm_timeout, read=self._llm_stream_read_timeout, write=self._llm_timeout, pool=self._llm_timeout),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)
        )

        # Retry configuration
        self._max_attempts = 3  # total attempts including initial request
        self._retry_base_delay = 0.5  # seconds
        self._retry_max_delay = 4.0  # seconds

        logger.info(f"Initialized LLM client for provider: {provider.name} ({provider.provider_type})")

    async def _build_tool_context(self, payload: Dict[str, Any], tools_enabled: bool) -> Dict[str, Any]:
        if not tools_enabled:
            return payload
        tools: List[CallableTool] = await build_agent_tools(self.db_session)
        return await self.provider_adapter.inject_tool_payload(tools, payload)

    def _build_client_headers(self):
        """
        Merge authorization headers with default client headers.
        """
        headers = {"Content-Type": "application/json"}
        auth_def = self.provider_adapter.get_authorization_header()
        if isinstance(auth_def, dict):
            header_templates = auth_def.get("headers") or {}
            for k, v in header_templates.items():
                headers[k] = str(v)
        return headers

    def _config_override(self, name: str) -> Optional[str]:
        cfg = self.provider.config if isinstance(self.provider.config, dict) else {}
        val = cfg.get(name) if isinstance(cfg, dict) else None
        return val if isinstance(val, str) else None

    def _apply_override(self, name: str, value: Any, format_kwargs: Optional[Dict[str, Any]] = None) -> Any:
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
        """
        Build an httpx.Timeout ensuring streaming read windows never shrink below the configured streaming timeout.

        Connect/write/pool use the caller-provided timeout (or fall back to global defaults).
        """
        base_timeout = request_timeout or self._llm_timeout
        if stream:
            read_timeout = max(request_timeout or 0.0, self._llm_stream_read_timeout)
        else:
            read_timeout = base_timeout

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
        model_overrides: Optional[Dict[str, Any]] = None,
        llm_params: Optional[Dict[str, Any]] = None,
        request_timeout: Optional[float] = None,
        return_as_stream: bool = False,
        tools_enabled: bool = False,
    ) -> Union[List[ProviderEventResult], AsyncGenerator[ProviderEventResult, None]]:
        """
        Universal chat completion - works across providers.

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
        start_time = datetime.now(timezone.utc)

        payload = {}

        payload = await self.provider_adapter.inject_model_parameter(model, payload)
        payload = await self.provider_adapter.inject_streaming_parameter(stream, payload)
        payload = await self.provider_adapter.set_messages_in_payload(messages, payload)

        # Merge normalized params and map to provider payload
        payload_patch = build_provider_params(
            serialize_parameter_mapping(self.provider_adapter.get_parameter_mapping()),
            model_overrides,
            llm_params or {}
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
                async def _single_event_stream(events: List[ProviderEventResult]) -> AsyncGenerator[ProviderStreamEvent, None]:
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
        payload: Dict[str, Any],
        model: str,
        request_timeout: Optional[float] = None,
    ) -> List[ProviderEventResult]:
        """
        Execute non-streaming completion with retry/backoff on retryable errors.
        """
        attempt = 0
        while True:
            attempt_start = datetime.now(timezone.utc)
            try:
                return await self._complete_response(payload, model, attempt_start, request_timeout=request_timeout)
            except httpx.HTTPStatusError as e:
                await self._retry_or_raise_http_error(e, model, attempt, has_progress=False)
                attempt += 1

    async def _stream_with_retry(
        self,
        payload: Dict[str, Any],
        model: str,
        request_timeout: Optional[float] = None,
    ) -> AsyncGenerator[ProviderEventResult, None]:
        """
        Wrap streaming response with retry/backoff before any content is emitted.
        """
        attempt = 0
        while True:
            attempt_has_yielded = False
            try:
                async for chunk in self._stream_response(payload, model, datetime.now(timezone.utc), request_timeout=request_timeout):
                    attempt_has_yielded = True
                    yield chunk
                return
            except httpx.HTTPStatusError as e:
                await self._retry_or_raise_http_error(e, model, attempt, has_progress=attempt_has_yielded)
                attempt += 1

    async def _complete_response(
        self,
        payload: Dict[str, Any],
        model: str,
        start_time: datetime,
        request_timeout: Optional[float] = None,
    ) -> List[ProviderEventResult]:
        """Handle non-streaming response."""

        logger.info("Running _complete_response to get provider result.")

        endpoint = self._apply_override("get_chat_endpoint", self.provider_adapter.get_chat_endpoint(), {"model": model, "stream": False})

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
        payload: Dict[str, Any],
        model: str,
        start_time: datetime,
        request_timeout: Optional[float] = None,
    ) -> AsyncGenerator[ProviderEventResult, None]:
        """Handle streaming response."""

        logger.info("Running _stream_response to get provider result.")

        endpoint = self._apply_override("get_chat_endpoint", self.provider_adapter.get_chat_endpoint(), {"model": model, "stream": True})

        provider_type = getattr(self.provider, 'provider_type', 'unknown')
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
                        logger.debug(f"Streaming ended with done=True")
                        break

                    provider_event = await self.provider_adapter.handle_provider_event(chunk)
                    if provider_event:
                        if isinstance(provider_event, (ProviderContentDeltaEventResult, ProviderReasoningDeltaEventResult)):
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
                details={"original_error": str(e), "error_type": type(e).__name__}
            ) from e
        except httpx.ReadTimeout as e:
            # Read timeout during streaming - provider stopped sending data
            logger.error("Streaming read timeout: %s", e, exc_info=True)
            raise LLMTimeoutError(
                "AI provider stopped responding. Please try again.",
                details={"original_error": str(e), "error_type": "ReadTimeout"}
            ) from e
        except httpx.ConnectTimeout as e:
            # Connection timeout - couldn't establish connection
            logger.error("Streaming connect timeout: %s", e, exc_info=True)
            raise LLMTimeoutError(
                "Could not connect to AI provider. Please try again.",
                details={"original_error": str(e), "error_type": "ConnectTimeout"}
            ) from e
        except httpx.TimeoutException as e:
            # Generic timeout
            logger.error("Streaming timeout: %s", e, exc_info=True)
            raise LLMTimeoutError(
                "Request to AI provider timed out. Please try again.",
                details={"original_error": str(e), "error_type": type(e).__name__}
            ) from e
        except httpx.HTTPStatusError:
            # Re-raise HTTP errors so _stream_with_retry can apply retry logic for 5xx,
            # and _retry_or_raise_http_error can convert to appropriate typed exceptions
            raise
        except Exception as e:
            # Unknown error - preserve original for debugging
            logger.error("Encountered streaming error: %s (type: %s)", e, type(e).__name__, exc_info=True)
            # Show details only in development environment
            settings = get_settings_instance()
            if settings.environment == "development":
                user_message = f"Unexpected error from AI provider: {type(e).__name__}: {e}"
            else:
                user_message = "An unexpected error occurred with the AI provider. Please try again."
            raise LLMProviderError(
                user_message,
                details={"original_error": str(e), "error_type": type(e).__name__}
            ) from e

    def _should_retry_http_error(self, status_code: Optional[int], attempt: int) -> bool:
        """Return True if the HTTP error is retryable for the current attempt."""
        if status_code is None:
            return False
        if status_code < 500 or status_code >= 600:
            return False
        # attempt is zero-indexed; allow retries while total attempts stay within the configured cutoff
        return (attempt + 1) < self._max_attempts

    def _get_retry_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with decorrelated jitter."""
        exponential = min(self._retry_base_delay * (2 ** attempt), self._retry_max_delay)
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

    def _log_retry(self, details: Dict[str, Any], attempt: int, delay: float) -> None:
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
            body_str
        )

    def _extract_http_error_details(self, e: httpx.HTTPStatusError, model: str) -> Dict[str, Any]:
        """Normalize useful fields from an HTTP error response."""
        status_code = e.response.status_code if e.response is not None else None
        request_id = e.response.headers.get("x-request-id") if e.response is not None else None
        endpoint = str(e.request.url) if getattr(e, "request", None) is not None else None

        body_text = None
        provider_msg = None
        provider_type = None
        provider_code = None
        body: Any = None

        if e.response is not None:
            try:
                body_text = e.response.text
            except Exception:
                body_text = None
            if body_text:
                # TODO: We should extend the providers to contain the error path format. Right now we are just guessing
                #       where we can find the details.
                try:
                    body_json = e.response.json()
                    if isinstance(body_json, dict):
                        body = body_json
                        error_section = body_json.get("error")
                        if isinstance(error_section, dict):
                            provider_msg = (
                                error_section.get("message")
                                or error_section.get("detail")
                                or error_section.get("error")
                                or error_section.get("status")
                            )
                            provider_type = (
                                error_section.get("type")
                                or error_section.get("status")
                                or error_section.get("reason")
                            )
                            provider_code = error_section.get("code") or error_section.get("status")
                        elif isinstance(error_section, list) and error_section:
                            first_error = error_section[0]
                            if isinstance(first_error, dict):
                                provider_msg = (
                                    first_error.get("message")
                                    or first_error.get("detail")
                                    or first_error.get("error")
                                )
                            provider_type = (
                                first_error.get("type")
                                or first_error.get("status")
                                or first_error.get("reason")
                            )
                            provider_code = first_error.get("code") or first_error.get("status")
                        elif isinstance(error_section, str):
                            provider_msg = error_section
                    if provider_msg is None and isinstance(body_json, dict):
                        provider_msg = (
                            body_json.get("message")
                            or body_json.get("detail")
                            or body_json.get("error_description")
                        )
                    if provider_type is None and isinstance(body_json, dict):
                        provider_type = body_json.get("type") or body_json.get("status")
                    if provider_code is None and isinstance(body_json, dict):
                        provider_code = body_json.get("code")
                    if body is None:
                        body = body_json
                except Exception:
                    body = body_text

        return {
            "status": status_code,
            "endpoint": endpoint,
            "request_id": request_id,
            "provider_message": provider_msg,
            "provider_error_type": provider_type,
            "provider_error_code": provider_code,
            "body": body,
            "model": model,
        }

    def _handle_http_status_error(
        self,
        e: httpx.HTTPStatusError,
        model: str,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Translate HTTP errors into domain errors with rich logging."""
        details = details or self._extract_http_error_details(e, model)
        status_code = details.get("status")
        body_str = self._stringify_error_body(details.get("body"))

        if status_code and 500 <= status_code < 600:
            logger.error(
                "LLM upstream error for provider %s: HTTP %s (request_id=%s, endpoint=%s, body=%s)",
                self.provider.name,
                status_code,
                details.get("request_id"),
                details.get("endpoint"),
                body_str
            )
        elif status_code == 401:
            logger.error(
                "LLM authentication error for provider %s (request_id=%s)",
                self.provider.name,
                details.get("request_id")
            )
            raise LLMAuthenticationError("Invalid API key or authentication failed", details=details) from e
        elif status_code == 429:
            logger.error(
                "LLM rate limit exceeded for provider %s (request_id=%s)",
                self.provider.name,
                details.get("request_id")
            )
            raise LLMRateLimitError("Rate limit exceeded", details=details) from e
        elif status_code == 400:
            logger.error(
                "LLM configuration error (400) for provider %s: %s",
                self.provider.name,
                details.get("provider_message") or str(e)
            )
            raise LLMConfigurationError("Provider rejected request (HTTP 400)", details=details) from e

        if status_code is not None:
            raise LLMProviderError(f"HTTP error {status_code}", details=details) from e
        raise LLMProviderError("HTTP error", details=details) from e

    async def _retry_or_raise_http_error(
        self,
        error: httpx.HTTPStatusError,
        model: str,
        attempt: int,
        has_progress: bool
    ) -> None:
        """
        Decide whether to retry a failed HTTP call or raise immediately.

        Args:
            error: The original httpx HTTPStatusError.
            model: Model identifier for logging details.
            attempt: Zero-based attempt index.
            has_progress: True if any data has already been yielded to the caller.
        """
        details = self._extract_http_error_details(error, model)
        if has_progress or not self._should_retry_http_error(details["status"], attempt):
            self._handle_http_status_error(error, model, details)

        delay = self._get_retry_delay(attempt)
        self._log_retry(details, attempt, delay)
        await asyncio.sleep(delay)

    async def discover_available_models(self) -> List[Dict[str, Any]]:
        """
        Discover available models from the provider's API.

        Returns:
            List of model dictionaries with model information
        """
        try:
            endpoint = self._apply_override("get_models_endpoint", self.provider_adapter.get_models_endpoint())

            response = await self.client.get(endpoint)

            response.raise_for_status()
            payload = response.json()

            model_information_path = self._apply_override("get_model_information_path", self.provider_adapter.get_model_information_path())

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
            logger.error(
                f"HTTP error during model discovery for provider {self.provider.name}: {status_code}"
            )
            raise LLMProviderError("Model discovery failed (HTTP error)", details=details)


    async def validate_connection(self) -> bool:
        """Validate provider connection and authentication."""
        try:
            # First try to discover models (lighter operation)
            try:
                models = await self.discover_available_models()
                if models:
                    return True
            except:
                pass  # Fall back to chat completion test

            # Fallback: Try a simple completion to test the connection
            test_messages = ChatContext(
                system_prompt=None,
                messages=[ChatMessage(id=None, role="user", content="Hello", created_at=None, attachments=[], metadata=None)]
            )

            # Use the first available model for testing
            if not self.provider.models:
                logger.warning(f"No models configured for provider {self.provider.name}")
                return False

            test_model = self.provider.models[0].model_name

            response = await self.chat_completion(
                messages=test_messages,
                model=test_model,
                stream=False,
                model_overrides=None,
                llm_params=None
            )

            return isinstance(response, ProviderStreamEvent) and response.content is not None

        except Exception as e:
            logger.error(f"Connection validation failed for provider {self.provider.name}: {e}")
            return False

    # --- Local provider implementation for tests/offline ---
    def _local_stream(self, payload: Dict[str, Any], model: str, start_time: datetime) -> AsyncGenerator[ProviderEventResult, None]:
        async def gen():
            input_path = self._apply_override("get_message_input_path", "messages")
            messages = DotPath.get(payload, input_path, default=payload.get("messages", []))
            last_user = next((m["content"] for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"), "")
            content = f"Echo: {last_user}" if last_user else "Echo: (no input)"
            for i in range(0, len(content), max(1, len(content)//3)):
                chunk = content[i:i+max(1, len(content)//3)]
                yield ProviderContentDeltaEventResult(content=chunk)
                await asyncio.sleep(0)  # yield control
            yield ProviderFinalEventResult(content=content)
        return gen()

    async def close(self):
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
        return f"<UnifiedLLMClient(provider='{self.provider.name}', type='{self.provider.provider_type}')>"
