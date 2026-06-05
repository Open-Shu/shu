"""Drift guards keeping the codebase in sync with the tenant-isolation invariants.

Four frozen artifacts must all agree with reality for tenant isolation
to hold:

1. ``GLOBAL_TABLES_ALLOWLIST`` below — every model must be either
   tenant-scoped (carries the ``tenant_id`` column from
   ``TenantScopedMixin``) or explicitly listed here as a shared catalog.
2. ``_TENANT_SCOPED_TABLES`` / ``_COMPOSITE_FKS`` in
   ``backend/migrations/versions/009_00011_tenant_isolation.py`` — that
   migration freezes these so it stays deterministic on fresh installs that
   land after later model edits.
3. ``composite_fk_inventory.json`` next to the migrations — the
   committed snapshot of every tenant-scoped → tenant-scoped composite FK.
4. Inline ``# noqa: STRAY-TENANT-ID`` / ``# noqa: LISTEN-NOTIFY`` markers
   on the few legitimate sites — source-code policies enforcing "no stray
   ``settings.tenant_id`` readers" and "no LISTEN/NOTIFY pinned-connection
   leak surfaces." Pragma-based (vs. line-number allowlist) so reformatting
   doesn't drift the check.

If any falls out of sync with reality, new tables / relationships / code
sites ship with a silent tenant-isolation hole. Each test below names
exactly what's drifted and what to do about it.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import shu.auth.models  # noqa: F401 - register every model on Base.metadata
import shu.models  # noqa: F401 - same
from shu.core.database import Base

_MIGRATION = importlib.import_module("migrations.versions.009_00011_tenant_isolation")

# Anchored on __file__ so the tests run from any cwd (pytest, IDE, CI).
_BACKEND_ROOT = Path(__file__).resolve().parents[4]
_SNAPSHOT_PATH = _BACKEND_ROOT / "migrations" / "versions" / "composite_fk_inventory.json"
_SHU_ROOT = _BACKEND_ROOT / "src" / "shu"


# ---------------------------------------------------------------------------
# Model-graph walkers — inlined here because this is the only consumer.
# ---------------------------------------------------------------------------

# (child_table, child_column, parent_table, parent_column)
CompositeFk = tuple[str, str, str, str]


def tenant_scoped_table_names() -> set[str]:
    return {name for name, table in Base.metadata.tables.items() if "tenant_id" in table.columns}


def compute_composite_fk_inventory() -> list[CompositeFk]:
    """Return every FK whose child and parent are both tenant-scoped.

    Self-referential FKs and FKs to global tables (``llm_providers``,
    ``plugin_definitions``, etc.) are excluded — composite tenant matching
    is only meaningful between two tenant-stamped rows.
    """
    tenant_scoped = tenant_scoped_table_names()
    result: list[CompositeFk] = []
    for child_name, child_table in Base.metadata.tables.items():
        if child_name not in tenant_scoped:
            continue
        for col in child_table.columns:
            for fk in col.foreign_keys:
                parent_name = fk.column.table.name
                if parent_name == child_name:
                    # Self-referential FKs (e.g. a tree-shaped table where a
                    # row points at its parent in the same table) don't need
                    # a composite (tenant_id, id) FK: the parent and child
                    # rows share the same physical table, so RLS already
                    # filters the FK target to the current tenant's rows.
                    # An extra (tenant_id, id) → (tenant_id, id) self-FK
                    # would be redundant with the single-column self-FK
                    # plus RLS's tenant-row filter.
                    continue
                if parent_name not in tenant_scoped:
                    continue  # FK to global table — composite scoping not meaningful
                result.append((child_name, col.name, parent_name, fk.column.name))
    return sorted(result)


# ---------------------------------------------------------------------------
# (1) GLOBAL_TABLES_ALLOWLIST — every model is tenant-scoped or explicitly global
# ---------------------------------------------------------------------------

# Tables that intentionally lack ``tenant_id``. Adding a row here is a
# deliberate "this is shared across all tenants" call; review accordingly.
#
# Decision rubric for "should this table be global?":
# * Catalog / definition data shared across tenants (LLM models, plugin
#   definitions, the tenants catalog itself) → global.
# * Anything else, including anything that mentions a user, conversation,
#   document, workspace, plan, billing record, etc. → tenant-scoped.
GLOBAL_TABLES_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Pre-existing global catalogs:
        "llm_models",
        "llm_providers",
        "llm_provider_type_definitions",
        "plugin_definitions",
        # The tenants catalog itself — every other table's FK to tenants(id)
        # would be circular if this one were tenant-scoped.
        "tenants",
        # Alembic bookkeeping:
        "alembic_version",
    }
)


def test_every_table_is_classified_as_tenant_scoped_or_explicitly_global() -> None:
    """When a new model lands, this test fails until the author either adds
    ``TenantScopedMixin`` to the model OR adds the table name to
    ``GLOBAL_TABLES_ALLOWLIST``. Silent unclassification is the worst
    failure mode — an unscoped table that should have been tenant-scoped
    becomes a cross-tenant leak the moment any tenant writes to it."""
    tenant_scoped: set[str] = set()
    unclassified: set[str] = set()

    for name, table in Base.metadata.tables.items():
        if "tenant_id" in table.columns:
            tenant_scoped.add(name)
        elif name in GLOBAL_TABLES_ALLOWLIST:
            continue
        else:
            unclassified.add(name)

    assert not unclassified, (
        f"Unclassified tables (neither tenant-scoped nor in the global allowlist): "
        f"{sorted(unclassified)}. Decide: does this table need RLS scoping "
        "(add TenantScopedMixin) or is it a shared catalog (add to "
        "GLOBAL_TABLES_ALLOWLIST in this file)?"
    )

    # Sanity floor — if this assertion ever fires, somebody nuked the
    # tenant-scoped tables and the more useful failure above didn't trigger.
    assert len(tenant_scoped) > 10


def test_global_allowlist_only_contains_real_tables() -> None:
    """Catch stale allowlist entries — a table renamed or dropped should
    fall out of the allowlist too, otherwise drift accumulates silently."""
    real_table_names = set(Base.metadata.tables.keys()) | {"alembic_version"}
    stale = GLOBAL_TABLES_ALLOWLIST - real_table_names
    assert not stale, f"GLOBAL_TABLES_ALLOWLIST entries that no longer exist: {sorted(stale)}"


# ---------------------------------------------------------------------------
# (2) Migration 009 frozen inventory — _TENANT_SCOPED_TABLES + _COMPOSITE_FKS
# ---------------------------------------------------------------------------
#
# Migration 009 freezes its inventory inline rather than computing from
# ``Base.metadata`` at apply time. The temptation to compute breaks
# determinism two ways:
#
# * **Fresh installs after a model is added but before a corresponding
#   migration ships:** Alembic runs 009 first, then later migrations that
#   create the new table. 009 sees the new model in metadata and tries to
#   ALTER a table that doesn't exist yet → crash.
# * **Already-migrated DBs:** 009 ran historically when the table didn't
#   exist. A later migration creates the table without RLS/FK/index —
#   silent tenant-isolation hole.
#
# Every future tenant-scoped table needs its own migration that adds the
# tenant_id column, index, FK, and RLS policy. The post-009 allowlists
# below track what's been added since, each with a follow-on migration.
# Together they must equal the live model graph.

# Post-009 tenant-scoped tables, each with its own follow-on migration.
# Empty today; populated as the schema evolves.
_ALLOWED_POST_009_TABLES: frozenset[str] = frozenset()

# Post-009 composite FKs, each with its own follow-on migration. Tuples are
# (child_table, child_column, parent_table, parent_column).
_ALLOWED_POST_009_FKS: frozenset[tuple[str, str, str, str]] = frozenset()


def test_009_frozen_table_list_matches_live_graph() -> None:
    """``_TENANT_SCOPED_TABLES`` + post-009 allowlist must cover the live graph.

    On drift:
    * If 009_00011 is still downgrade-able, edit ``_TENANT_SCOPED_TABLES`` in
      ``backend/migrations/versions/009_00011_tenant_isolation.py``.
    * Otherwise, write a new migration covering each table (tenant_id +
      index + FK to tenants(id) + RLS policy) and add it to
      ``_ALLOWED_POST_009_TABLES`` above.
    """
    frozen = set(_MIGRATION._TENANT_SCOPED_TABLES)
    expected = frozen | _ALLOWED_POST_009_TABLES
    live = tenant_scoped_table_names()

    missing_from_migration = live - expected
    stale_in_migration = expected - live

    if not missing_from_migration and not stale_in_migration:
        return

    parts: list[str] = []
    if missing_from_migration:
        parts.append(
            f"Live model graph has tenant-scoped tables not covered by 009 or any\n"
            f"post-009 allowlist:\n  {sorted(missing_from_migration)}\n"
            "  → If 009 is still downgrade-able, add them to _TENANT_SCOPED_TABLES.\n"
            "  → Otherwise, write a new migration covering each (see the comment\n"
            "    above), then add them to _ALLOWED_POST_009_TABLES."
        )
    if stale_in_migration:
        parts.append(
            f"009 or post-009 allowlist references tables not in the live model graph:\n"
            f"  {sorted(stale_in_migration)}\n"
            "  → A model was renamed/deleted. Either restore the model, or write\n"
            "    a migration that drops the no-longer-needed table/policy."
        )
    raise AssertionError("\n\n".join(parts))


def test_009_frozen_composite_fk_list_matches_live_graph() -> None:
    """``_COMPOSITE_FKS`` + post-009 allowlist must cover every tenant-scoped FK.

    A "composite FK" is on the pair ``(tenant_id, parent_id)`` rather than
    on ``parent_id`` alone. It's what stops tenant_A's row from pointing
    at tenant_B's parent: the FK requires the *pair* to exist in the
    parent, and the parent's ``UNIQUE(tenant_id, id)`` guarantees each
    id is unique within a tenant. Without it, only the single-column FK
    runs and cross-tenant pointers are accepted — RLS doesn't help here,
    its ``WITH CHECK`` only constrains the child's own ``tenant_id``.
    """
    frozen = set(_MIGRATION._COMPOSITE_FKS)
    expected = frozen | _ALLOWED_POST_009_FKS
    live = set(compute_composite_fk_inventory())

    missing_from_migration = live - expected
    stale_in_migration = expected - live

    if not missing_from_migration and not stale_in_migration:
        return

    parts: list[str] = []
    if missing_from_migration:
        rows = "\n".join(f"  {row}" for row in sorted(missing_from_migration))
        parts.append(
            "Live model graph has tenant-scoped composite FKs not covered by 009 or\n"
            f"any post-009 allowlist:\n{rows}\n"
            "  → If 009 is still downgrade-able, add them to _COMPOSITE_FKS.\n"
            "  → Otherwise, write a migration adding `(tenant_id, <col>)` FKs and\n"
            "    parent `UNIQUE(tenant_id, id)` constraints, then add the rows\n"
            "    to _ALLOWED_POST_009_FKS."
        )
    if stale_in_migration:
        rows = "\n".join(f"  {row}" for row in sorted(stale_in_migration))
        parts.append(
            "009 or post-009 allowlist references composite FKs not in the live\n"
            f"model graph:\n{rows}\n"
            "  → A relationship was renamed/removed. Either restore it, or write\n"
            "    a migration that DROPs the constraint."
        )
    raise AssertionError("\n\n".join(parts))


# ---------------------------------------------------------------------------
# (3) composite_fk_inventory.json snapshot
# ---------------------------------------------------------------------------
#
# Migration 009 covers FKs that existed at apply time. Every *future*
# tenant-scoped FK needs its own migration adding the matching composite
# constraint — if forgotten, the new relationship ships with a silent hole.
# The committed snapshot is the bridge: a JSON diff without an accompanying
# migration file is the signal a reviewer must reject.
#
# This test does NOT connect to Postgres or parse migration files — it
# checks model graph against snapshot. PR review discipline closes the loop.


def _serialize(inventory: list[tuple[str, str, str, str]]) -> str:
    return json.dumps([list(row) for row in inventory], indent=2) + "\n"


def test_composite_fk_inventory_matches_snapshot() -> None:
    """On drift the snapshot is rewritten in place so the dev can ``git add``
    it directly. Two follow-ups:
    1. ``git add`` the regenerated ``composite_fk_inventory.json``.
    2. If the new FK isn't already covered by a migration, write one that
       adds ``<child>_<col>_tfk FOREIGN KEY (tenant_id, <col>) REFERENCES
       <parent>(tenant_id, <parent_col>)``. The ``_tfk`` suffix is the
       convention from migration 009 Section D.
    """
    live = compute_composite_fk_inventory()
    serialized = _serialize(live)

    committed = _SNAPSHOT_PATH.read_text() if _SNAPSHOT_PATH.exists() else ""
    if serialized == committed:
        return

    _SNAPSHOT_PATH.write_text(serialized)

    raise AssertionError(
        f"Composite-FK inventory drifted from snapshot at {_SNAPSHOT_PATH}.\n"
        "The file has been regenerated in place. Two follow-ups are required:\n"
        "  1. `git add` the updated composite_fk_inventory.json and commit it.\n"
        "  2. If the new/removed FK isn't already covered by an existing\n"
        "     migration, add one (see ADD CONSTRAINT pattern in this test's\n"
        "     docstring) — otherwise the new FK ships without composite-\n"
        "     tenant protection.\n"
        "Re-run the test to confirm the snapshot is back in sync."
    )


# ---------------------------------------------------------------------------
# (4) Source-code policies — stray settings.tenant_id readers and LISTEN/NOTIFY
# ---------------------------------------------------------------------------
#
# SHU-761 replaced "read tenant_id from settings" with runtime resolution
# via ``tenant_context`` (for request / job code) or
# ``resolve_tenant_for_infra`` (for non-DB infra consumers). The settings
# field is now only meaningful in silo mode — in multi-tenant it's ``None``
# and any code that still reads it will silently log/route as null.
#
# The LISTEN/NOTIFY guard pins a separate posture: under PgBouncer
# transaction-mode pooling, a LISTEN call pins a connection for the
# lifetime of the subscription. That connection never sees the
# per-transaction ``set_config('app.tenant_id', ..., true)`` resets from
# the engine begin hook, so any read it performs lands under whatever
# tenant happened to be bound when the LISTEN started — a tenant leak.
#
# Both checks skip lines tagged with the corresponding ``# noqa: ...``
# pragma. Pragma-based (not line-number allowlist) so reformatting and
# whitespace edits don't drift the check.

# Only the "reading from settings" shapes — NOT bare ``self.tenant_id``,
# which is legitimate on ORM objects (Job, User) and inside the Settings
# class itself.
_SETTINGS_TENANT_ID_PATTERN = re.compile(
    r"\b(?:settings|get_settings_instance\(\)|get_settings\(\))\.tenant_id\b"
)

# Files where the field is *defined* (validator code) rather than read —
# matches in these files are the source of truth, not downstream reads.
_ALLOWED_SETTINGS_TENANT_ID_FILES: frozenset[str] = frozenset({"core/config.py"})

_LISTEN_NOTIFY_PATTERN = re.compile(r"\b(?:LISTEN|NOTIFY|pg_notify)\b")


def _iter_shu_python_files() -> list[Path]:
    return sorted(p for p in _SHU_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_stray_settings_tenant_id_readers() -> None:
    """Each legitimate reader must carry ``# noqa: STRAY-TENANT-ID`` with a
    one-liner WHY. Today: three silo branches in ``core/tenant.py`` and
    the two warn-helpers in queue / cache backends."""
    violations: list[tuple[str, int, str]] = []
    for file in _iter_shu_python_files():
        rel = file.relative_to(_SHU_ROOT).as_posix()
        if rel in _ALLOWED_SETTINGS_TENANT_ID_FILES:
            continue
        for lineno, line in enumerate(file.read_text().splitlines(), start=1):
            # Skip comments — references in prose / WHY-comments aren't reads.
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "# noqa: STRAY-TENANT-ID" in line:
                continue
            if not _SETTINGS_TENANT_ID_PATTERN.search(line):
                continue
            violations.append((rel, lineno, line.strip()))

    assert not violations, (
        "Stray settings.tenant_id readers found. SHU-761 moved tenant_id "
        "resolution to runtime context — use tenant_context.get() (request "
        "/ job code) or resolve_tenant_for_infra() (infra). To add a "
        "legitimate reader, append ``# noqa: STRAY-TENANT-ID — <why>`` to "
        "the line.\n  "
        + "\n  ".join(f"{f}:{ln} — {snippet}" for f, ln, snippet in violations)
    )


def test_no_listen_notify_in_source() -> None:
    """Today there are no LISTEN/NOTIFY callers; this test pins that posture
    so adding one is a conscious decision. Acceptable mitigations when a
    real need lands: route the long-running connection to a dedicated,
    RLS-safe role (e.g. ``shu_admin``) and document the constraint with
    a ``# noqa: LISTEN-NOTIFY`` marker on the line."""
    violations: list[tuple[str, int, str]] = []
    for file in _iter_shu_python_files():
        rel = file.relative_to(_SHU_ROOT).as_posix()
        for lineno, line in enumerate(file.read_text().splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "# noqa: LISTEN-NOTIFY" in line:
                continue
            if not _LISTEN_NOTIFY_PATTERN.search(line):
                continue
            violations.append((rel, lineno, line.strip()))

    assert not violations, (
        "LISTEN / NOTIFY / pg_notify reference found in shu source. "
        "Long-running pinned connections bypass the per-transaction "
        "``set_config('app.tenant_id', ..., true)`` reset under PgBouncer "
        "transaction-mode pooling, which is a tenant-leak surface. Either "
        "route the connection to ``shu_admin`` with an explicit comment, or "
        "add ``# noqa: LISTEN-NOTIFY`` to the line.\n  "
        + "\n  ".join(f"{f}:{ln} — {snippet}" for f, ln, snippet in violations)
    )
