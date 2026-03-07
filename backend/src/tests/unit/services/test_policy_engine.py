"""
Unit tests for PolicyCache.check() and PolicyCache.get_denied_resources().

Tests cover:
- Admin bypass (check returns True, get_denied_resources returns empty set)
- Default-deny when no policies bind to the user
- Exact action + exact resource matching
- Wildcard action matching via fnmatch
- Wildcard resource matching via fnmatch
- Deny-wins semantics (deny overrides allow)
- Group-based policy resolution
- get_denied_resources filtering with mixed deny/allow
- _statement_matches helper
- _split_patterns helper
- invalidate() marks cache stale
- TTL expiry triggers refresh
- Concurrent refresh prevented by lock
- Inactive policy excluded from evaluation
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.services.policy_engine import (
    CachedPolicy,
    CachedStatement,
    PolicyCache,
    _split_patterns,
)


def _make_statement(
    actions: list[str] | None = None,
    resources: list[str] | None = None,
) -> CachedStatement:
    """Build a CachedStatement from raw action/resource strings."""
    exact_a, wc_a = _split_patterns(actions or [])
    exact_r, wc_r = _split_patterns(resources or [])
    return CachedStatement(
        exact_actions=exact_a,
        wildcard_actions=wc_a,
        exact_resources=exact_r,
        wildcard_resources=wc_r,
    )


def _make_cache(**overrides) -> PolicyCache:
    """Create a PolicyCache with a mock settings object and pre-populated state.

    The cache is marked as not stale so _maybe_refresh is a no-op.
    """
    settings = MagicMock()
    settings.policy_cache_ttl = 9999
    cache = PolicyCache(settings=settings)
    cache._stale = False
    cache._last_refresh = 1e12  # far future so TTL never fires
    for key, value in overrides.items():
        setattr(cache, key, value)
    return cache


# -- Helpers shared across TestCheck and TestGetDeniedResources --

# A broad allow-all policy useful for tests that focus on deny behavior.
ALLOW_ALL = CachedPolicy(
    id="allow-all",
    effect="allow",
    statements=[_make_statement(["*"], ["*"])],
)


class TestSplitPatterns:
    """Tests for the _split_patterns helper."""

    def test_empty_list(self) -> None:
        exact, wildcard = _split_patterns([])
        assert exact == frozenset()
        assert wildcard == []

    def test_all_exact(self) -> None:
        exact, wildcard = _split_patterns(["experience.read", "plugin.execute"])
        assert exact == frozenset({"experience.read", "plugin.execute"})
        assert wildcard == []

    def test_all_wildcard(self) -> None:
        exact, wildcard = _split_patterns(["experience.*", "plugin:shu_*"])
        assert exact == frozenset()
        assert wildcard == ["experience.*", "plugin:shu_*"]

    def test_mixed(self) -> None:
        exact, wildcard = _split_patterns(["experience.read", "plugin:*"])
        assert exact == frozenset({"experience.read"})
        assert wildcard == ["plugin:*"]

    def test_wildcard_sorted(self) -> None:
        exact, wildcard = _split_patterns(["z:*", "a:*", "m:*"])
        assert exact == frozenset()
        assert wildcard == ["a:*", "m:*", "z:*"]


class TestStatementMatches:
    """Tests for PolicyCache._statement_matches static method."""

    def test_exact_action_exact_resource(self) -> None:
        stmt = _make_statement(["experience.read"], ["experience:abc"])
        assert PolicyCache._statement_matches(stmt, "experience.read", "experience:abc")

    def test_no_action_match(self) -> None:
        stmt = _make_statement(["experience.read"], ["experience:abc"])
        assert not PolicyCache._statement_matches(stmt, "plugin.execute", "experience:abc")

    def test_no_resource_match(self) -> None:
        stmt = _make_statement(["experience.read"], ["experience:abc"])
        assert not PolicyCache._statement_matches(stmt, "experience.read", "experience:xyz")

    def test_wildcard_action(self) -> None:
        stmt = _make_statement(["experience.*"], ["experience:abc"])
        assert PolicyCache._statement_matches(stmt, "experience.read", "experience:abc")
        assert PolicyCache._statement_matches(stmt, "experience.edit", "experience:abc")

    def test_wildcard_resource(self) -> None:
        stmt = _make_statement(["experience.read"], ["experience:*"])
        assert PolicyCache._statement_matches(stmt, "experience.read", "experience:abc")
        assert PolicyCache._statement_matches(stmt, "experience.read", "experience:xyz")

    def test_wildcard_prefix_resource(self) -> None:
        stmt = _make_statement(["plugin.execute"], ["plugin:shu_gmail_*"])
        assert PolicyCache._statement_matches(stmt, "plugin.execute", "plugin:shu_gmail_digest")
        assert not PolicyCache._statement_matches(stmt, "plugin.execute", "plugin:shu_calendar")

    def test_multiple_actions_and_resources(self) -> None:
        """Statement with several actions and resources matches any combination."""
        stmt = _make_statement(
            ["experience.read", "experience.edit"],
            ["experience:a", "experience:b"],
        )
        assert PolicyCache._statement_matches(stmt, "experience.read", "experience:a")
        assert PolicyCache._statement_matches(stmt, "experience.edit", "experience:b")
        assert not PolicyCache._statement_matches(stmt, "experience.read", "experience:c")
        assert not PolicyCache._statement_matches(stmt, "plugin.execute", "experience:a")


class TestCheck:
    """Tests for PolicyCache.check()."""

    @pytest.mark.asyncio
    async def test_admin_bypass(self) -> None:
        """Admin users bypass all checks, even explicit deny policies."""
        cache = _make_cache(
            _admin_user_ids={"admin-1"},
            _policies={
                "p1": CachedPolicy(
                    id="p1",
                    effect="deny",
                    statements=[_make_statement(["experience.read"], ["experience:*"])],
                ),
            },
            _user_policies={"admin-1": {"p1"}},
        )
        result = await cache.check("admin-1", "experience.read", "experience:abc", AsyncMock())
        assert result is True

    @pytest.mark.asyncio
    async def test_default_deny_empty_cache(self) -> None:
        """Empty cache (no policies loaded) → deny for any non-admin user."""
        cache = _make_cache()
        result = await cache.check("user-1", "experience.read", "experience:abc", AsyncMock())
        assert result is False

    @pytest.mark.asyncio
    async def test_default_deny_no_bindings(self) -> None:
        """Policies exist but none bind to this user → deny."""
        cache = _make_cache(
            _policies={
                "p1": CachedPolicy(
                    id="p1",
                    effect="allow",
                    statements=[_make_statement(["experience.read"], ["experience:*"])],
                ),
            },
            _user_policies={"other-user": {"p1"}},
        )
        result = await cache.check("user-1", "experience.read", "experience:abc", AsyncMock())
        assert result is False

    @pytest.mark.asyncio
    async def test_deny_wins(self) -> None:
        """When both allow and deny match, deny wins."""
        cache = _make_cache(
            _policies={
                "allow-p": CachedPolicy(
                    id="allow-p",
                    effect="allow",
                    statements=[_make_statement(["experience.read"], ["experience:abc"])],
                ),
                "deny-p": CachedPolicy(
                    id="deny-p",
                    effect="deny",
                    statements=[_make_statement(["experience.read"], ["experience:abc"])],
                ),
            },
            _user_policies={"user-1": {"allow-p", "deny-p"}},
        )
        result = await cache.check("user-1", "experience.read", "experience:abc", AsyncMock())
        assert result is False

    @pytest.mark.asyncio
    async def test_allow_when_only_allow_policies(self) -> None:
        """Allow policy matches with no deny → allow."""
        cache = _make_cache(
            _policies={
                "allow-p": CachedPolicy(
                    id="allow-p",
                    effect="allow",
                    statements=[_make_statement(["experience.read"], ["experience:abc"])],
                ),
            },
            _user_policies={"user-1": {"allow-p"}},
        )
        result = await cache.check("user-1", "experience.read", "experience:abc", AsyncMock())
        assert result is True

    @pytest.mark.asyncio
    async def test_deny_exact_match(self) -> None:
        """Exact action + exact resource deny blocks that resource."""
        cache = _make_cache(
            _policies={
                "allow-all": ALLOW_ALL,
                "deny-p": CachedPolicy(
                    id="deny-p",
                    effect="deny",
                    statements=[_make_statement(["experience.read"], ["experience:secret"])],
                ),
            },
            _user_policies={"user-1": {"allow-all", "deny-p"}},
        )
        assert await cache.check("user-1", "experience.read", "experience:secret", AsyncMock()) is False
        assert await cache.check("user-1", "experience.read", "experience:public", AsyncMock()) is True

    @pytest.mark.asyncio
    async def test_deny_wildcard_resource(self) -> None:
        """Glob-style wildcard resource matching on deny, allow covers the rest."""
        cache = _make_cache(
            _policies={
                "allow-all": ALLOW_ALL,
                "deny-p": CachedPolicy(
                    id="deny-p",
                    effect="deny",
                    statements=[_make_statement(["plugin.execute"], ["plugin:shu_gmail_*"])],
                ),
            },
            _user_policies={"user-1": {"allow-all", "deny-p"}},
        )
        assert await cache.check("user-1", "plugin.execute", "plugin:shu_gmail_digest", AsyncMock()) is False
        assert await cache.check("user-1", "plugin.execute", "plugin:shu_calendar", AsyncMock()) is True

    @pytest.mark.asyncio
    async def test_deny_wildcard_action(self) -> None:
        """Glob-style wildcard action matching."""
        cache = _make_cache(
            _policies={
                "deny-p": CachedPolicy(
                    id="deny-p",
                    effect="deny",
                    statements=[_make_statement(["experience.*"], ["experience:secret"])],
                ),
            },
            _user_policies={"user-1": {"deny-p"}},
        )
        assert await cache.check("user-1", "experience.read", "experience:secret", AsyncMock()) is False
        assert await cache.check("user-1", "experience.edit", "experience:secret", AsyncMock()) is False

    @pytest.mark.asyncio
    async def test_group_policy_resolution(self) -> None:
        """Policies resolved via group memberships."""
        cache = _make_cache(
            _policies={
                "deny-p": CachedPolicy(
                    id="deny-p",
                    effect="allow",
                    statements=[_make_statement(["experience.read"], ["experience:secret"])],
                ),
            },
            _group_policies={"group-eng": {"deny-p"}},
            _user_groups={"user-1": {"group-eng"}},
        )
        assert await cache.check("user-1", "experience.read", "experience:secret", AsyncMock()) is True

    @pytest.mark.asyncio
    async def test_group_and_direct_policies_combined(self) -> None:
        """Deny from group wins over direct allow."""
        cache = _make_cache(
            _policies={
                "allow-direct": CachedPolicy(
                    id="allow-direct",
                    effect="allow",
                    statements=[_make_statement(["experience.read"], ["experience:abc"])],
                ),
                "deny-group": CachedPolicy(
                    id="deny-group",
                    effect="deny",
                    statements=[_make_statement(["experience.read"], ["experience:abc"])],
                ),
            },
            _user_policies={"user-1": {"allow-direct"}},
            _group_policies={"group-1": {"deny-group"}},
            _user_groups={"user-1": {"group-1"}},
        )
        assert await cache.check("user-1", "experience.read", "experience:abc", AsyncMock()) is False

    @pytest.mark.asyncio
    async def test_no_statement_match_denies(self) -> None:
        """Policy binds to user but no statement matches → deny (default-deny)."""
        cache = _make_cache(
            _policies={
                "allow-p": CachedPolicy(
                    id="allow-p",
                    effect="allow",
                    statements=[_make_statement(["plugin.execute"], ["plugin:shu_gmail_digest"])],
                ),
            },
            _user_policies={"user-1": {"allow-p"}},
        )
        assert await cache.check("user-1", "experience.read", "experience:abc", AsyncMock()) is False

    @pytest.mark.asyncio
    async def test_multiple_statements_in_policy(self) -> None:
        """A policy with multiple statements — allow covers listed resources, deny the rest."""
        cache = _make_cache(
            _policies={
                "allow-p": CachedPolicy(
                    id="allow-p",
                    effect="allow",
                    statements=[
                        _make_statement(["experience.read"], ["experience:a"]),
                        _make_statement(["experience.read"], ["experience:b"]),
                    ],
                ),
            },
            _user_policies={"user-1": {"allow-p"}},
        )
        assert await cache.check("user-1", "experience.read", "experience:a", AsyncMock()) is True
        assert await cache.check("user-1", "experience.read", "experience:b", AsyncMock()) is True
        assert await cache.check("user-1", "experience.read", "experience:c", AsyncMock()) is False

    @pytest.mark.asyncio
    async def test_allow_does_not_override_deny(self) -> None:
        """An allow on a different resource doesn't grant access to a denied one."""
        cache = _make_cache(
            _policies={
                "allow-p": CachedPolicy(
                    id="allow-p",
                    effect="allow",
                    statements=[_make_statement(["experience.read"], ["experience:public"])],
                ),
                "deny-p": CachedPolicy(
                    id="deny-p",
                    effect="deny",
                    statements=[_make_statement(["experience.read"], ["experience:secret"])],
                ),
            },
            _user_policies={"user-1": {"allow-p", "deny-p"}},
        )
        assert await cache.check("user-1", "experience.read", "experience:public", AsyncMock()) is True
        assert await cache.check("user-1", "experience.read", "experience:secret", AsyncMock()) is False
        # Resource with neither allow nor deny → denied (default-deny)
        assert await cache.check("user-1", "experience.read", "experience:other", AsyncMock()) is False


class TestGetDeniedResources:
    """Tests for PolicyCache.get_denied_resources()."""

    @pytest.mark.asyncio
    async def test_admin_bypass(self) -> None:
        """Admin gets empty denied set regardless of policies."""
        cache = _make_cache(
            _admin_user_ids={"admin-1"},
            _policies={
                "deny-p": CachedPolicy(
                    id="deny-p",
                    effect="deny",
                    statements=[_make_statement(["experience.read"], ["experience:*"])],
                ),
            },
            _user_policies={"admin-1": {"deny-p"}},
        )
        denied = await cache.get_denied_resources(
            "admin-1", "experience.read", "experience", ["a", "b"], AsyncMock()
        )
        assert denied == set()

    @pytest.mark.asyncio
    async def test_no_policies_denies_all(self) -> None:
        """No policies bound to user → everything denied."""
        cache = _make_cache()
        denied = await cache.get_denied_resources(
            "user-1", "experience.read", "experience", ["a", "b"], AsyncMock()
        )
        assert denied == {"a", "b"}

    @pytest.mark.asyncio
    async def test_allow_all_denies_nothing(self) -> None:
        """Broad allow policy with no deny → nothing denied."""
        cache = _make_cache(
            _policies={
                "allow-p": CachedPolicy(
                    id="allow-p",
                    effect="allow",
                    statements=[_make_statement(["experience.read"], ["experience:*"])],
                ),
            },
            _user_policies={"user-1": {"allow-p"}},
        )
        denied = await cache.get_denied_resources(
            "user-1", "experience.read", "experience", ["a", "b"], AsyncMock()
        )
        assert denied == set()

    @pytest.mark.asyncio
    async def test_deny_specific_resources(self) -> None:
        """Deny blocks specific resources; allow covers the rest."""
        cache = _make_cache(
            _policies={
                "allow-all": ALLOW_ALL,
                "deny-p": CachedPolicy(
                    id="deny-p",
                    effect="deny",
                    statements=[_make_statement(["experience.read"], ["experience:secret"])],
                ),
            },
            _user_policies={"user-1": {"allow-all", "deny-p"}},
        )
        denied = await cache.get_denied_resources(
            "user-1", "experience.read", "experience", ["secret", "public", "other"], AsyncMock()
        )
        assert denied == {"secret"}

    @pytest.mark.asyncio
    async def test_deny_wildcard_resources(self) -> None:
        """Deny policy with wildcard resource pattern."""
        cache = _make_cache(
            _policies={
                "allow-all": ALLOW_ALL,
                "deny-p": CachedPolicy(
                    id="deny-p",
                    effect="deny",
                    statements=[_make_statement(["plugin.execute"], ["plugin:shu_gmail_*"])],
                ),
            },
            _user_policies={"user-1": {"allow-all", "deny-p"}},
        )
        denied = await cache.get_denied_resources(
            "user-1",
            "plugin.execute",
            "plugin",
            ["shu_gmail_digest", "shu_gmail_send", "shu_calendar"],
            AsyncMock(),
        )
        assert denied == {"shu_gmail_digest", "shu_gmail_send"}

    @pytest.mark.asyncio
    async def test_group_policy_resolution(self) -> None:
        """Deny policy resolved via group membership."""
        cache = _make_cache(
            _policies={
                "allow-all": ALLOW_ALL,
                "deny-p": CachedPolicy(
                    id="deny-p",
                    effect="deny",
                    statements=[_make_statement(["experience.read"], ["experience:secret"])],
                ),
            },
            _group_policies={"group-eng": {"deny-p", "allow-all"}},
            _user_groups={"user-1": {"group-eng"}},
        )
        denied = await cache.get_denied_resources(
            "user-1", "experience.read", "experience", ["secret", "public"], AsyncMock()
        )
        assert denied == {"secret"}

    @pytest.mark.asyncio
    async def test_action_mismatch_denies_all(self) -> None:
        """Deny policy for a different action doesn't match, but no allow either → all denied."""
        cache = _make_cache(
            _policies={
                "deny-p": CachedPolicy(
                    id="deny-p",
                    effect="deny",
                    statements=[_make_statement(["plugin.execute"], ["experience:secret"])],
                ),
            },
            _user_policies={"user-1": {"deny-p"}},
        )
        denied = await cache.get_denied_resources(
            "user-1", "experience.read", "experience", ["public", "secret"], AsyncMock()
        )
        assert denied == {"public", "secret"}

    @pytest.mark.asyncio
    async def test_partial_allow(self) -> None:
        """Allow covers some resources; uncovered ones are denied."""
        cache = _make_cache(
            _policies={
                "allow-p": CachedPolicy(
                    id="allow-p",
                    effect="allow",
                    statements=[_make_statement(["experience.read"], ["experience:a", "experience:b"])],
                ),
            },
            _user_policies={"user-1": {"allow-p"}},
        )
        denied = await cache.get_denied_resources(
            "user-1", "experience.read", "experience", ["a", "b", "c"], AsyncMock()
        )
        assert denied == {"c"}


class TestInvalidate:
    """Tests for PolicyCache.invalidate() and staleness tracking."""

    def test_invalidate_marks_stale(self) -> None:
        """invalidate() sets the _stale flag to True."""
        cache = _make_cache()
        assert cache._stale is False
        cache.invalidate()
        assert cache._stale is True

    def test_invalidate_idempotent(self) -> None:
        """Calling invalidate() multiple times is safe."""
        cache = _make_cache()
        assert cache._stale is False
        cache.invalidate()
        assert cache._stale is True
        cache.invalidate()
        assert cache._stale is True

    @pytest.mark.asyncio
    async def test_stale_triggers_refresh(self) -> None:
        """When stale, _maybe_refresh calls _refresh."""
        cache = _make_cache()
        cache._stale = True
        mock_db = AsyncMock()
        with patch.object(cache, "_refresh", new_callable=AsyncMock) as mock_refresh:
            await cache._maybe_refresh(mock_db)
            mock_refresh.assert_awaited_once_with(mock_db)

    @pytest.mark.asyncio
    async def test_not_stale_and_fresh_skips_refresh(self) -> None:
        """When not stale and TTL has not expired, _maybe_refresh is a no-op."""
        cache = _make_cache()  # _stale=False, _last_refresh=far future
        mock_db = AsyncMock()
        with patch.object(cache, "_refresh", new_callable=AsyncMock) as mock_refresh:
            await cache._maybe_refresh(mock_db)
            mock_refresh.assert_not_awaited()


class TestTTLExpiry:
    """Tests for TTL-based cache refresh."""

    @pytest.mark.asyncio
    async def test_ttl_expired_triggers_refresh(self) -> None:
        """When TTL has elapsed (even if not stale), _maybe_refresh calls _refresh."""
        cache = _make_cache()
        cache._ttl_seconds = 1
        cache._last_refresh = time.monotonic() - 10  # well past TTL
        mock_db = AsyncMock()
        with patch.object(cache, "_refresh", new_callable=AsyncMock) as mock_refresh:
            await cache._maybe_refresh(mock_db)
            mock_refresh.assert_awaited_once_with(mock_db)

    @pytest.mark.asyncio
    async def test_ttl_not_expired_skips_refresh(self) -> None:
        """When TTL has not elapsed and cache is not stale, no refresh."""
        cache = _make_cache()
        cache._ttl_seconds = 9999
        cache._last_refresh = time.monotonic()  # just refreshed
        mock_db = AsyncMock()
        with patch.object(cache, "_refresh", new_callable=AsyncMock) as mock_refresh:
            await cache._maybe_refresh(mock_db)
            mock_refresh.assert_not_awaited()


class TestConcurrentRefresh:
    """Tests for asyncio.Lock preventing concurrent refreshes."""

    @pytest.mark.asyncio
    async def test_concurrent_refresh_prevented(self) -> None:
        """Only one _refresh runs even when multiple _maybe_refresh calls race."""
        cache = _make_cache()
        cache._stale = True
        refresh_count = 0

        async def slow_refresh(db: AsyncMock) -> None:
            nonlocal refresh_count
            refresh_count += 1
            await asyncio.sleep(0.05)
            cache._stale = False
            cache._last_refresh = time.monotonic()

        mock_db = AsyncMock()
        with patch.object(cache, "_refresh", side_effect=slow_refresh):
            await asyncio.gather(
                cache._maybe_refresh(mock_db),
                cache._maybe_refresh(mock_db),
                cache._maybe_refresh(mock_db),
            )

        assert refresh_count == 1

    @pytest.mark.asyncio
    async def test_double_check_after_lock(self) -> None:
        """After acquiring the lock, the double-check prevents redundant refresh."""
        cache = _make_cache()
        cache._stale = True
        call_count = 0

        original_refresh = cache._refresh

        async def counting_refresh(db: AsyncMock) -> None:
            nonlocal call_count
            call_count += 1
            # Simulate what _refresh does: mark not stale and update timestamp
            cache._stale = False
            cache._last_refresh = time.monotonic()

        mock_db = AsyncMock()
        with patch.object(cache, "_refresh", side_effect=counting_refresh):
            # First call refreshes
            await cache._maybe_refresh(mock_db)
            # Second call sees fresh data → skips
            await cache._maybe_refresh(mock_db)

        assert call_count == 1


class TestInactivePolicyExclusion:
    """Tests verifying that inactive policies are excluded from evaluation.

    Since _refresh only loads active policies, an inactive policy that somehow
    ends up in the cache would still be evaluated.  The real exclusion happens
    at the DB query level (is_active filter in _load_policies_and_indexes).
    These tests verify the cache only contains policies that were loaded.
    """

    @pytest.mark.asyncio
    async def test_policy_not_in_cache_is_ignored(self) -> None:
        """A policy ID in _user_policies but not in _policies dict is skipped."""
        cache = _make_cache(
            _policies={},  # no policy objects loaded
            _user_policies={"user-1": {"ghost-policy"}},
        )
        result = await cache.check("user-1", "experience.read", "experience:abc", AsyncMock())
        # ghost-policy is in the binding index but not in _policies → no match → deny
        assert result is False

    @pytest.mark.asyncio
    async def test_only_loaded_policies_evaluated(self) -> None:
        """Only policies present in _policies dict participate in evaluation."""
        allow_policy = CachedPolicy(
            id="active-allow",
            effect="allow",
            statements=[_make_statement(["experience.read"], ["experience:*"])],
        )
        cache = _make_cache(
            _policies={"active-allow": allow_policy},
            # user has binding to both an active and a missing (inactive) policy
            _user_policies={"user-1": {"active-allow", "inactive-policy"}},
        )
        result = await cache.check("user-1", "experience.read", "experience:abc", AsyncMock())
        assert result is True

    @pytest.mark.asyncio
    async def test_get_denied_resources_ignores_missing_policy(self) -> None:
        """get_denied_resources skips policy IDs not present in _policies."""
        allow_policy = CachedPolicy(
            id="active-allow",
            effect="allow",
            statements=[_make_statement(["experience.read"], ["experience:*"])],
        )
        cache = _make_cache(
            _policies={"active-allow": allow_policy},
            _user_policies={"user-1": {"active-allow", "missing-policy"}},
        )
        denied = await cache.get_denied_resources(
            "user-1", "experience.read", "experience", ["a", "b"], AsyncMock()
        )
        assert denied == set()
