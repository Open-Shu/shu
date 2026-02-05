# Shu Testing System

**Framework**: Custom Integration Test Framework + Pytest Unit Tests
**Last Updated**: 2025-12-17

## Executive Summary

Shu uses a **custom integration test framework** for API/database integration tests, and **pytest** for isolated unit tests. For foundational abstractions requiring correctness guarantees, we use **Hypothesis** for property-based testing. The integration framework addresses async event loop conflicts from pytest-asyncio. This approach allows fast unit testing without database dependencies while maintaining robust integration coverage.

## Unit Tests vs Integration Tests

| Aspect | Unit Tests | Integration Tests |
|--------|------------|-------------------|
| Framework | pytest | Custom async framework |
| Location | `backend/src/tests/unit/` | `backend/src/tests/integ/` |
| Database | No | Yes (PostgreSQL) |
| API Server | No | Yes |
| Speed | Fast (~ms per test) | Slower (~100ms per test) |
| Purpose | Test pure logic, models, utilities | Test API workflows end-to-end |
| Run Command | `python -m pytest backend/src/tests/unit` | `python -m tests.integ.run_all_integration_tests` |

**Frontend Unit Tests:**
- Framework: Jest (via react-scripts)
- Location: `frontend/src/components/__tests__/`
- Run Command: `cd frontend && npm test -- --watchAll=false`

### When to Use Unit Tests (pytest)

**The core question**: Will this unit test catch a bug that integration tests won't?

Unit tests add value when they test:
- **Error handling paths** that integration tests won't exercise (e.g., validation errors, edge cases)
- **Conditional branching logic** where integration tests only cover one path (e.g., "if profiling enabled" vs "if profiling disabled")
- **Complex pure functions** with many input combinations (calculations, transformations, parsing)
- **State synchronization** between fields that must stay consistent (real bug source)
- **Serialization round-trips** where data could be lost or corrupted

Unit tests do NOT add value when they:
- **Verify enum values exist** — if `Status.PENDING` is misspelled, the code using it fails immediately
- **Test default values** — covered by integration tests that create and retrieve objects
- **Duplicate happy-path behavior** — if integration tests exercise the same code path, the unit test is redundant
- **Test framework behavior** — don't test that SQLAlchemy defaults work or that Pydantic validates types

**Rule of thumb**: If deleting the unit test wouldn't reduce your confidence in the code (because integration tests cover it), delete the unit test.

Unit tests should NOT:
- Require a running database
- Require a running API server
- Test API endpoints directly
- Use async/await (use integration tests for async code)

### When to Use Integration Tests (custom framework)

Integration tests are appropriate for:
- **API endpoints**: Full request/response cycles
- **Database operations**: CRUD, transactions, relationships
- **Authentication flows**: JWT, sessions, RBAC
- **Async workflows**: Background jobs, event handling
- **End-to-end scenarios**: Multi-step user workflows

### Unit Test Guidelines

1. **Location**: Place tests in `backend/src/tests/unit/<module>/test_<name>.py`
2. **No fixtures required**: Unit tests should not need database or client fixtures
3. **Fast**: Each test should complete in milliseconds
4. **Isolated**: Tests should not depend on each other or external state
5. **Model imports**: Import models after `conftest.py` sets up the path and registers all SQLAlchemy models

Example unit test:
```python
# backend/src/tests/unit/models/test_document_models.py
from shu.models import Document, DocumentChunk

class TestDocument:
    def test_is_processed_property(self):
        doc = Document()
        doc.status = "pending"
        assert doc.is_processed is False

        doc.status = "processed"
        assert doc.is_processed is True
```

### Property-Based Testing (Hypothesis)

For testing foundational abstractions where correctness must hold across all inputs, we use **Hypothesis** for property-based testing (PBT). This complements example-based unit tests by automatically generating hundreds of test cases to find edge cases.

**When to Use Property-Based Tests:**

- Protocol/interface implementations (e.g., CacheBackend)
- Serialization/deserialization (JSON round-trips, parsing)
- Data transformations where invariants must hold
- Any code where "for all X, property Y holds" is the requirement

**When NOT to Use Property-Based Tests:**

- API endpoint tests (use integration tests)
- CRUD operations (use example-based tests)
- Tests requiring specific database state
- UI/workflow tests

**Configuration:**

- Library: `hypothesis` (already in requirements.txt)
- Minimum iterations: 100 per property test
- Location: `backend/src/tests/unit/<module>/test_<name>.py`

**Example Property Test:**

```python
from hypothesis import given, strategies as st, settings
import pytest

@pytest.mark.asyncio
@settings(max_examples=100)
@given(key=st.text(min_size=1, max_size=200))
async def test_get_returns_none_for_missing_keys(key: str):
    """
    Property: For any key not set, get() returns None.
    Feature: unified-cache-interface, Property 1
    **Validates: Requirements 1.3**
    """
    backend = InMemoryCacheBackend()
    result = await backend.get(key)
    assert result is None
```

**Property Test Annotation Requirements:**

- Include feature name and property number in docstring
- Reference the requirement being validated
- Use `@settings(max_examples=100)` minimum

**Run Property Tests:**

```bash
# Run all unit tests including property tests
python -m pytest backend/src/tests/unit -v

# Run specific property test file
python -m pytest backend/src/tests/unit/core/test_cache_backend.py -v
```

### Known Issues (testing system)

- No dedicated security vulnerability testing suite; limited edge-case coverage
- Negative tests rely on developers explicitly logging expected errors
- Some suite docs list outdated test counts; use `python -m tests.integ.run_all_integration_tests --list-suites` as the source of truth

### Integration Test Capabilities
- Functional reliability for implemented scenarios
- Performance: sub-200ms per suite typical
- Integration testing: API + database workflows for happy path scenarios, envelope-aware assertions required
- Extensible architecture: framework supports adding new test suites
- CLI interface: individual tests, patterns, suites, discovery functionality

### Integration Test Limitations
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

## Framework History

### Original Migration (2024)

The original migration moved integration tests from pytest to a custom async framework to avoid pytest-asyncio event loop conflicts. Integration tests that required database/API access were migrated:
- **LLM Provider Tests** – `test_llm_providers.py` → `test_llm_integration.py`
- **Authentication Tests** – `test_authentication.py` → `test_auth_integration.py`
- **RBAC Tests** – `test_rbac_enforcement.py` → `test_rbac_integration.py`
- **Configuration Tests** – `test_configuration.py` → `test_config_integration.py`

### Current State (2025)

The testing system now has two components:

1. **Custom Integration Framework** (`tests/integ/`)
   - Used for async API/database integration tests
   - Auto-discovery via `run_all_integration_tests.py`
   - Based on `BaseIntegrationTestSuite`

2. **Pytest Unit Tests** (`tests/unit/`)
   - Used for fast, isolated unit tests
   - No database or API dependencies
   - Standard pytest with `conftest.py` for model registration
   - Run via `python -m pytest backend/src/tests/unit`

3. **Frontend Unit Tests** (`frontend/src/components/__tests__/`)
   - Used for React component testing
   - Jest framework via react-scripts
   - Run via `cd frontend && npm test -- --watchAll=false`

This dual approach provides the best of both worlds: pytest's excellent test discovery and assertion introspection for unit tests, while maintaining the custom framework's event loop control for integration tests.

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

Shu uses a multi-layered testing approach:

1. **Integration tests** (custom framework) for API/database workflows
2. **Unit tests** (pytest) for fast, isolated logic tests
3. **Property-based tests** (Hypothesis) for foundational abstractions requiring correctness guarantees

The custom integration framework avoids pytest-asyncio event loop conflicts while pytest provides excellent ergonomics for synchronous unit tests. Property-based testing with Hypothesis catches edge cases in protocols and serialization that example-based tests would miss. Dedicated security vulnerability testing still needs to be added before any production deployment.
