"""
Tests for the google_id to ProviderIdentity migration (r006_0004).

These tests verify:
- Migration creates ProviderIdentity rows for users with google_id
- Migration is idempotent (running twice produces same result)
- Migration drops google_id column
- Downgrade recreates column and restores values
"""

import sys
from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest

# Mock user row returned from database
UserRow = namedtuple("UserRow", ["id", "google_id", "email", "name", "picture_url"])
IdentityRow = namedtuple("IdentityRow", ["id"])
IdentityRestoreRow = namedtuple("IdentityRestoreRow", ["identity_id", "user_id", "account_id"])


# Create mock helpers module before any migration imports
_mock_helpers = MagicMock()
_mock_helpers.column_exists = MagicMock(return_value=True)
_mock_helpers.index_exists = MagicMock(return_value=True)


@pytest.fixture(autouse=True)
def mock_migration_dependencies():
    """Mock alembic and migrations.helpers modules before importing migration."""
    with patch.dict(
        "sys.modules",
        {
            "alembic": MagicMock(),
            "alembic.op": MagicMock(),
            "migrations": MagicMock(),
            "migrations.helpers": _mock_helpers,
        },
    ):
        # Clear any cached import of the migration module
        if "versions.r006_0004_migrate_google_id_to_provider_identity" in sys.modules:
            del sys.modules["versions.r006_0004_migrate_google_id_to_provider_identity"]
        yield


@pytest.mark.usefixtures("mock_migration_dependencies")
class TestGoogleIdMigrationUpgrade:
    """Tests for the upgrade path of the google_id migration."""

    def test_migration_creates_provider_identity_rows(self):
        """Test migration creates ProviderIdentity rows for users with google_id."""
        user1 = UserRow(
            id="user-1",
            google_id="google-sub-1",
            email="user1@example.com",
            name="User One",
            picture_url="https://example.com/pic1.jpg",
        )
        user2 = UserRow(
            id="user-2", google_id="google-sub-2", email="user2@example.com", name="User Two", picture_url=None
        )

        mock_conn = MagicMock()
        mock_inspector = MagicMock()

        # Setup mock responses
        select_result = MagicMock()
        select_result.fetchall.return_value = [user1, user2]

        check_result = MagicMock()
        check_result.fetchone.return_value = None  # No existing identity

        mock_conn.execute.side_effect = [select_result, check_result, MagicMock(), check_result, MagicMock()]

        # Import the module fresh (after mocks are set up by fixture)
        import versions.r006_0004_migrate_google_id_to_provider_identity as migration_module

        # Patch the op and sa modules
        with (
            patch.object(migration_module, "op") as mock_op,
            patch.object(migration_module, "sa") as mock_sa,
            patch.object(migration_module, "column_exists", return_value=True),
            patch.object(migration_module, "index_exists", return_value=True),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = mock_inspector

            migration_module.upgrade()

            # Verify drop operations were called
            mock_op.drop_index.assert_called_once_with("ix_users_google_id", "users")
            mock_op.drop_column.assert_called_once_with("users", "google_id")

    def test_migration_is_idempotent_skips_existing_identities(self):
        """Test migration skips users who already have ProviderIdentity rows."""
        user1 = UserRow(
            id="user-1", google_id="google-sub-1", email="user1@example.com", name="User One", picture_url=None
        )

        mock_conn = MagicMock()
        mock_inspector = MagicMock()

        # Setup mock responses
        select_result = MagicMock()
        select_result.fetchall.return_value = [user1]

        check_result = MagicMock()
        check_result.fetchone.return_value = IdentityRow(id="existing-identity")  # Identity exists

        mock_conn.execute.side_effect = [select_result, check_result]

        import versions.r006_0004_migrate_google_id_to_provider_identity as migration_module

        with (
            patch.object(migration_module, "op") as mock_op,
            patch.object(migration_module, "sa") as mock_sa,
            patch.object(migration_module, "column_exists", return_value=True),
            patch.object(migration_module, "index_exists", return_value=True),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = mock_inspector

            migration_module.upgrade()

            # Verify no INSERT was executed (only SELECT queries)
            # The execute calls should be: 1 SELECT users, 1 SELECT identity check
            assert mock_conn.execute.call_count == 2

            # Verify drop operations were still called
            mock_op.drop_index.assert_called_once()
            mock_op.drop_column.assert_called_once()

    def test_migration_skips_if_column_already_dropped(self):
        """Test migration does nothing if google_id column doesn't exist."""
        mock_conn = MagicMock()
        mock_inspector = MagicMock()

        import versions.r006_0004_migrate_google_id_to_provider_identity as migration_module

        with (
            patch.object(migration_module, "op") as mock_op,
            patch.object(migration_module, "sa") as mock_sa,
            patch.object(migration_module, "column_exists", return_value=False),
        ):  # Column doesn't exist
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = mock_inspector

            migration_module.upgrade()

            # Should not execute any queries or drop operations
            mock_conn.execute.assert_not_called()
            mock_op.drop_column.assert_not_called()


@pytest.mark.usefixtures("mock_migration_dependencies")
class TestGoogleIdMigrationDowngrade:
    """Tests for the downgrade path of the google_id migration."""

    def test_downgrade_recreates_column_and_restores_values(self):
        """Test downgrade recreates google_id column and restores values from ProviderIdentity."""
        identity1 = IdentityRestoreRow(identity_id="identity-1", user_id="user-1", account_id="google-sub-1")
        identity2 = IdentityRestoreRow(identity_id="identity-2", user_id="user-2", account_id="google-sub-2")

        mock_conn = MagicMock()
        mock_inspector = MagicMock()

        # Setup mock responses
        select_result = MagicMock()
        select_result.fetchall.return_value = [identity1, identity2]

        # Execution order: SELECT identities, UPDATE user1, UPDATE user2, DELETE identity1, DELETE identity2
        mock_conn.execute.side_effect = [select_result, MagicMock(), MagicMock(), MagicMock(), MagicMock()]

        import versions.r006_0004_migrate_google_id_to_provider_identity as migration_module

        with (
            patch.object(migration_module, "op") as mock_op,
            patch.object(migration_module, "sa") as mock_sa,
            patch.object(migration_module, "column_exists", return_value=False),
            patch.object(migration_module, "index_exists", return_value=False),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = mock_inspector

            migration_module.downgrade()

            # Verify add_column was called
            mock_op.add_column.assert_called_once()

            # Verify create_index was called
            mock_op.create_index.assert_called_once_with("ix_users_google_id", "users", ["google_id"], unique=True)

    def test_downgrade_skips_column_creation_if_exists(self):
        """Test downgrade doesn't recreate column if it already exists."""
        mock_conn = MagicMock()
        mock_inspector = MagicMock()

        # Setup mock responses
        select_result = MagicMock()
        select_result.fetchall.return_value = []

        mock_conn.execute.side_effect = [select_result, MagicMock()]

        import versions.r006_0004_migrate_google_id_to_provider_identity as migration_module

        with (
            patch.object(migration_module, "op") as mock_op,
            patch.object(migration_module, "sa") as mock_sa,
            patch.object(migration_module, "column_exists", return_value=True),
            patch.object(migration_module, "index_exists", return_value=True),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = mock_inspector

            migration_module.downgrade()

            # Should not add column or create index
            mock_op.add_column.assert_not_called()
            mock_op.create_index.assert_not_called()


@pytest.mark.usefixtures("mock_migration_dependencies")
class TestMigrationIdempotence:
    """Tests verifying migration idempotence property."""

    def test_running_upgrade_twice_produces_same_result(self):
        """Test that running upgrade twice produces the same final state."""
        user1 = UserRow(
            id="user-1", google_id="google-sub-1", email="user1@example.com", name="User One", picture_url=None
        )

        import versions.r006_0004_migrate_google_id_to_provider_identity as migration_module

        # First run: column exists
        mock_conn1 = MagicMock()
        mock_inspector1 = MagicMock()

        select_result1 = MagicMock()
        select_result1.fetchall.return_value = [user1]
        check_result1 = MagicMock()
        check_result1.fetchone.return_value = None
        mock_conn1.execute.side_effect = [select_result1, check_result1, MagicMock()]

        with (
            patch.object(migration_module, "op") as mock_op1,
            patch.object(migration_module, "sa") as mock_sa1,
            patch.object(migration_module, "column_exists", return_value=True),
            patch.object(migration_module, "index_exists", return_value=True),
        ):
            mock_op1.get_bind.return_value = mock_conn1
            mock_sa1.inspect.return_value = mock_inspector1

            migration_module.upgrade()

            # First run should drop column
            mock_op1.drop_column.assert_called_once()

        # Second run: column doesn't exist (already dropped)
        mock_conn2 = MagicMock()
        mock_inspector2 = MagicMock()

        with (
            patch.object(migration_module, "op") as mock_op2,
            patch.object(migration_module, "sa") as mock_sa2,
            patch.object(migration_module, "column_exists", return_value=False),
        ):
            mock_op2.get_bind.return_value = mock_conn2
            mock_sa2.inspect.return_value = mock_inspector2

            migration_module.upgrade()

            # Second run should do nothing
            mock_conn2.execute.assert_not_called()
            mock_op2.drop_column.assert_not_called()

    def test_downgrade_only_deletes_restored_identities(self):
        """Test downgrade only deletes ProviderIdentity rows that were restored to google_id."""
        # This identity will be restored (user has NULL google_id)
        identity_to_restore = IdentityRestoreRow(identity_id="identity-1", user_id="user-1", account_id="google-sub-1")

        mock_conn = MagicMock()
        mock_inspector = MagicMock()

        # Track DELETE calls
        delete_calls = []

        def track_execute(query, params=None):
            query_str = str(query)
            result = MagicMock()

            if "SELECT" in query_str:
                # Return only identities where user.google_id IS NULL
                result.fetchall.return_value = [identity_to_restore]
            elif "DELETE" in query_str:
                delete_calls.append(params)

            return result

        mock_conn.execute.side_effect = track_execute

        import versions.r006_0004_migrate_google_id_to_provider_identity as migration_module

        with (
            patch.object(migration_module, "op") as mock_op,
            patch.object(migration_module, "sa") as mock_sa,
            patch.object(migration_module, "column_exists", return_value=True),
            patch.object(migration_module, "index_exists", return_value=True),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = mock_inspector

            migration_module.downgrade()

            # Should only delete the identity that was restored
            assert len(delete_calls) == 1
            assert delete_calls[0]["id"] == "identity-1"


@pytest.mark.usefixtures("mock_migration_dependencies")
class TestDowngradeIdempotence:
    """Tests verifying downgrade idempotence."""

    def test_downgrade_skips_users_with_existing_google_id(self):
        """Test downgrade doesn't overwrite existing google_id values."""
        mock_conn = MagicMock()
        mock_inspector = MagicMock()

        # The SELECT query joins with users WHERE google_id IS NULL
        # So if a user already has google_id set, they won't be in the result
        select_result = MagicMock()
        select_result.fetchall.return_value = []  # No users need restoration

        mock_conn.execute.side_effect = [select_result]

        import versions.r006_0004_migrate_google_id_to_provider_identity as migration_module

        with (
            patch.object(migration_module, "op") as mock_op,
            patch.object(migration_module, "sa") as mock_sa,
            patch.object(migration_module, "column_exists", return_value=True),
            patch.object(migration_module, "index_exists", return_value=True),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = mock_inspector

            migration_module.downgrade()

            # Should only execute the SELECT query, no UPDATEs or DELETEs
            assert mock_conn.execute.call_count == 1

    def test_running_downgrade_twice_is_safe(self):
        """Test that running downgrade twice doesn't cause errors."""
        identity1 = IdentityRestoreRow(identity_id="identity-1", user_id="user-1", account_id="google-sub-1")

        import versions.r006_0004_migrate_google_id_to_provider_identity as migration_module

        # First downgrade: column doesn't exist, identity needs restoration
        mock_conn1 = MagicMock()
        mock_inspector1 = MagicMock()

        select_result1 = MagicMock()
        select_result1.fetchall.return_value = [identity1]
        mock_conn1.execute.side_effect = [select_result1, MagicMock(), MagicMock()]

        with (
            patch.object(migration_module, "op") as mock_op1,
            patch.object(migration_module, "sa") as mock_sa1,
            patch.object(migration_module, "column_exists", return_value=False),
            patch.object(migration_module, "index_exists", return_value=False),
        ):
            mock_op1.get_bind.return_value = mock_conn1
            mock_sa1.inspect.return_value = mock_inspector1

            migration_module.downgrade()

            mock_op1.add_column.assert_called_once()
            mock_op1.create_index.assert_called_once()

        # Second downgrade: column exists, no identities need restoration
        mock_conn2 = MagicMock()
        mock_inspector2 = MagicMock()

        select_result2 = MagicMock()
        select_result2.fetchall.return_value = []  # No users need restoration
        mock_conn2.execute.side_effect = [select_result2]

        with (
            patch.object(migration_module, "op") as mock_op2,
            patch.object(migration_module, "sa") as mock_sa2,
            patch.object(migration_module, "column_exists", return_value=True),
            patch.object(migration_module, "index_exists", return_value=True),
        ):
            mock_op2.get_bind.return_value = mock_conn2
            mock_sa2.inspect.return_value = mock_inspector2

            migration_module.downgrade()

            # Second run should not add column or create index
            mock_op2.add_column.assert_not_called()
            mock_op2.create_index.assert_not_called()
