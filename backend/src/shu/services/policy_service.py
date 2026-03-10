"""Policy Service — document-level CRUD and introspection for access policies.

Handles creating, listing, updating, and deleting access policies as whole
JSON documents (including nested bindings and statements).  Also provides
access-check delegation and effective-policy resolution for admin tooling.

All write operations invalidate the ``PolicyCache`` so that subsequent
access checks reflect the latest state.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shu.auth.models import User
from shu.core.exceptions import ConflictError, NotFoundError, ValidationError
from shu.core.logging import get_logger
from shu.models.access_policy import (
    AccessPolicy,
    AccessPolicyBinding,
    AccessPolicyStatement,
)
from shu.models.rbac import UserGroup, UserGroupMembership
from shu.schemas.access_policy import (
    AccessCheckResponse,
    EffectivePoliciesResponse,
    PolicyInput,
    PolicyListResponse,
    PolicyResponse,
)
from shu.services.policy_engine import POLICY_CACHE

logger = get_logger(__name__)


class PolicyService:
    """Service for managing access policy CRUD and introspection.

    Each policy is created and updated as a whole JSON document — bindings
    and statements are always included inline, not as sub-resources.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_policy(self, data: PolicyInput, created_by: str) -> AccessPolicy:
        """Create a complete policy document with bindings and statements.

        Validates that all referenced actor IDs exist before persisting.
        Invalidates the policy cache after a successful commit.

        Raises:
            ConflictError: If a policy with the same name already exists.
            ValidationError: If any referenced actor ID does not exist.

        """
        await self._check_duplicate_name(data.name)
        await self._validate_actor_ids(data.bindings)

        policy = AccessPolicy(
            name=data.name,
            description=data.description,
            effect=data.effect,
            is_active=data.is_active,
            created_by=created_by,
        )
        self.db.add(policy)
        await self.db.flush()
        await self.db.refresh(policy, attribute_names=["bindings", "statements"])

        await self._set_children(policy, data)

        await self.db.commit()
        await self.db.refresh(policy, attribute_names=["bindings", "statements"])

        POLICY_CACHE.invalidate()
        logger.info("policy.created", extra={"policy_id": policy.id, "policy_name": data.name})
        return policy

    async def list_policies(self, offset: int = 0, limit: int = 50, search: str | None = None) -> PolicyListResponse:
        """Return a paginated list of policies with bindings and statements.

        Args:
            offset: Number of records to skip.
            limit: Maximum number of records to return.
            search: Optional substring match on the policy name (case-insensitive).

        """
        base = select(AccessPolicy)
        if search:
            base = base.where(AccessPolicy.name.ilike(f"%{search}%"))

        count_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
        total = count_result.scalar() or 0

        query = (
            base.options(
                selectinload(AccessPolicy.bindings),
                selectinload(AccessPolicy.statements),
            )
            .order_by(AccessPolicy.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.db.execute(query)
        policies = result.scalars().unique().all()

        return PolicyListResponse(
            items=[PolicyResponse.model_validate(p) for p in policies],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def get_policy(self, policy_id: str) -> AccessPolicy | None:
        """Return a single policy with its bindings and statements, or None."""
        query = (
            select(AccessPolicy)
            .where(AccessPolicy.id == policy_id)
            .options(
                selectinload(AccessPolicy.bindings),
                selectinload(AccessPolicy.statements),
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def update_policy(self, policy_id: str, data: PolicyInput) -> AccessPolicy:
        """Replace a policy document — scalar fields, bindings, and statements.

        Full-document replacement: scalar fields are overwritten, bindings and
        statements are cleared and re-created from the input.

        Raises:
            NotFoundError: If the policy does not exist.
            ConflictError: If the new name collides with another policy.
            ValidationError: If any new actor ID does not exist.

        """
        policy = await self.get_policy(policy_id)
        if not policy:
            raise NotFoundError(f"Policy '{policy_id}' not found")

        if data.name != policy.name:
            await self._check_duplicate_name(data.name, exclude_id=policy_id)

        await self._validate_actor_ids(data.bindings)

        policy.name = data.name
        policy.description = data.description
        policy.effect = data.effect
        policy.is_active = data.is_active

        await self._set_children(policy, data)

        await self.db.commit()
        await self.db.refresh(policy, attribute_names=["bindings", "statements"])

        POLICY_CACHE.invalidate()
        logger.info("policy.updated", extra={"policy_id": policy_id})
        return policy

    async def delete_policy(self, policy_id: str) -> bool:
        """Delete a policy and its cascading bindings/statements.

        Returns True if the policy was found and deleted.

        Raises:
            NotFoundError: If the policy does not exist.

        """
        policy = await self.get_policy(policy_id)
        if not policy:
            raise NotFoundError(f"Policy '{policy_id}' not found")

        await self.db.delete(policy)
        await self.db.commit()

        POLICY_CACHE.invalidate()
        logger.info("policy.deleted", extra={"policy_id": policy_id})
        return True

    async def check_access(self, user_id: str, action: str, resource: str) -> AccessCheckResponse:
        """Delegate an access check to the PolicyCache and return a structured response."""
        allowed = await POLICY_CACHE.check(user_id, action, resource, self.db)
        decision = "allow" if allowed else "deny"

        if user_id in POLICY_CACHE._admin_user_ids:
            reason = "Admin users bypass all policy checks"
            matching: list[str] = []
        elif allowed:
            reason = "Matching allow policy with no deny override"
            matching = self._find_matching_policy_ids(user_id, action, resource)
        else:
            reason = "No matching allow policy or explicit deny"
            matching = self._find_matching_policy_ids(user_id, action, resource)

        return AccessCheckResponse(
            decision=decision,
            matching_policies=matching,
            reason=reason,
        )

    async def get_effective_policies(self, user_id: str) -> EffectivePoliciesResponse:
        """Resolve all policies that apply to a user (direct + group memberships)."""
        group_result = await self.db.execute(
            select(UserGroupMembership.group_id).where(
                UserGroupMembership.user_id == user_id,
                UserGroupMembership.is_active.is_(True),
            )
        )
        group_ids = [row[0] for row in group_result.all()]

        binding_conditions = [(AccessPolicyBinding.actor_type == "user") & (AccessPolicyBinding.actor_id == user_id)]
        if group_ids:
            binding_conditions.append(
                (AccessPolicyBinding.actor_type == "group") & (AccessPolicyBinding.actor_id.in_(group_ids))
            )

        policy_id_query = select(AccessPolicyBinding.policy_id).where(or_(*binding_conditions)).distinct()
        pid_result = await self.db.execute(policy_id_query)
        policy_ids = [row[0] for row in pid_result.all()]

        if not policy_ids:
            return EffectivePoliciesResponse(user_id=user_id, policies=[])

        query = (
            select(AccessPolicy)
            .where(AccessPolicy.id.in_(policy_ids))
            .options(
                selectinload(AccessPolicy.bindings),
                selectinload(AccessPolicy.statements),
            )
            .order_by(AccessPolicy.created_at.desc())
        )
        result = await self.db.execute(query)
        policies = result.scalars().unique().all()

        return EffectivePoliciesResponse(
            user_id=user_id,
            policies=[PolicyResponse.model_validate(p) for p in policies],
        )

    def _find_matching_policy_ids(self, user_id: str, action: str, resource: str) -> list[str]:
        """Identify which cached policies match a given action/resource for a user."""
        policy_ids = POLICY_CACHE._resolve_policy_ids(user_id)
        matching: list[str] = []
        for pid in policy_ids:
            policy = POLICY_CACHE._policies.get(pid)
            if not policy:
                continue
            for stmt in policy.statements:
                if POLICY_CACHE._statement_matches(stmt, action, resource):
                    matching.append(pid)
                    break
        return matching

    async def _check_duplicate_name(self, name: str, exclude_id: str | None = None) -> None:
        """Raise ConflictError if a policy with the given name already exists."""
        query = select(AccessPolicy.id).where(AccessPolicy.name == name)
        if exclude_id:
            query = query.where(AccessPolicy.id != exclude_id)
        result = await self.db.execute(query)
        if result.scalar_one_or_none():
            raise ConflictError(f"A policy named '{name}' already exists")

    async def _validate_actor_ids(self, bindings: list) -> None:
        """Verify that all actor IDs reference existing users or groups.

        Raises:
            ValidationError: If any actor ID does not exist.

        """
        user_ids = [b.actor_id for b in bindings if b.actor_type == "user"]
        group_ids = [b.actor_id for b in bindings if b.actor_type == "group"]

        if user_ids:
            result = await self.db.execute(select(func.count()).where(User.id.in_(user_ids)))
            found = result.scalar() or 0
            if found != len(set(user_ids)):
                raise ValidationError("One or more user IDs do not exist")

        if group_ids:
            result = await self.db.execute(select(func.count()).where(UserGroup.id.in_(group_ids)))
            found = result.scalar() or 0
            if found != len(set(group_ids)):
                raise ValidationError("One or more group IDs do not exist")

    async def _set_children(self, policy: AccessPolicy, data: PolicyInput) -> None:
        """Clear and re-create bindings and statements from the input document."""
        policy.bindings.clear()
        policy.statements.clear()
        await self.db.flush()

        for b in data.bindings:
            policy.bindings.append(
                AccessPolicyBinding(
                    policy_id=policy.id,
                    actor_type=b.actor_type,
                    actor_id=b.actor_id,
                )
            )

        for s in data.statements:
            policy.statements.append(
                AccessPolicyStatement(
                    policy_id=policy.id,
                    actions=s.actions,
                    resources=s.resources,
                )
            )
