"""
Property-based tests for google_id to ProviderIdentity migration.

These tests use Hypothesis to verify universal properties across all valid inputs.
"""

import sys
from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Mock user row returned from database
UserRow = namedtuple("UserRow", ["id", "google_id", "email", "name", "picture_url"])
IdentityRow = namedtuple("IdentityRow", ["id"])


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
class TestMigrationIdempotenceProperty:
    """
    Property 4: Migration is idempotent.

    For any database state, running the google_id migration N times (where N >= 1)
    SHALL produce the same final state as running it exactly once. Specifically,
    the count of ProviderIdentity rows with provider_key="google" SHALL be equal
    to the count of users with non-null google_id values, regardless of how many
    times the migration is executed.

    **Validates: Requirements 2.4, 4.6**
    """

    @given(num_users=st.integers(min_value=0, max_value=10), num_runs=st.integers(min_value=1, max_value=5))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_property_migration_idempotence(self, num_users: int, num_runs: int):
        """
        Feature: sso-adapter-refactor
        Property 4: Migration is idempotent

        **Validates: Requirements 2.4, 4.6**

        This property verifies that running the migration N times produces
        the same result as running it once. The number of ProviderIdentity
        rows created should equal the number of users with google_id.
        """
        import versions.r006_0004_migrate_google_id_to_provider_identity as migration_module

        # Generate test users with google_ids
        users = [
            UserRow(
                id=f"user-{i}",
                google_id=f"google-sub-{i}",
                email=f"user{i}@example.com",
                name=f"User {i}",
                picture_url=None,
            )
            for i in range(num_users)
        ]

        # Track created identities to simulate database state
        created_identities = set()

        def mock_execute(query):
            """Simulate database execution."""
            query_str = str(query)
            result = MagicMock()

            if "SELECT" in query_str and "users" in query_str:
                # Return users with google_id
                result.fetchall.return_value = users
            elif "SELECT" in query_str and "provider_identities" in query_str:
                # Check if identity exists
                # Extract user_id from the query parameters
                result.fetchone.return_value = None  # Will be set per-call
            elif "INSERT" in query_str:
                # Track the insert
                pass
            elif "DELETE" in query_str:
                pass

            return result

        # Simulate running migration multiple times
        for run in range(num_runs):
            mock_conn = MagicMock()
            mock_inspector = MagicMock()

            def execute_with_tracking(query, params=None):
                """Execute with identity tracking."""
                query_str = str(query)
                result = MagicMock()

                if "SELECT" in query_str and "FROM users" in query_str:
                    result.fetchall.return_value = users
                elif "SELECT" in query_str and "provider_identities" in query_str:
                    # Check if this identity was already created
                    if params and "user_id" in params:
                        user_id = params["user_id"]
                        if user_id in created_identities:
                            result.fetchone.return_value = IdentityRow(id=f"identity-{user_id}")
                        else:
                            result.fetchone.return_value = None
                    else:
                        result.fetchone.return_value = None
                elif "INSERT" in query_str and params:
                    # Track the created identity
                    if "user_id" in params:
                        created_identities.add(params["user_id"])

                return result

            mock_conn.execute.side_effect = execute_with_tracking

            # First run: column exists
            # Subsequent runs: column doesn't exist (already dropped)
            column_exists = run == 0

            with (
                patch.object(migration_module, "op") as mock_op,
                patch.object(migration_module, "sa") as mock_sa,
                patch.object(migration_module, "column_exists", return_value=column_exists),
                patch.object(migration_module, "index_exists", return_value=column_exists),
            ):
                mock_op.get_bind.return_value = mock_conn
                mock_sa.inspect.return_value = mock_inspector

                migration_module.upgrade()

        # Property assertion: number of created identities equals number of users
        assert len(created_identities) == num_users, f"Expected {num_users} identities, got {len(created_identities)}"

    @given(
        user_ids=st.lists(
            st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("L", "N"))),
            min_size=0,
            max_size=5,
            unique=True,
        )
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_property_no_duplicate_identities_created(self, user_ids: list):
        """
        Feature: sso-adapter-refactor
        Property 4: Migration is idempotent

        **Validates: Requirements 2.4, 4.6**

        This property verifies that running the migration never creates
        duplicate ProviderIdentity rows for the same user.
        """
        import versions.r006_0004_migrate_google_id_to_provider_identity as migration_module

        # Generate test users
        users = [
            UserRow(id=uid, google_id=f"google-{uid}", email=f"{uid}@example.com", name=f"User {uid}", picture_url=None)
            for uid in user_ids
        ]

        # Track all INSERT operations
        insert_operations = []
        existing_identities = set()

        def execute_with_tracking(query, params=None):
            """Execute with duplicate tracking."""
            query_str = str(query)
            result = MagicMock()

            if "SELECT" in query_str and "FROM users" in query_str:
                result.fetchall.return_value = users
            elif "SELECT" in query_str and "provider_identities" in query_str:
                if params and "user_id" in params:
                    user_id = params["user_id"]
                    if user_id in existing_identities:
                        result.fetchone.return_value = IdentityRow(id=f"identity-{user_id}")
                    else:
                        result.fetchone.return_value = None
                else:
                    result.fetchone.return_value = None
            elif "INSERT" in query_str and params:
                if "user_id" in params:
                    insert_operations.append(params["user_id"])
                    existing_identities.add(params["user_id"])

            return result

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = execute_with_tracking
        mock_inspector = MagicMock()

        with (
            patch.object(migration_module, "op") as mock_op,
            patch.object(migration_module, "sa") as mock_sa,
            patch.object(migration_module, "column_exists", return_value=True),
            patch.object(migration_module, "index_exists", return_value=True),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = mock_inspector

            # Run migration
            migration_module.upgrade()

        # Property assertion: no duplicate inserts
        assert len(insert_operations) == len(set(insert_operations)), f"Duplicate inserts detected: {insert_operations}"

        # Property assertion: exactly one insert per user
        assert len(insert_operations) == len(
            user_ids
        ), f"Expected {len(user_ids)} inserts, got {len(insert_operations)}"

    @given(
        existing_identity_ratio=st.floats(min_value=0.0, max_value=1.0),
        num_users=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_property_skips_existing_identities(self, existing_identity_ratio: float, num_users: int):
        """
        Feature: sso-adapter-refactor
        Property 4: Migration is idempotent

        **Validates: Requirements 2.4, 4.6**

        This property verifies that the migration skips users who already
        have ProviderIdentity rows, ensuring idempotent behavior.
        """
        import versions.r006_0004_migrate_google_id_to_provider_identity as migration_module

        # Generate test users
        users = [
            UserRow(
                id=f"user-{i}",
                google_id=f"google-sub-{i}",
                email=f"user{i}@example.com",
                name=f"User {i}",
                picture_url=None,
            )
            for i in range(num_users)
        ]

        # Determine which users already have identities
        num_existing = int(num_users * existing_identity_ratio)
        users_with_existing_identity = set(f"user-{i}" for i in range(num_existing))

        # Track INSERT operations
        insert_operations = []

        def execute_with_tracking(query, params=None):
            """Execute with existing identity simulation."""
            query_str = str(query)
            result = MagicMock()

            if "SELECT" in query_str and "FROM users" in query_str:
                result.fetchall.return_value = users
            elif "SELECT" in query_str and "provider_identities" in query_str:
                if params and "user_id" in params:
                    user_id = params["user_id"]
                    if user_id in users_with_existing_identity:
                        result.fetchone.return_value = IdentityRow(id=f"identity-{user_id}")
                    else:
                        result.fetchone.return_value = None
                else:
                    result.fetchone.return_value = None
            elif "INSERT" in query_str and params:
                if "user_id" in params:
                    insert_operations.append(params["user_id"])

            return result

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = execute_with_tracking
        mock_inspector = MagicMock()

        with (
            patch.object(migration_module, "op") as mock_op,
            patch.object(migration_module, "sa") as mock_sa,
            patch.object(migration_module, "column_exists", return_value=True),
            patch.object(migration_module, "index_exists", return_value=True),
        ):
            mock_op.get_bind.return_value = mock_conn
            mock_sa.inspect.return_value = mock_inspector

            migration_module.upgrade()

        # Property assertion: only users without existing identity get INSERT
        expected_inserts = num_users - num_existing
        assert (
            len(insert_operations) == expected_inserts
        ), f"Expected {expected_inserts} inserts, got {len(insert_operations)}"

        # Property assertion: no INSERT for users with existing identity
        for user_id in insert_operations:
            assert (
                user_id not in users_with_existing_identity
            ), f"Should not insert for user with existing identity: {user_id}"
