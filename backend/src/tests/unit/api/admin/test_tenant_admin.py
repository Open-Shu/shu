"""Unit tests for the CP-driven `/admin/cp/*` route handlers.

Per project policy ("Unit tests call functions directly with mocked deps"),
these tests invoke the handler coroutines as plain functions with mocked
services. No ASGI transport, no live HMAC envelope verification — the
HMAC dep is covered by `billing/router_envelope` tests already, and
mounting the same dep on a new router doesn't change its behaviour.

The handlers must be pure delegation: parse → call service method →
return. These tests assert exactly that.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.api.admin.tenant_admin import (
    cp_create_tenant,
    cp_set_model_configs,
    cp_set_policies,
    cp_set_prompt,
    cp_set_user_active,
)
from shu.schemas.cp_provisioning import (
    BillingInput,
    CreateTenantRequest,
    CreateTenantResponse,
    ModelConfigInput,
    PolicyInput,
    PolicyStatementInput,
    PromptInput,
    SetModelConfigsRequest,
    SetModelConfigsResponse,
    SetPoliciesRequest,
    SetPoliciesResponse,
    SetPromptRequest,
    SetPromptResponse,
    SetUserActiveRequest,
    SetUserActiveResponse,
    UserInput,
)


@pytest.mark.asyncio
async def test_cp_create_tenant_delegates() -> None:
    payload = CreateTenantRequest(
        tenant_id="t-1",
        billing=BillingInput(),
        user=UserInput(email="u@example.com", name="Alice"),
        reason="seed",
    )
    expected = CreateTenantResponse(
        tenant_id="t-1",
        user_id="user-1",
        welcome_email_sent=True,
        billing_state_created=True,
    )
    admin_svc = MagicMock()
    admin_svc.create_tenant = AsyncMock(return_value=expected)

    result = await cp_create_tenant(payload, admin_svc=admin_svc)

    assert result is expected
    admin_svc.create_tenant.assert_awaited_once_with(payload, "seed")


@pytest.mark.asyncio
async def test_cp_set_model_configs_delegates() -> None:
    payload = SetModelConfigsRequest(
        configs=[
            ModelConfigInput(name="default", provider_name="openai", model_name="gpt-4")
        ],
        reason="set MCs",
    )
    expected = SetModelConfigsResponse(
        config_ids_by_name={"default": "mc-1"},
        side_call_model_config_id=None,
        profiling_model_config_id=None,
    )
    mc_svc = MagicMock()
    mc_svc.cp_upsert_by_name = AsyncMock(return_value=expected)

    result = await cp_set_model_configs("t-1", payload, mc_svc=mc_svc)

    assert result is expected
    mc_svc.cp_upsert_by_name.assert_awaited_once_with("t-1", payload, "set MCs")


@pytest.mark.asyncio
async def test_cp_set_policies_delegates() -> None:
    payload = SetPoliciesRequest(
        policies=[
            PolicyInput(
                name="readers",
                effect="allow",
                statements=[
                    PolicyStatementInput(actions=["kb.read"], resources=["kb:*"])
                ],
            )
        ],
        reason="set policies",
    )
    expected = SetPoliciesResponse(
        policy_ids_by_name={"readers": "pol-1"},
        bindings_created=1,
    )
    policy_svc = MagicMock()
    policy_svc.cp_replace_and_bind = AsyncMock(return_value=expected)

    result = await cp_set_policies("t-1", payload, policy_svc=policy_svc)

    assert result is expected
    policy_svc.cp_replace_and_bind.assert_awaited_once_with("t-1", payload, "set policies")


@pytest.mark.asyncio
async def test_cp_set_prompt_delegates() -> None:
    payload = SetPromptRequest(
        prompt=PromptInput(name="canonical", content="hello"),
        reason="set prompt",
    )
    expected = SetPromptResponse(prompt_id="prompt-1")
    prompt_svc = MagicMock()
    prompt_svc.cp_upsert_by_name = AsyncMock(return_value=expected)

    result = await cp_set_prompt("t-1", payload, prompt_svc=prompt_svc)

    assert result is expected
    prompt_svc.cp_upsert_by_name.assert_awaited_once_with("t-1", payload, "set prompt")


@pytest.mark.asyncio
async def test_cp_set_user_active_delegates() -> None:
    payload = SetUserActiveRequest(is_active=False, reason="TOS")
    expected = SetUserActiveResponse(
        user_id="u-1", email="u@example.com", is_active=False
    )
    user_svc = MagicMock()
    user_svc.cp_set_user_active = AsyncMock(return_value=expected)
    admin_svc = MagicMock()
    audit = MagicMock()

    result = await cp_set_user_active(
        "t-1",
        payload,
        user_svc=user_svc,
        admin_svc=admin_svc,
        audit=audit,
    )

    assert result is expected
    user_svc.cp_set_user_active.assert_awaited_once_with(
        "t-1",
        False,
        "TOS",
        tenant_admin_svc=admin_svc,
        audit_logger=audit,
    )


@pytest.mark.asyncio
async def test_handler_propagates_service_exceptions() -> None:
    """A 404/409 raised by the service must bubble — handlers do no catching."""
    payload = SetPromptRequest(
        prompt=PromptInput(name="x", content="y"),
        reason="r",
    )
    from shu.core.exceptions import NotFoundError

    prompt_svc = MagicMock()
    prompt_svc.cp_upsert_by_name = AsyncMock(side_effect=NotFoundError("boom"))

    with pytest.raises(NotFoundError, match="boom"):
        await cp_set_prompt("t-1", payload, prompt_svc=prompt_svc)
