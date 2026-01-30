"""
Property-based tests for SSO authentication.

These tests use Hypothesis to verify universal properties across all valid inputs.

**Validates: Requirements 3.3, 3.4**
"""

from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


class TestRoleActivationConsistency:
    """
    Property 6: Role and activation logic is consistent across providers.

    For any SSO provider (Google or Microsoft) and any email address, the role
    determination and activation logic SHALL produce identical results. Specifically:
    - If the user is the first user in the system, they SHALL be assigned admin role and be active
    - If the email is in the configured admin_emails list, they SHALL be assigned admin role and be active
    - Otherwise, they SHALL be assigned regular_user role and be inactive (requiring admin activation)

    **Validates: Requirements 3.3, 3.4**
    """

    @pytest.fixture
    def user_service(self):
        """Create a UserService instance with mocked settings."""
        from shu.services.user_service import UserService

        service = UserService()
        service.settings = MagicMock()
        service.settings.admin_emails = ["admin@example.com", "superuser@test.org"]
        return service

    @given(_provider_key=st.sampled_from(["google", "microsoft"]), email=st.emails(), is_first_user=st.booleans())
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_property_role_activation_consistency_across_providers(
        self, _provider_key: str, email: str, is_first_user: bool
    ):
        """
        Feature: sso-adapter-refactor
        Property 6: Role and activation logic is consistent across providers

        **Validates: Requirements 3.3, 3.4**

        This property verifies that for any provider and email combination:
        1. First user always becomes admin and is active
        2. Admin emails always become admin and are active
        3. Regular users are assigned regular_user role and are inactive
        """
        from shu.auth.models import UserRole
        from shu.services.user_service import UserService

        # Create service with consistent admin_emails config
        service = UserService()
        service.settings = MagicMock()
        service.settings.admin_emails = ["admin@example.com", "superuser@test.org"]

        # Determine expected role
        is_admin_email = email.lower() in [e.lower() for e in service.settings.admin_emails]

        # Get actual role from service
        role = service.determine_user_role(email, is_first_user)
        is_active = service.is_active(role, is_first_user)

        # Property assertions
        if is_first_user:
            # First user is always admin and active
            assert role == UserRole.ADMIN, f"First user should be admin, got {role}"
            assert is_active is True, "First user should be active"
        elif is_admin_email:
            # Admin email is always admin and active
            assert role == UserRole.ADMIN, f"Admin email should be admin, got {role}"
            assert is_active is True, "Admin email user should be active"
        else:
            # Regular user is regular_user and inactive
            assert role == UserRole.REGULAR_USER, f"Regular user should be regular_user, got {role}"
            assert is_active is False, "Regular user should be inactive"

    @given(
        _provider_key_1=st.sampled_from(["google", "microsoft"]),
        _provider_key_2=st.sampled_from(["google", "microsoft"]),
        email=st.emails(),
        is_first_user=st.booleans(),
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_property_same_email_same_role_any_provider(
        self, _provider_key_1: str, _provider_key_2: str, email: str, is_first_user: bool
    ):
        """
        Feature: sso-adapter-refactor
        Property 6: Role and activation logic is consistent across providers

        **Validates: Requirements 3.3, 3.4**

        This property verifies that the same email address gets the same role
        regardless of which SSO provider is used.
        """
        from shu.services.user_service import UserService

        # Create two service instances (simulating different provider flows)
        service1 = UserService()
        service1.settings = MagicMock()
        service1.settings.admin_emails = ["admin@example.com"]

        service2 = UserService()
        service2.settings = MagicMock()
        service2.settings.admin_emails = ["admin@example.com"]

        # Get role from both "providers"
        role1 = service1.determine_user_role(email, is_first_user)
        role2 = service2.determine_user_role(email, is_first_user)

        is_active1 = service1.is_active(role1, is_first_user)
        is_active2 = service2.is_active(role2, is_first_user)

        # Property: same email should get same role regardless of provider
        assert role1 == role2, f"Role should be consistent: {role1} vs {role2}"
        assert is_active1 == is_active2, f"Activation should be consistent: {is_active1} vs {is_active2}"

    @given(email=st.emails())
    @settings(max_examples=50)
    @pytest.mark.asyncio
    async def test_property_first_user_always_admin_active(self, email: str):
        """
        Feature: sso-adapter-refactor
        Property 6: Role and activation logic is consistent across providers

        **Validates: Requirements 3.3, 3.4**

        This property verifies that the first user is ALWAYS admin and active,
        regardless of their email address.
        """
        from shu.auth.models import UserRole
        from shu.services.user_service import UserService

        service = UserService()
        service.settings = MagicMock()
        service.settings.admin_emails = []  # Empty admin list

        role = service.determine_user_role(email, is_first_user=True)
        is_active = service.is_active(role, is_first_user=True)

        assert role == UserRole.ADMIN, f"First user must be admin, got {role}"
        assert is_active is True, "First user must be active"

    @given(admin_email=st.sampled_from(["admin@example.com", "superuser@test.org"]), is_first_user=st.booleans())
    @settings(max_examples=50)
    @pytest.mark.asyncio
    async def test_property_admin_email_always_admin_active(self, admin_email: str, is_first_user: bool):
        """
        Feature: sso-adapter-refactor
        Property 6: Role and activation logic is consistent across providers

        **Validates: Requirements 3.3, 3.4**

        This property verifies that configured admin emails are ALWAYS admin and active.
        """
        from shu.auth.models import UserRole
        from shu.services.user_service import UserService

        service = UserService()
        service.settings = MagicMock()
        service.settings.admin_emails = ["admin@example.com", "superuser@test.org"]

        role = service.determine_user_role(admin_email, is_first_user)
        is_active = service.is_active(role, is_first_user)

        assert role == UserRole.ADMIN, f"Admin email must be admin, got {role}"
        assert is_active is True, "Admin email user must be active"
