"""Unit tests for Settings field validators."""

import pytest
from pydantic import ValidationError

from shu.core.config import DeploymentMode, Settings

# A constant UUID that satisfies the silo-mode UUID-shape check. Reused across
# the deployment-mode/tenant-id matrix tests below.
VALID_TENANT_UUID = "550e8400-e29b-41d4-a716-446655440000"


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
        # Pair with silo mode so the model-level "tenant_id required" check
        # doesn't preempt the field-level empty/whitespace check.
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "silo")
        for value in ("", "   "):
            monkeypatch.setenv("SHU_TENANT_ID", value)
            with pytest.raises(ValidationError, match="SHU_TENANT_ID must not be empty or whitespace"):
                Settings(_env_file=None)

    def test_value_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Pair with silo mode so the model-level "tenant_id must be unset"
        # check (self_hosted/multi_tenant default) doesn't preempt this test.
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "silo")
        monkeypatch.setenv("SHU_TENANT_ID", f" {VALID_TENANT_UUID} ")
        settings = Settings(_env_file=None)
        assert settings.tenant_id == VALID_TENANT_UUID

    def test_non_uuid_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Non-UUID values must fail at the field validator so non-shaped IDs
        # never reach SQL/Redis where they'd corrupt tenant isolation.
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "silo")
        monkeypatch.setenv("SHU_TENANT_ID", "not-a-uuid")
        with pytest.raises(ValidationError, match="SHU_TENANT_ID must be a valid UUID"):
            Settings(_env_file=None)

    def test_whitespace_padded_uuid_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Stripping happens before UUID parsing, so a padded but otherwise valid
        # UUID survives the validator with the surrounding whitespace removed.
        padded = "  a9c8d3e2-1f4b-4c7e-9a0d-5b6e7f8a9b0c  "
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "silo")
        monkeypatch.setenv("SHU_TENANT_ID", padded)
        settings = Settings(_env_file=None)
        assert settings.tenant_id == "a9c8d3e2-1f4b-4c7e-9a0d-5b6e7f8a9b0c"

    def test_malformed_uuid_too_few_hex_chars_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A UUID missing hex characters in one group is structurally invalid;
        # the validator must reject it rather than accept a truncated identifier.
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "silo")
        monkeypatch.setenv("SHU_TENANT_ID", "550e8400-e29b-41d4-a716-44665544")
        with pytest.raises(ValidationError, match="SHU_TENANT_ID must be a valid UUID"):
            Settings(_env_file=None)

    def test_uppercase_uuid_is_normalized_to_lowercase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SECURITY DEFINER lookups compare tenant_id via exact text
        equality, so the validator canonicalizes the operator-typed string.
        ``str(uuid.UUID(...))`` produces hyphenated-lowercase.
        """
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "silo")
        monkeypatch.setenv("SHU_TENANT_ID", "A9C8D3E2-1F4B-4C7E-9A0D-5B6E7F8A9B0C")
        settings = Settings(_env_file=None)
        assert settings.tenant_id == "a9c8d3e2-1f4b-4c7e-9a0d-5b6e7f8a9b0c"

    def test_braced_uuid_is_normalized_to_canonical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Python's UUID accepts ``{...}`` and urn:uuid:... wrappers; normalize
        to the canonical hyphenated form so downstream comparisons see one
        shape regardless of how an operator typed the env var.
        """
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "silo")
        monkeypatch.setenv("SHU_TENANT_ID", "{a9c8d3e2-1f4b-4c7e-9a0d-5b6e7f8a9b0c}")
        settings = Settings(_env_file=None)
        assert settings.tenant_id == "a9c8d3e2-1f4b-4c7e-9a0d-5b6e7f8a9b0c"


class TestValidateDeploymentModeTenantCombo:
    """Tests for the cross-field model validator gating SHU_TENANT_ID on SHU_DEPLOYMENT_MODE."""

    def test_self_hosted_without_tenant_id_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "self_hosted")
        monkeypatch.delenv("SHU_TENANT_ID", raising=False)
        settings = Settings(_env_file=None)
        assert settings.deployment_mode == DeploymentMode.SELF_HOSTED
        assert settings.tenant_id is None

    def test_self_hosted_with_tenant_id_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "self_hosted")
        monkeypatch.setenv("SHU_TENANT_ID", VALID_TENANT_UUID)
        with pytest.raises(
            ValidationError,
            match="SHU_TENANT_ID must not be set when SHU_DEPLOYMENT_MODE is self_hosted",
        ):
            Settings(_env_file=None)

    def test_silo_with_uuid_tenant_id_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "silo")
        monkeypatch.setenv("SHU_TENANT_ID", VALID_TENANT_UUID)
        settings = Settings(_env_file=None)
        assert settings.deployment_mode == DeploymentMode.SILO
        assert settings.tenant_id == VALID_TENANT_UUID

    def test_silo_without_tenant_id_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "silo")
        monkeypatch.delenv("SHU_TENANT_ID", raising=False)
        with pytest.raises(
            ValidationError,
            match="SHU_TENANT_ID is required when SHU_DEPLOYMENT_MODE is silo",
        ):
            Settings(_env_file=None)

    def test_silo_with_non_uuid_tenant_id_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Field validator runs before this model validator and is the authoritative
        # UUID-shape check; the message here comes from validate_tenant_id, not from
        # the silo-specific branch of validate_deployment_mode_tenant_combo (which
        # remains as defense-in-depth).
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "silo")
        monkeypatch.setenv("SHU_TENANT_ID", "not-a-uuid")
        with pytest.raises(ValidationError, match="SHU_TENANT_ID must be a valid UUID"):
            Settings(_env_file=None)

    def test_multi_tenant_without_tenant_id_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "multi_tenant")
        monkeypatch.delenv("SHU_TENANT_ID", raising=False)
        settings = Settings(_env_file=None)
        assert settings.deployment_mode == DeploymentMode.MULTI_TENANT
        assert settings.tenant_id is None

    def test_multi_tenant_with_tenant_id_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHU_DEPLOYMENT_MODE", "multi_tenant")
        monkeypatch.setenv("SHU_TENANT_ID", VALID_TENANT_UUID)
        with pytest.raises(
            ValidationError,
            match="SHU_TENANT_ID must not be set when SHU_DEPLOYMENT_MODE is multi_tenant",
        ):
            Settings(_env_file=None)
