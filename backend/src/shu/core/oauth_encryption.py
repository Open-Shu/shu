"""OAuth Token Encryption Service.

Provides secure encryption/decryption for OAuth tokens stored in the database.
Uses Fernet symmetric encryption for secure token storage.
"""

import logging

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings_instance

logger = logging.getLogger(__name__)


class OAuthEncryptionError(Exception):
    """Exception raised for OAuth encryption/decryption errors."""

    pass


class OAuthEncryptionService:
    """Service for encrypting and decrypting OAuth tokens."""

    def __init__(self) -> None:
        """Initialize the encryption service with the configured key."""
        settings = get_settings_instance()

        if not settings.oauth_encryption_key:
            raise OAuthEncryptionError(
                "OAuth encryption key not configured. Set SHU_OAUTH_ENCRYPTION_KEY environment variable."
            )

        try:
            self.fernet = Fernet(settings.oauth_encryption_key.encode())
        except Exception as e:
            raise OAuthEncryptionError(f"Invalid OAuth encryption key: {e}")

    def encrypt_token(self, token: str) -> str:
        """Encrypt an OAuth token for secure storage.

        Args:
            token: The plaintext OAuth token

        Returns:
            The encrypted token as a base64-encoded string

        Raises:
            OAuthEncryptionError: If encryption fails

        """
        if not token:
            raise OAuthEncryptionError("Cannot encrypt empty token")

        try:
            encrypted_bytes = self.fernet.encrypt(token.encode())
            return encrypted_bytes.decode()
        except Exception as e:
            logger.error(f"Failed to encrypt OAuth token: {e}")
            raise OAuthEncryptionError(f"Token encryption failed: {e}")

    def decrypt_token(self, encrypted_token: str) -> str:
        """Decrypt an OAuth token for use.

        Args:
            encrypted_token: The encrypted token as a base64-encoded string

        Returns:
            The decrypted plaintext token

        Raises:
            OAuthEncryptionError: If decryption fails

        """
        if not encrypted_token:
            raise OAuthEncryptionError("Cannot decrypt empty token")

        try:
            decrypted_bytes = self.fernet.decrypt(encrypted_token.encode())
            return decrypted_bytes.decode()
        except InvalidToken:
            logger.error("Failed to decrypt OAuth token: Invalid token or key")
            raise OAuthEncryptionError("Token decryption failed: Invalid token or key")
        except Exception as e:
            logger.error(f"Failed to decrypt OAuth token: {e}")
            raise OAuthEncryptionError(f"Token decryption failed: {e}")

    def is_token_encrypted(self, token: str) -> bool:
        """Check if a token appears to be encrypted (vs plaintext).

        This is a heuristic check based on the token format.
        Fernet tokens are base64-encoded and have a specific structure.

        Args:
            token: The token to check

        Returns:
            True if the token appears to be encrypted, False otherwise

        """
        if not token:
            return False

        try:
            # Try to decrypt - if it works, it's encrypted
            self.fernet.decrypt(token.encode())
            return True
        except (InvalidToken, Exception):
            # If decryption fails, assume it's plaintext
            return False


# Global encryption service instance
_oauth_encryption_service: OAuthEncryptionService | None = None


def get_oauth_encryption_service() -> OAuthEncryptionService:
    """Get the global OAuth encryption service instance.

    Returns:
        The OAuth encryption service instance

    Raises:
        OAuthEncryptionError: If the service cannot be initialized

    """
    global _oauth_encryption_service

    if _oauth_encryption_service is None:
        _oauth_encryption_service = OAuthEncryptionService()

    return _oauth_encryption_service


def encrypt_oauth_token(token: str) -> str:
    """Convenience function to encrypt an OAuth token.

    Args:
        token: The plaintext OAuth token

    Returns:
        The encrypted token

    """
    service = get_oauth_encryption_service()
    return service.encrypt_token(token)


def decrypt_oauth_token(encrypted_token: str) -> str:
    """Convenience function to decrypt an OAuth token.

    Args:
        encrypted_token: The encrypted token

    Returns:
        The decrypted plaintext token

    """
    service = get_oauth_encryption_service()
    return service.decrypt_token(encrypted_token)
