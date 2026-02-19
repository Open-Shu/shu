"""Unit tests for PasswordAuthService password validation and generation.

Validates: Requirements 7.4, 7.7, 7.10
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.auth.password_auth import PasswordAuthService


@pytest.fixture
def mock_settings_moderate():
    """Settings mock with moderate password policy."""
    settings = MagicMock()
    settings.password_policy = "moderate"
    settings.password_min_length = 8
    return settings


@pytest.fixture
def mock_settings_strict():
    """Settings mock with strict password policy."""
    settings = MagicMock()
    settings.password_policy = "strict"
    settings.password_min_length = 8
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
        with patch("shu.auth.password_auth.get_settings_instance", return_value=settings):
            svc = PasswordAuthService()

        errors = svc.validate_password("Abcdefg1")  # 8 chars, below 12
        assert any("at least 12 characters" in e for e in errors)

    def test_custom_min_length_passes(self) -> None:
        """A password meeting the custom min length should pass."""
        settings = MagicMock()
        settings.password_policy = "moderate"
        settings.password_min_length = 12
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
        special = PasswordAuthService.SPECIAL_CHARS
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
    def mock_user(self) -> MagicMock:
        """Mock password-authenticated user."""
        user = MagicMock()
        user.id = "user-456"
        user.email = "reset-target@example.com"
        user.auth_method = "password"
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
