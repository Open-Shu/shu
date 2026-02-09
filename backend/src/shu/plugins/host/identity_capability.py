from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IdentityCapability:
    """Identity information for the current user.

    Security: This dataclass is frozen (immutable) to prevent plugins from
    mutating user_id or user_email to impersonate other users.
    """

    user_id: str
    user_email: str | None
    providers: dict[str, list[dict[str, Any]]] | None = None

    def get_current_user_identity(self) -> dict[str, Any]:
        return {"user_id": self.user_id, "email": self.user_email}

    def get_primary_email(self, provider: str) -> str | None:
        try:
            prov = (self.providers or {}).get(provider) or []
            if prov:
                return (prov[0] or {}).get("primary_email")
        except Exception:
            return None
        return None
