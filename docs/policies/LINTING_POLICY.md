# Linting and Code Quality Policy

## Overview

This document defines the mandatory linting and code quality standards for the Shu project. All code contributions must
pass automated linting checks before being merged.

## Tooling Stack

### Python

- **Ruff** - Primary linter and formatter (replaces black, isort, flake8, pylint)
- **mypy** - Static type checker

### Frontend

- **ESLint** - JavaScript/React linter
- **Prettier** - Code formatter

### Automation

- **pre-commit** - Git hook framework for automated checks
- **GitHub Actions** - CI/CD linting enforcement

## Enforcement Levels

### Level 1: Pre-commit Hooks (Local)

- Runs automatically before each commit
- Auto-fixes most issues
- Blocks commits with unfixable issues
- Can be bypassed with `--no-verify` (discouraged)

### Level 2: CI/CD (GitHub Actions)

- Runs on all pull requests
- Cannot be bypassed
- Blocks merging if checks fail
- Required for all branches

### Level 3: Code Review

- Human reviewers verify compliance
- Check for patterns not caught by automated tools
- Ensure adherence to architectural standards

## Mandatory Checks

### Python (Backend)

#### Ruff Linting

- **PEP8 compliance** - Code style standards
- **Import sorting** - Organized imports (stdlib → third-party → local)
- **Unused code** - No unused imports, variables, or arguments
- **Timezone awareness** - All datetimes must be timezone-aware (DTZ rules)
- **No print statements** - Use structured logging instead
- **Code complexity** - Reasonable function/class complexity
- **Naming conventions** - PEP8 naming (snake_case, PascalCase)

#### mypy Type Checking

- **Type hints required** - All functions must have type hints
- **No implicit optionals** - Explicit Optional[T] required
- **Strict equality** - Type-safe comparisons
- **Return types** - All functions must declare return types

### Frontend (React/JavaScript)

#### ESLint

- **React best practices** - Functional components, proper hooks usage
- **Hook rules** - Correct dependencies, no conditional hooks
- **No console statements** - Use log.js utility instead
- **Unused variables** - No unused imports or variables
- **Custom rules** - Envelope access patterns (no response.data.data)

#### Prettier

- **Consistent formatting** - Semicolons, quotes, line length
- **Import organization** - Sorted and grouped
- **Trailing commas** - ES5 style

### General (All Files)

#### File Quality

- **Trailing whitespace** - Removed from all lines
- **End-of-file newlines** - All files must end with newline
- **Line endings** - LF (Unix-style) only
- **File size** - No files >1MB without justification

#### Security

- **No secrets** - API keys, passwords, tokens detected and blocked
- **No merge conflicts** - Conflict markers detected and blocked

## Configuration Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Python linting (Ruff, mypy, pytest) |
| `frontend/.eslintrc.json` | ESLint configuration |
| `frontend/.prettierrc.json` | Prettier configuration |
| `.pre-commit-config.yaml` | Pre-commit hooks |
| `.github/workflows/lint.yml` | CI/CD workflow |
| `.vscode/settings.json` | VS Code integration |

## Developer Workflow

### Initial Setup (One-Time)

```bash
# Install tools
pip install ruff mypy pre-commit
cd frontend && npm install

# Set up hooks
make setup-hooks
```

### Daily Development

```bash
# Before committing
make lint-fix

# Or check without fixing
make lint
```

### When Pre-commit Fails

```bash
# Auto-fix issues
make lint-fix

# Stage fixes and commit
git add .
git commit -m "your message"
```

### When CI Fails

1. Pull latest changes
2. Run `make lint-fix` locally
3. Commit and push fixes
4. Wait for CI to pass

## IDE Integration

### VS Code (Recommended)

- Install recommended extensions (prompted on first open)
- Settings configured in `.vscode/settings.json`
- Format on save enabled
- Auto-fix on save enabled

### PyCharm/IntelliJ

- Install Ruff plugin from marketplace
- Enable ESLint and Prettier in settings
- Configure format on save

### Other IDEs

- Configure Ruff as external tool
- Configure ESLint and Prettier
- See `docs/LINTING_GUIDE.md` for details

## Exceptions and Overrides

### Per-File Ignores

Defined in `pyproject.toml`:

- `__init__.py` - Allow unused imports (F401)
- `backend/scripts/*.py` - Allow print statements (T20)
- `tests/**/*.py` - Relaxed rules for tests

### Inline Ignores (Use Sparingly)

```python
# Python: Ignore specific rule
result = some_function()  # noqa: ARG001

# Python: Ignore type check
value = cast(str, some_value)  # type: ignore[arg-type]
```

```javascript
// JavaScript: Ignore ESLint rule
// eslint-disable-next-line no-console
console.log('debug');
```

**Policy:** Inline ignores require justification in code review.

## Metrics and Monitoring

### CI/CD Metrics

- Linting pass rate (target: 100%)
- Average fix time (target: <5 minutes)
- Bypass rate (target: 0%)

### Code Quality Metrics

- Type coverage (target: 100% for new code)
- Complexity scores (target: <10 per function)
- Test coverage (target: >80%)

## Consequences of Non-Compliance

### Pull Requests

- **Linting failures** - PR cannot be merged
- **Type errors** - Warning (will become blocking)
- **Security issues** - PR blocked immediately

### Repeated Violations

1. First time: Reminder of policy
2. Second time: Required training session
3. Third time: Code review privileges suspended

## Migration Plan

### Phase 1: Setup (Week 1)

- ✅ Install and configure tools
- ✅ Create documentation
- ✅ Set up CI/CD
- ✅ Train team

### Phase 2: Soft Enforcement (Weeks 2-4)

- Pre-commit hooks optional
- CI warnings only (non-blocking)
- Team feedback and adjustments

### Phase 3: Hard Enforcement (Week 5+)

- Pre-commit hooks required
- CI blocking on failures
- Full policy enforcement

## Resources

- **Quick Start:** `LINTING_QUICKSTART.md`
- **Full Guide:** `docs/LINTING_GUIDE.md`
- **Coding Standards:** `docs/policies/DEVELOPMENT_STANDARDS.md`
- **Ruff Docs:** <https://docs.astral.sh/ruff/>
- **ESLint Docs:** <https://eslint.org/>
- **Pre-commit Docs:** <https://pre-commit.com/>

## Policy Updates

This policy is reviewed quarterly and updated as needed. Suggestions for improvements should be submitted via pull request.

**Last Updated:** January 28, 2026
**Next Review:** April 28, 2026
**Policy Owner:** Engineering Team
