"""Drift guard for the composite-FK inventory.

If you landed here from a red CI run, jump to "If this test fails" below.

================================================================================
Background — what composite FKs do and why we care
================================================================================

A "composite FK" here means a foreign key on the pair ``(tenant_id, parent_id)``
rather than on ``parent_id`` alone. Every tenant-scoped child table has one
pointing at each tenant-scoped parent it references. Example::

    ALTER TABLE widget_assignments
      ADD CONSTRAINT widget_assignments_widget_id_tfk
      FOREIGN KEY (tenant_id, widget_id) REFERENCES widgets (tenant_id, id);

When Postgres validates that constraint on an INSERT/UPDATE, it runs the
equivalent of::

    SELECT 1 FROM widgets
    WHERE tenant_id = <child row's tenant_id>
      AND id        = <child row's widget_id>;

If no row matches, the write is rejected. That is the entire point: a child
row cannot reference a parent in a different tenant, because Postgres requires
the *pair* to exist in the parent table — and the parent's ``UNIQUE(tenant_id,
id)`` constraint (added by Section D of migration 009) guarantees each id
exists under exactly one tenant.

Without the composite FK, only the single-column ``widget_id → widgets.id``
FK runs. That accepts cross-tenant pointers: tenant_A's row pointing at
tenant_B's widget is a valid INSERT, because tenant_B's widget exists
*somewhere*. RLS doesn't close this hole — its ``WITH CHECK`` only verifies
the child row's own ``tenant_id`` matches the session; it doesn't constrain
what the row points at. Composite FKs are the structural backstop, enforced
at the storage layer regardless of RLS configuration, connection role, or
``tenant_context`` state.

================================================================================
Why this test exists
================================================================================

Migration 009 walks ``Base.metadata`` dynamically and emits ALTER statements
for every tenant-scoped→tenant-scoped FK that exists at apply time. That
solves the initial population once, in prod. But:

* 009 only runs once. It will not re-run when somebody adds a new FK
  six months from now.
* Every *future* tenant-scoped FK needs its own migration that adds the
  matching composite constraint. If that migration is forgotten, the new
  relationship ships with a silent hole in tenant isolation — no failure
  in tests, no warning in logs, just a quiet door between tenants.

This test is the forcing function that makes "model FK added without a
matching migration" impossible to merge unnoticed:

1. ``compute_composite_fk_inventory()`` walks ``Base.metadata`` and returns
   every tenant-scoped→tenant-scoped FK currently in the SQLAlchemy model
   graph — the live set.
2. ``composite_fk_inventory.json`` (committed next to the migrations) is the
   frozen list of FKs we have *already* covered with a constraint migration.
3. If the two differ, somebody added or removed a relevant FK in the model
   code without acking it here.

What this test does **not** check:

* It does not connect to Postgres and read ``pg_constraint`` — it can't tell
  you "constraint X is missing in the live DB."
* It does not parse migration files for ``ADD CONSTRAINT ..._tfk`` — it can't
  tell you "no migration adds this constraint."

The bridge between "test passes" and "constraint actually exists in prod" is
PR review discipline. A diff that updates the JSON without an accompanying
migration file is the signal a reviewer must reject.

================================================================================
If this test fails
================================================================================

On drift the test rewrites the JSON in place and fails with an actionable
message. You don't need to run anything special to regenerate the file —
it's already done. Your follow-ups are:

1. ``git add`` the updated ``composite_fk_inventory.json`` and commit it.

2. Look at the diff. If a row was **added**, the model graph has a new
   tenant-scoped FK that isn't yet covered by any constraint migration.
   Write a new migration that adds the composite FK::

       op.execute(
           "ALTER TABLE <child> "
           "ADD CONSTRAINT <child>_<col>_tfk "
           "FOREIGN KEY (tenant_id, <col>) "
           "REFERENCES <parent>(tenant_id, <parent_col>) NOT VALID"
       )
       op.execute("ALTER TABLE <child> VALIDATE CONSTRAINT <child>_<col>_tfk")

   The ``_tfk`` suffix is the convention used in migration 009 (Section D).
   If the parent doesn't already have ``UNIQUE(tenant_id, id)`` (it should,
   if it was tenant-scoped at the time 009 ran — but a *new* tenant-scoped
   parent introduced after 009 will need this added too), include::

       op.execute(
           "ALTER TABLE <parent> "
           "ADD CONSTRAINT <parent>_tenant_id_<parent_col>_unique "
           "UNIQUE (tenant_id, <parent_col>)"
       )

3. If a row was **removed**, the model relationship is gone and you should
   add a corresponding ``DROP CONSTRAINT`` in your migration — otherwise the
   constraint will linger in the DB pointing at columns that no longer
   participate in the relationship.

4. Reviewer's job: when this JSON changes, look for a matching migration
   file in the same PR. JSON diff without a migration diff = silent hole
   = block the PR.

Re-run the test once you've made the changes; it should pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import shu.auth.models
import shu.models  # noqa: F401 - register every model on Base.metadata

from tests.unit.migrations._inventory import compute_composite_fk_inventory

# Anchored on __file__ so the test runs from any cwd (pytest, IDE, CI).
_SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[4] / "migrations" / "versions" / "composite_fk_inventory.json"
)


def _serialize(inventory: list[tuple[str, str, str, str]]) -> str:
    return json.dumps([list(row) for row in inventory], indent=2) + "\n"


def test_composite_fk_inventory_matches_snapshot() -> None:
    live = compute_composite_fk_inventory()
    serialized = _serialize(live)

    committed = _SNAPSHOT_PATH.read_text() if _SNAPSHOT_PATH.exists() else ""
    if serialized == committed:
        return

    # Drift detected. Rewrite the snapshot in place so the dev can `git add`
    # it directly — failing here without rewriting would just send them
    # hunting for a regeneration command.
    _SNAPSHOT_PATH.write_text(serialized)

    raise AssertionError(
        f"Composite-FK inventory drifted from snapshot at {_SNAPSHOT_PATH}.\n"
        "The file has been regenerated in place. Two follow-ups are required:\n"
        "  1. `git add` the updated composite_fk_inventory.json and commit it.\n"
        "  2. If the new/removed FK isn't already covered by an existing\n"
        "     migration, add a migration that ALTER TABLE ... ADD CONSTRAINT\n"
        "     <child>_<col>_tfk FOREIGN KEY (tenant_id, <col>) REFERENCES\n"
        "     <parent>(tenant_id, <parent_col>) — otherwise the new FK\n"
        "     ships without composite-tenant protection.\n"
        "Re-run the test to confirm the snapshot is back in sync."
    )
