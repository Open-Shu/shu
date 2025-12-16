# Contributing to Shu

Thank you for your interest in contributing to Shu. This document outlines how to contribute effectively.

## Contributor License Agreement (CLA)

By submitting a contribution, you agree to the [Contributor License Agreement](CLA.md). This allows Shu to be dual-licensed under GPLv3 and commercial terms.

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL with pgvector extension
- Redis
- Docker and Docker Compose (recommended for local development)

### Local Development Setup

```bash
# Clone the repository
git clone https://github.com/open-shu/shu.git
cd shu

# Copy environment template
cp .env.example .env

# Start services with Docker Compose
docker compose -f deployment/compose/docker-compose.yml up -d

# Or run locally:
# Backend
cd backend && pip install -r requirements.txt
uvicorn shu.main:app --reload --app-dir src

# Frontend
cd frontend && npm install && npm start
```

## Code Standards

### Directory Structure

- `backend/src/shu/` - Backend application (src-layout)
- `frontend/src/` - React frontend
- `tests/` - Integration and unit tests
- `docs/` - Documentation

### Import Standards

Use absolute imports from the package root:

```python
# Correct
from shu.core.database import get_async_session_local
from shu.schemas.envelope import SuccessResponse

# Incorrect
from src.shu.core.database import get_async_session_local
```

### Python Standards

- Type hints for all function parameters and returns
- Docstrings for public functions and classes
- Use `logging` module (not print statements)
- Follow PEP 8 naming: `snake_case` for functions/variables, `PascalCase` for classes

### Frontend Standards

- Functional components with hooks (no class components)
- React Query for server state
- Material-UI for components
- Use `frontend/src/utils/log.js` instead of `console.log`

### API Response Format

All endpoints return a standardized envelope:

```json
// Success
{ "data": { ... } }

// Error
{ "error": { "message": "...", "code": "ERROR_CODE" } }
```

## Testing

Shu uses a custom integration test framework. Do not add pytest files.

### Running Tests

```bash
# Run all tests
python tests/run_all_integration_tests.py

# Run specific suite
python tests/test_llm_integration.py

# List available tests
python tests/run_all_integration_tests.py --list-suites
```

### Writing Tests

- Extend `BaseIntegrationTestSuite` for new test suites
- Name files `test_*_integration.py` for auto-discovery
- Use `extract_data()` from `tests/response_utils.py` to unwrap API responses
- Log expected errors with `=== EXPECTED TEST OUTPUT: ... ===` pattern

## Pull Request Process

1. **Fork and branch**: Create a feature branch from `mainline`
2. **Follow standards**: Ensure code follows the standards above
3. **Test**: All tests must pass (`python tests/run_all_integration_tests.py`)
4. **Document**: Update relevant documentation for any API or behavior changes
5. **Commit messages**: Use clear, descriptive commit messages
6. **Submit PR**: Open a pull request with a clear description of changes

### PR Checklist

- [ ] Tests pass locally
- [ ] Code follows project style guidelines
- [ ] Documentation updated if applicable
- [ ] No hardcoded secrets or credentials
- [ ] Commits are focused and well-described

## Reporting Issues

When reporting issues, please include:

- Shu version
- Python/Node.js version
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs or error messages

## Security Issues

For security vulnerabilities, please email security@openshu.ai rather than opening a public issue.

## Code of Conduct

Be respectful and constructive. We're building something together.

## Questions

- **General questions**: Open a GitHub Discussion
- **Bug reports**: Open a GitHub Issue
- **Security issues**: Email security@openshu.ai

## License

By contributing, you agree that your contributions will be licensed under the project's dual license (GPLv3 and Commercial). See [LICENSE.md](LICENSE.md) for details.

