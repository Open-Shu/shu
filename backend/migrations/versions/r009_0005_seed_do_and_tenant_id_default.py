"""Seed DigitalOcean provider types + switch ``tenant_id`` DEFAULT to the runtime GUC.

Revision ID: r009_0005
Revises: r009_0004
Create Date: 2026-05-28

Two unrelated changes bundled because they ship together:

1. Re-runs the canonical upsert of llm_provider_type_definitions defaults
   so existing deployments pick up the newly-appended
   ``digitalocean_completions`` and ``digitalocean`` entries. The upsert
   is idempotent (``INSERT ... ON CONFLICT (key) DO UPDATE``) so other
   provider rows are unaffected.

2. Rewrites the ``tenant_id`` column DEFAULT on every tenant-scoped
   table to ``current_setting('app.tenant_id', true)::uuid``.

   Why: the ``before_flush`` listener (``shu.core.database._stamp_tenant_id``)
   only auto-stamps ``tenant_id`` on ORM-tracked inserts. Many-to-many
   relationships declared with ``secondary=...`` (e.g.
   ``ModelConfiguration.knowledge_bases``) issue Core-level INSERTs
   directly into the association table — those rows never enter
   ``session.new`` and the listener never sees them. r009_0003 explicitly
   dropped the 009-era literal DEFAULT on the assumption that "app code
   always supplies tenant_id explicitly"; that assumption holds for ORM
   inserts only, leaving the ``secondary=`` path to fail at NOT NULL or
   RLS WITH CHECK.

   Setting the DEFAULT to a ``current_setting`` expression makes any
   INSERT (ORM or Core) that omits ``tenant_id`` inherit from the same
   GUC the RLS policy already trusts — which the ``Engine.begin``
   listener stamps from ``tenant_context``. Fail-fast preserved: if no
   context is set the GUC is unset, the expression returns NULL, and
   NOT NULL catches it. No silent cross-tenant write.

Policy: idempotent per docs/policies/DB_MIGRATION_POLICY.md §Policy.
The upsert is idempotent by construction and ``ALTER COLUMN ... SET
DEFAULT`` replaces whatever default was in place.
"""

from __future__ import annotations

from alembic import op

from migrations.seed_data.llm_provider_types import upsert_llm_provider_type_definitions

# revision identifiers, used by Alembic.
revision = "r009_0005"
down_revision = "r009_0004"
branch_labels = None
depends_on = None


# Frozen inventory mirrors 009's ``_TENANT_SCOPED_TABLES`` (and r009_0003's
# copy of the same). Same drift hazard those migrations document — if
# 009's list grows, this list grows in lockstep, enforced by the
# ``test_tenant_inventory`` companion test.
_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "access_policies",
    "access_policy_bindings",
    "access_policy_statements",
    "agent_memory",
    "attachments",
    "billing_state",
    "billing_state_audit",
    "conversations",
    "document_chunks",
    "document_participants",
    "document_projects",
    "document_queries",
    "documents",
    "email_send_log",
    "experience_runs",
    "experience_steps",
    "experiences",
    "knowledge_bases",
    "llm_usage",
    "mcp_server_connections",
    "message_attachments",
    "messages",
    "model_configuration_kb_prompts",
    "model_configuration_knowledge_bases",
    "model_configurations",
    "password_reset_token",
    "plugin_executions",
    "plugin_feeds",
    "plugin_storage",
    "plugin_subscriptions",
    "prompt_assignments",
    "prompts",
    "provider_credentials",
    "provider_identities",
    "system_settings",
    "user_group_memberships",
    "user_groups",
    "user_preferences",
    "users",
)


def upgrade() -> None:
    upsert_llm_provider_type_definitions(op)

    for table in _TENANT_SCOPED_TABLES:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN tenant_id SET DEFAULT current_setting('app.tenant_id', true)::uuid"
        )


def downgrade() -> None:
    """Reverse the ``tenant_id`` DEFAULT change. Provider-type seeding stays.

    Restoring 009's literal defaults would re-introduce the bug the
    DEFAULT change fixes (deployment-singleton default racing against
    the runtime tenant context for ``secondary=`` inserts), so downgrade
    matches the state r009_0003 left behind: no default, app code on
    the hook to supply ``tenant_id`` explicitly.

    The provider-type rows are intentionally left in place — dropping
    them would orphan any LLMProvider rows users created against the
    ``digitalocean_completions`` or ``digitalocean`` keys. The upsert
    on upgrade is idempotent, so leaving the rows on downgrade is safe.
    """
    for table in _TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN tenant_id DROP DEFAULT")
