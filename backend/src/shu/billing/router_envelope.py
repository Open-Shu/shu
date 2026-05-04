"""Router envelope verification for forwarded webhook events.

The Shu Control Plane (shu-control-plane) receives Stripe events, verifies
the Stripe signature once at the edge, looks up the tenant by customer id,
and forwards the verbatim event body to the tenant's shu-api webhook
endpoint wrapped in an HMAC envelope. This module implements the tenant
side of that envelope — verifying that the inbound request was signed by
the control plane with the shared secret provisioned for this tenant.

Canonical signing string (must stay byte-identical with the router):
    f"{timestamp}.POST.{request.url.path}.".encode() + body

Headers:
    X-Shu-Router-Timestamp  unix seconds at sign time
    X-Shu-Router-Signature  "v1=<hex hmac-sha256>"

Reference implementation on the router side:
    shu-control-plane/src/control_plane/webhooks/signing.py
    shu-control-plane/scripts/fake_tenant_server.py

Replay protection is bounded by the skew window (default 300s) — a leaked
envelope cannot be replayed indefinitely, but within the window it can. If
stronger replay protection is ever needed, add a (timestamp, signature)
nonce cache at this layer.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from shu.billing.config import BillingSettings, get_billing_settings_dependency
from shu.core.logging import get_logger

logger = get_logger(__name__)

SIGNATURE_PREFIX = "v1="
TIMESTAMP_HEADER = "X-Shu-Router-Timestamp"
SIGNATURE_HEADER = "X-Shu-Router-Signature"
DEFAULT_SKEW_SECONDS = 300


class RouterSignatureError(Exception):
    """Raised when the router envelope fails HMAC verification."""


def _build_canonical_string(timestamp: int, method: str, path: str, body: bytes) -> bytes:
    # Byte-for-byte identical to control_plane.webhooks.signing.build_canonical_string.
    # The literal "." separators are concatenation delimiters only — never parsed
    # back out, so paths containing "." do not need escaping.
    return f"{timestamp}.{method.upper()}.{path}.".encode() + body


def _sign(shared_secret: str, timestamp: int, method: str, path: str, body: bytes) -> str:
    canonical = _build_canonical_string(timestamp, method, path, body)
    digest = hmac.new(shared_secret.encode(), canonical, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def sign_envelope(
    shared_secret: str,
    method: str,
    path: str,
    body: bytes,
    *,
    timestamp: int | None = None,
) -> tuple[int, str]:
    """Sign an outbound request to CP, byte-compatible with verify_envelope.

    Returns (timestamp, signature) so callers don't have to capture the clock
    at sign time and again at header-build time — a 1-second drift between
    the two would silently land outside the skew window on slow paths.
    """
    if timestamp is None:
        timestamp = int(time.time())
    return timestamp, _sign(shared_secret, timestamp, method, path, body)


def verify_envelope(
    shared_secret: str,
    signature_header: str,
    timestamp_header: str,
    method: str,
    path: str,
    body: bytes,
    *,
    now: int,
    skew_seconds: int = DEFAULT_SKEW_SECONDS,
) -> None:
    """Verify the router HMAC envelope. Raises RouterSignatureError on any failure.

    A single exception class for all failure modes — external callers get the
    same "signature_invalid" response regardless of which specific check failed.
    Differentiating timestamp skew from HMAC mismatch at the API surface would
    give an attacker useful signal about what to adjust.
    """
    try:
        timestamp = int(timestamp_header)
    except ValueError as e:
        raise RouterSignatureError("timestamp header is not an integer") from e

    skew = abs(now - timestamp)
    if skew > skew_seconds:
        raise RouterSignatureError(f"timestamp skew {skew}s exceeds {skew_seconds}s")

    if not signature_header.startswith(SIGNATURE_PREFIX):
        raise RouterSignatureError(f"signature header missing '{SIGNATURE_PREFIX}' prefix")

    expected = _sign(shared_secret, timestamp, method, path, body)
    if not hmac.compare_digest(expected, signature_header):
        raise RouterSignatureError("HMAC mismatch")


async def verify_router_envelope_dep(
    request: Request,
    settings: Annotated[BillingSettings, Depends(get_billing_settings_dependency)],
) -> bytes:
    """FastAPI dependency: verify the router envelope on inbound webhook requests.

    Returns the verified raw body on success so the route handler can parse it
    as a Stripe event. Raises HTTPException(401) with a structured error body
    on any verification failure — matches the error-body contract the router
    parses when it logs TENANT_AUTH_REJECTED outcomes.

    Absent or blank SHU_ROUTER_SHARED_SECRET is a misconfiguration, not an
    auth failure — respond 503 so the operator notices before Stripe notices.
    """
    if not settings.router_shared_secret:
        logger.error("SHU_ROUTER_SHARED_SECRET not configured; refusing to accept router webhooks")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "router_secret_not_configured"},
        )

    signature_header = request.headers.get(SIGNATURE_HEADER, "")
    timestamp_header = request.headers.get(TIMESTAMP_HEADER, "")
    body = await request.body()

    try:
        verify_envelope(
            shared_secret=settings.router_shared_secret,
            signature_header=signature_header,
            timestamp_header=timestamp_header,
            method=request.method,
            path=request.url.path,
            body=body,
            now=int(time.time()),
        )
    except RouterSignatureError as e:
        logger.warning(
            "Router envelope verification failed",
            extra={"error": str(e), "path": request.url.path},
        )
        # Returning a JSONResponse via HTTPException keeps the body shape
        # consistent with what the router parses to distinguish auth failures
        # from customer-scoping failures (409) and genuine server errors (500).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "signature_invalid"},
        )

    return body


__all__ = [
    "DEFAULT_SKEW_SECONDS",
    "SIGNATURE_HEADER",
    "SIGNATURE_PREFIX",
    "TIMESTAMP_HEADER",
    "RouterSignatureError",
    "sign_envelope",
    "verify_envelope",
    "verify_router_envelope_dep",
]
