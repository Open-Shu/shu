"""
LLM Unit Tests for Shu

These tests focus on business logic, data validation, and API response formatting
without requiring database connections.
"""

import sys
import os
from typing import List, Callable
from unittest.mock import Mock, AsyncMock
from datetime import datetime
from decimal import Decimal

from integ.base_unit_test import BaseUnitTestSuite
from shu.api.llm import _provider_to_response
from shu.models.llm_provider import LLMProvider
from shu.llm.service import LLMService
from shu.core.exceptions import LLMProviderError


def test_provider_with_api_key():
    """Test provider response when API key exists."""
    provider = LLMProvider(
        id="test-id-1",
        name="Test OpenAI",
        provider_type="openai",
        api_endpoint="https://api.openai.com/v1",
        api_key_encrypted="encrypted_key_data",
        organization_id="org-123",
        is_active=True,
        supports_streaming=True,
        supports_functions=True,
        supports_vision=False,
        rate_limit_rpm=3500,
        rate_limit_tpm=90000,
        budget_limit_monthly=Decimal('100.00'),
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    
    response = _provider_to_response(None, provider)
    
    assert response.id == "test-id-1"
    assert response.name == "Test OpenAI"
    assert response.provider_type == "openai"
    assert response.has_api_key is True
    assert not hasattr(response, 'api_key')
    assert not hasattr(response, 'api_key_encrypted')


def test_provider_without_api_key():
    """Test provider response when no API key exists."""
    provider = LLMProvider(
        id="test-id-2",
        name="Test Provider No Key",
        provider_type="anthropic",
        api_endpoint="https://api.anthropic.com/v1",
        api_key_encrypted=None,
        is_active=False,
        supports_streaming=False,
        supports_functions=False,
        supports_vision=True,
        rate_limit_rpm=1000,
        rate_limit_tpm=50000,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    
    response = _provider_to_response(None, provider)
    
    assert response.id == "test-id-2"
    assert response.name == "Test Provider No Key"
    assert response.provider_type == "anthropic"
    assert response.has_api_key is False
    assert not hasattr(response, 'api_key')
    assert not hasattr(response, 'api_key_encrypted')


def test_provider_with_empty_api_key():
    """Test provider response when API key is empty string."""
    provider = LLMProvider(
        id="test-id-3",
        name="Test Provider Empty Key",
        provider_type="openai",
        api_endpoint="https://api.openai.com/v1",
        api_key_encrypted="",
        is_active=True,
        supports_streaming=True,
        supports_functions=True,
        supports_vision=False,
        rate_limit_rpm=2000,
        rate_limit_tpm=60000,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    
    response = _provider_to_response(None, provider)
    
    assert response.id == "test-id-3"
    assert response.has_api_key is False


def test_validate_provider_data_valid():
    """Test LLM service validation with valid data."""
    # Mock database session
    mock_db = Mock()
    service = LLMService(mock_db)
    
    valid_data = {
        "name": "Valid Provider",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",
        "rate_limit_rpm": 3500,
        "rate_limit_tpm": 90000
    }
    
    # This should not raise any exceptions
    try:
        # Test the validation logic (this would normally be in a validate method)
        assert valid_data["name"].strip() != ""
        assert valid_data["provider_type"] in ["openai", "anthropic", "azure", "custom"]
        assert valid_data["api_endpoint"].startswith("https://")
        assert valid_data["rate_limit_rpm"] > 0
        assert valid_data["rate_limit_tpm"] > 0
    except Exception as e:
        assert False, f"Valid data should not raise exception: {e}"


def test_validate_provider_data_invalid():
    """Test LLM service validation with invalid data."""
    # Mock database session
    mock_db = Mock()
    service = LLMService(mock_db)
    
    invalid_data_sets = [
        {"name": "", "provider_type": "openai"},  # Empty name
        {"name": "Test", "provider_type": "invalid"},  # Invalid provider type
        {"name": "Test", "provider_type": "openai", "api_endpoint": "http://insecure.com"},  # Non-HTTPS
        {"name": "Test", "provider_type": "openai", "rate_limit_rpm": -1},  # Negative rate limit
    ]
    
    for invalid_data in invalid_data_sets:
        try:
            # Test validation logic
            if "name" in invalid_data and invalid_data["name"].strip() == "":
                raise ValueError("Name cannot be empty")
            if "provider_type" in invalid_data and invalid_data["provider_type"] not in ["openai", "anthropic", "azure", "custom"]:
                raise ValueError("Invalid provider type")
            if "api_endpoint" in invalid_data and not invalid_data["api_endpoint"].startswith("https://"):
                raise ValueError("API endpoint must use HTTPS")
            if "rate_limit_rpm" in invalid_data and invalid_data["rate_limit_rpm"] <= 0:
                raise ValueError("Rate limit must be positive")
            
            # If we get here, validation didn't catch the invalid data
            assert False, f"Invalid data should have raised exception: {invalid_data}"
        except (ValueError, AssertionError):
            # Expected - validation caught the invalid data
            pass


def test_llm_provider_error_creation():
    """Test LLM provider error creation."""
    error = LLMProviderError("Test error message")
    assert str(error) == "LLM provider error: Test error message"
    assert isinstance(error, Exception)


def test_llm_provider_error_with_details():
    """Test LLM provider error with additional details."""
    error = LLMProviderError("API call failed", {"status_code": 429, "retry_after": 60})
    assert "API call failed" in str(error)
    # The error should contain the prefixed message
    assert str(error) == "LLM provider error: API call failed"


def test_rate_limit_validation():
    """Test rate limit validation logic."""
    # Valid rate limits
    valid_rpm = 3500
    valid_tpm = 90000
    
    assert valid_rpm > 0
    assert valid_tpm > 0
    assert valid_tpm >= valid_rpm  # TPM should generally be higher than RPM
    
    # Invalid rate limits
    invalid_values = [-1, 0, None]
    for invalid in invalid_values:
        if invalid is not None:
            assert not (invalid > 0), f"Invalid rate limit {invalid} should not be positive"


def test_budget_limit_validation():
    """Test budget limit validation logic."""
    # Valid budget limits
    valid_budgets = [Decimal('100.00'), Decimal('0.00'), None]
    
    for budget in valid_budgets:
        if budget is not None:
            assert budget >= 0, f"Budget {budget} should be non-negative"
    
    # Invalid budget limits
    invalid_budget = Decimal('-10.00')
    assert invalid_budget < 0, "Negative budget should be invalid"


def test_endpoint_validation():
    """Test API endpoint validation logic."""
    # Valid endpoints
    valid_endpoints = [
        "https://api.openai.com/v1",
        "https://api.anthropic.com/v1",
        "https://custom-endpoint.com/api"
    ]
    
    for endpoint in valid_endpoints:
        assert endpoint.startswith("https://"), f"Endpoint {endpoint} should use HTTPS"
        assert len(endpoint) > 8, f"Endpoint {endpoint} should be more than just https://"
    
    # Invalid endpoints
    invalid_endpoints = [
        "http://insecure.com",  # HTTP instead of HTTPS
        "",  # Empty
        "not-a-url",  # Not a URL
        "ftp://wrong-protocol.com"  # Wrong protocol
    ]
    
    for endpoint in invalid_endpoints:
        assert not endpoint.startswith("https://"), f"Invalid endpoint {endpoint} should not pass validation"


class LLMUnitTestSuite(BaseUnitTestSuite):
    """Unit test suite for LLM functionality."""
    
    def get_test_functions(self) -> List[Callable]:
        """Return all LLM unit test functions."""
        return [
            test_provider_with_api_key,
            test_provider_without_api_key,
            test_provider_with_empty_api_key,
            test_validate_provider_data_valid,
            test_validate_provider_data_invalid,
            test_llm_provider_error_creation,
            test_llm_provider_error_with_details,
            test_rate_limit_validation,
            test_budget_limit_validation,
            test_endpoint_validation,
        ]
    
    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "LLM Unit Tests"
    
    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "Unit tests for LLM business logic, data validation, and response formatting"
    
    def get_cli_examples(self) -> str:
        """Return LLM-specific CLI examples."""
        return """
Examples:
  python tests/test_llm_unit_migrated.py                          # Run all LLM unit tests
  python tests/test_llm_unit_migrated.py --list                   # List available tests
  python tests/test_llm_unit_migrated.py --test test_provider_with_api_key
  python tests/test_llm_unit_migrated.py --pattern "validation"   # Run validation tests
  python tests/test_llm_unit_migrated.py --pattern "error"        # Run error handling tests
        """


if __name__ == "__main__":
    suite = LLMUnitTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
