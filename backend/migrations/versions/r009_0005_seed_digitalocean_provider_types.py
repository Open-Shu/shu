"""Seed the DigitalOcean llm_provider_type_definition rows.

Revision ID: r009_0005
Revises: r009_0004
Create Date: 2026-05-28

Re-runs the canonical upsert of llm_provider_type_definitions defaults so
existing deployments pick up the newly-appended ``digitalocean_completions``
and ``digitalocean`` entries. The upsert is idempotent
(``INSERT ... ON CONFLICT (key) DO UPDATE``) so other provider rows are
unaffected.

Policy: idempotent per docs/policies/DB_MIGRATION_POLICY.md §Policy.
"""

from __future__ import annotations

from alembic import op

from migrations.seed_data.llm_provider_types import upsert_llm_provider_type_definitions

# revision identifiers, used by Alembic.
revision = "r009_0005"
down_revision = "r009_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upsert_llm_provider_type_definitions(op)


def downgrade() -> None:
    """Intentionally a no-op.

    Dropping the provider type rows would orphan any LLMProvider rows
    users have already created against ``digitalocean_completions`` or
    ``digitalocean``. The upsert on upgrade is idempotent, so leaving the
    rows in place on downgrade is safe and avoids destroying
    tenant-configured providers.
    """
