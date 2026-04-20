"""Tests for shu.billing.router_envelope — HMAC verifier for forwarded webhooks.

Coverage focus: the pure verify_envelope function that the route dependency
wraps. The dependency itself is thin I/O glue (read headers + body, call
verify, raise HTTPException) and is covered end-to-end by the SHU-712 lab
scenario matrix rather than unit-level FastAPI mocking.

Cross-implementation compatibility with the router's signer lives in
shu-control-plane/tests; duplicating it here would coupled the two repos.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from shu.billing.router_envelope import (
    DEFAULT_SKEW_SECONDS,
    RouterSignatureError,
    verify_envelope,
)


def _sign_for(secret: str, timestamp: int, method: str, path: str, body: bytes) -> str:
    canonical = f"{timestamp}.{method.upper()}.{path}.".encode() + body
    digest = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    return f"v1={digest}"


class TestVerifyEnvelope:
    secret = "a" * 64

    def test_valid_signature_returns_none(self):
        ts = int(time.time())
        body = b'{"id":"evt_test"}'
        sig = _sign_for(self.secret, ts, "POST", "/api/v1/billing/webhooks", body)

        verify_envelope(
            shared_secret=self.secret,
            signature_header=sig,
            timestamp_header=str(ts),
            method="POST",
            path="/api/v1/billing/webhooks",
            body=body,
            now=ts,
        )

    def test_rejects_non_integer_timestamp(self):
        with pytest.raises(RouterSignatureError, match="not an integer"):
            verify_envelope(
                shared_secret=self.secret,
                signature_header="v1=ffff",
                timestamp_header="not-a-number",
                method="POST",
                path="/x",
                body=b"",
                now=0,
            )

    def test_rejects_expired_timestamp(self):
        ts = 1_000_000
        body = b"body"
        sig = _sign_for(self.secret, ts, "POST", "/x", body)

        with pytest.raises(RouterSignatureError, match="timestamp skew"):
            verify_envelope(
                shared_secret=self.secret,
                signature_header=sig,
                timestamp_header=str(ts),
                method="POST",
                path="/x",
                body=body,
                now=ts + DEFAULT_SKEW_SECONDS + 1,
            )

    def test_rejects_future_timestamp_beyond_skew(self):
        """Skew is absolute: future timestamps are rejected too, not just stale ones."""
        ts = 1_000_000
        body = b"body"
        sig = _sign_for(self.secret, ts, "POST", "/x", body)

        with pytest.raises(RouterSignatureError, match="timestamp skew"):
            verify_envelope(
                shared_secret=self.secret,
                signature_header=sig,
                timestamp_header=str(ts),
                method="POST",
                path="/x",
                body=body,
                now=ts - DEFAULT_SKEW_SECONDS - 1,
            )

    def test_rejects_missing_version_prefix(self):
        ts = int(time.time())
        body = b"body"
        # Compute the correct digest but drop the "v1=" prefix.
        digest = hmac.new(
            self.secret.encode(),
            f"{ts}.POST./x.".encode() + body,
            hashlib.sha256,
        ).hexdigest()

        with pytest.raises(RouterSignatureError, match="'v1=' prefix"):
            verify_envelope(
                shared_secret=self.secret,
                signature_header=digest,  # no prefix
                timestamp_header=str(ts),
                method="POST",
                path="/x",
                body=body,
                now=ts,
            )

    def test_rejects_mismatched_hmac(self):
        ts = int(time.time())
        body = b"body"
        sig = _sign_for("different-secret" + "0" * 48, ts, "POST", "/x", body)

        with pytest.raises(RouterSignatureError, match="HMAC mismatch"):
            verify_envelope(
                shared_secret=self.secret,
                signature_header=sig,
                timestamp_header=str(ts),
                method="POST",
                path="/x",
                body=body,
                now=ts,
            )

    def test_body_tamper_fails_verification(self):
        """Byte-level body tamper invalidates the signature (integrity guarantee)."""
        ts = int(time.time())
        original_body = b'{"amount":100}'
        tampered_body = b'{"amount":999}'
        sig = _sign_for(self.secret, ts, "POST", "/x", original_body)

        with pytest.raises(RouterSignatureError, match="HMAC mismatch"):
            verify_envelope(
                shared_secret=self.secret,
                signature_header=sig,
                timestamp_header=str(ts),
                method="POST",
                path="/x",
                body=tampered_body,
                now=ts,
            )

    def test_path_mismatch_fails_verification(self):
        """Signature is bound to the request path — router and tenant must agree on it."""
        ts = int(time.time())
        body = b""
        sig = _sign_for(self.secret, ts, "POST", "/api/v1/billing/webhooks", body)

        with pytest.raises(RouterSignatureError, match="HMAC mismatch"):
            verify_envelope(
                shared_secret=self.secret,
                signature_header=sig,
                timestamp_header=str(ts),
                method="POST",
                path="/api/v1/billing/something-else",
                body=body,
                now=ts,
            )

    def test_method_is_case_insensitive_on_verify(self):
        """Canonical string upper-cases the method on both sides; lowercase input must verify."""
        ts = int(time.time())
        body = b"body"
        sig = _sign_for(self.secret, ts, "post", "/x", body)  # signer accepts lowercase

        verify_envelope(
            shared_secret=self.secret,
            signature_header=sig,
            timestamp_header=str(ts),
            method="post",
            path="/x",
            body=body,
            now=ts,
        )
