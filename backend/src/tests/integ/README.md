# Shu Integration Test Framework

**CRITICAL FOR AI ASSISTANTS**: Shu uses a **custom integration test framework**. DO NOT use pytest-asyncio.

**TESTING STRATEGY**: See `../docs/policies/TESTING.md` for our approach to writing tests that **discover real use-case bugs before UAT does**.

## Framework Overview

### **Why Custom Framework?**

Shu switched from pytest-asyncio to a custom test framework to solve fundamental async event loop issues that were causing unreliable tests.

#### **The pytest-asyncio Problem**
pytest-asyncio has inherent issues with async event loops that caused frequent test failures:

1. **Event Loop Conflicts**: pytest-asyncio creates multiple event loops during test execution, leading to conflicts when tests try to use async database connections or HTTP clients.

2. **Connection Pool Issues**: FastAPI applications with async database connections would fail unpredictably because pytest-asyncio couldn't properly manage the connection lifecycle across test boundaries.

3. **Flaky Tests**: Tests would pass individually but fail when run together due to event loop state pollution between tests.

4. **Slow Execution**: pytest-asyncio's overhead and event loop management made test suites take minutes instead of seconds.

#### **Our Custom Solution**
- **Single Event Loop**: One event loop for the entire test session, avoiding the pytest-asyncio event loop conflicts
- **Connection Management**: Database and HTTP connections are managed across test boundaries
- **Real Integration Testing**: Tests actual API endpoints with real database operations
- **CLI Interface**: Simple CLI interface with filtering options
- **Readable Output**: Progress is reported with TEST 1/N format and readable results
- **Extensible**: Easy to add new test suites for different features

### **Current Status (snapshot)**
- **6 Integration Test Suites**: 60 integration tests, last documented run at 100% pass rate
- **2 Unit Test Suites**: 16 unit tests, last documented run at 100% pass rate
- **Total Execution Time**: <1.0 seconds for all tests at last documented run
- **Migration**: COMPLETE - All pytest files removed
- **Output Format**: TEST 1/N style progress

## Quick Start

### **Run All Tests**
```bash
python -m tests.integ.run_all_integration_tests
```

### **Run Specific Test Suite**
```bash
# Integration Tests
python tests/test_llm_integration.py
python tests/test_auth_integration.py
python tests/test_chat_integration.py
python tests/test_knowledge_source_integration.py
python tests/test_rbac_integration.py
python tests/test_config_integration.py

# Unit Tests
python tests/test_llm_unit_migrated.py
python tests/test_api_key_unit.py
```

### **Run Individual Tests**
```bash
python tests/test_llm_integration.py --test test_create_provider_success
python tests/test_auth_integration.py --pattern "admin|rbac"
```

### **List Available Tests**
```bash
python tests/test_llm_integration.py --list
python -m tests.integ.run_all_integration_tests --list-suites
```

## Framework Architecture

### **Core Components**
```
tests/
├── integration_test_runner.py         # Core engine (DO NOT MODIFY)
├── base_integration_test.py           # Base class for integration tests
├── base_unit_test.py                  # Base class for unit tests
├── run_all_integration_tests.py       # Master runner (DO NOT MODIFY)
├── test_template.py                   # Copy this for new test suites
├── test_llm_integration.py           # LLM provider tests (10 tests)
├── test_auth_integration.py          # Authentication tests (10 tests)
├── test_rbac_integration.py          # RBAC tests (11 tests)
├── test_config_integration.py        # Configuration tests (11 tests)
├── test_llm_unit_migrated.py         # LLM unit tests (10 tests)
├── test_api_key_unit.py               # API key unit tests (6 tests)
└── test_*_integration.py             # Your new test suites go here
```

### **Test Function Signature**
**REQUIRED**: All test functions MUST use this exact signature:
```python
async def test_function_name(client, db, auth_headers):
    """Test description."""
    # client: httpx.AsyncClient for API calls
    # db: AsyncSession for database operations
    # auth_headers: Dict with Authorization header
```

## Adding New Test Suites

### **Step 1: Copy Template**
```bash
cp tests/test_template.py tests/test_your_feature_integration.py
```

### **Step 2: Customize Test Suite Class**
```python
class YourFeatureTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self) -> List[Callable]:
        return [
            test_your_function_1,
            test_your_function_2,
            # Add all your test functions here
        ]
    
    def get_suite_name(self) -> str:
        return "Your Feature Integration Tests"
    
    def get_suite_description(self) -> str:
        return "End-to-end tests for your feature"
```

### **Step 3: Write Test Functions**
```python
async def test_your_feature_create(client, db, auth_headers):
    """Test creating a resource."""
    # Test API endpoint
    response = await client.post("/api/v1/your-endpoint", 
                                json=test_data, 
                                headers=auth_headers)
    assert response.status_code == 201
    
    # Verify database state
    from integ.response_utils import extract_data
...
result = await db.execute(text("SELECT * FROM table WHERE id = :id"),
                             {"id": extract_data(response)["id"]})
    assert result.fetchone() is not None
```

### **Step 4: Test Your Suite**
```bash
python tests/test_your_feature_integration.py
```

**Auto-Discovery**: The master test runner automatically finds your new suite!

## CLI Commands Reference

### **Individual Test Suite Commands**
```bash
# Run all tests in a suite
python -m tests.integ.test_llm_integration

# List available tests  
python -m tests.integ.test_llm_integration --list

# Run specific tests
python -m tests.integ.test_llm_integration --test test_name1 test_name2

# Run tests matching pattern (regex)
python -m tests.integ.test_llm_integration --pattern "create|update"

# Get help
python -m tests.integ.test_llm_integration --help
```

### **Master Test Runner Commands**
```bash
# Run all test suites
python -m tests.integ.run_all_integration_tests

# List all test suites
python -m tests.integ.run_all_integration_tests --list-suites

# Run specific test suite
python -m tests.integ.run_all_integration_tests --suite llm

# Run multiple test suites
python -m tests.integ.run_all_integration_tests --suite auth --suite llm --suite config

# Run specific tests in a suite
python -m tests.integ.run_all_integration_tests --suite llm --test test_create_provider_success

# Run tests matching pattern in a suite
python -m tests.integ.run_all_integration_tests --suite llm --pattern create

# Clean up test data after running tests
python -m tests.integ.run_all_integration_tests --cleanup

# Only run cleanup, don't run tests
python -m tests.integ.run_all_integration_tests --cleanup-only

# Write test output to log file
python -m tests.integ.run_all_integration_tests --suite auth --log

# Run multiple suites with logging
python -m tests.integ.run_all_integration_tests --suite auth --suite config --log

# Combine logging with cleanup
python -m tests.integ.run_all_integration_tests --suite llm --log --cleanup
```

## Test Logging System

### **File Logging Feature**
The test framework can write all test output to a log file for detailed analysis and debugging.

#### **Usage**
```bash
# Enable file logging with --log flag
python -m tests.integ.run_all_integration_tests --suite auth --log
python -m tests.integ.run_all_integration_tests --log  # For all suites
```

#### **Log File Details**
- **Location**: `backend/src/tests/testing.log`
- **Behavior**: File is wiped at the beginning of each test run
- **Content**: All console output plus detailed logging from the application
- **Format**: Timestamped entries with logger names and levels
- **Git**: Automatically ignored (added to `.gitignore`)

#### **Benefits**
- **Detailed Debugging**: Capture all application logs during test execution
- **Persistent Records**: Keep test output for later analysis
- **CI/CD Integration**: Easily capture test logs in automated environments
- **Error Investigation**: Full context for failed tests

## Test Data Cleanup System

### **Problem Solved**
Integration tests run against the same API and database as your development environment. This creates test data that can clutter your development database. The cleanup system solves this by:

1. **Identifying test data** using naming patterns
2. Cleaning up test data via API calls (not just SQL)
3. **Preserving your dev data** by only targeting test patterns
4. **Running automatically** after tests or manually when needed

### **What Gets Cleaned Up**
The system identifies test data using these patterns:
- Contains "test" or "Test"
- Contains "TEST"
- Contains "Integration" or "INTEGRATION"
- Contains "dummy", "temp", "sample" (case insensitive)

**Examples of data that gets cleaned:**
- "Test Knowledge Base abc123"
- "Integration Test Source"
- "TEST_LLM_PROVIDER"
- "Sample Document Collection"

**Examples of data that's preserved:**
- "My Personal Knowledge Base"
- "Production Documents"
- "Client Project Files"

### **Usage Options**

#### **1. Automatic Cleanup After Tests**
```bash
# Clean up after running all tests
python -m tests.integ.run_all_integration_tests --cleanup

# Clean up after running specific suite
python -m tests.integ.run_all_integration_tests --suite knowledge_base --cleanup
```

#### **2. Manual Cleanup Script**
```bash
# See what would be cleaned up (dry run)
python scripts/cleanup_test_data.py --dry-run

# Actually clean up test data
python scripts/cleanup_test_data.py

# Verbose output
python scripts/cleanup_test_data.py --verbose
```

#### **3. Cleanup Only (No Tests)**
```bash
# Run cleanup without running tests
python -m tests.integ.run_all_integration_tests --cleanup-only
```

### **Safety Features**
- **Naming Pattern Matching**: Only deletes data matching test patterns
- **Dry Run Mode**: See what would be deleted before actually deleting
- **API-Based Cleanup**: Uses proper API endpoints with validation
- **Error Handling**: Continues cleanup even if some items fail
- **Detailed Logging**: Shows exactly what was cleaned up

### **Test Naming Convention Requirements**

#### **CRITICAL: Entity Names Must Include "test"**
The cleanup system identifies test data using these patterns:
- `.*[Tt]est.*` - Contains "test" or "Test"
- `.*TEST.*` - Contains "TEST"
- `.*Integration.*` - Contains "Integration"
- `.*[Dd]ummy.*` - Contains "dummy" or "Dummy"
- `.*[Tt]emp.*` - Contains "temp" or "Temp"
- `.*[Ss]ample.*` - Contains "sample" or "Sample"

#### **Correct Test Entity Names**
```python
# Use "test_" prefix (recommended pattern)
"test_memory_provider_abc123"
"test_kb_config_def456"
"test_perf_conversation_789xyz"

# Or include "test" anywhere in the name
"Memory Test Provider abc123"
"KB Config Test def456"
"Performance Test Conversation 789xyz"
```

#### **Incorrect Names (Won't Be Cleaned Up)**
```python
# Missing test keywords - will NOT be cleaned up
"Memory Provider abc123"
"KB Config def456"
"Performance Conversation 789xyz"
"My Research Documents"
"Client Project KB"
```

#### **For Development Data**
- Never use test keywords in real development data names
- Examples: `"My Research Documents"`, `"Client Project KB"`, `"Production Model"`

#### **For Cleanup**
- Run `--dry-run` first to see what would be deleted
- Use `--cleanup` flag when running tests regularly
- Run manual cleanup periodically: `python scripts/cleanup_test_data.py`

## Standardized Response Envelope Handling

This section describes how to use the shared helper for unwrapping API responses that follow the standard Shu response envelope.

Current state (snapshot):
- `tests.response_utils.extract_data` is the helper used to unwrap `SuccessResponse` envelopes while tolerating bare objects when needed.
- Some legacy tests may still contain direct `response.json()["data"]` reads. When you find one, replace it with `extract_data(response)`.

Required usage:
- Always unwrap payloads with `extract_data(response)` after asserting the status code.
- Only assert the envelope structure (presence of `data` or `error`) when that is the explicit test objective.
- Avoid directly indexing `response.json()["data"]`.

Example usage:
````python
from integ.response_utils import extract_data

# Create
resp = await client.post("/api/v1/items", json=payload, headers=auth_headers)
assert resp.status_code == 201
item = extract_data(resp)
item_id = item["id"]

# Get
resp = await client.get(f"/api/v1/items/{item_id}", headers=auth_headers)
assert resp.status_code == 200
item = extract_data(resp)

# Envelope structure assertion (only when needed)
obj = resp.json()
assert "data" in obj
````

## Common Test Patterns

### **API Testing**
```python
# Authenticated request
response = await client.post("/api/v1/endpoint", 
                            json=data, 
                            headers=auth_headers)
assert response.status_code == 201

# Unauthenticated request (should fail)
response = await client.post("/api/v1/endpoint", json=data)
assert response.status_code == 401
```

### **Database Verification**
```python
# Verify data created
result = await db.execute(text("SELECT * FROM table WHERE id = :id"), {"id": item_id})
assert result.fetchone() is not None

# Verify data updated
result = await db.execute(text("SELECT name FROM table WHERE id = :id"), {"id": item_id})
assert result.scalar() == "Updated Name"

# Verify data deleted
result = await db.execute(text("SELECT COUNT(*) FROM table WHERE id = :id"), {"id": item_id})
assert result.scalar() == 0
```

### **Error Testing**
```python
# Test validation errors
invalid_data = {"name": ""}  # Missing required field
response = await client.post("/api/v1/endpoint", json=invalid_data, headers=auth_headers)
assert response.status_code == 422

# Test duplicate creation
response1 = await client.post("/api/v1/endpoint", json=data, headers=auth_headers)
response2 = await client.post("/api/v1/endpoint", json=data, headers=auth_headers)  # Same data
assert response2.status_code == 400
```

### **Negative Test Logging** (CRITICAL)

When writing tests that expect warnings, errors, or exceptions, **you MUST log the expected test output** to distinguish between expected behavior and actual bugs.

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

#### **Why This Matters**
- **Prevents Bug Overlook**: Without logging expected output, we might miss real bugs
- **Clear Test Intent**: Makes it obvious when warnings/errors are expected vs. unexpected
- **Debugging Clarity**: Helps developers understand test behavior during investigation
- **CI/CD Reliability**: Prevents false alarms in automated testing

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

## What NOT to Do

### **DON'T Use pytest**
```python
# DON'T DO THIS
import pytest

@pytest.mark.asyncio
async def test_something():
    pass
```

### **DON'T Modify Core Framework Files**
- `integration_test_runner.py` (do not modify)
- `base_integration_test.py` (do not modify)
- `run_all_integration_tests.py` (do not modify)

### **DON'T Use Wrong Function Signature**
```python
# DON'T DO THIS
async def test_something():  # Missing required parameters
    pass

# DON'T DO THIS
def test_something(client, db, auth_headers):  # Not async
    pass
```

## Current Test Suites (snapshot)

### **Integration Test Suites**

#### **LLM Provider Integration Tests** (`test_llm_integration.py`)
- **Tests**: 10 integration tests (snapshot)
- **Coverage**: CRUD operations, API key security, validation, error handling
- **Performance**: 0.071s execution time (snapshot)
- **Status**: 100% passing at last documented run

#### **Authentication Integration Tests** (`test_auth_integration.py`)
- **Tests**: 10 integration tests (snapshot)
- **Coverage**: Authentication, authorization, RBAC, JWT tokens, security
- **Performance**: 0.053s execution time (snapshot)
- **Status**: 100% passing at last documented run

#### **RBAC Integration Tests** (`test_rbac_integration.py`)
- **Tests**: 11 integration tests (snapshot)
- **Coverage**: Role-based access control, endpoint protection, permission enforcement
- **Performance**: 0.206s execution time (snapshot)
- **Status**: 100% passing at last documented run

#### **Configuration Integration Tests** (`test_config_integration.py`)
- **Tests**: 11 integration tests (snapshot)
- **Coverage**: Public config endpoint, security validation, performance testing
- **Performance**: 0.020s execution time (snapshot)
- **Status**: 100% passing at last documented run

### **Unit Test Suites** (snapshot)

#### **LLM Unit Tests** (`test_llm_unit_migrated.py`)
- **Tests**: 10 unit tests (snapshot)
- **Coverage**: Business logic, data validation, response formatting
- **Performance**: <0.01s execution time (snapshot)
- **Status**: 100% passing at last documented run

#### **API Key Unit Tests** (`test_api_key_unit.py`)
- **Tests**: 6 unit tests (snapshot)
- **Coverage**: API key display, security, boolean logic
- **Performance**: <0.01s execution time (snapshot)
- **Status**: 100% passing at last documented run

## Debugging Tests

### View Test Output
```bash
# Run with verbose logging
python tests/test_your_suite.py --verbose

# Check Shu logs for errors
tail -f logs/shu.log
```

### Common Issues
1. **Wrong function signature**: Must be `async def test_name(client, db, auth_headers)`
2. **Missing imports**: Import `text` from `sqlalchemy` for raw SQL
3. **Auth headers**: Use provided `auth_headers` for authenticated requests
4. **Database queries**: Use parameterized queries with `text()` and parameter dict

## Differences from pytest-asyncio

- Uses a single event loop for the entire test session instead of per-test loops.
- Aligns connection pool behaviour with the single-loop model the app expects.
- Has shown faster execution than the previous pytest-asyncio setup in past measurements; re-measure when suites change.
- Aims to produce clearer error messages and test flow.
- Focuses on real integration testing against the application stack.
- Provides an architecture that is intended to make it straightforward to add new test suites and features.

## Technical Details: Why We Abandoned pytest-asyncio

### The pytest-asyncio Problem

pytest-asyncio caused recurring issues when testing FastAPI applications with async database connections:

```python
# This pattern often failed with pytest-asyncio:
@pytest.mark.asyncio
async def test_database_operation():
    async with get_db_session() as db:
        # Event loop conflicts and connection issues were common here
        result = await db.execute(text("SELECT 1"))
```

Observed issues included:
1. **Multiple Event Loops**: Creating new event loops for each test conflicted with FastAPI's expectation of a single, persistent event loop.
2. **Connection Pool Problems**: Async database connection pools became unstable when event loops were created and destroyed between tests.
3. **State Pollution**: Event loop state from one test affected subsequent tests, causing unpredictable failures.
4. **Fixture Lifecycle Issues**: Async fixtures did not consistently clean up resources when event loops were torn down.

### Our Custom Framework Approach

```python
# This pattern is what the custom framework is designed to support:
async def test_database_operation(client, db, auth_headers):
    # Uses the same event loop and connection pool throughout
    response = await client.post("/api/v1/resource",
                                json={"name": "test"},
                                headers=auth_headers)
    assert response.status_code == 201

    # Database verification
    from integ.response_utils import extract_data
    result = await db.execute(
        text("SELECT * FROM resource WHERE id = :id"),
        {"id": extract_data(response)["id"]},
    )
    assert result.fetchone() is not None
```

Key architectural decisions:
1. **Single Event Loop**: One event loop for the entire test session to avoid the conflicts seen with pytest-asyncio.
2. **Centralised Resource Management**: Database connections and HTTP clients are managed across test boundaries.
3. **Clean Test Boundaries**: Each test runs with a clean state without recreating the event loop.
4. **FastAPI Alignment**: The framework is designed around FastAPI-style async patterns.

Result (snapshot): After the migration, the specific async event loop issues described above stopped appearing in local runs, and test execution time decreased compared to the previous pytest-asyncio setup. Re-evaluate this periodically as suites and infrastructure evolve.

## Remember

1. **Use the custom framework** instead of pytest-asyncio for new tests.
2. **Copy the template** rather than starting from scratch.
3. **Follow the signature**: `async def test_name(client, db, auth_headers)`.
4. **Test real workflows** that exercise both API and database behaviour.
5. **Keep framework files unchanged**; only modify your test suites unless you are explicitly working on the framework itself.

This framework is intended to provide reliable integration testing across the application stack. Revisit this document and the implementation if you observe flaky behaviour or new categories of failures.
