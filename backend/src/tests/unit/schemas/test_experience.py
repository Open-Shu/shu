"""Unit tests for auth_override schema validation on ExperienceStep models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shu.schemas.experience import ExperienceStepBase, ExperienceStepUpdate


def _make_step_base(**overrides) -> dict:
    """Build minimal valid kwargs for ExperienceStepBase."""
    defaults = {
        "step_key": "emails",
        "order": 0,
    }
    defaults.update(overrides)
    return defaults


class TestAuthOverrideOnExperienceStepBase:
    """Validate auth_override behaviour on ExperienceStepBase."""

    def test_valid_domain_delegate_with_running_user(self):
        """A well-formed DWD config with subject_source=running_user is accepted."""
        auth = {
            "provider": "google",
            "mode": "domain_delegate",
            "subject_source": "running_user",
        }
        step = ExperienceStepBase(**_make_step_base(auth_override=auth))
        assert step.auth_override == auth

    def test_valid_domain_delegate_with_explicit_subject(self):
        """A well-formed DWD config with an explicit subject is accepted."""
        auth = {
            "provider": "google",
            "mode": "domain_delegate",
            "subject_source": "explicit",
            "subject": "alice@example.com",
        }
        step = ExperienceStepBase(**_make_step_base(auth_override=auth))
        assert step.auth_override == auth

    def test_user_mode_normalized_to_none(self):
        """mode='user' is the default behaviour; the validator normalizes it to None."""
        auth = {
            "provider": "google",
            "mode": "user",
        }
        step = ExperienceStepBase(**_make_step_base(auth_override=auth))
        assert step.auth_override is None

    def test_none_auth_override_passes(self):
        """None (or omitted) auth_override keeps default user-mode OAuth."""
        step = ExperienceStepBase(**_make_step_base(auth_override=None))
        assert step.auth_override is None

    def test_omitted_auth_override_passes(self):
        """Omitting auth_override entirely defaults to None."""
        step = ExperienceStepBase(**_make_step_base())
        assert step.auth_override is None

    def test_invalid_mode_rejected(self):
        """An unrecognized mode value is rejected with a validation error."""
        auth = {
            "provider": "google",
            "mode": "bogus_mode",
        }
        with pytest.raises(ValidationError, match="auth_override.mode must be one of"):
            ExperienceStepBase(**_make_step_base(auth_override=auth))

    def test_missing_subject_source_for_domain_delegate_rejected(self):
        """domain_delegate without subject_source is rejected."""
        auth = {
            "provider": "google",
            "mode": "domain_delegate",
        }
        with pytest.raises(ValidationError, match="auth_override.subject_source is required"):
            ExperienceStepBase(**_make_step_base(auth_override=auth))

    def test_invalid_subject_source_for_domain_delegate_rejected(self):
        """domain_delegate with an unrecognized subject_source is rejected."""
        auth = {
            "provider": "google",
            "mode": "domain_delegate",
            "subject_source": "unknown",
        }
        with pytest.raises(ValidationError, match="auth_override.subject_source is required"):
            ExperienceStepBase(**_make_step_base(auth_override=auth))

    def test_missing_subject_for_explicit_source_rejected(self):
        """subject_source='explicit' without a subject string is rejected."""
        auth = {
            "provider": "google",
            "mode": "domain_delegate",
            "subject_source": "explicit",
        }
        with pytest.raises(ValidationError, match="auth_override.subject is required"):
            ExperienceStepBase(**_make_step_base(auth_override=auth))

    def test_empty_subject_for_explicit_source_rejected(self):
        """subject_source='explicit' with a blank subject is rejected."""
        auth = {
            "provider": "google",
            "mode": "domain_delegate",
            "subject_source": "explicit",
            "subject": "   ",
        }
        with pytest.raises(ValidationError, match="auth_override.subject is required"):
            ExperienceStepBase(**_make_step_base(auth_override=auth))

    def test_missing_provider_rejected(self):
        """auth_override without a provider is rejected."""
        auth = {
            "mode": "domain_delegate",
            "subject_source": "running_user",
        }
        with pytest.raises(ValidationError, match="auth_override.provider is required"):
            ExperienceStepBase(**_make_step_base(auth_override=auth))

    def test_empty_provider_rejected(self):
        """auth_override with an empty provider is rejected."""
        auth = {
            "provider": "  ",
            "mode": "domain_delegate",
            "subject_source": "running_user",
        }
        with pytest.raises(ValidationError, match="auth_override.provider is required"):
            ExperienceStepBase(**_make_step_base(auth_override=auth))


class TestAuthOverrideOnExperienceStepUpdate:
    """Ensure the same auth_override validation applies to ExperienceStepUpdate."""

    def test_valid_domain_delegate_accepted(self):
        """A valid DWD config is accepted on the update schema."""
        auth = {
            "provider": "google",
            "mode": "domain_delegate",
            "subject_source": "running_user",
        }
        step = ExperienceStepUpdate(auth_override=auth)
        assert step.auth_override == auth

    def test_user_mode_normalized_to_none(self):
        """mode='user' is normalized to None on the update schema."""
        auth = {
            "provider": "google",
            "mode": "user",
        }
        step = ExperienceStepUpdate(auth_override=auth)
        assert step.auth_override is None

    def test_none_auth_override_passes(self):
        """None auth_override passes on the update schema (backward compat)."""
        step = ExperienceStepUpdate(auth_override=None)
        assert step.auth_override is None

    def test_invalid_mode_rejected(self):
        """An invalid mode is rejected on the update schema."""
        auth = {
            "provider": "google",
            "mode": "bad_mode",
        }
        with pytest.raises(ValidationError, match="auth_override.mode must be one of"):
            ExperienceStepUpdate(auth_override=auth)

    def test_missing_subject_for_explicit_source_rejected(self):
        """Missing subject with explicit source is rejected on the update schema."""
        auth = {
            "provider": "google",
            "mode": "domain_delegate",
            "subject_source": "explicit",
        }
        with pytest.raises(ValidationError, match="auth_override.subject is required"):
            ExperienceStepUpdate(auth_override=auth)
