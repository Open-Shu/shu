"""Unit tests for UserService.is_active() with auto_activate_users setting."""

from unittest.mock import MagicMock

import pytest

from shu.auth.models import UserRole
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

    def test_first_user_always_active(self, user_service):
        """First user is always active regardless of role or auto_activate setting."""
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
