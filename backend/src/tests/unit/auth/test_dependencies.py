"""Tests for shu.auth.dependencies.require_internal_admin.

Coverage focus: allowlist membership decides access. The fetch_user chain
itself is exercised by other tests; we mock the resolved User so this
file stays focused on the role-gate logic.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from shu.auth.dependencies import require_internal_admin


def _user(email: str) -> MagicMock:
    u = MagicMock()
    u.email = email
    return u


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
