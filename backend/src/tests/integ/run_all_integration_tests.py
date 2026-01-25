"""
Master test runner for all Shu test suites.

This script discovers and runs all test suites (integration and unit) in the tests directory.
It provides a unified interface for running all tests or specific test suites.
"""

# =============================================================================
# TEST ENVIRONMENT CONFIGURATION
# Must be set BEFORE any shu imports to ensure settings pick up test overrides
# =============================================================================
import os

# Disable rate limiting for test runs - the test suite makes many API calls
# and rate limiting causes cascade failures unrelated to actual test logic
os.environ["SHU_ENABLE_RATE_LIMITING"] = "false"

# =============================================================================

import sys
import asyncio
import importlib
import importlib.util
import glob
from typing import List, Dict, Any
import argparse

from integ.base_integration_test import BaseIntegrationTestSuite


class MasterTestRunner:
    """Discovers and runs all integration test suites."""

    def __init__(self):
        self.test_suites: Dict[str, BaseIntegrationTestSuite] = {}
        self.first_suite_run = True  # Track if this is the first suite to run
        self.discover_test_suites()
    
    def discover_test_suites(self):
        """Discover all integration test suites in the tests directory."""
        test_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Find test files: *_integration.py plus scenarios/workflows outliers
        patterns = [
            os.path.join(test_dir, "test_*_integration.py"),
            os.path.join(test_dir, "test_*_scenarios.py"),
            os.path.join(test_dir, "test_*_workflows.py"),
            os.path.join(test_dir, "test_chat_production_scenarios.py"),
        ]
        test_files = []
        for pattern in patterns:
            test_files.extend(glob.glob(pattern))
        # Deduplicate
        test_files = sorted(set(test_files))

        for test_file in test_files:
            try:
                # Extract module name from file path
                module_name = os.path.basename(test_file)[:-3]  # Remove .py extension
                
                # Import the module (guard against None spec)
                spec = importlib.util.spec_from_file_location(module_name, test_file)
                if spec is None or spec.loader is None:
                    print(f"⚠️  Warning: Could not load spec for {test_file}")
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Look for test suite classes
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type) and 
                        issubclass(attr, BaseIntegrationTestSuite) and 
                        attr != BaseIntegrationTestSuite):
                        
                        # Create instance of test suite
                        suite_instance = attr()
                        # Derive suite key by removing 'test_' prefix and known suffixes
                        suite_key = module_name
                        if suite_key.startswith('test_'):
                            suite_key = suite_key[len('test_'):]
                        for suffix in ('_integration', '_scenarios', '_workflows'):
                            if suite_key.endswith(suffix):
                                suite_key = suite_key[: -len(suffix)]
                        self.test_suites[suite_key] = suite_instance
                        print(f"📋 Discovered test suite: {suite_key} ({suite_instance.get_suite_name()})")

            except Exception as e:
                print(f"⚠️  Warning: Could not load test suite from {test_file}: {e}")
    
    def list_test_suites(self):
        """List all discovered test suites."""
        print("🧪 Available Integration Test Suites:")
        print("=" * 60)
        
        for i, (key, suite) in enumerate(self.test_suites.items(), 1):
            print(f"{i:2d}. {key}")
            print(f"    Name: {suite.get_suite_name()}")
            print(f"    Description: {suite.get_suite_description()}")
            print(f"    Tests: {len(suite.get_test_functions())}")
            print()
    
    async def run_suite(self, suite_key: str, **kwargs) -> bool:
        """Run a specific test suite."""
        if suite_key not in self.test_suites:
            print(f"❌ Test suite '{suite_key}' not found!")
            return False

        suite = self.test_suites[suite_key]
        print(f"🚀 Running {suite.get_suite_name()}")

        # Import the test runner function
        from integ.integration_test_runner import run_integration_test_suite

        # Only wipe log file for the first suite run
        if self.first_suite_run and kwargs.get('enable_file_logging', False):
            kwargs['wipe_log_file'] = True
            self.first_suite_run = False

        success = await run_integration_test_suite(
            all_tests=suite.get_test_functions(),
            **kwargs
        )

        return success
    
    async def run_all_suites(self, **kwargs) -> Dict[str, bool]:
        """Run all discovered test suites."""
        results = {}
        
        print(f"🎯 Running all {len(self.test_suites)} integration test suites")
        print("=" * 80)
        
        for suite_key in self.test_suites.keys():
            print(f"\n{'='*20} {suite_key.upper()} {'='*20}")
            success = await self.run_suite(suite_key, **kwargs)
            results[suite_key] = success
        
        return results
    
    def print_summary(self, results: Dict[str, bool]):
        """Print summary of all test results."""
        print("\n" + "="*80)
        print("📊 INTEGRATION TEST SUITE SUMMARY")
        print("="*80)
        
        total_suites = len(results)
        passed_suites = sum(1 for success in results.values() if success)
        failed_suites = total_suites - passed_suites
        
        for suite_key, success in results.items():
            status = "✅ PASS" if success else "❌ FAIL"
            suite_name = self.test_suites[suite_key].get_suite_name()
            print(f"{status} {suite_key} - {suite_name}")
        
        print("-" * 80)
        print(f"📈 OVERALL RESULTS: {passed_suites}/{total_suites} test suites passed")
        
        if failed_suites == 0:
            print("🎉 ALL INTEGRATION TEST SUITES PASSED!")
            return True
        else:
            print(f"❌ {failed_suites} test suite(s) failed")
            return False


async def main():
    """Main entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        description="Shu Master Integration Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m tests.integ.run_all_integration_tests                     # Run all test suites
  python -m tests.integ.run_all_integration_tests --list-suites       # List available test suites
  python -m tests.integ.run_all_integration_tests --suite llm         # Run specific test suite
  python -m tests.integ.run_all_integration_tests --suite auth --suite llm  # Run multiple test suites
  python -m tests.integ.run_all_integration_tests --suite llm --test test_create_provider_success
  python -m tests.integ.run_all_integration_tests --suite llm --pattern create
  python -m tests.integ.run_all_integration_tests --suite auth --log  # Run with file logging
        """
    )
    
    parser.add_argument(
        '--list-suites',
        action='store_true',
        help='List all available test suites'
    )
    
    parser.add_argument(
        '--suite', '-s',
        action='append',
        help='Run specific test suite(s) by key. Can be used multiple times (e.g., --suite auth --suite llm)'
    )
    
    parser.add_argument(
        '--test', '-t',
        nargs='*',
        help='Run specific test(s) by name (requires --suite)'
    )
    
    parser.add_argument(
        '--pattern', '-p',
        help='Run tests matching pattern (requires --suite)'
    )
    
    parser.add_argument(
        '--list', '-l',
        action='store_true',
        help='List tests in specified suite (requires --suite)'
    )

    parser.add_argument(
        '--cleanup',
        action='store_true',
        help='Clean up test data after running tests'
    )

    parser.add_argument(
        '--cleanup-only',
        action='store_true',
        help='Only run cleanup, don\'t run tests'
    )

    parser.add_argument(
        '--log',
        action='store_true',
        help='Write test output to tests/testing.log file'
    )

    args = parser.parse_args()

    runner = MasterTestRunner()

    if args.list_suites:
        runner.list_test_suites()
        return 0

    if args.cleanup_only:
        # Only run cleanup
        from integ.test_data_cleanup import cleanup_test_data_main
        await cleanup_test_data_main()
        return 0
    
    if args.suite:
        # Run specific test suite(s)
        kwargs = {}
        if args.test:
            kwargs['test_names'] = args.test
        if args.pattern:
            kwargs['pattern'] = args.pattern
        if args.list:
            kwargs['list_tests'] = True
        if args.log:
            kwargs['enable_file_logging'] = True

        # Handle multiple suites
        suites_to_run = args.suite
        all_success = True

        print(f"🎯 Running {len(suites_to_run)} test suite(s): {', '.join(suites_to_run)}")
        print("=" * 80)

        for i, suite_name in enumerate(suites_to_run):
            print(f"\n==================== {suite_name.upper()} ({i+1}/{len(suites_to_run)}) ====================")
            success = await runner.run_suite(suite_name, **kwargs)
            if not success:
                all_success = False
                print(f"❌ Suite '{suite_name}' failed")
            else:
                print(f"✅ Suite '{suite_name}' passed")

        # Run cleanup if requested
        if args.cleanup and all_success:
            print("\n🧹 Running post-test cleanup...")
            from integ.test_data_cleanup import cleanup_test_data_main
            await cleanup_test_data_main()

        return 0 if all_success else 1

    else:
        # Run all test suites
        kwargs = {}
        if args.log:
            kwargs['enable_file_logging'] = True

        results = await runner.run_all_suites(**kwargs)
        success = runner.print_summary(results)

        # Run cleanup if requested
        if args.cleanup and success:
            print("\n🧹 Running post-test cleanup...")
            from integ.test_data_cleanup import cleanup_test_data_main
            await cleanup_test_data_main()

        return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
