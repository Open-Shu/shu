"""Authentication API endpoints for Shu."""

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from ..api.dependencies import get_db
from ..auth import User, UserRole
from ..auth.password_auth import password_auth_service
from ..auth.rbac import get_current_user, require_admin
from ..core.rate_limiting import get_rate_limit_service
from ..core.response import ShuResponse
from ..schemas.envelope import SuccessResponse
from ..services.user_service import UserService, create_token_response, get_user_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])


async def _check_auth_rate_limit(request: Request) -> None:
    """Enforces authentication-specific rate limits and raises HTTP 429 when exceeded.

    If the configured rate limit service is disabled this function returns immediately. Otherwise it determines the client's IP from the provided Request and checks the authentication rate limit; when the limit is exceeded an HTTPException with status 429 and rate-limit headers is raised.

    Parameters
    ----------
        request (Request): FastAPI request used to determine client IP and headers.

    Raises
    ------
        HTTPException: with status 429 and a payload containing `retry_after` when the auth rate limit is exceeded.

    """
    from ..core.rate_limiting import get_client_ip

    rate_limit_service = get_rate_limit_service()

    if not rate_limit_service.enabled:
        return

    client_ip = get_client_ip(request.headers, request.client.host if request.client else None)
    result = await rate_limit_service.check_auth_limit(client_ip)

    if not result.allowed:
        logger.warning("Auth rate limit exceeded for IP %s", client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "message": "Too many authentication attempts. Please try again later.",
                    "code": "AUTH_RATE_LIMIT_EXCEEDED",
                    "details": {"retry_after": result.retry_after_seconds},
                }
            },
            headers=result.to_headers(),
        )


class LoginRequest(BaseModel):
    """Request model for Google OAuth login endpoint."""

    google_token: str


class PasswordLoginRequest(BaseModel):
    """Request model for password login endpoint."""

    email: str
    password: str


class RegisterRequest(BaseModel):
    """Request model for user registration endpoint."""

    email: str
    password: str
    name: str


class ChangePasswordRequest(BaseModel):
    """Request model for password change endpoint."""

    old_password: str
    new_password: str


class RefreshTokenRequest(BaseModel):
    """Request model for token refresh endpoint."""

    refresh_token: str


class TokenResponse(BaseModel):
    """Response model for token endpoints."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 # not an actual password
    user: dict[str, Any]


class UserUpdateRequest(BaseModel):
    """Request model for updating user."""

    role: str
    is_active: bool = True


class CreateUserRequest(BaseModel):
    """Request model for admin to create new users."""

    email: str
    name: str
    role: str = "regular_user"
    password: str = None  # Optional - if not provided, user must use SSO
    auth_method: Literal["password", "google", "microsoft"] = "password"


@router.post("/login", response_model=SuccessResponse[TokenResponse])
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
    _rate_limit: None = Depends(_check_auth_rate_limit),
):
    """Authenticate or create a user using a Google ID token and return JWT access and refresh tokens.

    Authenticates the incoming Google ID token, creates the user if needed, and issues an access token and refresh token packaged with the user's public data.
    Uses the unified SSO authentication architecture via adapter.get_user_info() and
    user_service.authenticate_or_create_sso_user().

    Parameters
    ----------
        request (LoginRequest): Payload containing the Google ID token.
        db (AsyncSession): Database session injected via dependency.
        user_service (UserService): User service injected via dependency.
        _rate_limit: Rate limiting dependency (enforces auth rate limits).

    Returns
    -------
        SuccessResponse: Contains a TokenResponse with `access_token`, `refresh_token`, `token_type`, and `user` dictionary.

    Raises
    ------
        HTTPException: Raised with 401 when authentication/verification fails; propagated as-is for other HTTP errors; raised with 500 for unexpected internal errors.

    """
    try:
        from ..plugins.host.auth_capability import AuthCapability
        from ..providers.registry import get_auth_adapter

        auth = AuthCapability(plugin_name="admin", user_id="anonymous")
        adapter = get_auth_adapter("google", auth)

        # Get normalized user info from adapter
        provider_info = await adapter.get_user_info(id_token=request.google_token, db=db)

        # Authenticate or create user using unified SSO method
        user = await user_service.authenticate_or_create_sso_user(provider_info, db)

        # Create JWT token response
        return SuccessResponse(data=TokenResponse(**create_token_response(user, user_service.jwt_manager)))

    except ValueError as e:
        # Adapter errors (token verification failures) come as ValueError
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Authentication failed")


@router.post(
    "/register", response_model=SuccessResponse[dict[str, str]], dependencies=[Depends(_check_auth_rate_limit)]
)
async def register_user(
    request: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    """Register a new user account using email and password.

    If the new account is assigned an admin role it is activated immediately; otherwise it remains inactive and requires administrator activation. The endpoint does not issue authentication tokens for newly registered (non-admin) users.

    Returns:
        SuccessResponse: Contains a data object with keys:
            - message: Confirmation text (notes if admin activation is required).
            - email: The created user's email.
            - status: "activated" for admin-created accounts or "pending_activation" for accounts awaiting admin activation.

    Raises:
        HTTPException: with status 400 when input validation or business rules fail, or 500 for unexpected server errors.

    """
    try:
        is_first_user = await user_service.is_first_user(db)
        user_role = user_service.determine_user_role(request.email, is_first_user)
        is_admin = user_role == UserRole.ADMIN

        # Create user with password authentication (inactive by default)
        user = await password_auth_service.create_user(
            email=request.email,
            password=request.password,
            name=request.name,
            role=user_role.value,
            db=db,
            admin_created=is_admin,
        )

        # Return success message without tokens (user is inactive)
        return SuccessResponse(
            data={
                "message": "Registration successful!"
                + (
                    " Your account has been created but requires administrator activation before you can log in."
                    if not is_admin
                    else ""
                ),
                "email": user.email,
                "status": "pending_activation" if not is_admin else "activated",
            }
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Registration failed")


@router.post(
    "/login/password", response_model=SuccessResponse[TokenResponse], dependencies=[Depends(_check_auth_rate_limit)]
)
async def login_with_password(
    request: PasswordLoginRequest,
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    """Authenticate a user using email and password and return JWT tokens and user info.

    Parameters
    ----------
        request (PasswordLoginRequest): Contains the user's `email` and `password`.
        db (AsyncSession): Database session (typically injected via dependency).
        user_service (UserService): User service injected via dependency.

    Returns
    -------
        SuccessResponse: Contains a TokenResponse with `access_token`, `refresh_token`, `token_type`, and `user` (user data dictionary).

    Raises
    ------
        HTTPException: 409 if the account was created with Google and password login is not allowed.
        HTTPException: 401 if authentication fails due to invalid credentials or other validation errors.
        HTTPException: 500 for unexpected server-side errors.

    """
    try:
        auth_method = await user_service.get_user_auth_method(db, request.email)
        if auth_method is not None and auth_method != "password":
            if auth_method == "google":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This account uses Google authentication. Please use the Google login flow.",
                )
            if auth_method == "microsoft":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This account uses Microsoft authentication. Please use the Microsoft login flow.",
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This account uses {auth_method} authentication. Please use the appropriate login flow.",
            )

        user = await password_auth_service.authenticate_user(request.email, request.password, db)

        # Create JWT token response using shared helper
        return SuccessResponse(data=TokenResponse(**create_token_response(user, user_service.jwt_manager)))

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Password login error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Authentication failed")


@router.put(
    "/change-password",
    response_model=SuccessResponse[dict[str, str]],
    dependencies=[Depends(_check_auth_rate_limit)],
)
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change current user's password."""
    try:
        await password_auth_service.change_password(
            user_id=current_user.id,
            old_password=request.old_password,
            new_password=request.new_password,
            db=db,
        )

        return ShuResponse.success({"message": "Password changed successfully"})

    except (ValueError, LookupError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Password change error: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Password change failed")


@router.post(
    "/users/{user_id}/reset-password",
    response_model=SuccessResponse[dict[str, str]],
    dependencies=[Depends(_check_auth_rate_limit)],
)
async def reset_user_password(
    user_id: str,
    _current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reset a user's password and generate a temporary password (admin only).

    Generates a random temporary password, hashes and stores it, and sets the
    ``must_change_password`` flag so the user must choose a new password on
    next login.

    Parameters
    ----------
        user_id: The ID of the user whose password should be reset.
        _current_user: The authenticated admin user (enforced by ``require_admin``).
        db: Database session injected via dependency.

    Returns
    -------
        SuccessResponse: Contains the generated temporary password and a
        confirmation message.

    Raises
    ------
        HTTPException: 404 if user not found, 400 if user does not use password
            authentication, 403 if requester is not admin, 429 if rate limited.

    """
    try:
        temporary_password = await password_auth_service.reset_password(user_id=user_id, db=db)
        return ShuResponse.success(
            {
                "temporary_password": temporary_password,
                "message": "Password reset. The user will be required to change their password on next login.",
            }
        )
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Password reset error: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Password reset failed")


@router.post("/refresh", response_model=SuccessResponse[TokenResponse])
async def refresh_token(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    """Refresh access token using refresh token."""
    try:
        # Verify refresh token and get user_id
        user_id = user_service.jwt_manager.refresh_access_token(request.refresh_token)

        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

        # Get current user data from database
        user = await user_service.get_user_by_id(user_id, db)

        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

        # Create JWT token response using shared helper
        return SuccessResponse(data=TokenResponse(**create_token_response(user, user_service.jwt_manager)))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token refresh error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Token refresh failed")


@router.get("/me", response_model=SuccessResponse[dict[str, Any]])
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information."""
    return SuccessResponse(data=current_user.to_dict())


@router.get("/google/login")
async def google_login(current_user: User | None = Depends(lambda: None)):
    """Redirect to Google OAuth via provider adapter (host_auth flow)."""
    try:
        from ..plugins.host.auth_capability import AuthCapability
        from ..providers.registry import get_auth_adapter

        auth = AuthCapability(plugin_name="admin", user_id=str(current_user.id) if current_user else "anonymous")
        adapter = get_auth_adapter("google", auth)
        res = await adapter.build_authorization_url(scopes=["openid", "email", "profile"])  # minimal SSO scopes
        url = res.get("url")
        if not url:
            raise ValueError("Failed to build authorization URL")
        return RedirectResponse(url=url)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"SSO redirect unavailable: {e}")


class CodeRequest(BaseModel):
    code: str


@router.post(
    "/google/exchange-login",
    response_model=SuccessResponse[TokenResponse],
    dependencies=[Depends(_check_auth_rate_limit)],
)
async def google_exchange_login(
    request: CodeRequest,
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    """Exchange an OAuth authorization code for Google ID token and issue Shu JWTs.

    This supports the explicit redirect fallback login flow (popup or top-level redirect).
    Uses the unified SSO authentication architecture via adapter.get_user_info() and
    user_service.authenticate_or_create_sso_user().
    """
    try:
        from ..plugins.host.auth_capability import AuthCapability
        from ..providers.registry import get_auth_adapter

        # Minimal SSO scopes for ID token
        scopes = ["openid", "email", "profile"]

        auth = AuthCapability(plugin_name="admin", user_id="anonymous")
        adapter = get_auth_adapter("google", auth)

        # Exchange the code for tokens
        tok = await adapter.exchange_code(code=request.code, scopes=scopes)
        id_token = tok.get("id_token")
        if not id_token:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provider did not return id_token")

        # Get normalized user info from adapter
        provider_info = await adapter.get_user_info(id_token=id_token, db=db)

        # Authenticate or create user using unified SSO method
        user = await user_service.authenticate_or_create_sso_user(provider_info, db)

        # Create JWT token response
        return SuccessResponse(data=TokenResponse(**create_token_response(user, user_service.jwt_manager)))

    except HTTPException:
        raise
    except ValueError as e:
        # Adapter errors (token verification failures) come as ValueError
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    except Exception as e:
        logger.error(f"google_exchange_login error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Google login exchange failed")


@router.get("/microsoft/login")
async def microsoft_login(current_user: User | None = Depends(lambda: None)):
    """Redirect to Microsoft OAuth via provider adapter (host_auth flow)."""
    try:
        from ..plugins.host.auth_capability import AuthCapability
        from ..providers.registry import get_auth_adapter

        auth = AuthCapability(plugin_name="admin", user_id=str(current_user.id) if current_user else "anonymous")
        adapter = get_auth_adapter("microsoft", auth)
        # SSO scopes: identity + basic profile
        res = await adapter.build_authorization_url(scopes=["openid", "email", "profile", "User.Read"])
        url = res.get("url")
        if not url:
            raise ValueError("Failed to build Microsoft authorization URL")
        return RedirectResponse(url=url)
    except Exception as e:
        logger.error(f"microsoft_login error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Microsoft SSO redirect unavailable: {e}",
        )


@router.post(
    "/microsoft/exchange-login",
    response_model=SuccessResponse[TokenResponse],
    dependencies=[Depends(_check_auth_rate_limit)],
)
async def microsoft_exchange_login(
    request: CodeRequest,
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    """Exchange an OAuth authorization code for Microsoft access token and issue Shu JWTs.

    This supports the Microsoft OAuth redirect login flow.
    Uses the unified SSO authentication architecture via adapter.get_user_info() and
    user_service.authenticate_or_create_sso_user().
    """
    try:
        from ..plugins.host.auth_capability import AuthCapability
        from ..providers.registry import get_auth_adapter

        # SSO scopes for user info
        scopes = ["openid", "email", "profile", "User.Read"]

        auth = AuthCapability(plugin_name="admin", user_id="anonymous")
        adapter = get_auth_adapter("microsoft", auth)

        # Exchange the code for tokens
        tok = await adapter.exchange_code(code=request.code, scopes=scopes)
        access_token = tok.get("access_token")
        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Microsoft did not return access_token",
            )

        # Get normalized user info from adapter
        provider_info = await adapter.get_user_info(access_token=access_token)

        # Authenticate or create user using unified SSO method
        user = await user_service.authenticate_or_create_sso_user(provider_info, db)

        # Create JWT token response
        return SuccessResponse(data=TokenResponse(**create_token_response(user, user_service.jwt_manager)))

    except HTTPException:
        raise
    except ValueError as e:
        # Adapter errors (user info request failures) come as ValueError
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    except Exception as e:
        logger.error(f"microsoft_exchange_login error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Microsoft login exchange failed"
        ) from e


@router.get("/users", response_model=SuccessResponse[list[dict[str, Any]]])
async def get_all_users(
    _current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    """Get all users (admin only)."""
    users = await user_service.get_all_users(db)
    return SuccessResponse(data=[user.to_dict() for user in users])


@router.put("/users/{user_id}", response_model=SuccessResponse[dict[str, Any]])
async def update_user(
    user_id: str,
    request: UserUpdateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    """Update user role and status (admin only)."""
    try:
        user = await user_service.update_user_role(user_id, request.role, db)
        user.is_active = request.is_active
        await db.commit()
        await db.refresh(user)
        return SuccessResponse(data=user.to_dict())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/users", response_model=SuccessResponse[dict[str, Any]])
async def create_user(
    request: CreateUserRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new user (admin only)."""
    try:
        if request.auth_method == "password":
            if not request.password:
                raise ValueError("Password is required for password authentication")

            # Create user with password authentication (admin-created)
            user = await password_auth_service.create_user(
                email=request.email,
                password=request.password,
                name=request.name,
                role=request.role,
                db=db,
                admin_created=True,  # Admin-created users are active by default
            )
        else:
            # Create user for SSO (password will be None)
            # Validate role before creating user
            try:
                UserRole(request.role)
            except ValueError as e:
                raise ValueError(f"Invalid role: {request.role}") from e

            # Admin-created users are active by default
            user = User(
                email=request.email,
                name=request.name,
                auth_method=request.auth_method,
                role=request.role,
                is_active=True,  # Admin-created users are active
            )
            db.add(user)

            try:
                await db.commit()
                await db.refresh(user)
            except IntegrityError as e:
                await db.rollback()
                raise ValueError(f"User with email {request.email} already exists") from e

        return SuccessResponse(data=user.to_dict())

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"User creation error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User creation failed")


@router.patch("/users/{user_id}/activate", response_model=SuccessResponse[dict[str, Any]])
async def activate_user(
    user_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    """Activate a user account (admin only)."""
    try:
        # Get user by ID
        user = await user_service.get_user_by_id(user_id, db)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # Activate user
        user.is_active = True
        await db.commit()
        await db.refresh(user)

        logger.info(f"User {user.email} activated by admin {current_user.email}")
        return SuccessResponse(data=user.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"User activation error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User activation failed")


@router.patch("/users/{user_id}/deactivate", response_model=SuccessResponse[dict[str, Any]])
async def deactivate_user(
    user_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    """Deactivate a user account (admin only)."""
    try:
        # Get user by ID
        user = await user_service.get_user_by_id(user_id, db)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # Prevent self-deactivation
        if user.id == current_user.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot deactivate your own account")

        # Deactivate user
        user.is_active = False
        await db.commit()
        await db.refresh(user)

        logger.info(f"User {user.email} deactivated by admin {current_user.email}")
        return SuccessResponse(data=user.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"User deactivation error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User deactivation failed")


@router.delete("/users/{user_id}", response_model=SuccessResponse[dict[str, str]])
async def delete_user(
    user_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    """Delete user (admin only)."""
    try:
        await user_service.delete_user(user_id, current_user.id, db)
        return SuccessResponse(data={"message": "User deleted successfully"})
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"User deletion error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User deletion failed")
