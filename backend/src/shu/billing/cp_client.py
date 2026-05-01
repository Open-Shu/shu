"""HTTP client for tenant→CP billing-state polls.

Signs each request with the same `router_shared_secret` CP uses to verify
inbound webhooks, classifies CP responses into typed exceptions, and
surfaces a single misconfiguration-shaped log on the first 401 (so an
operator who set `SHU_TENANT_ID` or `SHU_ROUTER_SHARED_SECRET` to the
wrong value sees it during deploy verification, not during a payment
failure).

This module owns transport classification only. Stale-while-error policy
and TTL caching live in `state_cache.py` — the consumer (SHU-703
enforcement) talks to the cache, never directly to this client.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import httpx

from shu.billing.router_envelope import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    sign_envelope,
)


@dataclass(frozen=True)
class BillingState:
    openrouter_key_disabled: bool
    payment_failed_at: datetime | None
    payment_grace_days: int


# Cold-start fallback when the cache has never observed a successful
# response. Once a real value lands the cache hands that out instead;
# this constant is only reached in the (cold-start ∧ CP-unreachable) corner.
HEALTHY_DEFAULT = BillingState(
    openrouter_key_disabled=False,
    payment_failed_at=None,
    payment_grace_days=0,
)


class CpClientError(Exception):
    """Base for tenant→CP transport failures."""


class CpUnreachable(CpClientError):
    """Network error / timeout reaching CP."""


class CpAuthFailed(CpClientError):
    """CP returned 401. Likely SHU_TENANT_ID / SHU_ROUTER_SHARED_SECRET mismatch."""


class CpUnexpectedStatus(CpClientError):
    """CP returned a non-2xx, non-401 response."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"CP returned status {status}: {body}")
        self.status = status
        self.body = body


class CpClient:
    """Async client for the CP /billing-state endpoint."""

    def __init__(
        self,
        base_url: str,
        tenant_id: UUID,
        shared_secret: str,
        http_client: httpx.AsyncClient,
        logger: logging.Logger,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._path = f"/api/v1/tenants/{tenant_id}/billing-state"
        self._shared_secret = shared_secret
        self._http_client = http_client
        self._logger = logger
        # First-401-per-process ERROR / subsequent WARNING. Instance-scoped
        # rather than module-global so tests stay isolated and a future
        # multi-tenant tenant process (if it ever existed) wouldn't share state.
        self._auth_failure_seen = False

    async def fetch_billing_state(self) -> BillingState:
        timestamp, signature = sign_envelope(self._shared_secret, "GET", self._path, b"")
        headers = {
            TIMESTAMP_HEADER: str(timestamp),
            SIGNATURE_HEADER: signature,
        }
        try:
            response = await self._http_client.get(f"{self._base_url}{self._path}", headers=headers, timeout=5.0)
        except httpx.RequestError as exc:
            raise CpUnreachable(f"CP unreachable: {exc}") from exc

        if response.status_code == 200:
            return self._parse(response.json())
        if response.status_code == 401:
            self._log_auth_failure()
            raise CpAuthFailed(f"CP rejected signature for tenant poll at {self._path}")
        raise CpUnexpectedStatus(response.status_code, response.text)

    def _log_auth_failure(self) -> None:
        if not self._auth_failure_seen:
            self._auth_failure_seen = True
            self._logger.error(
                "CP returned 401 for billing-state poll — likely "
                "SHU_TENANT_ID or SHU_ROUTER_SHARED_SECRET mismatch with the "
                "control-plane tenant row",
                extra={"path": self._path},
            )
        else:
            self._logger.warning("CP returned 401 for billing-state poll", extra={"path": self._path})

    @staticmethod
    def _parse(payload: dict) -> BillingState:
        raw_failed_at = payload["payment_failed_at"]
        return BillingState(
            openrouter_key_disabled=payload["openrouter_key_disabled"],
            payment_failed_at=(datetime.fromisoformat(raw_failed_at) if raw_failed_at is not None else None),
            payment_grace_days=payload["payment_grace_days"],
        )
