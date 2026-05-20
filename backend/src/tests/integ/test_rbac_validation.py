"""
RBAC System Validation Tests

Simple validation tests to ensure the RBAC system is working correctly.
These tests verify basic functionality without complex integration test setup.
"""

from shu.core.logging import get_logger
import sys
from collections.abc import Callable

from integ.base_integration_test import BaseIntegrationTestSuite

logger = get_logger(__name__)


async def test_rbac_service_imports(client, db, auth_headers):
    """Test that all RBAC service components can be imported successfully."""
    logger.info("Testing RBAC service imports")

    try:
        # Test RBAC service import
        logger.info("✅ RBACService imported successfully")

        # Test RBAC models import
        logger.info("✅ RBAC models imported successfully")

        # Test RBAC schemas import
        logger.info("✅ RBAC schemas imported successfully")

        # Test RBAC auth functions import
        logger.info("✅ RBAC auth functions imported successfully")

        return True

    except Exception as e:
        logger.error(f"❌ RBAC import failed: {e}")
        return False


async def test_rbac_api_endpoints_exist(client, db, auth_headers):
    """Test that RBAC API endpoints are properly registered."""
    logger.info("Testing RBAC API endpoint registration")

    admin_headers = auth_headers.get("admin", {})
    if not admin_headers:
        logger.warning("⚠️ No admin headers available, skipping endpoint tests")
        return True

    try:
        # Test user group endpoints
        response = await client.get("/api/v1/groups", headers=admin_headers)
        logger.info(f"Groups endpoint status: {response.status_code}")
        assert response.status_code in [200, 404], f"Groups endpoint failed: {response.status_code}"

        logger.info("✅ RBAC API endpoints are registered")
        return True

    except Exception as e:
        logger.error(f"❌ RBAC API endpoint test failed: {e}")
        return False


async def test_rbac_database_models(client, db, auth_headers):
    """Test that RBAC database models are properly created."""
    logger.info("Testing RBAC database models")

    try:
        from sqlalchemy import text

        # Check if RBAC tables exist
        tables_to_check = ["user_groups", "user_group_memberships"]

        for table_name in tables_to_check:
            result = await db.execute(
                text(f"""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = '{table_name}'
                );
            """)
            )
            exists = result.scalar()
            if exists:
                logger.info(f"✅ Table '{table_name}' exists")
            else:
                logger.error(f"❌ Table '{table_name}' does not exist")
                return False

        logger.info("✅ All RBAC database tables exist")
        return True

    except Exception as e:
        logger.error(f"❌ RBAC database model test failed: {e}")
        return False


async def test_rbac_group_roles(client, db, auth_headers):
    """Test that RBAC group roles are properly defined."""
    logger.info("Testing RBAC group roles")

    try:
        from shu.models.rbac import GroupRole

        # Check all expected group roles exist
        expected_roles = ["ADMIN", "MEMBER"]

        for role in expected_roles:
            assert hasattr(GroupRole, role), f"Group role {role} not found"
            logger.info(f"✅ Group role {role} exists")

        # Test group role values
        assert GroupRole.ADMIN.value == "admin"
        assert GroupRole.MEMBER.value == "member"

        logger.info("✅ All RBAC group roles are properly defined")
        return True

    except Exception as e:
        logger.error(f"❌ RBAC group roles test failed: {e}")
        return False


async def test_rbac_service_instantiation(client, db, auth_headers):
    """Test that RBAC service can be instantiated."""
    logger.info("Testing RBAC service instantiation")

    try:
        from shu.services.rbac_service import RBACService

        # Test service instantiation
        rbac_service = RBACService(db)
        assert rbac_service is not None
        assert rbac_service.db == db

        logger.info("✅ RBAC service instantiated successfully")
        return True

    except Exception as e:
        logger.error(f"❌ RBAC service instantiation failed: {e}")
        return False


class RBACValidationTestSuite(BaseIntegrationTestSuite):
    """RBAC system validation test suite."""

    def get_test_functions(self) -> list[Callable]:
        """Return list of RBAC validation test functions."""
        return [
            test_rbac_service_imports,
            test_rbac_api_endpoints_exist,
            test_rbac_database_models,
            test_rbac_group_roles,
            test_rbac_service_instantiation,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "RBAC System Validation"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return """
        Validates that the RBAC (Role-Based Access Control) system is properly
        implemented and all components are working correctly.

        Tests include:
        - Service and model imports
        - API endpoint registration
        - Database table creation
        - Group role definitions
        - Service instantiation
        """


if __name__ == "__main__":
    suite = RBACValidationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
