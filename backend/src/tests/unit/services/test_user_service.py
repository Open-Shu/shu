"""Unit tests for UserService.is_active() with auto_activate_users setting."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.auth.models import UserRole
from shu.core.exceptions import ConflictError, NotFoundError
from shu.services.user_service import UserService


class TestIsActive:
    """Tests for UserService.is_active() covering auto_activate_users combinations."""

    @pytest.fixture
    def user_service(self):
        service = UserService()
        service.settings = MagicMock()
        service.settings.auto_activate_users = False
        service.settings.admin_emails = []
        return service

    def test_first_user_active_when_no_admins_configured(self, user_service):
        """First user is active (self-hosted bootstrap) when ADMIN_EMAILS is empty."""
        assert user_service.is_active(UserRole.REGULAR_USER, is_first_user=True) is True

    def test_admin_always_active(self, user_service):
        """Admin users are always active regardless of auto_activate setting."""
        assert user_service.is_active(UserRole.ADMIN, is_first_user=False) is True

    def test_regular_user_inactive_by_default(self, user_service):
        """Regular users are inactive when auto_activate is false (default)."""
        assert user_service.is_active(UserRole.REGULAR_USER, is_first_user=False) is False

    def test_regular_user_active_when_auto_activate_enabled(self, user_service):
        """Regular users are immediately active when auto_activate is true."""
        user_service.settings.auto_activate_users = True
        assert user_service.is_active(UserRole.REGULAR_USER, is_first_user=False) is True

    def test_auto_activate_does_not_change_admin_behavior(self, user_service):
        """Admin activation is unchanged when auto_activate is enabled."""
        user_service.settings.auto_activate_users = True
        assert user_service.is_active(UserRole.ADMIN, is_first_user=False) is True

    def test_auto_activate_does_not_change_first_user_behavior(self, user_service):
        """First-user activation is unchanged when auto_activate is enabled."""
        user_service.settings.auto_activate_users = True
        assert user_service.is_active(UserRole.REGULAR_USER, is_first_user=True) is True


class TestAdminEmailsAuthoritative:
    """SHU-840: when ADMIN_EMAILS is configured (every hosted silo tenant — the
    CP seeds the customer's email at provision), that list is the sole authority
    for admin + first-login activation. A stranger who races to register first
    must not inherit admin, nor land active. First-user-becomes-admin and
    first-user-auto-active remain only as the self-hosted, no-admins-configured
    bootstrap."""

    @pytest.fixture
    def user_service(self):
        service = UserService()
        service.settings = MagicMock()
        service.settings.auto_activate_users = False
        service.settings.admin_emails = ["owner@acme.com"]
        return service

    def test_configured_admin_email_is_admin(self, user_service):
        assert (
            user_service.determine_user_role("owner@acme.com", is_first_user=False)
            == UserRole.ADMIN
        )

    def test_configured_admin_email_match_is_case_insensitive(self, user_service):
        assert (
            user_service.determine_user_role("Owner@ACME.com", is_first_user=True)
            == UserRole.ADMIN
        )

    def test_first_user_not_in_admin_list_is_regular(self, user_service):
        # The hole: a stranger registering first on a fresh tenant whose
        # ADMIN_EMAILS names the customer must NOT become admin.
        assert (
            user_service.determine_user_role("stranger@evil.com", is_first_user=True)
            == UserRole.REGULAR_USER
        )

    def test_first_user_not_in_admin_list_is_inactive(self, user_service):
        # ...and must not be auto-activated either — only the configured admin
        # can activate them.
        assert (
            user_service.is_active(UserRole.REGULAR_USER, is_first_user=True) is False
        )

    def test_configured_admin_is_active(self, user_service):
        assert user_service.is_active(UserRole.ADMIN, is_first_user=False) is True

    def test_bootstrap_preserved_when_no_admins_configured(self, user_service):
        # Self-hosted with no ADMIN_EMAILS: first user still bootstraps as an
        # active admin.
        user_service.settings.admin_emails = []
        assert (
            user_service.determine_user_role("anyone@self.host", is_first_user=True)
            == UserRole.ADMIN
        )
        assert user_service.is_active(UserRole.REGULAR_USER, is_first_user=True) is True


# ---------------------------------------------------------------------------
# cp_set_user_active (SHU-785) — the CP kill-switch.
# ---------------------------------------------------------------------------


def _wire_cp(users: list[object]) -> tuple[MagicMock, AsyncMock]:
    """Build (tenant_admin_svc, audit) wired to a session whose User SELECT
    returns `users`."""
    session = MagicMock()
    result = MagicMock()
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=users)))
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _impersonate(tenant_id, actor, reason):
        yield session

    tenant_admin_svc = MagicMock()
    tenant_admin_svc.impersonate_tenant = _impersonate

    audit = AsyncMock()
    return tenant_admin_svc, audit


class TestCpSetUserActive:
    @pytest.mark.asyncio
    async def test_flip_to_true_activates_single_user(self) -> None:
        user = MagicMock(id="user-1", email="u@example.com", is_active=False)
        tenant_admin, audit = _wire_cp([user])

        resp = await UserService().cp_set_user_active(
            "tenant-1",
            is_active=True,
            reason="reactivate after TOS resolution",
            tenant_admin_svc=tenant_admin,
            audit_logger=audit,
        )

        assert user.is_active is True
        assert resp.user_id == "user-1"
        assert resp.is_active is True
        audit.log.assert_awaited_once()
        assert audit.log.await_args.kwargs["event"] == "cp_user_active_set"
        assert audit.log.await_args.kwargs["is_active"] is True

    @pytest.mark.asyncio
    async def test_flip_to_false_deactivates_single_user(self) -> None:
        user = MagicMock(id="user-1", email="u@example.com", is_active=True)
        tenant_admin, audit = _wire_cp([user])

        resp = await UserService().cp_set_user_active(
            "tenant-1",
            is_active=False,
            reason="TOS violation",
            tenant_admin_svc=tenant_admin,
            audit_logger=audit,
        )

        assert user.is_active is False
        assert resp.is_active is False

    @pytest.mark.asyncio
    async def test_zero_users_raises_404(self) -> None:
        tenant_admin, audit = _wire_cp([])
        with pytest.raises(NotFoundError, match="no users"):
            await UserService().cp_set_user_active(
                "tenant-1",
                is_active=True,
                reason="r",
                tenant_admin_svc=tenant_admin,
                audit_logger=audit,
            )
        audit.log.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_more_than_one_user_raises_409(self) -> None:
        users = [
            MagicMock(id="user-1", email="a@example.com", is_active=True),
            MagicMock(id="user-2", email="b@example.com", is_active=True),
        ]
        tenant_admin, audit = _wire_cp(users)
        with pytest.raises(ConflictError) as exc_info:
            await UserService().cp_set_user_active(
                "tenant-1",
                is_active=False,
                reason="r",
                tenant_admin_svc=tenant_admin,
                audit_logger=audit,
            )
        assert exc_info.value.details["user_count"] == 2
        audit.log.assert_not_awaited()
        assert all(u.is_active is True for u in users)

    @pytest.mark.asyncio
    async def test_idempotent_reflip_still_audits(self) -> None:
        """No-state-change case still emits an audit event so the call is recorded."""
        user = MagicMock(id="user-1", email="u@example.com", is_active=True)
        tenant_admin, audit = _wire_cp([user])
        await UserService().cp_set_user_active(
            "tenant-1",
            is_active=True,
            reason="redundant retry",
            tenant_admin_svc=tenant_admin,
            audit_logger=audit,
        )
        audit.log.assert_awaited_once()
