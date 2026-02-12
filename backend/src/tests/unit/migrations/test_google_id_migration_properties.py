"""
Property-based tests for google_id to ProviderIdentity migration
(Part 12 of the 006 squash).

Uses Hypothesis to verify universal properties across all valid inputs.
"""

import importlib
import sys
from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

UserRow = namedtuple("UserRow", ["id", "google_id", "email", "name", "picture_url"])
IdentityRow = namedtuple("IdentityRow", ["id"])

MODULE_NAME = "versions.006_sixth_release_squash"


def _fresh_import():
    """Import the 006 squash migration module fresh."""
    for key in list(sys.modules.keys()):
        if "006_sixth_release_squash" in key:
            del sys.modules[key]
    return importlib.import_module(MODULE_NAME)


def _make_helpers(*, google_id_column_exists=True):
    """Helper mocks that make Parts 1-11 no-ops, isolating Part 12."""
    def _column_exists(inspector, table, column):
        if table == "users" and column == "google_id":
            return google_id_column_exists
        return True

    return {
        "column_exists": _column_exists,
        "index_exists": lambda insp, t, i: True,
        "table_exists": lambda insp, t: True,
        "add_column_if_not_exists": lambda *a, **kw: None,
        "drop_column_if_exists": lambda *a, **kw: None,
        "drop_table_if_exists": lambda *a, **kw: None,
    }



class TestMigrationIdempotenceProperty:
    """
    Property: Migration is idempotent.

    For any database state, running the google_id migration N times
    produces the same final state as running it exactly once.
    """

    @given(
        num_users=st.integers(min_value=0, max_value=10),
        num_runs=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_property_migration_idempotence(self, num_users: int, num_runs: int):
        """
        Feature: sso-adapter-refactor, Property: idempotence

        Running the migration N times creates exactly num_users identities.
        """
        migration = _fresh_import()

        users = [
            UserRow(f"user-{i}", f"google-sub-{i}", f"u{i}@example.com", f"User {i}", None)
            for i in range(num_users)
        ]

        created_identities = set()

        for run in range(num_runs):
            column_exists_this_run = run == 0

            def execute_with_tracking(query, params=None):
                query_str = str(query)
                result = MagicMock()
                if "pg_extension" in query_str:
                    result.scalar.return_value = False
                elif "FROM users" in query_str:
                    result.fetchall.return_value = users
                elif "provider_identities" in query_str and "SELECT" in query_str:
                    if params and "user_id" in params:
                        uid = params["user_id"]
                        if uid in created_identities:
                            result.fetchone.return_value = IdentityRow(f"id-{uid}")
                        else:
                            result.fetchone.return_value = None
                    else:
                        result.fetchone.return_value = None
                elif "INSERT" in query_str and params and "user_id" in params:
                    created_identities.add(params["user_id"])
                return result

            mock_conn = MagicMock()
            mock_conn.execute.side_effect = execute_with_tracking

            helpers = _make_helpers(google_id_column_exists=column_exists_this_run)

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

        assert len(created_identities) == num_users

    @given(
        user_ids=st.lists(
            st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("L", "N"))),
            min_size=0,
            max_size=5,
            unique=True,
        )
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_property_no_duplicate_identities_created(self, user_ids: list):
        """
        Feature: sso-adapter-refactor, Property: no duplicates

        The migration never creates duplicate ProviderIdentity rows.
        """
        migration = _fresh_import()

        users = [
            UserRow(uid, f"google-{uid}", f"{uid}@example.com", f"User {uid}", None)
            for uid in user_ids
        ]

        insert_operations = []
        existing_identities = set()

        def execute_with_tracking(query, params=None):
            query_str = str(query)
            result = MagicMock()
            if "pg_extension" in query_str:
                result.scalar.return_value = False
            elif "FROM users" in query_str:
                result.fetchall.return_value = users
            elif "provider_identities" in query_str and "SELECT" in query_str:
                if params and "user_id" in params:
                    uid = params["user_id"]
                    if uid in existing_identities:
                        result.fetchone.return_value = IdentityRow(f"id-{uid}")
                    else:
                        result.fetchone.return_value = None
                else:
                    result.fetchone.return_value = None
            elif "INSERT" in query_str and params and "user_id" in params:
                insert_operations.append(params["user_id"])
                existing_identities.add(params["user_id"])
            return result

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = execute_with_tracking

        helpers = _make_helpers(google_id_column_exists=True)

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

        assert len(insert_operations) == len(set(insert_operations))
        assert len(insert_operations) == len(user_ids)

    @given(
        existing_identity_ratio=st.floats(min_value=0.0, max_value=1.0),
        num_users=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_property_skips_existing_identities(self, existing_identity_ratio: float, num_users: int):
        """
        Feature: sso-adapter-refactor, Property: skip existing

        The migration skips users who already have ProviderIdentity rows.
        """
        migration = _fresh_import()

        users = [
            UserRow(f"user-{i}", f"google-sub-{i}", f"u{i}@example.com", f"User {i}", None)
            for i in range(num_users)
        ]

        num_existing = int(num_users * existing_identity_ratio)
        users_with_existing = set(f"user-{i}" for i in range(num_existing))
        insert_operations = []

        def execute_with_tracking(query, params=None):
            query_str = str(query)
            result = MagicMock()
            if "pg_extension" in query_str:
                result.scalar.return_value = False
            elif "FROM users" in query_str:
                result.fetchall.return_value = users
            elif "provider_identities" in query_str and "SELECT" in query_str:
                if params and "user_id" in params:
                    uid = params["user_id"]
                    if uid in users_with_existing:
                        result.fetchone.return_value = IdentityRow(f"id-{uid}")
                    else:
                        result.fetchone.return_value = None
                else:
                    result.fetchone.return_value = None
            elif "INSERT" in query_str and params and "user_id" in params:
                insert_operations.append(params["user_id"])
            return result

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = execute_with_tracking

        helpers = _make_helpers(google_id_column_exists=True)

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

        expected_inserts = num_users - num_existing
        assert len(insert_operations) == expected_inserts
        for uid in insert_operations:
            assert uid not in users_with_existing
