"""
Unit tests for the require_kb_write_access dependency.

Covers the three orthogonal grant paths:
- Power user / admin role grant (write any KB).
- Owner grant (regular user owns the target KB).
- PBAC grant (admin-authored kb.write policy on the target KB).

Plus 404 cases for missing KB and denied access (existence-leak prevention).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from shu.auth.models import UserRole
from shu.auth.rbac import require_kb_write_access


def _mock_user(user_id: str = "user-1", role: UserRole = UserRole.REGULAR_USER):
    """Build a User mock with a working has_role() that mirrors production hierarchy."""
    user = MagicMock()
    user.id = user_id
    user.role_enum = role

    role_hierarchy = {UserRole.REGULAR_USER: 1, UserRole.POWER_USER: 2, UserRole.ADMIN: 3}
    user_level = role_hierarchy[role]

    def has_role(required: UserRole) -> bool:
        return user_level >= role_hierarchy.get(required, 0)

    user.has_role = has_role
    return user


def _mock_kb(kb_id: str = "kb-1", slug: str = "kb-1-slug", owner_id: str | None = None):
    """Build a KnowledgeBase mock with the fields the dependency reads."""
    kb = MagicMock()
    kb.id = kb_id
    kb.slug = slug
    kb.owner_id = owner_id
    return kb


def _mock_db_returning(kb):
    """Build an AsyncMock DB session whose execute() returns the given KB."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=kb)
    db.execute = AsyncMock(return_value=result)
    return db


class TestRequireKbWriteAccess:
    """Tests for the require_kb_write_access dependency."""

    @pytest.mark.asyncio
    async def test_power_user_granted_on_any_kb(self):
        """Power users can write to KBs they don't own."""
        user = _mock_user("power-1", UserRole.POWER_USER)
        kb = _mock_kb(owner_id="someone-else")
        db = _mock_db_returning(kb)
        request = MagicMock()

        with patch("shu.auth.rbac.get_current_user", AsyncMock(return_value=user)):
            result = await require_kb_write_access("kb-1", request, db)

        assert result is user

    @pytest.mark.asyncio
    async def test_admin_granted_on_any_kb(self):
        """Admins can write to KBs they don't own."""
        user = _mock_user("admin-1", UserRole.ADMIN)
        kb = _mock_kb(owner_id="someone-else")
        db = _mock_db_returning(kb)
        request = MagicMock()

        with patch("shu.auth.rbac.get_current_user", AsyncMock(return_value=user)):
            result = await require_kb_write_access("kb-1", request, db)

        assert result is user

    @pytest.mark.asyncio
    async def test_regular_user_granted_on_owned_kb(self):
        """Regular users can write to KBs they own."""
        user = _mock_user("regular-1", UserRole.REGULAR_USER)
        kb = _mock_kb(owner_id="regular-1")
        db = _mock_db_returning(kb)
        request = MagicMock()

        with patch("shu.auth.rbac.get_current_user", AsyncMock(return_value=user)):
            result = await require_kb_write_access("kb-1", request, db)

        assert result is user

    @pytest.mark.asyncio
    async def test_regular_user_denied_on_non_owned_kb_without_pbac(self):
        """Regular users get 404 when they don't own the KB and no PBAC grant exists."""
        user = _mock_user("regular-1", UserRole.REGULAR_USER)
        kb = _mock_kb(owner_id="someone-else")
        db = _mock_db_returning(kb)
        request = MagicMock()

        with (
            patch("shu.auth.rbac.get_current_user", AsyncMock(return_value=user)),
            patch("shu.services.policy_engine.POLICY_CACHE.check", AsyncMock(return_value=False)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await require_kb_write_access("kb-1", request, db)

        # 404 (not 403) to avoid leaking KB existence — matches enforce_pbac convention.
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_regular_user_granted_on_non_owned_kb_with_pbac(self):
        """Regular users without ownership are granted when PBAC explicitly allows kb.write."""
        user = _mock_user("regular-1", UserRole.REGULAR_USER)
        kb = _mock_kb(owner_id="someone-else")
        db = _mock_db_returning(kb)
        request = MagicMock()

        with (
            patch("shu.auth.rbac.get_current_user", AsyncMock(return_value=user)),
            patch("shu.services.policy_engine.POLICY_CACHE.check", AsyncMock(return_value=True)),
        ):
            result = await require_kb_write_access("kb-1", request, db)

        assert result is user

    @pytest.mark.asyncio
    async def test_kb_not_found_raises_404(self):
        """Nonexistent KB IDs raise 404 before any role/ownership/PBAC checks run."""
        user = _mock_user("regular-1", UserRole.REGULAR_USER)
        db = _mock_db_returning(None)
        request = MagicMock()

        with patch("shu.auth.rbac.get_current_user", AsyncMock(return_value=user)):
            with pytest.raises(HTTPException) as exc_info:
                await require_kb_write_access("nonexistent-kb", request, db)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_null_owner_kb_treated_as_non_owned_for_regular_users(self):
        """System/shared KBs (owner_id IS NULL) are not owned by any user."""
        user = _mock_user("regular-1", UserRole.REGULAR_USER)
        kb = _mock_kb(owner_id=None)
        db = _mock_db_returning(kb)
        request = MagicMock()

        with (
            patch("shu.auth.rbac.get_current_user", AsyncMock(return_value=user)),
            patch("shu.services.policy_engine.POLICY_CACHE.check", AsyncMock(return_value=False)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await require_kb_write_access("kb-1", request, db)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_power_user_granted_on_null_owner_kb(self):
        """Power users can write to system/shared KBs (owner_id IS NULL)."""
        user = _mock_user("power-1", UserRole.POWER_USER)
        kb = _mock_kb(owner_id=None)
        db = _mock_db_returning(kb)
        request = MagicMock()

        with patch("shu.auth.rbac.get_current_user", AsyncMock(return_value=user)):
            result = await require_kb_write_access("kb-1", request, db)

        assert result is user
