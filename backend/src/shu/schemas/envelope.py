"""
Envelope schemas for standardized API responses.

This module provides Pydantic models for wrapping API responses
in a consistent envelope format with data wrapper for success
responses and error wrapper for failures.
"""

from pydantic import BaseModel
from typing import Generic, TypeVar, Optional, Any, Dict

T = TypeVar('T')


class SuccessResponse(BaseModel, Generic[T]):
    """
    Standardized success response envelope.
    
    Wraps successful API responses in a consistent format
    with a data field containing the actual response.
    """
    data: T


class ErrorResponse(BaseModel):
    """
    Standardized error response envelope.
    
    Wraps error responses in a consistent format
    with error details and optional metadata.
    """
    error: Dict[str, Any]
    meta: Optional[Dict[str, Any]] = None


class MetaInfo(BaseModel):
    """
    Metadata for API responses.
    
    Contains common metadata fields like timestamp,
    request ID, and version information.
    """
    timestamp: Optional[float] = None
    request_id: Optional[str] = None
    version: Optional[str] = None 