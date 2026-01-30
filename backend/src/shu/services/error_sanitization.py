"""Error sanitization service for Shu.

This module provides utilities for sanitizing error messages before displaying
them to users, ensuring sensitive information like API keys and full URLs are
not exposed in production environments.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

# Patterns for sensitive data that should be sanitized
API_KEY_PATTERNS = [
    r"sk-[a-zA-Z0-9]{20,}",  # OpenAI API keys
    r"sk-proj-[a-zA-Z0-9\-_]{20,}",  # OpenAI project API keys
    r"sk-ant-[a-zA-Z0-9\-_]{20,}",  # Anthropic API keys
    r"Bearer\s+[a-zA-Z0-9\-_\.]+",  # Bearer tokens
    r"api[_-]?key[=:]\s*['\"]?[a-zA-Z0-9\-_]{16,}['\"]?",  # Generic API key patterns
    r"[a-zA-Z0-9]{32,}",  # Long alphanumeric strings (potential keys)
]

# URL pattern to extract domain only
URL_PATTERN = r"https?://([^/\s]+)(/[^\s]*)?"

# Patterns for request IDs that should be removed in production
REQUEST_ID_PATTERNS = [
    r"request[_-]?id[=:]\s*['\"]?[a-zA-Z0-9\-_]+['\"]?",
    r"x-request-id[=:]\s*['\"]?[a-zA-Z0-9\-_]+['\"]?",
    r"req[_-]?[a-zA-Z0-9]{8,}",
]


@dataclass
class SanitizedError:
    """Sanitized error information safe for display to users.

    Attributes:
        message: User-friendly error message.
        error_type: Provider error type (e.g., 'invalid_api_key').
        error_code: Provider error code (e.g., 'authentication_error').
        status_code: HTTP status code.
        suggestions: List of suggested fixes for the error.
        details: Additional context (only included in development mode).

    """

    message: str
    error_type: str | None = None
    error_code: str | None = None
    status_code: int | None = None
    suggestions: list[str] = field(default_factory=list)
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation.

        Returns:
            Dictionary with all non-None fields.

        """
        result: dict[str, Any] = {"message": self.message}
        if self.error_type is not None:
            result["error_type"] = self.error_type
        if self.error_code is not None:
            result["error_code"] = self.error_code
        if self.status_code is not None:
            result["status_code"] = self.status_code
        if self.suggestions:
            result["suggestions"] = self.suggestions
        if self.details is not None:
            result["details"] = self.details
        return result


class ErrorSanitizer:
    """Sanitize error messages for safe display to users.

    This class provides methods to extract and sanitize error information
    from LLM provider responses, ensuring sensitive data is not exposed
    in production environments.
    """

    # Error guidance messages for common HTTP status codes
    ERROR_GUIDANCE: dict[int, dict[str, Any]] = {
        400: {
            "message": "The request was invalid or malformed",
            "suggestions": [
                "Check your model configuration parameters",
                "Verify the model name is correct",
                "Ensure all required fields are provided",
            ],
        },
        401: {
            "message": "Invalid API key or authentication failed",
            "suggestions": [
                "Check your API key in the provider settings",
                "Verify the API key has not expired",
                "Ensure the API key has the required permissions",
            ],
        },
        403: {
            "message": "Access forbidden",
            "suggestions": [
                "Check your API key permissions",
                "Verify your account has access to this model",
                "Contact your provider for access",
            ],
        },
        404: {
            "message": "Model or resource not found",
            "suggestions": [
                "Run model discovery to find available models",
                "Verify the model name is spelled correctly",
                "Check if the model is available in your region",
            ],
        },
        429: {
            "message": "Rate limit exceeded",
            "suggestions": [
                "Wait a few moments before trying again",
                "Consider upgrading your API plan",
                "Reduce the frequency of requests",
            ],
        },
        500: {
            "message": "Provider server error",
            "suggestions": [
                "The provider is experiencing issues",
                "Try again in a few moments",
                "Check the provider's status page",
            ],
        },
        502: {
            "message": "Provider gateway error",
            "suggestions": [
                "The provider's servers may be overloaded",
                "Try again in a few moments",
            ],
        },
        503: {
            "message": "Provider service unavailable",
            "suggestions": [
                "The provider is temporarily unavailable",
                "Try again later",
                "Check the provider's status page",
            ],
        },
        504: {
            "message": "Request timeout",
            "suggestions": [
                "Increase the timeout setting",
                "Try a simpler prompt",
                "Check your network connection",
            ],
        },
    }

    @staticmethod
    def sanitize_string(text: str) -> str:
        """Remove sensitive data from a string.

        Args:
            text: The string to sanitize.

        Returns:
            Sanitized string with sensitive data replaced.

        """
        if not text:
            return text

        result = text

        # Sanitize API keys
        for pattern in API_KEY_PATTERNS:
            result = re.sub(pattern, "[REDACTED]", result, flags=re.IGNORECASE)

        # Sanitize URLs to show only domain
        def replace_url(match: re.Match[str]) -> str:
            domain = match.group(1)
            return f"https://{domain}/..."

        result = re.sub(URL_PATTERN, replace_url, result)

        # Remove request IDs
        for pattern in REQUEST_ID_PATTERNS:
            result = re.sub(pattern, "[request_id_redacted]", result, flags=re.IGNORECASE)

        return result

    @staticmethod
    def sanitize_dict(data: dict[str, Any], keys_to_redact: list[str] | None = None) -> dict[str, Any]:
        """Recursively sanitize a dictionary.

        Args:
            data: Dictionary to sanitize.
            keys_to_redact: Additional keys whose values should be fully redacted.

        Returns:
            Sanitized dictionary.

        """
        if keys_to_redact is None:
            keys_to_redact = [
                "api_key",
                "apikey",
                "api-key",
                "authorization",
                "auth",
                "token",
                "secret",
                "password",
                "credential",
                "x-api-key",
            ]

        result: dict[str, Any] = {}
        for key, value in data.items():
            lower_key = key.lower()

            # Fully redact sensitive keys
            if any(sensitive in lower_key for sensitive in keys_to_redact):
                result[key] = "[REDACTED]"
            elif isinstance(value, dict):
                result[key] = ErrorSanitizer.sanitize_dict(value, keys_to_redact)
            elif isinstance(value, list):
                result[key] = [
                    ErrorSanitizer.sanitize_dict(item, keys_to_redact)
                    if isinstance(item, dict)
                    else ErrorSanitizer.sanitize_string(str(item))
                    if isinstance(item, str)
                    else item
                    for item in value
                ]
            elif isinstance(value, str):
                result[key] = ErrorSanitizer.sanitize_string(value)
            else:
                result[key] = value

        return result

    @classmethod
    def extract_provider_error(cls, response: httpx.Response) -> dict[str, Any]:
        """Extract structured error information from a provider response.

        This method attempts to parse the response body and extract
        error message, type, and code fields from various provider formats.

        Args:
            response: The httpx Response object from the provider.

        Returns:
            Dictionary with extracted error information:
                - message: The error message from the provider
                - error_type: The error type/category
                - error_code: The error code
                - status_code: HTTP status code
                - raw_body: The raw response body (for debugging)

        """
        result: dict[str, Any] = {
            "message": None,
            "error_type": None,
            "error_code": None,
            "status_code": response.status_code,
            "raw_body": None,
        }

        try:
            body_text = response.text
            result["raw_body"] = body_text
        except Exception:
            return result

        if not body_text:
            return result

        try:
            body_json = response.json()
        except Exception:
            # If not JSON, use the raw text as the message
            result["message"] = body_text[:500] if len(body_text) > 500 else body_text
            return result

        if not isinstance(body_json, dict):
            result["message"] = str(body_json)[:500]
            return result

        # Try to extract error information from common provider formats
        error_section = body_json.get("error")

        if isinstance(error_section, dict):
            # OpenAI/Anthropic style: {"error": {"message": "...", "type": "...", "code": "..."}}
            result["message"] = (
                error_section.get("message")
                or error_section.get("detail")
                or error_section.get("error")
                or error_section.get("status")
            )
            result["error_type"] = (
                error_section.get("type") or error_section.get("status") or error_section.get("reason")
            )
            result["error_code"] = error_section.get("code") or error_section.get("status")

        elif isinstance(error_section, list) and error_section:
            # Array of errors format
            first_error = error_section[0]
            if isinstance(first_error, dict):
                result["message"] = first_error.get("message") or first_error.get("detail") or first_error.get("error")
                result["error_type"] = first_error.get("type") or first_error.get("status") or first_error.get("reason")
                result["error_code"] = first_error.get("code") or first_error.get("status")
            elif isinstance(first_error, str):
                result["message"] = first_error

        elif isinstance(error_section, str):
            result["message"] = error_section

        # Fallback to top-level fields if error section didn't have what we need
        if result["message"] is None:
            result["message"] = (
                body_json.get("message")
                or body_json.get("detail")
                or body_json.get("error_description")
                or body_json.get("error")
            )

        if result["error_type"] is None:
            result["error_type"] = body_json.get("type") or body_json.get("status")

        if result["error_code"] is None:
            result["error_code"] = body_json.get("code")

        return result

    @classmethod
    def sanitize_error(
        cls,
        error_details: dict[str, Any],
    ) -> SanitizedError:
        """Sanitize error details for safe display to users.

        Sensitive data is always removed or masked, regardless of environment.
        This ensures consistent security across development and production.

        Args:
            error_details: Raw error details from the provider. Expected keys:
                - message: Error message
                - error_type: Error type/category
                - error_code: Error code
                - status_code: HTTP status code
                - raw_body: Raw response body (optional)
                - endpoint: Request endpoint (optional)
                - request_id: Request ID (optional)

        Returns:
            SanitizedError with sanitized information safe for display.

        """
        status_code = error_details.get("status_code") or error_details.get("status")
        error_type = error_details.get("error_type") or error_details.get("provider_error_type")
        error_code = error_details.get("error_code") or error_details.get("provider_error_code")

        # Get the provider message
        provider_message = error_details.get("message") or error_details.get("provider_message")

        # Get guidance for this status code
        guidance = cls.ERROR_GUIDANCE.get(status_code, {})
        default_message = guidance.get("message", "An error occurred")
        suggestions = list(guidance.get("suggestions", []))

        # Always sanitize the provider message
        if provider_message:
            message = cls.sanitize_string(provider_message)
        else:
            message = default_message

        # No details dict in any environment for security
        details: dict[str, Any] | None = None

        return SanitizedError(
            message=message,
            error_type=error_type,
            error_code=error_code,
            status_code=status_code,
            suggestions=suggestions,
            details=details,
        )

    @classmethod
    def get_error_guidance(cls, status_code: int) -> dict[str, Any]:
        """Get error guidance for a specific HTTP status code.

        Args:
            status_code: HTTP status code.

        Returns:
            Dictionary with 'message' and 'suggestions' keys.

        """
        return cls.ERROR_GUIDANCE.get(
            status_code,
            {
                "message": f"HTTP error {status_code}",
                "suggestions": ["Check the error details for more information"],
            },
        )
