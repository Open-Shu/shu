"""
Utility functions for Shu RAG Backend.

This module contains utility functions used throughout the application.
"""

import uuid
from datetime import UTC, datetime, timezone
from typing import Any, Dict, Optional

from .knowledge_base_verifier import KnowledgeBaseVerifier
from .tokenization import (
    chars_to_tokens_estimate,
    estimate_tokens,
    estimate_tokens_for_chunks,
    tokens_to_chars_estimate,
)

__all__ = [
    "KnowledgeBaseVerifier",
    "chars_to_tokens_estimate",
    "create_error_response",
    "create_success_response",
    "estimate_tokens",
    "estimate_tokens_for_chunks",
    "tokens_to_chars_estimate",
]


def create_success_response(data: Any, knowledge_base_id: str | None = None, **additional_meta: Any) -> dict[str, Any]:
    """
    Create a standardized success response.

    Args:
        data: The response data
        knowledge_base_id: Optional knowledge base ID for context
        **additional_meta: Additional metadata fields

    Returns:
        Standardized success response
    """
    meta = {
        "timestamp": datetime.now(UTC).isoformat(),
        "request_id": str(uuid.uuid4()),
        **additional_meta,
    }

    if knowledge_base_id:
        meta["knowledge_base_id"] = knowledge_base_id

    return {"data": data, "meta": meta}


def create_error_response(
    error_code: str,
    message: str,
    status_code: int = 500,
    details: dict[str, Any] | None = None,
    knowledge_base_id: str | None = None,
    **additional_meta: Any,
) -> dict[str, Any]:
    """
    Create a standardized error response.

    Args:
        error_code: Application-specific error code
        message: Human-readable error message
        status_code: HTTP status code
        details: Additional error details
        knowledge_base_id: Optional knowledge base ID for context
        **additional_meta: Additional metadata fields

    Returns:
        Standardized error response
    """
    meta = {
        "timestamp": datetime.now(UTC).isoformat(),
        "request_id": str(uuid.uuid4()),
        **additional_meta,
    }

    if knowledge_base_id:
        meta["knowledge_base_id"] = knowledge_base_id

    return {
        "error": {
            "code": error_code,
            "message": message,
            "status_code": status_code,
            "details": details or {},
        },
        "meta": meta,
    }
