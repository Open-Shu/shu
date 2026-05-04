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
        if job.attempts >= job.max_attempts:
            error_label = (
                exc.message  # type: ignore[attr-defined]
                if isinstance(exc, EmailTransportError)
                else str(exc)
            )
            session_local = get_async_session_local()
            async with session_local() as session:
                await mark_audit_failed(
                    session,
                    audit_id,
                    backend_name=backend.name,
                    error_message=f"max retries exceeded: {error_label}",
                )
                await session.commit()
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
