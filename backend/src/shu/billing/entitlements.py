"""Tenant-side entitlement contract.

Mirrors `shu-control-plane/src/control_plane/billing/entitlements.py:EntitlementSet`.
Kept duplicated rather than shared via a package because CP and tenant ship
independently; a shared dependency would couple their release cadence.
Keeping the shapes in lock-step is a code-review checklist item — when one
side adds a key, the other must follow before the next billing-state poll
is consumed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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
