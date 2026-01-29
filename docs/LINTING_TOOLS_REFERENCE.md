# Linting Tools Reference

Complete reference for all linting tools configured in the Shu project.

## Overview

| Language/Type | Tool | Purpose | Config File |
|---------------|------|---------|-------------|
| Python | Ruff | Linting + Formatting | `pyproject.toml` |
| Python | mypy | Type Checking | `pyproject.toml` |
| Python | Bandit | Security Analysis | `pyproject.toml` |
| JavaScript/React | ESLint | Linting | `frontend/.eslintrc.json` |
| JavaScript/React | Prettier | Formatting | `frontend/.prettierrc.json` |
| SQL | SQLFluff | Linting + Formatting | `.sqlfluff` |
| Markdown | ~~markdownlint~~ | ~~Documentation Linting~~ | Disabled (too strict) |
| Docker | hadolint | Dockerfile Linting | Built-in rules |
| Shell | shellcheck | Shell Script Linting | Built-in rules |
| Secrets | detect-secrets | Secret Detection | `.secrets.baseline` |

## Python Tools

### Ruff
**Purpose:** Fast, all-in-one Python linter and formatter

**What it checks:**
- Code style (PEP8)
- Import sorting
- Type annotations
- Docstrings
- Security issues
- Code complexity
- Unused code
- And 700+ more rules

**Commands:**
```bash
# Check for issues
ruff check backend/

# Auto-fix issues
ruff check --fix backend/

# Format code
ruff format backend/

# Check specific file
ruff check backend/src/shu/api/chat.py
```

**Configuration:** `pyproject.toml` under `[tool.ruff]`

**Speed:** 10-100x faster than traditional tools

### mypy
**Purpose:** Static type checker for Python

**What it checks:**
- Missing type hints
- Type mismatches
- Invalid type operations
- Return type consistency

**Commands:**
```bash
# Check all code
mypy backend/src/shu

# Check specific file
mypy backend/src/shu/api/chat.py

# Show error codes
mypy --show-error-codes backend/src/shu
```

**Configuration:** `pyproject.toml` under `[tool.mypy]`

**Strictness:** High - requires type hints on all functions

### Bandit
**Purpose:** Security vulnerability scanner for Python

**What it checks:**
- SQL injection risks
- Hardcoded passwords
- Insecure functions (eval, exec)
- Weak cryptography
- Shell injection
- And 40+ security issues

**Commands:**
```bash
# Scan all code
bandit -c pyproject.toml -r backend/src/shu

# Show only high severity
bandit -c pyproject.toml -r backend/src/shu -ll

# Generate report
bandit -c pyproject.toml -r backend/src/shu -f json -o report.json
```

**Configuration:** `pyproject.toml` under `[tool.bandit]`

**Severity Levels:** Low, Medium, High

## Frontend Tools

### ESLint
**Purpose:** JavaScript/React linter

**What it checks:**
- React best practices
- Hook rules
- Unused variables
- Console statements
- Code complexity
- Magic numbers
- Custom project rules

**Commands:**
```bash
cd frontend

# Check for issues
npm run lint

# Auto-fix issues
npm run lint:fix

# Check specific file
npx eslint src/App.js
```

**Configuration:** `frontend/.eslintrc.json`

**Plugins:** react, react-hooks

### Prettier
**Purpose:** Opinionated code formatter

**What it formats:**
- Semicolons
- Quotes
- Line length
- Indentation
- Trailing commas
- Bracket spacing

**Commands:**
```bash
cd frontend

# Format all files
npm run format

# Check formatting
npm run format:check

# Format specific file
npx prettier --write src/App.js
```

**Configuration:** `frontend/.prettierrc.json`

**Philosophy:** Stop debating style, just format

## SQL Tools

### SQLFluff
**Purpose:** SQL linter and formatter

**What it checks:**
- SQL syntax
- Keyword capitalization
- Indentation
- Line length
- Naming conventions
- Query structure

**Commands:**
```bash
# Lint SQL files
sqlfluff lint init-db.sql

# Auto-fix issues
sqlfluff fix init-db.sql

# Lint migrations
sqlfluff lint backend/migrations/

# Check specific dialect
sqlfluff lint --dialect postgres init-db.sql
```

**Configuration:** `.sqlfluff`

**Dialect:** PostgreSQL

**Style:** Keywords uppercase, identifiers lowercase

## Documentation Tools

### markdownlint (DISABLED)

**Status:** Disabled - too strict for documentation

**Why disabled:**
- Line length rules break on URLs
- Forces specific list numbering styles
- Too opinionated for technical docs
- Slows down documentation writing

**If you want to enable it:**
Edit `.pre-commit-config.yaml` and uncomment the markdownlint section.

**Manual usage (if needed):**
```bash
# Install
npm install -g markdownlint-cli

# Lint markdown files
markdownlint docs/ *.md

# Auto-fix issues
markdownlint --fix docs/ *.md
```

**Configuration:** `.markdownlint.json` (still present if you want to enable)

## Docker Tools

### hadolint
**Purpose:** Dockerfile linter

**What it checks:**
- Best practices
- Security issues
- Deprecated instructions
- Layer optimization
- Pinned versions
- Shell usage

**Commands:**
```bash
# Lint Dockerfile
hadolint deployment/docker/api/Dockerfile

# Lint all Dockerfiles
find . -name "Dockerfile*" | xargs hadolint

# Ignore specific rules
hadolint --ignore DL3008 Dockerfile
```

**Configuration:** Command-line args in `.pre-commit-config.yaml`

**Ignored Rules:** DL3008 (apt pinning), DL3013 (pip pinning)

## Shell Tools

### shellcheck
**Purpose:** Shell script linter

**What it checks:**
- Syntax errors
- Quoting issues
- Variable usage
- Command substitution
- Portability issues
- Common mistakes

**Commands:**
```bash
# Check shell script
shellcheck backend/scripts/run_dev.sh

# Check all shell scripts
find . -name "*.sh" | xargs shellcheck

# Show only warnings and errors
shellcheck --severity=warning script.sh
```

**Configuration:** Command-line args in `.pre-commit-config.yaml`

**Severity:** Warning and above

## Security Tools

### detect-secrets
**Purpose:** Prevent secrets from being committed

**What it detects:**
- API keys
- Passwords
- Private keys
- Tokens
- Connection strings
- High entropy strings

**Commands:**
```bash
# Scan for secrets
detect-secrets scan

# Update baseline
detect-secrets scan --baseline .secrets.baseline

# Audit findings
detect-secrets audit .secrets.baseline

# Check specific file
detect-secrets scan backend/src/shu/api/auth.py
```

**Configuration:** `.secrets.baseline`

**Workflow:** Baseline tracks known safe values, blocks new secrets

## Pre-commit Integration

All tools run automatically via pre-commit hooks:

```bash
# Install hooks
pre-commit install

# Run all hooks manually
pre-commit run --all-files

# Run specific hook
pre-commit run ruff --all-files
pre-commit run eslint --all-files
pre-commit run sqlfluff-lint --all-files

# Update hook versions
pre-commit autoupdate
```

**Configuration:** `.pre-commit-config.yaml`

## Makefile Commands

Convenient shortcuts for common operations:

```bash
# Check everything
make lint

# Check specific language
make lint-python
make lint-frontend
make lint-sql
make lint-docs
make lint-docker

# Format everything
make format

# Format specific language
make format-python
make format-frontend
make format-sql

# Auto-fix everything
make lint-fix
```

## CI/CD Integration

All tools run in GitHub Actions on every PR:

**Workflow:** `.github/workflows/lint.yml`

**Jobs:**
- python-lint (Ruff + mypy + Bandit)
- frontend-lint (ESLint + Prettier)
- pre-commit (All hooks)

**Blocking:** PRs cannot merge if linting fails

## Tool Comparison

### Speed
1. **Ruff** - Fastest (Rust-based)
2. **Prettier** - Very fast
3. **ESLint** - Fast
4. **mypy** - Moderate (caching helps)
5. **Bandit** - Moderate
6. **SQLFluff** - Slower (complex parsing)

### Auto-fix Capability
- ✅ **Full auto-fix:** Ruff, Prettier, SQLFluff, markdownlint
- ⚠️ **Partial auto-fix:** ESLint (some rules)
- ❌ **No auto-fix:** mypy, Bandit, hadolint, shellcheck, detect-secrets

### Strictness
- **Strictest:** mypy (type checking), Bandit (security)
- **Moderate:** Ruff, ESLint
- **Lenient:** Prettier (formatting only)

## Configuration Hierarchy

### Python (pyproject.toml)
```toml
[tool.ruff]           # Ruff settings
[tool.ruff.lint]      # Linting rules
[tool.mypy]           # Type checking
[tool.bandit]         # Security scanning
```

### Frontend
```
frontend/.eslintrc.json      # ESLint rules
frontend/.prettierrc.json    # Prettier formatting
frontend/.prettierignore     # Prettier exclusions
```

### Other
```
.sqlfluff                    # SQL linting
.markdownlint.json          # Markdown linting
.pre-commit-config.yaml     # Pre-commit hooks
.secrets.baseline           # Known safe secrets
.editorconfig               # Editor settings
```

## Troubleshooting

### Tool not found
```bash
# Python tools
pip install ruff mypy bandit sqlfluff

# Node tools
cd frontend && npm install

# Pre-commit
pip install pre-commit
```

### Cache issues
```bash
# Clear Ruff cache
rm -rf .ruff_cache

# Clear mypy cache
rm -rf .mypy_cache

# Clear pre-commit cache
pre-commit clean
```

### False positives
```bash
# Python - ignore specific line
result = some_function()  # noqa: ARG001

# JavaScript - ignore specific line
// eslint-disable-next-line no-console
console.log('debug');

# SQL - ignore specific rule
-- noqa: disable=L003
SELECT * FROM table;
```

### Performance issues
```bash
# Run only changed files
pre-commit run --files backend/src/shu/api/chat.py

# Skip slow hooks
SKIP=mypy git commit -m "message"

# Disable specific hook temporarily
# Edit .pre-commit-config.yaml and add:
# stages: [manual]
```

## Best Practices

1. **Run locally before pushing** - Use `make lint-fix`
2. **Fix issues immediately** - Don't accumulate linting debt
3. **Use IDE integration** - Format on save
4. **Review warnings** - They often indicate real issues
5. **Update regularly** - Keep tools and rules current
6. **Customize thoughtfully** - Don't disable rules without reason
7. **Document exceptions** - Explain why rules are ignored

## Resources

- **Ruff:** https://docs.astral.sh/ruff/
- **mypy:** https://mypy.readthedocs.io/
- **Bandit:** https://bandit.readthedocs.io/
- **ESLint:** https://eslint.org/docs/
- **Prettier:** https://prettier.io/docs/
- **SQLFluff:** https://docs.sqlfluff.com/
- **markdownlint:** https://github.com/DavidAnson/markdownlint
- **hadolint:** https://github.com/hadolint/hadolint
- **shellcheck:** https://www.shellcheck.net/
- **detect-secrets:** https://github.com/Yelp/detect-secrets
- **pre-commit:** https://pre-commit.com/

## Summary

**Total Tools:** 9 (markdownlint disabled)
**Languages Covered:** Python, JavaScript, SQL, Docker, Shell
**Auto-fix Capable:** 6 tools
**Security Focused:** 2 tools (Bandit, detect-secrets)
**Pre-commit Integrated:** All active tools
**CI/CD Integrated:** All active tools

**Coverage:** Comprehensive linting for all code, configuration, and SQL in the project. Markdown linting disabled to avoid friction with documentation.
