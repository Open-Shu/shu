"""Cross-tenant admin operations gated on audit emission.

Two operation shapes, each with its own role binding and isolation story:

* ``impersonate_tenant`` — open a ``shu_app`` session with ``tenant_context``
  set to the target. RLS still applies (shu_app does not have BYPASSRLS), so
  the operator sees exactly what that tenant's app code would see. Used by
  support reading another tenant's data to debug a ticket.

* ``cross_tenant_query`` — open a ``shu_admin`` session with no tenant
  context. ``shu_admin`` has BYPASSRLS, so RLS is bypassed entirely; callers
  must write explicit ``WHERE tenant_id = ...`` predicates to scope. Used by
  internal analytics (cost reports, usage summaries) that genuinely span
  tenants.

Both paths audit-log entry and exit. Emission happens **before** the session
yields — if the audit logger raises, the session never opens and the caller
sees the audit failure, not a successful operation with a missing record.
The exit audit fires in a ``finally`` so the trail never loses an event,
even when the caller's body raises; the exit event is ``*_close`` on a
clean exit and ``*_aborted`` (carrying the original error class) on a raise.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shu.auth.models import User
from shu.auth.password_auth import PasswordAuthService
from shu.billing.state_service import BillingStateService
from shu.core.exceptions import ConflictError
from shu.core.logging import get_logger
from shu.core.tenant import tenant_context_for_tenant_id
from shu.models.tenant import Tenant
from shu.schemas.cp_provisioning import CreateTenantRequest, CreateTenantResponse
from shu.services.audit_logger import AuditLogger
from shu.services.knowledge_base_service import (
    KnowledgeBaseService,
    resolve_personal_kb_name,
    resolve_personal_kb_slug_token,
)
from shu.services.password_reset_service import PasswordResetService

logger = get_logger(__name__)

# Used as the `actor` field on every audit event emitted by CP-driven flows.
# Public (no leading underscore) because the per-domain services
# (PolicyService, PromptService, etc.) re-export it for their own cp_*
# methods rather than redeclaring the literal. One canonical source.
CP_ACTOR = "cp:control-plane"


class TenantAdminService:
    """Owns the two cross-tenant admin patterns.

    Constructor takes session factories rather than engines so test code can
    inject in-memory sessionmakers without standing up a real Postgres
    connection. The default wire-up (see ``get_tenant_admin_service`` in the
    admin router module) reads ``get_async_session_local`` /
    ``get_admin_session_local`` from ``shu.core.database``.
    """

    def __init__(
        self,
        *,
        app_session_local: async_sessionmaker[AsyncSession],
        admin_session_local: async_sessionmaker[AsyncSession],
        audit_logger: AuditLogger,
        password_auth: PasswordAuthService,
        password_reset: PasswordResetService,
    ) -> None:
        self._app_session_local = app_session_local
        self._admin_session_local = admin_session_local
        self._audit = audit_logger
        self._password_auth = password_auth
        self._password_reset = password_reset

    async def _emit_exit_audit(
        self,
        *,
        event_prefix: str,
        actor_user_id: str,
        target: str | None,
        body_exc: BaseException | None,
    ) -> None:
        """Emit the close/aborted audit, swallowing failures when the body raised.

        Two failure modes have to be handled differently:

        * Body succeeded → emit ``*_close``. If THIS audit raises, propagate —
          the operator needs to know the close record didn't make it.
        * Body raised → emit ``*_aborted`` with the error class so the trail
          captures the failure. If THIS audit raises, log loudly and swallow —
          we want the original ``body_exc`` to be the visible failure, not the
          audit-transport error masking it.
        """
        audit_kwargs: dict[str, Any] = {
            "event": f"{event_prefix}_close" if body_exc is None else f"{event_prefix}_aborted",
            "actor": actor_user_id,
            "target": target,
        }
        if body_exc is not None:
            audit_kwargs["error_class"] = type(body_exc).__name__

        try:
            await self._audit.log(**audit_kwargs)
        except Exception:
            if body_exc is None:
                # Clean-exit audit failure surfaces to the caller — same
                # contract as the open-audit failure (fail-closed on audit).
                raise
            logger.exception(
                "audit_emit_failed_during_unwind",
                extra={
                    "event_prefix": event_prefix,
                    "actor": actor_user_id,
                    "original_exception": type(body_exc).__name__,
                },
            )

    @asynccontextmanager
    async def impersonate_tenant(
        self,
        target_tenant_id: str,
        actor_user_id: str,
        reason: str,
    ) -> AsyncIterator[AsyncSession]:
        """Open a ``shu_app`` session scoped to ``target_tenant_id``.

        The session inherits RLS enforcement from ``shu_app``; the
        engine-level ``begin`` hook reads ``tenant_context`` and stamps
        ``app.tenant_id`` via ``set_config(..., true)`` on every transaction,
        so the impersonation persists for the session's lifetime.
        """
        # Audit FIRST. If this raises we never open the session — fail-closed.
        await self._audit.log(
            event="impersonate_tenant_open",
            actor=actor_user_id,
            target=target_tenant_id,
            reason=reason,
        )

        body_exc: BaseException | None = None
        try:
            async with tenant_context_for_tenant_id(target_tenant_id), self._app_session_local() as session:
                try:
                    yield session
                except BaseException as exc:
                    body_exc = exc
                    raise
        finally:
            await self._emit_exit_audit(
                event_prefix="impersonate_tenant",
                actor_user_id=actor_user_id,
                target=target_tenant_id,
                body_exc=body_exc,
            )

    @asynccontextmanager
    async def cross_tenant_query(
        self,
        actor_user_id: str,
        reason: str,
    ) -> AsyncIterator[AsyncSession]:
        """Open a ``shu_admin`` session for queries that span tenants.

        Callers MUST add explicit ``WHERE tenant_id = ...`` predicates to
        scope; no policy filters their reads. ``app.tenant_id`` is
        intentionally not set — BYPASSRLS ignores it, so setting it would
        only mislead a reader of the SQL into thinking it does something.
        """
        await self._audit.log(
            event="cross_tenant_query_open",
            actor=actor_user_id,
            reason=reason,
        )

        body_exc: BaseException | None = None
        try:
            async with self._admin_session_local() as session:
                try:
                    yield session
                except BaseException as exc:
                    body_exc = exc
                    raise
        finally:
            await self._emit_exit_audit(
                event_prefix="cross_tenant_query",
                actor_user_id=actor_user_id,
                target=None,
                body_exc=body_exc,
            )

    async def create_tenant(
        self,
        payload: CreateTenantRequest,
        reason: str,
    ) -> CreateTenantResponse:
        """Seed a brand-new tenant: tenants + billing_state + first regular user.

        Atomicity contract: user creation and the reset-token write live in a
        single transaction. ``create_user`` is called with ``flush_only=True``
        and ``request_reset`` only flushes — so an email-queue failure rolls
        the just-flushed user back. CP's retry then re-fires the create+reset
        path because the user row no longer exists.

        Two session boundaries are still required: ``tenants`` is a global
        write (admin role, no RLS context); the per-tenant rows need
        ``app.tenant_id`` set so RLS WITH CHECK and the auto-stamp listener
        cooperate. Idempotency rides on the natural unique keys (tenants.id
        PK, billing_state.tenant_id PK, users.email UNIQUE).

        Stripe identity is immutable once set: re-call with a diverging
        ``stripe_customer_id`` or ``stripe_subscription_id`` raises 409
        *before* ``BillingStateService.update()`` so we don't audit-log an
        overwrite we then have to roll back.

        Welcome email is once-only: re-call against an existing user row
        returns ``welcome_email_sent=False`` and skips ``request_reset``.

        Personal-KB ensure runs *after* the user commit and unconditionally
        (idempotent by owner). Running it after-commit keeps it outside the
        user-flush rollback window, and running it on every call repairs the
        rare case where a previous attempt committed the user but crashed
        before the KB row was created.
        """
        # --- Step 1: tenants row (admin/global write, no RLS context) ---
        async with self.cross_tenant_query(CP_ACTOR, reason) as admin_session:
            existing_tenant = (
                await admin_session.execute(select(Tenant).where(Tenant.id == payload.tenant_id))
            ).scalar_one_or_none()

            if existing_tenant is None:
                admin_session.add(Tenant(id=payload.tenant_id))
                await admin_session.commit()
                await self._audit.log(
                    event="cp_tenant_inserted",
                    actor=CP_ACTOR,
                    target=payload.tenant_id,
                    reason=reason,
                )
            else:
                await self._audit.log(
                    event="cp_tenant_found",
                    actor=CP_ACTOR,
                    target=payload.tenant_id,
                    reason=reason,
                )

        # --- Step 2: billing_state + user + welcome email (tenant-scoped) ---
        async with self.impersonate_tenant(payload.tenant_id, CP_ACTOR, reason) as session:
            state, billing_state_created = await BillingStateService.ensure_exists(session)

            # Stripe identity is immutable. Reject before update() so we
            # don't write an audit row for an overwrite we then have to
            # roll back. First-time fill (existing value is NULL) is allowed.
            if not billing_state_created:
                conflicts: list[str] = []
                if (
                    state.stripe_customer_id is not None
                    and state.stripe_customer_id != payload.billing.stripe_customer_id
                ):
                    conflicts.append("stripe_customer_id")
                if (
                    state.stripe_subscription_id is not None
                    and state.stripe_subscription_id != payload.billing.stripe_subscription_id
                ):
                    conflicts.append("stripe_subscription_id")
                if conflicts:
                    raise ConflictError(
                        f"Stripe identity diverges from existing billing_state: {conflicts}",
                        details={"conflicting_fields": conflicts},
                    )

            updates: dict[str, Any] = {
                "user_limit_enforcement": payload.billing.user_limit_enforcement,
            }
            if payload.billing.stripe_customer_id is not None:
                updates["stripe_customer_id"] = payload.billing.stripe_customer_id
            if payload.billing.stripe_subscription_id is not None:
                updates["stripe_subscription_id"] = payload.billing.stripe_subscription_id
            if payload.billing.billing_email is not None:
                updates["billing_email"] = payload.billing.billing_email
            await BillingStateService.update(
                session,
                updates=updates,
                source="cp:provision",
            )

            existing_user = (
                await session.execute(select(User).where(User.email == payload.user.email))
            ).scalar_one_or_none()

            if existing_user is not None:
                user = existing_user
                welcome_email_sent = False
                await self._audit.log(
                    event="cp_user_found",
                    actor=CP_ACTOR,
                    target=user.id,
                    reason=reason,
                )
            else:
                # generate_temporary_password produces a strict-policy-passing
                # value so create_user's validate_password step is happy. We
                # don't surface it — the user gets in via the reset email.
                temp_password = self._password_auth.generate_temporary_password()
                # flush_only=True keeps the user pending in this transaction
                # so request_reset's failure rolls it back. The final commit
                # below is the only place this user becomes durable.
                try:
                    user = await self._password_auth.create_user(
                        email=payload.user.email,
                        password=temp_password,
                        name=payload.user.name,
                        role="regular_user",
                        db=session,
                        admin_created=True,
                        flush_only=True,
                    )
                except IntegrityError as exc:
                    # users.email is globally unique. The same-tenant case was
                    # caught by the existence check above, so reaching here
                    # means the email belongs to a user in a different tenant
                    # (invisible under RLS). Translate to 409 — leaking the
                    # other tenant's user_id would be a privacy violation, so
                    # the details echo only the email CP supplied.
                    raise ConflictError(
                        "user email already exists outside this tenant",
                        details={"conflicting_fields": ["email"], "email": payload.user.email},
                    ) from exc
                await self._audit.log(
                    event="cp_user_inserted",
                    actor=CP_ACTOR,
                    target=user.id,
                    reason=reason,
                )
                # request_reset uses db.flush()-only (see service body); a
                # failure here aborts the impersonate transaction and the
                # user flush is rolled back with it.
                await self._password_reset.request_reset(
                    email=payload.user.email,
                    ip=None,
                    db=session,
                )
                welcome_email_sent = True

            await session.commit()

            # KB ensure runs after the user/billing commit. It uses its own
            # internal commit and is owner-idempotent — a retry after a
            # crash that committed the user but never reached this point
            # heals the missing KB. Lives inside the impersonate context so
            # the RLS tenant scope is still active.
            kb_svc = KnowledgeBaseService(session)
            await kb_svc.ensure_personal_knowledge_base(
                owner_id=user.id,
                display_name=resolve_personal_kb_name(user),
                slug_token=resolve_personal_kb_slug_token(user),
            )
            await self._audit.log(
                event="cp_personal_kb_ensured",
                actor=CP_ACTOR,
                target=user.id,
                reason=reason,
            )

        return CreateTenantResponse(
            tenant_id=payload.tenant_id,
            user_id=user.id,
            welcome_email_sent=welcome_email_sent,
            billing_state_created=billing_state_created,
        )
