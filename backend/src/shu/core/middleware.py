"""Custom middleware for Shu RAG Backend.

This module provides middleware for request tracking, timing, and other
cross-cutting concerns.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC
from typing import TYPE_CHECKING, ClassVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..auth.jwt_manager import JWTManager
from ..core.config import get_settings_instance

if TYPE_CHECKING:
    from .rate_limiting import RateLimitService

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
            logger.info(
                "Query performance",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round(duration * 1000, 2),
                    "request_id": getattr(request.state, "request_id", "unknown"),
                    "user_id": getattr(request.state, "user", {}).get("user_id", "anonymous"),
                    "db_operations": len(db_operations) if db_operations else 0,
                },
            )
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
                },
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

    def __init__(self, app) -> None:
        super().__init__(app)
        self.jwt_manager = JWTManager()

        # Public endpoints that don't require authentication
        self.public_paths: set[str] = {
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
            "/api/v1/auth/microsoft/login",
            "/api/v1/auth/microsoft/exchange-login",
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
            "/static/",  # Self-hosted static assets (e.g., ReDoc JS)
            "/api/v1/settings/branding/assets/",
        ]
        return any(path.startswith(prefix) for prefix in public_prefixes)

    async def _update_daily_login(self, db, user) -> None:
        """Update last_login if this is the user's first request of the day.

        This treats the first authenticated request each calendar day (in UTC)
        as a "login" event, providing accurate activity tracking even when
        OAuth tokens are silently refreshed.
        """
        from datetime import datetime

        now = datetime.now(UTC)

        # Check if last_login is null or from a previous day
        if user.last_login is None or user.last_login.date() < now.date():
            user.last_login = now
            await db.commit()
            logger.debug(f"Updated daily login for user {user.id}")

    # TODO: Refactor this function. It's too complex (number of branches and statements).
    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # noqa: PLR0912, PLR0915
        # Skip authentication for public endpoints
        """Authenticate incoming requests, validate user status against the database, and attach the resolved user context to request.state for downstream authorization.

        Supports "Bearer <jwt>" and "ApiKey <key>" authorization. For Bearer tokens, validates the JWT and marks the request for a token refresh if the token is near expiry. For ApiKey auth, validates the configured global API key, marks the request.state.api_key_authenticated flag, and maps the API key to a configured user email. In all authenticated flows, verifies the corresponding user exists and is active in the database, then stores up-to-date user information on request.state.user. If authentication succeeds, forwards the request to the next handler; if authentication fails, returns an appropriate JSON error response. When a token refresh is required, the response will include the "X-Token-Refresh-Needed": "true" header.

        Returns:
            Response: The downstream handler's response on successful authentication, or a JSON error response with one of:
              - 401 Unauthorized for missing/invalid credentials or missing user mapping,
              - 400 Bad Request if the user account is inactive,
              - 500 Internal Server Error for database/validation errors.

        """
        if self._is_public_path(request.url.path):
            return await call_next(request)

        # Extract Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            logger.warning(f"Missing Authorization header for {request.method} {request.url.path}")
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})

        # Accept either Bearer <jwt> or ApiKey <key>
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
        else:
            logger.warning(f"Unsupported Authorization scheme for {request.method} {request.url.path}")
            return JSONResponse(status_code=401, content={"detail": "Unsupported Authorization scheme"})

        # Check if token is near expiry for sliding expiration (Bearer only)
        if (
            (not getattr(request.state, "api_key_authenticated", False))
            and token
            and self.jwt_manager.is_token_near_expiry(token, buffer_minutes=10)
        ):
            # Add header to indicate client should refresh token
            request.state.token_needs_refresh = True

        # SECURITY FIX: Check current user status in database
        # JWT tokens contain user data from when they were created, but we need to verify
        # the user is still active in the database
        try:
            from sqlalchemy import select

            from ..auth.models import User
            from ..core.database import get_db

            # Get database session
            async for db in get_db():
                # Determine lookup based on auth mode
                if getattr(request.state, "api_key_authenticated", False):
                    settings = get_settings_instance()
                    if not settings.api_key_user_email:
                        logger.warning("API key user mapping not configured (SHU_API_KEY_USER_EMAIL missing)")
                        return JSONResponse(
                            status_code=401,
                            content={"detail": "API key user mapping not configured"},
                        )
                    stmt = select(User).where(User.email == settings.api_key_user_email)
                else:
                    stmt = select(User).where(User.id == user_data["user_id"])

                result = await db.execute(stmt)
                current_user = result.scalar_one_or_none()

                if not current_user:
                    missing = (
                        settings.api_key_user_email
                        if getattr(request.state, "api_key_authenticated", False)
                        else user_data.get("user_id")
                    )
                    logger.warning(f"User not found in database for {request.method} {request.url.path}: {missing}")
                    return JSONResponse(status_code=401, content={"detail": "User account not found"})

                if not current_user.is_active:
                    logger.warning(
                        f"Inactive user {current_user.email} attempted access to {request.method} {request.url.path}"
                    )
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "User account is inactive. Please contact an administrator for activation."},
                    )

                # Update last_login on first request of the day
                await self._update_daily_login(db, current_user)

                # Build user context for RBAC
                if getattr(request.state, "api_key_authenticated", False):
                    user_data = {
                        "user_id": current_user.id,
                        "email": current_user.email,
                        "name": current_user.name,
                        "role": current_user.role,
                        "is_active": current_user.is_active,
                        "must_change_password": current_user.must_change_password,
                    }
                else:
                    # Update user data with current database values to ensure consistency
                    user_data.update(
                        {
                            "email": current_user.email,
                            "name": current_user.name,
                            "role": current_user.role,
                            "is_active": current_user.is_active,
                            "must_change_password": current_user.must_change_password,
                        }
                    )
                break

        except Exception as e:
            logger.error(f"Database error during user validation: {e}")
            return JSONResponse(status_code=500, content={"detail": "Authentication validation failed"})

        # Store user data in request state for role-based authorization
        request.state.user = user_data

        logger.debug(
            f"Authenticated user {user_data['email']} ({user_data['role']}) for {request.method} {request.url.path}"
        )

        # Process the request
        response = await call_next(request)

        # Add refresh header if token needs refresh
        if hasattr(request.state, "token_needs_refresh") and request.state.token_needs_refresh:
            response.headers["X-Token-Refresh-Needed"] = "true"

        return response


class MustChangePasswordMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce the must_change_password flag server-side.

    When an authenticated user has must_change_password=True, rejects all
    requests with 403 Forbidden except the endpoints needed to complete the
    password change flow: PUT /auth/change-password, GET /auth/me, and
    POST /auth/refresh.

    This provides defense-in-depth so the flag cannot be bypassed by calling
    API endpoints directly (e.g. via curl or devtools).
    """

    # Paths that are allowed even when must_change_password is True.
    # These use the full API-prefixed paths.
    ALLOWED_PATHS: ClassVar[set[str]] = {
        "/api/v1/auth/change-password",
        "/api/v1/auth/me",
        "/api/v1/auth/refresh",
    }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Check must_change_password flag and block disallowed requests."""
        user = getattr(request.state, "user", None)

        if user and isinstance(user, dict) and user.get("must_change_password"):
            path = request.url.path.rstrip("/")
            if path not in self.ALLOWED_PATHS:
                logger.info(
                    "Blocked request due to must_change_password",
                    extra={
                        "user_id": user.get("user_id"),
                        "path": path,
                        "method": request.method,
                    },
                )
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Password change required. Please change your password before continuing."},
                )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to apply rate limiting to API endpoints.

    Applies per-user rate limiting with configurable exclusions for
    public endpoints (health checks, public config, etc.).

    Rate limit headers are added to all responses:
    - RateLimit-Limit: Maximum requests allowed
    - RateLimit-Remaining: Remaining requests in current window
    - RateLimit-Reset: Seconds until rate limit resets
    - Retry-After: Seconds to wait (only on 429 responses)
    """

    def __init__(self, app, excluded_paths: set[str] | None = None) -> None:
        """Initialize the RateLimitMiddleware and configure paths excluded from rate limiting.

        Sets up a lazy holder for the rate limit service, a default set of public endpoints that bypass rate limiting, and a list of path prefixes to exclude.

        Parameters
        ----------
            excluded_paths (Optional[Set[str]]): Optional set of exact request paths to exclude from rate limiting.
                If omitted, defaults to common public endpoints such as "/docs", "/redoc", "/openapi.json",
                health check routes, and the public config endpoint.

        """
        super().__init__(app)
        self._rate_limit_service: RateLimitService | None = None

        # Default excluded paths (public endpoints that don't need rate limiting)
        self.excluded_paths: set[str] = excluded_paths or {
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/v1/health/liveness",
            "/api/v1/health/readiness",
            "/api/v1/config/public",
        }

        # Excluded prefixes
        self.excluded_prefixes: list[str] = [
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/v1/health/",
        ]

    def _get_rate_limit_service(self) -> RateLimitService:
        """Lazily initialize and return the rate limit service instance.

        Returns:
            The rate limit service instance used to check and manage API limits.

        """
        if self._rate_limit_service is None:
            from .rate_limiting import get_rate_limit_service

            self._rate_limit_service = get_rate_limit_service()
        return self._rate_limit_service

    def _is_excluded(self, path: str) -> bool:
        """Determine whether a request path is excluded from rate limiting.

        Parameters
        ----------
            path (str): Request path to evaluate.

        Returns
        -------
            bool: `True` if the path is exactly in the excluded paths or begins with any excluded prefix, `False` otherwise.

        """
        if path in self.excluded_paths:
            return True
        return any(path.startswith(prefix) for prefix in self.excluded_prefixes)

    def _get_user_id(self, request: Request) -> str | None:
        """Extract user ID from request state."""
        user = getattr(request.state, "user", None)
        if user and isinstance(user, dict):
            return user.get("user_id")
        return None

    def _get_client_ip(self, request: Request) -> str:
        """Determine the client's IP address to use for anonymous rate limiting.

        Parameters
        ----------
            request (Request): The incoming request from which headers and the client host are read.

        Returns
        -------
            client_ip (str): IP address string chosen for rate-limiting (may come from proxy headers or the request's client host).

        """
        from .rate_limiting import get_client_ip

        return get_client_ip(request.headers, request.client.host if request.client else None)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip rate limiting for excluded paths
        """Enforce per-user or IP-based rate limits for incoming requests.

        Skips enforcement for configured excluded paths or when the rate limit service is disabled. Determines an identifier from the authenticated user ID, falling back to the client IP. If the request exceeds the allowed rate, returns a 429 JSON response containing a retry_after value and rate-limit headers. Otherwise forwards the request to the next handler and attaches rate-limit headers from the rate limit service to the returned response.

        Parameters
        ----------
            request (Request): The incoming HTTP request.
            call_next (Callable): The next request handler to invoke.

        Returns
        -------
            Response: A 429 JSON error response when the rate limit is exceeded, or the downstream response with rate-limit headers added.

        """
        if self._is_excluded(request.url.path):
            return await call_next(request)

        rate_limit_service = self._get_rate_limit_service()

        # Skip if rate limiting is disabled
        if not rate_limit_service.enabled:
            return await call_next(request)

        # Get identifier (user ID or IP for anonymous)
        user_id = self._get_user_id(request)
        identifier = user_id or f"ip:{self._get_client_ip(request)}"

        # Check rate limit
        result = await rate_limit_service.check_api_limit(identifier)

        if not result.allowed:
            # Rate limit exceeded
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "identifier": identifier,
                    "path": request.url.path,
                    "method": request.method,
                    "retry_after": result.retry_after_seconds,
                },
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "message": "Rate limit exceeded. Please try again later.",
                        "code": "RATE_LIMIT_EXCEEDED",
                        "details": {
                            "retry_after": result.retry_after_seconds,
                        },
                    }
                },
                headers=result.to_headers(),
            )

        # Process request
        response = await call_next(request)

        # Add rate limit headers to successful responses only.
        # Don't overwrite headers on 429 responses - they may contain
        # rate limit info from a downstream handler (e.g., per-plugin limits).
        if response.status_code != 429:
            for header, value in result.to_headers().items():
                response.headers[header] = value

        return response
