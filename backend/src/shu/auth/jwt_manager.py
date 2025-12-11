"""JWT token management for Shu authentication"""

from jose import jwt, JWTError
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
import logging

from ..core.config import get_settings_instance

logger = logging.getLogger(__name__)

class JWTManager:
    """JWT token management for Shu authentication"""
    
    def __init__(self):
        settings = get_settings_instance()
        self.secret_key = settings.jwt_secret_key
        self.algorithm = "HS256"
        self.access_token_expire_minutes = settings.jwt_access_token_expire_minutes
        self.refresh_token_expire_days = settings.jwt_refresh_token_expire_days

        if not self.secret_key:
            raise ValueError("JWT_SECRET_KEY not configured in settings")
    
    def create_access_token(self, user_data: Dict[str, Any]) -> str:
        """Create JWT access token with user information"""
        if not self.secret_key:
            raise ValueError("JWT secret key not configured")

        expire = datetime.now(timezone.utc) + timedelta(minutes=self.access_token_expire_minutes)

        payload = {
            "user_id": user_data["user_id"],
            "email": user_data["email"],
            "role": user_data["role"],
            "exp": expire,
            "iat": datetime.now(timezone.utc),
            "type": "access"
        }

        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
    
    def create_refresh_token(self, user_id: str) -> str:
        """Create JWT refresh token"""
        if not self.secret_key:
            raise ValueError("JWT secret key not configured")

        expire = datetime.now(timezone.utc) + timedelta(days=self.refresh_token_expire_days)

        payload = {
            "user_id": user_id,
            "exp": expire,
            "iat": datetime.now(timezone.utc),
            "type": "refresh"
        }

        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
    
    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify and decode JWT token"""
        if not self.secret_key:
            logger.error("JWT secret key not configured")
            return None

        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
        except JWTError as e:
            logger.warning(f"JWT verification failed: {e}")
            return None
    
    def extract_user_from_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Extract user information from access token"""
        payload = self.verify_token(token)
        if not payload or payload.get("type") != "access":
            return None
        
        return {
            "user_id": payload.get("user_id"),
            "email": payload.get("email"),
            "role": payload.get("role")
        }
    
    def is_token_expired(self, token: str) -> bool:
        """Check if token is expired without raising exception"""
        if not self.secret_key:
            return True

        try:
            jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return False
        except JWTError:
            return True

    def is_token_near_expiry(self, token: str, buffer_minutes: int = 5) -> bool:
        """Check if token will expire within buffer_minutes"""
        if not self.secret_key:
            return True

        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            exp_timestamp = payload.get("exp")
            if not exp_timestamp:
                return True

            exp_datetime = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
            buffer_time = datetime.now(timezone.utc) + timedelta(minutes=buffer_minutes)

            return buffer_time >= exp_datetime
        except JWTError:
            return True

    def refresh_access_token(self, refresh_token: str) -> Optional[str]:
        """Create new access token from valid refresh token"""
        if not self.secret_key:
            logger.error("JWT secret key not configured")
            return None

        try:
            # Verify refresh token
            payload = jwt.decode(refresh_token, self.secret_key, algorithms=[self.algorithm])

            # Check if it's a refresh token
            if payload.get("type") != "refresh":
                logger.warning("Token is not a refresh token")
                return None

            user_id = payload.get("user_id")
            if not user_id:
                logger.warning("No user_id in refresh token")
                return None

            # For refresh, we need to get current user data from database
            # This will be handled by the endpoint that calls this method
            return user_id

        except JWTError as e:
            logger.warning(f"Refresh token verification failed: {e}")
            return None
