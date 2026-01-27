"""
Unit tests for SSO auth adapter get_user_info() methods.

Tests cover:
- Google adapter with valid/invalid tokens (mocked)
- Microsoft adapter with valid/invalid tokens (mocked)
- Error handling for network failures
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx


class TestGoogleAuthAdapterGetUserInfo:
    """Tests for GoogleAuthAdapter.get_user_info()"""

    @pytest.fixture
    def mock_auth_capability(self):
        """Create a mock AuthCapability for adapter initialization."""
        mock = MagicMock()
        mock._settings = MagicMock()
        mock._settings.google_client_id = "test-google-client-id"
        mock._user_id = "test-user-id"
        return mock

    @pytest.fixture
    def google_adapter(self, mock_auth_capability):
        """Create a GoogleAuthAdapter instance with mocked dependencies."""
        from shu.providers.google.auth_adapter import GoogleAuthAdapter
        return GoogleAuthAdapter(mock_auth_capability)

    @pytest.mark.asyncio
    async def test_get_user_info_valid_token(self, google_adapter):
        """Test get_user_info with a valid Google ID token returns normalized user info."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "sub": "google-user-123",
            "email": "test@example.com",
            "name": "Test User",
            "picture": "https://example.com/photo.jpg",
            "aud": "test-google-client-id"
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await google_adapter.get_user_info(id_token="valid-token")

        assert result["provider_id"] == "google-user-123"
        assert result["provider_key"] == "google"
        assert result["email"] == "test@example.com"
        assert result["name"] == "Test User"
        assert result["picture"] == "https://example.com/photo.jpg"
        assert "existing_user" not in result  # No db provided

    @pytest.mark.asyncio
    async def test_get_user_info_missing_token(self, google_adapter):
        """Test get_user_info raises ValueError when id_token is missing."""
        with pytest.raises(ValueError, match="Missing Google ID token"):
            await google_adapter.get_user_info(id_token=None)

        with pytest.raises(ValueError, match="Missing Google ID token"):
            await google_adapter.get_user_info(id_token="")

    @pytest.mark.asyncio
    async def test_get_user_info_invalid_token(self, google_adapter):
        """Test get_user_info raises ValueError when token verification fails."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Invalid token"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(ValueError, match="Google token verification failed: HTTP 400"):
                await google_adapter.get_user_info(id_token="invalid-token")

    @pytest.mark.asyncio
    async def test_get_user_info_audience_mismatch(self, google_adapter):
        """Test get_user_info raises ValueError when audience doesn't match client ID."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "sub": "google-user-123",
            "email": "test@example.com",
            "name": "Test User",
            "aud": "wrong-client-id"  # Doesn't match test-google-client-id
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(ValueError, match="Invalid Google ID token: audience mismatch"):
                await google_adapter.get_user_info(id_token="valid-token")

    @pytest.mark.asyncio
    async def test_get_user_info_network_error(self, google_adapter):
        """Test get_user_info raises ValueError on network errors."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(ValueError, match="Network error during Google token verification"):
                await google_adapter.get_user_info(id_token="valid-token")

    @pytest.mark.asyncio
    async def test_get_user_info_incomplete_response(self, google_adapter):
        """Test get_user_info raises ValueError when response is missing required fields."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "sub": "google-user-123",
            "aud": "test-google-client-id"
            # Missing email
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(ValueError, match="Invalid Google ID token payload: missing sub or email"):
                await google_adapter.get_user_info(id_token="valid-token")

    @pytest.mark.asyncio
    async def test_get_user_info_name_fallback(self, google_adapter):
        """Test get_user_info uses email prefix as name fallback when name is missing."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "sub": "google-user-123",
            "email": "testuser@example.com",
            "aud": "test-google-client-id"
            # No name provided
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await google_adapter.get_user_info(id_token="valid-token")

        assert result["name"] == "testuser"

    @pytest.mark.asyncio
    async def test_get_user_info_db_param_ignored(self, google_adapter):
        """Test get_user_info ignores db parameter (kept for interface compatibility)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "sub": "google-user-123",
            "email": "test@example.com",
            "name": "Test User",
            "aud": "test-google-client-id"
        }

        mock_db = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await google_adapter.get_user_info(id_token="valid-token", db=mock_db)

        assert result["provider_id"] == "google-user-123"
        # db parameter is ignored - no database queries should be made
        mock_db.execute.assert_not_called()


class TestMicrosoftAuthAdapterGetUserInfo:
    """Tests for MicrosoftAuthAdapter.get_user_info()"""

    @pytest.fixture
    def mock_auth_capability(self):
        """Create a mock AuthCapability for adapter initialization."""
        mock = MagicMock()
        mock._settings = MagicMock()
        mock._settings.microsoft_tenant_id = "common"
        mock._user_id = "test-user-id"
        return mock

    @pytest.fixture
    def microsoft_adapter(self, mock_auth_capability):
        """Create a MicrosoftAuthAdapter instance with mocked dependencies."""
        from shu.providers.microsoft.auth_adapter import MicrosoftAuthAdapter
        return MicrosoftAuthAdapter(mock_auth_capability)

    @pytest.mark.asyncio
    async def test_get_user_info_valid_token(self, microsoft_adapter):
        """Test get_user_info with a valid Microsoft access token returns normalized user info."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "microsoft-user-456",
            "mail": "test@example.com",
            "displayName": "Test User"
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await microsoft_adapter.get_user_info(access_token="valid-token")

        assert result["provider_id"] == "microsoft-user-456"
        assert result["provider_key"] == "microsoft"
        assert result["email"] == "test@example.com"
        assert result["name"] == "Test User"
        assert result["picture"] is None

    @pytest.mark.asyncio
    async def test_get_user_info_user_principal_name_fallback(self, microsoft_adapter):
        """Test get_user_info uses userPrincipalName when mail is not available."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "microsoft-user-456",
            "userPrincipalName": "test@example.onmicrosoft.com",
            "displayName": "Test User"
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await microsoft_adapter.get_user_info(access_token="valid-token")

        assert result["email"] == "test@example.onmicrosoft.com"

    @pytest.mark.asyncio
    async def test_get_user_info_missing_token(self, microsoft_adapter):
        """Test get_user_info raises ValueError when access_token is missing."""
        with pytest.raises(ValueError, match="Missing Microsoft access token"):
            await microsoft_adapter.get_user_info(access_token=None)

        with pytest.raises(ValueError, match="Missing Microsoft access token"):
            await microsoft_adapter.get_user_info(access_token="")

    @pytest.mark.asyncio
    async def test_get_user_info_invalid_token(self, microsoft_adapter):
        """Test get_user_info raises ValueError when token is invalid."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(ValueError, match="Microsoft user info request failed: HTTP 401"):
                await microsoft_adapter.get_user_info(access_token="invalid-token")

    @pytest.mark.asyncio
    async def test_get_user_info_network_error(self, microsoft_adapter):
        """Test get_user_info raises ValueError on network errors."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(ValueError, match="Network error during Microsoft user info request"):
                await microsoft_adapter.get_user_info(access_token="valid-token")

    @pytest.mark.asyncio
    async def test_get_user_info_incomplete_response(self, microsoft_adapter):
        """Test get_user_info raises ValueError when response is missing required fields."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "microsoft-user-456"
            # Missing email/mail/userPrincipalName
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(ValueError, match="Invalid Microsoft user info: missing id or email"):
                await microsoft_adapter.get_user_info(access_token="valid-token")

    @pytest.mark.asyncio
    async def test_get_user_info_name_fallback(self, microsoft_adapter):
        """Test get_user_info uses email prefix as name fallback when displayName is missing."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "microsoft-user-456",
            "mail": "testuser@example.com"
            # No displayName provided
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await microsoft_adapter.get_user_info(access_token="valid-token")

        assert result["name"] == "testuser"
