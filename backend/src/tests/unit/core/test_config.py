"""Unit tests for Settings field validators.

Validates the password_policy validator added for the password-change feature.
"""

import pytest
from pydantic import ValidationError

from shu.core.config import Settings


class TestValidatePasswordPolicy:
    """Tests for Settings.validate_password_policy field validator."""

    def test_moderate_accepted(self) -> None:
        """'moderate' should be accepted and lowercased."""
        settings = Settings(password_policy="moderate")
        assert settings.password_policy == "moderate"

    def test_strict_accepted(self) -> None:
        """'strict' should be accepted and lowercased."""
        settings = Settings(password_policy="strict")
        assert settings.password_policy == "strict"

    def test_case_insensitive(self) -> None:
        """Uppercase variants should be normalised to lowercase."""
        settings = Settings(password_policy="STRICT")
        assert settings.password_policy == "strict"

    def test_invalid_value_rejected(self) -> None:
        """An unrecognised policy value should raise a ValidationError."""
        with pytest.raises(ValidationError, match="Password policy must be one of"):
            Settings(password_policy="extreme")
