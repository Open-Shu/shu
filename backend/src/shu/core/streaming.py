"""Shared SSE stream generator utility for Shu RAG Backend.

This module provides a reusable async generator for Server-Sent Events (SSE)
streaming, with robust error handling, optional error sanitization, and
correlation ID support. It is used by both the chat and experience streaming
endpoints.
"""

import json
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any

from .logging import get_logger

logger = get_logger(__name__)


def sanitize_stream_error_message(error_content: str | None) -> str:
    """Sanitize error messages for SSE streams while preserving actionable errors.

    Provides selective error sanitization so that user-actionable errors
    (rate limits, timeouts, service unavailability) are passed through
    transparently, while all other errors (API keys, auth, config, DB, etc.)
    are replaced with a generic message to avoid leaking internal details.

    Args:
        error_content: The original error message from the provider or backend.

    Returns:
        Either the original error message (for allowed errors) or a sanitized
        generic message.

    """
    if not error_content:
        return "The request failed. You may want to try another model."

    error_lower = error_content.lower()

    # Preserve user-actionable error types
    if "rate limit" in error_lower or "too many requests" in error_lower:
        return error_content

    if "timeout" in error_lower or "timed out" in error_lower:
        return error_content

    if "service unavailable" in error_lower or "temporarily unavailable" in error_lower:
        return error_content

    # Sanitize all other errors (API keys, auth, config, DB, malformed requests, etc.)
    return "The request failed. You may want to try another model."


async def create_sse_stream_generator(
    event_generator: AsyncGenerator[Any, None],
    error_context: str = "streaming",
    error_sanitizer: Callable[[str | None], str] | None = sanitize_stream_error_message,
    include_correlation_id: bool = False,
    error_code: str = "STREAM_ERROR",
) -> AsyncGenerator[str, None]:
    r"""Wrap an async event generator with robust error handling for SSE streaming.

    Iterates over the supplied ``event_generator``, serialises each event as a
    JSON SSE payload, and yields the result.  Two distinct error-handling paths
    are implemented:

    **Point A -- in-stream error events:**
    If an individual event has ``type == "error"`` and an ``error_sanitizer``
    callable is provided, the sanitizer is applied to the event's ``content``
    attribute *before* serialisation.  Serialisation failures for individual
    events are logged and skipped so the stream can continue.

    **Point B -- catch-all exception handler:**
    If the underlying generator raises an unexpected exception, a generic error
    payload is constructed and yielded.  A ``correlation_id`` is included in both
    the log output and the client payload when ``include_correlation_id`` is
    ``True``.  ``GeneratorExit`` (client disconnect) is handled gracefully.

    A ``data: [DONE]\\n\\n`` marker is always emitted as the final event.

    Args:
        event_generator: Async generator that yields events with a ``to_dict()``
            method.
        error_context: Human-readable context for log messages (e.g.
            ``"send_message"``, ``"experience_execution"``).
        error_sanitizer: Callable to transform the ``content`` field of
            error-type events before serialisation (Point A only).  Defaults to
            ``sanitize_stream_error_message`` which preserves rate-limit,
            timeout, and service-unavailable errors while replacing all others
            with a generic message.  Pass ``None`` to disable sanitisation.
        include_correlation_id: When ``True``, generates a UUID correlation ID
            and includes it in catch-all error payloads and log messages.
        error_code: Error code string included in catch-all error payloads.

    Yields:
        SSE-formatted ``data: {json}\\n\\n`` strings.

    """
    try:
        # Point A: iterate events and serialize
        async for event in event_generator:
            try:
                # Apply error sanitizer to error-type events if provided
                if error_sanitizer is not None and getattr(event, "type", None) == "error":
                    event.content = error_sanitizer(event.content)

                payload = event.to_dict()
                yield f"data: {json.dumps(payload)}\n\n"
            except Exception:
                logger.exception(f"Error serializing event during {error_context}")
                # Continue to next event rather than breaking the stream
                continue
    except GeneratorExit:
        # Client disconnected - log but don't treat as error
        logger.info(f"Client disconnected from {error_context} stream")
    except Exception:
        # Point B: catch-all exception handler for stream-level failures
        correlation_id = str(uuid.uuid4()) if include_correlation_id else None

        # Build log extras
        log_extra: dict[str, Any] = {}
        if correlation_id is not None:
            log_extra["correlation_id"] = correlation_id

        # Log full exception details server-side for debugging
        logger.exception(f"Streaming error during {error_context}", extra=log_extra)

        # Construct sanitized error payload for the client
        error_payload: dict[str, Any] = {
            "type": "error",
            "code": error_code,
            "message": "An internal streaming error occurred",
        }
        if correlation_id is not None:
            error_payload["id"] = correlation_id

        try:
            yield f"data: {json.dumps(error_payload)}\n\n"
        except Exception:
            logger.exception(f"Failed to send error event to client during {error_context}")
    finally:
        # Always send DONE marker to properly close the stream
        try:
            yield "data: [DONE]\n\n"
        except Exception:
            logger.debug(f"Could not send DONE marker during {error_context} - connection likely closed")
