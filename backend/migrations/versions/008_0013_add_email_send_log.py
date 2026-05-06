"""SHU-508 add email_send_log audit table.

Adds the audit table backing `EmailService.send` — one row per outbound
email send, written at enqueue time with `status=queued` and updated to
`sent` or `failed` by the EMAIL workload-type worker handler.

Indexes:

* ``ix_email_send_log_to_address_created_at`` — support lookups
  ("show all sends to X, newest first").
* ``uq_email_send_log_idempotency`` — unique partial index on
  ``(template_name, to_address, idempotency_key)`` where
  ``idempotency_key IS NOT NULL``. Enforces idempotency at the DB level
  so retried ``EmailService.send`` calls with the same key surface the
  existing row instead of double-enqueueing.

Migration is idempotent — re-running is a no-op (per repo convention; see
``migrations/helpers.py``).

Revision ID: 008_0013
Revises: 008_0012
Create Date: 2026-05-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

from migrations.helpers import drop_table_if_exists, index_exists, table_exists

# revision identifiers, used by Alembic.
revision = "008_0013"
down_revision = "008_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
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

    # Re-inspect so newly-created indexes from a previous partial run are
    # visible. Cheap, and lets us guard each index independently.
    inspector = sa.inspect(conn)

    if not index_exists(inspector, "email_send_log", "ix_email_send_log_to_address_created_at"):
        op.create_index(
            "ix_email_send_log_to_address_created_at",
            "email_send_log",
            ["to_address", "created_at"],
        )

    if not index_exists(inspector, "email_send_log", "uq_email_send_log_idempotency"):
        # Unique partial index — only enforces uniqueness when an idempotency
        # key was supplied. Two NULL-key rows for the same (template, to) are
        # allowed (legitimate repeat sends without a dedup key).
        op.create_index(
            "uq_email_send_log_idempotency",
            "email_send_log",
            ["template_name", "to_address", "idempotency_key"],
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # IF EXISTS form so partial-state recovery is safe.
    op.execute("DROP INDEX IF EXISTS uq_email_send_log_idempotency")
    op.execute("DROP INDEX IF EXISTS ix_email_send_log_to_address_created_at")
    drop_table_if_exists(inspector, "email_send_log")
