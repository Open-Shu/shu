"""
Microsoft SSO Integration Tests for Shu

These tests verify Microsoft SSO authentication workflows:
- New user signup via Microsoft SSO
- Existing user login via Microsoft SSO  
- Email conflict handling (user exists with same email)
- Password auth conflict (409 response)
- Inactive account handling
"""

import sys
import logging
import uuid
from typing import List, Callable
from unittest.mock import patch, AsyncMock

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data

logger = logging.getLogger(__name__)


def _mock_microsoft_adapter():
    """Create a mock for MicrosoftAuthAdapter.exchange_code."""
    mock = AsyncMock()
    mock.return_value = {
        "access_token": "mock_ms_access_token",
        "refresh_token": "mock_ms_refresh_token",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    return mock


def _mock_microsoft_user_info(user_data: dict):
    """Create a mock for _get_microsoft_user_info."""
    mock = AsyncMock()
    mock.return_value = user_data
    return mock


async def _create_user_with_orm(db, email: str, name: str, google_id: str = None, 
                                 auth_method: str = "google", is_active: bool = True,
                                 password_hash: str = None):
    """Create a user using the ORM pattern (consistent with integration_test_runner.py)."""
    from shu.auth.models import User
    
    user = User(
        email=email,
        name=name,
        google_id=google_id,
        auth_method=auth_method,
        is_active=is_active,
        password_hash=password_hash,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _create_provider_identity(db, user_id: str, provider_key: str, account_id: str,
                                     primary_email: str, display_name: str):
    """Create a ProviderIdentity using the ORM pattern."""
    from shu.models.provider_identity import ProviderIdentity
    
    identity = ProviderIdentity(
        user_id=user_id,
        provider_key=provider_key,
        account_id=account_id,
        primary_email=primary_email,
        display_name=display_name,
    )
    db.add(identity)
    await db.commit()
    await db.refresh(identity)
    return identity


async def test_microsoft_login_endpoint_returns_redirect(client, db, auth_headers):
    """Test that /auth/microsoft/login returns a redirect to Microsoft OAuth."""
    response = await client.get("/api/v1/auth/microsoft/login", follow_redirects=False)
    # Should redirect to Microsoft OAuth
    assert response.status_code in (302, 307), f"Expected redirect, got {response.status_code}"
    location = response.headers.get("location", "")
    assert "login.microsoftonline.com" in location or "microsoft" in location.lower(), \
        f"Expected Microsoft OAuth URL, got: {location}"


async def test_microsoft_exchange_login_new_user(client, db, auth_headers):
    """Test Microsoft SSO creates a new user when none exists."""
    unique_id = uuid.uuid4().hex
    unique_email = f"ms_new_user_{unique_id}@example.com"
    mock_user = {
        "microsoft_id": f"ms_new_{unique_id}",
        "email": unique_email,
        "name": "New Microsoft User",
        "picture": None,
    }

    with patch("shu.api.auth._get_microsoft_user_info", _mock_microsoft_user_info(mock_user)):
        with patch("shu.providers.microsoft.auth_adapter.MicrosoftAuthAdapter.exchange_code", _mock_microsoft_adapter()):
            response = await client.post(
                "/api/v1/auth/microsoft/exchange-login",
                json={"code": "mock_auth_code"}
            )

    # New user should be created (may be inactive pending admin activation)
    assert response.status_code in (200, 201), f"Unexpected status: {response.status_code}, body: {response.text}"
    
    if response.status_code == 200:
        data = extract_data(response)
        assert "access_token" in data, f"Missing access_token in response: {data}"
        assert "refresh_token" in data, f"Missing refresh_token in response: {data}"
        assert "user" in data, f"Missing user in response: {data}"
        assert data["user"]["email"] == unique_email


async def test_microsoft_exchange_login_existing_user(client, db, auth_headers):
    """Test Microsoft SSO logs in an existing Microsoft user."""
    unique_id = uuid.uuid4().hex
    unique_email = f"ms_existing_{unique_id}@example.com"
    microsoft_id = f"ms_existing_id_{unique_id}"
    
    # Create user using ORM
    user = await _create_user_with_orm(
        db,
        email=unique_email,
        name="Existing MS User",
        google_id=None,  # Microsoft users don't have google_id
        auth_method="microsoft",
        is_active=True,
    )
    
    # Create provider identity
    await _create_provider_identity(
        db,
        user_id=user.id,
        provider_key="microsoft",
        account_id=microsoft_id,
        primary_email=unique_email,
        display_name="Existing MS User",
    )
    
    mock_user = {
        "microsoft_id": microsoft_id,
        "email": unique_email,
        "name": "Existing MS User",
        "picture": None,
    }

    with patch("shu.api.auth._get_microsoft_user_info", _mock_microsoft_user_info(mock_user)):
        with patch("shu.providers.microsoft.auth_adapter.MicrosoftAuthAdapter.exchange_code", _mock_microsoft_adapter()):
            response = await client.post(
                "/api/v1/auth/microsoft/exchange-login",
                json={"code": "mock_auth_code"}
            )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}, body: {response.text}"
    data = extract_data(response)
    assert "access_token" in data
    assert data["user"]["email"] == unique_email


async def test_microsoft_exchange_login_links_to_existing_google_user(client, db, auth_headers):
    """Test Microsoft SSO links to existing user with same email (e.g., Google user)."""
    unique_id = uuid.uuid4().hex
    unique_email = f"ms_link_{unique_id}@example.com"
    google_id = f"google_id_{unique_id}"
    microsoft_id = f"ms_link_id_{unique_id}"
    
    # Create existing Google user using ORM
    await _create_user_with_orm(
        db,
        email=unique_email,
        name="Google User",
        google_id=google_id,
        auth_method="google",
        is_active=True,
    )
    
    mock_user = {
        "microsoft_id": microsoft_id,
        "email": unique_email,
        "name": "Google User",
        "picture": None,
    }

    with patch("shu.api.auth._get_microsoft_user_info", _mock_microsoft_user_info(mock_user)):
        with patch("shu.providers.microsoft.auth_adapter.MicrosoftAuthAdapter.exchange_code", _mock_microsoft_adapter()):
            response = await client.post(
                "/api/v1/auth/microsoft/exchange-login",
                json={"code": "mock_auth_code"}
            )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}, body: {response.text}"
    data = extract_data(response)
    assert "access_token" in data
    assert data["user"]["email"] == unique_email


async def test_microsoft_exchange_login_password_conflict(client, db, auth_headers):
    """Test Microsoft SSO returns 409 when user exists with password auth."""
    unique_id = uuid.uuid4().hex
    unique_email = f"ms_pwd_conflict_{unique_id}@example.com"
    
    # Create existing password user using ORM
    await _create_user_with_orm(
        db,
        email=unique_email,
        name="Password User",
        google_id=None,
        auth_method="password",
        is_active=True,
        password_hash="fake_hash",
    )
    
    mock_user = {
        "microsoft_id": f"ms_pwd_{unique_id}",
        "email": unique_email,
        "name": "Password User",
        "picture": None,
    }

    logger.info("=== EXPECTED TEST OUTPUT: 409 conflict error for password auth user is expected ===")

    with patch("shu.api.auth._get_microsoft_user_info", _mock_microsoft_user_info(mock_user)):
        with patch("shu.providers.microsoft.auth_adapter.MicrosoftAuthAdapter.exchange_code", _mock_microsoft_adapter()):
            response = await client.post(
                "/api/v1/auth/microsoft/exchange-login",
                json={"code": "mock_auth_code"}
            )

    assert response.status_code == 409, f"Expected 409, got {response.status_code}, body: {response.text}"
    logger.info("=== EXPECTED TEST OUTPUT: 409 conflict occurred as expected ===")


async def test_microsoft_exchange_login_inactive_user(client, db, auth_headers):
    """Test Microsoft SSO returns 400 when user account is inactive."""
    unique_id = uuid.uuid4().hex
    unique_email = f"ms_inactive_{unique_id}@example.com"
    microsoft_id = f"ms_inactive_id_{unique_id}"
    
    # Create inactive user with Microsoft identity using ORM
    user = await _create_user_with_orm(
        db,
        email=unique_email,
        name="Inactive MS User",
        google_id=None,
        auth_method="microsoft",
        is_active=False,  # Inactive user
    )
    
    # Create provider identity
    await _create_provider_identity(
        db,
        user_id=user.id,
        provider_key="microsoft",
        account_id=microsoft_id,
        primary_email=unique_email,
        display_name="Inactive MS User",
    )
    
    mock_user = {
        "microsoft_id": microsoft_id,
        "email": unique_email,
        "name": "Inactive MS User",
        "picture": None,
    }

    logger.info("=== EXPECTED TEST OUTPUT: 400 error for inactive user is expected ===")

    with patch("shu.api.auth._get_microsoft_user_info", _mock_microsoft_user_info(mock_user)):
        with patch("shu.providers.microsoft.auth_adapter.MicrosoftAuthAdapter.exchange_code", _mock_microsoft_adapter()):
            response = await client.post(
                "/api/v1/auth/microsoft/exchange-login",
                json={"code": "mock_auth_code"}
            )

    assert response.status_code == 400, f"Expected 400, got {response.status_code}, body: {response.text}"
    logger.info("=== EXPECTED TEST OUTPUT: 400 error for inactive user occurred as expected ===")


async def test_microsoft_exchange_login_missing_code(client, db, auth_headers):
    """Test Microsoft SSO returns 422 when code is missing."""
    logger.info("=== EXPECTED TEST OUTPUT: 422 validation error for missing code is expected ===")
    
    response = await client.post(
        "/api/v1/auth/microsoft/exchange-login",
        json={}
    )

    assert response.status_code == 422, f"Expected 422, got {response.status_code}, body: {response.text}"
    logger.info("=== EXPECTED TEST OUTPUT: 422 validation error occurred as expected ===")


class MicrosoftSSOTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for Microsoft SSO functionality."""
    
    def get_test_functions(self) -> List[Callable]:
        """Return all Microsoft SSO test functions."""
        return [
            test_microsoft_login_endpoint_returns_redirect,
            test_microsoft_exchange_login_new_user,
            test_microsoft_exchange_login_existing_user,
            test_microsoft_exchange_login_links_to_existing_google_user,
            test_microsoft_exchange_login_password_conflict,
            test_microsoft_exchange_login_inactive_user,
            test_microsoft_exchange_login_missing_code,
        ]
    
    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Microsoft SSO Integration Tests"
    
    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for Microsoft SSO authentication [SHU-500]"


if __name__ == "__main__":
    suite = MicrosoftSSOTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
