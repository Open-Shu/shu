"""EmailService — the mid-layer that user-facing flows consume.

Owns templates, queueing, audit logging, idempotency, and the rate-limit
helper. Wraps `EmailBackend` (transport) and never exposes it directly to
callers — verification, password reset, and broadcast services should
depend on `EmailService`, not on a concrete backend.

`send` is async and **always queues** — there is no public sync method.
The queued audit row is written first, then the job is enqueued under
`WorkloadType.EMAIL`. The dedicated worker handler (in `shu/worker.py`)
loads the audit row, renders the template, calls the backend, and updates
the audit row to `sent` or `failed`. See SHU-508 for the full design.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, Template, TemplateNotFound, select_autoescape
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.cache_backend import CacheBackend
from ..core.email.backend import EmailMessage, SendStatus
from ..core.logging import get_logger
from ..core.queue_backend import QueueBackend
from ..core.workload_routing import WorkloadType, enqueue_job
from ..models.email_send_log import EmailSendLog

logger = get_logger(__name__)


_TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates" / "email"


@dataclass
class RenderedTemplate:
    """Output of template rendering — backends receive these fields."""

    subject: str
    body_text: str
    body_html: str | None


class EmailServiceError(Exception):
    """Base error raised by EmailService for caller-visible problems."""


class TemplateNotFoundError(EmailServiceError):
    """Raised when a requested template name has no files in the templates dir."""


class EmailService:
    """Owns the queue/audit/template surface for outbound email."""

    def __init__(
        self,
        *,
        queue: QueueBackend,
        cache: CacheBackend,
        from_address: str | None,
        from_name: str | None = None,
        templates_root: Path = _TEMPLATES_ROOT,
        app_name: str = "Shu",
        support_email: str | None = None,
    ) -> None:
        self._queue = queue
        self._cache = cache
        self._from_address = from_address
        self._from_name = from_name
        self._app_name = app_name
        self._support_email = support_email

        # `select_autoescape(["html"])` enables autoescape only for HTML — the
        # plain-text variant must NOT autoescape or `&` becomes `&amp;` in
        # what users see as raw text.
        self._env = Environment(
            loader=FileSystemLoader(str(templates_root)),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send(
        self,
        *,
        db: AsyncSession,
        template_name: str,
        to: str,
        context: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Render, audit, and enqueue an outbound email. Returns the audit row id.

        The send itself happens in the EMAIL workload-type worker. This call
        returns as soon as the audit row + queue job exist, never blocking
        on transport.

        Idempotency: if `idempotency_key` is provided and a prior row exists
        with the same `(template_name, to, idempotency_key)`, the existing
        row's id is returned and no new job is enqueued.
        """
        if self._from_address is None:
            raise EmailServiceError(
                "EmailService.send called but SHU_EMAIL_FROM_ADDRESS is not configured. "
                "Set SHU_EMAIL_FROM_ADDRESS to enable outbound mail."
            )

        # Idempotency dedup — return existing row when caller passes a key
        # that has been used before for the same template + recipient.
        if idempotency_key is not None:
            existing = await self._find_existing(db, template_name, to, idempotency_key)
            if existing is not None:
                logger.info(
                    "email.send dedup",
                    extra={
                        "template": template_name,
                        "to": to,
                        "idempotency_key": idempotency_key,
                        "existing_audit_id": existing,
                    },
                )
                return existing

        # Render now so a missing template is caught synchronously rather
        # than surfacing as a worker failure later.
        rendered = self._render(template_name, context or {})

        audit_id = str(uuid.uuid4())
        audit = EmailSendLog(
            id=audit_id,
            to_address=to,
            template_name=template_name,
            backend_name="pending",  # actual backend recorded by worker on dispatch
            status="queued",
            idempotency_key=idempotency_key,
        )
        db.add(audit)
        try:
            await db.flush()
        except IntegrityError:
            # Lost a race against a concurrent send with the same idempotency
            # key. Roll back our row and return the winner's id.
            await db.rollback()
            existing = await self._find_existing(db, template_name, to, idempotency_key)
            if existing is None:
                # This shouldn't happen — the unique constraint that fired
                # implies a row exists — but surface a clear error rather
                # than retrying indefinitely.
                raise EmailServiceError(
                    f"Idempotency conflict on ({template_name}, {to}, {idempotency_key}) "
                    "but no existing row could be located"
                ) from None
            return existing

        await enqueue_job(
            self._queue,
            WorkloadType.EMAIL,
            payload={
                "audit_id": audit_id,
                "template_name": template_name,
                "to": to,
                "from_address": self._from_address,
                "from_name": self._from_name,
                "subject": rendered.subject,
                "body_text": rendered.body_text,
                "body_html": rendered.body_html,
                "idempotency_key": idempotency_key,
            },
        )
        return audit_id

    async def check_rate_limit(
        self,
        *,
        template_name: str,
        to: str,
        max_per_window: int,
        window_seconds: int,
    ) -> bool:
        """Check + consume a rate-limit slot for `(template_name, to)`.

        Returns True when the call is under the limit and a slot has been
        consumed. Returns False when the limit is hit (no slot consumed).

        Specific limits (e.g. "3 verification resends per hour") are set by
        callers — this layer ships the helper, not the policy.
        """
        key = f"email_ratelimit:{template_name}:{to}"
        # incr starts at 0 if the key does not exist, so the first call
        # returns 1. We set the TTL only on the first increment so the
        # window starts when the first call lands, not when the limit is hit.
        count = await self._cache.incr(key)
        if count == 1:
            await self._cache.expire(key, window_seconds)
        return count <= max_per_window

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _render(self, template_name: str, context: dict[str, Any]) -> RenderedTemplate:
        """Render `{name}/subject.txt`, `{name}/body.txt`, optional `{name}/body.html`.

        `subject.txt` and `body.txt` are required; `body.html` is optional —
        when present, the message is sent as multipart/alternative.
        """
        full_context = self._build_render_context(context)

        subject_tmpl = self._load_template(f"{template_name}/subject.txt", required=True)
        body_text_tmpl = self._load_template(f"{template_name}/body.txt", required=True)
        body_html_tmpl = self._load_template(f"{template_name}/body.html", required=False)

        subject = subject_tmpl.render(full_context).strip()
        body_text = body_text_tmpl.render(full_context)
        body_html = body_html_tmpl.render(full_context) if body_html_tmpl else None

        return RenderedTemplate(subject=subject, body_text=body_text, body_html=body_html)

    def _load_template(self, path: str, *, required: bool) -> Template | None:
        try:
            return self._env.get_template(path)
        except TemplateNotFound:
            if required:
                raise TemplateNotFoundError(
                    f"Required email template '{path}' not found under templates/email/"
                ) from None
            return None

    def _build_render_context(self, context: dict[str, Any]) -> dict[str, Any]:
        """Merge caller context with service-provided defaults.

        Service defaults (`app_name`, `support_email`) are added under their
        own keys but do not overwrite caller-supplied values — a future
        broadcast template may want to render with a custom app_name.
        """
        merged = {
            "app_name": self._app_name,
            "support_email": self._support_email,
        }
        merged.update(context)
        return merged

    async def _find_existing(
        self,
        db: AsyncSession,
        template_name: str,
        to: str,
        idempotency_key: str,
    ) -> str | None:
        stmt = select(EmailSendLog.id).where(
            EmailSendLog.template_name == template_name,
            EmailSendLog.to_address == to,
            EmailSendLog.idempotency_key == idempotency_key,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Worker-side helpers — used by the EMAIL workload handler in shu/worker.py
# ---------------------------------------------------------------------------


def build_message_from_payload(payload: dict[str, Any]) -> EmailMessage:
    """Reconstruct an `EmailMessage` from a queue job payload.

    Used by the EMAIL worker handler. Kept here so the payload shape lives
    in one module — `EmailService.send` writes it, the handler reads it.
    """
    from_address = payload["from_address"]
    from_name = payload.get("from_name")
    from_value = f"{from_name} <{from_address}>" if from_name else from_address

    return EmailMessage(
        to=payload["to"],
        subject=payload["subject"],
        body_text=payload["body_text"],
        body_html=payload.get("body_html"),
        from_address=from_value,
        idempotency_key=payload.get("idempotency_key"),
    )


async def mark_audit_sent(
    db: AsyncSession,
    audit_id: str,
    *,
    backend_name: str,
    provider_message_id: str | None,
) -> None:
    """Update the audit row from `queued` → `sent` after a successful dispatch."""
    from datetime import UTC, datetime

    await db.execute(
        update(EmailSendLog)
        .where(EmailSendLog.id == audit_id)
        .values(
            status=SendStatus.SENT.value,
            backend_name=backend_name,
            provider_message_id=provider_message_id,
            sent_at=datetime.now(UTC),
        )
    )


async def mark_audit_failed(
    db: AsyncSession,
    audit_id: str,
    *,
    backend_name: str,
    error_message: str,
) -> None:
    """Update the audit row from `queued` → `failed` after a permanent failure."""
    await db.execute(
        update(EmailSendLog)
        .where(EmailSendLog.id == audit_id)
        .values(
            status=SendStatus.FAILED.value,
            backend_name=backend_name,
            error_message=error_message[:2000],  # column has no length limit, but cap at sane value
        )
    )


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


def get_email_service_dependency(
    queue: QueueBackend | None = None,
    cache: CacheBackend | None = None,
) -> EmailService:
    """Construct an EmailService from the configured backends and settings.

    Used as a FastAPI dependency by consumer services (verification,
    password reset). Each request gets a new EmailService instance, but
    the underlying queue/cache singletons are shared via the existing
    `get_*_backend_dependency` helpers.

    Tests can pass `queue` and `cache` directly to bypass DI.
    """
    from ..core.cache_backend import get_cache_backend_dependency
    from ..core.config import get_settings_instance
    from ..core.queue_backend import get_queue_backend_dependency

    settings = get_settings_instance()
    return EmailService(
        queue=queue if queue is not None else get_queue_backend_dependency(),
        cache=cache if cache is not None else get_cache_backend_dependency(),
        from_address=settings.email_from_address,
        from_name=settings.email_from_name,
        app_name=settings.app_name,
    )
