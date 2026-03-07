"""
Unit tests for PolicyService.

Tests cover:
- create_policy: DB objects created, cache invalidated, duplicate name rejected
- list_policies: pagination and search delegation
- get_policy: returns detail or None
- update_policy: scalar fields updated, children replaced, cache invalidated
- delete_policy: cascade delete, cache invalidated, 404 for missing
- Actor ID validation: reject non-existent user/group
- check_access: delegates to POLICY_CACHE
- get_effective_policies: resolves group memberships
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.exceptions import ConflictError, NotFoundError, ValidationError
from shu.models.access_policy import (
    AccessPolicy,
    AccessPolicyBinding,
    AccessPolicyStatement,
)
from shu.schemas.access_policy import (
    AccessCheckResponse,
    BindingInput,
    EffectivePoliciesResponse,
    PolicyInput,
    StatementInput,
)
from shu.services.policy_service import PolicyService


def _make_policy_input(**overrides) -> PolicyInput:
    """Build a PolicyInput with sensible defaults."""
    defaults = {
        "name": "test-policy",
        "description": "A test policy",
        "effect": "allow",
        "is_active": True,
        "bindings": [BindingInput(actor_type="user", actor_id="user-1")],
        "statements": [
            StatementInput(actions=["experience.read"], resources=["experience:*"])
        ],
    }
    defaults.update(overrides)
    return PolicyInput(**defaults)


def _make_mock_policy(policy_id: str = "policy-1", **overrides) -> MagicMock:
    """Build a mock AccessPolicy ORM object."""
    policy = MagicMock(spec=AccessPolicy)
    policy.id = policy_id
    policy.name = overrides.get("name", "test-policy")
    policy.description = overrides.get("description", "desc")
    policy.effect = overrides.get("effect", "allow")
    policy.is_active = overrides.get("is_active", True)
    policy.created_by = overrides.get("created_by", "admin-1")
    policy.created_at = datetime.now(timezone.utc)
    policy.updated_at = datetime.now(timezone.utc)
    policy.bindings = overrides.get("bindings", [])
    policy.statements = overrides.get("statements", [])
    return policy


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def service(mock_db):
    """Create a PolicyService instance with mocked db."""
    return PolicyService(mock_db)


class TestCreatePolicy:
    """Tests for PolicyService.create_policy()."""

    @pytest.mark.asyncio
    async def test_create_policy_success(self, service, mock_db) -> None:
        """Create policy adds to DB, flushes, commits, and invalidates cache."""
        data = _make_policy_input()

        # _check_duplicate_name: no existing policy
        # _validate_actor_ids: user count matches
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # _check_duplicate_name query
                result.scalar_one_or_none.return_value = None
            elif call_count == 2:
                # _validate_actor_ids user count
                result.scalar.return_value = 1
            else:
                result.scalar_one_or_none.return_value = None
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        with patch("shu.services.policy_service.POLICY_CACHE") as mock_cache:
            result = await service.create_policy(data, "admin-1")

        mock_db.add.assert_called_once()
        mock_db.flush.assert_awaited_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        mock_cache.invalidate.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_policy_duplicate_name(self, service, mock_db) -> None:
        """Create policy with duplicate name raises ConflictError."""
        data = _make_policy_input()

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = "existing-id"
        mock_db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(ConflictError, match="already exists"):
            await service.create_policy(data, "admin-1")

    @pytest.mark.asyncio
    async def test_create_policy_invalid_actor(self, service, mock_db) -> None:
        """Create policy with non-existent user ID raises ValidationError."""
        data = _make_policy_input()

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none.return_value = None  # no duplicate name
            elif call_count == 2:
                result.scalar.return_value = 0  # user not found
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        with pytest.raises(ValidationError, match="user IDs do not exist"):
            await service.create_policy(data, "admin-1")


class TestListPolicies:
    """Tests for PolicyService.list_policies()."""

    @pytest.mark.asyncio
    async def test_list_returns_paginated_response(self, service, mock_db) -> None:
        """list_policies returns a PolicyListResponse with total, offset, limit."""
        call_count = 0
        mock_policy = _make_mock_policy()

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # count query
                result.scalar.return_value = 1
            else:
                # list query
                scalars = MagicMock()
                scalars.unique.return_value.all.return_value = [mock_policy]
                result.scalars.return_value = scalars
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        response = await service.list_policies(offset=0, limit=10)

        assert response.total == 1
        assert response.offset == 0
        assert response.limit == 10
        assert len(response.items) == 1

    @pytest.mark.asyncio
    async def test_list_empty(self, service, mock_db) -> None:
        """list_policies with no results returns empty list and zero total."""
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar.return_value = 0
            else:
                scalars = MagicMock()
                scalars.unique.return_value.all.return_value = []
                result.scalars.return_value = scalars
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        response = await service.list_policies()

        assert response.total == 0
        assert response.items == []


class TestGetPolicy:
    """Tests for PolicyService.get_policy()."""

    @pytest.mark.asyncio
    async def test_get_returns_policy(self, service, mock_db) -> None:
        """get_policy returns the policy when found."""
        mock_policy = _make_mock_policy()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_policy
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await service.get_policy("policy-1")

        assert result is mock_policy

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(self, service, mock_db) -> None:
        """get_policy returns None when not found."""
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await service.get_policy("nonexistent")

        assert result is None


class TestUpdatePolicy:
    """Tests for PolicyService.update_policy()."""

    @pytest.mark.asyncio
    async def test_update_policy_success(self, service, mock_db) -> None:
        """Update overwrites scalar fields, replaces children, invalidates cache."""
        existing = _make_mock_policy(name="old-name")
        existing.bindings = []
        existing.statements = []
        data = _make_policy_input(name="new-name")

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # get_policy
                result.scalar_one_or_none.return_value = existing
            elif call_count == 2:
                # _check_duplicate_name (name changed)
                result.scalar_one_or_none.return_value = None
            elif call_count == 3:
                # _validate_actor_ids
                result.scalar.return_value = 1
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        with patch("shu.services.policy_service.POLICY_CACHE") as mock_cache:
            result = await service.update_policy("policy-1", data)

        assert existing.name == "new-name"
        assert existing.effect == "allow"
        mock_db.commit.assert_awaited_once()
        mock_cache.invalidate.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_policy_not_found(self, service, mock_db) -> None:
        """Update raises NotFoundError when policy doesn't exist."""
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        data = _make_policy_input()

        with pytest.raises(NotFoundError, match="not found"):
            await service.update_policy("nonexistent", data)

    @pytest.mark.asyncio
    async def test_update_skips_name_check_when_unchanged(self, service, mock_db) -> None:
        """Update skips duplicate name check when the name hasn't changed."""
        existing = _make_mock_policy(name="same-name")
        existing.bindings = []
        existing.statements = []
        data = _make_policy_input(name="same-name")

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # get_policy
                result.scalar_one_or_none.return_value = existing
            elif call_count == 2:
                # _validate_actor_ids (no name check — skipped)
                result.scalar.return_value = 1
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        with patch("shu.services.policy_service.POLICY_CACHE"):
            await service.update_policy("policy-1", data)

        # Only 2 DB calls: get_policy + validate_actor_ids (no duplicate name check)
        assert call_count == 2


class TestDeletePolicy:
    """Tests for PolicyService.delete_policy()."""

    @pytest.mark.asyncio
    async def test_delete_success(self, service, mock_db) -> None:
        """Delete removes the policy and invalidates cache."""
        mock_policy = _make_mock_policy()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_policy
        mock_db.execute = AsyncMock(return_value=result_mock)

        with patch("shu.services.policy_service.POLICY_CACHE") as mock_cache:
            result = await service.delete_policy("policy-1")

        assert result is True
        mock_db.delete.assert_awaited_once_with(mock_policy)
        mock_db.commit.assert_awaited_once()
        mock_cache.invalidate.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_not_found(self, service, mock_db) -> None:
        """Delete raises NotFoundError when policy doesn't exist."""
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(NotFoundError, match="not found"):
            await service.delete_policy("nonexistent")


class TestValidateActorIds:
    """Tests for PolicyService._validate_actor_ids()."""

    @pytest.mark.asyncio
    async def test_valid_user_ids(self, service, mock_db) -> None:
        """No error when all user IDs exist."""
        bindings = [BindingInput(actor_type="user", actor_id="u1")]
        result_mock = MagicMock()
        result_mock.scalar.return_value = 1
        mock_db.execute = AsyncMock(return_value=result_mock)

        await service._validate_actor_ids(bindings)

    @pytest.mark.asyncio
    async def test_invalid_user_ids(self, service, mock_db) -> None:
        """ValidationError when user IDs don't exist."""
        bindings = [
            BindingInput(actor_type="user", actor_id="u1"),
            BindingInput(actor_type="user", actor_id="u2"),
        ]
        result_mock = MagicMock()
        result_mock.scalar.return_value = 1  # only 1 of 2 found
        mock_db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(ValidationError, match="user IDs"):
            await service._validate_actor_ids(bindings)

    @pytest.mark.asyncio
    async def test_invalid_group_ids(self, service, mock_db) -> None:
        """ValidationError when group IDs don't exist."""
        bindings = [BindingInput(actor_type="group", actor_id="g1")]
        result_mock = MagicMock()
        result_mock.scalar.return_value = 0
        mock_db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(ValidationError, match="group IDs"):
            await service._validate_actor_ids(bindings)

    @pytest.mark.asyncio
    async def test_empty_bindings(self, service, mock_db) -> None:
        """No error and no DB calls for empty bindings."""
        await service._validate_actor_ids([])
        mock_db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mixed_user_and_group(self, service, mock_db) -> None:
        """Validates both users and groups when both types present."""
        bindings = [
            BindingInput(actor_type="user", actor_id="u1"),
            BindingInput(actor_type="group", actor_id="g1"),
        ]
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            result.scalar.return_value = 1  # both found
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        await service._validate_actor_ids(bindings)

        assert call_count == 2


class TestCheckAccess:
    """Tests for PolicyService.check_access()."""

    @pytest.mark.asyncio
    async def test_check_access_allowed(self, service) -> None:
        """check_access returns allow when POLICY_CACHE.check returns True."""
        with patch("shu.services.policy_service.POLICY_CACHE") as mock_cache:
            mock_cache.check = AsyncMock(return_value=True)
            mock_cache._admin_user_ids = set()
            mock_cache._resolve_policy_ids.return_value = set()

            result = await service.check_access("user-1", "experience.read", "experience:abc")

        assert result.decision == "allow"

    @pytest.mark.asyncio
    async def test_check_access_denied(self, service) -> None:
        """check_access returns deny when POLICY_CACHE.check returns False."""
        with patch("shu.services.policy_service.POLICY_CACHE") as mock_cache:
            mock_cache.check = AsyncMock(return_value=False)
            mock_cache._admin_user_ids = set()
            mock_cache._resolve_policy_ids.return_value = set()

            result = await service.check_access("user-1", "experience.read", "experience:abc")

        assert result.decision == "deny"

    @pytest.mark.asyncio
    async def test_check_access_admin_bypass(self, service) -> None:
        """check_access returns allow with admin reason for admin users."""
        with patch("shu.services.policy_service.POLICY_CACHE") as mock_cache:
            mock_cache.check = AsyncMock(return_value=True)
            mock_cache._admin_user_ids = {"admin-1"}

            result = await service.check_access("admin-1", "experience.read", "experience:abc")

        assert result.decision == "allow"
        assert "Admin" in result.reason
        assert result.matching_policies == []


class TestGetEffectivePolicies:
    """Tests for PolicyService.get_effective_policies()."""

    @pytest.mark.asyncio
    async def test_no_policies(self, service, mock_db) -> None:
        """Returns empty list when user has no policies bound."""
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # group memberships query
                result.all.return_value = []
            elif call_count == 2:
                # binding policy_ids query
                result.all.return_value = []
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        response = await service.get_effective_policies("user-1")

        assert response.user_id == "user-1"
        assert response.policies == []

    @pytest.mark.asyncio
    async def test_with_direct_and_group_policies(self, service, mock_db) -> None:
        """Returns policies resolved through direct bindings and group memberships."""
        mock_policy = _make_mock_policy()
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # group memberships
                row = MagicMock()
                row.__getitem__ = lambda _, i: "group-1"
                result.all.return_value = [row]
            elif call_count == 2:
                # binding policy_ids
                row = MagicMock()
                row.__getitem__ = lambda _, i: "policy-1"
                result.all.return_value = [row]
            elif call_count == 3:
                # policy query
                scalars = MagicMock()
                scalars.unique.return_value.all.return_value = [mock_policy]
                result.scalars.return_value = scalars
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        response = await service.get_effective_policies("user-1")

        assert response.user_id == "user-1"
        assert len(response.policies) == 1
