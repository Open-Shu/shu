"""Authentication API endpoints for Shu"""

from fastapi import APIRouter, HTTPException, Depends, status
from starlette.requests import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Dict, Any, List
import logging
import uuid
from datetime import datetime, timezone

from ..auth import User, UserRole, JWTManager
from ..auth.rbac import get_current_user, require_admin
from ..auth.password_auth import password_auth_service
from ..schemas.envelope import SuccessResponse
from ..core.config import get_settings_instance
from ..core.rate_limiting import get_rate_limit_service
from ..api.dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])


async def _check_auth_rate_limit(request: Request) -> None:
    """Rate limit dependency for auth endpoints.

    Uses stricter rate limits for brute-force protection.
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

async def _verify_google_id_token(id_token: str) -> Dict[str, Any]:
    """Verify Google ID token using Google's tokeninfo endpoint with proper TLS trust.

    Uses httpx with certifi CA bundle to avoid local trust store issues (e.g., macOS).
    """
    import httpx, certifi

    if not id_token:
        raise ValueError("Missing Google ID token")

    url = "https://oauth2.googleapis.com/tokeninfo"

    try:
        async with httpx.AsyncClient(verify=certifi.where(), timeout=httpx.Timeout(15.0)) as client:
            resp = await client.get(url, params={"id_token": id_token}, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            text = resp.text[:300]
            raise ValueError(f"Google token verification failed: HTTP {resp.status_code}: {text}")
        data = resp.json()
    except httpx.HTTPError as e:
        logger.error("Google token verification network error: %s", e)
        raise ValueError(f"Network error during Google token verification: {e}")

    # Map to our expected fields
    sub = data.get("sub")
    email = data.get("email")
    if not sub or not email:
        raise ValueError("Invalid Google ID token payload")
    return {
        "google_id": sub,
        "email": email,
        "name": data.get("name") or email.split("@")[0],
        "picture": data.get("picture"),
    }


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

    async def authenticate_or_create_google_user(self, google_token: str, db: AsyncSession) -> User:
        """Authenticate user with Google token or create new user"""
        # Verify Google ID token via Google's tokeninfo endpoint
        google_user = await _verify_google_id_token(google_token)

        auth_method = await user_service.get_user_auth_method(db, google_user["email"])
        if auth_method == "password":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The provided account was created using a password. Please use the username & password login flow."
            )

        # Check if user exists in database
        stmt = select(User).where(User.google_id == google_user["google_id"])
        result = await db.execute(stmt)
        existing_user = result.scalar_one_or_none()

        if existing_user:
            # Check if user is active
            if not existing_user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User account is inactive. Please contact an administrator for activation."
                )

            # Update last login and refresh avatar if provided
            existing_user.last_login = datetime.now(timezone.utc)
            try:
                new_picture = google_user.get("picture")
                if new_picture and new_picture != existing_user.picture_url:
                    existing_user.picture_url = new_picture
            except Exception:
                pass
            await db.commit()
            return existing_user

        is_first_user = await self.is_first_user(db)
        user_role = await self.determine_user_role(google_user["email"], is_first_user)
        is_active = await self.is_active(user_role, is_first_user)

        user = User(
            email=google_user["email"],
            name=google_user["name"],
            google_id=google_user["google_id"],
            picture_url=google_user.get("picture"),
            role=user_role.value,
            is_active=is_active,
            last_login=datetime.now(timezone.utc) if is_active else None
        )

        if user_role == UserRole.ADMIN.value:
            if is_first_user:
                logger.info(f"Creating first user as admin: {google_user['email']}")
            else:
                logger.info(f"Creating admin user from configured list: {google_user['email']}")

        # Save user to database
        db.add(user)
        await db.commit()
        await db.refresh(user)

        if not is_active:
            raise HTTPException(
                status_code=status.HTTP_201_CREATED,
                detail="Account was created, but will need to be activated. Please contact an administrator for activation."
            )

        return user

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


user_service = UserService()


@router.post("/login", response_model=SuccessResponse[TokenResponse])
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(_check_auth_rate_limit),
):
    """Authenticate user with Google token"""
    try:
        # Authenticate or create user
        user = await user_service.authenticate_or_create_google_user(request.google_token, db)

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
        logger.error(f"Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed"
        )

@router.post("/register", response_model=SuccessResponse[Dict[str, str]], dependencies=[Depends(_check_auth_rate_limit)])
async def register_user(request: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user with email and password (requires admin activation)"""
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
    """Authenticate user with email and password"""
    try:
        auth_method = await user_service.get_user_auth_method(db, request.email)
        if auth_method == "google":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The provided account was created using Google. Please use the Google login flow."
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

        # Authenticate or create user using the ID token
        user = await user_service.authenticate_or_create_google_user(id_token, db)

        # Create JWT tokens
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
        logger.error(f"google_exchange_login error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Google login exchange failed")


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
