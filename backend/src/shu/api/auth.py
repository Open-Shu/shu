"""Authentication API endpoints for Shu"""

from datetime import datetime, timezone
import logging
from typing import Dict, Any, List

from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from ..api.dependencies import get_db
from ..auth import User, UserRole, JWTManager
from ..auth.rbac import get_current_user, require_admin
from ..auth.password_auth import password_auth_service
from ..core.config import get_settings_instance
from ..core.rate_limiting import get_rate_limit_service
from ..models.provider_identity import ProviderIdentity
from ..schemas.envelope import SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])


async def _check_auth_rate_limit(request: Request) -> None:
    """
    Enforces authentication-specific rate limits and raises HTTP 429 when exceeded.
    
    If the configured rate limit service is disabled this function returns immediately. Otherwise it determines the client's IP from the provided Request and checks the authentication rate limit; when the limit is exceeded an HTTPException with status 429 and rate-limit headers is raised.
    
    Parameters:
        request (Request): FastAPI request used to determine client IP and headers.
    
    Raises:
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
    """Request model for Google OAuth login endpoint"""
    google_token: str

class PasswordLoginRequest(BaseModel):
    """Request model for password login endpoint"""
    email: str
    password: str

class RegisterRequest(BaseModel):
    """Request model for user registration endpoint"""
    email: str
    password: str
    name: str

class ChangePasswordRequest(BaseModel):
    """Request model for password change endpoint"""
    old_password: str
    new_password: str

class RefreshTokenRequest(BaseModel):
    """Request model for token refresh endpoint"""
    refresh_token: str

class TokenResponse(BaseModel):
    """Response model for token endpoints"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: Dict[str, Any]

class UserUpdateRequest(BaseModel):
    """Request model for updating user"""
    role: str
    is_active: bool = True

class CreateUserRequest(BaseModel):
    """Request model for admin to create new users"""
    email: str
    name: str
    role: str = "regular_user"
    password: str = None  # Optional - if not provided, user must use Google OAuth
    auth_method: str = "password"  # 'password' or 'google'

class UserService:
    """Service for user management operations"""

    def __init__(self):
        self.jwt_manager = JWTManager()
        self.settings = get_settings_instance()

    async def get_user_by_id(self, user_id: str, db: AsyncSession) -> User:
        """Get user by ID"""
        from sqlalchemy import select
        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_users(self, db: AsyncSession) -> List[User]:
        """Get all users (admin only)"""
        from sqlalchemy import select
        stmt = select(User).order_by(User.is_active.desc(), User.name.asc())
        result = await db.execute(stmt)
        return result.scalars().all()

    async def update_user_role(self, user_id: str, new_role: str, db: AsyncSession) -> User:
        """Update user role (admin only)"""
        from sqlalchemy import select
        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise ValueError("User not found")

        # Validate role
        try:
            UserRole(new_role)
        except ValueError:
            raise ValueError("Invalid role")

        user.role = new_role
        await db.commit()
        await db.refresh(user)
        return user

    async def delete_user(self, user_id: str, current_user_id: str, db: AsyncSession) -> bool:
        """Delete user (admin only)"""
        from sqlalchemy import select

        # Prevent self-deletion
        if user_id == current_user_id:
            raise ValueError("Cannot delete your own account")

        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise ValueError("User not found")

        # Delete the user
        await db.delete(user)
        await db.commit()

        logger.info(f"User deleted: {user.email} (ID: {user_id})")
        return True

    async def determine_user_role(self, email: str, is_first_user: bool) -> UserRole:
        # Determine user role
        is_admin_email = email.lower() in [
            email.lower()
            for email in self.settings.admin_emails
        ]

        if is_first_user or is_admin_email:
            return UserRole.ADMIN
        else:
            return UserRole.REGULAR_USER

    async def is_first_user(self, db: AsyncSession) -> bool:
        count_stmt = select(User)
        count_result = await db.execute(count_stmt)
        existing_users = count_result.scalars().all()
        return len(existing_users) == 0

    async def is_active(self, user_role: UserRole, is_first_user: bool) -> bool:
        return is_first_user or user_role == UserRole.ADMIN

    async def get_user_auth_method(self, db: AsyncSession, email: str) -> str:
        auth_method_result = await db.execute(select(User.auth_method).where(User.email == email))
        return auth_method_result.scalar_one_or_none()

    async def authenticate_or_create_sso_user(
        self,
        provider_info: Dict[str, Any],
        db: AsyncSession
    ) -> User:
        """
        Authenticate or create a user from SSO provider info.
        
        This method is provider-agnostic. Any provider-specific logic (like backward
        compatibility for legacy storage) should be handled in the adapter's 
        get_user_info() method before calling this.
        
        Args:
            provider_info: Normalized provider info with keys:
                - provider_id: Provider's unique user identifier
                - provider_key: Provider name ("google" or "microsoft")
                - email: User's email address
                - name: User's display name
                - picture: Avatar URL (optional)
                - existing_user: Optional[User] - Pre-looked-up user from adapter (for backward compat)
            db: Database session
            
        Returns:
            Authenticated User object
            
        Raises:
            HTTPException: 409 if user exists with password auth
            HTTPException: 400 if user account is inactive
            HTTPException: 201 if new user created but requires activation
        """
        email = provider_info["email"]
        provider_id = provider_info["provider_id"]
        provider_key = provider_info["provider_key"]
        
        # Check for password auth conflict
        auth_method = await self.get_user_auth_method(db, email)
        if auth_method == "password":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This account uses password authentication. Please use the username & password login flow."
            )
        
        # Check if adapter already found an existing user (e.g., via legacy google_id lookup)
        existing_user_from_adapter = provider_info.get("existing_user")
        if existing_user_from_adapter:
            if not existing_user_from_adapter.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User account is inactive. Please contact an administrator for activation."
                )
            # Ensure ProviderIdentity exists (migration on login)
            await self._ensure_provider_identity(existing_user_from_adapter, provider_info, db)
            existing_user_from_adapter.last_login = datetime.now(timezone.utc)
            # Update avatar if provided and different
            new_picture = provider_info.get("picture")
            if new_picture and new_picture != existing_user_from_adapter.picture_url:
                existing_user_from_adapter.picture_url = new_picture
            await db.commit()
            return existing_user_from_adapter
        
        # Look up existing identity in ProviderIdentity table
        user = await self._get_user_by_identity(provider_key, provider_id, db)
        if user:
            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User account is inactive. Please contact an administrator for activation."
                )
            user.last_login = datetime.now(timezone.utc)
            # Update avatar if provided and different
            new_picture = provider_info.get("picture")
            if new_picture and new_picture != user.picture_url:
                user.picture_url = new_picture
            await db.commit()
            return user
        
        # Check if user exists by email (link identity to existing user)
        email_stmt = select(User).where(User.email == email)
        email_result = await db.execute(email_stmt)
        existing_user = email_result.scalar_one_or_none()
        
        if existing_user:
            if not existing_user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User account is inactive. Please contact an administrator for activation."
                )
            await self._create_provider_identity(existing_user, provider_info, db)
            existing_user.last_login = datetime.now(timezone.utc)
            await db.commit()
            logger.info(f"Linked {provider_key} identity to existing user", extra={"email": email})
            return existing_user
        
        # Create new user
        return await self._create_new_sso_user(provider_info, db)

    async def _get_user_by_identity(
        self,
        provider_key: str,
        provider_id: str,
        db: AsyncSession
    ) -> User | None:
        """Get user from ProviderIdentity table.
        
        Args:
            provider_key: Provider name ("google" or "microsoft")
            provider_id: Provider's unique user identifier
            db: Database session
            
        Returns:
            User if found via ProviderIdentity, None otherwise
        """
        stmt = select(ProviderIdentity).where(
            ProviderIdentity.provider_key == provider_key,
            ProviderIdentity.account_id == provider_id
        )
        result = await db.execute(stmt)
        existing_identity = result.scalar_one_or_none()
        
        if not existing_identity:
            return None
        
        # Fetch the user
        user_stmt = select(User).where(User.id == existing_identity.user_id)
        user_result = await db.execute(user_stmt)
        user = user_result.scalar_one_or_none()
        
        if not user:
            # Orphaned identity - should not happen
            logger.warning(f"Orphaned ProviderIdentity found", extra={"provider_key": provider_key, "provider_id": provider_id})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="User account data inconsistency. Please contact support."
            )
        
        return user

    async def _ensure_provider_identity(
        self,
        user: User,
        provider_info: Dict[str, Any],
        db: AsyncSession
    ) -> None:
        """Ensure ProviderIdentity exists for user, create if missing (migration on login).
        
        Args:
            user: The user to ensure identity for
            provider_info: Normalized provider info dict
            db: Database session
        """
        stmt = select(ProviderIdentity).where(
            ProviderIdentity.user_id == user.id,
            ProviderIdentity.provider_key == provider_info["provider_key"],
            ProviderIdentity.account_id == provider_info["provider_id"]
        )
        result = await db.execute(stmt)
        if not result.scalar_one_or_none():
            await self._create_provider_identity(user, provider_info, db)

    async def _create_provider_identity(
        self,
        user: User,
        provider_info: Dict[str, Any],
        db: AsyncSession
    ) -> ProviderIdentity:
        """Create ProviderIdentity linking user to provider.
        
        Args:
            user: The user to link
            provider_info: Normalized provider info dict
            db: Database session
            
        Returns:
            The created ProviderIdentity
        """
        identity = ProviderIdentity(
            user_id=user.id,
            provider_key=provider_info["provider_key"],
            account_id=provider_info["provider_id"],
            primary_email=provider_info["email"],
            display_name=provider_info["name"],
            avatar_url=provider_info.get("picture"),
        )
        db.add(identity)
        await db.flush()
        return identity

    async def _create_new_sso_user(
        self,
        provider_info: Dict[str, Any],
        db: AsyncSession
    ) -> User:
        """Create new user from SSO provider info.
        
        Args:
            provider_info: Normalized provider info dict
            db: Database session
            
        Returns:
            The created User
            
        Raises:
            HTTPException: 201 if user requires activation
        """
        email = provider_info["email"]
        provider_key = provider_info["provider_key"]
        
        is_first_user = await self.is_first_user(db)
        user_role = await self.determine_user_role(email, is_first_user)
        is_active = await self.is_active(user_role, is_first_user)
        
        user = User(
            email=email,
            name=provider_info["name"],
            google_id=None,  # No longer used for new users
            picture_url=provider_info.get("picture"),
            role=user_role.value,
            auth_method=provider_key,
            is_active=is_active,
            last_login=datetime.now(timezone.utc) if is_active else None
        )
        
        if user_role == UserRole.ADMIN:
            if is_first_user:
                logger.info(f"Creating first user as admin via {provider_key}", extra={"email": email})
            else:
                logger.info(f"Creating admin user from configured list via {provider_key}", extra={"email": email})
        
        db.add(user)
        await db.commit()
        await db.refresh(user)
        
        # Create ProviderIdentity
        await self._create_provider_identity(user, provider_info, db)
        await db.commit()
        
        if not is_active:
            raise HTTPException(
                status_code=status.HTTP_201_CREATED,
                detail="Account was created, but will need to be activated. Please contact an administrator for activation."
            )
        
        return user


user_service = UserService()


def create_token_response(user: User, jwt_manager: JWTManager) -> TokenResponse:
    """Create JWT token response for authenticated user.
    
    Args:
        user: The authenticated user
        jwt_manager: JWTManager instance for token creation
        
    Returns:
        TokenResponse with access_token, refresh_token, and user data
    """
    access_token = jwt_manager.create_access_token(user.to_dict())
    refresh_token = jwt_manager.create_refresh_token(user.id)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=user.to_dict()
    )


@router.post("/login", response_model=SuccessResponse[TokenResponse])
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(_check_auth_rate_limit),
):
    """
    Authenticate or create a user using a Google ID token and return JWT access and refresh tokens.
    
    Authenticates the incoming Google ID token, creates the user if needed, and issues an access token and refresh token packaged with the user's public data.
    Uses the unified SSO authentication architecture via adapter.get_user_info() and
    user_service.authenticate_or_create_sso_user().
    
    Parameters:
        request (LoginRequest): Payload containing the Google ID token.
        db (AsyncSession): Database session injected via dependency.
        _rate_limit: Rate limiting dependency (enforces auth rate limits).
    
    Returns:
        SuccessResponse: Contains a TokenResponse with `access_token`, `refresh_token`, `token_type`, and `user` dictionary.
    
    Raises:
        HTTPException: Raised with 401 when authentication/verification fails; propagated as-is for other HTTP errors; raised with 500 for unexpected internal errors.
    """
    try:
        from ..plugins.host.auth_capability import AuthCapability
        from ..providers.registry import get_auth_adapter

        auth = AuthCapability(plugin_name="admin", user_id="anonymous")
        adapter = get_auth_adapter("google", auth)

        # Get normalized user info from adapter (includes legacy google_id lookup for backward compat)
        provider_info = await adapter.get_user_info(id_token=request.google_token, db=db)

        # Authenticate or create user using unified SSO method
        user = await user_service.authenticate_or_create_sso_user(provider_info, db)

        # Create JWT token response
        return SuccessResponse(data=create_token_response(user, user_service.jwt_manager))

    except ValueError as e:
        # Adapter errors (token verification failures) come as ValueError
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed"
        )

@router.post("/register", response_model=SuccessResponse[Dict[str, str]], dependencies=[Depends(_check_auth_rate_limit)])
async def register_user(request: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Register a new user account using email and password.
    
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
        user_role = await user_service.determine_user_role(request.email, is_first_user)
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
        return SuccessResponse(data={
            "message": "Registration successful!" + (
                " Your account has been created but requires administrator activation before you can log in."
                if not is_admin else
                ""
            ),
            "email": user.email,
            "status": "pending_activation" if not is_admin else "activated"
        })

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed"
        )

@router.post("/login/password", response_model=SuccessResponse[TokenResponse], dependencies=[Depends(_check_auth_rate_limit)])
async def login_with_password(request: PasswordLoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Authenticate a user using email and password and return JWT tokens and user info.
    
    Parameters:
        request (PasswordLoginRequest): Contains the user's `email` and `password`.
        db (AsyncSession): Database session (typically injected via dependency).
    
    Returns:
        SuccessResponse: Contains a TokenResponse with `access_token`, `refresh_token`, `token_type`, and `user` (user data dictionary).
    
    Raises:
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
                    detail="This account uses Google authentication. Please use the Google login flow."
                )
            elif auth_method == "microsoft":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This account uses Microsoft authentication. Please use the Microsoft login flow."
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"This account uses {auth_method} authentication. Please use the appropriate login flow."
                )

        user = await password_auth_service.authenticate_user(request.email, request.password, db)

        # Create JWT tokens
        access_token = user_service.jwt_manager.create_access_token(user.to_dict())
        refresh_token = user_service.jwt_manager.create_refresh_token(user.id)

        response_data = TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=user.to_dict()
        )

        return SuccessResponse(data=response_data)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Password login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed"
        )

@router.put("/change-password", response_model=SuccessResponse[Dict[str, str]])
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Change current user's password"""
    try:
        await password_auth_service.change_password(
            user_id=current_user.id,
            old_password=request.old_password,
            new_password=request.new_password,
            db=db
        )

        return SuccessResponse(data={"message": "Password changed successfully"})

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Password change error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password change failed"
        )


@router.post("/refresh", response_model=SuccessResponse[TokenResponse])
async def refresh_token(request: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    """Refresh access token using refresh token"""
    try:
        # Verify refresh token and get user_id
        user_id = user_service.jwt_manager.refresh_access_token(request.refresh_token)

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token"
            )

        # Get current user data from database
        from sqlalchemy import select
        from ..auth.models import User

        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive"
            )

        # Create new tokens
        access_token = user_service.jwt_manager.create_access_token(user.to_dict())
        refresh_token = user_service.jwt_manager.create_refresh_token(user.id)

        response_data = TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=user.to_dict()
        )

        return SuccessResponse(data=response_data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token refresh error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token refresh failed"
        )

@router.get("/me", response_model=SuccessResponse[Dict[str, Any]])
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information"""
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


@router.post("/google/exchange-login", response_model=SuccessResponse[TokenResponse], dependencies=[Depends(_check_auth_rate_limit)])
async def google_exchange_login(request: CodeRequest, db: AsyncSession = Depends(get_db)):
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

        # Get normalized user info from adapter (includes legacy google_id lookup for backward compat)
        provider_info = await adapter.get_user_info(id_token=id_token, db=db)

        # Authenticate or create user using unified SSO method
        user = await user_service.authenticate_or_create_sso_user(provider_info, db)

        # Create JWT token response
        return SuccessResponse(data=create_token_response(user, user_service.jwt_manager))

    except HTTPException:
        raise
    except ValueError as e:
        # Adapter errors (token verification failures) come as ValueError
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
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
        logger.error("microsoft_login error", error=str(e), exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Microsoft SSO redirect unavailable: {e}")


@router.post("/microsoft/exchange-login", response_model=SuccessResponse[TokenResponse], dependencies=[Depends(_check_auth_rate_limit)])
async def microsoft_exchange_login(request: CodeRequest, db: AsyncSession = Depends(get_db)):
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
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Microsoft did not return access_token")

        # Get normalized user info from adapter
        provider_info = await adapter.get_user_info(access_token=access_token)

        # Authenticate or create user using unified SSO method
        user = await user_service.authenticate_or_create_sso_user(provider_info, db)

        # Create JWT token response
        return SuccessResponse(data=create_token_response(user, user_service.jwt_manager))

    except HTTPException:
        raise
    except ValueError as e:
        # Adapter errors (user info request failures) come as ValueError
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except Exception as e:
        logger.error("microsoft_exchange_login error", error=str(e), exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Microsoft login exchange failed")


@router.get("/users", response_model=SuccessResponse[List[Dict[str, Any]]])
async def get_all_users(current_user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Get all users (admin only)"""
    users = await user_service.get_all_users(db)
    return SuccessResponse(data=[user.to_dict() for user in users])

@router.put("/users/{user_id}", response_model=SuccessResponse[Dict[str, Any]])
async def update_user(
    user_id: str,
    request: UserUpdateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update user role and status (admin only)"""
    try:
        user = await user_service.update_user_role(user_id, request.role, db)
        user.is_active = request.is_active
        await db.commit()
        await db.refresh(user)
        return SuccessResponse(data=user.to_dict())
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )

@router.post("/users", response_model=SuccessResponse[Dict[str, Any]])
async def create_user(
    request: CreateUserRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new user (admin only)"""
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
                admin_created=True  # Admin-created users are active by default
            )
        else:
            # Create user for Google OAuth (password will be None)
            # Admin-created users are active by default
            user = User(
                email=request.email,
                name=request.name,
                auth_method="google",
                role=request.role,
                is_active=True  # Admin-created users are active
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)

        return SuccessResponse(data=user.to_dict())

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"User creation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User creation failed"
        )

@router.patch("/users/{user_id}/activate", response_model=SuccessResponse[Dict[str, Any]])
async def activate_user(
    user_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Activate a user account (admin only)"""
    try:
        # Get user by ID
        user = await user_service.get_user_by_id(user_id, db)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User activation failed"
        )

@router.patch("/users/{user_id}/deactivate", response_model=SuccessResponse[Dict[str, Any]])
async def deactivate_user(
    user_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Deactivate a user account (admin only)"""
    try:
        # Get user by ID
        user = await user_service.get_user_by_id(user_id, db)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Prevent self-deactivation
        if user.id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate your own account"
            )

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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User deactivation failed"
        )

@router.delete("/users/{user_id}", response_model=SuccessResponse[Dict[str, str]])
async def delete_user(
    user_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Delete user (admin only)"""
    try:
        await user_service.delete_user(user_id, current_user.id, db)
        return SuccessResponse(data={"message": "User deleted successfully"})
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"User deletion error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User deletion failed"
        )