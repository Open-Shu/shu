"""Tests for shu.services.tenant_admin_service.

Coverage focus:
* Audit emission ordering (open BEFORE session yield; fail-closed if open
  audit raises).
* Tenant context is set during impersonation and cleared after.
* Cross-tenant path uses the admin session factory and does NOT set context.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.auth.password_auth import PasswordAuthService
from shu.billing.state_service import BillingStateService
from shu.core.exceptions import ConflictError
from shu.core.tenant import tenant_context
from shu.schemas.cp_provisioning import (
    BillingInput,
    CreateTenantRequest,
    UserInput,
)
from shu.services.audit_logger import AuditLogEmitError
from shu.services.password_reset_service import PasswordResetService
from shu.services.tenant_admin_service import TenantAdminService


def _stub_session_factory() -> tuple[MagicMock, MagicMock]:
    """Return (factory, session) with the async-context-manager dance wired up.

    The real ``async_sessionmaker`` returns an object that supports
    ``async with``; we mirror that with MagicMocks so the service code's
    ``async with self._app_session_local() as session`` works against the
    stub without standing up a real engine.
    """
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session)
    return factory, session


def _build_svc(
    *,
    app_factory: MagicMock,
    admin_factory: MagicMock,
    audit: AsyncMock,
    password_auth: MagicMock | None = None,
    password_reset: MagicMock | None = None,
) -> TenantAdminService:
    """Construct a TenantAdminService with all required deps.

    The non-CP tests (impersonate_tenant / cross_tenant_query) don't exercise
    the password collaborators but the constructor requires them — pass
    plain ``MagicMock()`` placeholders when the test doesn't care.
    """
    return TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
        password_auth=password_auth or MagicMock(spec=PasswordAuthService),
        password_reset=password_reset or MagicMock(spec=PasswordResetService),
    )


@pytest.mark.asyncio
async def test_impersonate_tenant_sets_context_during_session() -> None:
    """Inside the contextmanager the tenant_context ContextVar must be the target."""
    app_factory, app_session = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()

    svc = _build_svc(app_factory=app_factory, admin_factory=admin_factory, audit=audit)

    # The autouse conftest fixture pre-sets tenant_context to a default
    # value, so we snapshot it and assert restoration relative to that
    # rather than to None.
    pre_context = tenant_context.get(None)
    async with svc.impersonate_tenant("tenant-X", "actor-1", "ticket 7") as session:
        assert tenant_context.get(None) == "tenant-X"
        assert session is app_session
    # Reset on exit — otherwise a subsequent admin operation would inherit it.
    assert tenant_context.get(None) == pre_context


@pytest.mark.asyncio
async def test_impersonate_tenant_audits_open_before_session_opens() -> None:
    """If the open audit raises, the session factory must not be called.

    This is the fail-closed contract: no cross-tenant work happens without
    a durable audit record.
    """
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()
    audit.log.side_effect = AuditLogEmitError("transport down")

    svc = _build_svc(app_factory=app_factory, admin_factory=admin_factory, audit=audit)

    pre_context = tenant_context.get(None)
    with pytest.raises(AuditLogEmitError):
        async with svc.impersonate_tenant("tenant-X", "actor-1", "ticket 7"):
            pass  # pragma: no cover - should never reach here

    app_factory.assert_not_called()
    # Context must NOT leak past the failed open — the contextmanager that
    # would have set it never got entered.
    assert tenant_context.get(None) == pre_context


@pytest.mark.asyncio
async def test_impersonate_tenant_emits_open_and_close_records() -> None:
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()

    svc = _build_svc(app_factory=app_factory, admin_factory=admin_factory, audit=audit)

    async with svc.impersonate_tenant("tenant-X", "actor-1", "ticket 7"):
        pass

    open_call, close_call = audit.log.call_args_list
    open_kwargs: dict[str, Any] = open_call.kwargs
    assert open_kwargs["event"] == "impersonate_tenant_open"
    assert open_kwargs["target"] == "tenant-X"
    assert open_kwargs["actor"] == "actor-1"
    assert open_kwargs["reason"] == "ticket 7"
    close_kwargs: dict[str, Any] = close_call.kwargs
    assert close_kwargs["event"] == "impersonate_tenant_close"
    assert close_kwargs["actor"] == "actor-1"


@pytest.mark.asyncio
async def test_cross_tenant_query_uses_admin_factory_and_no_context() -> None:
    """The cross-tenant path must NOT set tenant_context — BYPASSRLS ignores it
    and setting it would mislead a reader of the SQL into thinking it scopes."""
    app_factory, _ = _stub_session_factory()
    admin_factory, admin_session = _stub_session_factory()
    audit = AsyncMock()

    svc = _build_svc(app_factory=app_factory, admin_factory=admin_factory, audit=audit)

    # The cross-tenant path must not *change* the context — whatever the
    # caller had set going in remains set throughout, because BYPASSRLS
    # makes the value irrelevant for the query semantics. We assert the
    # path leaves the pre-existing value untouched rather than asserting
    # None (the conftest pre-populates context for every test).
    pre_context = tenant_context.get(None)
    async with svc.cross_tenant_query("actor-1", "usage report Q1") as session:
        assert session is admin_session
        assert tenant_context.get(None) == pre_context

    app_factory.assert_not_called()
    admin_factory.assert_called_once()


@pytest.mark.asyncio
async def test_cross_tenant_query_audits_open_before_session_opens() -> None:
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()
    audit.log.side_effect = AuditLogEmitError("transport down")

    svc = _build_svc(app_factory=app_factory, admin_factory=admin_factory, audit=audit)

    with pytest.raises(AuditLogEmitError):
        async with svc.cross_tenant_query("actor-1", "usage report Q1"):
            pass  # pragma: no cover

    admin_factory.assert_not_called()


# ---------------------------------------------------------------------------
# Exit-audit coverage
#
# Every open audit must be matched by a close-or-aborted exit audit, even
# when the caller's body raises. And when audit emission itself fails
# during unwind, the caller's original exception must surface — the
# audit-transport error must not mask it.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_impersonate_tenant_emits_aborted_event_when_body_raises() -> None:
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()

    svc = _build_svc(app_factory=app_factory, admin_factory=admin_factory, audit=audit)

    class _CustomError(RuntimeError):
        pass

    with pytest.raises(_CustomError):
        async with svc.impersonate_tenant("tenant-X", "actor-1", "ticket 7"):
            raise _CustomError("simulated failure in body")

    open_call, exit_call = audit.log.call_args_list
    assert open_call.kwargs["event"] == "impersonate_tenant_open"
    assert exit_call.kwargs["event"] == "impersonate_tenant_aborted"
    assert exit_call.kwargs["error_class"] == "_CustomError"
    # Target carried through so the audit trail can be filtered by tenant.
    assert exit_call.kwargs["target"] == "tenant-X"


@pytest.mark.asyncio
async def test_cross_tenant_query_emits_aborted_event_when_body_raises() -> None:
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()

    svc = _build_svc(app_factory=app_factory, admin_factory=admin_factory, audit=audit)

    class _CustomError(RuntimeError):
        pass

    with pytest.raises(_CustomError):
        async with svc.cross_tenant_query("actor-1", "usage report"):
            raise _CustomError("simulated failure")

    open_call, exit_call = audit.log.call_args_list
    assert open_call.kwargs["event"] == "cross_tenant_query_open"
    assert exit_call.kwargs["event"] == "cross_tenant_query_aborted"
    assert exit_call.kwargs["error_class"] == "_CustomError"


@pytest.mark.asyncio
async def test_aborted_audit_failure_does_not_mask_original_exception() -> None:
    """When close-time audit itself raises while a caller exception is
    in flight, the caller's exception must be the one that surfaces —
    otherwise the operator chases the audit infra blip instead of the
    actual failure that triggered the cross-tenant rollback."""
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()

    # First call (open) succeeds; second call (aborted) raises.
    audit.log.side_effect = [None, AuditLogEmitError("transport hiccup on close")]

    svc = _build_svc(app_factory=app_factory, admin_factory=admin_factory, audit=audit)

    class _OriginalError(RuntimeError):
        pass

    with pytest.raises(_OriginalError):
        async with svc.impersonate_tenant("tenant-X", "actor-1", "ticket"):
            raise _OriginalError("the real reason this failed")


# ---------------------------------------------------------------------------
# create_tenant (SHU-785) — covers happy path, idempotency, the Stripe
# identity-immutability 409 path, and the atomic-rollback contract.
# ---------------------------------------------------------------------------


def _make_billing(**overrides: Any) -> BillingInput:
    defaults: dict[str, Any] = {}
    defaults.update(overrides)
    return BillingInput(**defaults)


def _make_payload(
    *,
    tenant_id: str = "tenant-1",
    email: str = "user@example.com",
    **billing_overrides: Any,
) -> CreateTenantRequest:
    return CreateTenantRequest(
        tenant_id=tenant_id,
        billing=_make_billing(**billing_overrides),
        user=UserInput(email=email, name="Alice"),
        reason="seed test",
    )


def _make_create_tenant_svc(
    *,
    existing_tenant: Any = None,
    existing_user: Any = None,
) -> tuple[TenantAdminService, MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]:
    """Build a TenantAdminService wired with mocks for create_tenant.

    Returns (svc, audit, admin_session, app_session, password_auth, password_reset).
    The audit mock and per-session mocks let individual tests assert call patterns.
    """
    app_factory, app_session = _stub_session_factory()
    admin_factory, admin_session = _stub_session_factory()
    audit = AsyncMock()

    tenant_result = MagicMock()
    tenant_result.scalar_one_or_none = MagicMock(return_value=existing_tenant)
    admin_session.execute = AsyncMock(return_value=tenant_result)
    admin_session.add = MagicMock()
    admin_session.commit = AsyncMock()

    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=existing_user)
    app_session.execute = AsyncMock(return_value=user_result)
    app_session.commit = AsyncMock()

    password_auth = MagicMock(spec=PasswordAuthService)
    password_auth.generate_temporary_password = MagicMock(return_value="TempPass1!")
    new_user = MagicMock()
    new_user.id = "user-uuid-1"
    password_auth.create_user = AsyncMock(return_value=new_user)

    password_reset = MagicMock(spec=PasswordResetService)
    password_reset.request_reset = AsyncMock()

    svc = _build_svc(
        app_factory=app_factory,
        admin_factory=admin_factory,
        audit=audit,
        password_auth=password_auth,
        password_reset=password_reset,
    )
    return svc, audit, admin_session, app_session, password_auth, password_reset


def _patch_kb_ensure() -> Any:
    """Patch the personal-KB ensure call inside create_tenant.

    create_tenant invokes ``KnowledgeBaseService(...).ensure_personal_knowledge_base``
    unconditionally after the user/billing commit (idempotent self-heal on
    retry). Tests patch the class at the import site so the side-effects of
    real KB creation (slug generation, defaults lookup, ORM flush) don't
    leak into the unit test.
    """
    kb_svc_mock = MagicMock()
    kb_svc_mock.ensure_personal_knowledge_base = AsyncMock()
    return patch(
        "shu.services.tenant_admin_service.KnowledgeBaseService",
        return_value=kb_svc_mock,
    ), kb_svc_mock


@pytest.mark.asyncio
async def test_create_tenant_happy_path() -> None:
    """All four steps fire in order; response carries the right flags."""
    svc, audit, admin_session, app_session, password_auth, password_reset = (
        _make_create_tenant_svc()
    )
    fresh_state = MagicMock()
    fresh_state.stripe_customer_id = None
    fresh_state.stripe_subscription_id = None

    kb_patcher, kb_svc_mock = _patch_kb_ensure()
    with (
        patch.object(BillingStateService, "ensure_exists", new_callable=AsyncMock) as ensure_exists,
        patch.object(BillingStateService, "update", new_callable=AsyncMock) as update,
        kb_patcher,
    ):
        ensure_exists.return_value = (fresh_state, True)

        response = await svc.create_tenant(
            _make_payload(stripe_customer_id="cus_123"),
            reason="seed test",
        )

    assert response.tenant_id == "tenant-1"
    assert response.user_id == "user-uuid-1"
    assert response.welcome_email_sent is True
    assert response.billing_state_created is True

    # Tenant row was inserted (admin session got an `add` + commit).
    admin_session.add.assert_called_once()
    admin_session.commit.assert_awaited_once()

    # billing_state went through the service path, not direct ORM.
    ensure_exists.assert_awaited_once()
    update.assert_awaited_once()
    update_kwargs = update.await_args.kwargs
    assert update_kwargs["updates"]["stripe_customer_id"] == "cus_123"
    assert update_kwargs["updates"]["user_limit_enforcement"] == "hard"
    assert update_kwargs["source"] == "cp:provision"

    # User created with admin_created=True so the first-user auto-promote
    # path can't fire. flush_only=True keeps user pending until commit so
    # a request_reset failure rolls it back (atomicity contract).
    password_auth.create_user.assert_awaited_once()
    create_kwargs = password_auth.create_user.await_args.kwargs
    assert create_kwargs["admin_created"] is True
    assert create_kwargs["role"] == "regular_user"
    assert create_kwargs["flush_only"] is True

    # Welcome email queued, INSIDE the impersonate txn so a failure rolls
    # the just-flushed user back.
    password_reset.request_reset.assert_awaited_once()

    # Personal KB ensured AFTER the user commit, idempotent on retry.
    kb_svc_mock.ensure_personal_knowledge_base.assert_awaited_once()

    # The atomic-commit-then-KB-then-end ordering: app_session.commit must
    # fire once (the user/reset commit). KB ensure runs after that — its
    # own internal commit is the responsibility of the KB service.
    app_session.commit.assert_awaited_once()

    # All emitted audit events must come from the CP actor.
    actors = {call.kwargs.get("actor") for call in audit.log.await_args_list}
    assert actors == {"cp:control-plane"}

    events = [call.kwargs.get("event") for call in audit.log.await_args_list]
    # Both context-manager open audits, the per-step inserts, and the
    # exit close events should all have fired.
    assert "cp_tenant_inserted" in events
    assert "cp_user_inserted" in events
    assert "cp_personal_kb_ensured" in events
    assert "impersonate_tenant_open" in events
    assert "cross_tenant_query_open" in events

    # Per-step inserts (not the open/close lifecycle events) carry the
    # caller-supplied reason. The lifecycle events deliberately don't —
    # `_emit_exit_audit` doesn't pass reason on close/abort.
    per_step_reasons = {
        call.kwargs.get("reason")
        for call in audit.log.await_args_list
        if call.kwargs.get("event", "").startswith("cp_")
    }
    assert per_step_reasons == {"seed test"}


@pytest.mark.asyncio
async def test_create_tenant_idempotent_when_tenant_and_user_already_exist() -> None:
    """Re-call against a fully-seeded tenant is a no-op for inserts and emails."""
    existing_tenant = MagicMock()
    existing_tenant.id = "tenant-1"
    existing_user = MagicMock()
    existing_user.id = "user-existing-1"

    svc, _, admin_session, _, password_auth, password_reset = _make_create_tenant_svc(
        existing_tenant=existing_tenant,
        existing_user=existing_user,
    )
    existing_state = MagicMock()
    existing_state.stripe_customer_id = "cus_123"
    existing_state.stripe_subscription_id = None

    kb_patcher, kb_svc_mock = _patch_kb_ensure()
    with (
        patch.object(BillingStateService, "ensure_exists", new_callable=AsyncMock) as ensure_exists,
        patch.object(BillingStateService, "update", new_callable=AsyncMock),
        kb_patcher,
    ):
        ensure_exists.return_value = (existing_state, False)

        response = await svc.create_tenant(
            _make_payload(stripe_customer_id="cus_123"),
            reason="seed retry",
        )

    assert response.user_id == "user-existing-1"
    assert response.welcome_email_sent is False
    assert response.billing_state_created is False

    # Tenant row NOT inserted (we already had it).
    admin_session.add.assert_not_called()
    # New user NOT created and reset email NOT re-sent.
    password_auth.create_user.assert_not_awaited()
    password_reset.request_reset.assert_not_awaited()
    # KB ensure DOES fire on the existing-user path — it's idempotent and
    # repairs the rare crash-after-user-commit-before-KB case.
    kb_svc_mock.ensure_personal_knowledge_base.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_tenant_409_on_diverging_stripe_customer_id() -> None:
    """Existing non-None stripe_customer_id with diverging payload raises BEFORE update()."""
    svc, _, _, _, _, _ = _make_create_tenant_svc()
    existing_state = MagicMock()
    existing_state.stripe_customer_id = "cus_OLD"
    existing_state.stripe_subscription_id = None

    with (
        patch.object(BillingStateService, "ensure_exists", new_callable=AsyncMock) as ensure_exists,
        patch.object(BillingStateService, "update", new_callable=AsyncMock) as update,
    ):
        ensure_exists.return_value = (existing_state, False)

        with pytest.raises(ConflictError) as exc_info:
            await svc.create_tenant(
                _make_payload(stripe_customer_id="cus_NEW"),
                reason="retry",
            )

    # update() must not run — we don't want to overwrite Stripe identity
    # then have to roll it back.
    update.assert_not_awaited()
    assert "stripe_customer_id" in exc_info.value.details["conflicting_fields"]


@pytest.mark.asyncio
async def test_create_tenant_first_time_fill_stripe_id_is_allowed() -> None:
    """Existing row has NULL stripe_customer_id; payload supplies one. update() runs."""
    svc, _, _, _, _, _ = _make_create_tenant_svc()
    half_filled_state = MagicMock()
    half_filled_state.stripe_customer_id = None
    half_filled_state.stripe_subscription_id = None

    kb_patcher, _ = _patch_kb_ensure()
    with (
        patch.object(BillingStateService, "ensure_exists", new_callable=AsyncMock) as ensure_exists,
        patch.object(BillingStateService, "update", new_callable=AsyncMock) as update,
        kb_patcher,
    ):
        ensure_exists.return_value = (half_filled_state, False)

        await svc.create_tenant(
            _make_payload(stripe_customer_id="cus_FILL"),
            reason="fill in",
        )

    update.assert_awaited_once()
    assert update.await_args.kwargs["updates"]["stripe_customer_id"] == "cus_FILL"


@pytest.mark.asyncio
async def test_create_tenant_request_reset_failure_rolls_back_user_and_skips_kb() -> None:
    """request_reset failure rolls back the just-flushed user, never commits,
    and never reaches the post-commit KB ensure.

    This is the atomicity contract: a partial provision (user committed
    without a reset email queued) is the failure mode we explicitly do not
    want — the user would exist in the DB with no way to log in.
    """
    svc, _, _, app_session, password_auth, password_reset = _make_create_tenant_svc()
    password_reset.request_reset.side_effect = RuntimeError("email queue down")
    fresh_state = MagicMock()
    fresh_state.stripe_customer_id = None
    fresh_state.stripe_subscription_id = None

    kb_patcher, kb_svc_mock = _patch_kb_ensure()
    with (
        patch.object(BillingStateService, "ensure_exists", new_callable=AsyncMock) as ensure_exists,
        patch.object(BillingStateService, "update", new_callable=AsyncMock),
        kb_patcher,
    ):
        ensure_exists.return_value = (fresh_state, True)

        with pytest.raises(RuntimeError, match="email queue down"):
            await svc.create_tenant(_make_payload(), reason="seed test")

    # create_user was called with flush_only=True — the user was never
    # committed. The impersonate context closes without our final
    # session.commit() running, so the flushed user rolls back.
    password_auth.create_user.assert_awaited_once()
    assert password_auth.create_user.await_args.kwargs["flush_only"] is True
    app_session.commit.assert_not_awaited()

    # KB ensure runs AFTER the user commit. Since the commit never
    # happened, KB ensure must not have fired either.
    kb_svc_mock.ensure_personal_knowledge_base.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_tenant_cross_tenant_email_collision_raises_conflict() -> None:
    """If create_user's flush hits a UNIQUE violation (email exists in a
    different tenant — invisible under RLS), translate to a 409 ConflictError
    rather than letting the IntegrityError surface as a 500.
    """
    from sqlalchemy.exc import IntegrityError

    svc, _, _, _, password_auth, _ = _make_create_tenant_svc()
    password_auth.create_user.side_effect = IntegrityError(
        statement="INSERT INTO users", params=None, orig=Exception("dup")
    )
    fresh_state = MagicMock()
    fresh_state.stripe_customer_id = None
    fresh_state.stripe_subscription_id = None

    kb_patcher, _ = _patch_kb_ensure()
    with (
        patch.object(BillingStateService, "ensure_exists", new_callable=AsyncMock) as ensure_exists,
        patch.object(BillingStateService, "update", new_callable=AsyncMock),
        kb_patcher,
    ):
        ensure_exists.return_value = (fresh_state, True)

        with pytest.raises(ConflictError) as exc_info:
            await svc.create_tenant(_make_payload(), reason="cross-tenant email")

    # The 409 body leaks only what CP supplied. Echoing the other tenant's
    # user id here would be a privacy violation.
    assert exc_info.value.details["conflicting_fields"] == ["email"]
    assert exc_info.value.details["email"] == "user@example.com"


@pytest.mark.asyncio
async def test_create_tenant_without_injected_password_services_raises() -> None:
    """Wire-up bug surfaces at construction (TypeError), not at first call.

    The CP collaborators are required kwargs on ``__init__`` so this
    misconfiguration can never reach a request handler.
    """
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    with pytest.raises(TypeError, match="password_auth"):
        TenantAdminService(  # type: ignore[call-arg]
            app_session_local=app_factory,
            admin_session_local=admin_factory,
            audit_logger=AsyncMock(),
        )
