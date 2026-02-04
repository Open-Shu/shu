"""Google Single Sign-On authentication for Shu."""

import logging
from typing import Any

import logging
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow

from ..core.config import get_settings_instance

logger = logging.getLogger(__name__)


class GoogleSSOAuth:
    """Google Single Sign-On authentication handler."""

    def __init__(self) -> None:
        settings = get_settings_instance()
        self.client_id = settings.google_client_id
        self.client_secret = settings.google_client_secret
        self.redirect_uri = settings.google_redirect_uri
        # Defer hard failure to call-sites; allow service to start without Google creds
        self.enabled = bool(self.client_id and self.client_secret)

    async def verify_token(self, token: str) -> dict[str, Any]:
        """Verify Google ID token and return user information."""
        if not self.enabled:
            raise ValueError("Google OAuth2 is not configured/enabled")
        try:
            # Verify the token with Google
            idinfo = id_token.verify_oauth2_token(token, Request(), self.client_id)

            # Verify the token is for our application
            if idinfo["aud"] != self.client_id:
                raise ValueError("Invalid audience")

            return {
                "google_id": idinfo["sub"],
                "email": idinfo["email"],
                "name": idinfo["name"],
                "picture": idinfo.get("picture"),
                "verified_email": idinfo.get("email_verified", False),
            }

        except ValueError as e:
            logger.error(f"Token verification failed: {e}")
            raise ValueError(f"Invalid token: {e}")

    def get_authorization_url(self) -> str:
        """Get the Google OAuth2 authorization URL."""
        if not self.enabled:
            raise ValueError("Google OAuth2 is not configured/enabled")
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [self.redirect_uri],
                }
            },
            scopes=["openid", "email", "profile"],
        )
        flow.redirect_uri = self.redirect_uri

        authorization_url, _ = flow.authorization_url(access_type="offline", include_granted_scopes="true")

        return authorization_url

    async def exchange_code_for_token(self, code: str) -> dict[str, Any]:
        """Exchange authorization code for access token and user info."""
        if not self.enabled:
            raise ValueError("Google OAuth2 is not configured/enabled")
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [self.redirect_uri],
                }
            },
            scopes=["openid", "email", "profile"],
        )
        flow.redirect_uri = self.redirect_uri

        # Exchange the authorization code for tokens
        flow.fetch_token(code=code)

        # Get user info from the ID token
        credentials = flow.credentials
        id_token_jwt = credentials.id_token

        return await self.verify_token(id_token_jwt)
