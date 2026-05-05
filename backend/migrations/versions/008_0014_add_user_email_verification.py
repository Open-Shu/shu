"""SHU-507 add email verification columns to users.

Adds three columns supporting the verification flow that gates password
self-registration on proof of email ownership:

* ``email_verified`` — gate for login when the SHU-508 email backend is
  configured. Existing rows are backfilled to ``True`` so already-onboarded
  accounts are not regressed into a verification-required state on deploy.
* ``email_verification_token_hash`` — sha256 hex of the active verification
  token. NULL when no verification is pending. Plaintext is never stored.
* ``email_verification_expires_at`` — TTL for the active token. NULL when
  no verification is pending.

Plus a partial index on the token hash so verify-email lookups don't scan
the full ``users`` table.

Migration is idempotent. The backfill ``UPDATE users SET email_verified =
true`` runs only on the first apply (when the column is freshly added),
so re-applying the migration after some users have legitimately been
created with ``email_verified=false`` does not clobber that state.

Revision ID: 008_0014
Revises: 008_0013
Create Date: 2026-05-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

from migrations.helpers import add_column_if_not_exists, column_exists, index_exists

# revision identifiers, used by Alembic.
revision = "008_0014"
down_revision = "008_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Track whether email_verified was already present BEFORE we add it.
    # The backfill below is only correct when we are adding the column for
    # the first time — if the column exists (re-run, partial state), we
    # must not touch existing data because users may legitimately be
    # email_verified=false (a real pending verification) and a blanket
    # UPDATE would silently mark them verified.
    email_verified_already_present = column_exists(inspector, "users", "email_verified")

    # email_verified: server_default false so the ALTER ADD COLUMN is fast on
    # large user tables.
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

    # Backfill only on first apply — see comment above.
    if not email_verified_already_present:
        op.execute("UPDATE users SET email_verified = true")

    add_column_if_not_exists(
        inspector,
        "users",
        sa.Column("email_verification_token_hash", sa.String(length=64), nullable=True),
    )
    add_column_if_not_exists(
        inspector,
        "users",
        sa.Column("email_verification_expires_at", TIMESTAMP(timezone=True), nullable=True),
    )

    # Re-inspect so the newly-added column is visible to the index check.
    inspector = sa.inspect(conn)

    if not index_exists(inspector, "users", "ix_users_email_verification_token_hash"):
        # Partial index on the token hash. The verify-email endpoint's hot
        # path is `WHERE email_verification_token_hash = ?`; without an
        # index it is a full table scan. The hash is NULL in the steady
        # state (no pending verification), so a partial index is
        # dramatically smaller than indexing every row.
        op.create_index(
            "ix_users_email_verification_token_hash",
            "users",
            ["email_verification_token_hash"],
            postgresql_where=sa.text("email_verification_token_hash IS NOT NULL"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    op.execute("DROP INDEX IF EXISTS ix_users_email_verification_token_hash")

    from migrations.helpers import drop_column_if_exists

    drop_column_if_exists(inspector, "users", "email_verification_expires_at")
    drop_column_if_exists(inspector, "users", "email_verification_token_hash")
    drop_column_if_exists(inspector, "users", "email_verified")
