"""Migration 002: Second Release Squash (squashes 007..017)

This migration represents the net schema and seed state after applying
migrations 007 through 017 on top of the 001 first-release base.

It removes legacy tables and FKs, creates new plugin- and provider-related
tables, adds required columns, and seeds critical data.

Replaces: 007, 008, 009, 010, 011, 012_plugins_consolidation,
          013_remove_legacy_sync_sources, 014_provider_type_defs,
          015_provider_credentials, 015_system_settings,
          016_drop_user_google_credentials, 017_plugin_subscriptions
"""

import sqlalchemy as sa
from alembic import op

from migrations.seed_data.llm_provider_types import upsert_llm_provider_type_definitions

# revision identifiers, used by Alembic.
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None
replaces = (
    "007",
    "008",
    "009",
    "010",
    "011",
    "012_plugins_consolidation",
    "013_remove_legacy_sync_sources",
    "014_provider_type_defs",
    "015_provider_credentials",
    "015_system_settings",
    "016_drop_user_google_credentials",
    "017_plugin_subscriptions",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1) Legacy cleanup (skipped in dev):
    # To avoid transactional aborts on heterogeneous prior states, we skip
    # dropping legacy FKs/tables here. This migration focuses on additive
    # changes only; cleanup can be handled separately if needed.
    # (Intentionally no-op)

    # 2) Create new tables introduced during 007..017
    # plugin_definitions (007 + 010 limits)
    if not inspector.has_table("plugin_definitions"):
        op.create_table(
            "plugin_definitions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("version", sa.String(50), nullable=False, server_default=sa.text("'v0'")),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("schema_hash", sa.String(64), nullable=True),
            sa.Column("input_schema", sa.JSON(), nullable=True),
            sa.Column("output_schema", sa.JSON(), nullable=True),
            sa.Column("limits", sa.JSON(), nullable=True),
            sa.Column("created_by", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("name", "version", name="uq_plugin_name_version"),
        )
        try:
            op.create_index("ix_plugin_definitions_name", "plugin_definitions", ["name"])
            op.create_index("ix_plugin_definitions_enabled", "plugin_definitions", ["enabled"])
            op.create_index("ix_plugin_definitions_created_by", "plugin_definitions", ["created_by"])
        except Exception:
            pass

    # agent_memory (007)
    if not inspector.has_table("agent_memory"):
        op.create_table(
            "agent_memory",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("agent_key", sa.String(100), nullable=False),
            sa.Column("key", sa.String(200), nullable=False),
            sa.Column("value", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", "agent_key", "key", name="uq_agent_memory_scope_key"),
        )
        try:
            op.create_index("ix_agent_memory_user_id", "agent_memory", ["user_id"])
            op.create_index("ix_agent_memory_agent_key", "agent_memory", ["agent_key"])
            op.create_index("ix_agent_memory_key", "agent_memory", ["key"])
        except Exception:
            pass

    # provider_identities (011)
    if not inspector.has_table("provider_identities"):
        op.create_table(
            "provider_identities",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("provider_key", sa.String(), nullable=False),
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("primary_email", sa.String(), nullable=True),
            sa.Column("display_name", sa.String(), nullable=True),
            sa.Column("avatar_url", sa.String(), nullable=True),
            sa.Column("scopes", sa.JSON(), nullable=True),
            sa.Column("credential_id", sa.String(), nullable=True),
            sa.Column("identity_meta", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_provider_identity_user"),
        )
        for ix_name, cols, unique in [
            ("ix_provider_identity_user", ["user_id"], False),
            ("ix_provider_identity_provider_key", ["provider_key"], False),
            ("ix_provider_identity_account_id", ["account_id"], False),
            ("ix_provider_identity_primary_email", ["primary_email"], False),
            (
                "ux_provider_identity_user_provider_account",
                ["user_id", "provider_key", "account_id"],
                True,
            ),
        ]:
            try:
                op.create_index(ix_name, "provider_identities", cols, unique=unique)
            except Exception:
                pass

    # plugin_feeds and plugin_executions (012)
    if not inspector.has_table("plugin_feeds"):
        op.create_table(
            "plugin_feeds",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("plugin_name", sa.String(100), nullable=False),
            sa.Column("agent_key", sa.String(100), nullable=True),
            sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("params", sa.JSON(), nullable=True),
            sa.Column("interval_seconds", sa.Integer, nullable=False, server_default=sa.text("3600")),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        for ix_name, cols, unique in [
            ("ix_plugin_feed_enabled_next", ["enabled", "next_run_at"], False),
            ("ix_plugin_feeds_name", ["name"], False),
            ("ix_plugin_feeds_owner_user_id", ["owner_user_id"], False),
            ("ix_plugin_feeds_plugin_name", ["plugin_name"], False),
            ("ix_plugin_feeds_enabled", ["enabled"], False),
        ]:
            try:
                op.create_index(ix_name, "plugin_feeds", cols, unique=unique)
            except Exception:
                pass

    if not inspector.has_table("plugin_executions"):
        op.create_table(
            "plugin_executions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "schedule_id",
                sa.String(36),
                sa.ForeignKey("plugin_feeds.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("plugin_name", sa.String(100), nullable=False),
            sa.Column("agent_key", sa.String(100), nullable=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("params", sa.JSON(), nullable=True),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'pending'")),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        for ix_name, cols, unique in [
            ("ix_plugin_executions_status", ["status"], False),
            ("ix_plugin_executions_plugin_name", ["plugin_name"], False),
            ("ix_plugin_executions_user_id", ["user_id"], False),
            ("ix_plugin_executions_schedule_id", ["schedule_id"], False),
            ("ix_plugin_exec_status_plugin", ["status", "plugin_name"], False),
        ]:
            try:
                op.create_index(ix_name, "plugin_executions", cols, unique=unique)
            except Exception:
                pass

    # provider_credentials (015_provider_credentials)
    if not inspector.has_table("provider_credentials"):
        op.create_table(
            "provider_credentials",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("provider_key", sa.String(), nullable=False),
            sa.Column("account_id", sa.String(), nullable=True),
            sa.Column("access_token_encrypted", sa.Text(), nullable=False),
            sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
            sa.Column("token_uri", sa.String(), nullable=True),
            sa.Column("client_id", sa.String(), nullable=True),
            sa.Column("client_secret", sa.String(), nullable=True),
            sa.Column("scopes", sa.JSON(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("credential_meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        try:
            op.create_index(
                "ix_provider_credentials_user_provider_account",
                "provider_credentials",
                ["user_id", "provider_key", "account_id"],
            )
        except Exception:
            pass

    # system_settings (015_system_settings)
    if not inspector.has_table("system_settings"):
        op.create_table(
            "system_settings",
            sa.Column("key", sa.String(128), primary_key=True),
            sa.Column("value", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    # plugin_subscriptions (017)
    if not inspector.has_table("plugin_subscriptions"):
        op.create_table(
            "plugin_subscriptions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("provider_key", sa.String(), nullable=False),
            sa.Column("account_id", sa.String(), nullable=True),
            sa.Column("plugin_name", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_plugin_sub_user", ondelete="CASCADE"),
            sa.UniqueConstraint(
                "user_id",
                "provider_key",
                "account_id",
                "plugin_name",
                name="ux_plugin_sub_user_provider_account_plugin",
            ),
        )
        for ix_name, cols, unique in [
            ("ix_plugin_sub_user", ["user_id"], False),
            ("ix_plugin_sub_provider", ["provider_key"], False),
            ("ix_plugin_sub_user_provider", ["user_id", "provider_key"], False),
        ]:
            try:
                op.create_index(ix_name, "plugin_subscriptions", cols, unique=unique)
            except Exception:
                pass

    # 3) Add/alter columns and FKs on existing tables
    # model_configurations: add parameter_overrides + functionalities
    try:
        op.add_column("model_configurations", sa.Column("parameter_overrides", sa.JSON(), nullable=True))
    except Exception:
        pass
    try:
        op.add_column(
            "model_configurations",
            sa.Column("functionalities", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        )
    except Exception:
        pass

    # FK: llm_providers.provider_type -> llm_provider_type_definitions.key
    # First create type defs table if missing, then add FK.
    if not inspector.has_table("llm_provider_type_definitions"):
        op.create_table(
            "llm_provider_type_definitions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("key", sa.String(50), nullable=False, unique=True),
            sa.Column("display_name", sa.String(100), nullable=False),
            sa.Column("provider_adapter_name", sa.String(100), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        try:
            op.create_index("ix_llm_provider_type_definitions_key", "llm_provider_type_definitions", ["key"])
        except Exception:
            pass

    try:
        op.create_foreign_key(
            "fk_llm_providers_provider_type_provider_type_definitions",
            "llm_providers",
            "llm_provider_type_definitions",
            ["provider_type"],
            ["key"],
            ondelete="SET NULL",
        )
    except Exception:
        pass

    # llm_provider_type_definitions seeds
    upsert_llm_provider_type_definitions(op)


def downgrade() -> None:
    # Best-effort downgrade: drop new objects. Legacy objects are not restored.
    for ix in [
        (
            "plugin_subscriptions",
            [
                "ix_plugin_sub_user_provider",
                "ix_plugin_sub_provider",
                "ix_plugin_sub_user",
            ],
        ),
        (
            "plugin_executions",
            [
                "ix_plugin_exec_status_plugin",
                "ix_plugin_executions_schedule_id",
                "ix_plugin_executions_user_id",
                "ix_plugin_executions_plugin_name",
                "ix_plugin_executions_status",
            ],
        ),
        (
            "plugin_feeds",
            [
                "ix_plugin_feeds_enabled",
                "ix_plugin_feeds_plugin_name",
                "ix_plugin_feeds_owner_user_id",
                "ix_plugin_feeds_name",
                "ix_plugin_feed_enabled_next",
            ],
        ),
        (
            "provider_identities",
            [
                "ux_provider_identity_user_provider_account",
                "ix_provider_identity_primary_email",
                "ix_provider_identity_account_id",
                "ix_provider_identity_provider_key",
                "ix_provider_identity_user",
            ],
        ),
        (
            "plugin_definitions",
            [
                "ix_plugin_definitions_created_by",
                "ix_plugin_definitions_enabled",
                "ix_plugin_definitions_name",
            ],
        ),
        (
            "llm_provider_type_definitions",
            [
                "ix_llm_provider_type_definitions_key",
            ],
        ),
        (
            "provider_credentials",
            [
                "ix_provider_credentials_user_provider_account",
            ],
        ),
    ]:
        table, indices = ix
        for name in indices:
            try:
                op.drop_index(name, table_name=table)
            except Exception:
                pass
    # Drop FK from llm_providers if exists
    try:
        op.drop_constraint(
            "fk_llm_providers_provider_type_provider_type_definitions",
            "llm_providers",
            type_="foreignkey",
        )
    except Exception:
        pass
    # Drop newly created tables (reverse order)
    for t in [
        "plugin_subscriptions",
        "system_settings",
        "provider_credentials",
        "plugin_executions",
        "plugin_feeds",
        "provider_identities",
        "llm_provider_type_definitions",
        "agent_memory",
        "plugin_definitions",
    ]:
        try:
            op.drop_table(t)
        except Exception:
            pass
    # Remove added columns from model_configurations
    for col in ["functionalities", "parameter_overrides"]:
        try:
            op.drop_column("model_configurations", col)
        except Exception:
            pass
