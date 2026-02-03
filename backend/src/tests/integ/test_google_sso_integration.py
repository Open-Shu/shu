"""
Google SSO Integration Tests for Shu

These tests verify Google SSO authentication workflows:
- New user signup via Google SSO
- Existing user login via Google SSO (ProviderIdentity)
- Email conflict handling (user exists with same email)
- Password auth conflict (409 response)
- Inactive account handling
"""

import logging
import sys
import uuid
from collections.abc import Callable
from unittest.mock import AsyncMock, patch

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data

logger = logging.getLogger(__name__)


def _mock_google_adapter_exchange_code():
    """Create a mock for GoogleAuthAdapter.exchange_code."""
    mock = AsyncMock()
    mock.return_value = {
        "access_token": "mock_google_access_token",
        "id_token": "mock_google_id_token",
        "refresh_token": "mock_google_refresh_token",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    return mock


def _mock_adapter_get_user_info(user_data: dict):
    """Create a mock for GoogleAuthAdapter.get_user_info.

    Converts test data format to normalized provider info format.
    """
    mock = AsyncMock()
    # Convert to normalized format
    mock.return_value = {
        "provider_id": user_data.get("google_id"),
        "provider_key": "google",
        "email": user_data.get("email"),
        "name": user_data.get("name"),
        "picture": user_data.get("picture"),
    }
    return mock


async def _create_user_with_orm(
    db, email: str, name: str, auth_method: str = "google", is_active: bool = True, password_hash: str | None = None
):
    """Create a user using the ORM pattern (consistent with integration_test_runner.py)."""
    from shu.auth.models import User

    user = User(
        email=email,
        name=name,
        auth_method=auth_method,
        is_active=is_active,
        password_hash=password_hash,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _create_provider_identity(
    db, user_id: str, provider_key: str, account_id: str, primary_email: str, display_name: str
):
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


async def test_google_login_endpoint_returns_redirect(client, _db, _auth_headers):
    """Test that /auth/google/login returns a redirect to Google OAuth."""
    response = await client.get("/api/v1/auth/google/login", follow_redirects=False)
    # Should redirect to Google OAuth
    assert response.status_code in (302, 307), f"Expected redirect, got {response.status_code}"
    location = response.headers.get("location", "")
    assert (
        "accounts.google.com" in location or "google" in location.lower()
    ), f"Expected Google OAuth URL, got: {location}"


async def test_google_exchange_login_new_user(client, _db, _auth_headers):
    """Test Google SSO creates a new user when none exists."""
    unique_id = uuid.uuid4().hex
    unique_email = f"test_google_new_user_{unique_id}@example.com"
    mock_user = {
        "google_id": f"test_google_new_{unique_id}",
        "email": unique_email,
        "name": "Test New Google User",
        "picture": "https://example.com/photo.jpg",
    }

    with patch(
        "shu.providers.google.auth_adapter.GoogleAuthAdapter.get_user_info", _mock_adapter_get_user_info(mock_user)
    ):
        with patch(
            "shu.providers.google.auth_adapter.GoogleAuthAdapter.exchange_code", _mock_google_adapter_exchange_code()
        ):
            response = await client.post("/api/v1/auth/google/exchange-login", json={"code": "mock_auth_code"})

    # New user should be created (may be inactive pending admin activation)
    assert response.status_code in (200, 201), f"Unexpected status: {response.status_code}, body: {response.text}"

    if response.status_code == 200:
        data = extract_data(response)
        assert "access_token" in data, f"Missing access_token in response: {data}"
        assert "refresh_token" in data, f"Missing refresh_token in response: {data}"
        assert "user" in data, f"Missing user in response: {data}"
        assert data["user"]["email"] == unique_email


async def test_google_exchange_login_existing_user_via_provider_identity(client, db, _auth_headers):
    """Test Google SSO logs in an existing Google user via ProviderIdentity table."""
    unique_id = uuid.uuid4().hex
    unique_email = f"test_google_existing_{unique_id}@example.com"
    google_id = f"test_google_existing_id_{unique_id}"

    # Create user using ORM
    user = await _create_user_with_orm(
        db,
        email=unique_email,
        name="Test Existing Google User",
        auth_method="google",
        is_active=True,
    )

    # Create provider identity (the new way to store Google identities)
    await _create_provider_identity(
        db,
        user_id=user.id,
        provider_key="google",
        account_id=google_id,
        primary_email=unique_email,
        display_name="Test Existing Google User",
    )

    mock_user = {
        "google_id": google_id,
        "email": unique_email,
        "name": "Test Existing Google User",
        "picture": None,
    }

    with patch(
        "shu.providers.google.auth_adapter.GoogleAuthAdapter.get_user_info", _mock_adapter_get_user_info(mock_user)
    ):
        with patch(
            "shu.providers.google.auth_adapter.GoogleAuthAdapter.exchange_code", _mock_google_adapter_exchange_code()
        ):
            response = await client.post("/api/v1/auth/google/exchange-login", json={"code": "mock_auth_code"})

    assert response.status_code == 200, f"Unexpected status: {response.status_code}, body: {response.text}"
    data = extract_data(response)
    assert "access_token" in data
    assert data["user"]["email"] == unique_email


async def test_google_exchange_login_links_to_existing_microsoft_user(client, db, _auth_headers):
    """Test Google SSO links to existing user with same email (e.g., Microsoft user)."""
    unique_id = uuid.uuid4().hex
    unique_email = f"test_google_link_{unique_id}@example.com"
    google_id = f"test_google_link_id_{unique_id}"

    # Create existing Microsoft user using ORM
    user = await _create_user_with_orm(
        db,
        email=unique_email,
        name="Test Microsoft User",
        auth_method="microsoft",
        is_active=True,
    )

    # Create Microsoft provider identity for the user
    await _create_provider_identity(
        db,
        user_id=user.id,
        provider_key="microsoft",
        account_id=f"test_microsoft_id_{unique_id}",
        primary_email=unique_email,
        display_name="Test Microsoft User",
    )

    mock_user = {
        "google_id": google_id,
        "email": unique_email,
        "name": "Test Microsoft User",
        "picture": None,
    }

    with patch(
        "shu.providers.google.auth_adapter.GoogleAuthAdapter.get_user_info", _mock_adapter_get_user_info(mock_user)
    ):
        with patch(
            "shu.providers.google.auth_adapter.GoogleAuthAdapter.exchange_code", _mock_google_adapter_exchange_code()
        ):
            response = await client.post("/api/v1/auth/google/exchange-login", json={"code": "mock_auth_code"})

    assert response.status_code == 200, f"Unexpected status: {response.status_code}, body: {response.text}"
    data = extract_data(response)
    assert "access_token" in data
    assert data["user"]["email"] == unique_email

    # Verify that a Google ProviderIdentity was created (identity linking)
    from sqlalchemy import select

    from shu.models.provider_identity import ProviderIdentity

    result = await db.execute(
        select(ProviderIdentity).where(ProviderIdentity.user_id == user.id, ProviderIdentity.provider_key == "google")
    )
    google_identity = result.scalar_one_or_none()
    assert google_identity is not None, "Google ProviderIdentity should be created for identity linking"
    assert google_identity.account_id == google_id


async def test_google_exchange_login_password_conflict(client, db, _auth_headers):
    """Test Google SSO returns 409 when user exists with password auth."""
    unique_id = uuid.uuid4().hex
    unique_email = f"test_google_pwd_conflict_{unique_id}@example.com"

    # Create existing password user using ORM
    await _create_user_with_orm(
        db,
        email=unique_email,
        name="Test Password User",
        auth_method="password",
        is_active=True,
        password_hash="fake_hash",
    )

    mock_user = {
        "google_id": f"test_google_pwd_{unique_id}",
        "email": unique_email,
        "name": "Test Password User",
        "picture": None,
    }

    logger.info("=== EXPECTED TEST OUTPUT: 409 conflict error for password auth user is expected ===")

    with patch(
        "shu.providers.google.auth_adapter.GoogleAuthAdapter.get_user_info", _mock_adapter_get_user_info(mock_user)
    ):
        with patch(
            "shu.providers.google.auth_adapter.GoogleAuthAdapter.exchange_code", _mock_google_adapter_exchange_code()
        ):
            response = await client.post("/api/v1/auth/google/exchange-login", json={"code": "mock_auth_code"})

    assert response.status_code == 409, f"Expected 409, got {response.status_code}, body: {response.text}"
    logger.info("=== EXPECTED TEST OUTPUT: 409 conflict occurred as expected ===")


async def test_google_exchange_login_inactive_user(client, db, _auth_headers):
    """Test Google SSO returns 400 when user account is inactive."""
    unique_id = uuid.uuid4().hex
    unique_email = f"test_google_inactive_{unique_id}@example.com"
    google_id = f"test_google_inactive_id_{unique_id}"

    # Create inactive user with Google identity using ORM
    user = await _create_user_with_orm(
        db,
        email=unique_email,
        name="Test Inactive Google User",
        auth_method="google",
        is_active=False,  # Inactive user
    )

    # Create provider identity
    await _create_provider_identity(
        db,
        user_id=user.id,
        provider_key="google",
        account_id=google_id,
        primary_email=unique_email,
        display_name="Test Inactive Google User",
    )

    mock_user = {
        "google_id": google_id,
        "email": unique_email,
        "name": "Test Inactive Google User",
        "picture": None,
    }

    logger.info("=== EXPECTED TEST OUTPUT: 400 error for inactive user is expected ===")

    with patch(
        "shu.providers.google.auth_adapter.GoogleAuthAdapter.get_user_info", _mock_adapter_get_user_info(mock_user)
    ):
        with patch(
            "shu.providers.google.auth_adapter.GoogleAuthAdapter.exchange_code", _mock_google_adapter_exchange_code()
        ):
            response = await client.post("/api/v1/auth/google/exchange-login", json={"code": "mock_auth_code"})

    assert response.status_code == 400, f"Expected 400, got {response.status_code}, body: {response.text}"
    logger.info("=== EXPECTED TEST OUTPUT: 400 error for inactive user occurred as expected ===")


async def test_google_exchange_login_missing_code(client, _db, _auth_headers):
    """Test Google SSO returns 422 when code is missing."""
    logger.info("=== EXPECTED TEST OUTPUT: 422 validation error for missing code is expected ===")

    response = await client.post("/api/v1/auth/google/exchange-login", json={})

    assert response.status_code == 422, f"Expected 422, got {response.status_code}, body: {response.text}"
    logger.info("=== EXPECTED TEST OUTPUT: 422 validation error occurred as expected ===")


class GoogleSSOTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for Google SSO functionality."""

    def get_test_functions(self) -> list[Callable]:
        """Return all Google SSO test functions."""
        return [
            test_google_login_endpoint_returns_redirect,
            test_google_exchange_login_new_user,
            test_google_exchange_login_existing_user_via_provider_identity,
            test_google_exchange_login_links_to_existing_microsoft_user,
            test_google_exchange_login_password_conflict,
            test_google_exchange_login_inactive_user,
            test_google_exchange_login_missing_code,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Google SSO Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for Google SSO authentication with ProviderIdentity [SHU-504]"


if __name__ == "__main__":
    suite = GoogleSSOTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
