"""Unit tests for the policies API router.

Tests cover:
- POST /policies -> 201 with policy document
- GET /policies -> paginated list
- GET /policies/{id} -> detail, 404 for missing
- PUT /policies/{id} -> updated document
- DELETE /policies/{id} -> 204
- GET /policies/check -> access check result
- GET /policies/effective/{user_id} -> effective policies
- Error handling (conflict, not found, validation, unexpected)
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.api.policies import (
    check_access,
    create_policy,
    delete_policy,
    get_effective_policies,
    get_policy,
    list_policies,
    policies_router,
    update_policy,
)
from shu.core.exceptions import ConflictError, NotFoundError, ShuException, ValidationError
from shu.schemas.access_policy import (
    AccessCheckResponse,
    EffectivePoliciesResponse,
    PolicyInput,
    PolicyListResponse,
    PolicyResponse,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

SAMPLE_INPUT = PolicyInput(
    name="test-policy",
    description="A test policy",
    effect="allow",
    is_active=True,
    bindings=[{"actor_type": "user", "actor_id": "user-1"}],
    statements=[{"actions": ["experience.read"], "resources": ["experience:*"]}],
)


def _mock_user(*, user_id: str = "admin-1") -> MagicMock:
    """Build a mock admin User."""
    user = MagicMock()
    user.id = user_id
    return user


def _make_policy_orm(
    policy_id: str = "policy-1",
    name: str = "test-policy",
    effect: str = "allow",
) -> MagicMock:
    """Build a mock AccessPolicy ORM object that satisfies PolicyResponse.model_validate."""
    policy = MagicMock()
    policy.id = policy_id
    policy.name = name
    policy.description = "A test policy"
    policy.effect = effect
    policy.is_active = True
    policy.created_by = "admin-1"
    policy.created_at = NOW
    policy.updated_at = NOW

    binding = MagicMock()
    binding.actor_type = "user"
    binding.actor_id = "user-1"
    policy.bindings = [binding]

    stmt = MagicMock()
    stmt.actions = ["experience.read"]
    stmt.resources = ["experience:*"]
    policy.statements = [stmt]

    return policy


def _parse(response) -> dict:
    """Decode a JSONResponse body."""
    return json.loads(response.body.decode())


class TestCreatePolicy:
    """POST /policies -> 201 with policy document."""

    @pytest.mark.asyncio
    async def test_create_policy_returns_201(self):
        """Successful creation returns 201 with the policy document."""
        db = AsyncMock()
        user = _mock_user()
        mock_policy = _make_policy_orm()

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.create_policy = AsyncMock(return_value=mock_policy)
            mock_svc_cls.return_value = mock_svc

            response = await create_policy(policy_data=SAMPLE_INPUT, current_user=user, db=db)

        assert response.status_code == 201
        body = _parse(response)
        assert body["data"]["id"] == "policy-1"
        assert body["data"]["name"] == "test-policy"
        mock_svc.create_policy.assert_called_once_with(SAMPLE_INPUT, created_by="admin-1")

    @pytest.mark.asyncio
    async def test_create_policy_conflict_returns_409(self):
        """Duplicate policy name returns 409."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.create_policy = AsyncMock(
                side_effect=ConflictError("A policy named 'test-policy' already exists")
            )
            mock_svc_cls.return_value = mock_svc

            response = await create_policy(policy_data=SAMPLE_INPUT, current_user=user, db=db)

        assert response.status_code == 409
        body = _parse(response)
        assert body["error"]["code"] == "POLICY_CONFLICT"

    @pytest.mark.asyncio
    async def test_create_policy_validation_error_returns_400(self):
        """Invalid actor IDs returns 400."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.create_policy = AsyncMock(
                side_effect=ValidationError("One or more user IDs do not exist")
            )
            mock_svc_cls.return_value = mock_svc

            response = await create_policy(policy_data=SAMPLE_INPUT, current_user=user, db=db)

        assert response.status_code == 400
        body = _parse(response)
        assert body["error"]["code"] == "POLICY_VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_create_policy_unexpected_error_returns_500(self):
        """Unexpected exception returns 500."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.create_policy = AsyncMock(side_effect=RuntimeError("boom"))
            mock_svc_cls.return_value = mock_svc

            response = await create_policy(policy_data=SAMPLE_INPUT, current_user=user, db=db)

        assert response.status_code == 500
        body = _parse(response)
        assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"


class TestListPolicies:
    """GET /policies -> paginated list."""

    @pytest.mark.asyncio
    async def test_list_policies_returns_paginated_list(self):
        """Returns a paginated response with items and total."""
        db = AsyncMock()
        user = _mock_user()
        policy = _make_policy_orm()
        list_response = PolicyListResponse(
            items=[PolicyResponse.model_validate(policy)],
            total=1,
            offset=0,
            limit=50,
        )

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.list_policies = AsyncMock(return_value=list_response)
            mock_svc_cls.return_value = mock_svc

            response = await list_policies(
                offset=0, limit=50, search=None, current_user=user, db=db
            )

        assert response.status_code == 200
        body = _parse(response)
        assert body["data"]["total"] == 1
        assert len(body["data"]["items"]) == 1

    @pytest.mark.asyncio
    async def test_list_policies_passes_query_params(self):
        """Query params are forwarded to the service."""
        db = AsyncMock()
        user = _mock_user()
        empty_list = PolicyListResponse(items=[], total=0, offset=10, limit=5)

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.list_policies = AsyncMock(return_value=empty_list)
            mock_svc_cls.return_value = mock_svc

            await list_policies(offset=10, limit=5, search="admin", current_user=user, db=db)

        mock_svc.list_policies.assert_called_once_with(offset=10, limit=5, search="admin")


class TestGetPolicy:
    """GET /policies/{id} -> detail, 404 for missing."""

    @pytest.mark.asyncio
    async def test_get_policy_returns_detail(self):
        """Existing policy returns 200 with full document."""
        db = AsyncMock()
        user = _mock_user()
        mock_policy = _make_policy_orm()

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.get_policy = AsyncMock(return_value=mock_policy)
            mock_svc_cls.return_value = mock_svc

            response = await get_policy(policy_id="policy-1", current_user=user, db=db)

        assert response.status_code == 200
        body = _parse(response)
        assert body["data"]["id"] == "policy-1"
        assert len(body["data"]["bindings"]) == 1
        assert len(body["data"]["statements"]) == 1

    @pytest.mark.asyncio
    async def test_get_policy_not_found_returns_404(self):
        """Missing policy returns 404."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.get_policy = AsyncMock(return_value=None)
            mock_svc_cls.return_value = mock_svc

            response = await get_policy(policy_id="nonexistent", current_user=user, db=db)

        assert response.status_code == 404
        body = _parse(response)
        assert body["error"]["code"] == "POLICY_NOT_FOUND"


class TestUpdatePolicy:
    """PUT /policies/{id} -> updated document."""

    @pytest.mark.asyncio
    async def test_update_policy_returns_updated_document(self):
        """Successful update returns 200 with the updated policy."""
        db = AsyncMock()
        user = _mock_user()
        updated = _make_policy_orm(name="updated-policy")

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.update_policy = AsyncMock(return_value=updated)
            mock_svc_cls.return_value = mock_svc

            response = await update_policy(
                policy_data=SAMPLE_INPUT, policy_id="policy-1", current_user=user, db=db
            )

        assert response.status_code == 200
        body = _parse(response)
        assert body["data"]["name"] == "updated-policy"
        mock_svc.update_policy.assert_called_once_with("policy-1", SAMPLE_INPUT)

    @pytest.mark.asyncio
    async def test_update_policy_not_found_returns_404(self):
        """Updating a nonexistent policy returns 404."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.update_policy = AsyncMock(
                side_effect=NotFoundError("Policy 'nonexistent' not found")
            )
            mock_svc_cls.return_value = mock_svc

            response = await update_policy(
                policy_data=SAMPLE_INPUT, policy_id="nonexistent", current_user=user, db=db
            )

        assert response.status_code == 404
        body = _parse(response)
        assert body["error"]["code"] == "POLICY_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_update_policy_conflict_returns_409(self):
        """Name conflict on update returns 409."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.update_policy = AsyncMock(
                side_effect=ConflictError("A policy named 'duplicate' already exists")
            )
            mock_svc_cls.return_value = mock_svc

            response = await update_policy(
                policy_data=SAMPLE_INPUT, policy_id="policy-1", current_user=user, db=db
            )

        assert response.status_code == 409
        body = _parse(response)
        assert body["error"]["code"] == "POLICY_CONFLICT"


class TestDeletePolicy:
    """DELETE /policies/{id} -> 204."""

    @pytest.mark.asyncio
    async def test_delete_policy_returns_204(self):
        """Successful deletion returns 204 with no content."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.delete_policy = AsyncMock(return_value=True)
            mock_svc_cls.return_value = mock_svc

            response = await delete_policy(policy_id="policy-1", current_user=user, db=db)

        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_policy_not_found_returns_404(self):
        """Deleting a nonexistent policy returns 404."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.delete_policy = AsyncMock(
                side_effect=NotFoundError("Policy 'nonexistent' not found")
            )
            mock_svc_cls.return_value = mock_svc

            response = await delete_policy(policy_id="nonexistent", current_user=user, db=db)

        assert response.status_code == 404
        body = _parse(response)
        assert body["error"]["code"] == "POLICY_NOT_FOUND"


class TestCheckAccess:
    """GET /policies/check -> access check result."""

    @pytest.mark.asyncio
    async def test_check_access_returns_allow(self):
        """Access check returning allow includes matching policies."""
        db = AsyncMock()
        user = _mock_user()
        check_result = AccessCheckResponse(
            decision="allow",
            matching_policies=["policy-1"],
            reason="Matching allow policy with no deny override",
        )

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.check_access = AsyncMock(return_value=check_result)
            mock_svc_cls.return_value = mock_svc

            response = await check_access(
                user_id="user-1",
                action="experience.read",
                resource="experience:exp-1",
                current_user=user,
                db=db,
            )

        assert response.status_code == 200
        body = _parse(response)
        assert body["data"]["decision"] == "allow"
        assert "policy-1" in body["data"]["matching_policies"]
        mock_svc.check_access.assert_called_once_with(
            user_id="user-1", action="experience.read", resource="experience:exp-1"
        )

    @pytest.mark.asyncio
    async def test_check_access_returns_deny(self):
        """Access check returning deny includes reason."""
        db = AsyncMock()
        user = _mock_user()
        check_result = AccessCheckResponse(
            decision="deny",
            matching_policies=[],
            reason="Explicit deny policy matched",
        )

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.check_access = AsyncMock(return_value=check_result)
            mock_svc_cls.return_value = mock_svc

            response = await check_access(
                user_id="user-1",
                action="experience.write",
                resource="experience:*",
                current_user=user,
                db=db,
            )

        assert response.status_code == 200
        body = _parse(response)
        assert body["data"]["decision"] == "deny"


class TestEffectivePolicies:
    """GET /policies/effective/{user_id} -> effective policies."""

    @pytest.mark.asyncio
    async def test_get_effective_policies_returns_list(self):
        """Returns all effective policies for a user."""
        db = AsyncMock()
        user = _mock_user()
        policy = _make_policy_orm()
        effective = EffectivePoliciesResponse(
            user_id="user-1",
            policies=[PolicyResponse.model_validate(policy)],
        )

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.get_effective_policies = AsyncMock(return_value=effective)
            mock_svc_cls.return_value = mock_svc

            response = await get_effective_policies(user_id="user-1", current_user=user, db=db)

        assert response.status_code == 200
        body = _parse(response)
        assert body["data"]["user_id"] == "user-1"
        assert len(body["data"]["policies"]) == 1

    @pytest.mark.asyncio
    async def test_get_effective_policies_empty(self):
        """User with no policies returns empty list."""
        db = AsyncMock()
        user = _mock_user()
        effective = EffectivePoliciesResponse(user_id="user-2", policies=[])

        with patch("shu.api.policies.PolicyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.get_effective_policies = AsyncMock(return_value=effective)
            mock_svc_cls.return_value = mock_svc

            response = await get_effective_policies(user_id="user-2", current_user=user, db=db)

        assert response.status_code == 200
        body = _parse(response)
        assert body["data"]["policies"] == []


class TestRouterContract:
    """Router contract tests for path ordering and documented status codes."""

    def test_create_and_delete_have_explicit_status_codes(self):
        """POST and DELETE routes declare status codes for accurate OpenAPI docs."""
        create_route = next(
            route for route in policies_router.routes if route.path == "/policies" and "POST" in route.methods
        )
        delete_route = next(
            route for route in policies_router.routes if route.path == "/policies/{policy_id}" and "DELETE" in route.methods
        )

        assert create_route.status_code == 201
        assert delete_route.status_code == 204

    def test_static_routes_precede_dynamic_policy_id_route(self):
        """Prevent '/check' and '/effective/{user_id}' from being shadowed by '/{policy_id}'."""

        def _index(path: str) -> int:
            for i, route in enumerate(policies_router.routes):
                if route.path == path and "GET" in route.methods:
                    return i
            raise AssertionError(f"Route not found: {path}")

        check_idx = _index("/policies/check")
        effective_idx = _index("/policies/effective/{user_id}")
        policy_id_idx = _index("/policies/{policy_id}")

        assert check_idx < policy_id_idx
        assert effective_idx < policy_id_idx
