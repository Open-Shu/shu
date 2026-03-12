"""Policy-Based Access Control (PBAC) engine — in-memory policy cache.

Maintains a singleton cache of all active access policies, their bindings,
and statements.  The cache is bulk-loaded on startup and refreshed either
when explicitly invalidated (mutations) or when the TTL elapses.

Access-check evaluation is performed by ``PolicyCache.check()`` (single
resource) and ``PolicyCache.get_denied_resources()`` (batch filtering).
Both methods use inverted indexes for O(1) policy lookups and support
glob-style wildcard matching via ``fnmatch``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from fnmatch import fnmatch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shu.auth.models import User, UserRole
from shu.core.config import Settings, get_settings
from shu.core.exceptions import AuthorizationError, NotFoundError
from shu.core.logging import get_logger
from shu.models.access_policy import AccessPolicy
from shu.models.rbac import UserGroupMembership

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CachedStatement:
    """Pre-processed statement with actions/resources split into exact vs wildcard sets.

    Exact values use ``frozenset`` for O(1) membership tests.
    Wildcard patterns are stored as a sorted ``list`` for sequential matching.
    """

    exact_actions: frozenset[str]
    wildcard_actions: list[str]
    exact_resources: frozenset[str]
    wildcard_resources: list[str]


@dataclass(frozen=True, slots=True)
class CachedPolicy:
    """Lightweight, immutable representation of an active policy."""

    id: str
    effect: str  # "allow" or "deny"
    statements: list[CachedStatement]


def _split_patterns(values: list[str]) -> tuple[frozenset[str], list[str]]:
    """Partition a list of action/resource strings into exact and wildcard sets.

    A value is considered a wildcard pattern if it contains ``*``.
    Wildcard patterns are returned sorted for deterministic evaluation order.

    Returns:
        A 2-tuple of (exact_frozenset, wildcard_sorted_list).

    """
    exact: list[str] = []
    wildcard: list[str] = []
    for v in values:
        if "*" in v:
            wildcard.append(v)
        else:
            exact.append(v)
    return frozenset(exact), sorted(wildcard)


class PolicyCache:
    """In-memory cache of all active access policies with inverted indexes.

    Designed as a module-level singleton (``POLICY_CACHE``).  On startup the
    application calls ``await POLICY_CACHE.initialize(db)`` to bulk-load
    policies.  Subsequent mutations invalidate the cache via ``invalidate()``,
    and the next access-check path calls ``_maybe_refresh(db)`` to reload if
    stale or if the TTL has elapsed.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

        # All active policies keyed by policy ID.
        self._policies: dict[str, CachedPolicy] = {}

        # Inverted indexes: user/group ID -> set of policy IDs
        self._user_policies: dict[str, set[str]] = {}
        self._group_policies: dict[str, set[str]] = {}

        # user_id -> set of group IDs the user belongs to (active memberships only)
        self._user_groups: dict[str, set[str]] = {}

        # Set of user IDs with admin role (bypass all policy checks)
        self._admin_user_ids: set[str] = set()

        # Refresh bookkeeping
        self._last_refresh: float = 0.0
        self._stale: bool = True
        self._ttl_seconds: int = self._settings.policy_cache_ttl
        self._lock: asyncio.Lock = asyncio.Lock()

    async def initialize(self, db: AsyncSession) -> None:
        """Bootstrap the cache on application startup.

        Must be called once during the app lifespan with an active DB session.
        """
        await self._refresh(db)

    def invalidate(self) -> None:
        """Mark the cache as stale so the next access check triggers a refresh.

        This is intentionally synchronous — mutation endpoints call it after
        committing their transaction, and the actual reload happens lazily on
        the next ``_maybe_refresh`` call.
        """
        self._stale = True
        logger.info("policy_cache.invalidated")

    async def _maybe_refresh(self, db: AsyncSession) -> None:
        """Refresh the cache if it is stale or the TTL has elapsed.

        Uses an ``asyncio.Lock`` to ensure only one refresh runs at a time;
        concurrent callers wait on the lock and then see the fresh data.
        """
        now = time.monotonic()
        ttl_expired = (now - self._last_refresh) >= self._ttl_seconds
        if not self._stale and not ttl_expired:
            return

        async with self._lock:
            # Double-check after acquiring the lock — another coroutine may
            # have already refreshed while we were waiting.
            now = time.monotonic()
            ttl_expired = (now - self._last_refresh) >= self._ttl_seconds
            if not self._stale and not ttl_expired:
                return
            try:
                await self._refresh(db)
            except Exception as exc:
                # Serve existing cache state on refresh failures and retry later.
                self._stale = True
                logger.warning("policy_cache.refresh_failed", extra={"error": str(exc)}, exc_info=True)

    async def _refresh(self, db: AsyncSession) -> None:
        """Bulk-load all active policies, bindings, statements, group memberships, and admin users.

        Replaces the entire cache contents.  The goal is a small number of DB
        round trips.
        """
        t0 = time.monotonic()

        new_policies, new_user_policies, new_group_policies = await self._load_policies_and_indexes(db)
        new_user_groups = await self._load_memberships(db)
        admin_ids = await self._load_admin_user_ids(db)

        self._policies = new_policies
        self._user_policies = new_user_policies
        self._group_policies = new_group_policies
        self._user_groups = new_user_groups
        self._admin_user_ids = admin_ids
        self._last_refresh = time.monotonic()
        self._stale = False

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "policy_cache.refreshed",
            extra={
                "policies": len(new_policies),
                "user_bindings": sum(len(v) for v in new_user_policies.values()),
                "group_bindings": sum(len(v) for v in new_group_policies.values()),
                "user_groups": len(new_user_groups),
                "admin_users": len(admin_ids),
                "elapsed_ms": round(elapsed_ms, 1),
            },
        )

    async def _load_policies_and_indexes(
        self, db: AsyncSession
    ) -> tuple[dict[str, CachedPolicy], dict[str, set[str]], dict[str, set[str]]]:
        """Load all active policies with bindings and statements, and build inverted indexes."""
        stmt = (
            select(AccessPolicy)
            .where(AccessPolicy.is_active.is_(True))
            .options(
                selectinload(AccessPolicy.bindings),
                selectinload(AccessPolicy.statements),
            )
        )
        result = await db.execute(stmt)
        policies = result.scalars().unique().all()

        new_policies: dict[str, CachedPolicy] = {}
        new_user_policies: dict[str, set[str]] = {}
        new_group_policies: dict[str, set[str]] = {}

        for policy in policies:
            cached_stmts: list[CachedStatement] = []
            for s in policy.statements:
                exact_actions, wc_actions = _split_patterns(s.actions or [])
                exact_resources, wc_resources = _split_patterns(s.resources or [])
                cached_stmts.append(
                    CachedStatement(
                        exact_actions=exact_actions,
                        wildcard_actions=wc_actions,
                        exact_resources=exact_resources,
                        wildcard_resources=wc_resources,
                    )
                )

            cp = CachedPolicy(
                id=policy.id,
                effect=policy.effect,
                statements=cached_stmts,
            )
            new_policies[policy.id] = cp

            for binding in policy.bindings:
                if binding.actor_type == "user":
                    new_user_policies.setdefault(binding.actor_id, set()).add(policy.id)
                elif binding.actor_type == "group":
                    new_group_policies.setdefault(binding.actor_id, set()).add(policy.id)

        return new_policies, new_user_policies, new_group_policies

    async def _load_memberships(self, db: AsyncSession) -> dict[str, set[str]]:
        """Load active group memberships and return a user_id -> group_ids map."""
        mem_stmt = select(
            UserGroupMembership.user_id,
            UserGroupMembership.group_id,
        ).where(UserGroupMembership.is_active.is_(True))
        mem_result = await db.execute(mem_stmt)

        user_groups: dict[str, set[str]] = {}
        for row in mem_result.all():
            user_groups.setdefault(row.user_id, set()).add(row.group_id)
        return user_groups

    async def _load_admin_user_ids(self, db: AsyncSession) -> set[str]:
        """Load IDs of all active admin users."""
        admin_stmt = select(User.id).where(
            User.role == UserRole.ADMIN.value,
            User.is_active.is_(True),
        )
        admin_result = await db.execute(admin_stmt)
        return {row[0] for row in admin_result.all()}

    def _resolve_policy_ids(self, user_id: str) -> set[str]:
        """Collect all policy IDs relevant to a user via direct and group bindings."""
        policy_ids = set(self._user_policies.get(user_id, set()))
        for group_id in self._user_groups.get(user_id, set()):
            policy_ids.update(self._group_policies.get(group_id, set()))
        return policy_ids

    @staticmethod
    def _statement_matches(stmt: CachedStatement, action: str, resource: str) -> bool:
        """Return True if a statement matches the given action and resource."""
        action_match = action in stmt.exact_actions or any(fnmatch(action, p) for p in stmt.wildcard_actions)
        if not action_match:
            return False
        return resource in stmt.exact_resources or any(fnmatch(resource, p) for p in stmt.wildcard_resources)

    async def _resolve_user_policies(self, user_id: str, db: AsyncSession) -> set[str] | None:
        """Refresh the cache if needed and resolve the user's relevant policy IDs.

        Returns ``None`` when the user is an admin (caller should allow
        unconditionally).  Otherwise returns the (possibly empty) set of
        policy IDs bound to this user.
        """
        await self._maybe_refresh(db)

        if user_id in self._admin_user_ids:
            return None

        return self._resolve_policy_ids(user_id)

    async def is_admin(self, user_id: str, db: AsyncSession) -> bool:
        """Return whether the user has the admin role (cached)."""
        await self._maybe_refresh(db)
        return user_id in self._admin_user_ids

    async def check(self, user_id: str, action: str, resource: str, db: AsyncSession) -> bool:
        """Evaluate whether a user is allowed to perform an action on a resource.

        Returns True if access is allowed, False if denied.

        Evaluation order:
        1. Admin users bypass all checks (always allowed).
        2. Collect policies bound to the user (directly or via groups).
        3. If no policies bind to the user, deny (default-deny).
        4. If any matching policy has effect=deny, deny (deny wins).
        5. If any matching policy has effect=allow, allow.
        6. No matching policies → deny.
        """
        relevant_policy_ids = await self._resolve_user_policies(user_id, db)
        if relevant_policy_ids is None:
            return True

        has_allow = False
        for policy_id in relevant_policy_ids:
            policy = self._policies.get(policy_id)
            if not policy:
                continue
            for stmt in policy.statements:
                if not self._statement_matches(stmt, action, resource):
                    continue
                if policy.effect == "deny":
                    return False
                has_allow = True

        return has_allow

    async def get_denied_resources(
        self,
        user_id: str,
        action: str,
        resource_type: str,
        resource_ids: list[str],
        db: AsyncSession,
    ) -> set[str]:
        """Return the subset of *resource_ids* that the user is denied access to.

        Constructs ``f"{resource_type}:{rid}"`` for each resource ID and checks
        it against the user's policies.  A resource is denied if an explicit deny
        matches **or** if no allow matches (default-deny).
        """
        relevant_policy_ids = await self._resolve_user_policies(user_id, db)
        if relevant_policy_ids is None:
            return set()

        if not relevant_policy_ids:
            return set(resource_ids)

        denied: set[str] = set()
        for rid in resource_ids:
            resource = f"{resource_type}:{rid}"
            has_allow = False
            is_denied = False
            for policy_id in relevant_policy_ids:
                policy = self._policies.get(policy_id)
                if not policy:
                    continue
                for stmt in policy.statements:
                    if not self._statement_matches(stmt, action, resource):
                        continue
                    if policy.effect == "deny":
                        is_denied = True
                        break
                    has_allow = True
                if is_denied:
                    break
            if is_denied or not has_allow:
                denied.add(rid)

        return denied


POLICY_CACHE = PolicyCache()


async def enforce_pbac(
    user_id: str, action: str, resource: str, db: AsyncSession, *, message: str = "Not found"
) -> None:
    """Raise ``NotFoundError`` if the user is denied access.

    Wraps ``POLICY_CACHE.check`` so callers can enforce PBAC with a single
    ``await`` — no conditional logic at the call site.  Returns 404 (not 403)
    to avoid leaking resource existence.

    Pass a custom *message* to match the surrounding not-found wording so
    that denied responses are indistinguishable from genuine misses.
    """
    if not await POLICY_CACHE.check(user_id, action, resource, db):
        raise NotFoundError(message)


async def enforce_admin(user_id: str, db: AsyncSession) -> None:
    """Raise ``AuthorizationError`` if the user is not an admin.

    Wraps ``POLICY_CACHE.is_admin`` so callers can gate admin-only operations
    with a single ``await``.
    """
    if not await POLICY_CACHE.is_admin(user_id, db):
        raise AuthorizationError("Admin access required")
