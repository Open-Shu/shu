"""EMAIL workload-type job handler (SHU-508).

Extracted from `shu/worker.py` so the dispatch table there stays a thin
routing layer rather than accumulating handler bodies. Mirrors the
precedent set by `re_embedding_handler.py` — `worker._handle_email_job`
is now a one-line shim that calls into this module.

Loads the audit row referenced by the queue payload, builds an
`EmailMessage`, invokes the configured `EmailBackend`, and updates the
audit row to `sent` or `failed` based on the outcome. Failure semantics
match the AC in SHU-508:

- `EmailTransportError` (transient — connection refused, 429, 5xx):
  raise so the queue rejects+requeues. On the *final* attempt
  (`job.attempts >= job.max_attempts`) mark the audit row `failed` first
  so its terminal state matches the queue's "give up" decision.
- `SendResult.status == FAILED` (permanent — bad recipient, 4xx):
  mark audit `failed`, return normally so the queue acknowledges. No
  retry on permanent failures.
- Any unexpected exception: same terminal-attempt treatment as transient
  errors — mark `failed` on the final retry, raise either way.
"""

from __future__ import annotations

from .core.database import get_async_session_local
from .core.email import get_email_backend
from .core.email.backend import EmailTransportError, SendStatus
from .core.logging import get_logger
from .core.queue_backend import Job
from .services.email_service import (
    build_message_from_payload,
    mark_audit_failed,
    mark_audit_sent,
)

logger = get_logger(__name__)


async def handle_email_job(job: Job) -> None:
    """Run a single EMAIL workload job. See module docstring for failure semantics."""
    payload = job.payload or {}
    audit_id = payload.get("audit_id")
    if not audit_id:
        raise ValueError("EMAIL job missing audit_id in payload")

    backend = await get_email_backend()
    message = build_message_from_payload(payload)

    # Producer transaction may not yet be visible — EmailService.send
    # flushes the audit row but the producer's outer commit happens
    # AFTER the queue job has already been published to Redis. If the
    # worker dequeues before that commit, the audit row is invisible
    # to its session. Sending the email anyway would be a correctness
    # bug:
    #   * If the producer's transaction later commits, the email was
    #     sent for a token that exists; mark_audit_sent would UPDATE
    #     zero rows and the audit log would stay 'queued' forever.
    #   * If the producer rolls back, the email was sent for a token
    #     that NEVER exists — user clicks the link and gets "invalid".
    # Look up the audit row first; raise a transient error if missing
    # so the queue retries until the producer commit becomes visible
    # or max_attempts is hit. Idempotency: skip if the row is already
    # in a terminal state (a previous attempt got there first).
    session_local = get_async_session_local()
    async with session_local() as session:
        from sqlalchemy import select

        from .models.email_send_log import EmailSendLog

        existing = (await session.execute(select(EmailSendLog).where(EmailSendLog.id == audit_id))).scalar_one_or_none()

    if existing is None:
        # Producer transaction not yet visible (or rolled back). Treat
        # as transient — the queue will retry. If the producer rolled
        # back, max_attempts will eventually expire and the email is
        # never sent.
        logger.info(
            "Email audit row not yet visible — producer transaction probably " "still in flight; will retry",
            extra={
                "event": "email.audit_not_visible",
                "audit_id": audit_id,
                "job_id": job.id,
                "attempt": job.attempts,
                "max_attempts": job.max_attempts,
            },
        )
        raise EmailTransportError(
            f"audit row {audit_id} not yet visible (producer transaction pending)",
            details={"audit_id": audit_id},
        )

    if existing.status in ("sent", "failed"):
        # Idempotency: a previous attempt already updated the row to a
        # terminal state. Don't re-send — just ack the job.
        logger.info(
            "Email job already terminal; skipping",
            extra={
                "event": "email.job_already_terminal",
                "audit_id": audit_id,
                "status": existing.status,
            },
        )
        return

    logger.info(
        "Processing email job",
        extra={
            "job_id": job.id,
            "audit_id": audit_id,
            "template": payload.get("template_name"),
            "to": payload.get("to"),
            "backend": backend.name,
            "attempt": job.attempts,
            "max_attempts": job.max_attempts,
        },
    )

    try:
        result = await backend.send_email(message)
    except Exception as exc:
        # On the final attempt, the queue will reject without requeue —
        # mark the audit row failed so its terminal state matches the
        # queue's "give up" decision. Otherwise leave the row queued for
        # the next retry to update.
        #
        # The audit-row update is wrapped in its own try/except so a DB
        # failure during the terminal mark-failed write cannot mask the
        # original transport exception — the queue must see the actual
        # transport error to make the right retry decision (and the
        # operator must see it in logs to debug why send failed).
        if job.attempts >= job.max_attempts:
            error_label = (
                exc.message  # type: ignore[attr-defined]
                if isinstance(exc, EmailTransportError)
                else str(exc)
            )
            try:
                session_local = get_async_session_local()
                async with session_local() as session:
                    await mark_audit_failed(
                        session,
                        audit_id,
                        backend_name=backend.name,
                        error_message=f"max retries exceeded: {error_label}",
                    )
                    await session.commit()
            except Exception as audit_exc:
                logger.error(
                    "Failed to mark audit row failed on terminal attempt; "
                    "the row will remain in 'queued' state. Original transport "
                    "error is re-raised below so the queue still sees it.",
                    extra={
                        "event": "email.terminal_audit_update_failed",
                        "audit_id": audit_id,
                        "audit_error": str(audit_exc),
                        "original_error": error_label,
                    },
                )
        raise

    session_local = get_async_session_local()
    async with session_local() as session:
        if result.status == SendStatus.SENT:
            await mark_audit_sent(
                session,
                audit_id,
                backend_name=result.backend_name,
                provider_message_id=result.provider_message_id,
            )
        else:
            # status == FAILED — permanent. Record and ack (no retry).
            await mark_audit_failed(
                session,
                audit_id,
                backend_name=result.backend_name,
                error_message=result.error_message or "unknown error",
            )
            logger.warning(
                "Email permanent failure",
                extra={
                    "job_id": job.id,
                    "audit_id": audit_id,
                    "backend": result.backend_name,
                    "error": result.error_message,
                },
            )
        await session.commit()
