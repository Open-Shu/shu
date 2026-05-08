"""HTTP client for tenant→CP billing-state polls.

Signs each request with the same `router_shared_secret` CP uses to verify
inbound webhooks, classifies CP responses into typed exceptions, and
surfaces a single misconfiguration-shaped log on the first 401 (so an
operator who set `SHU_TENANT_ID` or `SHU_ROUTER_SHARED_SECRET` to the
wrong value sees it during deploy verification, not during a payment
failure).

This module owns transport classification only. Stale-while-error policy
and TTL caching live in `billing_state_cache.py` — the consumer (SHU-703
enforcement) talks to the cache, never directly to this client.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

import httpx
from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from pydantic.dataclasses import dataclass

from shu.billing.router_envelope import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    sign_envelope,
)


@dataclass(frozen=True, config=ConfigDict(extra="ignore"))
class BillingState:
    """Frozen CP billing-state record — also the wire-validation schema.

    The strict field types are load-bearing: a CP version-skew that sends
    `"false"` (string) for openrouter_key_disabled would otherwise flow
    through as truthy and lock healthy users out of OCR. AwareDatetime +
    the validator below reject Unix timestamps and naive datetimes —
    downstream tz-aware arithmetic would crash on naive values.
    """

    openrouter_key_disabled: StrictBool
    payment_failed_at: AwareDatetime | None
    payment_grace_days: Annotated[StrictInt, Field(ge=0)]

    @field_validator("payment_failed_at", mode="before")
    @classmethod
    def _reject_unix_timestamp(cls, v: object) -> object:
        # AwareDatetime in lax mode coerces Unix timestamps (int/float) to
        # UTC datetimes — that masks a CP wire-format drift. Strings (parsed
        # by pydantic) and datetime instances (direct construction) pass
        # through. bool is a subclass of int, but AwareDatetime rejects it
        # downstream so no special-casing here.
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            raise TypeError(f"payment_failed_at must be ISO string or datetime, not {type(v).__name__}")
        return v

    @property
    def grace_deadline(self) -> datetime | None:
        if self.payment_failed_at is None:
            return None
        return self.payment_failed_at + timedelta(days=self.payment_grace_days)


_BILLING_STATE_ADAPTER = TypeAdapter(BillingState)


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


class CpMalformedResponse(CpClientError):
    """CP returned 2xx with a body that does not match the expected schema.

    Surfacing this as a CpClientError keeps the cache's stale-while-error
    fail-open path active when CP misbehaves at the application layer
    (corrupted JSON, missing fields, type drift) rather than letting the
    raw parse exception escape to consumers.
    """


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

        if response.status_code == 401:
            self._log_auth_failure()
            raise CpAuthFailed(f"CP rejected signature for tenant poll at {self._path}")
        if response.status_code != 200:
            raise CpUnexpectedStatus(response.status_code, response.text)

        try:
            return self._parse(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise CpMalformedResponse(f"CP returned a malformed billing-state body: {exc}") from exc

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
    def _parse(payload: Any) -> BillingState:
        return _BILLING_STATE_ADAPTER.validate_python(payload)
