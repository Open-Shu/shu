# Shu Testing System

**Framework**: Custom Integration Test Framework
**Last Updated**: 2025-08-26

> **Non-negotiable**: Do **not** add standalone `pytest` modules or unit-test harnesses. Extend the custom integration framework (existing async suites under `tests/`) or add inline verification scripts that respect this design. Any new pytest file will be rejected during review.

## Executive Summary

Shu uses a **custom integration test framework** that addresses async event loop conflicts from pytest-asyncio. The framework is currently functional for the covered integration testing scenarios.

Known Issues (testing system):
- No dedicated security vulnerability testing suite; limited edge-case coverage
- Negative tests rely on developers explicitly logging expected errors
- Some suite docs list outdated test counts; use `python -m tests.integ.run_all_integration_tests --list-suites` as the source of truth

### **Capabilities**
- Functional reliability for implemented scenarios
- Performance: sub-200ms per suite typical
- Integration testing: API + database workflows for happy path scenarios, envelope-aware assertions required
- Extensible architecture: framework supports adding new test suites
- CLI interface: individual tests, patterns, suites, discovery functionality

### **Known Limitations**
- No security vulnerability testing; limited edge case coverage
- Tests must log expected error output for negative cases (see below)
- All tests must validate response envelopes (data or error)
- Standardize payload extraction with tests.response_utils.extract_data()

## Testing Strategy: Production Bug Prevention

### **Core Mission: Discover Real Use-Case Bugs Before UAT**

Our testing strategy is built around one fundamental principle: **Catch actual bugs that users would encounter before they reach UAT or production.**

#### **Critical Production Bugs We Must Catch**

**1. Data Persistence Failures** (Severity: CRITICAL)
- **Example**: RAG configuration updates that appear successful but don't actually save
- **Real Bug Found**: `search_threshold: 0.8` update returns success but persists as `0.7`
- **Test Pattern**: Always verify data retrieval after creation/update operations

**2. API Response Inconsistencies** (Severity: HIGH)
- **Example**: Some endpoints return `{"data": {...}}` while others return direct objects
- **Real Bug Found**: Chat API missing `llm_provider_id` field in model configuration responses
- **Test Pattern**: Validate response structure and handle format variations

**3. Complex Relationship Loading Failures** (Severity: HIGH)
- **Example**: Model configurations not loading associated knowledge bases
- **Test Pattern**: Verify all expected relationships are properly loaded and accessible


### Positive vs Negative Tests Policy (REQUIRED)
- Positive tests must assert that real functionality succeeds with the required configuration. Do not pass tests by treating real failures as “expected failures”.
- If a positive test depends on external configuration (e.g., GOOGLE_SERVICE_ACCOUNT_JSON path, domain-wide delegation, API keys), the test must:
  - Fail with a clear setup error message if configuration is missing or invalid.
  - Never downshift to negative-test logging to mask a setup failure.
- Negative tests are only for intentionally failing scenarios and must use the “EXPECTED TEST OUTPUT” logging pattern defined below.
- Do not blur the two categories. Never use negative test logging to allow a positive test to pass when functionality or configuration is broken.
- For optional, environment-dependent features: either (a) mark the test as explicitly skipped with a clear reason, or (b) require the configuration as part of the standard development environment. Do not silently degrade assertions.

### **Negative Test Logging Requirements** (CRITICAL)

When writing tests that expect warnings, errors, or exceptions (negative tests), **you MUST log the expected test output** to distinguish between expected behavior and actual bugs.

#### **Why This Matters**
- **Prevents Bug Overlook**: Without logging expected output, we might miss real bugs
- **Clear Test Intent**: Makes it obvious when warnings/errors are expected vs. unexpected
- **Debugging Clarity**: Helps developers understand test behavior during investigation
- **CI/CD Reliability**: Prevents false alarms in automated testing

#### **Required Pattern for Negative Tests**
```python
async def test_authentication_failure_expected(client, db, auth_headers):
    """Test that unauthenticated requests properly fail with 401."""

    # Log expected behavior BEFORE the test
    logger.info("=== EXPECTED TEST OUTPUT: The following 401 authentication errors are expected ===")

    # Perform the test that should fail
    response = await client.get("/api/v1/protected-endpoint")
    assert response.status_code == 401

    # Log confirmation of expected behavior
    logger.info("=== EXPECTED TEST OUTPUT: 401 error for unauthenticated request occurred as expected ===")
```

#### **Examples of Required Logging**

**1. Authentication Error Tests**
```python
async def test_invalid_token_rejection(client, db, auth_headers):
    """Test that invalid tokens are properly rejected."""

    logger.info("=== EXPECTED TEST OUTPUT: The following 401 authentication error is expected ===")

    # Test with invalid token
    invalid_headers = {"Authorization": "Bearer invalid_token"}
    response = await client.get("/api/v1/protected-endpoint", headers=invalid_headers)
    assert response.status_code == 401

    logger.info("=== EXPECTED TEST OUTPUT: 401 error for invalid token occurred as expected ===")
```

**2. Validation Error Tests**
```python
async def test_invalid_data_validation(client, db, auth_headers):
    """Test that invalid data is properly rejected."""

    logger.info("=== EXPECTED TEST OUTPUT: The following 422 validation errors are expected ===")

    # Test with invalid data
    invalid_data = {"name": ""}  # Missing required field
    response = await client.post("/api/v1/endpoint", json=invalid_data, headers=auth_headers)
    assert response.status_code == 422
    assert "error" in response.json()

    logger.info("=== EXPECTED TEST OUTPUT: 422 validation error occurred as expected ===")
```

**3. Cleanup Operation Tests**
```python
async def test_cleanup_operations(client, db, auth_headers):
    """Test cleanup operations that may generate warnings."""

    logger.info("=== EXPECTED TEST OUTPUT: Authentication warnings may occur during cleanup operations ===")

    # Perform cleanup that might generate warnings
    await cleanup_test_data()

    logger.info("=== EXPECTED TEST OUTPUT: Any authentication warnings above were expected during cleanup ===")
```

#### **Framework Integration**
The test framework automatically wraps test suites with expected error context:

```python
# In test suites, the framework provides:
with expect_test_suite_errors():
    # Your tests here
    # Framework automatically logs expected error patterns
```

#### **Logging Best Practices**
1. **Log BEFORE the test**: Set expectations before the operation
2. **Log AFTER the test**: Confirm expected behavior occurred
3. **Be specific**: Describe exactly what warnings/errors are expected
4. **Use consistent format**: `=== EXPECTED TEST OUTPUT: description ===`
5. **Include context**: Explain why the behavior is expected

#### **Common Expected Error Patterns**
- **Authentication failures**: 401 errors for invalid/missing tokens
- **Validation errors**: 422 errors for invalid data
- **Permission errors**: 403 errors for insufficient permissions
- **Cleanup warnings**: Authentication warnings during test cleanup
- **Background job errors**: Race conditions during test cleanup
- **Database rollback errors**: Expected during cleanup operations

**Remember**: If you don't log expected output, you might be overlooking a real bug!

## Current Test Status (snapshot)

> The table and metrics below are a snapshot from the last documented run. For the current source of truth, use `python -m tests.integ.run_all_integration_tests --list-suites` and check actual output.

### Active Test Suites — Functional Testing (snapshot)

| Test Suite | Tests | Coverage | Performance | Status |
|------------|-------|----------|-------------|---------|
| **LLM Provider** | 10 | CRUD, basic validation | 0.092s | Functional |
| **Authentication** | 10 | Basic auth, JWT | 0.066s | Functional |
| **RBAC** | 11 | Basic access control | 0.223s | Functional |
| **Configuration** | 11 | Config endpoints | 0.023s | Functional |
| **Chat Integration** | 10 | Conversations, messages | 0.331s | Functional |
| **Knowledge Sources** | 8 | Gmail/Chat sources | 0.118s | Partial |

**Security Testing Status**: Missing — No tests for timing attacks, RBAC bypass, malicious input, or file processing vulnerabilities.

### Overall Metrics (snapshot)
- **Total Tests**: 76 (60 integration + 16 unit) at last documented run
- **Pass Rate**: 100% (76/76 passing) at last documented run
- **Total Execution Time**: <1.0 seconds for all tests at last documented run
- **Reliability**: Tests run consistently with proper cleanup (per last documented run)
- **Coverage**: Core application workflows for happy path scenarios
- **Security Coverage**: Missing — No dedicated security vulnerability testing
- **Migration Status**: Pytest removal completed
- **Cleanup System**: See tests/README.md for the current cleanup behavior and limitations.

### **Test Coverage Areas**
- LLM Provider Management: CRUD operations, API key configuration, validation (covered by current suites).
- Authentication & Authorization: JWT tokens, RBAC, session management (covered by current suites).
- Role-Based Access Control: Endpoint protection, permission enforcement (covered by current suites).
- Configuration Management: Public config, basic security validation, performance checks (covered by current suites).
- Chat Integration: Conversations, messages, streaming, session management (covered by current suites).
- Knowledge Sources: Gmail/Google Chat integration, source configuration (partially covered; more tests needed).
- API Security: Authentication requirements, malformed request handling (covered by current suites).
- Database Operations: Real data persistence, transaction integrity (covered by current suites).

## Quick Start

### Standardized response unwrapping (Required)

All API endpoints return a standardized envelope. Tests should use the helper to unwrap payloads, and only directly assert the envelope when the test’s purpose is to validate structure.

- Helper: from tests.response_utils import extract_data
- Usage patterns:

````python
# After asserting status code
payload = extract_data(response)

# Access nested items
item_id = payload["id"]
items = payload.get("items", [])

# If you need to assert the envelope itself (rare)
full = response.json()
assert "data" in full or "error" in full
````

Anti-patterns to remove in tests:
- response.json()["data"]["id"]
- create_response.json()["data"]
- conv_response.json()["data"]

Edge cases:
- Some legacy endpoints may still return bare objects; extract_data returns the object as-is when no data key is present.

### Suite registration requirement

- Every integration test file must define a subclass of BaseIntegrationTestSuite and implement get_test_functions().
- The master runner auto-discovers files matching test_*_integration.py and registers their suites. If a file is not named with that pattern (e.g., test_chat_production_scenarios.py), it will NOT be discovered. Either rename to test_*_integration.py or add a secondary loader for outliers.
- Our standard is to adhere to the filename pattern. Non-conforming files should be renamed or refactored to fit the framework.

### **Run All Tests**
```bash
python -m tests.integ.run_all_integration_tests
```

### **Run Specific Test Suite**
```bash
python -m tests.integ.test_llm_integration
```

### **Run Individual Tests**
```bash
python -m tests.integ.test_llm_integration --test test_create_provider_success
```

### **Run with File Logging**
```bash
python -m tests.integ.run_all_integration_tests --suite auth --log
python -m tests.integ.run_all_integration_tests --suite auth --suite config --log
python -m tests.integ.run_all_integration_tests --log --cleanup
```

### **List Available Tests**
```bash
python -m tests.integ.test_llm_integration --list
python -m tests.integ.run_all_integration_tests --list-suites
```

## System Architecture

### **Core Framework Components**
```
tests/
├── integration_test_runner.py         # Core test engine (single event loop)
├── base_integration_test.py           # Abstract base class for test suites
├── run_all_integration_tests.py       # Master test runner with auto-discovery
├── test_template.py                   # Copy-paste template for new suites
├── test_llm_integration.py           # LLM provider tests
├── test_auth_integration.py          # Authentication tests
├── test_rbac_integration.py          # RBAC tests
├── test_config_integration.py        # Configuration tests
└── README.md                         # Complete technical documentation
```

## Migration Status

### Migration Summary

The migration away from pytest to the custom integration/unit framework described in this document has been completed for the files listed below. This section describes the state at the time of migration; any new pytest usage introduced later should be documented separately.

#### Successfully Migrated and Deleted
- **LLM Provider Tests** – `test_llm_providers.py` → `test_llm_integration.py` (original pytest file deleted)
- **Authentication Tests** – `test_authentication.py` → `test_auth_integration.py` (original pytest file deleted)
- **RBAC Tests** – `test_rbac_enforcement.py` → `test_rbac_integration.py` (original pytest file deleted)
- **Configuration Tests** – `test_configuration.py` → `test_config_integration.py` (original pytest file deleted)
- **LLM Unit Tests** – `test_llm_unit.py` → `test_llm_unit_migrated.py` (original pytest file deleted)
- **API Key Tests** – `test_api_key_*.py` → `test_api_key_unit.py` (original pytest files deleted)

#### pytest Infrastructure Removed
- **Configuration Files** – `pytest.ini`, `tests/pytest.ini`, `tests/conftest.py` (removed from the repo)
- **Dependencies** – pytest packages removed from `requirements.txt` at the time of migration
- **Cache Directories** – `.pytest_cache` directories removed
- **Complex Test Files** – Remaining pytest-based test files removed

#### Framework Implementation State
- **Custom Integration Framework** – Implemented with auto-discovery (`run_all_integration_tests.py` and `BaseIntegrationTestSuite`)
- **Custom Unit Test Framework** – Implemented for business logic tests
- **Master Test Runner** – Provides a unified interface for all test types
- **Documentation** – This file and `tests/README.md` describe the framework for developers and AI assistants

**Migration Result (snapshot)**: At the time this section was last edited, all known pytest infrastructure had been removed. Re-run a repository-wide search for `pytest` if you suspect new usage has been introduced.

## Benefits Over Previous System

These notes describe why the custom integration framework was introduced and what it is currently expected to provide compared to the previous pytest-asyncio setup. Treat them as design and observation notes that should be updated when reality diverges.

### Technical Characteristics
- **Async event loop control** – Single event loop avoids the pytest-asyncio conflicts we previously saw.
- **Real database testing** – Uses PostgreSQL with the same connection stack the app uses.
- **Execution speed (snapshot)** – At the last documented run, all tests completed in under 1 second (see metrics above). This will change as more suites are added.
- **Debugging support** – Error messages include suite names and there is optional file logging.
- **Suite extensibility** – New suites plug into `BaseIntegrationTestSuite` and the shared runner.
- **Test data cleanup** – The runner and suites are responsible for cleaning up data they create; see individual suites for details and limitations.

### Developer Experience
- **CI/CD friendliness** – Removing pytest-asyncio and centralising the event loop has eliminated the specific infra flakes we saw before; new flakes are still possible and should be documented when they occur.
- **Fast feedback (snapshot)** – Current suite size keeps local runs short; if timings grow, update the metrics and expectations above.
- **Test authoring** – New tests follow a template and explicit registration instead of implicit fixtures.
- **Coverage focus** – Current suites focus on happy-path workflows; security and negative-path coverage is still missing (see gaps above).
- **Logging** – Structured logging and optional file logging are available when suites opt in.

## Documentation

- **Technical Guide**: `tests/README.md` – Current framework documentation and usage guide.
- **Task Tracking**: Use Jira (canonical source of task state) and `docs/SHU_TECHNICAL_ROADMAP.md` for test-related roadmap items; there is no separate local task index file for testing.

## Conclusion

The Shu custom integration test framework exists to replace the previous pytest-asyncio setup with a single-loop, integration-focused runner. It currently:

- Runs integration tests against the application stack and database rather than heavy mocking.
- Avoids the specific async event loop issues we previously saw with pytest-asyncio.
- Provides a straightforward pattern for adding new suites.

All pytest files have been removed. Dedicated security vulnerability testing still needs to be added before any production deployment.
