"""Migration r006_0004: Migrate google_id to ProviderIdentity

This migration moves Google identity data from the legacy User.google_id column
to the ProviderIdentity table, then drops the google_id column.

Changes:
- Creates ProviderIdentity rows for all users with google_id (idempotent)
- Drops ix_users_google_id index
- Drops google_id column from users table

Requirements: 2.3, 2.4, 2.5
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
import uuid

from helpers import (
    column_exists,
    index_exists,
)

# revision identifiers, used by Alembic.
revision = "r006_0004"
down_revision = "r006_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Migrate google_id to ProviderIdentity and drop column."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Only proceed if google_id column exists
    if not column_exists(inspector, "users", "google_id"):
        return

    # Migrate google_id values to ProviderIdentity
    users_with_google_id = conn.execute(
        text("""
            SELECT id, google_id, email, name, picture_url 
            FROM users 
            WHERE google_id IS NOT NULL
        """)
    ).fetchall()

    for user in users_with_google_id:
        # Check if ProviderIdentity already exists (idempotent)
        existing = conn.execute(
            text("""
                SELECT id FROM provider_identities 
                WHERE user_id = :user_id 
                AND provider_key = 'google' 
                AND account_id = :account_id
            """),
            {"user_id": user.id, "account_id": user.google_id}
        ).fetchone()

        if not existing:
            conn.execute(
                text("""
                    INSERT INTO provider_identities 
                    (id, user_id, provider_key, account_id, primary_email, display_name, avatar_url, created_at, updated_at)
                    VALUES (:id, :user_id, 'google', :account_id, :email, :name, :avatar_url, NOW(), NOW())
                """),
                {
                    "id": str(uuid.uuid4()),
                    "user_id": user.id,
                    "account_id": user.google_id,
                    "email": user.email,
                    "name": user.name,
                    "avatar_url": user.picture_url,
                }
            )

    # Drop index if it exists
    if index_exists(inspector, "users", "ix_users_google_id"):
        op.drop_index("ix_users_google_id", "users")

    # Drop google_id column
    op.drop_column("users", "google_id")


def downgrade() -> None:
    """Restore google_id column and migrate data back from ProviderIdentity."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Add google_id column back if it doesn't exist
    if not column_exists(inspector, "users", "google_id"):
        op.add_column(
            "users",
            sa.Column("google_id", sa.String(), nullable=True)
        )

    # Restore google_id values from ProviderIdentity
    google_identities = conn.execute(
        text("""
            SELECT user_id, account_id 
            FROM provider_identities 
            WHERE provider_key = 'google'
        """)
    ).fetchall()

    for identity in google_identities:
        conn.execute(
            text("""
                UPDATE users 
                SET google_id = :google_id 
                WHERE id = :user_id
            """),
            {"user_id": identity.user_id, "google_id": identity.account_id}
        )

    # Recreate unique index
    if not index_exists(inspector, "users", "ix_users_google_id"):
        op.create_index("ix_users_google_id", "users", ["google_id"], unique=True)

    # Remove migrated ProviderIdentity rows
    conn.execute(
        text("DELETE FROM provider_identities WHERE provider_key = 'google'")
    )
