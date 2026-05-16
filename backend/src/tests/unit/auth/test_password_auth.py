"""Unit tests for PasswordAuthService password validation and generation.

Validates: Requirements 7.4, 7.7, 7.10
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.auth.password_auth import PasswordAuthService

DEFAULT_SPECIAL_CHARS = "!@#$%^&*()-_+="


@pytest.fixture
def mock_settings_moderate():
    """Settings mock with moderate password policy."""
    settings = MagicMock()
    settings.password_policy = "moderate"
    settings.password_min_length = 8
    settings.password_special_chars = DEFAULT_SPECIAL_CHARS
    return settings


@pytest.fixture
def mock_settings_strict():
    """Settings mock with strict password policy."""
    settings = MagicMock()
    settings.password_policy = "strict"
    settings.password_min_length = 8
    settings.password_special_chars = DEFAULT_SPECIAL_CHARS
    return settings


@pytest.fixture
def service_moderate(mock_settings_moderate):
    """PasswordAuthService configured with moderate policy."""
    with patch("shu.auth.password_auth.get_settings_instance", return_value=mock_settings_moderate):
        return PasswordAuthService()


@pytest.fixture
def service_strict(mock_settings_strict):
    """PasswordAuthService configured with strict policy."""
    with patch("shu.auth.password_auth.get_settings_instance", return_value=mock_settings_strict):
        return PasswordAuthService()


# ---------------------------------------------------------------------------
# validate_password — moderate policy
# ---------------------------------------------------------------------------


class TestValidatePasswordModerate:
    """Tests for validate_password() under the moderate policy."""

    def test_valid_password(self, service_moderate: PasswordAuthService) -> None:
        """A password meeting all moderate rules should return no errors."""
        errors = service_moderate.validate_password("Abcdef1x")
        assert errors == []

    def test_too_short(self, service_moderate: PasswordAuthService) -> None:
        """A password shorter than min length should be rejected."""
        errors = service_moderate.validate_password("Ab1xyzA")
        assert any("at least 8 characters" in e for e in errors)

    def test_missing_uppercase(self, service_moderate: PasswordAuthService) -> None:
        """A password without uppercase should be rejected."""
        errors = service_moderate.validate_password("abcdefg1")
        assert any("uppercase" in e for e in errors)

    def test_missing_lowercase(self, service_moderate: PasswordAuthService) -> None:
        """A password without lowercase should be rejected."""
        errors = service_moderate.validate_password("ABCDEFG1")
        assert any("lowercase" in e for e in errors)

    def test_missing_digit(self, service_moderate: PasswordAuthService) -> None:
        """A password without a digit should be rejected."""
        errors = service_moderate.validate_password("Abcdefgh")
        assert any("digit" in e for e in errors)

    def test_multiple_violations(self, service_moderate: PasswordAuthService) -> None:
        """A very weak password should return multiple errors."""
        errors = service_moderate.validate_password("aaa")
        assert len(errors) >= 3

    def test_no_special_char_required(self, service_moderate: PasswordAuthService) -> None:
        """Moderate policy should NOT require special characters."""
        errors = service_moderate.validate_password("Abcdefg1")
        assert not any("special character" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_password — strict policy
# ---------------------------------------------------------------------------


class TestValidatePasswordStrict:
    """Tests for validate_password() under the strict policy."""

    def test_valid_password(self, service_strict: PasswordAuthService) -> None:
        """A password meeting all strict rules should return no errors."""
        errors = service_strict.validate_password("Abcdef1!")
        assert errors == []

    def test_missing_special_char(self, service_strict: PasswordAuthService) -> None:
        """Strict policy should reject passwords without a special character."""
        errors = service_strict.validate_password("Abcdefg1")
        assert any("special character" in e for e in errors)

    def test_too_short_strict(self, service_strict: PasswordAuthService) -> None:
        """Short passwords should still fail under strict policy."""
        errors = service_strict.validate_password("Ab1!")
        assert any("at least 8 characters" in e for e in errors)

    def test_missing_uppercase_strict(self, service_strict: PasswordAuthService) -> None:
        """Strict policy still enforces uppercase."""
        errors = service_strict.validate_password("abcdefg1!")
        assert any("uppercase" in e for e in errors)

    def test_missing_lowercase_strict(self, service_strict: PasswordAuthService) -> None:
        """Strict policy still enforces lowercase."""
        errors = service_strict.validate_password("ABCDEFG1!")
        assert any("lowercase" in e for e in errors)

    def test_missing_digit_strict(self, service_strict: PasswordAuthService) -> None:
        """Strict policy still enforces digits."""
        errors = service_strict.validate_password("Abcdefgh!")
        assert any("digit" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_password — configurable min length
# ---------------------------------------------------------------------------


class TestValidatePasswordMinLength:
    """Tests for configurable password_min_length."""

    def test_custom_min_length(self) -> None:
        """Validate that a custom min length is respected."""
        settings = MagicMock()
        settings.password_policy = "moderate"
        settings.password_min_length = 12
        settings.password_special_chars = DEFAULT_SPECIAL_CHARS
        with patch("shu.auth.password_auth.get_settings_instance", return_value=settings):
            svc = PasswordAuthService()

        errors = svc.validate_password("Abcdefg1")  # 8 chars, below 12
        assert any("at least 12 characters" in e for e in errors)

    def test_custom_min_length_passes(self) -> None:
        """A password meeting the custom min length should pass."""
        settings = MagicMock()
        settings.password_policy = "moderate"
        settings.password_min_length = 12
        settings.password_special_chars = DEFAULT_SPECIAL_CHARS
        with patch("shu.auth.password_auth.get_settings_instance", return_value=settings):
            svc = PasswordAuthService()

        errors = svc.validate_password("Abcdefghij1x")  # 12 chars
        assert errors == []


# ---------------------------------------------------------------------------
# generate_temporary_password
# ---------------------------------------------------------------------------


class TestGenerateTemporaryPassword:
    """Tests for generate_temporary_password()."""

    def test_default_length(self, service_moderate: PasswordAuthService) -> None:
        """Default generated password should be 16 characters."""
        pw = service_moderate.generate_temporary_password()
        assert len(pw) == 16

    def test_custom_length(self, service_moderate: PasswordAuthService) -> None:
        """Generated password should respect a custom length."""
        pw = service_moderate.generate_temporary_password(length=24)
        assert len(pw) == 24

    def test_meets_strict_policy(self, service_strict: PasswordAuthService) -> None:
        """Generated password must always satisfy the strict policy."""
        pw = service_strict.generate_temporary_password()
        errors = service_strict.validate_password(pw)
        assert errors == [], f"Generated password '{pw}' failed strict validation: {errors}"

    def test_meets_strict_even_under_moderate(self, service_moderate: PasswordAuthService) -> None:
        """Generated password must satisfy strict rules even when service uses moderate policy."""
        special = service_moderate.special_chars
        for _ in range(20):
            pw = service_moderate.generate_temporary_password()
            assert any(c.isupper() for c in pw), f"No uppercase in '{pw}'"
            assert any(c.islower() for c in pw), f"No lowercase in '{pw}'"
            assert any(c.isdigit() for c in pw), f"No digit in '{pw}'"
            assert any(c in special for c in pw), f"No special char in '{pw}'"

    def test_different_passwords(self, service_moderate: PasswordAuthService) -> None:
        """Multiple calls should produce different passwords."""
        passwords = {service_moderate.generate_temporary_password() for _ in range(10)}
        assert len(passwords) > 1, "generate_temporary_password produced identical passwords"


# ---------------------------------------------------------------------------
# change_password
# ---------------------------------------------------------------------------


class TestChangePassword:
    """Tests for change_password().

    Validates: Requirements 7.1, 7.2, 7.3, 7.5, 7.6, 7.8
    """

    OLD_PASSWORD = "OldPass1!"
    NEW_PASSWORD = "NewPass2!"

    @pytest.fixture
    def known_hash(self, service_moderate: PasswordAuthService) -> str:
        """Bcrypt hash for OLD_PASSWORD."""
        return service_moderate._hash_password(self.OLD_PASSWORD)

    @pytest.fixture
    def mock_user(self, known_hash: str) -> MagicMock:
        """Mock password-authenticated user with must_change_password=True."""
        user = MagicMock()
        user.id = "user-123"
        user.email = "test@example.com"
        user.password_hash = known_hash
        user.auth_method = "password"
        user.must_change_password = True
        return user

    @pytest.fixture
    def mock_db(self, mock_user: MagicMock) -> AsyncMock:
        """AsyncSession that returns mock_user on query."""
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_user
        db.execute.return_value = result
        return db

    @pytest.mark.asyncio
    async def test_success(
        self,
        service_moderate: PasswordAuthService,
        mock_user: MagicMock,
        mock_db: AsyncMock,
    ) -> None:
        """Successful password change updates hash and returns True. (Req 7.1)"""
        old_hash = mock_user.password_hash
        result = await service_moderate.change_password(
            "user-123", self.OLD_PASSWORD, self.NEW_PASSWORD, mock_db,
        )
        assert result is True
        assert mock_user.password_hash != old_hash
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_wrong_current_password(
        self,
        service_moderate: PasswordAuthService,
        mock_db: AsyncMock,
    ) -> None:
        """Wrong current password raises ValueError. (Req 7.2)"""
        with pytest.raises(ValueError, match="Current password is incorrect"):
            await service_moderate.change_password(
                "user-123", "WrongPass9!", self.NEW_PASSWORD, mock_db,
            )

    @pytest.mark.asyncio
    async def test_non_password_auth_method(
        self,
        service_moderate: PasswordAuthService,
        mock_user: MagicMock,
        mock_db: AsyncMock,
    ) -> None:
        """Non-password auth method raises ValueError. (Req 7.3)"""
        mock_user.auth_method = "google"
        with pytest.raises(ValueError, match="does not use password authentication"):
            await service_moderate.change_password(
                "user-123", self.OLD_PASSWORD, self.NEW_PASSWORD, mock_db,
            )

    @pytest.mark.asyncio
    async def test_user_not_found(
        self,
        service_moderate: PasswordAuthService,
    ) -> None:
        """User not found raises LookupError. (Req 7.5)"""
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute.return_value = result

        with pytest.raises(LookupError, match="User not found"):
            await service_moderate.change_password(
                "nonexistent", self.OLD_PASSWORD, self.NEW_PASSWORD, db,
            )

    @pytest.mark.asyncio
    async def test_clears_must_change_password(
        self,
        service_moderate: PasswordAuthService,
        mock_user: MagicMock,
        mock_db: AsyncMock,
    ) -> None:
        """Successful change clears must_change_password flag. (Req 7.8)"""
        assert mock_user.must_change_password is True
        await service_moderate.change_password(
            "user-123", self.OLD_PASSWORD, self.NEW_PASSWORD, mock_db,
        )
        assert mock_user.must_change_password is False

    @pytest.mark.asyncio
    async def test_rejects_same_password(
        self,
        service_moderate: PasswordAuthService,
        mock_db: AsyncMock,
    ) -> None:
        """Same old and new password raises ValueError. (Req 7.6)"""
        with pytest.raises(ValueError, match="New password must be different from current password"):
            await service_moderate.change_password(
                "user-123", self.OLD_PASSWORD, self.OLD_PASSWORD, mock_db,
            )


# ---------------------------------------------------------------------------
# reset_password
# ---------------------------------------------------------------------------


class TestResetPassword:
    """Tests for reset_password().

    Validates: Requirements 7.6, 7.9
    """

    @pytest.fixture
    def mock_user(self, service_moderate: PasswordAuthService) -> MagicMock:
        """Mock password-authenticated user."""
        user = MagicMock()
        user.id = "user-456"
        user.email = "reset-target@example.com"
        user.auth_method = "password"
        user.password_hash = service_moderate._hash_password("OldTempPass1!")
        user.must_change_password = False
        return user

    @pytest.fixture
    def mock_db(self, mock_user: MagicMock) -> AsyncMock:
        """AsyncSession that returns mock_user on query."""
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_user
        db.execute.return_value = result
        return db

    @pytest.mark.asyncio
    async def test_generates_password_and_sets_flag(
        self,
        service_moderate: PasswordAuthService,
        mock_user: MagicMock,
        mock_db: AsyncMock,
    ) -> None:
        """Reset generates temp password, updates hash, sets must_change_password. (Req 7.6)"""
        temp_pw = await service_moderate.reset_password("user-456", mock_db)
        assert isinstance(temp_pw, str)
        assert len(temp_pw) == 16
        assert mock_user.must_change_password is True
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_password_user_rejected(
        self,
        service_moderate: PasswordAuthService,
        mock_user: MagicMock,
        mock_db: AsyncMock,
    ) -> None:
        """Reset for non-password user raises ValueError. (Req 7.9)"""
        mock_user.auth_method = "google"
        with pytest.raises(ValueError, match="does not use password authentication"):
            await service_moderate.reset_password("user-456", mock_db)


class TestAuthenticateUserEmailVerificationGate:
    """SHU-507: login is blocked when email_verified=False AND an email backend
    is configured. Self-hosted deployments running with email_backend=disabled
    keep the legacy is_active gate as the only login check.
    """

    PASSWORD = "ValidPass1!"

    @pytest.fixture
    def known_hash(self, service_moderate: PasswordAuthService) -> str:
        return service_moderate._hash_password(self.PASSWORD)

    @pytest.fixture
    def mock_user(self, known_hash: str) -> MagicMock:
        user = MagicMock()
        user.id = "user-1"
        user.email = "user@example.com"
        user.password_hash = known_hash
        user.auth_method = "password"
        user.is_active = True
        user.email_verified = True
        return user

    @pytest.fixture
    def mock_db(self, mock_user: MagicMock) -> AsyncMock:
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_user
        db.execute.return_value = result
        return db

    def _service_with_backend(self, mock_settings_moderate, backend: str) -> PasswordAuthService:
        mock_settings_moderate.email_backend = backend
        with patch("shu.auth.password_auth.get_settings_instance", return_value=mock_settings_moderate):
            return PasswordAuthService()

    def _patch_effective_backend(self, name: str):
        """Patch the *effective* backend name reported by the factory.

        The login gate now consults the factory's effective backend name
        (post-fallback to disabled when config is missing), not the raw
        setting. Tests must patch the factory function rather than just
        flipping the setting field on the mock.
        """
        return patch(
            "shu.core.email.factory.get_effective_email_backend_name",
            return_value=name,
        )

    @pytest.mark.asyncio
    async def test_unverified_blocked_when_email_backend_configured(
        self, mock_settings_moderate, mock_user: MagicMock, mock_db: AsyncMock
    ) -> None:
        from fastapi import HTTPException

        mock_user.email_verified = False
        service = self._service_with_backend(mock_settings_moderate, "resend")

        with self._patch_effective_backend("resend"), pytest.raises(HTTPException) as exc_info:
            await service.authenticate_user("user@example.com", self.PASSWORD, mock_db)
        assert exc_info.value.status_code == 400
        # Must be the verification message (NOT the inactive message) so the
        # frontend can offer a "resend" CTA on this specific failure.
        assert "verify your email" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_unverified_allowed_when_email_backend_disabled(
        self, mock_settings_moderate, mock_user: MagicMock, mock_db: AsyncMock
    ) -> None:
        # Self-hosted-without-email path: is_active is the only gate; an
        # admin who activated the user implicitly vouches for the email.
        # mock_user is already is_active=True so login proceeds.
        mock_user.email_verified = False
        service = self._service_with_backend(mock_settings_moderate, "disabled")

        with self._patch_effective_backend("disabled"):
            result = await service.authenticate_user("user@example.com", self.PASSWORD, mock_db)
        assert result is mock_user

    @pytest.mark.asyncio
    async def test_unverified_allowed_when_factory_downgrades_to_disabled(
        self, mock_settings_moderate, mock_user: MagicMock, mock_db: AsyncMock
    ) -> None:
        """Codex finding: gate must read the *effective* backend, not the
        raw setting. If SHU_EMAIL_BACKEND=smtp but the factory downgraded
        to disabled (e.g., SMTP host missing), unverified users must still
        be allowed in via the legacy admin-activation gate — otherwise the
        deployment is stuck creating users who can't receive verification
        emails.
        """
        mock_user.email_verified = False
        # Raw setting is "smtp" but the factory's effective backend is "disabled"
        service = self._service_with_backend(mock_settings_moderate, "smtp")

        with self._patch_effective_backend("disabled"):
            result = await service.authenticate_user("user@example.com", self.PASSWORD, mock_db)
        assert result is mock_user

    @pytest.mark.asyncio
    async def test_verified_user_logs_in(
        self, mock_settings_moderate, mock_user: MagicMock, mock_db: AsyncMock
    ) -> None:
        mock_user.email_verified = True
        service = self._service_with_backend(mock_settings_moderate, "resend")

        with self._patch_effective_backend("resend"):
            result = await service.authenticate_user("user@example.com", self.PASSWORD, mock_db)
        assert result is mock_user

    @pytest.mark.asyncio
    async def test_inactive_check_runs_before_verification_check(
        self, mock_settings_moderate, mock_user: MagicMock, mock_db: AsyncMock
    ) -> None:
        """An inactive AND unverified user gets the inactive message — the gate
        order is is_active first, email_verified second. The frontend should
        not show "resend verification" to an admin-deactivated account.
        """
        from fastapi import HTTPException

        mock_user.is_active = False
        mock_user.email_verified = False
        service = self._service_with_backend(mock_settings_moderate, "resend")

        with pytest.raises(HTTPException) as exc_info:
            await service.authenticate_user("user@example.com", self.PASSWORD, mock_db)
        assert "inactive" in exc_info.value.detail.lower()
        assert "verify" not in exc_info.value.detail.lower()


class TestCreateUserVerificationActivationComposition:
    """SHU-507: SHU_AUTO_ACTIVATE_USERS interacts with email-verification.

    When a user self-registers and the email backend is configured:
      - SHU_AUTO_ACTIVATE_USERS=true  → is_active=True, email_verified=False.
        The single remaining gate is verification.
      - SHU_AUTO_ACTIVATE_USERS=false → is_active=False, email_verified=False.
        BOTH gates apply — admin must activate AND user must verify.

    Regression test: an earlier version of the create_user code unilaterally
    set is_active=True for the verification path, which silently bypassed
    SHU_AUTO_ACTIVATE_USERS=false (default) and let users into the system
    without admin approval.
    """

    @pytest.fixture
    def mock_db(self) -> AsyncMock:
        db = AsyncMock()
        # No existing user with this email.
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)
        db.add = MagicMock()
        db.flush = AsyncMock()
        return db

    def _service(self, mock_settings_moderate, *, auto_activate: bool) -> PasswordAuthService:
        mock_settings_moderate.auto_activate_users = auto_activate
        with patch("shu.auth.password_auth.get_settings_instance", return_value=mock_settings_moderate):
            return PasswordAuthService()

    @pytest.mark.asyncio
    async def test_auto_activate_false_keeps_is_active_false(
        self, mock_settings_moderate, mock_db: AsyncMock
    ) -> None:
        service = self._service(mock_settings_moderate, auto_activate=False)
        user = await service.create_user(
            email="new@example.com",
            password="ValidPass1!",
            name="New User",
            db=mock_db,
            requires_email_verification=True,
            flush_only=True,
        )
        # Both gates apply: admin must activate AND user must verify.
        assert user.is_active is False
        assert user.email_verified is False

    @pytest.mark.asyncio
    async def test_auto_activate_true_sets_is_active_true(
        self, mock_settings_moderate, mock_db: AsyncMock
    ) -> None:
        service = self._service(mock_settings_moderate, auto_activate=True)
        user = await service.create_user(
            email="new@example.com",
            password="ValidPass1!",
            name="New User",
            db=mock_db,
            requires_email_verification=True,
            flush_only=True,
        )
        # Operator opted in to trusting verification — only the email
        # gate remains.
        assert user.is_active is True
        assert user.email_verified is False

    @pytest.mark.asyncio
    async def test_force_inactive_overrides_auto_activate_true(
        self, mock_settings_moderate, mock_db: AsyncMock
    ) -> None:
        """SHU-784: soft enforcement at_limit must override auto_activate=true.

        Without the override, an over-limit signup with auto-activate on would
        land active after email verification, making soft enforcement
        indistinguishable from `none`. The caller (api/auth.py register_user)
        passes ``force_inactive=True`` when ``check_user_limit`` returns
        ``soft + at_limit``.
        """
        service = self._service(mock_settings_moderate, auto_activate=True)
        user = await service.create_user(
            email="new@example.com",
            password="ValidPass1!",
            name="New User",
            db=mock_db,
            requires_email_verification=True,
            flush_only=True,
            force_inactive=True,
        )
        # Admin must approve the over-limit user even though operator
        # set SHU_AUTO_ACTIVATE_USERS=true.
        assert user.is_active is False
        assert user.email_verified is False
