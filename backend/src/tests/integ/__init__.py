"""
Shu Test Suite

This package contains comprehensive tests for Shu using a custom test framework
that provides reliable, fast integration and unit testing without async event loop conflicts.

Test Suites:
- Integration Tests: Real API + database testing
  - test_llm_integration: LLM provider CRUD operations and security
  - test_auth_integration: Authentication, JWT, and RBAC testing
  - test_rbac_integration: Role-based access control enforcement
  - test_config_integration: Configuration endpoint testing

- Unit Tests: Business logic and data validation
  - test_llm_unit_migrated: LLM business logic and validation
  - test_api_key_unit: API key display and security logic

Usage:
    # Go into the src directory
    cd backend/src

    # Run all integration tests
    python -m tests.integ.run_all_integration_tests

    # Run specific test suite
    python -m tests.integ.test_llm_integration
    python -m tests.integ.test_llm_unit_migrated

    # Run individual tests
    python -m tests.integ.test_llm_integration --test test_create_provider_success
"""

__version__ = "1.0.0"
__author__ = "Shu RAG Backend Team"

# Ensure project root (backend/src) is on sys.path once for all tests
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # backend/src
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
