"""Pydantic schemas for the `/admin/cp/*` Control-Plane provisioning endpoints.

Strict types throughout: CP is an external system and silently coercing the
wrong shape is the opposite of what we want at this boundary. `extra="forbid"`
on every inbound model so a contract drift surfaces here rather than as a
silently-dropped field downstream.

Response models are plain BaseModel — we control what we emit, so the same
strictness isn't needed on the outbound side.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
)

# CP must seed with a non-blocking status so the just-created tenant can serve
# chat on the very next request. Rejecting terminal/blocked states at the API
# boundary surfaces a CP-side bug here, not at first chat in
# `enforcement.assert_subscription_active`.
_ALLOWED_SEED_SUBSCRIPTION_STATUSES: frozenset[str] = frozenset({"active", "trialing", "pending"})


class _CpInboundBase(BaseModel):
    """Shared config for all CP→Shu request models."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# POST /admin/cp/tenants
# ---------------------------------------------------------------------------


class BillingInput(_CpInboundBase):
    stripe_customer_id: StrictStr | None = None
    stripe_subscription_id: StrictStr | None = None
    billing_email: StrictStr | None = None
    subscription_status: StrictStr
    current_period_start: AwareDatetime | None = None
    current_period_end: AwareDatetime | None = None
    user_limit_enforcement: Literal["soft", "hard", "none"] = "hard"

    @field_validator("subscription_status")
    @classmethod
    def _validate_seed_status(cls, v: str) -> str:
        if v not in _ALLOWED_SEED_SUBSCRIPTION_STATUSES:
            raise ValueError(
                f"subscription_status {v!r} not allowed at seed time; "
                f"must be one of {sorted(_ALLOWED_SEED_SUBSCRIPTION_STATUSES)}"
            )
        return v


class UserInput(_CpInboundBase):
    email: StrictStr
    name: StrictStr


class CreateTenantRequest(_CpInboundBase):
    tenant_id: StrictStr
    billing: BillingInput
    user: UserInput
    reason: StrictStr = Field(min_length=1)


class CreateTenantResponse(BaseModel):
    tenant_id: str
    user_id: str
    welcome_email_sent: bool
    billing_state_created: bool


# ---------------------------------------------------------------------------
# PUT /admin/cp/tenants/{tenant_id}/model-configs
# ---------------------------------------------------------------------------


class ModelConfigInput(_CpInboundBase):
    name: StrictStr
    provider_name: StrictStr
    model_name: StrictStr
    parameter_overrides: dict[str, Any] | None = None
    prompt_name: StrictStr | None = None
    functionalities: dict[str, Any] | None = None


class SetModelConfigsRequest(_CpInboundBase):
    configs: list[ModelConfigInput]
    side_call_model_config_name: StrictStr | None = None
    profiling_model_config_name: StrictStr | None = None
    reason: StrictStr = Field(min_length=1)


class SetModelConfigsResponse(BaseModel):
    config_ids_by_name: dict[str, str]
    side_call_model_config_id: str | None
    profiling_model_config_id: str | None


# ---------------------------------------------------------------------------
# PUT /admin/cp/tenants/{tenant_id}/policies
# ---------------------------------------------------------------------------


class PolicyStatementInput(_CpInboundBase):
    # Mirrors `AccessPolicyStatement` columns: each row is a list of actions
    # and a list of resources stored as JSON. `effect` lives at the policy
    # level (one column on `access_policies`), not here.
    actions: list[StrictStr] = Field(min_length=1)
    resources: list[StrictStr] = Field(min_length=1)


class PolicyInput(_CpInboundBase):
    name: StrictStr
    effect: Literal["allow", "deny"]
    description: StrictStr | None = None
    statements: list[PolicyStatementInput]


class SetPoliciesRequest(_CpInboundBase):
    # Wipe-and-replace semantics: the tenant's existing policy set is
    # deleted (cascading through bindings and statements) and replaced with
    # `policies`. CP is the source of truth for the policy set; partial
    # diffs aren't supported.
    policies: list[PolicyInput]
    bind_to_all_users: StrictBool = True
    reason: StrictStr = Field(min_length=1)


class SetPoliciesResponse(BaseModel):
    policy_ids_by_name: dict[str, str]
    bindings_created: int


# ---------------------------------------------------------------------------
# PUT /admin/cp/tenants/{tenant_id}/prompt
# ---------------------------------------------------------------------------


class PromptInput(_CpInboundBase):
    name: StrictStr
    content: StrictStr
    entity_type: StrictStr | None = None


class SetPromptRequest(_CpInboundBase):
    prompt: PromptInput
    reason: StrictStr = Field(min_length=1)


class SetPromptResponse(BaseModel):
    prompt_id: str


# ---------------------------------------------------------------------------
# PATCH /admin/cp/tenants/{tenant_id}/user/active
# ---------------------------------------------------------------------------


class SetUserActiveRequest(_CpInboundBase):
    is_active: StrictBool
    reason: StrictStr = Field(min_length=1)


class SetUserActiveResponse(BaseModel):
    user_id: str
    email: str
    is_active: bool
