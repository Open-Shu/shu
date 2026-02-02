"""Custom response handlers for Shu API.

This module provides utilities for creating consistent API responses
with proper envelope formatting and preventing double-wrapping issues.
"""

from typing import Any

from fastapi import status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from .logging import get_logger

logger = get_logger(__name__)


def to_serializable(obj):
    """Recursively convert Pydantic models, lists, and dicts to serializable types."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if isinstance(obj, list):
        return [to_serializable(item) for item in obj]
    if isinstance(obj, dict):
        return {k: to_serializable(v) for k, v in obj.items()}
    return obj


class ShuResponse:
    """Custom response handler for Shu API endpoints.

    Provides consistent response formatting with single-envelope structure
    and prevents FastAPI's automatic double-wrapping of response models.
    """

    @staticmethod
    def success(
        data: Any, status_code: int = status.HTTP_200_OK, headers: dict[str, str] | None = None
    ) -> JSONResponse:
        """Create a successful response with single-envelope structure.

        Args:
            data: The response data to wrap (can be Pydantic models, dicts, or other JSON-serializable data)
            status_code: HTTP status code (default: 200)
            headers: Optional response headers

        Returns:
            JSONResponse with proper envelope structure

        """
        serialized_data = to_serializable(data)
        response_content = {"data": serialized_data}
        response_content = jsonable_encoder(response_content)

        logger.debug(
            "Creating success response",
            extra={
                "status_code": status_code,
                "data_type": type(data).__name__,
                "has_headers": headers is not None,
            },
        )

        return JSONResponse(content=response_content, status_code=status_code, headers=headers)

    @staticmethod
    def error(
        message: str,
        code: str = "API_ERROR",
        details: Any | None = None,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        headers: dict[str, str] | None = None,
    ) -> JSONResponse:
        """Create an error response with consistent envelope structure.

        Args:
            message: Error message
            code: Error code for client handling
            details: Optional additional error details
            status_code: HTTP status code (default: 400)
            headers: Optional response headers

        Returns:
            JSONResponse with error envelope structure

        """
        error_content = {"error": {"message": message, "code": code}}

        if details is not None:
            # Preserve structured details instead of stringifying
            error_content["error"]["details"] = to_serializable(details)

        error_content = jsonable_encoder(error_content)

        logger.debug(
            "Creating error response",
            extra={
                "status_code": status_code,
                "error_code": code,
                "has_details": details is not None,
            },
        )

        return JSONResponse(content=error_content, status_code=status_code, headers=headers)

    @staticmethod
    def created(data: Any, headers: dict[str, str] | None = None) -> JSONResponse:
        """Create a 201 Created response.

        Args:
            data: The created resource data
            headers: Optional response headers

        Returns:
            JSONResponse with 201 status and envelope structure

        """
        return ShuResponse.success(data, status.HTTP_201_CREATED, headers)

    @staticmethod
    def no_content(headers: dict[str, str] | None = None) -> Response:
        """Create a 204 No Content response.

        Args:
            headers: Optional response headers

        Returns:
            Response with 204 status and no content

        """
        return Response(content=b"", status_code=status.HTTP_204_NO_CONTENT, headers=headers)


def create_success_response(data: Any, **kwargs) -> JSONResponse:
    """Create success responses convenience function.

    Args:
        data: The response data
        **kwargs: Additional arguments for ShuResponse.success

    Returns:
        JSONResponse with proper envelope structure

    """
    return ShuResponse.success(data, **kwargs)


def create_error_response(message: str, code: str = "API_ERROR", **kwargs) -> JSONResponse:
    """Create error responses convenience function.

    Args:
        message: Error message
        code: Error code
        **kwargs: Additional arguments for ShuResponse.error

    Returns:
        JSONResponse with error envelope structure

    """
    return ShuResponse.error(message, code, **kwargs)
