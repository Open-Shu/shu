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
from decimal import Decimal
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

from shu.billing.entitlements import EntitlementSet
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

    `extra="ignore"` is intentional: CP can add new fields to the
    billing-state response in future releases without forcing every
    deployed tenant to re-deploy first. Removing or renaming a field is
    still a coordinated change.
    """

    openrouter_key_disabled: StrictBool
    payment_failed_at: AwareDatetime | None
    payment_grace_days: Annotated[StrictInt, Field(ge=0)]
    entitlements: EntitlementSet
    is_trial: StrictBool
    trial_deadline: AwareDatetime | None
    total_grant_amount: Annotated[Decimal, Field(ge=0)]
    # None during trial: CP returns `None` as the wire signal "compute
    # locally" because Stripe doesn't bill metered usage during `trialing`,
    # so its cached grant balance is useless for display. The tenant router
    # fills this in from local `LLMUsage` before sending to the frontend.
    # Design Component 3a; pairs with the router fallback path.
    remaining_grant_amount: Annotated[Decimal | None, Field(ge=0)]
    seat_price_usd: Annotated[Decimal, Field(ge=0)]
    # Customer-billed markup applied to raw provider cost. Today CP does not
    # send this — the tenant attaches it post-fetch inside `BillingStateCache`
    # from the metered Price's `unit_amount_decimal`. Wire-shaped this way
    # (Decimal | None, default None) so when CP starts shipping it the tenant
    # picks it up automatically — `extra="ignore"` covers the rollout window.
    # `None` is the "compute / default locally" signal; see `resolve_markup`.
    usage_markup_multiplier: Annotated[Decimal | None, Field(gt=0)] = None

    @field_validator("payment_failed_at", "trial_deadline", mode="before")
    @classmethod
    def _reject_unix_timestamp(cls, v: object) -> object:
        # AwareDatetime in lax mode coerces Unix timestamps (int/float) to
        # UTC datetimes — that masks a CP wire-format drift. Strings (parsed
        # by pydantic) and datetime instances (direct construction) pass
        # through. bool is a subclass of int, but AwareDatetime rejects it
        # downstream so no special-casing here.
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            # Generic message because this validator now applies to both
            # payment_failed_at and trial_deadline.
            raise TypeError(f"datetime field must be ISO string or datetime, not {type(v).__name__}")
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
#
# Posture asymmetry:
#   - OR-key path stays fail-open (`openrouter_key_disabled=False`) so OCR
#     and embeddings keep working during a CP outage.
#   - Trial-cap path fails CLOSED (`is_trial=True` with zero grant) so a
#     trial tenant whose process restarts during a CP outage cannot rack
#     up unbounded LLM costs while we wait for CP to come back. The cost
#     of this asymmetry is that a standard tenant caught in the same
#     window also sees chat blocked with a misleading "trial exhausted"
#     message — bounded by the outage duration, and judged a much smaller
#     exposure than uncapped trial spend across N tenants.
HEALTHY_DEFAULT = BillingState(
    openrouter_key_disabled=False,
    payment_failed_at=None,
    payment_grace_days=0,
    entitlements=EntitlementSet(),
    is_trial=True,
    trial_deadline=None,
    total_grant_amount=Decimal(0),
    # None matches the trial wire shape: tenant router computes locally
    # from period usage. With `total_grant_amount=0`, the local
    # computation returns 0 — consistent with the fail-closed posture.
    remaining_grant_amount=None,
    seat_price_usd=Decimal(0),
    # Cold-start carries no Stripe-derived markup; consumers fall back to
    # `BillingSettings.usage_markup_multiplier_default` via `resolve_markup`.
    usage_markup_multiplier=None,
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


class CpNoActiveTrial(CpClientError):
    """CP returned 409 to a cancel-trial POST: subscription is not trialing.

    Typed (rather than collapsed into `CpUnexpectedStatus`) so the admin
    router can re-emit a 409 to the frontend instead of a generic 502 —
    "no active trial to cancel" is a useful client-facing distinction.
    """

    def __init__(self, status_code: int = 409) -> None:
        super().__init__(f"CP returned {status_code}: no active trial to cancel")
        self.status_code = status_code


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
        self._tenant_path_prefix = f"/api/v1/tenants/{tenant_id}"
        self._path = f"{self._tenant_path_prefix}/billing-state"
        self._shared_secret = shared_secret
        self._http_client = http_client
        self._logger = logger
        # First-401-per-process ERROR / subsequent WARNING. Instance-scoped
        # rather than module-global so tests stay isolated and a future
        # multi-tenant tenant process (if it ever existed) wouldn't share state.
        self._auth_failure_seen = False

    async def fetch_billing_state(self) -> BillingState:
        response = await self._send_signed("GET", self._path)

        if response.status_code == 401:
            self._log_auth_failure(self._path)
            raise CpAuthFailed(f"CP rejected signature for tenant poll at {self._path}")
        if response.status_code != 200:
            raise CpUnexpectedStatus(response.status_code, response.text)

        try:
            return self._parse(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise CpMalformedResponse(f"CP returned a malformed billing-state body: {exc}") from exc

    async def post_upgrade_now(self) -> None:
        """POST to CP's `/upgrade-now` endpoint to end the trial immediately.

        Returns None on success — CP doesn't ship a body the tenant needs.
        The next billing-state poll reflects the new (non-trial) state.
        Raises `CpNoActiveTrial` if the subscription isn't trialing (CP
        returns 409 in that case — see `_classify_trial_action_response`).
        """
        path = f"{self._tenant_path_prefix}/upgrade-now"
        response = await self._send_signed("POST", path)
        self._classify_trial_action_response(response, path)

    async def post_cancel_subscription(self) -> None:
        """POST to CP's `/cancel-subscription` endpoint to immediately cancel.

        Same shape as `post_upgrade_now` — CP requires the subscription to
        be trialing for either action right now (per the TODO on CP's
        router, paid-sub cancellation will land later), so 409 → no active
        trial for both endpoints.
        """
        path = f"{self._tenant_path_prefix}/cancel-subscription"
        response = await self._send_signed("POST", path)
        self._classify_trial_action_response(response, path)

    async def _send_signed(self, method: str, path: str) -> httpx.Response:
        """Sign and send a body-less request to CP. Returns the response.
        Translates network errors into `CpUnreachable`; status-code
        classification is the caller's job (each endpoint has its own
        status taxonomy).
        """
        timestamp, signature = sign_envelope(self._shared_secret, method, path, b"")
        headers = {
            TIMESTAMP_HEADER: str(timestamp),
            SIGNATURE_HEADER: signature,
        }
        url = f"{self._base_url}{path}"
        try:
            if method == "GET":
                return await self._http_client.get(url, headers=headers, timeout=5.0)
            if method == "POST":
                return await self._http_client.post(url, headers=headers, timeout=5.0)
            raise ValueError(f"unsupported method: {method}")
        except httpx.RequestError as exc:
            raise CpUnreachable(f"CP unreachable: {exc}") from exc

    def _classify_trial_action_response(self, response: httpx.Response, path: str) -> None:
        """Classify a trial-action POST response (upgrade-now / cancel-subscription).

        Both CP endpoints currently require a trialing subscription and emit
        409 with detail `"no active trial"` when the sub isn't trialing.
        Surfaced as `CpNoActiveTrial` so the admin router can re-emit 409
        with a clear message instead of collapsing to 502.
        """
        if response.status_code == 401:
            self._log_auth_failure(path)
            raise CpAuthFailed(f"CP rejected signature for {path}")
        if response.status_code == 409:
            raise CpNoActiveTrial(status_code=409)
        if not 200 <= response.status_code < 300:
            raise CpUnexpectedStatus(response.status_code, response.text)

    def _log_auth_failure(self, path: str) -> None:
        if not self._auth_failure_seen:
            self._auth_failure_seen = True
            self._logger.error(
                "CP returned 401 — likely "
                "SHU_TENANT_ID or SHU_ROUTER_SHARED_SECRET mismatch with the "
                "control-plane tenant row",
                extra={"path": path},
            )
        else:
            self._logger.warning("CP returned 401", extra={"path": path})

    @staticmethod
    def _parse(payload: Any) -> BillingState:
        return _BILLING_STATE_ADAPTER.validate_python(payload)
