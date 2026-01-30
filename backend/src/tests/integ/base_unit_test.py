"""
Base Unit Test Framework for Shu

This module provides a reusable base class for creating unit test suites
that don't require database or API setup.
"""

import argparse
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime


class TestResult:
    """Represents the result of a single test."""

    def __init__(self, name: str, passed: bool, error: str = None, duration: float = 0.0):
        self.name = name
        self.passed = passed
        self.error = error
        self.duration = duration


class BaseUnitTestSuite(ABC):
    """
    Abstract base class for unit test suites.

    Unit tests don't require database or API setup - they test business logic,
    data validation, and response formatting in isolation.
    """

    def __init__(self):
        self.test_results: list[TestResult] = []

    @abstractmethod
    def get_test_functions(self) -> list[Callable]:
        """Return a list of test functions to run."""
        pass

    @abstractmethod
    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        pass

    @abstractmethod
    def get_suite_description(self) -> str:
        """Return a description of this test suite."""
        pass

    def get_cli_examples(self) -> str:
        """Return CLI usage examples specific to this test suite."""
        return """
Examples:
  python tests/test_your_unit.py                    # Run all unit tests
  python tests/test_your_unit.py --list             # List available tests
  python tests/test_your_unit.py --test test_name   # Run specific test
  python tests/test_your_unit.py --pattern "valid"  # Run tests matching pattern
        """

    def run_test(self, test_func: Callable) -> TestResult:
        """Run a single unit test."""
        test_name = test_func.__name__
        start_time = time.time()

        try:
            # Run the test function (synchronous for unit tests)
            test_func()

            duration = time.time() - start_time
            result = TestResult(test_name, True, duration=duration)

        except Exception as e:
            duration = time.time() - start_time
            error_msg = f"{type(e).__name__}: {e!s}"
            result = TestResult(test_name, False, error_msg, duration)

        self.test_results.append(result)
        return result

    def run_test_suite(self, tests: list[Callable]) -> dict:
        """Run a suite of unit tests."""
        print()  # Add blank line for better separation

        start_time = datetime.now()

        for i, test_func in enumerate(tests, 1):
            # Clear, prominent test start message
            test_name = test_func.__name__
            print(f"📋 TEST {i}/{len(tests)}: {test_name}")

            result = self.run_test(test_func)

            # Clear, prominent result message
            if result.passed:
                print(f"✅ PASS: {test_name} ({result.duration:.3f}s)")
            else:
                print(f"❌ FAIL: {test_name} ({result.duration:.3f}s)")
                print(f"   Error: {result.error}")

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
            "pass_rate": len(passed_tests) / len(self.test_results) * 100 if self.test_results else 0,
        }

        return results

    def print_summary(self) -> bool:
        """Print test results summary and return success status."""
        if not self.test_results:
            print("No tests were run")
            return True

        print("\n" + "=" * 80)
        print("🧪 UNIT TEST RESULTS")
        print("=" * 80)

        # Print individual test results
        for result in self.test_results:
            status = "✅ PASS" if result.passed else "❌ FAIL"
            print(f"{status} {result.name} ({result.duration:.3f}s)")
            if not result.passed:
                print(f"     Error: {result.error}")

        # Print summary
        passed = len([r for r in self.test_results if r.passed])
        total = len(self.test_results)
        pass_rate = (passed / total * 100) if total > 0 else 0
        total_time = sum(r.duration for r in self.test_results)

        print("-" * 80)
        print(f"📊 SUMMARY: {passed}/{total} tests passed ({pass_rate:.1f}%)")
        print(f"⏱️  Total time: {total_time:.3f}s")

        if passed == total:
            print("🎉 All tests passed!")
            return True
        print(f"💥 {total - passed} test(s) failed!")
        return False

    def run(self) -> int:
        """
        Main entry point for running the test suite.
        Returns exit code (0 for success, 1 for failure).
        """
        parser = argparse.ArgumentParser(
            description=f"{self.get_suite_name()}: {self.get_suite_description()}",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=self.get_cli_examples(),
        )

        parser.add_argument("--test", nargs="+", help="Run specific test(s) by name")
        parser.add_argument("--pattern", help="Run tests matching regex pattern")
        parser.add_argument("--list", action="store_true", help="List available tests")

        args = parser.parse_args()

        # Get all test functions
        all_tests = self.get_test_functions()

        if args.list:
            print(f"📋 Available Tests in {self.get_suite_name()}:")
            for i, test in enumerate(all_tests, 1):
                print(f"  {i:2d}. {test.__name__}")
            return 0

        # Filter tests based on arguments
        if args.test:
            test_map = {test.__name__: test for test in all_tests}
            tests_to_run = []
            for name in args.test:
                if name in test_map:
                    tests_to_run.append(test_map[name])
                else:
                    print(f"❌ Test '{name}' not found. Available tests: {list(test_map.keys())}")
                    return 1
        elif args.pattern:
            import re

            regex = re.compile(args.pattern, re.IGNORECASE)
            tests_to_run = [test for test in all_tests if regex.search(test.__name__)]
            if not tests_to_run:
                print(f"❌ No tests match pattern '{args.pattern}'")
                return 1
        else:
            tests_to_run = all_tests

        if not tests_to_run:
            print("❌ No tests selected to run!")
            return 1

        print(f"🚀 {self.get_suite_name()}")
        print(f"Running {len(tests_to_run)} out of {len(all_tests)} available tests")

        # Run the tests
        self.run_test_suite(tests_to_run)
        success = self.print_summary()

        if success:
            print(f"\n🎉 All {self.get_suite_name().lower()} tests passed!")
            return 0
        return 1
