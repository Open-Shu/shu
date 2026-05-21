"""Unit tests for CP provisioning schema validators.

Per project policy "test our code, not the framework": only the bits we wrote
get tests here — the `effect` Literal narrowing, the `min_length=1` constraints
on policy statements, and the `extra="forbid"` config we set. Pydantic's
StrictStr, default handling, and bool parsing are not retested.
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
        # `subscription_status` was removed in the SHU-774 wake — sending it
        # should now be rejected by `extra="forbid"` rather than silently
        # accepted-then-dropped. This pins that contract.
        with pytest.raises(ValidationError) as exc_info:
            BillingInput(
                subscription_status="active",  # type: ignore[call-arg]
            )
        assert "subscription_status" in str(exc_info.value)
