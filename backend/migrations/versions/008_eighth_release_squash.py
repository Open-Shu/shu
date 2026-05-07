"""Migration 008: Eighth Release Squash (008_0001..008_0016)

This migration condenses the eighth release development migrations into one.

Net schema changes:

1. Dimensionless vector columns (document_chunks.embedding,
   documents.synopsis_embedding, document_queries.query_embedding); IVFFlat
   indexes dropped (HNSW recreated at runtime by ``VectorStore.ensure_index``);
   new default embedding model on knowledge_bases
   (Snowflake/snowflake-arctic-embed-l-v2.0); add knowledge_bases.embedding_status
   and re_embedding_progress; add document_chunks.summary_embedding;
   knowledge_bases.rag_max_results → rag_max_chunks; documents.processing_status
   migrated from ``processed`` to granular ``content_processed`` /
   ``rag_processed`` / ``profile_processed``.
2. PBAC tables (access_policies, access_policy_bindings,
   access_policy_statements); experiences.slug.
3. knowledge_bases.slug; convert legacy knowledge_base_permissions rows to PBAC
   policies; drop knowledge_base_permissions table.
4. Drop document_chunks.keywords + GIN index; create ParadeDB BM25 index on
   documents (requires the ``pg_search`` extension; skipped if absent);
   document_queries.source_chunk_id FK; knowledge_bases.rag_minimum_query_words
   nullable with no default.
5. experience_steps.auth_override JSON.
6. knowledge_bases.import_progress JSON.
7. mcp_server_connections table.
8. Widen llm_usage cost columns to DECIMAL(16,9); billing_state singleton
   table (created without the legacy ``quantity`` column — Stripe is the source
   of truth); billing_state_audit append-only log.
9. Rename llm_models.cost_per_input_token → cost_per_input_unit and
   cost_per_output_token → cost_per_output_unit.
10. llm_providers.is_system_managed boolean.
11. llm_usage.provider_id FK CASCADE → SET NULL; column made nullable;
    provider_name + model_name snapshot columns + backfill.
12. users.deactivation_scheduled_at + partial index (defensively drops
    billing_state.quantity / target_quantity if a partial dev run left them).
13. email_send_log audit table + lookup and idempotency indexes.
14. users.email_verified (backfilled to true only on first apply),
    email_verification_token_hash, email_verification_expires_at + partial index.
15. password_reset_token table + indexes; users.password_changed_at.
16. knowledge_bases.is_personal boolean.

Replaces: 008_0001_dimensionless_vector_columns,
          008_0002_add_access_policy_tables,
          008_0003_kb_pbac_migration,
          008_0004_add_bm25_search_vector_drop_keywords,
          008_0005_add_experience_step_auth_override,
          008_0006_add_import_progress_to_knowledge_bases,
          008_0007_mcp_server_connections,
          008_0008_widen_llm_usage_cost_precision,
          008_0009_rename_llm_models_cost_columns_to_unit,
          008_0010_add_llm_providers_is_system_managed,
          008_0011_preserve_llm_usage_rows,
          008_0012_add_user_deactivation_scheduled_at,
          008_0013_add_email_send_log,
          008_0014_add_user_email_verification,
          008_0015_add_password_reset,
          008_0016_add_knowledge_bases_is_personal

The ParadeDB ``pg_search`` extension is a prerequisite for the BM25 index in
Part 4; the migration prints a notice and skips the index when the extension
is not installed (BM25Surface degrades gracefully at query time).
"""

import uuid
from collections import defaultdict
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

from migrations.helpers import (
    add_column_if_not_exists,
    column_exists,
    drop_column_if_exists,
    drop_table_if_exists,
    index_exists,
    slugify,
    table_exists,
)

# revision identifiers, used by Alembic.
revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None
replaces = (
    "008_0001",
    "008_0002",
    "008_0003",
    "008_0004",
    "008_0005",
    "008_0006",
    "008_0007",
    "008_0008",
    "008_0009",
    "008_0010",
    "008_0011",
    "008_0012",
    "008_0013",
    "008_0014",
    "008_0015",
    "008_0016",
)

# IVFFlat indexes that pre-date the dimensionless-vector migration.
_OLD_VECTOR_INDEXES = (
    ("document_chunks", "idx_document_chunks_embedding", "embedding"),
    ("documents", "ix_documents_synopsis_embedding", "synopsis_embedding"),
    ("document_queries", "ix_document_queries_query_embedding", "query_embedding"),
)

_VECTOR_COLUMNS = (
    ("document_chunks", "embedding"),
    ("documents", "synopsis_embedding"),
    ("document_queries", "query_embedding"),
)

_LLM_USAGE_PROVIDER_FK = "llm_usage_provider_id_fkey"


# ---------------------------------------------------------------------------
# Helpers (squash-local — not promoted to shared helpers)
# ---------------------------------------------------------------------------


def _has_extension(conn: sa.engine.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM pg_extension WHERE extname = :name"), {"name": name}
        ).scalar()
    )


def _column_numeric_scale(conn: sa.engine.Connection, table: str, column: str) -> int | None:
    row = conn.execute(
        sa.text(
            "SELECT numeric_scale FROM information_schema.columns "
            "WHERE table_name = :tbl AND column_name = :col"
        ),
        {"tbl": table, "col": column},
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _rename_column_if_needed(
    conn: sa.engine.Connection, table: str, old: str, new: str
) -> None:
    """Rename a column only when the old name is present and the new one is not."""
    has_old = bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :tbl AND column_name = :col"
            ),
            {"tbl": table, "col": old},
        ).scalar()
    )
    has_new = bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :tbl AND column_name = :col"
            ),
            {"tbl": table, "col": new},
        ).scalar()
    )
    if has_old and not has_new:
        op.alter_column(table, old, new_column_name=new)


def _find_admin_user(conn: sa.engine.Connection) -> str | None:
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
        .where(sa.or_(perms.c.expires_at.is_(None), perms.c.expires_at > now))
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
    kbs = sa.table(
        "knowledge_bases",
        sa.column("slug", sa.String),
        sa.column("owner_id", sa.String),
    )
    rows = conn.execute(
        sa.select(kbs.c.slug, kbs.c.owner_id).where(kbs.c.owner_id.isnot(None))
    ).fetchall()
    for row in rows:
        bindings_by_slug.setdefault(row.slug, set()).add(("user", row.owner_id))


def _create_policies(
    conn: sa.engine.Connection,
    admin_id: str,
    bindings_by_slug: dict[str, set[tuple[str, str]]],
) -> None:
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


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Apply all eighth release schema changes.

    All operations are idempotent — safe to re-run on a database already
    upgraded through any subset of the individual 008_* dev migrations.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ========================================================================
    # Part 1: Dimensionless vector columns + new defaults + processing_status
    # ========================================================================
    # 1a. Default embedding model on knowledge_bases.
    op.alter_column(
        "knowledge_bases",
        "embedding_model",
        server_default=sa.text("'Snowflake/snowflake-arctic-embed-l-v2.0'"),
    )

    # 1b. embedding_status + re_embedding_progress on knowledge_bases.
    add_column_if_not_exists(
        inspector,
        "knowledge_bases",
        sa.Column(
            "embedding_status",
            sa.String(20),
            server_default=sa.text("'current'"),
            nullable=False,
        ),
    )
    add_column_if_not_exists(
        inspector,
        "knowledge_bases",
        sa.Column("re_embedding_progress", sa.JSON(), nullable=True),
    )

    # 1c. Rename rag_max_results → rag_max_chunks.
    _rename_column_if_needed(conn, "knowledge_bases", "rag_max_results", "rag_max_chunks")

    # 1d. Migrate processing_status to granular terminal statuses.
    op.execute(
        sa.text(
            "UPDATE documents SET processing_status = 'profile_processed' "
            "WHERE processing_status = 'processed' AND profiling_status = 'complete'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE documents SET processing_status = 'content_processed' "
            "WHERE processing_status = 'processed'"
        )
    )

    # 1e. pgvector-dependent: dimensionless vectors, drop old indexes,
    # add summary_embedding.
    if _has_extension(conn, "vector"):
        for table, column in _VECTOR_COLUMNS:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE vector")
        for _table, index_name, _col in _OLD_VECTOR_INDEXES:
            op.execute(f"DROP INDEX IF EXISTS {index_name}")
        op.execute(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS summary_embedding vector"
        )

    # ========================================================================
    # Part 2: PBAC tables + experiences.slug
    # ========================================================================
    inspector = sa.inspect(conn)

    if not table_exists(inspector, "access_policies"):
        op.create_table(
            "access_policies",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("effect", sa.String(10), nullable=False),
            sa.CheckConstraint("effect IN ('allow', 'deny')", name="chk_policy_effect"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column(
                "created_by",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
            ),
        )
        op.create_index("ix_access_policies_name", "access_policies", ["name"])
        op.create_index("ix_access_policies_is_active", "access_policies", ["is_active"])
        op.create_index("ix_access_policies_created_by", "access_policies", ["created_by"])

    if not table_exists(inspector, "access_policy_bindings"):
        op.create_table(
            "access_policy_bindings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "policy_id",
                sa.String(36),
                sa.ForeignKey("access_policies.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("actor_type", sa.String(10), nullable=False),
            sa.Column("actor_id", sa.String(36), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "policy_id", "actor_type", "actor_id", name="uq_binding_policy_actor"
            ),
            sa.CheckConstraint(
                "actor_type IN ('user', 'group')", name="chk_binding_actor_type"
            ),
        )
        op.create_index(
            "ix_access_policy_bindings_policy_id", "access_policy_bindings", ["policy_id"]
        )
        op.create_index(
            "ix_access_policy_bindings_actor_type", "access_policy_bindings", ["actor_type"]
        )
        op.create_index(
            "ix_access_policy_bindings_actor_id", "access_policy_bindings", ["actor_id"]
        )

    if not table_exists(inspector, "access_policy_statements"):
        op.create_table(
            "access_policy_statements",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "policy_id",
                sa.String(36),
                sa.ForeignKey("access_policies.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("actions", sa.JSON(), nullable=False),
            sa.Column("resources", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_access_policy_statements_policy_id",
            "access_policy_statements",
            ["policy_id"],
        )

    inspector = sa.inspect(conn)
    if not column_exists(inspector, "experiences", "slug"):
        op.add_column("experiences", sa.Column("slug", sa.String(100), nullable=True))

        experiences_table = sa.table(
            "experiences",
            sa.column("id", sa.String),
            sa.column("name", sa.String),
            sa.column("slug", sa.String),
        )
        rows = conn.execute(
            sa.select(experiences_table.c.id, experiences_table.c.name)
        ).fetchall()
        seen_slugs: set[str] = set()
        for row in rows:
            slug = slugify(row.name)
            if not slug or slug in seen_slugs:
                conn.execute(
                    experiences_table.delete().where(experiences_table.c.id == row.id)
                )
                continue
            seen_slugs.add(slug)
            conn.execute(
                experiences_table.update()
                .where(experiences_table.c.id == row.id)
                .values(slug=slug)
            )

        op.alter_column("experiences", "slug", nullable=False)
        op.create_index("ix_experiences_slug", "experiences", ["slug"], unique=True)

    # ========================================================================
    # Part 3: knowledge_bases.slug + PBAC backfill + drop legacy table
    # ========================================================================
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
        # Diverges from the original 008_0003 dev migration, which used a bare
        # `continue` on collision and then attempted nullable=False below — that
        # would crash on a DB with two KBs whose names slugify the same. Append
        # a deterministic numeric suffix so every row gets a unique non-NULL
        # slug.
        kb_seen_slugs: set[str] = set()
        for row in rows:
            base_slug = slugify(row.name) or "kb"
            slug = base_slug
            suffix = 2
            while slug in kb_seen_slugs:
                slug = f"{base_slug}-{suffix}"
                suffix += 1
            kb_seen_slugs.add(slug)
            conn.execute(
                kb_table.update().where(kb_table.c.id == row.id).values(slug=slug)
            )

        op.alter_column("knowledge_bases", "slug", nullable=False)
        op.create_index(
            "ix_knowledge_bases_slug", "knowledge_bases", ["slug"], unique=True
        )

    inspector = sa.inspect(conn)
    if table_exists(inspector, "knowledge_base_permissions"):
        admin_id = _find_admin_user(conn)
        if admin_id is not None:
            perm_bindings = _collect_permission_bindings(conn)
            _add_owner_bindings(conn, perm_bindings)
            _create_policies(conn, admin_id, perm_bindings)
        op.drop_table("knowledge_base_permissions")

    # ========================================================================
    # Part 4: BM25 + drop keywords + source_chunk_id + nullable
    #         rag_minimum_query_words
    # ========================================================================
    inspector = sa.inspect(conn)

    if index_exists(inspector, "document_chunks", "ix_document_chunks_keywords"):
        op.drop_index("ix_document_chunks_keywords", table_name="document_chunks")
    drop_column_if_exists(inspector, "document_chunks", "keywords")

    if _has_extension(conn, "pg_search"):
        inspector = sa.inspect(conn)
        if not index_exists(inspector, "documents", "ix_documents_bm25"):
            op.execute(
                sa.text(
                    """
                    CREATE INDEX ix_documents_bm25 ON documents
                    USING bm25 (
                        id,
                        (title::pdb.simple('stemmer=english', 'stopwords_language=english')),
                        (content::pdb.simple('stemmer=english', 'stopwords_language=english'))
                    )
                    WITH (key_field='id')
                    """
                )
            )
    else:
        print(
            "NOTE: pg_search extension not available — skipping BM25 index creation. "
            "BM25 retrieval surface will be inactive."
        )

    inspector = sa.inspect(conn)
    add_column_if_not_exists(
        inspector,
        "document_queries",
        sa.Column(
            "source_chunk_id",
            sa.String,
            sa.ForeignKey("document_chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    inspector = sa.inspect(conn)
    if not index_exists(
        inspector, "document_queries", "ix_document_queries_source_chunk_id"
    ):
        op.create_index(
            "ix_document_queries_source_chunk_id",
            "document_queries",
            ["source_chunk_id"],
        )

    # rag_minimum_query_words → nullable, no default; existing rows set to NULL
    # so the ConfigurationManager cascade reaches the global setting. Idempotent
    # because the second apply finds no NOT NULL rows to clear.
    op.alter_column(
        "knowledge_bases",
        "rag_minimum_query_words",
        existing_type=sa.Integer(),
        nullable=True,
        server_default=None,
    )
    op.execute(sa.text("UPDATE knowledge_bases SET rag_minimum_query_words = NULL"))

    # ========================================================================
    # Part 5: experience_steps.auth_override
    # ========================================================================
    inspector = sa.inspect(conn)
    add_column_if_not_exists(
        inspector,
        "experience_steps",
        sa.Column("auth_override", sa.JSON, nullable=True),
    )

    # ========================================================================
    # Part 6: knowledge_bases.import_progress
    # ========================================================================
    inspector = sa.inspect(conn)
    add_column_if_not_exists(
        inspector,
        "knowledge_bases",
        sa.Column("import_progress", sa.JSON(), nullable=True),
    )

    # ========================================================================
    # Part 7: mcp_server_connections
    # ========================================================================
    inspector = sa.inspect(conn)
    if not table_exists(inspector, "mcp_server_connections"):
        op.create_table(
            "mcp_server_connections",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "created_at",
                TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
            ),
            sa.Column("name", sa.String(96), nullable=False),
            sa.Column("url", sa.String(500), nullable=False),
            sa.Column("tool_configs", sa.JSON(), nullable=True),
            sa.Column("discovered_tools", sa.JSON(), nullable=True),
            sa.Column("timeouts", sa.JSON(), nullable=True),
            sa.Column("response_size_limit_bytes", sa.Integer(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("last_synced_at", TIMESTAMP(timezone=True), nullable=True),
            sa.Column("last_connected_at", TIMESTAMP(timezone=True), nullable=True),
            sa.Column("last_error", sa.String(500), nullable=True),
            sa.Column(
                "consecutive_failures",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("server_info", sa.JSON(), nullable=True),
            sa.UniqueConstraint("name", name="uq_mcp_server_connections_name"),
        )
        op.create_index(
            "ix_mcp_server_connections_enabled", "mcp_server_connections", ["enabled"]
        )

    # ========================================================================
    # Part 8: widen llm_usage costs + billing_state + billing_state_audit
    # ========================================================================
    for column_name in ("input_cost", "output_cost", "total_cost"):
        current_scale = _column_numeric_scale(conn, "llm_usage", column_name)
        if current_scale is not None and current_scale != 9:
            op.alter_column(
                "llm_usage",
                column_name,
                type_=sa.DECIMAL(16, 9),
                existing_type=sa.DECIMAL(10, 6),
                existing_nullable=False,
                existing_server_default=sa.text("0"),
            )

    inspector = sa.inspect(conn)
    if not table_exists(inspector, "billing_state"):
        # Net schema: no ``quantity`` column. Stripe is the source of truth for
        # seat counts (see Part 12 / SHU-730).
        op.create_table(
            "billing_state",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("stripe_customer_id", sa.Text(), nullable=True),
            sa.Column("stripe_subscription_id", sa.Text(), nullable=True),
            sa.Column("billing_email", sa.Text(), nullable=True),
            sa.Column(
                "subscription_status",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            sa.Column("current_period_start", TIMESTAMP(timezone=True), nullable=True),
            sa.Column("current_period_end", TIMESTAMP(timezone=True), nullable=True),
            sa.Column(
                "cancel_at_period_end",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "last_reported_total",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "last_reported_period_start", TIMESTAMP(timezone=True), nullable=True
            ),
            sa.Column("payment_failed_at", TIMESTAMP(timezone=True), nullable=True),
            sa.Column(
                "user_limit_enforcement",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'soft'"),
            ),
            sa.Column(
                "version", sa.Integer(), nullable=False, server_default=sa.text("0")
            ),
            sa.Column(
                "updated_at",
                TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
            sa.CheckConstraint("id = 1", name="billing_state_singleton"),
            sa.CheckConstraint(
                "user_limit_enforcement IN ('soft', 'hard', 'none')",
                name="billing_state_enforcement_check",
            ),
        )

    inspector = sa.inspect(conn)
    if not table_exists(inspector, "billing_state_audit"):
        op.create_table(
            "billing_state_audit",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column(
                "changed_at",
                TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
            sa.Column("changed_by", sa.Text(), nullable=True),
            sa.Column("field_name", sa.Text(), nullable=False),
            sa.Column("old_value", JSONB(), nullable=True),
            sa.Column("new_value", JSONB(), nullable=True),
            sa.Column("stripe_event_id", sa.Text(), nullable=True),
        )

    inspector = sa.inspect(conn)
    if not index_exists(
        inspector, "billing_state_audit", "idx_billing_state_audit_changed_at"
    ):
        op.create_index(
            "idx_billing_state_audit_changed_at",
            "billing_state_audit",
            ["changed_at"],
        )
    if not index_exists(
        inspector, "billing_state_audit", "idx_billing_state_audit_stripe_event_id"
    ):
        op.create_index(
            "idx_billing_state_audit_stripe_event_id",
            "billing_state_audit",
            ["stripe_event_id"],
        )

    # ========================================================================
    # Part 9: rename llm_models cost columns
    # ========================================================================
    _rename_column_if_needed(
        conn, "llm_models", "cost_per_input_token", "cost_per_input_unit"
    )
    _rename_column_if_needed(
        conn, "llm_models", "cost_per_output_token", "cost_per_output_unit"
    )

    # ========================================================================
    # Part 10: llm_providers.is_system_managed
    # ========================================================================
    inspector = sa.inspect(conn)
    add_column_if_not_exists(
        inspector,
        "llm_providers",
        sa.Column(
            "is_system_managed",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # ========================================================================
    # Part 11: llm_usage provider/model snapshots + FK SET NULL
    # ========================================================================
    inspector = sa.inspect(conn)
    add_column_if_not_exists(
        inspector,
        "llm_usage",
        sa.Column("provider_name", sa.String(length=255), nullable=True),
    )
    inspector = sa.inspect(conn)
    add_column_if_not_exists(
        inspector,
        "llm_usage",
        sa.Column("model_name", sa.String(length=255), nullable=True),
    )

    # Backfill is idempotent: only fills NULLs.
    op.execute(
        """
        UPDATE llm_usage u
           SET provider_name = p.name
          FROM llm_providers p
         WHERE u.provider_id = p.id
           AND u.provider_name IS NULL
        """
    )
    op.execute(
        """
        UPDATE llm_usage u
           SET model_name = m.model_name
          FROM llm_models m
         WHERE u.model_id = m.id
           AND u.model_name IS NULL
        """
    )

    inspector = sa.inspect(conn)
    existing_fk = next(
        (
            fk
            for fk in inspector.get_foreign_keys("llm_usage")
            if fk.get("referred_table") == "llm_providers"
            and "provider_id" in (fk.get("constrained_columns") or [])
        ),
        None,
    )
    current_ondelete = (
        (existing_fk.get("options", {}).get("ondelete") or "").upper()
        if existing_fk
        else None
    )
    if current_ondelete != "SET NULL":
        existing_fk_name = existing_fk.get("name") if existing_fk is not None else None
        if existing_fk_name:
            op.drop_constraint(existing_fk_name, "llm_usage", type_="foreignkey")
        if column_exists(inspector, "llm_usage", "provider_id"):
            op.alter_column(
                "llm_usage", "provider_id", existing_type=sa.String(), nullable=True
            )
        op.create_foreign_key(
            _LLM_USAGE_PROVIDER_FK,
            "llm_usage",
            "llm_providers",
            ["provider_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # ========================================================================
    # Part 12: users.deactivation_scheduled_at + drop legacy seat columns
    # ========================================================================
    inspector = sa.inspect(conn)
    add_column_if_not_exists(
        inspector,
        "users",
        sa.Column("deactivation_scheduled_at", TIMESTAMP(timezone=True), nullable=True),
    )
    inspector = sa.inspect(conn)
    if not index_exists(inspector, "users", "ix_users_deactivation_scheduled"):
        op.create_index(
            "ix_users_deactivation_scheduled",
            "users",
            ["deactivation_scheduled_at"],
            postgresql_where=sa.text("deactivation_scheduled_at IS NOT NULL"),
        )

    # Defensive: billing_state was created in Part 8 without these columns, but
    # a partial dev-migration sequence (008_0008 applied, 008_0012 not yet) may
    # have left them in place. Drop if present.
    inspector = sa.inspect(conn)
    drop_column_if_exists(inspector, "billing_state", "quantity")
    drop_column_if_exists(inspector, "billing_state", "target_quantity")

    # ========================================================================
    # Part 13: email_send_log
    # ========================================================================
    inspector = sa.inspect(conn)
    if not table_exists(inspector, "email_send_log"):
        op.create_table(
            "email_send_log",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("to_address", sa.Text(), nullable=False),
            sa.Column("template_name", sa.Text(), nullable=False),
            sa.Column("backend_name", sa.Text(), nullable=False),
            sa.Column("provider_message_id", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("idempotency_key", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("sent_at", TIMESTAMP(timezone=True), nullable=True),
            sa.CheckConstraint(
                "status IN ('queued', 'sent', 'failed')",
                name="email_send_log_status_check",
            ),
        )

    inspector = sa.inspect(conn)
    if not index_exists(
        inspector, "email_send_log", "ix_email_send_log_to_address_created_at"
    ):
        op.create_index(
            "ix_email_send_log_to_address_created_at",
            "email_send_log",
            ["to_address", "created_at"],
        )
    if not index_exists(inspector, "email_send_log", "uq_email_send_log_idempotency"):
        op.create_index(
            "uq_email_send_log_idempotency",
            "email_send_log",
            ["template_name", "to_address", "idempotency_key"],
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        )

    # ========================================================================
    # Part 14: email verification columns + partial index
    # ========================================================================
    inspector = sa.inspect(conn)
    # The email_verified backfill must run only on the first apply. If the
    # column already exists from a prior dev-migration run, real users may
    # have been written with email_verified=false (a legitimate pending
    # verification) and a blanket UPDATE would silently mark them verified.
    email_verified_already_present = column_exists(inspector, "users", "email_verified")
    add_column_if_not_exists(
        inspector,
        "users",
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    if not email_verified_already_present:
        op.execute("UPDATE users SET email_verified = true")

    inspector = sa.inspect(conn)
    add_column_if_not_exists(
        inspector,
        "users",
        sa.Column("email_verification_token_hash", sa.String(length=64), nullable=True),
    )
    inspector = sa.inspect(conn)
    add_column_if_not_exists(
        inspector,
        "users",
        sa.Column(
            "email_verification_expires_at", TIMESTAMP(timezone=True), nullable=True
        ),
    )

    inspector = sa.inspect(conn)
    if not index_exists(inspector, "users", "ix_users_email_verification_token_hash"):
        op.create_index(
            "ix_users_email_verification_token_hash",
            "users",
            ["email_verification_token_hash"],
            postgresql_where=sa.text("email_verification_token_hash IS NOT NULL"),
        )

    # ========================================================================
    # Part 15: password_reset_token + users.password_changed_at
    # ========================================================================
    inspector = sa.inspect(conn)
    if not table_exists(inspector, "password_reset_token"):
        op.create_table(
            "password_reset_token",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("expires_at", TIMESTAMP(timezone=True), nullable=False),
            sa.Column("used_at", TIMESTAMP(timezone=True), nullable=True),
            sa.Column("created_ip", sa.String(length=64), nullable=True),
            sa.Column(
                "created_at",
                TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )

    inspector = sa.inspect(conn)
    if not index_exists(
        inspector, "password_reset_token", "ix_password_reset_token_token_hash"
    ):
        op.create_index(
            "ix_password_reset_token_token_hash",
            "password_reset_token",
            ["token_hash"],
        )
    if not index_exists(
        inspector, "password_reset_token", "ix_password_reset_token_user_id_used_at"
    ):
        op.create_index(
            "ix_password_reset_token_user_id_used_at",
            "password_reset_token",
            ["user_id", "used_at"],
        )

    inspector = sa.inspect(conn)
    add_column_if_not_exists(
        inspector,
        "users",
        sa.Column("password_changed_at", TIMESTAMP(timezone=True), nullable=True),
    )

    # ========================================================================
    # Part 16: knowledge_bases.is_personal
    # ========================================================================
    inspector = sa.inspect(conn)
    add_column_if_not_exists(
        inspector,
        "knowledge_bases",
        sa.Column(
            "is_personal",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Revert all eighth release schema changes.

    Reversal order is the inverse of upgrade(). All operations are idempotent.
    Note: the PBAC backfill in Part 3 destroyed the legacy
    knowledge_base_permissions data; the downgrade re-creates the empty table
    structure but cannot reconstruct the original rows. Restore from backup
    when reverting on a real deployment.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Part 16 (reverse): is_personal
    drop_column_if_exists(inspector, "knowledge_bases", "is_personal")

    # Part 15 (reverse): password_reset_token + password_changed_at
    op.execute("DROP INDEX IF EXISTS ix_password_reset_token_user_id_used_at")
    op.execute("DROP INDEX IF EXISTS ix_password_reset_token_token_hash")
    inspector = sa.inspect(conn)
    drop_table_if_exists(inspector, "password_reset_token")
    inspector = sa.inspect(conn)
    drop_column_if_exists(inspector, "users", "password_changed_at")

    # Part 14 (reverse): email verification
    op.execute("DROP INDEX IF EXISTS ix_users_email_verification_token_hash")
    inspector = sa.inspect(conn)
    drop_column_if_exists(inspector, "users", "email_verification_expires_at")
    drop_column_if_exists(inspector, "users", "email_verification_token_hash")
    drop_column_if_exists(inspector, "users", "email_verified")

    # Part 13 (reverse): email_send_log
    op.execute("DROP INDEX IF EXISTS uq_email_send_log_idempotency")
    op.execute("DROP INDEX IF EXISTS ix_email_send_log_to_address_created_at")
    inspector = sa.inspect(conn)
    drop_table_if_exists(inspector, "email_send_log")

    # Part 12 (reverse): deactivation_scheduled_at
    op.execute("DROP INDEX IF EXISTS ix_users_deactivation_scheduled")
    inspector = sa.inspect(conn)
    drop_column_if_exists(inspector, "users", "deactivation_scheduled_at")

    # Part 11 (reverse): llm_usage FK back to CASCADE; drop snapshot columns.
    inspector = sa.inspect(conn)
    existing_fk = next(
        (
            fk
            for fk in inspector.get_foreign_keys("llm_usage")
            if fk.get("referred_table") == "llm_providers"
            and "provider_id" in (fk.get("constrained_columns") or [])
        ),
        None,
    )
    current_ondelete = (
        (existing_fk.get("options", {}).get("ondelete") or "").upper()
        if existing_fk
        else None
    )
    if current_ondelete != "CASCADE":
        existing_fk_name = existing_fk.get("name") if existing_fk is not None else None
        if existing_fk_name:
            op.drop_constraint(existing_fk_name, "llm_usage", type_="foreignkey")
        if column_exists(inspector, "llm_usage", "provider_id"):
            op.alter_column(
                "llm_usage", "provider_id", existing_type=sa.String(), nullable=False
            )
        op.create_foreign_key(
            _LLM_USAGE_PROVIDER_FK,
            "llm_usage",
            "llm_providers",
            ["provider_id"],
            ["id"],
            ondelete="CASCADE",
        )
    drop_column_if_exists(inspector, "llm_usage", "model_name")
    drop_column_if_exists(inspector, "llm_usage", "provider_name")

    # Part 10 (reverse): is_system_managed
    inspector = sa.inspect(conn)
    drop_column_if_exists(inspector, "llm_providers", "is_system_managed")

    # Part 9 (reverse): rename cost columns back
    _rename_column_if_needed(
        conn, "llm_models", "cost_per_input_unit", "cost_per_input_token"
    )
    _rename_column_if_needed(
        conn, "llm_models", "cost_per_output_unit", "cost_per_output_token"
    )

    # Part 8 (reverse): drop billing_state_audit + billing_state; narrow costs.
    op.execute("DROP INDEX IF EXISTS idx_billing_state_audit_stripe_event_id")
    op.execute("DROP INDEX IF EXISTS idx_billing_state_audit_changed_at")
    inspector = sa.inspect(conn)
    drop_table_if_exists(inspector, "billing_state_audit")
    inspector = sa.inspect(conn)
    drop_table_if_exists(inspector, "billing_state")

    for column_name in ("input_cost", "output_cost", "total_cost"):
        current_scale = _column_numeric_scale(conn, "llm_usage", column_name)
        if current_scale is not None and current_scale != 6:
            op.alter_column(
                "llm_usage",
                column_name,
                type_=sa.DECIMAL(10, 6),
                existing_type=sa.DECIMAL(16, 9),
                existing_nullable=False,
                existing_server_default=sa.text("0"),
            )

    # Part 7 (reverse): mcp_server_connections
    inspector = sa.inspect(conn)
    if index_exists(inspector, "mcp_server_connections", "ix_mcp_server_connections_enabled"):
        op.drop_index(
            "ix_mcp_server_connections_enabled", table_name="mcp_server_connections"
        )
    inspector = sa.inspect(conn)
    drop_table_if_exists(inspector, "mcp_server_connections")

    # Part 6 (reverse): import_progress
    inspector = sa.inspect(conn)
    drop_column_if_exists(inspector, "knowledge_bases", "import_progress")

    # Part 5 (reverse): auth_override
    inspector = sa.inspect(conn)
    drop_column_if_exists(inspector, "experience_steps", "auth_override")

    # Part 4 (reverse): document_queries.source_chunk_id; restore
    # rag_minimum_query_words; restore document_chunks.keywords; drop BM25.
    inspector = sa.inspect(conn)
    if index_exists(
        inspector, "document_queries", "ix_document_queries_source_chunk_id"
    ):
        op.drop_index(
            "ix_document_queries_source_chunk_id", table_name="document_queries"
        )
    inspector = sa.inspect(conn)
    drop_column_if_exists(inspector, "document_queries", "source_chunk_id")

    op.execute(
        sa.text(
            "UPDATE knowledge_bases SET rag_minimum_query_words = 3 "
            "WHERE rag_minimum_query_words IS NULL"
        )
    )
    op.alter_column(
        "knowledge_bases",
        "rag_minimum_query_words",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("3"),
    )

    op.execute("DROP INDEX IF EXISTS ix_documents_bm25")

    inspector = sa.inspect(conn)
    add_column_if_not_exists(
        inspector,
        "document_chunks",
        sa.Column("keywords", JSONB, nullable=True),
    )
    inspector = sa.inspect(conn)
    if not index_exists(inspector, "document_chunks", "ix_document_chunks_keywords"):
        op.create_index(
            "ix_document_chunks_keywords",
            "document_chunks",
            ["keywords"],
            postgresql_using="gin",
        )

    # Part 3 (reverse): drop knowledge_bases.slug + recreate empty
    # knowledge_base_permissions table for downgrade compatibility. Original
    # permission rows cannot be reconstructed.
    inspector = sa.inspect(conn)
    if index_exists(inspector, "knowledge_bases", "ix_knowledge_bases_slug"):
        op.drop_index("ix_knowledge_bases_slug", table_name="knowledge_bases")
    inspector = sa.inspect(conn)
    drop_column_if_exists(inspector, "knowledge_bases", "slug")

    inspector = sa.inspect(conn)
    if not table_exists(inspector, "knowledge_base_permissions"):
        op.create_table(
            "knowledge_base_permissions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "knowledge_base_id",
                sa.String(36),
                sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("user_id", sa.String(36), nullable=True),
            sa.Column("group_id", sa.String(36), nullable=True),
            sa.Column("permission", sa.String(20), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
            ),
        )

    # Part 2 (reverse): experiences.slug + PBAC tables
    inspector = sa.inspect(conn)
    if index_exists(inspector, "experiences", "ix_experiences_slug"):
        op.drop_index("ix_experiences_slug", table_name="experiences")
    inspector = sa.inspect(conn)
    drop_column_if_exists(inspector, "experiences", "slug")

    inspector = sa.inspect(conn)
    drop_table_if_exists(inspector, "access_policy_statements")
    inspector = sa.inspect(conn)
    drop_table_if_exists(inspector, "access_policy_bindings")
    inspector = sa.inspect(conn)
    drop_table_if_exists(inspector, "access_policies")

    # Part 1 (reverse): processing_status, rag_max_chunks rename, embedding
    # status columns, default model, dimensionless vectors.
    op.execute(
        sa.text(
            "UPDATE documents SET processing_status = 'processed' "
            "WHERE processing_status IN ('content_processed', 'rag_processed', "
            "'profile_processed', 'artifact_embedding')"
        )
    )

    _rename_column_if_needed(conn, "knowledge_bases", "rag_max_chunks", "rag_max_results")

    op.execute("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS re_embedding_progress")
    op.execute("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS embedding_status")

    op.alter_column(
        "knowledge_bases",
        "embedding_model",
        server_default=sa.text("'sentence-transformers/all-MiniLM-L6-v2'"),
    )

    if _has_extension(conn, "vector"):
        op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS summary_embedding")
        for table, column in _VECTOR_COLUMNS:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE vector(384)")
        inspector = sa.inspect(conn)
        for table, index_name, column in _OLD_VECTOR_INDEXES:
            if not index_exists(inspector, table, index_name):
                op.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS {index_name}
                    ON {table} USING ivfflat ({column} vector_cosine_ops)
                    WITH (lists = 100)
                    """
                )
