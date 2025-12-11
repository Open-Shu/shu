"""
Base Integration Test Framework for Shu

This module provides a reusable base class for creating integration test suites
across different modules of the Shu application.
"""

import asyncio
import argparse
import sys
import os
from typing import List, Callable
from abc import ABC, abstractmethod

from integ.integration_test_runner import run_integration_test_suite


class BaseIntegrationTestSuite(ABC):
    """
    Base class for creating integration test suites.
    
    Subclasses should implement:
    - get_test_functions(): Return list of test functions
    - get_suite_name(): Return descriptive name for the test suite
    - get_suite_description(): Return description for CLI help
    """
    
    @abstractmethod
    def get_test_functions(self) -> List[Callable]:
        """Return list of test functions for this suite."""
        pass
    
    @abstractmethod
    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        pass
    
    @abstractmethod
    def get_suite_description(self) -> str:
        """Return description of this test suite for CLI help."""
        pass
    
    def get_cli_examples(self) -> str:
        """Return CLI usage examples. Override to customize."""
        script_name = os.path.basename(sys.argv[0])
        return f"""
Examples:
  python {script_name}                           # Run all tests
  python {script_name} --list                    # List available tests
  python {script_name} --test test_function_name
  python {script_name} --test test_1 test_2     # Run multiple specific tests
  python {script_name} --pattern create          # Run tests matching 'create'
  python {script_name} --pattern "api|security"  # Run tests matching pattern
        """
    
    def create_argument_parser(self) -> argparse.ArgumentParser:
        """Create and configure argument parser."""
        parser = argparse.ArgumentParser(
            description=f"Shu {self.get_suite_name()} - {self.get_suite_description()}",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=self.get_cli_examples()
        )
        
        parser.add_argument(
            '--test', '-t',
            nargs='*',
            help='Run specific test(s) by name'
        )
        
        parser.add_argument(
            '--pattern', '-p',
            help='Run tests matching pattern (regex)'
        )
        
        parser.add_argument(
            '--list', '-l',
            action='store_true',
            help='List all available tests'
        )
        
        parser.add_argument(
            '--verbose', '-v',
            action='store_true',
            help='Enable verbose output'
        )
        
        return parser
    
    async def run_suite(self) -> int:
        """
        Run the integration test suite with CLI argument parsing.
        
        Returns:
            int: Exit code (0 for success, 1 for failure)
        """
        parser = self.create_argument_parser()
        args = parser.parse_args()
        
        print(f"🚀 Shu {self.get_suite_name()}")
        
        success = await run_integration_test_suite(
            all_tests=self.get_test_functions(),
            test_names=args.test,
            pattern=args.pattern,
            list_tests=args.list
        )
        
        if args.list:
            return 0
        
        if success:
            print(f"\n🎉 All {self.get_suite_name().lower()} tests passed!")
            return 0
        else:
            print(f"\n❌ Some {self.get_suite_name().lower()} tests failed!")
            return 1
    
    def run(self) -> int:
        """
        Convenience method to run the test suite.
        
        Returns:
            int: Exit code (0 for success, 1 for failure)
        """
        return asyncio.run(self.run_suite())


def create_test_runner_script(test_suite_class, script_globals):
    """
    Helper function to create a standard test runner script.
    
    Usage in your test file:
    
    ```python
    class MyTestSuite(BaseIntegrationTestSuite):
        def get_test_functions(self):
            return [test_func1, test_func2, ...]
        
        def get_suite_name(self):
            return "My Feature Tests"
        
        def get_suite_description(self):
            return "Integration tests for My Feature functionality"
    
    # At the bottom of your test file:
    if __name__ == "__main__":
        from integ.base_integration_test import create_test_runner_script
        create_test_runner_script(MyTestSuite, globals())
    ```
    """
    if script_globals.get('__name__') == '__main__':
        suite = test_suite_class()
        exit_code = suite.run()
        sys.exit(exit_code)


# Example usage and template
class ExampleTestSuite(BaseIntegrationTestSuite):
    """Example test suite showing how to use the base class."""
    
    def get_test_functions(self) -> List[Callable]:
        # Return your test functions here
        return []
    
    def get_suite_name(self) -> str:
        return "Example Tests"
    
    def get_suite_description(self) -> str:
        return "Example integration tests showing framework usage"


if __name__ == "__main__":
    # Example of how to run a test suite
    suite = ExampleTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
