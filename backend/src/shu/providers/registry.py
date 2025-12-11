from __future__ import annotations

from typing import Dict

from .base_auth_adapter import BaseAuthAdapter

# Adapters
from .google.auth_adapter import GoogleAuthAdapter  # type: ignore
from .microsoft.auth_adapter import MicrosoftAuthAdapter  # type: ignore


def get_auth_adapter(provider: str, auth_capability) -> BaseAuthAdapter:
    """
    Factory for provider auth adapters.

    The adapter is constructed with the calling AuthCapability to reuse its helpers
    (HTTP, settings, encryption, caches) and to keep state localized.
    """
    prov = (provider or "").strip().lower()
    if prov == "google":
        return GoogleAuthAdapter(auth_capability)
    if prov in ("microsoft", "ms", "m365"):
        return MicrosoftAuthAdapter(auth_capability)
    raise NotImplementedError(f"Provider not supported: {provider}")

