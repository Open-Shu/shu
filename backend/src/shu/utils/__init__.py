"""
Utility functions for Shu RAG Backend.

This module contains utility functions used throughout the application.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone
import uuid

from .knowledge_base_verifier import KnowledgeBaseVerifier
from .tokenization import (
    estimate_tokens,
    estimate_tokens_for_chunks,
    tokens_to_chars_estimate,
    chars_to_tokens_estimate,
)

__all__ = [
    "create_success_response",
    "create_error_response",
    "KnowledgeBaseVerifier",
    "estimate_tokens",
    "estimate_tokens_for_chunks",
    "tokens_to_chars_estimate",
    "chars_to_tokens_estimate",
]


def create_success_response(
    data: Any,
    knowledge_base_id: Optional[str] = None,
    **additional_meta: Any
) -> Dict[str, Any]:
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": str(uuid.uuid4()),
        **additional_meta
    }
    
    if knowledge_base_id:
        meta["knowledge_base_id"] = knowledge_base_id
    
    return {
        "data": data,
        "meta": meta
    }


def create_error_response(
    error_code: str,
    message: str,
    status_code: int = 500,
    details: Optional[Dict[str, Any]] = None,
    knowledge_base_id: Optional[str] = None,
    **additional_meta: Any
) -> Dict[str, Any]:
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": str(uuid.uuid4()),
        **additional_meta
    }
    
    if knowledge_base_id:
        meta["knowledge_base_id"] = knowledge_base_id
    
    return {
        "error": {
            "code": error_code,
            "message": message,
            "status_code": status_code,
            "details": details or {}
        },
        "meta": meta
    } 