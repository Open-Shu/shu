"""Unit tests for CP provisioning schema validators.

Per project policy "test our code, not the framework": only the bits we wrote
get tests here — the `subscription_status` allowlist, the `effect` Literal
narrowing, and the `extra="forbid"` config we set. Pydantic's StrictStr,
default handling, and bool parsing are not retested.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shu.schemas.cp_provisioning import (
    BillingInput,
    PolicyInput,
    PolicyStatementInput,
    SetUserActiveRequest,
)


def _valid_statement_kwargs() -> dict[str, object]:
    return {"actions": ["kb.read"], "resources": ["kb:*"]}


def _valid_billing_kwargs(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {"subscription_status": "active"}
    defaults.update(overrides)
    return defaults


class TestBillingInputSubscriptionStatus:
    """The custom allowlist enforced by `_validate_seed_status`."""

    @pytest.mark.parametrize("status", ["active", "trialing", "pending"])
    def test_allowed_statuses_pass(self, status: str) -> None:
        billing = BillingInput(**_valid_billing_kwargs(subscription_status=status))
        assert billing.subscription_status == status

    @pytest.mark.parametrize("status", ["canceled", "unpaid", "past_due", "", "ACTIVE"])
    def test_blocked_statuses_raise(self, status: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            BillingInput(**_valid_billing_kwargs(subscription_status=status))
        # Surface the field name + supplied value so test failures point at
        # the right place — guards against the validator silently widening
        # its accepted set later.
        msg = str(exc_info.value)
        assert "subscription_status" in msg
        assert status in msg or repr(status) in msg


class TestPolicyEffectLiteral:
    """The Literal["allow", "deny"] narrowing on `PolicyInput.effect`."""

    @pytest.mark.parametrize("effect", ["allow", "deny"])
    def test_valid_effects_pass(self, effect: str) -> None:
        policy = PolicyInput(
            name="readers",
            effect=effect,
            statements=[PolicyStatementInput(**_valid_statement_kwargs())],
        )
        assert policy.effect == effect

    @pytest.mark.parametrize("effect", ["permit", "ALLOW", "", "block"])
    def test_invalid_effects_raise(self, effect: str) -> None:
        with pytest.raises(ValidationError):
            PolicyInput(
                name="readers",
                effect=effect,
                statements=[PolicyStatementInput(**_valid_statement_kwargs())],
            )


class TestPolicyStatementNonEmptyLists:
    """`min_length=1` constraints we added on actions/resources."""

    def test_empty_actions_raises(self) -> None:
        with pytest.raises(ValidationError):
            PolicyStatementInput(actions=[], resources=["kb:*"])

    def test_empty_resources_raises(self) -> None:
        with pytest.raises(ValidationError):
            PolicyStatementInput(actions=["kb.read"], resources=[])


class TestExtraForbid:
    """`extra="forbid"` config on every CP inbound model."""

    def test_unknown_top_level_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SetUserActiveRequest(
                is_active=True,
                reason="emergency lockout",
                unexpected_field="oops",  # type: ignore[call-arg]
            )
        assert "unexpected_field" in str(exc_info.value)

    def test_unknown_nested_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            BillingInput(
                **_valid_billing_kwargs(stripe_customer_id_typo="cus_x"),  # type: ignore[arg-type]
            )
        assert "stripe_customer_id_typo" in str(exc_info.value)
