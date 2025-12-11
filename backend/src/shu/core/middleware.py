"""
Custom middleware for Shu RAG Backend.

This module provides middleware for request tracking, timing, and other
cross-cutting concerns.
"""

import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from typing import Callable, Set
import logging

from ..auth.jwt_manager import JWTManager
from ..core.config import get_settings_instance

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware to add unique request IDs to all requests."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate or extract request ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        
        # Store request ID in request state
        request.state.request_id = request_id
        
        # Call the next middleware/route handler
        response = await call_next(request)
        
        # Add request ID to response headers
        response.headers["X-Request-ID"] = request_id
        
        return response


class TimingMiddleware(BaseHTTPMiddleware):
    """Enhanced timing middleware with query performance tracking."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Record start time
        start_time = time.time()
        
        # Track database operations for query endpoints
        db_operations = []
        if "/query/" in request.url.path:
            # We could track database operations here if needed
            pass
        
        # Call the next middleware/route handler
        response = await call_next(request)
        
        # Calculate duration
        duration = time.time() - start_time
        
        # Add timing header
        response.headers["X-Response-Time"] = f"{duration:.3f}s"
        
        # Enhanced logging for query endpoints
        if "/query/" in request.url.path:
            logger.info("Query performance", extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration * 1000, 2),
                "request_id": getattr(request.state, "request_id", "unknown"),
                "user_id": getattr(request.state, "user", {}).get("user_id", "anonymous"),
                "db_operations": len(db_operations) if db_operations else 0
            })
        else:
            # Standard request logging
            logger.info(
                "Request processed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round(duration * 1000, 2),
                    "request_id": getattr(request.state, "request_id", "unknown"),
                    "user_id": getattr(request.state, "user", {}).get("user_id", "anonymous"),
            }
        )
        
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Enable FedCM API for same-origin contexts
        # Note: This is intentionally narrow; we only enable identity-credentials-get for self.
        response.headers["Permissions-Policy"] = "identity-credentials-get=(self)"

        return response


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Global authentication middleware to enforce auth on protected endpoints."""

    def __init__(self, app):
        super().__init__(app)
        self.jwt_manager = JWTManager()

        # Public endpoints that don't require authentication
        self.public_paths: Set[str] = {
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/v1/health/liveness",
            "/api/v1/health/readiness",
            "/api/v1/config/public",
            "/api/v1/auth/login",
            "/api/v1/auth/register",
            "/api/v1/auth/login/password",
            "/api/v1/auth/refresh",
            "/api/v1/auth/google/login",
            "/api/v1/auth/google/exchange-login",
            # Host-auth OAuth callbacks must be public (popup redirects from Google)
            "/api/v1/host/auth/callback",
            "/auth/callback",
            "/api/v1/settings/branding",
            "/api/v1/settings/branding/",
        }

    def _is_public_path(self, path: str) -> bool:
        """Check if the path is public and doesn't require authentication."""
        # Exact match
        if path in self.public_paths:
            return True

        # Check for path prefixes that should be public
        public_prefixes = [
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/v1/settings/branding/assets/",
        ]
        for prefix in public_prefixes:
            if path.startswith(prefix):
                return True

        return False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip authentication for public endpoints
        if self._is_public_path(request.url.path):
            return await call_next(request)

        # Extract Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            logger.warning(f"Missing Authorization header for {request.method} {request.url.path}")
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})

        # Accept either Bearer <jwt> or ApiKey <key>
        is_api_key_auth = False
        token = None
        user_data = None
        if auth_header.startswith("Bearer "):
            # Extract and validate JWT access token
            token = auth_header.split(" ", 1)[1]
            user_data = self.jwt_manager.extract_user_from_token(token)

            if not user_data:
                logger.warning(f"Invalid or expired token for {request.method} {request.url.path}")
                return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})
        elif auth_header.startswith("ApiKey "):
            # Validate global API key (Tier 0)
            settings = get_settings_instance()
            provided_key = auth_header.split(" ", 1)[1]
            if not settings.api_key or provided_key != settings.api_key:
                logger.warning("Invalid API key presented")
                return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
            # Mark request as API key authenticated; RBAC will resolve user context
            request.state.api_key_authenticated = True
            is_api_key_auth = True
        else:
            logger.warning(f"Unsupported Authorization scheme for {request.method} {request.url.path}")
            return JSONResponse(status_code=401, content={"detail": "Unsupported Authorization scheme"})

        # Check if token is near expiry for sliding expiration (Bearer only)
        if (not getattr(request.state, "api_key_authenticated", False)) and token and self.jwt_manager.is_token_near_expiry(token, buffer_minutes=10):
            # Add header to indicate client should refresh token
            request.state.token_needs_refresh = True

        # SECURITY FIX: Check current user status in database
        # JWT tokens contain user data from when they were created, but we need to verify
        # the user is still active in the database
        try:
            from ..core.database import get_db
            from ..auth.models import User
            from sqlalchemy import select

            # Get database session
            async for db in get_db():
                # Determine lookup based on auth mode
                if getattr(request.state, "api_key_authenticated", False):
                    settings = get_settings_instance()
                    if not settings.api_key_user_email:
                        logger.warning("API key user mapping not configured (SHU_API_KEY_USER_EMAIL missing)")
                        return JSONResponse(
                            status_code=401,
                            content={"detail": "API key user mapping not configured"}
                        )
                    stmt = select(User).where(User.email == settings.api_key_user_email)
                else:
                    stmt = select(User).where(User.id == user_data['user_id'])

                result = await db.execute(stmt)
                current_user = result.scalar_one_or_none()

                if not current_user:
                    missing = settings.api_key_user_email if getattr(request.state, "api_key_authenticated", False) else user_data.get('user_id')
                    logger.warning(f"User not found in database for {request.method} {request.url.path}: {missing}")
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "User account not found"}
                    )

                if not current_user.is_active:
                    logger.warning(f"Inactive user {current_user.email} attempted access to {request.method} {request.url.path}")
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "User account is inactive. Please contact an administrator for activation."}
                    )

                # Build user context for RBAC
                if getattr(request.state, "api_key_authenticated", False):
                    user_data = {
                        'user_id': current_user.id,
                        'email': current_user.email,
                        'name': current_user.name,
                        'role': current_user.role,
                        'is_active': current_user.is_active
                    }
                else:
                    # Update user data with current database values to ensure consistency
                    user_data.update({
                        'email': current_user.email,
                        'name': current_user.name,
                        'role': current_user.role,
                        'is_active': current_user.is_active
                    })
                break

        except Exception as e:
            logger.error(f"Database error during user validation: {e}")
            return JSONResponse(
                status_code=500,
                content={"detail": "Authentication validation failed"}
            )

        # Store user data in request state for role-based authorization
        request.state.user = user_data

        logger.debug(f"Authenticated user {user_data['email']} ({user_data['role']}) for {request.method} {request.url.path}")

        # Process the request
        response = await call_next(request)

        # Add refresh header if token needs refresh
        if hasattr(request.state, 'token_needs_refresh') and request.state.token_needs_refresh:
            response.headers["X-Token-Refresh-Needed"] = "true"

        return response

