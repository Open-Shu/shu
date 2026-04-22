"""Unit tests for Settings field validators."""

import pytest
from pydantic import ValidationError

from shu.core.config import Settings


class TestValidatePasswordPolicy:
    """Tests for Settings.validate_password_policy field validator."""

    def test_moderate_accepted(self) -> None:
        """'moderate' should be accepted and lowercased."""
        settings = Settings(SHU_PASSWORD_POLICY="moderate")
        assert settings.password_policy == "moderate"

    def test_strict_accepted(self) -> None:
        """'strict' should be accepted and lowercased."""
        settings = Settings(SHU_PASSWORD_POLICY="strict")
        assert settings.password_policy == "strict"

    def test_case_insensitive(self) -> None:
        """Uppercase variants should be normalised to lowercase."""
        settings = Settings(SHU_PASSWORD_POLICY="STRICT")
        assert settings.password_policy == "strict"

    def test_invalid_value_rejected(self) -> None:
        """An unrecognised policy value should raise a ValidationError."""
        with pytest.raises(ValidationError, match="Password policy must be one of"):
            Settings(SHU_PASSWORD_POLICY="extreme")


class TestValidateTenantId:
    """Tests for Settings.validate_tenant_id field validator."""

    def test_empty_or_whitespace_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Silent fallthrough to no-prefix in a hosted context would cause
        # cross-tenant key contamination — must fail hard.
        for value in ("", "   "):
            monkeypatch.setenv("SHU_TENANT_ID", value)
            with pytest.raises(ValidationError, match="SHU_TENANT_ID must not be empty or whitespace"):
                Settings()

    def test_value_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHU_TENANT_ID", " tenant-abc ")
        settings = Settings()
        assert settings.tenant_id == "tenant-abc"
