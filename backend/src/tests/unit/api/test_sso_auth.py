"""
Unit tests for unified SSO authentication method.

Tests cover:
- New user creation (first user, admin email, regular user)
- Existing user login via ProviderIdentity
- Password auth conflict (409)
- Inactive user (400)
- Identity linking for existing email

Requirements: 3.3, 3.4, 3.5, 3.6
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from fastapi import HTTPException


class TestAuthenticateOrCreateSSOUser:
    """Tests for UserService.authenticate_or_create_sso_user()"""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def user_service(self):
        """Create a UserService instance with mocked dependencies."""
        from shu.services.user_service import UserService
        service = UserService()
        service.settings = MagicMock()
        service.settings.admin_emails = ["admin@example.com"]
        return service

    @pytest.fixture
    def google_provider_info(self):
        """Sample Google provider info."""
        return {
            "provider_id": "google-user-123",
            "provider_key": "google",
            "email": "test@example.com",
            "name": "Test User",
            "picture": "https://example.com/photo.jpg"
        }

    @pytest.fixture
    def microsoft_provider_info(self):
        """Sample Microsoft provider info."""
        return {
            "provider_id": "microsoft-user-456",
            "provider_key": "microsoft",
            "email": "test@example.com",
            "name": "Test User",
            "picture": None
        }

    @pytest.mark.asyncio
    async def test_new_user_first_user_becomes_admin(self, user_service, mock_db, google_provider_info):
        """Test that the first user in the system becomes admin and is active."""
        # Mock: no existing auth method
        mock_db.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # get_user_auth_method
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # _get_user_by_identity (ProviderIdentity)
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # email lookup
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),  # is_first_user
        ])
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.flush = AsyncMock()

        with patch.object(user_service, 'is_first_user', return_value=True):
            with patch.object(user_service, 'determine_user_role') as mock_role:
                from shu.auth.models import UserRole
                mock_role.return_value = UserRole.ADMIN
                with patch.object(user_service, 'is_active', return_value=True):
                    with patch.object(user_service, '_get_user_by_identity', return_value=None):
                        with patch.object(user_service, 'get_user_auth_method', return_value=None):
                            with patch.object(user_service, '_create_provider_identity', return_value=MagicMock()):
                                user = await user_service.authenticate_or_create_sso_user(google_provider_info, mock_db)

        assert user.email == "test@example.com"
        assert user.role == "admin"
        assert user.is_active is True

    @pytest.mark.asyncio
    async def test_new_user_admin_email_becomes_admin(self, user_service, mock_db, google_provider_info):
        """Test that a user with admin email becomes admin and is active."""
        google_provider_info["email"] = "admin@example.com"

        with patch.object(user_service, 'is_first_user', return_value=False):
            with patch.object(user_service, 'determine_user_role') as mock_role:
                from shu.auth.models import UserRole
                mock_role.return_value = UserRole.ADMIN
                with patch.object(user_service, 'is_active', return_value=True):
                    with patch.object(user_service, '_get_user_by_identity', return_value=None):
                        with patch.object(user_service, 'get_user_auth_method', return_value=None):
                            with patch.object(user_service, '_create_provider_identity', return_value=MagicMock()):
                                mock_db.add = MagicMock()
                                mock_db.commit = AsyncMock()
                                mock_db.refresh = AsyncMock()
                                mock_db.flush = AsyncMock()
                                # Mock email lookup to return None (no existing user)
                                mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

                                user = await user_service.authenticate_or_create_sso_user(google_provider_info, mock_db)

        assert user.email == "admin@example.com"
        assert user.role == "admin"
        assert user.is_active is True

    @pytest.mark.asyncio
    async def test_new_user_regular_requires_activation(self, user_service, mock_db, google_provider_info):
        """Test that a regular user requires activation (201 response)."""
        with patch.object(user_service, 'is_first_user', return_value=False):
            with patch.object(user_service, 'determine_user_role') as mock_role:
                from shu.auth.models import UserRole
                mock_role.return_value = UserRole.REGULAR_USER
                with patch.object(user_service, 'is_active', return_value=False):
                    with patch.object(user_service, '_get_user_by_identity', return_value=None):
                        with patch.object(user_service, 'get_user_auth_method', return_value=None):
                            with patch.object(user_service, '_create_provider_identity', return_value=MagicMock()):
                                mock_db.add = MagicMock()
                                mock_db.commit = AsyncMock()
                                mock_db.refresh = AsyncMock()
                                mock_db.flush = AsyncMock()
                                mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

                                with pytest.raises(HTTPException) as exc_info:
                                    await user_service.authenticate_or_create_sso_user(google_provider_info, mock_db)

        assert exc_info.value.status_code == 201
        assert "activation" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_existing_user_login_via_provider_identity(self, user_service, mock_db, google_provider_info):
        """Test that existing user can login via ProviderIdentity."""
        mock_user = MagicMock()
        mock_user.id = "user-uuid"
        mock_user.email = "test@example.com"
        mock_user.is_active = True
        mock_user.picture_url = None

        with patch.object(user_service, 'get_user_auth_method', return_value="google"):
            with patch.object(user_service, '_get_user_by_identity', return_value=mock_user):
                mock_db.commit = AsyncMock()

                user = await user_service.authenticate_or_create_sso_user(google_provider_info, mock_db)

        assert user == mock_user
        assert user.last_login is not None

    @pytest.mark.asyncio
    async def test_password_auth_conflict_returns_409(self, user_service, mock_db, google_provider_info):
        """Test that password auth conflict returns 409."""
        with patch.object(user_service, 'get_user_auth_method', return_value="password"):
            with pytest.raises(HTTPException) as exc_info:
                await user_service.authenticate_or_create_sso_user(google_provider_info, mock_db)

        assert exc_info.value.status_code == 409
        assert "password authentication" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_inactive_user_returns_400(self, user_service, mock_db, google_provider_info):
        """Test that inactive user returns 400."""
        mock_user = MagicMock()
        mock_user.id = "user-uuid"
        mock_user.email = "test@example.com"
        mock_user.is_active = False

        with patch.object(user_service, 'get_user_auth_method', return_value="google"):
            with patch.object(user_service, '_get_user_by_identity', return_value=mock_user):
                with pytest.raises(HTTPException) as exc_info:
                    await user_service.authenticate_or_create_sso_user(google_provider_info, mock_db)

        assert exc_info.value.status_code == 400
        assert "inactive" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_identity_linking_for_existing_email(self, user_service, mock_db, microsoft_provider_info):
        """Test that new provider identity is linked to existing user with same email."""
        mock_user = MagicMock()
        mock_user.id = "user-uuid"
        mock_user.email = "test@example.com"
        mock_user.is_active = True
        mock_user.picture_url = None

        with patch.object(user_service, 'get_user_auth_method', return_value="google"):  # Existing Google user
            with patch.object(user_service, '_get_user_by_identity', return_value=None):  # No Microsoft identity yet
                with patch.object(user_service, '_create_provider_identity', return_value=MagicMock()) as mock_create:
                    mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_user)))
                    mock_db.commit = AsyncMock()

                    user = await user_service.authenticate_or_create_sso_user(microsoft_provider_info, mock_db)

        assert user == mock_user
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_existing_user_from_adapter_backward_compat(self, user_service, mock_db, google_provider_info):
        """Test backward compatibility when adapter provides existing_user (legacy google_id lookup)."""
        mock_user = MagicMock()
        mock_user.id = "user-uuid"
        mock_user.email = "test@example.com"
        mock_user.is_active = True
        mock_user.picture_url = None

        google_provider_info["existing_user"] = mock_user

        with patch.object(user_service, 'get_user_auth_method', return_value="google"):
            with patch.object(user_service, '_ensure_provider_identity', return_value=None) as mock_ensure:
                mock_db.commit = AsyncMock()

                user = await user_service.authenticate_or_create_sso_user(google_provider_info, mock_db)

        assert user == mock_user
        mock_ensure.assert_called_once()

    @pytest.mark.asyncio
    async def test_inactive_user_from_adapter_returns_400(self, user_service, mock_db, google_provider_info):
        """Test that inactive user from adapter (legacy lookup) returns 400."""
        mock_user = MagicMock()
        mock_user.id = "user-uuid"
        mock_user.email = "test@example.com"
        mock_user.is_active = False

        google_provider_info["existing_user"] = mock_user

        with patch.object(user_service, 'get_user_auth_method', return_value="google"):
            with pytest.raises(HTTPException) as exc_info:
                await user_service.authenticate_or_create_sso_user(google_provider_info, mock_db)

        assert exc_info.value.status_code == 400
        assert "inactive" in exc_info.value.detail.lower()


class TestCreateTokenResponse:
    """Tests for create_token_response() helper function."""

    def test_create_token_response_returns_valid_response(self):
        """Test that create_token_response returns a valid dict for TokenResponse."""
        from shu.services.user_service import create_token_response
        from shu.auth import JWTManager

        mock_user = MagicMock()
        mock_user.id = "user-uuid"
        mock_user.to_dict.return_value = {
            "user_id": "user-uuid",
            "email": "test@example.com",
            "name": "Test User",
            "role": "regular_user"
        }

        mock_jwt_manager = MagicMock(spec=JWTManager)
        mock_jwt_manager.create_access_token.return_value = "access-token-123"
        mock_jwt_manager.create_refresh_token.return_value = "refresh-token-456"

        response = create_token_response(mock_user, mock_jwt_manager)

        # create_token_response now returns a dict that can be unpacked into TokenResponse
        assert isinstance(response, dict)
        assert response["access_token"] == "access-token-123"
        assert response["refresh_token"] == "refresh-token-456"
        assert response["token_type"] == "bearer"
        assert response["user"]["user_id"] == "user-uuid"
        mock_jwt_manager.create_access_token.assert_called_once()
        mock_jwt_manager.create_refresh_token.assert_called_once_with("user-uuid")


class TestHelperMethods:
    """Tests for helper methods in UserService."""

    @pytest.fixture
    def user_service(self):
        """Create a UserService instance."""
        from shu.services.user_service import UserService
        return UserService()

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_get_user_by_identity_returns_user(self, user_service, mock_db):
        """Test _get_user_by_identity returns user when identity exists."""
        mock_identity = MagicMock()
        mock_identity.user_id = "user-uuid"

        mock_user = MagicMock()
        mock_user.id = "user-uuid"

        # First call returns identity, second returns user
        mock_db.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=mock_identity)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=mock_user))
        ])

        result = await user_service._get_user_by_identity("google", "google-123", mock_db)

        assert result == mock_user

    @pytest.mark.asyncio
    async def test_get_user_by_identity_returns_none_when_not_found(self, user_service, mock_db):
        """Test _get_user_by_identity returns None when identity doesn't exist."""
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        result = await user_service._get_user_by_identity("google", "google-123", mock_db)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_by_identity_raises_on_orphaned_identity(self, user_service, mock_db):
        """Test _get_user_by_identity raises 500 when identity exists but user doesn't."""
        mock_identity = MagicMock()
        mock_identity.user_id = "user-uuid"

        # First call returns identity, second returns None (orphaned)
        mock_db.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=mock_identity)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        ])

        with pytest.raises(HTTPException) as exc_info:
            await user_service._get_user_by_identity("google", "google-123", mock_db)

        assert exc_info.value.status_code == 500
        assert "inconsistency" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_ensure_provider_identity_creates_when_missing(self, user_service, mock_db):
        """Test _ensure_provider_identity creates identity when missing."""
        mock_user = MagicMock()
        mock_user.id = "user-uuid"

        provider_info = {
            "provider_id": "google-123",
            "provider_key": "google",
            "email": "test@example.com",
            "name": "Test User"
        }

        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        with patch.object(user_service, '_create_provider_identity', return_value=MagicMock()) as mock_create:
            await user_service._ensure_provider_identity(mock_user, provider_info, mock_db)

        mock_create.assert_called_once_with(mock_user, provider_info, mock_db)

    @pytest.mark.asyncio
    async def test_ensure_provider_identity_skips_when_exists(self, user_service, mock_db):
        """Test _ensure_provider_identity skips creation when identity exists."""
        mock_user = MagicMock()
        mock_user.id = "user-uuid"

        provider_info = {
            "provider_id": "google-123",
            "provider_key": "google",
            "email": "test@example.com",
            "name": "Test User"
        }

        mock_identity = MagicMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_identity)))

        with patch.object(user_service, '_create_provider_identity', return_value=MagicMock()) as mock_create:
            await user_service._ensure_provider_identity(mock_user, provider_info, mock_db)

        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_provider_identity_creates_row(self, user_service, mock_db):
        """Test _create_provider_identity creates a ProviderIdentity row."""
        mock_user = MagicMock()
        mock_user.id = "user-uuid"

        provider_info = {
            "provider_id": "google-123",
            "provider_key": "google",
            "email": "test@example.com",
            "name": "Test User",
            "picture": "https://example.com/photo.jpg"
        }

        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        result = await user_service._create_provider_identity(mock_user, provider_info, mock_db)

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()
        # Verify the identity was created with correct values
        added_identity = mock_db.add.call_args[0][0]
        assert added_identity.user_id == "user-uuid"
        assert added_identity.provider_key == "google"
        assert added_identity.account_id == "google-123"
        assert added_identity.primary_email == "test@example.com"
        assert added_identity.display_name == "Test User"
        assert added_identity.avatar_url == "https://example.com/photo.jpg"
