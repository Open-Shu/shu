"""
API Key Unit Tests for Shu

These tests verify API key display and security functionality.
"""

import sys
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from integ.base_unit_test import BaseUnitTestSuite
from shu.api.llm import _provider_to_response
from shu.models.llm_provider import LLMProvider


def test_api_key_display_with_key():
    """Test that API key display works correctly when provider has API key."""
    provider = LLMProvider(
        id="test-id-1",
        name="API Key Test Provider",
        provider_type="openai",
        api_endpoint="https://api.openai.com/v1",
        api_key_encrypted="encrypted_key_data_here",
        is_active=True,
        supports_streaming=True,
        supports_functions=True,
        supports_vision=False,
        rate_limit_rpm=3500,
        rate_limit_tpm=90000,
        budget_limit_monthly=Decimal("100.00"),
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    response = _provider_to_response(None, provider)

    # API key should never be exposed in response
    assert not hasattr(response, "api_key")
    assert not hasattr(response, "api_key_encrypted")

    # But has_api_key should be True
    assert response.has_api_key is True

    # Other fields should be present
    assert response.id == "test-id-1"
    assert response.name == "API Key Test Provider"
    assert response.provider_type == "openai"


def test_api_key_display_without_key():
    """Test that API key display works correctly when provider has no API key."""
    provider = LLMProvider(
        id="test-id-2",
        name="No API Key Provider",
        provider_type="anthropic",
        api_endpoint="https://api.anthropic.com/v1",
        api_key_encrypted=None,
        is_active=True,
        supports_streaming=False,
        supports_functions=False,
        supports_vision=True,
        rate_limit_rpm=1000,
        rate_limit_tpm=50000,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    response = _provider_to_response(None, provider)

    # API key should never be exposed in response
    assert not hasattr(response, "api_key")
    assert not hasattr(response, "api_key_encrypted")

    # has_api_key should be False
    assert response.has_api_key is False

    # Other fields should be present
    assert response.id == "test-id-2"
    assert response.name == "No API Key Provider"
    assert response.provider_type == "anthropic"


def test_api_key_display_with_empty_key():
    """Test that API key display works correctly when provider has empty API key."""
    provider = LLMProvider(
        id="test-id-3",
        name="Empty API Key Provider",
        provider_type="azure",
        api_endpoint="https://api.azure.com/v1",
        api_key_encrypted="",  # Empty string
        is_active=False,
        supports_streaming=True,
        supports_functions=False,
        supports_vision=False,
        rate_limit_rpm=2000,
        rate_limit_tpm=60000,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    response = _provider_to_response(None, provider)

    # API key should never be exposed in response
    assert not hasattr(response, "api_key")
    assert not hasattr(response, "api_key_encrypted")

    # has_api_key should be False for empty string
    assert response.has_api_key is False

    # Other fields should be present
    assert response.id == "test-id-3"
    assert response.name == "Empty API Key Provider"
    assert response.provider_type == "azure"


def test_api_key_security_no_leakage():
    """Test that API keys never leak through any response field."""
    provider = LLMProvider(
        id="security-test",
        name="Security Test Provider",
        provider_type="openai",
        api_endpoint="https://api.openai.com/v1",
        api_key_encrypted="super_secret_encrypted_key_12345",
        organization_id="org-secret-123",
        is_active=True,
        supports_streaming=True,
        supports_functions=True,
        supports_vision=True,
        rate_limit_rpm=5000,
        rate_limit_tpm=100000,
        budget_limit_monthly=Decimal("500.00"),
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    response = _provider_to_response(None, provider)

    # Convert response to dict to check all fields
    response_dict = response.dict() if hasattr(response, "dict") else response.__dict__

    # Check that no field contains the secret key
    secret_key = "super_secret_encrypted_key_12345"
    for field_name, field_value in response_dict.items():
        if isinstance(field_value, str):
            assert secret_key not in field_value, f"Secret key found in field '{field_name}'"

    # Specifically check that sensitive fields are not present
    sensitive_fields = ["api_key", "api_key_encrypted", "api_key_raw"]
    for field in sensitive_fields:
        assert field not in response_dict, f"Sensitive field '{field}' found in response"


def test_api_key_boolean_logic():
    """Test the boolean logic for has_api_key field."""
    test_cases = [
        (None, False),  # None should be False
        ("", False),  # Empty string should be False
        ("   ", True),  # Whitespace is truthy in Python, so True
        ("actual_key", True),  # Real key should be True
        ("encrypted_data_123", True),  # Any non-empty content should be True
    ]

    for api_key_value, expected_has_key in test_cases:
        provider = LLMProvider(
            id=f"test-{hash(str(api_key_value))}",
            name="Boolean Logic Test",
            provider_type="openai",
            api_endpoint="https://api.openai.com/v1",
            api_key_encrypted=api_key_value,
            is_active=True,
            supports_streaming=True,
            supports_functions=True,
            supports_vision=False,
            rate_limit_rpm=1000,
            rate_limit_tpm=10000,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        response = _provider_to_response(None, provider)

        assert (
            response.has_api_key == expected_has_key
        ), f"API key '{api_key_value}' should result in has_api_key={expected_has_key}"


def test_response_structure_consistency():
    """Test that response structure is consistent regardless of API key presence."""
    providers = [
        # Provider with API key
        LLMProvider(
            id="with-key",
            name="With Key",
            provider_type="openai",
            api_endpoint="https://api.openai.com/v1",
            api_key_encrypted="encrypted_key",
            is_active=True,
            supports_streaming=True,
            supports_functions=True,
            supports_vision=False,
            rate_limit_rpm=1000,
            rate_limit_tpm=10000,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
        # Provider without API key
        LLMProvider(
            id="without-key",
            name="Without Key",
            provider_type="anthropic",
            api_endpoint="https://api.anthropic.com/v1",
            api_key_encrypted=None,
            is_active=False,
            supports_streaming=False,
            supports_functions=False,
            supports_vision=True,
            rate_limit_rpm=500,
            rate_limit_tpm=5000,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
    ]

    responses = [_provider_to_response(None, provider) for provider in providers]

    # Both responses should have the same structure (same fields)
    response1_fields = set(responses[0].__dict__.keys())
    response2_fields = set(responses[1].__dict__.keys())

    assert (
        response1_fields == response2_fields
    ), "Response structure should be consistent regardless of API key presence"

    # Both should have has_api_key field
    for response in responses:
        assert hasattr(response, "has_api_key"), "All responses should have has_api_key field"
        assert isinstance(response.has_api_key, bool), "has_api_key should be boolean"


class APIKeyUnitTestSuite(BaseUnitTestSuite):
    """Unit test suite for API key functionality."""

    def get_test_functions(self) -> list[Callable]:
        """Return all API key unit test functions."""
        return [
            test_api_key_display_with_key,
            test_api_key_display_without_key,
            test_api_key_display_with_empty_key,
            test_api_key_security_no_leakage,
            test_api_key_boolean_logic,
            test_response_structure_consistency,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "API Key Unit Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "Unit tests for API key display and security functionality"

    def get_cli_examples(self) -> str:
        """Return API key-specific CLI examples."""
        return """
Examples:
  python tests/test_api_key_unit.py                          # Run all API key unit tests
  python tests/test_api_key_unit.py --list                   # List available tests
  python tests/test_api_key_unit.py --test test_api_key_display_with_key
  python tests/test_api_key_unit.py --pattern "security"     # Run security tests
  python tests/test_api_key_unit.py --pattern "display"      # Run display tests
        """


if __name__ == "__main__":
    suite = APIKeyUnitTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
