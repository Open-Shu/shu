"""Tests for shu.auth.dependencies.

Three concentric layers covered here:

1. ``decode_credential`` — turns the ``Authorization`` header into a
   ``CredentialResolution`` (JWT → user_id, API key → email). Every
   failure shape surfaces as 401, never 500.
2. ``resolve_tenant`` (yield-dependency) — routes the credential into the
   right ``tenant_context_for_*`` helper. The deeper SD-function lookup
   behavior is exercised in ``tests/unit/core/test_tenant.py``; what we
   pin here is the dependency wiring (credential → helper → context set).
3. ``require_internal_admin`` — case-insensitive allowlist gate on the
   admin email list.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from shu.auth.dependencies import (
    CredentialResolution,
    decode_credential,
    require_internal_admin,
    resolve_tenant,
)
from shu.core.tenant import tenant_context


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(authorization: str | None) -> MagicMock:
    request = MagicMock()
    # Mimic the dict-like interface of starlette Headers — only ``get`` is
    # used by the dependency.
    request.headers = {"Authorization": authorization} if authorization else {}
    return request


def _api_key_settings(api_key: str | None, mapped_email: str | None = "apikey@example.com") -> SimpleNamespace:
    return SimpleNamespace(api_key=api_key, api_key_user_email=mapped_email)


def _self_hosted_tenant_settings() -> SimpleNamespace:
    """Settings stub the tenant resolver reads (self-hosted short-circuit
    so resolve_tenant tests don't depend on a multi-tenant SD function)."""
    from shu.core.config import DeploymentMode

    return SimpleNamespace(deployment_mode=DeploymentMode.SELF_HOSTED, tenant_id=None)


def _user(email: str) -> MagicMock:
    u = MagicMock()
    u.email = email
    return u


# ---------------------------------------------------------------------------
# decode_credential — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_jwt_returns_jwt_credential_with_user_id() -> None:
    with patch(
        "shu.auth.dependencies.JWTManager.extract_user_from_token",
        return_value={"user_id": "user-abc"},
    ):
        cred = await decode_credential(_request("Bearer eyJraWQ.well.formed"))

    assert cred.source == "jwt"
    assert cred.user_id == "user-abc"
    assert cred.email is None


@pytest.mark.asyncio
async def test_valid_api_key_returns_api_key_credential_with_email() -> None:
    """API key path carries an email (the configured mapping), not a
    user_id — Shu has no per-user api_keys table, so the user_id is
    unknown until the users row is read downstream."""
    settings = _api_key_settings(api_key="super-secret", mapped_email="apikey@example.com")
    with patch("shu.auth.dependencies.get_settings_instance", return_value=settings):
        cred = await decode_credential(_request("ApiKey super-secret"))

    assert cred.source == "api_key"
    assert cred.email == "apikey@example.com"
    assert cred.user_id is None


# ---------------------------------------------------------------------------
# decode_credential — every failure shape must surface as 401, never 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_authorization_header_raises_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await decode_credential(_request(None))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_invalid_jwt_raises_401() -> None:
    """``extract_user_from_token`` returns ``None`` on bad signature /
    malformed token — the dependency turns that into 401."""
    with (
        patch("shu.auth.dependencies.JWTManager.extract_user_from_token", return_value=None),
        pytest.raises(HTTPException) as exc,
    ):
        await decode_credential(_request("Bearer garbage"))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_expired_jwt_raises_401() -> None:
    """Expired JWTs come back as ``None`` from extract_user_from_token —
    same shape as "invalid". Test pinned separately so a future
    refactor that distinguishes expiry doesn't silently weaken the gate."""
    with (
        patch("shu.auth.dependencies.JWTManager.extract_user_from_token", return_value=None),
        pytest.raises(HTTPException) as exc,
    ):
        await decode_credential(_request("Bearer expired.token"))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unknown_api_key_raises_401() -> None:
    settings = _api_key_settings(api_key="configured-key")
    with (
        patch("shu.auth.dependencies.get_settings_instance", return_value=settings),
        pytest.raises(HTTPException) as exc,
    ):
        await decode_credential(_request("ApiKey wrong-key"))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_api_key_present_but_settings_have_no_api_key_returns_401() -> None:
    """Deployment with API-key feature disabled (settings.api_key is None).
    The request must be rejected, not crash on a None-vs-str compare."""
    settings = _api_key_settings(api_key=None, mapped_email=None)
    with (
        patch("shu.auth.dependencies.get_settings_instance", return_value=settings),
        pytest.raises(HTTPException) as exc,
    ):
        await decode_credential(_request("ApiKey anything"))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_api_key_present_but_email_mapping_unconfigured_returns_401() -> None:
    """Operator misconfiguration: ``SHU_API_KEY`` set but
    ``SHU_API_KEY_USER_EMAIL`` is empty. Surfaces as 401 (not 500) so the
    distinction between bad-key and unconfigured-key isn't leaked."""
    settings = _api_key_settings(api_key="super-secret", mapped_email="")
    with (
        patch("shu.auth.dependencies.get_settings_instance", return_value=settings),
        pytest.raises(HTTPException) as exc,
    ):
        await decode_credential(_request("ApiKey super-secret"))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unsupported_scheme_raises_401() -> None:
    """Authorization header with an unknown scheme (``Basic ...``,
    ``Digest ...``, etc.) must 401 — not fall through to one of the
    supported branches."""
    with pytest.raises(HTTPException) as exc:
        await decode_credential(_request("Basic dXNlcjpwYXNz"))
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# CredentialResolution — frozen so intermediates can't rewrite it
# ---------------------------------------------------------------------------


def test_credential_resolution_is_frozen() -> None:
    """Defensive sanity: ``CredentialResolution`` is a frozen dataclass so an
    intermediate dependency can't accidentally rewrite ``user_id`` /
    ``email`` between decode and resolve. Either field rotting would mean
    the resolver sets context for one tenant and the user-fetch reads a
    different one."""
    cred = CredentialResolution(source="api_key", email="x@example.com")
    with pytest.raises(Exception):  # noqa: B017 — dataclass FrozenInstanceError is acceptable here
        cred.email = "y@example.com"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# resolve_tenant — credential → tenant_context wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_tenant_api_key_path_sets_context_via_email() -> None:
    """API-key credentials carry only an email, so resolve_tenant must
    route through ``tenant_context_for_email``. Verify the context is
    actually set inside the yield (not just returned)."""
    settings = _api_key_settings(api_key="super-secret", mapped_email="apikey@example.com")
    with patch("shu.auth.dependencies.get_settings_instance", return_value=settings):
        cred = await decode_credential(_request("ApiKey super-secret"))

    # Drive the yield-dependency manually rather than via Depends().
    with patch("shu.core.tenant.get_settings_instance", return_value=_self_hosted_tenant_settings()):
        gen = resolve_tenant(cred)
        tid = await gen.__anext__()
        try:
            assert tenant_context.get(None) == tid
        finally:
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()


# ---------------------------------------------------------------------------
# require_internal_admin — allowlist membership gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allows_user_whose_email_is_in_allowlist() -> None:
    settings = SimpleNamespace(admin_emails=["ops@maxwell.com", "founder@maxwell.com"])
    with patch("shu.auth.dependencies.get_settings_instance", return_value=settings):
        returned = await require_internal_admin(_user("ops@maxwell.com"))
    # Identity-check: the dependency returns the same User row it received,
    # so downstream handlers can use it as the actor.
    assert returned.email == "ops@maxwell.com"


@pytest.mark.asyncio
async def test_email_comparison_is_case_insensitive() -> None:
    """Operators frequently type emails in mixed case; the allowlist should
    not depend on capitalization."""
    settings = SimpleNamespace(admin_emails=["Ops@Maxwell.Com"])
    with patch("shu.auth.dependencies.get_settings_instance", return_value=settings):
        await require_internal_admin(_user("ops@MAXWELL.com"))


@pytest.mark.asyncio
async def test_rejects_user_not_in_allowlist() -> None:
    settings = SimpleNamespace(admin_emails=["ops@maxwell.com"])
    with (
        patch("shu.auth.dependencies.get_settings_instance", return_value=settings),
        pytest.raises(HTTPException) as exc,
    ):
        await require_internal_admin(_user("intruder@example.com"))
    assert exc.value.status_code == 403
    assert "Internal admin" in exc.value.detail


@pytest.mark.asyncio
async def test_rejects_when_allowlist_is_empty() -> None:
    """Empty ADMIN_EMAILS must NOT mean 'everybody is admin' — that's a
    silent privilege escalation if the env var is forgotten."""
    settings = SimpleNamespace(admin_emails=[])
    with (
        patch("shu.auth.dependencies.get_settings_instance", return_value=settings),
        pytest.raises(HTTPException) as exc,
    ):
        await require_internal_admin(_user("anyone@example.com"))
    assert exc.value.status_code == 403
