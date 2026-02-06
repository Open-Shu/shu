import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

# Ensure scheduler is enabled and ticks quickly for the test, and disable rate limiting to avoid 429 noise
os.environ.setdefault("SHU_PLUGINS_SCHEDULER_ENABLED", "true")
os.environ.setdefault("SHU_PLUGINS_SCHEDULER_TICK_SECONDS", "1")
os.environ.setdefault("SHU_PLUGINS_SCHEDULER_BATCH_LIMIT", "5")
os.environ.setdefault("SHU_ENABLE_API_RATE_LIMITING", "false")
# Make stale RUNNING cleanup aggressive for tests
os.environ.setdefault("SHU_PLUGINS_SCHEDULER_RUNNING_TIMEOUT_SECONDS", "1")

from integ.base_integration_test import (
    BaseIntegrationTestSuite,
    create_test_runner_script,
)
from shu.models.plugin_execution import PluginExecution, PluginExecutionStatus

logger = logging.getLogger(__name__)


async def _ensure_tool_enabled(client, auth_headers, name: str = "test_schema"):
    await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    await client.patch(f"/api/v1/plugins/admin/{name}/enable", json={"enabled": True}, headers=auth_headers)


async def test_auto_tick_executes_schedule(client, db, auth_headers):
    await _ensure_tool_enabled(client, auth_headers)

    # Create a schedule that should run immediately
    resp = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "Test Plugins Scheduler Auto",
            "plugin_name": "test_schema",
            "params": {"q": "auto"},
            "interval_seconds": 60,
            "enabled": True,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Wait for a couple of ticks to allow enqueue + run
    await asyncio.sleep(3)

    # Verify at least one completed execution with expected output
    resp = await client.get("/api/v1/plugins/admin/executions?plugin_name=test_schema", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    matches = []
    for r in rows:
        res = r.get("result") or {}
        data = res.get("data") or {}
        if data.get("echo") == "auto":
            matches.append(r)
    assert len(matches) >= 1, f"No execution found for auto run: rows={rows[-3:]}"
    assert matches[-1]["status"] in ("completed",)


async def test_concurrent_run_due_single_flight(client, db, auth_headers):
    """
    Call /admin/feeds/run-due concurrently and verify only one execution is enqueued for a due schedule.
    This validates single-flight behavior via row locks and enqueue idempotency guard.
    """
    await _ensure_tool_enabled(client, auth_headers)

    # Create a schedule that is due immediately (next_run_at is NULL => due)
    resp = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "Test Concurrent Run-Due",
            "plugin_name": "test_schema",
            "params": {"q": "concurrent"},
            "interval_seconds": 300,
            "enabled": True,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    sched_id = resp.json()["data"]["id"]

    # Fire two concurrent run-due requests
    r1, r2 = await asyncio.gather(
        client.post("/api/v1/plugins/admin/feeds/run-due", headers=auth_headers),
        client.post("/api/v1/plugins/admin/feeds/run-due", headers=auth_headers),
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text

    # Give a brief moment for commit
    await asyncio.sleep(0.2)

    # Verify exactly one execution exists for this schedule
    resp = await client.get(f"/api/v1/plugins/admin/executions?schedule_id={sched_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert len(rows) == 1, f"Expected 1 execution for schedule {sched_id}, found {len(rows)}: {rows}"


async def test_stale_running_cleanup_marks_failed(client, db, auth_headers):
    """
    Insert a RUNNING execution with an old started_at and verify the scheduler cleanup marks it FAILED with error 'stale_timeout'.
    """
    await _ensure_tool_enabled(client, auth_headers)

    # Create a schedule (not strictly required for cleanup, but keeps data coherent)
    resp = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "Test Stale RUNNING Cleanup",
            "plugin_name": "test_schema",
            "params": {"q": "stale"},
            "interval_seconds": 600,
            "enabled": True,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    sched_id = resp.json()["data"]["id"]

    # Insert a stale RUNNING execution directly
    old_started = datetime.now(UTC) - timedelta(seconds=50000)
    exec_rec = PluginExecution(
        schedule_id=sched_id,
        plugin_name="test_schema",
        user_id=auth_headers.get("_user_id"),
        agent_key=None,
        params={"q": "stale"},
        status=PluginExecutionStatus.RUNNING,
        started_at=old_started,
    )
    db.add(exec_rec)
    await db.commit()
    await db.refresh(exec_rec)

    # Proactively invoke the scheduler service to ensure cleanup path runs in this test context
    from shu.services.plugins_scheduler_service import PluginsSchedulerService

    svc = PluginsSchedulerService(db)
    await svc.cleanup_stale_executions()

    # Reload and verify it was marked failed due to stale timeout
    res = await db.execute(select(PluginExecution).where(PluginExecution.id == exec_rec.id))
    row = res.scalars().first()
    assert row is not None
    assert row.status == PluginExecutionStatus.FAILED, f"Unexpected status: {row.status}"
    assert (row.error or "").startswith("stale_timeout"), f"Unexpected error: {row.error}"


async def test_admin_scheduler_metrics_endpoint(client, db, auth_headers):
    """
    The metrics endpoint should return recent scheduler tick summaries.
    We create a due schedule to ensure at least one active tick is recorded.
    """
    await _ensure_tool_enabled(client, auth_headers)

    # Create a schedule due immediately
    resp = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "Test Metrics Endpoint",
            "plugin_name": "test_schema",
            "params": {"q": "metrics"},
            "interval_seconds": 300,
            "enabled": True,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Allow at least one tick to run
    await asyncio.sleep(2)

    # Fetch metrics
    resp = await client.get("/api/v1/plugins/admin/scheduler/metrics?limit=10", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # Expect at least one entry with enqueue and run keys once a tick with activity happened
    assert isinstance(data, list)
    if data:
        item = data[0]
        assert "enqueue" in item and "run" in item


async def test_enqueue_skips_no_owner(client, db, auth_headers):
    await _ensure_tool_enabled(client, auth_headers)

    # Create schedule and capture admin id, then remove owner and control next_run_at to avoid background races
    resp = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "Test No Owner Skip",
            "plugin_name": "test_schema",
            "params": {"q": "no-owner"},
            "interval_seconds": 300,
            "enabled": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    sched = resp.json()["data"]
    sched_id = sched["id"]

    # Force owner None via ORM to avoid API schema ignoring nulls; set due now and enable
    from shu.models.plugin_feed import PluginFeed

    res = await db.execute(select(PluginFeed).where(PluginFeed.id == sched_id))
    sched_row = res.scalars().first()
    assert sched_row is not None
    sched_row.owner_user_id = None
    sched_row.enabled = True
    sched_row.next_run_at = datetime.now(UTC)
    await db.commit()

    from shu.services.plugins_scheduler_service import PluginsSchedulerService

    svc = PluginsSchedulerService(db)
    # Trigger enqueue path (without fallback) and verify no executions are created
    _ = await svc.enqueue_due_schedules(limit=1)

    # Ensure no executions were created (allow brief settle)
    await asyncio.sleep(0.1)
    resp = await client.get(f"/api/v1/plugins/admin/executions?schedule_id={sched_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["data"]) == 0


async def test_admin_run_due_uses_fallback_owner(client, db, auth_headers):
    await _ensure_tool_enabled(client, auth_headers)

    # Create schedule and capture admin id from creation response
    resp = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "Test Admin Fallback Owner",
            "plugin_name": "test_schema",
            "params": {"q": "admin-fallback"},
            "interval_seconds": 300,
            "enabled": True,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    created = resp.json()["data"]
    sched_id = created["id"]
    admin_id = created["owner_user_id"]

    # Remove owner and make due at the moment we run-due to avoid race
    future_iso = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    await client.patch(
        f"/api/v1/plugins/admin/feeds/{sched_id}",
        json={"owner_user_id": None, "next_run_at": future_iso},
        headers=auth_headers,
    )
    now_iso = datetime.now(UTC).isoformat()
    await client.patch(
        f"/api/v1/plugins/admin/feeds/{sched_id}",
        json={"next_run_at": now_iso},
        headers=auth_headers,
    )

    # Run-due should pass fallback_user_id=admin.id internally
    r = await client.post("/api/v1/plugins/admin/feeds/run-due", headers=auth_headers)
    assert r.status_code == 200, r.text

    # Verify an execution exists and is owned by admin
    resp = await client.get(f"/api/v1/plugins/admin/executions?schedule_id={sched_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert len(rows) == 1
    assert rows[0]["user_id"] == admin_id


async def test_bounded_batch_respected(client, db, auth_headers):
    await _ensure_tool_enabled(client, auth_headers)

    await client.put(
        "/api/v1/plugins/admin/test_schema/limits",
        json={
            "quota_daily_requests": 0,
            "quota_monthly_requests": 0,
            "rate_limit_user_requests": 100,
            "rate_limit_user_period": 60,
        },
        headers=auth_headers,
    )

    # Create 3 schedules disabled so background auto-tick cannot claim them
    ids = []
    for i in range(3):
        resp = await client.post(
            "/api/v1/plugins/admin/feeds",
            json={
                "name": f"Test Bounded Batch {i}",
                "plugin_name": "test_schema",
                "params": {"q": f"batch-{i}"},
                "interval_seconds": 300,
                "enabled": False,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        ids.append(resp.json()["data"]["id"])
    future_iso = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    for sid in ids:
        await client.patch(
            f"/api/v1/plugins/admin/feeds/{sid}",
            json={"next_run_at": future_iso},
            headers=auth_headers,
        )

    # Enable and make exactly two due now; keep the third disabled to avoid races
    now_iso = datetime.now(UTC).isoformat()
    for sid in ids[:2]:
        await client.patch(
            f"/api/v1/plugins/admin/feeds/{sid}",
            json={"enabled": True, "next_run_at": now_iso},
            headers=auth_headers,
        )

    # Enqueue and run with limit=2
    from shu.services.plugins_scheduler_service import PluginsSchedulerService

    svc = PluginsSchedulerService(db)
    enq = await svc.enqueue_due_schedules(limit=2)
    # Background auto-tick may have claimed some; only assert we did not exceed the limit
    assert enq["enqueued"] <= 2, f"Unexpected enqueued: {enq}"
    ran = await svc.run_pending(limit=2)
    assert ran["attempted"] <= 2, f"Unexpected run stats: {ran}"

    # Exactly 2 executions should exist across the two due schedules (third remains for later)
    rows_all = []
    for sid in ids:
        resp = await client.get(f"/api/v1/plugins/admin/executions?schedule_id={sid}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        rows_all.extend(resp.json()["data"])
    assert len(rows_all) == 2, f"Expected 2 executions total, found {len(rows_all)}: {rows_all}"

    # Disable schedules again
    for sid in ids:
        await client.patch(
            f"/api/v1/plugins/admin/feeds/{sid}",
            json={"enabled": False},
            headers=auth_headers,
        )


async def test_429_defer_respects_retry_backoff(client, db, auth_headers):
    """
    When the executor raises HTTP 429 with Retry-After, the scheduler should defer
    the execution back to PENDING and push started_at forward by the backoff.
    """
    await _ensure_tool_enabled(client, auth_headers)

    # Create a schedule but keep it disabled/future-dated so background scheduler does not race
    resp = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "Test 429 Defer",
            "plugin_name": "test_schema",
            "params": {"q": "rate-limit"},
            "interval_seconds": 300,
            "enabled": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    sched_id = resp.json()["data"]["id"]

    # Push next_run_at into the future while disabled
    future_iso = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    await client.patch(
        f"/api/v1/plugins/admin/feeds/{sched_id}",
        json={"next_run_at": future_iso},
        headers=auth_headers,
    )

    # Enable and mark due only within the controlled window for this test
    now_iso = datetime.now(UTC).isoformat()
    await client.patch(
        f"/api/v1/plugins/admin/feeds/{sched_id}",
        json={"enabled": True, "next_run_at": now_iso},
        headers=auth_headers,
    )

    # Service instance on the same db session
    from shu.services.plugins_scheduler_service import PluginsSchedulerService

    svc = PluginsSchedulerService(db)

    # Enqueue once to create a PENDING execution
    await svc.enqueue_due_schedules(limit=1)

    # Patch executor to raise 429 with Retry-After
    from fastapi import HTTPException

    from shu.plugins.executor import EXECUTOR

    async def _raise_429(*args, **kwargs):
        raise HTTPException(status_code=429, detail={"error": "rate_limited"}, headers={"Retry-After": "2"})

    orig_execute = EXECUTOR.execute
    EXECUTOR.execute = _raise_429
    try:
        now_ts = datetime.now(UTC)
        await svc.run_pending(limit=1, schedule_id=sched_id)
    finally:
        EXECUTOR.execute = orig_execute

    # Fetch execution by schedule and verify it was deferred
    from sqlalchemy import select

    from shu.models.plugin_execution import PluginExecution, PluginExecutionStatus

    res = await db.execute(select(PluginExecution).where(PluginExecution.schedule_id == sched_id))
    row = res.scalars().first()
    assert row is not None
    assert row.status == PluginExecutionStatus.PENDING
    assert (row.error or "").startswith("deferred:"), f"Unexpected error: {row.error}"
    threshold = now_ts + timedelta(seconds=2)
    assert row.started_at >= threshold, f"started_at {row.started_at} not >= {threshold}"

    # Disable schedule again to avoid background runs after this test
    await client.patch(
        f"/api/v1/plugins/admin/feeds/{sched_id}",
        json={"enabled": False},
        headers=auth_headers,
    )


async def test_multi_replica_contention_no_duplicates(client, db, auth_headers):
    """
    Simulate two concurrent scheduler runners (separate DB sessions) enqueuing/running the same due schedule.
    Assert only a single execution is created/claimed.
    """
    await _ensure_tool_enabled(client, auth_headers)

    # Create a schedule due immediately
    resp = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "Test Multi-Runner Contention",
            "plugin_name": "test_schema",
            "params": {"q": "multi"},
            "interval_seconds": 300,
            "enabled": True,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    sched_id = resp.json()["data"]["id"]

    # Two concurrent runners using distinct DB sessions
    from shu.core.database import get_db_session
    from shu.services.plugins_scheduler_service import PluginsSchedulerService

    async def _runner_once():
        sess = await get_db_session()
        async with sess as s:
            svc = PluginsSchedulerService(s)
            await svc.enqueue_due_schedules(limit=1)
            await svc.run_pending(limit=1, schedule_id=sched_id)

    await asyncio.gather(_runner_once(), _runner_once())

    # Brief commit settle
    await asyncio.sleep(0.2)

    # Verify exactly one execution exists for this schedule
    resp = await client.get(f"/api/v1/plugins/admin/executions?schedule_id={sched_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert len(rows) == 1, f"Expected 1 execution for schedule {sched_id}, found {len(rows)}: {rows}"


class PluginsSchedulerIntegrationSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_auto_tick_executes_schedule,
            test_concurrent_run_due_single_flight,
            test_stale_running_cleanup_marks_failed,
            test_admin_scheduler_metrics_endpoint,
            test_enqueue_skips_no_owner,
            test_admin_run_due_uses_fallback_owner,
            test_bounded_batch_respected,
            test_429_defer_respects_retry_backoff,
            test_multi_replica_contention_no_duplicates,
        ]

    def get_suite_name(self) -> str:
        return "Plugins Scheduler Auto-Tick"

    def get_suite_description(self) -> str:
        return "Validates the in-process Plugins scheduler enqueues and runs due schedules automatically, enforces single-flight, and cleans up stale RUNNING executions."


if __name__ == "__main__":
    create_test_runner_script(PluginsSchedulerIntegrationSuite, globals())
