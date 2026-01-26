"""
Custom Integration Test Framework for Shu

This framework provides reliable integration testing without pytest-asyncio conflicts.
It uses a single event loop and real database connections to test actual application workflows.
"""

# =============================================================================
# TEST ENVIRONMENT CONFIGURATION
# Must be set BEFORE any shu imports to ensure settings pick up test overrides
# =============================================================================
import os

# Disable rate limiting for test runs - the test suite makes many API calls
# and rate limiting causes cascade failures unrelated to actual test logic
os.environ.setdefault("SHU_ENABLE_API_RATE_LIMITING", "false")

# =============================================================================

import asyncio
import logging
import traceback
from typing import List, Callable, Dict, Any, Optional
from datetime import datetime
import httpx
from sqlalchemy import text

from shu.main import app
from shu.core.logging import setup_logging
from shu.core.database import get_db_session
from shu.auth.models import User, UserRole
from shu.auth.jwt_manager import JWTManager
from integ.test_data_cleanup import TestDataCleaner
from integ.expected_error_context import expect_test_suite_errors

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestResult:
    """Represents the result of a single test."""
    
    def __init__(self, name: str, passed: bool, error: Optional[str] = None, duration: float = 0.0):
        self.name = name
        self.passed = passed
        self.error = error
        self.duration = duration
    
    def __str__(self):
        status = "✅ PASS" if self.passed else "❌ FAIL"
        duration_str = f"({self.duration:.3f}s)"
        if self.passed:
            return f"{status} {self.name} {duration_str}"
        else:
            return f"{status} {self.name} {duration_str}\n  Error: {self.error}"


class IntegrationTestRunner:
    """Custom integration test runner for Shu application."""
    
    def __init__(self, enable_file_logging: bool = False):
        self.app = app
        self.db = None
        self.client = None
        self.admin_user = None
        self.admin_token = None
        self.auth_headers = None
        self.test_results: List[TestResult] = []
        self.enable_file_logging = enable_file_logging
        self.log_file_handler = None
        self._lifespan_cm = None

    def setup_file_logging(self, wipe_file: bool = False):
        """Setup file logging to tests/testing.log."""
        if not self.enable_file_logging:
            return

        # Create tests directory if it doesn't exist
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        log_file_path = os.path.join(tests_dir, "testing.log")

        # Only wipe the file if explicitly requested (at start of test session)
        if wipe_file and os.path.exists(log_file_path):
            try:
                os.remove(log_file_path)
            except PermissionError:
                # File might be in use, try to truncate it instead
                with open(log_file_path, 'w') as f:
                    f.truncate(0)

        # Create file handler
        self.log_file_handler = logging.FileHandler(log_file_path)
        self.log_file_handler.setLevel(logging.INFO)

        # Create formatter
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self.log_file_handler.setFormatter(formatter)

        # Add handler to root logger to capture all logs
        root_logger = logging.getLogger()
        root_logger.addHandler(self.log_file_handler)

        if wipe_file:
            logger.info(f"📝 Test logging enabled: {log_file_path}")
        else:
            logger.info(f"📝 Test logging continuing: {log_file_path}")

    def cleanup_file_logging(self):
        """Clean up file logging handler."""
        if self.log_file_handler:
            root_logger = logging.getLogger()
            root_logger.removeHandler(self.log_file_handler)
            self.log_file_handler.close()
            self.log_file_handler = None

    async def setup(self, wipe_log_file: bool = False):
        """Initialize test environment with real database and API client."""
        # Ensure application logging is configured (idempotent)
        setup_logging()
        # Setup file logging first if enabled
        self.setup_file_logging(wipe_file=wipe_log_file)

        logger.info("🚀 Setting up integration test environment...")

        # Reduce noise from HTTP logging during tests
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("src.shu.core.middleware").setLevel(logging.WARNING)

        try:
            # Run FastAPI lifespan startup to ensure init_db() is executed
            try:
                self._lifespan_cm = self.app.router.lifespan_context(self.app)
                await self._lifespan_cm.__aenter__()
                logger.info("✅ App lifespan startup executed")
            except Exception as e:
                logger.warning(f"Lifespan startup error (continuing): {e}")

            # Get database session
            self.db = await get_db_session()
            logger.info("✅ Database connection established")

            # Create HTTP client for API testing
            self.client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://test"
            )
            logger.info("✅ API client created")

            # Create admin user for authenticated tests
            await self._create_admin_user()
            logger.info("✅ Admin user created")

            # Initial cleanup
            await self._cleanup_test_data()
            logger.info("✅ Test environment ready")

        except Exception as e:
            logger.error(f"❌ Setup failed: {e}")
            raise
    
    async def _create_admin_user(self):
        """Create an admin user for testing."""
        import uuid
        test_id = str(uuid.uuid4())[:8]
        
        # Clean up any existing test admin users (and dependent rows)
        await self.db.execute(text("DELETE FROM plugin_subscriptions WHERE user_id IN (SELECT id FROM users WHERE email LIKE 'test-admin-%@example.com')"))
        await self.db.execute(text("DELETE FROM provider_credentials WHERE user_id IN (SELECT id FROM users WHERE email LIKE 'test-admin-%@example.com')"))
        await self.db.execute(text("DELETE FROM users WHERE email LIKE 'test-admin-%@example.com'"))
        await self.db.commit()

        # Create new admin user
        self.admin_user = User(
            email=f"test-admin-{test_id}@example.com",
            name=f"Test Admin {test_id}",
            role=UserRole.ADMIN.value,
            google_id=f"test_admin_{test_id}",
            is_active=True
        )
        self.db.add(self.admin_user)
        await self.db.commit()
        await self.db.refresh(self.admin_user)
        
        # Generate JWT token
        jwt_manager = JWTManager()
        token_data = {
            "user_id": self.admin_user.id,
            "email": self.admin_user.email,
            "role": self.admin_user.role
        }
        self.admin_token = jwt_manager.create_access_token(token_data)
        self.auth_headers = {
            "Authorization": f"Bearer {self.admin_token}",
            "_user_id": self.admin_user.id,
        }
    
    async def _cleanup_test_data(self, quick: bool = True):
        """Clean up test data using API-based cleanup."""
        try:
            if self.client and self.auth_headers:
                if quick:
                    logger.info("🧹 Running quick test data cleanup...")
                    cleaner = TestDataCleaner(self.client, self.auth_headers)
                    stats = await cleaner.cleanup_test_data_quick()
                else:
                    logger.info("🧹 Running comprehensive test data cleanup...")
                    cleaner = TestDataCleaner(self.client, self.auth_headers)
                    stats = await cleaner.cleanup_all_test_data()

                # Log cleanup results
                total_cleaned = sum(stats.get(key, 0) for key in (
                    'plugins',
                    'knowledge_bases',
                    'sources',
                    'sync_jobs',
                    'llm_providers',
                    'prompts',
                    'prompt_assignments',
                    'conversations',
                    'users',
                    'model_configurations',
                ))
                if total_cleaned > 0:
                    logger.info(f"✅ Cleaned up {total_cleaned} test entities")

                if stats['errors']:
                    logger.warning(f"⚠️  {len(stats['errors'])} cleanup errors occurred")
            else:
                # Fallback to basic SQL cleanup if API client not available
                await self._basic_sql_cleanup()

        except Exception as e:
            logger.warning(f"Cleanup warning: {e}")
            # Try fallback cleanup
            try:
                await self._basic_sql_cleanup()
            except Exception as fallback_error:
                logger.warning(f"Fallback cleanup also failed: {fallback_error}")

    async def _basic_sql_cleanup(self):
        """Basic SQL-based cleanup as fallback."""
        try:
            # Clean up test data in dependency order

            # 1. Clean up model configurations first (they reference LLM providers)
            await self.db.execute(text("""
                DELETE FROM model_configurations
                WHERE name LIKE 'Test %' OR name LIKE '%Test%' OR name LIKE '%Integration%'
            """))

            # 2. Clean up LLM providers
            await self.db.execute(text("""
                DELETE FROM llm_providers
                WHERE name LIKE 'Test %' OR name LIKE '%Test Provider%' OR name LIKE '%Integration%'
                   OR name LIKE '%test%' OR name LIKE '%Test%'
            """))

            # 3. Clean up prompts
            await self.db.execute(text("""
                DELETE FROM prompts
                WHERE name LIKE 'Test %' OR name LIKE '%Test%' OR name LIKE '%Integration%'
                   OR name LIKE '%test%'
            """))

            # 4. Clean up knowledge bases and related data
            await self.db.execute(text("""
                DELETE FROM knowledge_bases
                WHERE name LIKE 'Test %' OR name LIKE '%Test%' OR name LIKE '%Integration%'
                   OR name LIKE '%test%'
            """))

            # 5. Clean up conversations
            await self.db.execute(text("""
                DELETE FROM conversations
                WHERE title LIKE 'Test %' OR title LIKE '%Test%' OR title LIKE '%Integration%'
                   OR title LIKE '%test%'
            """))

            # 6. Clean up test users (except our current admin user)
            if self.admin_user:
                await self.db.execute(text("""
                    DELETE FROM users
                    WHERE (email LIKE 'test-%' OR email LIKE '%@test.%' OR email LIKE '%integration%'
                           OR email LIKE '%test%' OR name LIKE 'Test %' OR name LIKE '%Test%')
                      AND id != :admin_id
                """), {"admin_id": self.admin_user.id})
            else:
                await self.db.execute(text("""
                    DELETE FROM users
                    WHERE email LIKE 'test-%' OR email LIKE '%@test.%' OR email LIKE '%integration%'
                       OR email LIKE '%test%' OR name LIKE 'Test %' OR name LIKE '%Test%'
                """))

            await self.db.commit()
            logger.info("✅ Basic SQL cleanup completed")

        except Exception as e:
            logger.warning(f"Basic cleanup warning: {e}")
            try:
                await self.db.rollback()
            except Exception as rollback_error:
                logger.warning(f"Rollback error: {rollback_error}")
    
    async def run_test(self, test_func: Callable, test_name: str = None) -> TestResult:
        """Run a single integration test."""
        if test_name is None:
            test_name = test_func.__name__

        start_time = datetime.now()

        try:
            # Run the test (no logging here, handled at suite level)
            await test_func(self.client, self.db, self.auth_headers)

            # Clean up after test
            await self._cleanup_test_data(quick=True)  # Use quick cleanup for individual tests

            duration = (datetime.now() - start_time).total_seconds()
            result = TestResult(test_name, True, duration=duration)

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            error_msg = f"{type(e).__name__}: {str(e)}"
            result = TestResult(test_name, False, error_msg, duration)

            # Log full traceback for debugging
            logger.debug(f"Full traceback for {test_name}:\n{traceback.format_exc()}")
        
        self.test_results.append(result)
        return result
    
    async def run_test_suite(self, tests: List[Callable]) -> Dict[str, Any]:
        """Run a suite of integration tests."""
        logger.info(f"🎯 Running integration test suite with {len(tests)} tests")
        print()  # Add blank line for better separation

        start_time = datetime.now()

        # Wrap the entire test suite with comprehensive expected error handling
        with expect_test_suite_errors():
            for i, test_func in enumerate(tests, 1):
                # Clear, prominent test start message with visual separation
                test_name = test_func.__name__
                separator = "=" * 80
                logger.info(f"{separator}")
                logger.info(f"📋 TEST {i}/{len(tests)}: {test_name}")
                logger.info(f"{separator}")
                print(f"📋 TEST {i}/{len(tests)}: {test_name}")

                result = await self.run_test(test_func)

                # Clear, prominent result message with visual separation
                if result.passed:
                    logger.info(f"✅ PASS: {test_name} ({result.duration:.3f}s)")
                    print(f"✅ PASS: {test_name} ({result.duration:.3f}s)")
                else:
                    logger.error(f"❌ FAIL: {test_name} ({result.duration:.3f}s)")
                    logger.error(f"   Error: {result.error}")
                    print(f"❌ FAIL: {test_name} ({result.duration:.3f}s)")
                    print(f"   Error: {result.error}")

                logger.info(f"--------------------------------------------------------------------------------\n")
                print()  # Add blank line after each test

        total_duration = (datetime.now() - start_time).total_seconds()

        # Calculate results
        passed_tests = [r for r in self.test_results if r.passed]
        failed_tests = [r for r in self.test_results if not r.passed]

        results = {
            "total": len(self.test_results),
            "passed": len(passed_tests),
            "failed": len(failed_tests),
            "duration": total_duration,
            "pass_rate": len(passed_tests) / len(self.test_results) * 100 if self.test_results else 0
        }

        return results
    
    def print_summary(self):
        """Print test results summary."""
        if not self.test_results:
            logger.info("No tests were run")
            return
        
        print("\n" + "="*80)
        print("🧪 INTEGRATION TEST RESULTS")
        print("="*80)
        
        # Print individual test results
        for result in self.test_results:
            print(result)
        
        # Print summary
        passed = len([r for r in self.test_results if r.passed])
        failed = len([r for r in self.test_results if not r.passed])
        total_duration = sum(r.duration for r in self.test_results)
        
        print("\n" + "-"*80)
        print(f"📊 SUMMARY: {passed}/{len(self.test_results)} tests passed ({passed/len(self.test_results)*100:.1f}%)")
        print(f"⏱️  Total time: {total_duration:.3f}s")
        
        if failed > 0:
            print(f"❌ {failed} tests failed")
            return False
        else:
            print("🎉 All tests passed!")
            return True
    
    async def teardown(self):
        """Clean up test environment."""
        logger.info("🧹 Cleaning up test environment...")

        # Do final cleanup BEFORE closing client
        if self.client and self.db:
            try:
                await self._cleanup_test_data(quick=False) # Use comprehensive cleanup for suite-level
            except Exception as e:
                logger.warning(f"⚠️  Error during final cleanup: {e}")

        # Close client after cleanup
        if self.client:
            try:
                await self.client.aclose()
                logger.info("✅ API client closed")
            except Exception as e:
                logger.warning(f"⚠️  Error closing API client: {e}")

        # Then handle database cleanup
        if self.db:
            try:
                # Ensure all transactions are committed/rolled back
                try:
                    await self.db.commit()
                except Exception:
                    try:
                        await self.db.rollback()
                    except Exception:
                        pass  # Ignore rollback errors during cleanup

                # Close the database session properly
                await self.db.close()

                # Give a small delay to allow connection pool cleanup
                await asyncio.sleep(0.1)

                logger.info("✅ Database connection closed")

            except Exception as e:
                logger.warning(f"⚠️  Error during database cleanup: {e}")
                # Try to close the connection anyway, but ignore errors
                try:
                    await self.db.close()
                    await asyncio.sleep(0.1)  # Small delay even on error
                except Exception:
                    pass  # Ignore close errors during cleanup

        # Force garbage collection to clean up any remaining connections
        import gc
        gc.collect()

        # Additional delay to allow async cleanup to complete
        await asyncio.sleep(0.2)

        # Run FastAPI lifespan shutdown
        try:
            if self._lifespan_cm:
                await self._lifespan_cm.__aexit__(None, None, None)
                logger.info("✅ App lifespan shutdown executed")
        except Exception as e:
            logger.warning(f"Lifespan shutdown error (continuing): {e}")

        # Clean up file logging last
        self.cleanup_file_logging()

        logger.info("✅ Teardown complete")


# Convenience function for running tests
async def run_integration_tests(tests: List[Callable], enable_file_logging: bool = False, wipe_log_file: bool = False) -> bool:
    """Run integration tests and return success status."""
    runner = IntegrationTestRunner(enable_file_logging=enable_file_logging)

    try:
        await runner.setup(wipe_log_file=wipe_log_file)
        await runner.run_test_suite(tests)
        success = runner.print_summary()
        return success
    finally:
        await runner.teardown()


def filter_tests_by_name(all_tests: List[Callable], test_names: List[str]) -> List[Callable]:
    """Filter tests by their function names."""
    if not test_names:
        return all_tests

    # Create a mapping of test names to functions
    test_map = {test.__name__: test for test in all_tests}

    # Filter tests based on provided names
    filtered_tests = []
    for name in test_names:
        if name in test_map:
            filtered_tests.append(test_map[name])
        else:
            logger.warning(f"Test '{name}' not found. Available tests: {list(test_map.keys())}")

    return filtered_tests


def filter_tests_by_pattern(all_tests: List[Callable], pattern: str) -> List[Callable]:
    """Filter tests by pattern matching (e.g., 'create' matches all tests with 'create' in name)."""
    import re

    if not pattern:
        return all_tests

    regex = re.compile(pattern, re.IGNORECASE)
    return [test for test in all_tests if regex.search(test.__name__)]


async def run_integration_test_suite(
    all_tests: List[Callable],
    test_names: List[str] = None,
    pattern: str = None,
    list_tests: bool = False,
    enable_file_logging: bool = False,
    wipe_log_file: bool = False
) -> bool:
    """
    Run integration test suite with filtering options.

    Args:
        all_tests: List of all available test functions
        test_names: List of specific test names to run
        pattern: Pattern to match test names (regex)
        list_tests: If True, just list available tests and exit
        enable_file_logging: If True, write test output to tests/testing.log
        wipe_log_file: If True, wipe the log file before starting (only for first suite)

    Returns:
        bool: True if all tests passed, False otherwise
    """
    if list_tests:
        print("📋 Available Tests:")
        for i, test in enumerate(all_tests, 1):
            print(f"  {i:2d}. {test.__name__}")
        return True

    # Filter tests based on criteria
    if test_names:
        tests_to_run = filter_tests_by_name(all_tests, test_names)
    elif pattern:
        tests_to_run = filter_tests_by_pattern(all_tests, pattern)
    else:
        tests_to_run = all_tests

    if not tests_to_run:
        logger.error("No tests selected to run!")
        return False

    logger.info(f"Running {len(tests_to_run)} out of {len(all_tests)} available tests")

    return await run_integration_tests(tests_to_run, enable_file_logging=enable_file_logging, wipe_log_file=wipe_log_file)


if __name__ == "__main__":
    # Example usage
    async def example_test(client, db, auth_headers):
        """Example test function."""
        response = await client.get("/api/v1/health", headers=auth_headers)
        assert response.status_code == 200
    
    # Run the example
    asyncio.run(run_integration_tests([example_test]))
