"""Migration r006_0004: Migrate google_id to ProviderIdentity

This migration moves Google identity data from the legacy User.google_id column
to the ProviderIdentity table, then drops the google_id column.

Changes:
- Creates ProviderIdentity rows for all users with google_id (idempotent)
- Drops ix_users_google_id index
- Drops google_id column from users table

Idempotency guarantees:
- Upgrade: Safe to run multiple times. Skips if google_id column doesn't exist.
           Skips individual users if ProviderIdentity already exists.
- Downgrade: Safe to run multiple times. Only recreates column if missing.
             Only restores values for users that don't already have google_id set.
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

from migrations.helpers import (
    column_exists,
    index_exists,
)

# revision identifiers, used by Alembic.
revision = "r006_0004"
down_revision = "r006_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Migrate google_id to ProviderIdentity and drop column.

    Idempotent: Safe to run multiple times.
    - If google_id column doesn't exist, does nothing.
    - If ProviderIdentity already exists for a user, skips that user.
    - If index doesn't exist, skips index drop.
    """
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
        # Check by user_id + provider_key + account_id (unique constraint)
        existing = conn.execute(
            text("""
                SELECT id FROM provider_identities
                WHERE user_id = :user_id
                AND provider_key = 'google'
                AND account_id = :account_id
            """),
            {"user_id": user.id, "account_id": user.google_id},
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
                },
            )

    # Drop index if it exists (idempotent)
    if index_exists(inspector, "users", "ix_users_google_id"):
        op.drop_index("ix_users_google_id", "users")

    # Drop google_id column (only if it exists - already checked above)
    op.drop_column("users", "google_id")


def downgrade() -> None:
    """Restore google_id column and migrate data back from ProviderIdentity.

    Idempotent: Safe to run multiple times.
    - If google_id column already exists, skips column creation.
    - Only updates users where google_id is NULL (doesn't overwrite existing values).
    - If index already exists, skips index creation.
    - Only deletes ProviderIdentity rows that were successfully restored.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Add google_id column back if it doesn't exist (idempotent)
    if not column_exists(inspector, "users", "google_id"):
        op.add_column("users", sa.Column("google_id", sa.String(), nullable=True))
        # Re-inspect after adding column
        inspector = sa.inspect(conn)

    # Restore google_id values from ProviderIdentity
    # Only restore for users where google_id is currently NULL (idempotent)
    google_identities = conn.execute(
        text("""
            SELECT pi.id as identity_id, pi.user_id, pi.account_id
            FROM provider_identities pi
            JOIN users u ON u.id = pi.user_id
            WHERE pi.provider_key = 'google'
            AND u.google_id IS NULL
        """)
    ).fetchall()

    restored_identity_ids = []
    for identity in google_identities:
        conn.execute(
            text("""
                UPDATE users
                SET google_id = :google_id
                WHERE id = :user_id
                AND google_id IS NULL
            """),
            {"user_id": identity.user_id, "google_id": identity.account_id},
        )
        restored_identity_ids.append(identity.identity_id)

    # Recreate unique index if it doesn't exist (idempotent)
    if not index_exists(inspector, "users", "ix_users_google_id"):
        op.create_index("ix_users_google_id", "users", ["google_id"], unique=True)

    # Remove only the ProviderIdentity rows that were restored
    # This preserves any Google identities created after the migration
    for identity_id in restored_identity_ids:
        conn.execute(
            text("""
                DELETE FROM provider_identities
                WHERE id = :id
            """),
            {"id": identity_id},
        )
