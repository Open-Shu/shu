"""SHU-745 add password reset tokens + password_changed_at on users.

Adds the `password_reset_token` table that backs the email-based password
reset flow, plus a `password_changed_at` timestamp on users that the JWT
middleware uses to invalidate sessions issued before a password reset.

* `password_reset_token` — one row per issued reset token. Stores only the
  sha256 hash of the token; the plaintext appears only in the outbound
  email. Single-use (`used_at`), short-TTL (`expires_at`). Indexed on
  `(user_id, used_at)` for the "invalidate older outstanding tokens"
  sweep that runs on each new request.
* `users.password_changed_at` — timestamp of the most recent password
  change (reset or admin reset). The middleware compares the JWT's `iat`
  claim against this column; tokens issued before the password change
  are rejected. This is the session-invalidation primitive — JWTs are
  otherwise stateless and there is no token blacklist or refresh-token
  table to nuke.

Migration is idempotent. Re-applying after partial state must be a no-op.

Revision ID: 008_0015
Revises: 008_0014
Create Date: 2026-05-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

from migrations.helpers import (
    add_column_if_not_exists,
    drop_column_if_exists,
    drop_table_if_exists,
    index_exists,
    table_exists,
)

# revision identifiers, used by Alembic.
revision = "008_0015"
down_revision = "008_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # password_reset_token table.
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

    # Re-inspect so the newly-added table is visible to the index checks.
    inspector = sa.inspect(conn)

    # Hot-path index for the verify-and-consume lookup.
    if not index_exists(inspector, "password_reset_token", "ix_password_reset_token_token_hash"):
        op.create_index(
            "ix_password_reset_token_token_hash",
            "password_reset_token",
            ["token_hash"],
        )

    # Sweep index for "invalidate other outstanding tokens for this user"
    # on each new request and on each successful reset. The hot path is
    # `WHERE user_id = ? AND used_at IS NULL`; partial would be ideal but
    # must remain inclusive of NULL to be useful here.
    if not index_exists(inspector, "password_reset_token", "ix_password_reset_token_user_id_used_at"):
        op.create_index(
            "ix_password_reset_token_user_id_used_at",
            "password_reset_token",
            ["user_id", "used_at"],
        )

    # users.password_changed_at — null for accounts whose passwords have
    # never changed since this column was added. Middleware treats null as
    # "no invalidation gate" so existing sessions on existing accounts are
    # NOT regressed at deploy time. Once a user resets, the column is set
    # and the gate becomes active for that account.
    inspector = sa.inspect(conn)
    add_column_if_not_exists(
        inspector,
        "users",
        sa.Column("password_changed_at", TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Drop indexes first; create_index/drop_index are explicit because
    # drop_table cascades indexes only on Postgres and we want the
    # downgrade to be portable.
    op.execute("DROP INDEX IF EXISTS ix_password_reset_token_user_id_used_at")
    op.execute("DROP INDEX IF EXISTS ix_password_reset_token_token_hash")

    drop_table_if_exists(inspector, "password_reset_token")

    inspector = sa.inspect(conn)
    drop_column_if_exists(inspector, "users", "password_changed_at")
