"""Access Policy Schemas for Policy-Based Access Control (PBAC).

This module contains Pydantic schemas for the PBAC engine including
policy request/response models with nested bindings and statements,
and access evaluation responses.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ============================================================================
# Input Schemas (nested within request bodies, reused in responses)
# ============================================================================


class BindingInput(BaseModel):
    """A policy binding that associates an actor with a policy.

    Each binding links a policy to a specific actor (user or group) by type and ID.
    """

    actor_type: Literal["user", "group"] = Field(..., description="Type of actor: 'user' or 'group'")
    actor_id: str = Field(..., description="ID of the actor (user ID or group ID)")

    class Config:
        """Configure Pydantic to work with ORM objects."""

        from_attributes = True


class StatementInput(BaseModel):
    """A policy statement defining actions and resources.

    Each statement specifies which actions are allowed/denied on which resources.
    """

    actions: list[str] = Field(
        ...,
        min_length=1,
        description="List of actions (e.g., 'experience.read', 'plugin.execute')",
    )
    resources: list[str] = Field(
        ...,
        min_length=1,
        description="List of resources (e.g., 'experience:*', 'plugin:shu_gmail_*')",
    )

    class Config:
        """Configure Pydantic to work with ORM objects."""

        from_attributes = True


# ============================================================================
# Request Schema (used for both create and update — full document every time)
# ============================================================================


class PolicyInput(BaseModel):
    """Schema for creating or updating an access policy.

    Always sends the complete policy document. Used for both POST and PUT.
    """

    name: str = Field(..., min_length=1, max_length=255, description="Policy name (unique)")
    description: str | None = Field(None, description="Policy description")
    effect: Literal["allow", "deny"] = Field(..., description="Policy effect: 'allow' or 'deny'")
    is_active: bool = Field(True, description="Whether the policy is active")
    bindings: list[BindingInput] = Field(default_factory=list, description="Actor bindings for this policy")
    statements: list[StatementInput] = Field(
        ..., min_length=1, description="Action/resource statements (at least one required)"
    )


# ============================================================================
# Response Schemas
# ============================================================================


class PolicyResponse(BaseModel):
    """Response schema for a policy with nested bindings and statements."""

    id: str = Field(..., description="Policy ID")
    name: str = Field(..., description="Policy name")
    description: str | None = Field(None, description="Policy description")
    effect: Literal["allow", "deny"] = Field(..., description="Policy effect: 'allow' or 'deny'")
    is_active: bool = Field(..., description="Whether the policy is active")
    created_by: str = Field(..., description="ID of user who created the policy")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    bindings: list[BindingInput] = Field(default_factory=list, description="Actor bindings for this policy")
    statements: list[StatementInput] = Field(
        default_factory=list, description="Action/resource statements for this policy"
    )

    class Config:
        """Configure Pydantic to work with ORM objects."""

        from_attributes = True


class PolicyListResponse(BaseModel):
    """Paginated response schema for listing policies."""

    items: list[PolicyResponse] = Field(..., description="List of policy documents")
    total: int = Field(..., description="Total number of policies matching the query")
    offset: int = Field(..., description="Current offset")
    limit: int = Field(..., description="Page size limit")


# ============================================================================
# Access Evaluation Response Schemas
# ============================================================================


class AccessCheckResponse(BaseModel):
    """Response schema for access check (dry-run policy evaluation).

    Returns the evaluation decision along with matching policies and reasoning.
    """

    decision: Literal["allow", "deny"] = Field(..., description="Evaluation result: 'allow' or 'deny'")
    matching_policies: list[str] = Field(..., description="IDs of policies that matched the request")
    reason: str = Field(..., description="Human-readable explanation of the decision")


class EffectivePoliciesResponse(BaseModel):
    """Response schema for effective policies resolved for a user.

    Includes all policies that apply to a user, resolved through
    direct user bindings and group memberships.
    """

    user_id: str = Field(..., description="User ID the policies are resolved for")
    policies: list[PolicyResponse] = Field(..., description="All effective policies for the user")


class PolicyActionOption(BaseModel):
    """A known action that can be used in policy statements."""

    value: str = Field(..., description="Action string (e.g., 'experience.read')")
    label: str = Field(..., description="Human-readable description")


class PolicyActionsResponse(BaseModel):
    """Available actions for building policy statements."""

    actions: list[PolicyActionOption] = Field(..., description="Known actions")
