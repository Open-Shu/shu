"""Tests for shu.services.tenant_admin_service.

Coverage focus:
* Audit emission ordering (open BEFORE session yield; fail-closed if open
  audit raises).
* Tenant context is set during impersonation and cleared after.
* Cross-tenant path uses the admin session factory and does NOT set context.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.core.tenant import tenant_context
from shu.services.audit_logger import AuditLogEmitError
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


@pytest.mark.asyncio
async def test_impersonate_tenant_sets_context_during_session() -> None:
    """Inside the contextmanager the tenant_context ContextVar must be the target."""
    app_factory, app_session = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    audit = AsyncMock()

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

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

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

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

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

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

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

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

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

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

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

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

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

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

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
    )

    class _OriginalError(RuntimeError):
        pass

    with pytest.raises(_OriginalError):
        async with svc.impersonate_tenant("tenant-X", "actor-1", "ticket"):
            raise _OriginalError("the real reason this failed")


# ---------------------------------------------------------------------------
# create_tenant (SHU-785) — covers happy path, idempotency, and the Stripe
# identity-immutability 409 path.
# ---------------------------------------------------------------------------


from unittest.mock import patch  # noqa: E402

from shu.auth.password_auth import PasswordAuthService  # noqa: E402
from shu.billing.state_service import BillingStateService  # noqa: E402
from shu.core.exceptions import ConflictError  # noqa: E402
from shu.schemas.cp_provisioning import (  # noqa: E402
    BillingInput,
    CreateTenantRequest,
    UserInput,
)
from shu.services.password_reset_service import PasswordResetService  # noqa: E402


def _make_billing(**overrides: Any) -> BillingInput:
    defaults: dict[str, Any] = {"subscription_status": "active"}
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

    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=audit,
        password_auth=password_auth,
        password_reset=password_reset,
    )
    return svc, audit, admin_session, app_session, password_auth, password_reset


@pytest.mark.asyncio
async def test_create_tenant_happy_path() -> None:
    """All three steps fire in order; response carries the right flags."""
    svc, audit, admin_session, app_session, password_auth, password_reset = (
        _make_create_tenant_svc()
    )
    fresh_state = MagicMock()
    fresh_state.stripe_customer_id = None
    fresh_state.stripe_subscription_id = None

    with (
        patch.object(BillingStateService, "ensure_exists", new_callable=AsyncMock) as ensure_exists,
        patch.object(BillingStateService, "update", new_callable=AsyncMock) as update,
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
    # path can't fire.
    password_auth.create_user.assert_awaited_once()
    create_kwargs = password_auth.create_user.await_args.kwargs
    assert create_kwargs["admin_created"] is True
    assert create_kwargs["role"] == "regular_user"

    # Welcome email queued.
    password_reset.request_reset.assert_awaited_once()

    # All emitted audit events must come from the CP actor.
    actors = {call.kwargs.get("actor") for call in audit.log.await_args_list}
    assert actors == {"cp:control-plane"}

    events = [call.kwargs.get("event") for call in audit.log.await_args_list]
    # Both context-manager open audits, the per-step inserts, and the
    # exit close events should all have fired.
    assert "cp_tenant_inserted" in events
    assert "cp_user_inserted" in events
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

    with (
        patch.object(BillingStateService, "ensure_exists", new_callable=AsyncMock) as ensure_exists,
        patch.object(BillingStateService, "update", new_callable=AsyncMock),
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

    with (
        patch.object(BillingStateService, "ensure_exists", new_callable=AsyncMock) as ensure_exists,
        patch.object(BillingStateService, "update", new_callable=AsyncMock) as update,
    ):
        ensure_exists.return_value = (half_filled_state, False)

        await svc.create_tenant(
            _make_payload(stripe_customer_id="cus_FILL"),
            reason="fill in",
        )

    update.assert_awaited_once()
    assert update.await_args.kwargs["updates"]["stripe_customer_id"] == "cus_FILL"


@pytest.mark.asyncio
async def test_create_tenant_request_reset_failure_propagates() -> None:
    """If request_reset raises, the exception bubbles — the impersonate context's
    transaction rollback happens via the session __aexit__ on the way out."""
    svc, _, _, _, _, password_reset = _make_create_tenant_svc()
    password_reset.request_reset.side_effect = RuntimeError("email queue down")
    fresh_state = MagicMock()
    fresh_state.stripe_customer_id = None
    fresh_state.stripe_subscription_id = None

    with (
        patch.object(BillingStateService, "ensure_exists", new_callable=AsyncMock) as ensure_exists,
        patch.object(BillingStateService, "update", new_callable=AsyncMock),
    ):
        ensure_exists.return_value = (fresh_state, True)

        with pytest.raises(RuntimeError, match="email queue down"):
            await svc.create_tenant(_make_payload(), reason="seed test")


@pytest.mark.asyncio
async def test_create_tenant_without_injected_password_services_raises() -> None:
    """Wire-up bug surfaces with RuntimeError, not a partial provision."""
    app_factory, _ = _stub_session_factory()
    admin_factory, _ = _stub_session_factory()
    svc = TenantAdminService(
        app_session_local=app_factory,
        admin_session_local=admin_factory,
        audit_logger=AsyncMock(),
    )
    with pytest.raises(RuntimeError, match="password_auth and password_reset"):
        await svc.create_tenant(_make_payload(), reason="seed test")
