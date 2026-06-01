"""Tests for shu.core.tenant.

Coverage focus:
* ``tenant_context`` is asyncio-task-local (no cross-task bleed under
  ``asyncio.gather``) — proves we picked ``ContextVar`` rather than a
  module global or threading.local.
* ``_tenant_context_for_credential`` branches per deployment mode.
* The yield-style contextmanager resets ``tenant_context`` on exit so a
  request doesn't poison the next one on the same worker.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import DBAPIError, NoResultFound

from shu.core.config import SELF_HOSTED_TENANT_UUID
from shu.core.tenant import (
    MissingTenantContextError,
    UnknownStripeCustomerError,
    UserTenantNotFoundError,
    _is_no_data_found,
    _lookup_tenant_for_reset_token,
    _lookup_tenant_for_verification_token,
    for_each_tenant_in_deployment,
    tenant_context,
    tenant_context_for_email,
    tenant_context_for_reset_token,
    tenant_context_for_stripe_customer,
    tenant_context_for_tenant_id,
    tenant_context_for_user_id,
    tenant_context_for_verification_token,
)

# ---------------------------------------------------------------------------
# 16.4 — tenant_context is asyncio-task-local
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_context_does_not_bleed_across_concurrent_tasks() -> None:
    """Two tasks running under ``asyncio.gather`` must each see their own
    tenant_id. Bleed would indicate we accidentally wired a global/threading
    primitive instead of ``ContextVar``."""

    async def _task(tid: str, observed: list[str]) -> None:
        async with tenant_context_for_tenant_id(tid):
            # Yield control so the scheduler is free to interleave with the
            # sibling task — that's the moment a global would be observed
            # at the wrong value.
            await asyncio.sleep(0)
            observed.append(tenant_context.get(None))
            await asyncio.sleep(0)
            observed.append(tenant_context.get(None))

    obs_a: list[str] = []
    obs_b: list[str] = []
    await asyncio.gather(
        _task("tenant-A", obs_a),
        _task("tenant-B", obs_b),
    )

    assert obs_a == ["tenant-A", "tenant-A"]
    assert obs_b == ["tenant-B", "tenant-B"]


# ---------------------------------------------------------------------------
# 16.6 — Resolver branches per deployment mode
# ---------------------------------------------------------------------------


def _settings(
    mode: str, *, tenant_id: str | None = None, redis_namespace: str | None = None
) -> SimpleNamespace:
    """Build the minimum Settings-shaped stub the resolver reads.

    We use SimpleNamespace rather than the real Settings because hitting the
    real validator chain for each test is unnecessary noise — the resolver
    only reads ``deployment_mode``, ``tenant_id``, and (for the
    redis-namespace helper) ``redis_namespace``.
    """
    from shu.core.config import DeploymentMode

    return SimpleNamespace(
        deployment_mode=DeploymentMode(mode),
        tenant_id=tenant_id,
        redis_namespace=redis_namespace,
    )


@pytest.mark.asyncio
async def test_self_hosted_short_circuits_to_constant() -> None:
    """SELF_HOSTED: deployment constant wins regardless of which credential
    is supplied. The pre-auth lookups must NOT be called — they'd hit the
    DB needlessly and (in tests) trip on the missing SECURITY DEFINER fn."""
    with patch("shu.core.tenant.get_settings_instance", return_value=_settings("self_hosted")):
        async with tenant_context_for_email("anything@example.com") as tid:
            assert tid == SELF_HOSTED_TENANT_UUID
            assert tenant_context.get(None) == SELF_HOSTED_TENANT_UUID


@pytest.mark.asyncio
async def test_silo_short_circuits_to_settings_tenant_id() -> None:
    """SILO: every request maps to the single configured tenant — same
    reasoning as self-hosted, but the UUID comes from env config."""
    with patch(
        "shu.core.tenant.get_settings_instance",
        return_value=_settings("silo", tenant_id="silo-tenant-uuid"),
    ):
        async with tenant_context_for_user_id("user-123") as tid:
            assert tid == "silo-tenant-uuid"


@pytest.mark.asyncio
async def test_multi_tenant_invokes_sd_lookup_per_credential() -> None:
    """MULTI_TENANT: the user_id path routes to ``_lookup_tenant_for_user``;
    the email path routes to ``_lookup_tenant_for_email``. Each must hit the
    SECURITY DEFINER fn and propagate the returned tenant_id."""
    with (
        patch("shu.core.tenant.get_settings_instance", return_value=_settings("multi_tenant")),
        patch("shu.core.tenant._lookup_tenant_for_user", new=AsyncMock(return_value="from-uid")),
        patch("shu.core.tenant._lookup_tenant_for_email", new=AsyncMock(return_value="from-email")),
    ):
        async with tenant_context_for_user_id("user-123") as tid:
            assert tid == "from-uid"

        async with tenant_context_for_email("x@example.com") as tid:
            assert tid == "from-email"


@pytest.mark.asyncio
async def test_user_id_lookup_miss_raises_typed_exception() -> None:
    """Deleted user with an active JWT: the SD lookup returns None.
    Translate that to ``UserTenantNotFoundError`` so the auth dependency /
    middleware can 401 instead of falling through to RLS default-deny."""
    with (
        patch("shu.core.tenant.get_settings_instance", return_value=_settings("multi_tenant")),
        patch("shu.core.tenant._lookup_tenant_for_user", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(UserTenantNotFoundError):
            async with tenant_context_for_user_id("deleted-user-id"):
                pytest.fail("body should not run when resolver fails")


@pytest.mark.asyncio
async def test_stripe_customer_lookup_miss_raises_typed_exception() -> None:
    """Webhook arrives with a customer_id we've never billed (deleted
    tenant, leaked event). The legacy silent-None acked 200; raising lets
    the route surface 409 so Stripe stops retrying but the event stays
    visible in the dashboard."""
    with (
        patch("shu.core.tenant.get_settings_instance", return_value=_settings("multi_tenant")),
        patch("shu.core.tenant._lookup_tenant_for_stripe_customer", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(UnknownStripeCustomerError):
            async with tenant_context_for_stripe_customer("cus_unknown"):
                pytest.fail("body should not run when resolver fails")


@pytest.mark.asyncio
async def test_email_lookup_miss_does_not_raise() -> None:
    """Email is the carve-out: login with an unknown email already produces
    the right UX (generic 401 from the downstream user-lookup running under
    RLS-deny). Raising here would force every login route to catch and
    convert. Don't translate the None."""
    with (
        patch("shu.core.tenant.get_settings_instance", return_value=_settings("multi_tenant")),
        patch("shu.core.tenant._lookup_tenant_for_email", new=AsyncMock(return_value=None)),
    ):
        async with tenant_context_for_email("nobody@example.com") as tid:
            assert tid is None


# ---------------------------------------------------------------------------
# 16.7 — resolve_tenant resets on exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_resets_to_prior_value_on_clean_exit() -> None:
    """The autouse conftest fixture pre-sets a sentinel tenant; the
    contextmanager has to restore it (not None) after the block ends.
    A reset-to-None would break the next test in the same worker."""
    prior = tenant_context.get(None)
    with patch("shu.core.tenant.get_settings_instance", return_value=_settings("self_hosted")):
        async with tenant_context_for_email("x@example.com"):
            assert tenant_context.get(None) == SELF_HOSTED_TENANT_UUID
    assert tenant_context.get(None) == prior


@pytest.mark.asyncio
async def test_uuid_tenant_id_is_normalized_to_str_in_context() -> None:
    """SHU-823 regression: a uuid.UUID tenant_id (e.g. from a raw SELECT over
    the uuid-typed tenants.id, or a worker job payload) must be coerced to str
    before landing in tenant_context. The contextvar is ``str | None`` and the
    before_flush guard compares it against the str ``Uuid(as_uuid=False)``
    column; a UUID context made `str != UUID` true on every tenant-scoped flush
    and broke the scheduler fan-out."""
    tid = uuid.UUID(SELF_HOSTED_TENANT_UUID)
    async with tenant_context_for_tenant_id(tid) as resolved:
        ctx = tenant_context.get(None)
        assert isinstance(ctx, str)
        assert ctx == SELF_HOSTED_TENANT_UUID
        # The yielded value is normalized too, so callers that capture it
        # (e.g. for logging or as a dict key) get the same str representation.
        assert isinstance(resolved, str)
        assert resolved == SELF_HOSTED_TENANT_UUID


@pytest.mark.asyncio
async def test_empty_string_tenant_id_routes_like_none() -> None:
    """SHU-825 + review: an empty-string tenant_id must be treated exactly like None
    — routed to deployment-mode resolution, NOT taken as a literal explicit tenant
    (which previously collapsed to a None context that silently mis-ran jobs). In
    self-hosted it resolves to the deployment tenant; in multi-tenant it raises the
    same MissingTenantContextError that makes an empty-tenant worker job a poison
    pill. Either way it never lands a literal '' in the contextvar (which would 500
    on ''::uuid via the begin hook)."""
    # Self-hosted: '' resolves to the deployment tenant, identical to None.
    with patch("shu.core.tenant.get_settings_instance", return_value=_settings("self_hosted")):
        async with tenant_context_for_tenant_id("") as resolved:
            assert resolved == SELF_HOSTED_TENANT_UUID
            assert tenant_context.get(None) == SELF_HOSTED_TENANT_UUID
    # Multi-tenant: '' carries no credential, so it raises (poison-pill path), same as None.
    with patch("shu.core.tenant.get_settings_instance", return_value=_settings("multi_tenant")):
        with pytest.raises(MissingTenantContextError):
            async with tenant_context_for_tenant_id(""):
                pytest.fail("empty-string tenant must not yield a usable context in multi-tenant")


@pytest.mark.asyncio
async def test_context_resets_even_when_body_raises() -> None:
    """If the route handler raises mid-request, the context must still pop —
    otherwise the next request on this asyncio task / thread inherits the
    stale tenant_id."""
    prior = tenant_context.get(None)

    class Boom(RuntimeError):
        pass

    with (
        patch("shu.core.tenant.get_settings_instance", return_value=_settings("self_hosted")),
        pytest.raises(Boom),
    ):
        async with tenant_context_for_email("x@example.com"):
            raise Boom("simulate handler failure mid-request")

    assert tenant_context.get(None) == prior


# ---------------------------------------------------------------------------
# resolve_redis_namespace
#
# Static deployment-level Redis key namespace, resolved once at engine
# construction. Distinct from resolve_tenant_for_infra (which is per-call
# and raises in MT without context); the namespace's only job is collision
# avoidance between deployments sharing one Redis, so it must never need
# tenant_context.
# ---------------------------------------------------------------------------


class TestResolveRedisNamespace:
    def test_self_hosted_returns_constant(self) -> None:
        from shu.core.tenant import resolve_redis_namespace

        with patch("shu.core.tenant.get_settings_instance", return_value=_settings("self_hosted")):
            assert resolve_redis_namespace() == SELF_HOSTED_TENANT_UUID

    def test_silo_returns_settings_tenant_id(self) -> None:
        from shu.core.tenant import resolve_redis_namespace

        stub = _settings("silo", tenant_id="silo-uuid")
        with patch("shu.core.tenant.get_settings_instance", return_value=stub):
            assert resolve_redis_namespace() == "silo-uuid"

    def test_multi_tenant_returns_literal_default(self) -> None:
        from shu.core.tenant import resolve_redis_namespace

        with patch("shu.core.tenant.get_settings_instance", return_value=_settings("multi_tenant")):
            assert resolve_redis_namespace() == "multitenant"

    def test_explicit_override_wins_in_every_mode(self) -> None:
        """SHU_REDIS_NAMESPACE supersedes the per-mode default — the operator
        escape hatch for the rare case of two deployments sharing Redis."""
        from shu.core.tenant import resolve_redis_namespace

        for mode in ("self_hosted", "silo", "multi_tenant"):
            stub = _settings(
                mode,
                tenant_id="silo-uuid" if mode == "silo" else None,
                redis_namespace="explicit-override",
            )
            with patch("shu.core.tenant.get_settings_instance", return_value=stub):
                assert resolve_redis_namespace() == "explicit-override"

    def test_does_not_consult_tenant_context(self) -> None:
        """Crucially, the helper does not read tenant_context. The worker poll
        loop calls into queue keys without a context set — if the namespace
        path depended on it, dequeue would crash. Pin the invariant."""
        from shu.core.tenant import resolve_redis_namespace

        token = tenant_context.set(None)
        try:
            with patch(
                "shu.core.tenant.get_settings_instance",
                return_value=_settings("multi_tenant"),
            ):
                # Would have raised MissingTenantContextError if implementation
                # accidentally reached for tenant_context.
                assert resolve_redis_namespace() == "multitenant"
        finally:
            tenant_context.reset(token)


# ---------------------------------------------------------------------------
# for_each_tenant_in_deployment — per-tenant fan-out helper
# ---------------------------------------------------------------------------


class TestForEachTenantInDeployment:
    """The helper drives every per-tenant fan-out site (scheduler tick,
    startup KB-stale detection, mark-stale-imports). What we pin here is the
    contract callers depend on: ``work`` is invoked once per tenant with
    ``tenant_context`` set to that tid, and the context resets between
    invocations and on exception. Drift is a silent RLS-default-deny."""

    @pytest.mark.asyncio
    async def test_invokes_work_once_per_tenant(self) -> None:
        fake_tenants = ["tenant-A", "tenant-B", "tenant-C"]
        observed: list[str] = []

        async def work(tid: str) -> None:
            observed.append(tid)

        with patch(
            "shu.core.worker.list_all_tenant_ids",
            new=AsyncMock(return_value=fake_tenants),
        ):
            await for_each_tenant_in_deployment(work)
        assert observed == fake_tenants

    @pytest.mark.asyncio
    async def test_empty_catalog_invokes_nothing(self) -> None:
        calls: list[str] = []

        async def work(tid: str) -> None:
            calls.append(tid)

        with patch(
            "shu.core.worker.list_all_tenant_ids",
            new=AsyncMock(return_value=[]),
        ):
            await for_each_tenant_in_deployment(work)
        assert calls == []

    @pytest.mark.asyncio
    async def test_tenant_context_is_set_inside_work(self) -> None:
        observed_ctx: list[str | None] = []

        async def work(tid: str) -> None:
            observed_ctx.append(tenant_context.get(None))
            assert tenant_context.get(None) == tid

        with patch(
            "shu.core.worker.list_all_tenant_ids",
            new=AsyncMock(return_value=["a", "b", "c"]),
        ):
            await for_each_tenant_in_deployment(work)
        assert observed_ctx == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_context_resets_to_prior_value_after_completion(self) -> None:
        async def work(tid: str) -> None:
            pass

        token = tenant_context.set("prior")
        try:
            with patch(
                "shu.core.worker.list_all_tenant_ids",
                new=AsyncMock(return_value=["a", "b"]),
            ):
                await for_each_tenant_in_deployment(work)
            assert tenant_context.get(None) == "prior"
        finally:
            tenant_context.reset(token)

    @pytest.mark.asyncio
    async def test_context_resets_when_work_raises(self) -> None:
        async def work(tid: str) -> None:
            raise RuntimeError("boom")

        token = tenant_context.set("prior")
        try:
            with patch(
                "shu.core.worker.list_all_tenant_ids",
                new=AsyncMock(return_value=["a", "b"]),
            ):
                with pytest.raises(RuntimeError, match="boom"):
                    await for_each_tenant_in_deployment(work)
            assert tenant_context.get(None) == "prior"
        finally:
            tenant_context.reset(token)


# ---------------------------------------------------------------------------
# SSO callback path — verified email from IdP funnels through the same
# tenant_context_for_email contract login uses
# ---------------------------------------------------------------------------


def _multi_tenant_settings() -> SimpleNamespace:
    from shu.core.config import DeploymentMode

    return SimpleNamespace(deployment_mode=DeploymentMode.MULTI_TENANT, tenant_id=None)


@pytest.mark.asyncio
async def test_sso_callback_sets_context_via_email() -> None:
    """SSO callbacks have a verified email from the IdP and use
    ``tenant_context_for_email`` to scope subsequent reads/writes —
    identical mechanics to the login form path. The fact that the email
    is IdP-verified rather than form-submitted doesn't change the
    resolver contract."""
    observed: list[str | None] = []

    with patch("shu.core.tenant.get_settings_instance", return_value=_settings("self_hosted")):
        async with tenant_context_for_email("sso-verified@example.com") as tid:
            observed.append(tenant_context.get(None))
            assert tid is not None

    assert observed and observed[0] is not None


@pytest.mark.asyncio
async def test_sso_callback_propagates_no_account_in_multi_tenant() -> None:
    """A first-time SSO sign-in for an email that isn't registered in any
    tenant must not crash with an RLS-related traceback. The SD-function
    ``INTO STRICT`` raises ``NoResultFound``; the upstream auth handler is
    responsible for catching and translating (auto-register, error, etc.)
    — we just pin the propagation contract here."""
    with (
        patch("shu.core.tenant.get_settings_instance", return_value=_multi_tenant_settings()),
        patch(
            "shu.core.tenant._lookup_tenant_for_email",
            new=AsyncMock(side_effect=NoResultFound("unknown")),
        ),
        pytest.raises(NoResultFound),
    ):
        async with tenant_context_for_email("new-user@example.com"):
            pass  # pragma: no cover


# ---------------------------------------------------------------------------
# Multi-tenant pre-auth resolver coverage (SHU-761 16.14 / 16.15)
#
# Five credential shapes flow into the same internal helper
# ``_tenant_context_for_credential`` and each one has its own
# ``_lookup_tenant_for_*`` SECURITY-DEFINER-backed function. We pin two
# properties per credential:
#
# * Miss propagates (16.14): when the underlying lookup raises (e.g.,
#   ``NoResultFound`` from the SD function's ``INTO STRICT``), the
#   contextmanager re-raises rather than swallowing.
# * Hit sets context to the looked-up value (16.15): the tenant returned
#   from the SD function is what subsequent queries see via
#   ``tenant_context.get()``.
#
# Mock at the ``_lookup_tenant_for_*`` boundary rather than spinning up a
# real DB session — the SD functions themselves are migration-deployed
# code that the unit-test environment doesn't run.
# ---------------------------------------------------------------------------


# Each row drives a parametrized test across all five credential shapes.
# Tuple shape: credential argument value, contextmanager factory under test,
# name of the lookup attribute on shu.core.tenant to monkeypatch.
_CREDENTIAL_MATRIX = [
    ("user-123", tenant_context_for_user_id, "_lookup_tenant_for_user"),
    ("login@example.com", tenant_context_for_email, "_lookup_tenant_for_email"),
    ("sha-of-reset-token", tenant_context_for_reset_token, "_lookup_tenant_for_reset_token"),
    (
        "sha-of-verification",
        tenant_context_for_verification_token,
        "_lookup_tenant_for_verification_token",
    ),
    ("cus_abc123", tenant_context_for_stripe_customer, "_lookup_tenant_for_stripe_customer"),
]


@pytest.mark.parametrize("credential,factory,lookup_attr", _CREDENTIAL_MATRIX)
@pytest.mark.asyncio
async def test_resolver_propagates_lookup_miss(
    credential: str, factory, lookup_attr: str
) -> None:
    """16.14: NoResultFound from the SD function must propagate, not be
    swallowed. The auth handler upstream is what translates this into the
    enumeration-resistant 401/410/200 the user sees."""
    with (
        patch("shu.core.tenant.get_settings_instance", return_value=_multi_tenant_settings()),
        patch(
            f"shu.core.tenant.{lookup_attr}",
            new=AsyncMock(side_effect=NoResultFound("no row")),
        ),
        pytest.raises(NoResultFound),
    ):
        async with factory(credential):
            pass  # pragma: no cover - never reached


@pytest.mark.parametrize("credential,factory,lookup_attr", _CREDENTIAL_MATRIX)
@pytest.mark.asyncio
async def test_resolver_sets_context_to_looked_up_tenant(
    credential: str, factory, lookup_attr: str
) -> None:
    """16.15: a successful lookup must set ``tenant_context`` to the
    returned tenant_id for the body of the with-block. A resolver that
    returned the value without setting context would silently dump
    multi-tenant work into the default-deny zero-row set."""
    observed: list[str | None] = []
    with (
        patch("shu.core.tenant.get_settings_instance", return_value=_multi_tenant_settings()),
        patch(f"shu.core.tenant.{lookup_attr}", new=AsyncMock(return_value="looked-up-tenant")),
    ):
        async with factory(credential) as yielded_tid:
            observed.append(tenant_context.get(None))
            assert yielded_tid == "looked-up-tenant"

    assert observed == ["looked-up-tenant"]


@pytest.mark.parametrize("credential,factory,lookup_attr", _CREDENTIAL_MATRIX)
@pytest.mark.asyncio
async def test_resolver_resets_context_after_miss(
    credential: str, factory, lookup_attr: str
) -> None:
    """When the lookup raises, the contextmanager body never runs and the
    surrounding context must not be modified — otherwise a failed
    enumerable lookup could clobber the autouse-fixture tenant and break
    subsequent unrelated test assertions (and in prod, leak between
    requests on the same task)."""
    prior = tenant_context.get(None)
    with (
        patch("shu.core.tenant.get_settings_instance", return_value=_multi_tenant_settings()),
        patch(
            f"shu.core.tenant.{lookup_attr}",
            new=AsyncMock(side_effect=NoResultFound("no row")),
        ),
        pytest.raises(NoResultFound),
    ):
        async with factory(credential):
            pass  # pragma: no cover

    assert tenant_context.get(None) == prior


# ---------------------------------------------------------------------------
# Token-lookup no-data translation
#
# Defense-in-depth coverage for the case where tenant_for_reset_token /
# tenant_for_verification_token is reverted to PL/pgSQL ``INTO STRICT``
# (which raises NO_DATA_FOUND on miss). The lookups catch the no-data
# shape and return None; ``tenant_context`` ends up unset, the service-
# layer token read sees zero rows under RLS, and the existing 400 path
# fires. Today the SD functions are plain SQL so this branch never
# triggers in production — the test pins the safety net.
# ---------------------------------------------------------------------------


class _FakeNoDataDBAPIError(DBAPIError):
    """DBAPIError shaped like asyncpg's NoDataFoundError unwrap.

    Subclassing DBAPIError so the lookup's broad ``except DBAPIError`` catches
    it; setting a minimal ``orig`` whose ``sqlstate`` is the no-data SQLSTATE
    P0002 so ``_is_no_data_found`` returns True.
    """

    def __init__(self) -> None:
        class _Orig(Exception):
            sqlstate = "P0002"

        super().__init__("SELECT", {}, _Orig())


@pytest.mark.parametrize(
    "lookup,arg",
    [
        (_lookup_tenant_for_reset_token, "sha-of-reset-token"),
        (_lookup_tenant_for_verification_token, "sha-of-verification"),
    ],
)
@pytest.mark.asyncio
async def test_token_lookup_returns_none_on_no_data_error(lookup, arg) -> None:
    """A NO_DATA_FOUND from the PL/pgSQL SD function must translate to None,
    not propagate to the route handler as a 500."""
    from unittest.mock import MagicMock

    fake_session = AsyncMock()
    fake_session.execute = AsyncMock(side_effect=_FakeNoDataDBAPIError())
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    session_cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session_cm)

    with patch("shu.core.tenant._open_app_session", new=factory):
        result = await lookup(arg)

    assert result is None


@pytest.mark.parametrize(
    "lookup,arg",
    [
        (_lookup_tenant_for_reset_token, "sha-of-reset-token"),
        (_lookup_tenant_for_verification_token, "sha-of-verification"),
    ],
)
@pytest.mark.asyncio
async def test_token_lookup_returns_none_on_scalar_one_no_result(lookup, arg) -> None:
    """SQLAlchemy ``scalar_one()`` raises ``NoResultFound`` on zero rows.
    The token lookups catch that shape too (same no-token-found semantics
    as a NO_DATA_FOUND from the SD function)."""
    from unittest.mock import MagicMock

    fake_session = AsyncMock()
    fake_session.execute = AsyncMock(side_effect=NoResultFound("no row"))
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    session_cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session_cm)

    with patch("shu.core.tenant._open_app_session", new=factory):
        result = await lookup(arg)

    assert result is None


@pytest.mark.asyncio
async def test_token_lookup_propagates_non_no_data_dbapi_errors() -> None:
    """A genuine DB outage (anything other than NO_DATA_FOUND) must NOT be
    silently translated to None — that would mask the outage as
    'invalid token' and hide real infra failures from operators."""
    from unittest.mock import MagicMock

    class _ConnectionLostError(Exception):
        sqlstate = "08006"  # connection_failure

    class _RealDBAPIError(DBAPIError):
        def __init__(self) -> None:
            super().__init__("SELECT", {}, _ConnectionLostError())

    fake_session = AsyncMock()
    fake_session.execute = AsyncMock(side_effect=_RealDBAPIError())
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    session_cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session_cm)

    with patch("shu.core.tenant._open_app_session", new=factory), pytest.raises(DBAPIError):
        await _lookup_tenant_for_reset_token("sha-of-reset-token")


def test_is_no_data_found_helper_recognizes_p0002() -> None:
    class _Orig(Exception):
        sqlstate = "P0002"

    err = DBAPIError("SELECT", {}, _Orig())
    assert _is_no_data_found(err) is True


def test_is_no_data_found_helper_rejects_other_sqlstates() -> None:
    class _Orig(Exception):
        sqlstate = "23505"  # unique_violation

    err = DBAPIError("SELECT", {}, _Orig())
    assert _is_no_data_found(err) is False
