"""Tests for shu.services.audit_logger.

Coverage focus: the DefaultAuditLogger preserves the expected log fields
and re-raises on transport failure. The Protocol shape itself isn't
exercised — that's framework behavior, not our code.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from shu.services.audit_logger import AuditLogEmitError, DefaultAuditLogger


@pytest.mark.asyncio
async def test_log_emits_expected_record_fields(caplog: pytest.LogCaptureFixture) -> None:
    logger = DefaultAuditLogger()

    with caplog.at_level(logging.INFO, logger="shu.services.audit_logger"):
        await logger.log(
            event="impersonate_tenant_open",
            actor="actor-uuid",
            target="tenant-uuid",
            reason="debugging ticket #123",
            request_id="req-xyz",
        )

    records = [r for r in caplog.records if r.name == "shu.services.audit_logger"]
    assert len(records) == 1
    record = records[0]
    assert record.audit is True
    assert record.event == "impersonate_tenant_open"
    assert record.actor == "actor-uuid"
    assert record.target == "tenant-uuid"
    assert record.reason == "debugging ticket #123"
    assert record.request_id == "req-xyz"


@pytest.mark.asyncio
async def test_log_reraises_transport_failure_as_audit_emit_error() -> None:
    """A failing handler must surface as AuditLogEmitError.

    The TenantAdminService relies on this to fail closed: if the audit
    record can't be emitted, the cross-tenant operation must not proceed.
    """
    logger = DefaultAuditLogger()

    boom = MagicMock(side_effect=RuntimeError("handler exploded"))
    with (
        patch("shu.services.audit_logger._logger.info", boom),
        pytest.raises(AuditLogEmitError) as exc_info,
    ):
        await logger.log(event="impersonate_tenant_open", actor="actor", reason="x")

    # Confirm the underlying transport error is chained, not swallowed —
    # operators need it to diagnose what broke.
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "handler exploded" in str(exc_info.value.__cause__)


@pytest.mark.asyncio
async def test_log_accepts_target_none_for_cross_tenant_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cross-tenant queries span every tenant, so ``target`` is intentionally None."""
    logger = DefaultAuditLogger()

    with caplog.at_level(logging.INFO, logger="shu.services.audit_logger"):
        await logger.log(event="cross_tenant_query_open", actor="actor", reason="usage report")

    records: list[Any] = [r for r in caplog.records if r.name == "shu.services.audit_logger"]
    assert len(records) == 1
    assert records[0].target is None
    assert records[0].event == "cross_tenant_query_open"
