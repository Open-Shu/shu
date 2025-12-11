"""
OAuth Token Encryption Integration Tests

Tests for the OAuth token encryption functionality to ensure tokens
are properly encrypted when stored and decrypted when retrieved.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import sys

from shu.models.provider_credential import ProviderCredential
from shu.core.oauth_encryption import (
    OAuthEncryptionService,
    OAuthEncryptionError,
    get_oauth_encryption_service,
    encrypt_oauth_token,
    decrypt_oauth_token
)
import shu.core.oauth_encryption as oe
from integ.base_integration_test import BaseIntegrationTestSuite
from integ.integration_test_runner import run_integration_test_suite


class TestOAuthEncryptionIntegration:
    """Integration tests for OAuth token encryption."""

    @staticmethod
    def _test_tokens():
        """Sample OAuth tokens for testing."""
        return {
            "access_token": "ya29.a0AfH6SMBxyz123...",
            "refresh_token": "1//04abc123def456..."
        }

    @staticmethod
    def _tokens():
        return TestOAuthEncryptionIntegration._test_tokens()
    
    async def test_oauth_encryption_service_initialization(self):
        """Test that the OAuth encryption service initializes correctly."""
        service = OAuthEncryptionService()
        assert service.fernet is not None

    
    async def test_token_encryption_decryption(self):
        """Test basic token encryption and decryption."""
        service = OAuthEncryptionService()
        tokens = self._tokens()

        # Test access token
        encrypted_access = service.encrypt_token(tokens["access_token"])
        assert encrypted_access != tokens["access_token"]
        assert len(encrypted_access) > len(tokens["access_token"])

        decrypted_access = service.decrypt_token(encrypted_access)
        assert decrypted_access == tokens["access_token"]

        # Test refresh token
        encrypted_refresh = service.encrypt_token(tokens["refresh_token"])
        assert encrypted_refresh != tokens["refresh_token"]

        decrypted_refresh = service.decrypt_token(encrypted_refresh)
        assert decrypted_refresh == tokens["refresh_token"]
    
    async def test_token_encryption_detection(self):
        """Test that the service can detect encrypted vs plaintext tokens."""
        service = OAuthEncryptionService()

        # Plaintext token should not be detected as encrypted
        tokens = self._tokens()
        assert not service.is_token_encrypted(tokens["access_token"])

        # Encrypted token should be detected as encrypted
        encrypted_token = service.encrypt_token(tokens["access_token"])
        assert service.is_token_encrypted(encrypted_token)

    async def test_provider_credential_encryption_methods(self):
        """Test the encryption methods on ProviderCredential model."""

        # Create credentials instance
        credentials = ProviderCredential(
            user_id=str(uuid.uuid4()),
            provider_key="google",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            is_active=True
        )

        # Test setting and getting access token
        tokens = self._tokens()
        credentials.set_access_token(tokens["access_token"])
        assert credentials.access_token_encrypted != tokens["access_token"]

        retrieved_access = credentials.get_access_token()
        assert retrieved_access == tokens["access_token"]

        # Test setting and getting refresh token
        credentials.set_refresh_token(tokens["refresh_token"])
        assert credentials.refresh_token_encrypted != tokens["refresh_token"]

        retrieved_refresh = credentials.get_refresh_token()
        assert retrieved_refresh == tokens["refresh_token"]

    async def test_provider_credential_client_metadata_and_tokens(self):
        """Test that ProviderCredential stores client metadata and decrypts tokens via getters."""

        # Create credentials with encrypted tokens + client metadata
        credentials = ProviderCredential(
            user_id=str(uuid.uuid4()),
            provider_key="google",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            client_id="test-client-id",
            client_secret="test-client-secret",
            is_active=True
        )

        tokens = self._tokens()
        credentials.set_access_token(tokens["access_token"])
        credentials.set_refresh_token(tokens["refresh_token"])

        # Build OAuth dict via getters/fields (ProviderCredential has no to_oauth_dict)
        oauth_dict = {
            "token": credentials.get_access_token(),
            "refresh_token": credentials.get_refresh_token(),
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
        }

        # Verify tokens are decrypted in the dict
        tokens = self._tokens()
        assert oauth_dict["token"] == tokens["access_token"]
        assert oauth_dict["refresh_token"] == tokens["refresh_token"]
        assert oauth_dict["client_id"] == "test-client-id"
        assert oauth_dict["client_secret"] == "test-client-secret"

    async def test_plaintext_tokens_raise_error(self):
        """ProviderCredential getters should raise errors if tokens are stored plaintext."""
        # Create credentials with plaintext tokens (simulating invalid legacy data)
        tokens = self._tokens()
        credentials = ProviderCredential(
            user_id=str(uuid.uuid4()),
            provider_key="google",
            access_token_encrypted=tokens["access_token"],  # Plaintext
            refresh_token_encrypted=tokens["refresh_token"],  # Plaintext
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            is_active=True
        )

        # Getting tokens should raise OAuthEncryptionError
        raised_access = False
        try:
            _ = credentials.get_access_token()
        except OAuthEncryptionError:
            raised_access = True
        assert raised_access, "Expected OAuthEncryptionError for plaintext access token"

        raised_refresh = False
        try:
            _ = credentials.get_refresh_token()
        except OAuthEncryptionError:
            raised_refresh = True
        assert raised_refresh, "Expected OAuthEncryptionError for plaintext refresh token"

    async def test_convenience_functions(self):
        """Test the convenience encryption/decryption functions."""
        tokens = self._tokens()
        encrypted = encrypt_oauth_token(tokens["access_token"])
        assert encrypted != tokens["access_token"]

        decrypted = decrypt_oauth_token(encrypted)
        assert decrypted == tokens["access_token"]
    
    async def test_error_handling(self):
        """Test error handling for invalid tokens and keys."""
        service = OAuthEncryptionService()

        # Empty token encryption should raise
        raised = False
        try:
            service.encrypt_token("")
        except OAuthEncryptionError:
            raised = True
        assert raised, "Expected OAuthEncryptionError for empty encrypt"

        # Empty token decryption should raise
        raised = False
        try:
            service.decrypt_token("")
        except OAuthEncryptionError:
            raised = True
        assert raised, "Expected OAuthEncryptionError for empty decrypt"

        # Invalid encrypted token should raise
        raised = False
        try:
            service.decrypt_token("invalid-encrypted-token")
        except OAuthEncryptionError:
            raised = True
        assert raised, "Expected OAuthEncryptionError for invalid token"


# Integration test runner configuration using BaseIntegrationTestSuite pattern
from typing import List, Callable

class OAuthEncryptionIntegrationSuite(BaseIntegrationTestSuite):
    def get_test_functions(self) -> List[Callable]:
        t = TestOAuthEncryptionIntegration()
        # Wrap methods to call with (client, db, auth_headers) even if unused
        return [
            lambda client, db, auth_headers: t.test_oauth_encryption_service_initialization(),
            lambda client, db, auth_headers: t.test_token_encryption_decryption(),
            lambda client, db, auth_headers: t.test_token_encryption_detection(),
            lambda client, db, auth_headers: t.test_provider_credential_encryption_methods(),
            lambda client, db, auth_headers: t.test_provider_credential_client_metadata_and_tokens(),
            lambda client, db, auth_headers: t.test_plaintext_tokens_raise_error(),
            lambda client, db, auth_headers: t.test_convenience_functions(),
            lambda client, db, auth_headers: t.test_error_handling(),
        ]

    def get_suite_name(self) -> str:
        return "OAuth Encryption Integration"

    def get_suite_description(self) -> str:
        return "Integration tests for OAuth token encryption and credential utilities"

if __name__ == "__main__":
    suite = OAuthEncryptionIntegrationSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
