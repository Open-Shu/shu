#!/usr/bin/env python3
"""
Script to automatically add expected error logging to integration tests.

This script analyzes test files and adds appropriate expected error context
to tests that are likely to generate errors, warnings, or exceptions.
"""

import re
from pathlib import Path


class TestErrorLoggerUpdater:
    """Updates test files to include expected error logging."""

    # Test patterns that typically generate specific types of errors
    TEST_PATTERNS = {
        "authentication": [
            r"test.*auth.*invalid",
            r"test.*unauthorized",
            r"test.*without.*auth",
            r"test.*missing.*auth",
            r"test.*invalid.*token",
        ],
        "validation": [
            r"test.*invalid.*data",
            r"test.*validation",
            r"test.*malformed",
            r"test.*empty.*data",
        ],
        "not_found": [
            r"test.*not.*found",
            r"test.*invalid.*id",
            r"test.*nonexistent",
            r"test.*fake.*id",
        ],
        "duplicate": [r"test.*duplicate", r"test.*already.*exists", r"test.*same.*name"],
        "llm_errors": [
            r"test.*send.*message",
            r"test.*llm",
            r"test.*chat.*completion",
            r"test.*streaming",
        ],
        "sync_errors": [r"test.*sync.*invalid", r"test.*sync.*error", r"test.*filesystem.*sync"],
    }

    def __init__(self, test_dir: str = "tests"):
        self.test_dir = Path(test_dir)
        self.updated_files: set[str] = set()

    def find_test_files(self) -> list[Path]:
        """Find all integration test files."""
        test_files = []
        for file_path in self.test_dir.glob("test_*_integration.py"):
            test_files.append(file_path)
        return test_files

    def analyze_test_function(self, func_name: str, func_content: str) -> list[str]:
        """
        Analyze a test function to determine what types of errors it might generate.

        Returns:
            List of error types this test might generate
        """
        error_types = []

        for error_type, patterns in self.TEST_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, func_name, re.IGNORECASE):
                    error_types.append(error_type)
                    break

        # Additional content-based analysis
        if "status_code.*[45][0-9][0-9]" in func_content:
            if "401" in func_content or "403" in func_content:
                if "authentication" not in error_types:
                    error_types.append("authentication")
            if "400" in func_content or "422" in func_content:
                if "validation" not in error_types:
                    error_types.append("validation")
            if "404" in func_content:
                if "not_found" not in error_types:
                    error_types.append("not_found")

        return error_types

    def has_expected_error_logging(self, func_content: str) -> bool:
        """Check if a function already has expected error logging."""
        return "EXPECTED TEST OUTPUT" in func_content or "expect_" in func_content

    def generate_error_context_imports(self, error_types: set[str]) -> str:
        """Generate the import statement for expected error contexts."""
        imports = []

        if "authentication" in error_types:
            imports.append("expect_authentication_errors")
        if "validation" in error_types:
            imports.append("expect_validation_errors")
        if "not_found" in error_types:
            imports.append("expect_not_found_errors")
        if "duplicate" in error_types:
            imports.append("expect_duplicate_errors")
        if "llm_errors" in error_types:
            imports.append("expect_llm_errors")
        if "sync_errors" in error_types:
            imports.append("expect_sync_errors")

        imports.append("ExpectedErrorContext")

        return f"""from integ.expected_error_context import (
    {',\n    '.join(imports)}
)"""

    def update_test_file(self, file_path: Path) -> bool:
        """
        Update a test file to include expected error logging.

        Returns:
            True if the file was updated, False otherwise
        """
        try:
            with open(file_path) as f:
                content = f.read()

            # Check if file already has expected error context imports
            if "from integ.expected_error_context import" in content:
                print(f"✅ {file_path.name} already has expected error context imports")
                return False

            # Find all test functions and analyze them
            test_functions = re.findall(
                r"async def (test_[^(]+)\([^)]*\):[^}]*?(?=\n\nasync def|\n\nclass|\Z)",
                content,
                re.DOTALL,
            )

            error_types_needed = set()
            functions_to_update = []

            for func_match in re.finditer(
                r"(async def (test_[^(]+)\([^)]*\):.*?)(?=\n\nasync def|\n\nclass|\Z)",
                content,
                re.DOTALL,
            ):
                func_content = func_match.group(1)
                func_name = func_match.group(2)

                if not self.has_expected_error_logging(func_content):
                    error_types = self.analyze_test_function(func_name, func_content)
                    if error_types:
                        error_types_needed.update(error_types)
                        functions_to_update.append((func_name, error_types))

            if not error_types_needed:
                print(f"ℹ️  {file_path.name} doesn't need expected error logging")
                return False

            # Add imports
            import_statement = self.generate_error_context_imports(error_types_needed)

            # Find where to insert imports (after existing imports)
            import_insertion_point = content.rfind("from integ.base_integration_test import")
            if import_insertion_point == -1:
                import_insertion_point = content.find("# Add project root to path")
                if import_insertion_point != -1:
                    import_insertion_point = content.find("\n", import_insertion_point)

            if import_insertion_point != -1:
                # Insert after the base integration test import
                next_line = content.find("\n", import_insertion_point) + 1
                content = content[:next_line] + import_statement + "\n" + content[next_line:]

                print(f"✅ Added expected error context imports to {file_path.name}")
                print(f"   Error types: {', '.join(error_types_needed)}")
                print(f"   Functions to update: {len(functions_to_update)}")

                # Write the updated content
                with open(file_path, "w") as f:
                    f.write(content)

                self.updated_files.add(str(file_path))
                return True
            print(f"❌ Could not find import insertion point in {file_path.name}")
            return False

        except Exception as e:
            print(f"❌ Error updating {file_path.name}: {e}")
            return False

    def run(self):
        """Run the updater on all test files."""
        test_files = self.find_test_files()
        print(f"Found {len(test_files)} integration test files")

        updated_count = 0
        for file_path in test_files:
            if self.update_test_file(file_path):
                updated_count += 1

        print("\n📊 Summary:")
        print(f"   Files analyzed: {len(test_files)}")
        print(f"   Files updated: {updated_count}")

        if self.updated_files:
            print("\n📝 Updated files:")
            for file_path in sorted(self.updated_files):
                print(f"   - {file_path}")


if __name__ == "__main__":
    updater = TestErrorLoggerUpdater()
    updater.run()
