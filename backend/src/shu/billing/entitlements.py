"""Tenant-side entitlement contract.

Mirrors `shu-control-plane/src/control_plane/billing/entitlements.py:EntitlementSet`.
Kept duplicated rather than shared via a package because CP and tenant ship
independently; a shared dependency would couple their release cadence.
Keeping the shapes in lock-step is a code-review checklist item — when one
side adds a key, the other must follow before the next billing-state poll
is consumed.

Types only. Enforcement helpers (`assert_entitlement`, `require_entitlement`)
live in `enforcement.py` to avoid a circular import: `cp_client.py` imports
`EntitlementSet` from here, and anything importing the cache transitively
goes through `cp_client.py` — so this module must stay leaf-level.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from shu.core.exceptions import ShuException


class EntitlementSet(BaseModel):
    """Per-tier feature gate set, frozen so resolved values can't mutate.

    Defaults reflect the initial release: `chat` open on every tier, the
    rest dark-shipped behind operator overrides.
    """

    # protected_namespaces=() silences the Pydantic warning about the
    # model_config_management field colliding with the model_ namespace;
    # the field name is part of the wire contract with CP.
    model_config = ConfigDict(frozen=True, protected_namespaces=())

    chat: bool = True
    plugins: bool = False
    experiences: bool = False
    provider_management: bool = False
    model_config_management: bool = False
    mcp_servers: bool = False


class LimitSet(BaseModel):
    """Per-tier integer caps, resolved CP-side from tier baseline + per-tenant overrides.

    Mirrors `shu-control-plane/.../billing/entitlements.py:LimitSet`. Defaults
    are 0 so a cold-start / fallback wire shape fails closed — a tenant whose
    state we don't yet know shouldn't get unbounded quota.
    """

    model_config = ConfigDict(frozen=True)

    document_count_limit: int = 0
    kb_count_limit: int = 0


class EntitlementDeniedError(ShuException):
    """Raised when a tenant's effective entitlement set does not include
    the key required by a backend route.

    Inherits `ShuException` so the generic exception handler in `main.py`
    routes it to a 403 with the standard nested-error body — same shape
    as `SubscriptionInactiveError` / `TrialCapExhaustedError`, so the
    frontend has one error-parsing path.
    """

    def __init__(self, key: str) -> None:
        super().__init__(
            message="Feature not enabled for this tenant.",
            error_code="entitlement_denied",
            status_code=403,
            details={"entitlement": key},
        )
        self.key = key
