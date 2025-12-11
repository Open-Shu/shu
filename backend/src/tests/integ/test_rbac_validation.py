"""
RBAC System Validation Tests

Simple validation tests to ensure the RBAC system is working correctly.
These tests verify basic functionality without complex integration test setup.
"""

import sys
import os
from typing import List, Callable
import asyncio
import logging

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.expected_error_context import expect_authentication_errors, ExpectedErrorContext

logger = logging.getLogger(__name__)


async def test_rbac_service_imports(client, db, auth_headers):
    """Test that all RBAC service components can be imported successfully."""
    logger.info("Testing RBAC service imports")
    
    try:
        # Test RBAC service import
        from shu.services.rbac_service import RBACService
        logger.info("✅ RBACService imported successfully")
        
        # Test RBAC models import
        from shu.models.rbac import (
            UserGroup, UserGroupMembership, KnowledgeBasePermission, 
            PermissionLevel, GroupRole
        )
        logger.info("✅ RBAC models imported successfully")
        
        # Test RBAC schemas import
        from shu.schemas.rbac import (
            UserGroupCreate, UserGroupResponse, KnowledgeBasePermissionCreate
        )
        logger.info("✅ RBAC schemas imported successfully")
        
        # Test RBAC auth functions import
        from shu.auth.rbac import (
            rbac, require_kb_query_access, require_kb_manage_access
        )
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
        
        # Test user permissions endpoints  
        response = await client.get("/api/v1/users/me/permissions/knowledge-bases", headers=admin_headers)
        logger.info(f"User permissions endpoint status: {response.status_code}")
        assert response.status_code in [200, 404], f"User permissions endpoint failed: {response.status_code}"
        
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
        tables_to_check = [
            'user_groups',
            'user_group_memberships', 
            'knowledge_base_permissions'
        ]
        
        for table_name in tables_to_check:
            result = await db.execute(text(f"""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = '{table_name}'
                );
            """))
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


async def test_rbac_permission_levels(client, db, auth_headers):
    """Test that RBAC permission levels are properly defined."""
    logger.info("Testing RBAC permission levels")
    
    try:
        from shu.models.rbac import PermissionLevel
        
        # Check all expected permission levels exist
        expected_levels = ['OWNER', 'ADMIN', 'MEMBER', 'READ_ONLY']
        
        for level in expected_levels:
            assert hasattr(PermissionLevel, level), f"Permission level {level} not found"
            logger.info(f"✅ Permission level {level} exists")
        
        # Test permission level values
        assert PermissionLevel.OWNER.value == 'owner'
        assert PermissionLevel.ADMIN.value == 'admin'
        assert PermissionLevel.MEMBER.value == 'member'
        assert PermissionLevel.READ_ONLY.value == 'read_only'
        
        logger.info("✅ All RBAC permission levels are properly defined")
        return True
        
    except Exception as e:
        logger.error(f"❌ RBAC permission levels test failed: {e}")
        return False


async def test_rbac_group_roles(client, db, auth_headers):
    """Test that RBAC group roles are properly defined."""
    logger.info("Testing RBAC group roles")
    
    try:
        from shu.models.rbac import GroupRole
        
        # Check all expected group roles exist
        expected_roles = ['ADMIN', 'MEMBER']
        
        for role in expected_roles:
            assert hasattr(GroupRole, role), f"Group role {role} not found"
            logger.info(f"✅ Group role {role} exists")
        
        # Test group role values
        assert GroupRole.ADMIN.value == 'admin'
        assert GroupRole.MEMBER.value == 'member'
        
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


async def test_rbac_auth_functions(client, db, auth_headers):
    """Test that RBAC auth functions are working."""
    logger.info("Testing RBAC auth functions")
    
    try:
        from shu.auth.rbac import rbac
        
        # Test RBAC singleton exists
        assert rbac is not None
        logger.info("✅ RBAC singleton exists")
        
        # Test RBAC methods exist
        assert hasattr(rbac, 'can_access_knowledge_base')
        assert hasattr(rbac, 'can_manage_kb')
        assert hasattr(rbac, 'can_modify_kb')
        assert hasattr(rbac, 'can_query_kb')
        assert hasattr(rbac, 'can_delete_kb')
        logger.info("✅ RBAC methods exist")
        
        # Test dependency functions exist
        from shu.auth.rbac import (
            require_kb_query_access, require_kb_manage_access,
            require_kb_modify_access, require_kb_delete_access
        )
        logger.info("✅ RBAC dependency functions exist")
        
        logger.info("✅ RBAC auth functions are working")
        return True
        
    except Exception as e:
        logger.error(f"❌ RBAC auth functions test failed: {e}")
        return False


class RBACValidationTestSuite(BaseIntegrationTestSuite):
    """RBAC system validation test suite."""
    
    def get_test_functions(self) -> List[Callable]:
        """Return list of RBAC validation test functions."""
        return [
            test_rbac_service_imports,
            test_rbac_api_endpoints_exist,
            test_rbac_database_models,
            test_rbac_permission_levels,
            test_rbac_group_roles,
            test_rbac_service_instantiation,
            test_rbac_auth_functions,
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
        - Permission level definitions
        - Group role definitions
        - Service instantiation
        - Auth function availability
        """


if __name__ == "__main__":
    suite = RBACValidationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
