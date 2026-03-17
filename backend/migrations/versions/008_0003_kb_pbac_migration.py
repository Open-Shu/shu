"""Migration 008_0003: Add slug column to knowledge_bases and migrate permissions to PBAC

Adds a ``slug`` column to the ``knowledge_bases`` table so PBAC resource
identifiers use human-readable, wildcard-friendly names instead of UUIDs.

Existing knowledge bases are backfilled from their ``name`` column.

Then converts active, non-expired ``knowledge_base_permissions`` rows into PBAC
access policies and drops the legacy table. For each knowledge base with
permissions, creates:
- One ``AccessPolicy`` named ``kb-migrated-{slug}`` with effect ``allow``
- One ``AccessPolicyStatement`` with actions ``["kb.read"]`` and
  resources ``["kb:{slug}"]``
- One ``AccessPolicyBinding`` per unique actor (user or group)
- An additional binding for the KB ``owner_id`` if not already covered

If no active admin user exists, no policies are created (there is nobody to
attribute the ``created_by`` audit field to).

Part of SHU-613: Policy-Based Access Control Engine.
"""

import uuid
from collections import defaultdict
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

from migrations.helpers import column_exists, slugify, table_exists


# revision identifiers, used by Alembic.
revision = "008_0003"
down_revision = "008_0002"
branch_labels = None
depends_on = None


def _find_admin_user(conn: sa.engine.Connection) -> str | None:
    """Return the ID of the first active admin user, or None."""
    users = sa.table(
        "users",
        sa.column("id", sa.String),
        sa.column("role", sa.String),
        sa.column("is_active", sa.Boolean),
    )
    row = conn.execute(
        sa.select(users.c.id)
        .where(users.c.role == "admin")
        .where(users.c.is_active.is_(True))
        .limit(1)
    ).fetchone()
    return row.id if row else None


def _collect_permission_bindings(
    conn: sa.engine.Connection,
) -> dict[str, set[tuple[str, str]]]:
    """Query active, non-expired permissions grouped by KB slug.

    Returns ``{kb_slug: {(actor_type, actor_id), ...}}``.
    """
    perms = sa.table(
        "knowledge_base_permissions",
        sa.column("knowledge_base_id", sa.String),
        sa.column("user_id", sa.String),
        sa.column("group_id", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("expires_at", sa.DateTime(timezone=True)),
    )
    kbs = sa.table(
        "knowledge_bases",
        sa.column("id", sa.String),
        sa.column("slug", sa.String),
    )

    now = datetime.now(timezone.utc)
    rows = conn.execute(
        sa.select(kbs.c.slug, perms.c.user_id, perms.c.group_id)
        .select_from(perms.join(kbs, perms.c.knowledge_base_id == kbs.c.id))
        .where(perms.c.is_active.is_(True))
        .where(
            sa.or_(
                perms.c.expires_at.is_(None),
                perms.c.expires_at > now,
            )
        )
    ).fetchall()

    bindings_by_slug: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        if row.user_id is not None:
            bindings_by_slug[row.slug].add(("user", row.user_id))
        elif row.group_id is not None:
            bindings_by_slug[row.slug].add(("group", row.group_id))

    return bindings_by_slug


def _add_owner_bindings(
    conn: sa.engine.Connection,
    bindings_by_slug: dict[str, set[tuple[str, str]]],
) -> None:
    """Add owner bindings for KBs whose owner is not already covered.

    Mutates ``bindings_by_slug`` in place, adding entries for KBs whose
    ``owner_id`` is set but not yet represented in the binding set.
    """
    kbs = sa.table(
        "knowledge_bases",
        sa.column("slug", sa.String),
        sa.column("owner_id", sa.String),
    )
    rows = conn.execute(
        sa.select(kbs.c.slug, kbs.c.owner_id).where(kbs.c.owner_id.isnot(None))
    ).fetchall()

    for row in rows:
        owner_binding = ("user", row.owner_id)
        bindings_by_slug.setdefault(row.slug, set()).add(owner_binding)


def _create_policies(
    conn: sa.engine.Connection,
    admin_id: str,
    bindings_by_slug: dict[str, set[tuple[str, str]]],
) -> None:
    """Insert PBAC policy, statement, and binding rows for each KB slug."""
    policies = sa.table(
        "access_policies",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("effect", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_by", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    statements = sa.table(
        "access_policy_statements",
        sa.column("id", sa.String),
        sa.column("policy_id", sa.String),
        sa.column("actions", sa.JSON),
        sa.column("resources", sa.JSON),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bindings = sa.table(
        "access_policy_bindings",
        sa.column("id", sa.String),
        sa.column("policy_id", sa.String),
        sa.column("actor_type", sa.String),
        sa.column("actor_id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    now = datetime.now(timezone.utc)

    for slug, actors in bindings_by_slug.items():
        policy_id = str(uuid.uuid4())

        conn.execute(
            policies.insert().values(
                id=policy_id,
                name=f"kb-migrated-{slug}",
                description=f"Migrated from legacy knowledge_base_permissions for KB '{slug}'",
                effect="allow",
                is_active=True,
                created_by=admin_id,
                created_at=now,
                updated_at=now,
            )
        )

        conn.execute(
            statements.insert().values(
                id=str(uuid.uuid4()),
                policy_id=policy_id,
                actions=["kb.read"],
                resources=[f"kb:{slug}"],
                created_at=now,
                updated_at=now,
            )
        )

        for actor_type, actor_id in actors:
            conn.execute(
                bindings.insert().values(
                    id=str(uuid.uuid4()),
                    policy_id=policy_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    created_at=now,
                    updated_at=now,
                )
            )



def upgrade() -> None:
    """Add slug column to knowledge_bases, migrate permissions to PBAC, drop legacy table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not column_exists(inspector, "knowledge_bases", "slug"):
        op.add_column("knowledge_bases", sa.Column("slug", sa.String(100), nullable=True))

        kb_table = sa.table(
            "knowledge_bases",
            sa.column("id", sa.String),
            sa.column("name", sa.String),
            sa.column("slug", sa.String),
            sa.column("created_at", sa.DateTime),
        )

        rows = conn.execute(
            sa.select(kb_table.c.id, kb_table.c.name).order_by(kb_table.c.created_at.asc())
        ).fetchall()

        seen_slugs: set[str] = set()
        for row in rows:
            slug = slugify(row.name) or "kb"
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            conn.execute(
                kb_table.update().where(kb_table.c.id == row.id).values(slug=slug)
            )

        op.alter_column("knowledge_bases", "slug", nullable=False)
        op.create_index(
            "ix_knowledge_bases_slug", "knowledge_bases", ["slug"], unique=True
        )

    if table_exists(inspector, "knowledge_base_permissions"):
        admin_id = _find_admin_user(conn)
        if admin_id is not None:
            perm_bindings = _collect_permission_bindings(conn)
            _add_owner_bindings(conn, perm_bindings)
            _create_policies(conn, admin_id, perm_bindings)

        op.drop_table("knowledge_base_permissions")


def downgrade() -> None:
    """Downgrade is irreversible — original permission data cannot be restored."""
    raise RuntimeError(
        "Irreversible migration: 008_0003 (KB PBAC). "
        "Legacy knowledge_base_permissions data was dropped during upgrade "
        "and cannot be reconstructed. Restore from a database backup instead."
    )
