"""Tenant-isolation primitives: request context, errors, and infra helpers.

Holds:
- ``tenant_context`` ContextVar — set by the FastAPI tenant resolver and
  the worker dispatch wrapper; read by the engine ``"begin"`` hook and the
  ``before_flush`` listener in :mod:`shu.core.database`.
- ``MissingTenantContextError`` / ``CrossTenantInsertError`` — raised by
  the auto-stamping listener when a tenant-scoped insert can't be resolved
  safely.
- ``_lookup_tenant_for_user`` / ``_lookup_tenant_for_email`` — multi-tenant
  SECURITY-DEFINER-backed pre-auth lookups. Process-local LRU cached.
- ``resolve_tenant`` — FastAPI yield dependency that sets ``tenant_context``
  for the request and resets it on response.
- ``tenant_context_for_email`` — async contextmanager for pre-auth code
  paths that have an email but not a request (login form handlers, SSO
  callback, Stripe webhook).
- ``warn_tenant_without_redis`` — shared message shape for the cache and
  queue Redis-backed factories. The message has to stay in lockstep across
  factories, so it lives in one place to prevent drift.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from logging import Logger

from async_lru import alru_cache
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, NoResultFound

from .config import DeploymentMode, get_settings_instance
from .logging import get_logger

logger = get_logger(__name__)

# ContextVar — not threading.local — because Shu is async: ContextVar storage
# is asyncio-task-local, so concurrent requests don't bleed tenant across each
# other. The hook chain in core/database.py depends on this isolation property.
tenant_context: ContextVar[str | None] = ContextVar("tenant_context", default=None)


class MissingTenantContextError(RuntimeError):
    """No tenant context set when one was required.

    Raised by the ``before_flush`` listener when a tenant-scoped object is
    about to be inserted without a ``tenant_id`` and ``tenant_context`` is
    also unset. Usually indicates a missing tenant-resolution dependency on
    the route, or a worker handler that wasn't wrapped to set the job's
    tenant context.
    """


class CrossTenantInsertError(RuntimeError):
    """Object's ``tenant_id`` disagrees with the session's context on write.

    Raised by the ``before_flush`` listener for both inserts (new object
    with an explicit ``tenant_id`` that doesn't match ``tenant_context``)
    and updates (existing row mutated so its ``tenant_id`` no longer
    matches). The RLS ``WITH CHECK`` policy would also reject these at
    the DB layer, but raising in Python gives a cleaner stack trace
    pointing at the mutation site.

    The class name is insert-shaped for historical reasons; kept stable
    to avoid churning every call site that imports it.
    """


class TenantResolutionError(RuntimeError):
    """Credential didn't map to any tenant.

    Base class for credential-specific subclasses so route layers can
    catch precisely the credential kind they own (a webhook handler
    catches ``UnknownStripeCustomerError`` and returns 409; an auth
    dependency catches ``UserTenantNotFoundError`` and returns 401).
    Catching the base class is fine for a generic "no tenant" guard.
    """


class UnknownStripeCustomerError(TenantResolutionError):
    """Stripe customer_id from a webhook payload doesn't map to any tenant.

    Without this, the legacy silent-None path stamped
    ``tenant_context=None``, ran the webhook body under RLS default-deny,
    returned a successful 200, and let Stripe drop the event — no record,
    no retry. Raising here lets the route return a non-2xx so the event
    stays visible.
    """


class UserTenantNotFoundError(TenantResolutionError):
    """user_id from a verified JWT doesn't map to any tenant.

    Practical cause is the deleted-user-with-active-JWT edge: the JWT was
    valid when issued, the user row has since been removed. Without this,
    the legacy silent-None path produced confusing RLS-default-deny errors
    from downstream queries; with it, the auth dependency surfaces a
    clean 401.
    """


# =============================================================================
# Multi-tenant lookups via SECURITY DEFINER functions (SHU-761)
#
# Both lookups open a short-lived session on the app engine. The session does
# not have tenant_context set, so the engine "begin" hook fires with tid=None
# (no-op). The SD functions themselves bypass RLS via shu_admin ownership.
#
# Cached process-locally with @alru_cache. Per the design, the user→tenant and
# email→tenant assignments are set-once invariants, so cache entries can't
# go stale. If we ever introduce a "move user between tenants" operation,
# that operation has to invalidate the cache (out of scope here).
# =============================================================================


def _open_app_session():
    # Deferred import keeps core.tenant import-cycle-safe — core.database
    # imports symbols from this module.
    from .database import get_async_session_local

    return get_async_session_local()()


@alru_cache(maxsize=4096)
async def _lookup_tenant_for_user(user_id: str) -> str | None:
    # Cached: fires on every authenticated request that hits resolve_tenant —
    # the hot path. user→tenant is set-once, so the cache can't go stale.
    # Returns None on miss (deleted-user-with-active-JWT edge); the caller
    # in ``_tenant_context_for_credential`` translates that to a typed
    # ``UserTenantNotFoundError`` so the auth dependency can 401 cleanly.
    async with _open_app_session() as session:
        result = await session.execute(
            text("SELECT tenant_for_user_id(:uid)"),
            {"uid": user_id},
        )
        return result.scalar_one()


async def _lookup_tenant_for_email(email: str) -> str:
    # Not cached: only fires on login, password-reset, and SSO callback —
    # once-per-session events. Caching them would burn RAM for negligible
    # hit-rate gain.
    async with _open_app_session() as session:
        result = await session.execute(
            text("SELECT tenant_for_email(:email)"),
            {"email": email},
        )
        return result.scalar_one()


async def _lookup_tenant_for_reset_token(token_hash: str) -> str | None:
    # Returns None on a miss. The SD function is plain ``LANGUAGE sql`` so
    # ``scalar_one()`` already returns None on no-row match — the except
    # branches below never fire under normal operation. They remain as
    # defense-in-depth: if anyone reverts the function to PL/pgSQL
    # ``INTO STRICT``, the NO_DATA_FOUND raise stops at this boundary so
    # the route layer keeps surfacing 400-on-invalid-token instead of 500.
    #
    # Only the "no-row" shape is caught — other DBAPI errors propagate so a
    # genuine outage doesn't masquerade as an invalid token.
    async with _open_app_session() as session:
        try:
            result = await session.execute(
                text("SELECT tenant_for_reset_token(:h)"),
                {"h": token_hash},
            )
            return result.scalar_one()
        except NoResultFound:
            return None
        except DBAPIError as exc:
            if _is_no_data_found(exc):
                return None
            raise


async def _lookup_tenant_for_verification_token(token_hash: str) -> str | None:
    # Same defense-in-depth shape as ``_lookup_tenant_for_reset_token``.
    # The SD function returns NULL on miss directly.
    async with _open_app_session() as session:
        try:
            result = await session.execute(
                text("SELECT tenant_for_verification_token(:h)"),
                {"h": token_hash},
            )
            return result.scalar_one()
        except NoResultFound:
            return None
        except DBAPIError as exc:
            if _is_no_data_found(exc):
                return None
            raise


def _is_no_data_found(exc: DBAPIError) -> bool:
    """Evaluate: True if ``exc`` wraps Postgres SQLSTATE ``P0002`` (NO_DATA_FOUND).

    The PL/pgSQL ``INTO STRICT`` SD functions raise this when no row matches.
    Asyncpg surfaces it as ``NoDataFoundError`` with ``sqlstate == 'P0002'``;
    psycopg / others surface the same SQLSTATE on ``exc.orig.pgcode``. Match
    on the SQLSTATE rather than the driver-specific exception class so this
    works across DB driver versions and other PEP 249 wrappers.
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate == "P0002"


async def _lookup_tenant_for_stripe_customer(customer_id: str) -> str | None:
    # Uncached: webhooks are bursty per customer but ultimately rare per
    # second; the cost of an extra session is dominated by the network RTT
    # the webhook already paid.
    # Returns None on miss (unknown customer in our DB — e.g. webhook for a
    # deleted tenant). The caller in ``_tenant_context_for_credential``
    # translates that to ``UnknownStripeCustomerError`` so the webhook
    # route returns 409 instead of silently ack'ing 200.
    async with _open_app_session() as session:
        result = await session.execute(
            text("SELECT tenant_for_stripe_customer(:cid)"),
            {"cid": customer_id},
        )
        return result.scalar_one()


@asynccontextmanager
async def _tenant_context_for_credential(
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    email: str | None = None,
    reset_token_hash: str | None = None,
    verification_token_hash: str | None = None,
    stripe_customer_id: str | None = None,
) -> AsyncIterator[str]:
    """Resolve tenant_id from a credential, set tenant_context, yield, reset on exit.

    An explicit ``tenant_id`` short-circuits everything — used by the worker
    dispatch wrapper and the per-tenant fan-out helper, both of which already
    have a verified tenant_id and don't need to go through the credential
    resolver.

    Otherwise: silo and self-hosted short-circuit to the deployment constant
    regardless of which credential is passed (they have one tenant either
    way). Multi-tenant routes to the appropriate SECURITY DEFINER lookup.
    Set/reset bookkeeping is the same in every case — kept here so the
    public contextmanagers below are one-liners.
    """
    # Lazy import to avoid duplicating the constant — config.py is the source of truth.
    from .config import SELF_HOSTED_TENANT_UUID

    if tenant_id is not None:
        tid = tenant_id
    else:
        settings = get_settings_instance()
        if settings.deployment_mode == DeploymentMode.SELF_HOSTED:
            tid = SELF_HOSTED_TENANT_UUID
        elif settings.deployment_mode == DeploymentMode.SILO:
            tid = settings.tenant_id  # noqa: STRAY-TENANT-ID — silo: validator guarantees UUID string
        elif user_id is not None:
            tid = await _lookup_tenant_for_user(user_id)
            if tid is None:
                # Deleted user with a still-valid JWT — surface to auth
                # dependency as a clean 401, not as downstream RLS noise.
                raise UserTenantNotFoundError(f"No tenant found for user_id={user_id!r}; user may have been deleted.")
        elif email is not None:
            # ``_lookup_tenant_for_email`` returns None silently on miss; the
            # login/SSO/password-reset routes that funnel through here want
            # the RLS-default-deny path so they emit a generic "invalid
            # credentials" 401 (and don't leak email existence). Don't
            # translate to a typed exception here.
            tid = await _lookup_tenant_for_email(email)
        elif reset_token_hash is not None:
            tid = await _lookup_tenant_for_reset_token(reset_token_hash)
        elif verification_token_hash is not None:
            tid = await _lookup_tenant_for_verification_token(verification_token_hash)
        elif stripe_customer_id is not None:
            tid = await _lookup_tenant_for_stripe_customer(stripe_customer_id)
            if tid is None:
                # Webhook for a customer we don't have in billing_state — let
                # the route surface a non-2xx so Stripe stops retrying but the
                # event stays visible (the legacy silent-200 dropped events).
                raise UnknownStripeCustomerError(f"No tenant found for stripe customer_id={stripe_customer_id!r}.")
        else:
            raise MissingTenantContextError(
                "Multi-tenant tenant resolution requires a credential identifier; none was provided."
            )

    # Normalize at this single boundary (the only ``tenant_context.set`` site):
    #   - a uuid.UUID (explicit tenant_id from worker dispatch / fan-out) becomes a
    #     str so the contextvar honors its ``str | None`` annotation and the
    #     before_flush guard never compares a str column against a UUID context;
    #   - any falsy value (``""`` from an empty job payload or a stray caller)
    #     collapses to ``None`` — "no tenant", not a literal empty string. Without
    #     this, the begin hook would write ``set_config('app.tenant_id', '')`` and
    #     every tenant-scoped query on that connection would 500 on ``''::uuid``
    #     instead of behaving like "no context → no rows".
    tid = str(tid) if tid else None

    token = tenant_context.set(tid)
    try:
        yield tid
    finally:
        tenant_context.reset(token)


# Five named public wrappers — each just picks which credential goes into the
# shared resolver. Keeping the names distinct at call sites makes it obvious
# at a glance which pre-auth flow we're in (grep-friendly too).


def tenant_context_for_email(email: str):
    """Pre-auth tenant context keyed by email.

    Used by login, SSO callback, and password-reset request — anywhere the
    request only carries an email.
    """
    return _tenant_context_for_credential(email=email)


def tenant_context_for_user_id(user_id: str):
    """Pre-auth tenant context keyed by user_id.

    Used by the FastAPI ``resolve_tenant`` yield dependency (post-JWT-decode)
    and any imperative caller that already has a verified user_id.
    """
    return _tenant_context_for_credential(user_id=user_id)


def tenant_context_for_reset_token(token_hash: str):
    """Pre-auth tenant context for the password-reset redeem flow.

    Caller hashes the plaintext token from the URL with sha256 and passes
    the hex digest. Multi-tenant resolution can raise NO_DATA_FOUND if the
    token is unknown — callers should translate to their existing
    "invalid or expired token" error.
    """
    return _tenant_context_for_credential(reset_token_hash=token_hash)


def tenant_context_for_verification_token(token_hash: str):
    """Pre-auth tenant context for the email-verification confirm flow.

    Same shape as ``tenant_context_for_reset_token`` — sha256 hex digest of
    the plaintext token from the URL.
    """
    return _tenant_context_for_credential(verification_token_hash=token_hash)


def tenant_context_for_stripe_customer(customer_id: str):
    """Pre-auth tenant context for the Stripe webhook handler.

    Stripe sends events with a ``customer`` field; resolving tenant from it
    is the standard pattern for "I have payment data but no Shu user context".
    """
    return _tenant_context_for_credential(stripe_customer_id=customer_id)


def resolve_redis_namespace() -> str:
    """Return the static Redis-key namespace for this deployment.

    Resolved once at engine construction time (not per-call), so it works
    from the worker poll loop and the fan-out helper — both of which run
    without tenant_context set. Tenant isolation on the worker side is
    enforced by ``tenant_context.set(job.tenant_id)`` inside the dispatch
    wrapper plus RLS at the DB layer; the Redis namespace only exists to
    prevent collisions between **deployments** sharing one Redis instance,
    not between tenants within a deployment.

    Defaults per mode:
      * SELF_HOSTED  → ``SELF_HOSTED_TENANT_UUID`` (matches the deployment's
        identity in every other code path).
      * SILO         → the configured ``SHU_TENANT_ID`` (the deployment is
        the tenant; validator guarantees the field is a UUID).
      * MULTI_TENANT → literal ``"multitenant"``.

    Operators can override via ``SHU_REDIS_NAMESPACE`` — useful when two
    MT clusters share managed Redis and would otherwise collide on the
    default.
    """
    from .config import SELF_HOSTED_TENANT_UUID

    settings = get_settings_instance()
    if settings.redis_namespace:
        return settings.redis_namespace
    if settings.deployment_mode == DeploymentMode.SELF_HOSTED:
        return SELF_HOSTED_TENANT_UUID
    if settings.deployment_mode == DeploymentMode.SILO:
        return settings.tenant_id  # noqa: STRAY-TENANT-ID — silo: validator guarantees UUID string
    return "multitenant"


def resolve_tenant_for_infra() -> str:
    """Return the tenant_id for non-DB infra consumers (Redis keys, CP traffic, etc.).

    Used by Redis cache / queue / CP-billing / anything outside the DB session
    pipeline that needs a tenant_id but doesn't get one through a credential
    or a job payload. The hot question is multi-tenant: a naive ``or``-chain
    would silently fall through to the self-hosted constant if context isn't
    set, which would dump every multi-tenant worker's writes into the same
    Redis namespace. We raise instead, so the misconfiguration is loud.
    """
    # Lazy import to avoid duplicating the constant — config.py is the source of truth.
    from .config import SELF_HOSTED_TENANT_UUID

    settings = get_settings_instance()
    if settings.deployment_mode == DeploymentMode.SELF_HOSTED:
        return SELF_HOSTED_TENANT_UUID
    if settings.deployment_mode == DeploymentMode.SILO:
        return settings.tenant_id  # noqa: STRAY-TENANT-ID — silo: validator guarantees UUID string
    # MULTI_TENANT — the only place tenant_context might be unset, in which case
    # we have a real misconfiguration: some code path is touching infra without
    # having gone through the request resolver or the worker dispatch wrapper.
    tid = tenant_context.get(None)
    if tid is None:
        raise MissingTenantContextError(
            "resolve_tenant_for_infra() called in multi-tenant mode without tenant_context set. "
            "This usually means an infra call (Redis key, CP request) is happening outside a "
            "request handler and outside the worker dispatch wrapper."
        )
    return tid


def tenant_context_for_tenant_id(tenant_id: str | None):
    """Tenant context for callers that already have a verified tenant_id.

    Used by the worker dispatch wrapper (one per job, tenant_id from the job
    payload) and the per-tenant fan-out helper (one per row of the tenants
    catalog). Passing ``None`` falls through to deployment-constant resolution
    — silo / self-hosted produce the right tenant; multi-tenant raises.
    """
    return _tenant_context_for_credential(tenant_id=tenant_id)


async def for_each_tenant_in_deployment(
    work: Callable[[str], Awaitable[None]],
) -> None:
    """Run ``work(tenant_id)`` once per tenant under that tenant's context.

    Self-hosted / silo: invokes ``work`` once with the deployment's tenant.
    Multi-tenant: invokes ``work`` for every row of the ``tenants`` catalog
    in catalog-insertion order.

    Each invocation runs inside ``tenant_context_for_tenant_id(tid)`` so
    DB queries scope correctly under RLS. The context resets between
    invocations and on exception — exceptions raised by ``work`` propagate
    after the current tenant's context is cleaned up.

    Why a higher-order function instead of an async generator: the natural
    ``async for tid in helper(): ...`` shape leaks tenant_context if the
    loop body raises. Python's async-iter protocol schedules generator
    cleanup but doesn't await it before the exception unwinds — leaving
    the contextvar in the failed iteration's tenant. The callback shape
    guarantees ``__aexit__`` runs synchronously before propagating.

    Suitable for boot-time fan-outs (stale-KB detection) and scheduler
    ticks that need identical work against every tenant.
    """
    # Deferred import: ``list_all_tenant_ids`` lives in ``core.worker`` to
    # keep it next to the dispatch loop that owns the BYPASSRLS reasoning.
    # Top-level import would close the cycle (worker → tenant → worker).
    from .worker import list_all_tenant_ids

    for tid in await list_all_tenant_ids():
        async with tenant_context_for_tenant_id(tid):
            await work(tid)


def warn_tenant_without_redis(caller_logger: Logger, backend_kind: str, tenant_id: str) -> None:
    # Multi-tenant deployments have many tenants; warning per-tenant about a
    # missing Redis URL would either spam logs or only fire for one tenant's
    # request. The misconfiguration there is structural (operator shipped
    # multi-tenant without Redis) and surfaces other ways — skip the noise.
    if get_settings_instance().deployment_mode == DeploymentMode.MULTI_TENANT:
        return
    caller_logger.warning(
        "SHU_TENANT_ID is set (tenant=%s) but SHU_REDIS_URL is not — " "falling back to in-memory %s backend.",
        tenant_id,
        backend_kind,
    )
