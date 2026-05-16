"""Authentication API endpoints for Shu."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from shu.core.logging import get_logger

from ..api.dependencies import get_db
from ..auth import User, UserRole
from ..auth.password_auth import password_auth_service
from ..auth.rbac import get_current_user, require_admin
from ..billing.enforcement import check_user_limit
from ..billing.seat_service import (
    SeatMinimumError,
    SeatService,
    UserNotFoundError,
    UserStateError,
    get_seat_service,
)
from ..billing.stripe_client import StripeClientError
from ..core.rate_limiting import get_rate_limit_service
from ..core.response import ShuResponse
from ..schemas.envelope import SuccessResponse
from ..services.email_verification_service import (
    TokenExpiredError,
    TokenInvalidError,
    get_email_verification_service_dependency,
)
from ..services.user_service import UserService, create_token_response, get_user_service

logger = get_logger(__name__)

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


class VerifyEmailRequest(BaseModel):
    """Request model for the email-verification confirm endpoint (SHU-507).

    Token is a `secrets.token_urlsafe(32)` value (43 characters base64 url-safe).
    The 256-char cap leaves headroom for any future token-format change while
    still rejecting absurd payloads.
    """

    token: str = Field(min_length=1, max_length=256)


class ResendVerificationRequest(BaseModel):
    """Request model for resending the verification email (SHU-507).

    320 chars is the SMTP RFC 5321 maximum (64 local + @ + 255 domain).
    """

    email: str = Field(min_length=3, max_length=320)


class ResendVerificationFromTokenRequest(BaseModel):
    """Request model for resending verification using the original token
    as the identity proof (SHU-507).

    Same length envelope as VerifyEmailRequest.token.
    """

    token: str = Field(min_length=1, max_length=256)


class RequestPasswordResetRequest(BaseModel):
    """Request model for the password-reset request endpoint (SHU-745)."""

    email: str = Field(min_length=3, max_length=320)


class ResetPasswordRequest(BaseModel):
    """Request model for completing a password reset (SHU-745)."""

    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ResendPasswordResetFromTokenRequest(BaseModel):
    """Request model for token-based reset resend (SHU-745). The caller
    passes the original (possibly expired) token from the URL — server
    resolves the user from its hash and issues a fresh token without
    requiring an email retype.
    """

    token: str = Field(min_length=1, max_length=256)


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

        # Enforce user limit (skip for the very first user bootstrapping the instance)
        over_limit_soft = False
        if not is_first_user:
            limit_status = await check_user_limit(db)
            if limit_status.at_limit and limit_status.enforcement == "hard":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"User limit ({limit_status.user_limit}) reached. Contact your administrator.",
                )
            if limit_status.at_limit and limit_status.enforcement == "soft":
                logger.warning(
                    "User registered above subscription limit",
                    extra={"current_users": limit_status.current_count, "limit": limit_status.user_limit},
                )
                # SHU-784: force admin approval on the over-limit user even if
                # SHU_AUTO_ACTIVATE_USERS=true. Without this override, soft
                # enforcement degenerates to `none` whenever auto-activate is
                # on. Below the limit, operator policy still applies.
                over_limit_soft = True

        user_role = user_service.determine_user_role(request.email, is_first_user)
        is_admin = user_role == UserRole.ADMIN

        # SHU-507: decide which gate the new user must clear before logging in.
        # - Admin / first-user: no gate (admin path also applies to first-user bootstrap)
        # - Email backend disabled (or downgraded to disabled by the factory
        #   because the configured backend's required config is missing):
        #   legacy admin-activation gate
        # - Email backend effectively configured: email-verification gate
        from ..core.email.factory import get_effective_email_backend_name

        verification_required = not is_admin and get_effective_email_backend_name() != "disabled"

        if verification_required:
            # Flush the user row so the verification token write rides the
            # same transaction. The endpoint commits at the end.
            user = await password_auth_service.create_user(
                email=request.email,
                password=request.password,
                name=request.name,
                role=user_role.value,
                db=db,
                admin_created=False,
                requires_email_verification=True,
                flush_only=True,
                force_inactive=over_limit_soft,
            )
            verification_service = get_email_verification_service_dependency()
            await verification_service.issue_token(user, db)
            await db.commit()
            await db.refresh(user)

            # Two gates may apply after registration:
            #   1. Email verification (always, when the email backend is on)
            #   2. Admin activation (when SHU_AUTO_ACTIVATE_USERS=false, the
            #      default — so the admin still has to approve even after
            #      the user verifies)
            # Surface the combination so the frontend can describe exactly
            # what is required of the user before login works.
            both_gates_pending = not user.is_active
            if both_gates_pending:
                message = (
                    "Registration successful! Check your email for a verification link, "
                    "and wait for an administrator to activate your account before you can log in."
                )
                status_value = "pending_verification_and_admin"
            else:
                message = (
                    "Registration successful! Check your email for a verification link " "to activate your account."
                )
                status_value = "pending_email_verification"

            return SuccessResponse(
                data={
                    "message": message,
                    "email": user.email,
                    "status": status_value,
                }
            )

        # Legacy paths: admin-created (instant) or email backend disabled
        # (admin must activate manually).
        user = await password_auth_service.create_user(
            email=request.email,
            password=request.password,
            name=request.name,
            role=user_role.value,
            db=db,
            admin_created=is_admin,
        )

        return SuccessResponse(
            data={
                "message": "Registration successful!"
                + (
                    " Your account has been created but requires administrator activation before you can log in."
                    if not is_admin
                    else ""
                ),
                "email": user.email,
                "status": "pending_admin_activation" if not is_admin else "activated",
            }
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Registration failed")


@router.post(
    "/verify-email",
    response_model=SuccessResponse[dict[str, str]],
    dependencies=[Depends(_check_auth_rate_limit)],
)
async def verify_email(
    request: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
):
    """Confirm a user's email address using the token sent in the verification email (SHU-507).

    Single-use: a successful verify clears the token columns; a replay attempt
    returns 400. Expired tokens also return 400 with a distinct message so the
    frontend can offer a "resend" CTA.

    Note: this endpoint does NOT 503 when the effective email backend is
    disabled. It only redeems an already-issued token — the redemption is
    independent of whether the backend can currently send NEW emails. A
    user who legitimately got a token (perhaps before an operator toggled
    the backend off) still has a valid token; refusing to consume it
    would strand them. The 503 gate lives only on issue/resend endpoints
    that actually attempt outbound delivery.
    """
    service = get_email_verification_service_dependency()
    try:
        user = await service.verify_token(request.token, db)
    except TokenExpiredError as e:
        # Expired branch: the hash matched a real row, so we know which
        # account this token belongs to. We do NOT need to send the email
        # address back — the token IS the identity. The frontend uses the
        # `code` to switch to the token-based resend UX, which posts the
        # *original* token back to /resend-verification-from-token and the
        # server resolves the user from the same hash.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "message": str(e),
                    "code": "VERIFICATION_TOKEN_EXPIRED",
                }
            },
        )
    except TokenInvalidError as e:
        # Unknown-token branch: NO email leak (we cannot identify a user).
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    await db.commit()

    return SuccessResponse(
        data={
            "message": "Email verified. You can now log in.",
            "email": user.email,
        }
    )


@router.post(
    "/resend-verification",
    response_model=SuccessResponse[dict[str, str]],
    dependencies=[Depends(_check_auth_rate_limit)],
)
async def resend_verification(
    request: ResendVerificationRequest,
    db: AsyncSession = Depends(get_db),
):
    """Resend a verification email if one is pending for the address (SHU-507).

    Always returns 200 regardless of whether the address exists, is already
    verified, or hit the rate limit — no enumeration. The actual send happens
    only when there is a real pending verification.
    """
    from ..core.email.factory import get_effective_email_backend_name as _eff_backend

    email_backend = _eff_backend()
    if email_backend == "disabled":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email backend is not configured; admin activation is the gate on this instance.",
        )

    service = get_email_verification_service_dependency()
    await service.resend(request.email, db)
    await db.commit()

    # Generic envelope — does not leak whether the address exists or was sent.
    return SuccessResponse(
        data={
            "message": (
                "If an account is pending verification for this address, " "a new verification email has been sent."
            ),
        }
    )


@router.post(
    "/resend-verification-from-token",
    response_model=SuccessResponse[dict[str, str]],
    dependencies=[Depends(_check_auth_rate_limit)],
)
async def resend_verification_from_token(
    request: ResendVerificationFromTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """Resend a verification email using the original (possibly expired) token
    as the identity proof (SHU-507).

    The verify-email page already knows the token from the URL. When the
    token has expired, the user clicks "Send a new verification email" and
    the page hands the same token back here. We hash it, look up the user,
    and issue a fresh token — the user never has to type, see, or know
    their email address.

    Always returns 200 with a generic envelope (no enumeration regardless
    of whether the token matched a real user, was already verified, or
    hit the rate limit).
    """
    from ..core.email.factory import get_effective_email_backend_name as _eff_backend

    email_backend = _eff_backend()
    if email_backend == "disabled":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email backend is not configured; admin activation is the gate on this instance.",
        )

    service = get_email_verification_service_dependency()
    await service.resend_from_token(request.token, db)
    await db.commit()

    return SuccessResponse(
        data={
            "message": ("If the link belonged to a pending account, a new verification " "email has been sent."),
        }
    )


# ---------------------------------------------------------------------------
# Password reset (SHU-745)
# ---------------------------------------------------------------------------

_RESET_NEUTRAL_RESPONSE = {
    "message": (
        "If an account is registered for this address, a password reset email has been sent. "
        "If you don't see it within a few minutes, check spam or try again."
    ),
}

_RESEND_RESET_NEUTRAL_RESPONSE = {
    "message": ("If the link belonged to a real account, a fresh password reset email has been sent."),
}


@router.post(
    "/request-password-reset",
    response_model=SuccessResponse[dict[str, str]],
    dependencies=[Depends(_check_auth_rate_limit)],
)
async def request_password_reset(
    request: RequestPasswordResetRequest,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Issue a password reset token if the email belongs to a known
    active password user (SHU-745).

    Always returns 200 with a generic envelope — no enumeration regardless
    of whether the address exists, is SSO-only, is inactive, or hit the
    per-IP / per-email rate limit. The actual send happens only when there
    is a real account that can use it.

    When the configured email backend is `disabled`, returns 200 anyway
    (no token created, no email sent — operators must reset manually as
    they did before SHU-745). The frontend cannot distinguish from the
    other no-op branches.
    """
    from ..core.email.factory import get_effective_email_backend_name as _eff_backend

    email_backend = _eff_backend()
    if email_backend == "disabled":
        logger.warning(
            "Password reset requested for %s but SHU_EMAIL_BACKEND=disabled — operators must reset manually",
            request.email,
            extra={"event": "password_reset.requested_email_disabled", "email": request.email},
        )
        return SuccessResponse(data=_RESET_NEUTRAL_RESPONSE)

    from ..core.rate_limiting import get_client_ip
    from ..services.password_reset_service import get_password_reset_service_dependency

    client_ip = get_client_ip(
        raw_request.headers,
        raw_request.client.host if raw_request.client else None,
    )

    service = get_password_reset_service_dependency()
    await service.request_reset(request.email, client_ip, db)
    await db.commit()

    return SuccessResponse(data=_RESET_NEUTRAL_RESPONSE)


@router.post(
    "/reset-password",
    response_model=SuccessResponse[dict[str, str]],
    dependencies=[Depends(_check_auth_rate_limit)],
)
async def reset_password(
    request: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Apply a new password using a valid reset token (SHU-745).

    On success, returns 200 and invalidates every existing JWT for the
    user (the JWT auth middleware checks each access token's `iat` claim
    against `users.password_changed_at` — bumping the latter forces every
    open browser/device to log in fresh).

    On expired token, returns 400 with a structured `code` of
    ``PASSWORD_RESET_TOKEN_EXPIRED`` so the frontend can render a
    one-click "Send a new reset link" CTA without asking the user to
    retype their email.

    Note: this endpoint does NOT 503 when the effective email backend
    is disabled. It only redeems an already-issued token; redemption is
    independent of whether the backend can currently send NEW emails.
    The 503 gate lives only on issue/resend endpoints.
    """
    from ..services.password_reset_service import (
        PasswordPolicyError,
        RateLimitedError,
        TokenExpiredError,
        TokenInvalidError,
        get_password_reset_service_dependency,
    )

    service = get_password_reset_service_dependency()
    try:
        await service.complete_reset(request.token, request.new_password, db)
    except RateLimitedError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait a minute and try again.",
        )
    except TokenExpiredError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "message": str(e),
                    "code": "PASSWORD_RESET_TOKEN_EXPIRED",
                }
            },
        )
    except TokenInvalidError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PasswordPolicyError as e:
        # Surface the validation errors as the detail string. Same
        # convention the registration / change-password endpoints use.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    await db.commit()

    return SuccessResponse(
        data={
            "message": "Password reset successfully. You can now log in with your new password.",
        }
    )


@router.post(
    "/resend-password-reset-from-token",
    response_model=SuccessResponse[dict[str, str]],
    dependencies=[Depends(_check_auth_rate_limit)],
)
async def resend_password_reset_from_token(
    request: ResendPasswordResetFromTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """Re-issue a reset token using a (possibly expired) old token as
    identity (SHU-745). Mirrors SHU-507's resend-verification-from-token.

    Always returns 200 with a generic envelope — no enumeration regardless
    of whether the token matched a real user, was already used, ineligible,
    or rate-limited.
    """
    from ..core.email.factory import get_effective_email_backend_name as _eff_backend

    email_backend = _eff_backend()
    if email_backend == "disabled":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email backend is not configured; password reset is unavailable on this instance.",
        )

    from ..services.password_reset_service import get_password_reset_service_dependency

    service = get_password_reset_service_dependency()
    await service.resend_from_token(request.token, db)
    await db.commit()

    return SuccessResponse(data=_RESEND_RESET_NEUTRAL_RESPONSE)


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

        # SHU-745: refuse to mint a fresh access token for a refresh token
        # issued before the user's most recent password change. JWT
        # middleware applies the same gate to access tokens; this is the
        # corresponding rejection for refresh tokens (which have a much
        # longer TTL and would otherwise let a stolen pre-reset session
        # keep working until natural expiry).
        if user.password_changed_at is not None:
            from jose import jwt as _jwt

            from ..auth.jwt_manager import is_token_revoked_by_password_change

            try:
                refresh_payload = _jwt.decode(
                    request.refresh_token,
                    user_service.jwt_manager.secret_key,
                    algorithms=[user_service.jwt_manager.algorithm],
                )
                token_iat = refresh_payload.get("iat")
            except Exception:
                token_iat = None

            if is_token_revoked_by_password_change(token_iat, user.password_changed_at):
                logger.info(
                    "Refresh blocked for %s (user %s): token predates password change",
                    user.email,
                    user.id,
                    extra={
                        "event": "refresh.blocked_password_changed",
                        "user_id": user.id,
                        "email": user.email,
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session expired due to password change. Please sign in again.",
                )

        # SHU-507 defense-in-depth: refuse to mint a fresh access token for an
        # unverified password user when an email backend is configured. The
        # login endpoint already gates on email_verified; this is a belt and
        # suspenders so a regression in any login path can't leak through
        # refresh and grant an unverified user continued access.
        from ..core.email.factory import get_effective_email_backend_name as _eff_backend

        email_backend = _eff_backend()
        if email_backend != "disabled" and user.auth_method == "password" and not user.email_verified:
            logger.info(
                "Refresh blocked for %s (user %s): email not verified",
                user.email,
                user.id,
                extra={
                    "event": "refresh.blocked_unverified",
                    "user_id": user.id,
                    "email": user.email,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Please verify your email address before continuing.",
            )

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
    seat_service: SeatService | None = Depends(get_seat_service),
    x_seat_charge_confirmed: Annotated[str | None, Header(alias="X-Seat-Charge-Confirmed")] = None,
):
    """Update user role and status (admin only).

    A False→True flip on ``is_active`` consumes a seat — same two-phase
    preflight as ``activate_user`` so the Edit dialog can't bypass the
    seat charge.
    """
    try:
        # Lock + validate before any write so a 402 cancel doesn't leave the
        # role change committed. Previously `update_user_role` committed first
        # and the preflight ran after, so an admin who saw the 402 and bailed
        # still had their role edit stick.
        locked = await user_service.get_user_by_id(user_id, db, for_update=True)
        if locked is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        try:
            UserRole(request.role)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid role: {request.role}") from e

        # Preflight only when crossing inactive→active. Active→inactive
        # frees a seat (no Stripe write), and a no-op flip skips both.
        if request.is_active and not locked.is_active:
            preflight = await _preflight_seat_charge(db, seat_service, x_seat_charge_confirmed)
            if preflight is not None:
                return preflight

        # Single commit covers role + is_active so the row never lands in a
        # half-edited state.
        locked.role = request.role
        locked.is_active = request.is_active
        await db.commit()
        await db.refresh(locked)
        return SuccessResponse(data=locked.to_dict())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


def _raise_seat_error_http(e: Exception) -> None:
    """Map SeatService / Stripe domain errors onto HTTP status codes."""
    if isinstance(e, UserNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    if isinstance(e, UserStateError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if isinstance(e, SeatMinimumError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if isinstance(e, StripeClientError):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Billing provider error")
    raise e


def _require_seat_service(seat_service: SeatService | None) -> SeatService:
    """Reject the request when billing isn't configured.

    Use on routes that genuinely cannot run without Stripe (schedule /
    unschedule deactivation). `create_user` and `activate_user` use the
    optional dependency directly because they degrade to "no preflight"
    on self-hosted deploys.
    """
    if seat_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured",
        )
    return seat_service


async def _seat_charge_preview(
    db: AsyncSession,
    seat_service: SeatService | None,
    confirm_header: str | None,
) -> tuple[JSONResponse | None, bool]:
    """Phase 1 of the seat-charge preflight.

    Returns ``(response, charge_required)`` where:
    - ``response`` is a 402 JSONResponse to return immediately, or ``None``
      to proceed with the write.
    - ``charge_required`` is ``True`` only when the caller must invoke
      ``_seat_charge_confirm`` *after* its DB write. This is captured here
      so the caller doesn't re-check ``check_user_limit`` post-write — that
      second check would see the just-flushed/updated user and falsely
      conclude an upgrade is needed even when the new user fits an open
      seat.
    """
    if seat_service is None:
        return None, False
    try:
        limit_status = await check_user_limit(db)
    except StripeClientError as e:
        _raise_seat_error_http(e)
    if not (limit_status.at_limit and limit_status.enforcement == "hard"):
        return None, False

    if confirm_header != "true":
        details: dict[str, Any] = {
            "user_limit": limit_status.user_limit,
            "current_count": limit_status.current_count,
        }
        preview = await seat_service.preview_upgrade(db)
        if preview is not None:
            details["cost_per_seat_usd"] = preview.cost_per_seat_usd
            details["proration"] = {
                "amount_usd": preview.amount_usd,
                "period_end": preview.period_end.isoformat(),
            }
        envelope = ShuResponse.error(
            message="Seat limit reached. Confirm to add one seat and proceed.",
            code="seat_limit_reached",
            details=details,
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
        )
        # Caller returns this 402 immediately — charge_required is moot here.
        return envelope, False

    # Header confirmed: caller must invoke confirm_upgrade after its DB write.
    return None, True


async def _seat_charge_confirm(
    db: AsyncSession,
    seat_service: SeatService | None,
) -> None:
    """Phase 2 — apply the Stripe upgrade. Caller has already decided this is needed.

    No re-check of ``check_user_limit`` here on purpose: the phase-1 caller
    captured the decision before any DB write. Re-checking now would count
    a freshly-flushed user as already consuming a seat and trigger an
    unnecessary upgrade when the new user exactly fits the last open seat.
    """
    if seat_service is None:
        return
    try:
        await seat_service.confirm_upgrade(db)
    except StripeClientError as e:
        _raise_seat_error_http(e)


async def _preflight_seat_charge(
    db: AsyncSession,
    seat_service: SeatService | None,
    confirm_header: str | None,
) -> JSONResponse | None:
    """Run both preflight phases for callers that don't need to interleave a DB write.

    ``activate_user`` and ``update_user`` both check-then-commit a single
    UPDATE, so they can run preview and confirm back-to-back. ``create_user``
    splits these to wedge a flushed INSERT between them — see its body.
    """
    preview, charge_required = await _seat_charge_preview(db, seat_service, confirm_header)
    if preview is not None:
        return preview
    if charge_required:
        await _seat_charge_confirm(db, seat_service)
    return None


@router.post("/users", response_model=SuccessResponse[dict[str, Any]])
async def create_user(
    request: CreateUserRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
    seat_service: SeatService | None = Depends(get_seat_service),
    x_seat_charge_confirmed: Annotated[str | None, Header(alias="X-Seat-Charge-Confirmed")] = None,
):
    """Create a new user (admin only).

    Admin-created users are active on creation, which may consume a seat
    under hard enforcement. In that case the endpoint runs a two-phase
    preflight: a 402 preview on first call, then a Stripe upgrade when
    the client re-submits with `X-Seat-Charge-Confirmed: true`.
    """
    try:
        # Pre-validate everything we can deterministically check before any
        # DB or Stripe write. Pre-checks fail fast on the obvious cases;
        # the FLUSH below catches the narrow concurrent-INSERT race that
        # could slip past the email pre-check.
        if request.auth_method == "password" and not request.password:
            raise ValueError("Password is required for password authentication")
        try:
            UserRole(request.role)
        except ValueError as e:
            raise ValueError(f"Invalid role: {request.role}") from e
        existing = await user_service.get_user_by_email(request.email, db)
        if existing is not None:
            raise ValueError(f"User with email {request.email} already exists")

        # Phase 1: capture the seat-charge decision *before* any DB write.
        # We can't re-check check_user_limit after the flush — the flushed
        # user would be counted as active and falsely flip at_limit, so a
        # tenant going from N-1 active to N seats would get charged for a
        # second extra seat even though the new user fits the last open one.
        preview, charge_required = await _seat_charge_preview(db, seat_service, x_seat_charge_confirmed)
        if preview is not None:
            return preview

        # Phase 2: flush the user INSERT first so the unique-email constraint
        # fires *before* Stripe is charged. If a concurrent request inserted
        # the same email between our pre-check and here, the flush raises
        # IntegrityError and we abort with no seat charge. If the flush
        # succeeds, only then do we charge Stripe; if Stripe fails we roll
        # back the insert. The remaining narrow window is "Stripe succeeds,
        # commit fails" — same edge case the design accepted as unavoidable
        # without 2PC.
        try:
            if request.auth_method == "password":
                user = await password_auth_service.create_user(
                    email=request.email,
                    password=request.password,
                    name=request.name,
                    role=request.role,
                    db=db,
                    admin_created=True,
                    flush_only=True,
                )
            else:
                user = User(
                    email=request.email,
                    name=request.name,
                    auth_method=request.auth_method,
                    role=request.role,
                    is_active=True,
                )
                db.add(user)
                await db.flush()
        except IntegrityError as e:
            await db.rollback()
            raise ValueError(f"User with email {request.email} already exists") from e

        if charge_required:
            try:
                await _seat_charge_confirm(db, seat_service)
            except HTTPException:
                # Stripe failed (mapped to 502 inside the helper). Roll back
                # the flushed user — no orphan row, no orphan seat.
                await db.rollback()
                raise

        await db.commit()
        await db.refresh(user)
        return SuccessResponse(data=user.to_dict())

    except HTTPException:
        raise
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
    seat_service: SeatService | None = Depends(get_seat_service),
    x_seat_charge_confirmed: Annotated[str | None, Header(alias="X-Seat-Charge-Confirmed")] = None,
):
    """Activate a user account (admin only).

    Activating a pending user consumes a seat, so it runs the same
    two-phase preflight as create_user when the activation would cross
    the hard seat limit.
    """
    try:
        # Lock the row so two concurrent activate requests serialise here:
        # whichever wins commits is_active=True; the loser sees the post-write
        # state, hits the "already active" branch, and skips a duplicate charge.
        user = await user_service.get_user_by_id(user_id, db, for_update=True)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # Skip the preflight for already-active users — activation is a no-op
        # and would otherwise double-charge the admin on a redundant click.
        if not user.is_active:
            preflight = await _preflight_seat_charge(db, seat_service, x_seat_charge_confirmed)
            if preflight is not None:
                return preflight

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


@router.post(
    "/users/{user_id}/schedule-deactivation",
    response_model=SuccessResponse[dict[str, Any]],
)
async def schedule_user_deactivation(
    user_id: str,
    _current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
    seat_service: SeatService | None = Depends(get_seat_service),
):
    """Schedule a user's deactivation for the next billing period end (admin only).

    Flags the user locally and schedules the Stripe seat quantity to drop
    at period end via the SHU-704 primitive. The user stays active (and
    billed) until the rollover fires.
    """
    seat_service = _require_seat_service(seat_service)
    try:
        await seat_service.flag_user(db, user_id)
    except (UserNotFoundError, UserStateError, SeatMinimumError, StripeClientError) as e:
        _raise_seat_error_http(e)

    user = await user_service.get_user_by_id(user_id, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return SuccessResponse(data=user.to_dict())


@router.delete(
    "/users/{user_id}/schedule-deactivation",
    response_model=SuccessResponse[dict[str, Any]],
)
async def unschedule_user_deactivation(
    user_id: str,
    _current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
    seat_service: SeatService | None = Depends(get_seat_service),
):
    """Cancel a previously scheduled deactivation (admin only).

    At parity (no other flagged users), this clears the pending Stripe
    downgrade via the primitive's release-schedule branch.
    """
    seat_service = _require_seat_service(seat_service)
    try:
        await seat_service.unflag_user(db, user_id)
    except (UserNotFoundError, UserStateError, StripeClientError) as e:
        _raise_seat_error_http(e)

    user = await user_service.get_user_by_id(user_id, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return SuccessResponse(data=user.to_dict())


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
