"""
Tests for the google_id to ProviderIdentity migration (Part 12 of 006 squash).

These tests verify:
- Migration creates ProviderIdentity rows for users with google_id
- Migration is idempotent (running twice produces same result)
- Migration drops google_id column
- Downgrade recreates column and restores values
"""

import importlib
import sys
from collections import namedtuple
from unittest.mock import MagicMock, call, patch

import pytest

# Mock user row returned from database
UserRow = namedtuple("UserRow", ["id", "google_id", "email", "name", "picture_url"])
IdentityRow = namedtuple("IdentityRow", ["id"])
IdentityRestoreRow = namedtuple("IdentityRestoreRow", ["identity_id", "user_id", "account_id"])

MODULE_NAME = "versions.006_sixth_release_squash"


def _fresh_import():
    """Import the 006 squash migration module fresh (clearing cached import)."""
    for key in list(sys.modules.keys()):
        if "006_sixth_release_squash" in key:
            del sys.modules[key]
    return importlib.import_module(MODULE_NAME)


def _make_inspector(*, google_id_exists=True, google_id_index_exists=True):
    """Build a mock inspector that reports table/column/index state.

    Parts 1-11 of upgrade() use table_exists / column_exists / index_exists
    to decide whether to create schema objects.  We tell them everything
    already exists so those parts become no-ops, isolating Part 12.
    """
    inspector = MagicMock()
    return inspector


def _make_helpers(*, google_id_column_exists=True, google_id_index_exists=True):
    """Return a dict of patched helper functions for the squash module.

    All table/column/index helpers report "already exists" so Parts 1-11
    are no-ops.  google_id column/index state is configurable for Part 12.
    """
    def _column_exists(inspector, table, column):
        if table == "users" and column == "google_id":
            return google_id_column_exists
        return True  # everything else already exists

    def _index_exists(inspector, table, index):
        if table == "users" and index == "ix_users_google_id":
            return google_id_index_exists
        return True

    return {
        "column_exists": _column_exists,
        "index_exists": _index_exists,
        "table_exists": lambda insp, t: True,
        "add_column_if_not_exists": lambda *a, **kw: None,
        "drop_column_if_exists": lambda *a, **kw: None,
        "drop_table_if_exists": lambda *a, **kw: None,
    }



class TestGoogleIdMigrationUpgrade:
    """Tests for the upgrade path — Part 12 of the 006 squash."""

    def test_migration_creates_provider_identity_rows(self):
        """Upgrade creates ProviderIdentity rows for users with google_id."""
        migration = _fresh_import()

        user1 = UserRow("user-1", "google-sub-1", "u1@example.com", "User One", "https://pic/1")
        user2 = UserRow("user-2", "google-sub-2", "u2@example.com", "User Two", None)

        select_users = MagicMock()
        select_users.fetchall.return_value = [user1, user2]
        no_existing = MagicMock()
        no_existing.fetchone.return_value = None

        # execute calls: pgvector check, SELECT users, CHECK id1, INSERT id1, CHECK id2, INSERT id2
        mock_conn = MagicMock()
        pgvector_result = MagicMock()
        pgvector_result.scalar.return_value = False  # no pgvector — skip vector indexes
        mock_conn.execute.side_effect = [
            pgvector_result, select_users,
            no_existing, MagicMock(),  # user1: check + insert
            no_existing, MagicMock(),  # user2: check + insert
        ]

        helpers = _make_helpers(google_id_column_exists=True, google_id_index_exists=True)

        with (
            patch.object(migration, "op") as mock_op,
            patch.object(migration, "sa") as mock_sa,
            patch.object(migration, "column_exists", side_effect=helpers["column_exists"]),
            patch.object(migration, "index_exists", side_effect=helpers["index_exists"]),
            patch.object(migration, "table_exists", side_effect=helpers["table_exists"]),
            patch.object(migration, "add_column_if_not_exists", side_effect=helpers["add_column_if_not_exists"]),
            patch.object(migration, "drop_column_if_exists", side_effect=helpers["drop_column_if_exists"]),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = MagicMock()

            migration.upgrade()

            mock_op.drop_index.assert_called_once_with("ix_users_google_id", "users")
            mock_op.drop_column.assert_called_once_with("users", "google_id")

    def test_migration_skips_existing_identities(self):
        """Upgrade skips users who already have a ProviderIdentity row."""
        migration = _fresh_import()

        user1 = UserRow("user-1", "google-sub-1", "u1@example.com", "User One", None)

        select_users = MagicMock()
        select_users.fetchall.return_value = [user1]
        already_exists = MagicMock()
        already_exists.fetchone.return_value = IdentityRow(id="existing-id")

        mock_conn = MagicMock()
        pgvector_result = MagicMock()
        pgvector_result.scalar.return_value = False
        mock_conn.execute.side_effect = [pgvector_result, select_users, already_exists]

        helpers = _make_helpers(google_id_column_exists=True, google_id_index_exists=True)

        with (
            patch.object(migration, "op") as mock_op,
            patch.object(migration, "sa") as mock_sa,
            patch.object(migration, "column_exists", side_effect=helpers["column_exists"]),
            patch.object(migration, "index_exists", side_effect=helpers["index_exists"]),
            patch.object(migration, "table_exists", side_effect=helpers["table_exists"]),
            patch.object(migration, "add_column_if_not_exists", side_effect=helpers["add_column_if_not_exists"]),
            patch.object(migration, "drop_column_if_exists", side_effect=helpers["drop_column_if_exists"]),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = MagicMock()

            migration.upgrade()

            # Only 3 execute calls: pgvector check, SELECT users, CHECK identity
            assert mock_conn.execute.call_count == 3
            mock_op.drop_index.assert_called_once()
            mock_op.drop_column.assert_called_once()

    def test_migration_skips_if_column_already_dropped(self):
        """Upgrade is a no-op for Part 12 when google_id column is already gone."""
        migration = _fresh_import()

        mock_conn = MagicMock()
        pgvector_result = MagicMock()
        pgvector_result.scalar.return_value = False
        mock_conn.execute.side_effect = [pgvector_result]

        helpers = _make_helpers(google_id_column_exists=False)

        with (
            patch.object(migration, "op") as mock_op,
            patch.object(migration, "sa") as mock_sa,
            patch.object(migration, "column_exists", side_effect=helpers["column_exists"]),
            patch.object(migration, "index_exists", side_effect=helpers["index_exists"]),
            patch.object(migration, "table_exists", side_effect=helpers["table_exists"]),
            patch.object(migration, "add_column_if_not_exists", side_effect=helpers["add_column_if_not_exists"]),
            patch.object(migration, "drop_column_if_exists", side_effect=helpers["drop_column_if_exists"]),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = MagicMock()

            migration.upgrade()

            # Only the pgvector check — no user queries, no drops
            assert mock_conn.execute.call_count == 1
            mock_op.drop_column.assert_not_called()


class TestGoogleIdMigrationDowngrade:
    """Tests for the downgrade path — Part 12 (reverse) of the 006 squash."""

    def test_downgrade_recreates_column_and_restores_values(self):
        """Downgrade recreates google_id and restores values from ProviderIdentity."""
        migration = _fresh_import()

        id1 = IdentityRestoreRow("identity-1", "user-1", "google-sub-1")
        id2 = IdentityRestoreRow("identity-2", "user-2", "google-sub-2")

        select_result = MagicMock()
        select_result.fetchall.return_value = [id1, id2]

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [
            select_result,       # SELECT provider_identities
            MagicMock(),         # UPDATE user-1
            MagicMock(),         # UPDATE user-2
            MagicMock(),         # DELETE identity-1
            MagicMock(),         # DELETE identity-2
        ]

        def _col_exists(inspector, table, column):
            if table == "users" and column == "google_id":
                return False  # needs recreation
            if table == "conversations" and column == "is_favorite":
                return False  # already removed
            return True

        def _idx_exists(inspector, table, index):
            if table == "users" and index == "ix_users_google_id":
                return False
            return True

        with (
            patch.object(migration, "op") as mock_op,
            patch.object(migration, "sa") as mock_sa,
            patch.object(migration, "column_exists", side_effect=_col_exists),
            patch.object(migration, "index_exists", side_effect=_idx_exists),
            patch.object(migration, "add_column_if_not_exists", side_effect=lambda *a, **kw: None),
            patch.object(migration, "drop_column_if_exists", side_effect=lambda *a, **kw: None),
            patch.object(migration, "drop_table_if_exists", side_effect=lambda *a, **kw: None),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = MagicMock()

            migration.downgrade()

            # google_id column recreated
            mock_op.add_column.assert_any_call(
                "users",
                pytest.approx(mock_op.add_column.call_args_list[0][0][1], abs=0),
            )
            # index recreated
            mock_op.create_index.assert_any_call(
                "ix_users_google_id", "users", ["google_id"], unique=True
            )

    def test_downgrade_skips_column_creation_if_exists(self):
        """Downgrade doesn't recreate google_id if it already exists."""
        migration = _fresh_import()

        select_result = MagicMock()
        select_result.fetchall.return_value = []  # no identities to restore

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [select_result]

        def _col_exists(inspector, table, column):
            if table == "users" and column == "google_id":
                return True  # already exists
            if table == "conversations" and column == "is_favorite":
                return False
            return True

        def _idx_exists(inspector, table, index):
            if table == "users" and index == "ix_users_google_id":
                return True  # already exists
            return True

        with (
            patch.object(migration, "op") as mock_op,
            patch.object(migration, "sa") as mock_sa,
            patch.object(migration, "column_exists", side_effect=_col_exists),
            patch.object(migration, "index_exists", side_effect=_idx_exists),
            patch.object(migration, "add_column_if_not_exists", side_effect=lambda *a, **kw: None),
            patch.object(migration, "drop_column_if_exists", side_effect=lambda *a, **kw: None),
            patch.object(migration, "drop_table_if_exists", side_effect=lambda *a, **kw: None),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = MagicMock()

            migration.downgrade()

            # Should NOT add google_id column (already exists)
            for c in mock_op.add_column.call_args_list:
                assert c[0][0] != "users" or "google_id" not in str(c)


class TestMigrationIdempotence:
    """Tests verifying migration idempotence for the google_id portion."""

    def test_running_upgrade_twice_produces_same_result(self):
        """Running upgrade twice: first run migrates, second is a no-op."""
        migration = _fresh_import()

        user1 = UserRow("user-1", "google-sub-1", "u1@example.com", "User One", None)

        # --- First run: google_id column exists ---
        mock_conn1 = MagicMock()
        pgv1 = MagicMock(); pgv1.scalar.return_value = False
        sel1 = MagicMock(); sel1.fetchall.return_value = [user1]
        chk1 = MagicMock(); chk1.fetchone.return_value = None
        mock_conn1.execute.side_effect = [pgv1, sel1, chk1, MagicMock()]

        helpers1 = _make_helpers(google_id_column_exists=True, google_id_index_exists=True)

        with (
            patch.object(migration, "op") as mock_op1,
            patch.object(migration, "sa") as mock_sa1,
            patch.object(migration, "column_exists", side_effect=helpers1["column_exists"]),
            patch.object(migration, "index_exists", side_effect=helpers1["index_exists"]),
            patch.object(migration, "table_exists", side_effect=helpers1["table_exists"]),
            patch.object(migration, "add_column_if_not_exists", side_effect=helpers1["add_column_if_not_exists"]),
            patch.object(migration, "drop_column_if_exists", side_effect=helpers1["drop_column_if_exists"]),
        ):
            mock_op1.get_bind.return_value = mock_conn1
            mock_sa1.inspect.return_value = MagicMock()
            migration.upgrade()
            mock_op1.drop_column.assert_called_once()

        # --- Second run: google_id column already dropped ---
        mock_conn2 = MagicMock()
        pgv2 = MagicMock(); pgv2.scalar.return_value = False
        mock_conn2.execute.side_effect = [pgv2]

        helpers2 = _make_helpers(google_id_column_exists=False)

        with (
            patch.object(migration, "op") as mock_op2,
            patch.object(migration, "sa") as mock_sa2,
            patch.object(migration, "column_exists", side_effect=helpers2["column_exists"]),
            patch.object(migration, "index_exists", side_effect=helpers2["index_exists"]),
            patch.object(migration, "table_exists", side_effect=helpers2["table_exists"]),
            patch.object(migration, "add_column_if_not_exists", side_effect=helpers2["add_column_if_not_exists"]),
            patch.object(migration, "drop_column_if_exists", side_effect=helpers2["drop_column_if_exists"]),
        ):
            mock_op2.get_bind.return_value = mock_conn2
            mock_sa2.inspect.return_value = MagicMock()
            migration.upgrade()
            mock_op2.drop_column.assert_not_called()

    def test_downgrade_only_deletes_restored_identities(self):
        """Downgrade only deletes ProviderIdentity rows that were restored."""
        migration = _fresh_import()

        identity = IdentityRestoreRow("identity-1", "user-1", "google-sub-1")

        delete_calls = []

        def track_execute(query, params=None):
            query_str = str(query)
            result = MagicMock()
            if "SELECT" in query_str:
                result.fetchall.return_value = [identity]
            elif "DELETE" in query_str:
                delete_calls.append(params)
            return result

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = track_execute

        def _col_exists(inspector, table, column):
            if table == "users" and column == "google_id":
                return True  # column already restored
            if table == "conversations" and column == "is_favorite":
                return False
            return True

        with (
            patch.object(migration, "op") as mock_op,
            patch.object(migration, "sa") as mock_sa,
            patch.object(migration, "column_exists", side_effect=_col_exists),
            patch.object(migration, "index_exists", return_value=True),
            patch.object(migration, "add_column_if_not_exists", side_effect=lambda *a, **kw: None),
            patch.object(migration, "drop_column_if_exists", side_effect=lambda *a, **kw: None),
            patch.object(migration, "drop_table_if_exists", side_effect=lambda *a, **kw: None),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = MagicMock()

            migration.downgrade()

        assert len(delete_calls) == 1
        assert delete_calls[0]["id"] == "identity-1"
