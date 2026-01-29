# Linting and Code Quality Guide

This guide explains how to use the automated linting and formatting tools in the Shu project.

## Quick Start for New Team Members

### 1. Install Dependencies

**Python (Backend):**

```bash
# Activate your conda environment first
source /opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh
conda activate shu

# Install linting tools
pip install ruff mypy pre-commit
```

**Node.js (Frontend):**

```bash
cd frontend
npm install
```

### 2. Set Up Pre-commit Hooks (Recommended)

This automatically runs linters before every commit:

```bash
# From repo root
make setup-hooks
```

Or manually:

```bash
pip install pre-commit
pre-commit install
```

### 3. Run Linters Manually

**Check everything:**

```bash
make lint
```

**Auto-fix issues:**

```bash
make lint-fix
```

**Python only:**

```bash
make lint-python
make format-python
```

**Frontend only:**

```bash
make lint-frontend
make format-frontend
```

## Tools Overview

### Python Stack

#### Ruff (Primary Linter + Formatter)

- **What it does:** Combines linting and formatting in one fast tool
- **Replaces:** black, isort, flake8, pylint
- **Speed:** 10-100x faster than traditional tools
- **Configuration:** `pyproject.toml`

**Commands:**

```bash
# Check for issues
ruff check backend/

# Auto-fix issues
ruff check --fix backend/

# Format code
ruff format backend/
```

#### mypy (Type Checker)

- **What it does:** Static type checking for Python
- **Enforces:** Type hints on all functions (required by our standards)
- **Configuration:** `pyproject.toml`

**Commands:**

```bash
# Check types
mypy backend/src/shu

# Check specific file
mypy backend/src/shu/api/chat.py
```

### Frontend Stack

#### ESLint (JavaScript/React Linter)

- **What it does:** Finds problems in JavaScript/React code
- **Configuration:** `frontend/.eslintrc.json`

**Commands:**

```bash
cd frontend

# Check for issues
npm run lint

# Auto-fix issues
npm run lint:fix
```

#### Prettier (Code Formatter)

- **What it does:** Enforces consistent code style
- **Configuration:** `frontend/.prettierrc.json`

**Commands:**

```bash
cd frontend

# Format all files
npm run format

# Check formatting without changing files
npm run format:check
```

## Pre-commit Hooks

Pre-commit hooks run automatically before each commit to catch issues early.

### What Gets Checked

1. **Python:**
   - Ruff linting (auto-fixes)
   - Ruff formatting
   - mypy type checking

2. **Frontend:**
   - ESLint (auto-fixes)
   - Prettier formatting

3. **General:**
   - Trailing whitespace
   - End-of-file newlines
   - YAML syntax
   - Large files (>1MB)
   - Merge conflicts
   - Secrets detection

### Manual Pre-commit Run

```bash
# Run on all files
pre-commit run --all-files

# Run on staged files only
pre-commit run

# Run specific hook
pre-commit run ruff --all-files
```

### Skipping Hooks (Emergency Only)

```bash
# Skip all hooks (NOT RECOMMENDED)
git commit --no-verify -m "message"
```

## IDE Integration

### VS Code

**Python (Ruff):**

```json
{
  "python.linting.enabled": true,
  "python.linting.ruffEnabled": true,
  "python.formatting.provider": "none",
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.fixAll.ruff": true,
      "source.organizeImports.ruff": true
    }
  }
}
```

**JavaScript/React (ESLint + Prettier):**

```json
{
  "eslint.enable": true,
  "editor.formatOnSave": true,
  "[javascript]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[javascriptreact]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  }
}
```

**Extensions to install:**

- Ruff (charliermarsh.ruff)
- ESLint (dbaeumer.vscode-eslint)
- Prettier (esbenp.prettier-vscode)

### PyCharm/IntelliJ

1. **Ruff:** Install Ruff plugin from marketplace
2. **mypy:** Configure as external tool
3. **ESLint:** Built-in, enable in Settings → Languages → JavaScript → Code Quality Tools
4. **Prettier:** Built-in, enable in Settings → Languages → JavaScript → Prettier

## CI/CD Integration

Linting runs automatically on every pull request via GitHub Actions.

**Workflow file:** `.github/workflows/lint.yml`

**What it checks:**

- Python: Ruff + mypy
- Frontend: ESLint + Prettier
- Pre-commit hooks

**Local simulation:**

```bash
# Run the same checks as CI
make lint
```

## Common Issues and Solutions

### Python: Import Order

**Problem:** Imports not in correct order

**Solution:** Ruff auto-fixes this

```bash
ruff check --fix backend/
```

### Python: Missing Type Hints

**Problem:** mypy complains about missing type hints

**Solution:** Add type hints to all functions

```python
# Before
def process_data(data):
    return data

# After
def process_data(data: List[Dict]) -> List[Dict]:
    return data
```

### Python: Timezone-Aware Datetimes

**Problem:** Ruff DTZ rules flag naive datetimes

**Solution:** Always use timezone-aware datetimes

```python
from datetime import datetime, timezone

# Wrong
now = datetime.now()

# Correct
now = datetime.now(timezone.utc)
```

### Frontend: Console Statements

**Problem:** ESLint flags console.log statements

**Solution:** Use log.js utility

```javascript
// Wrong
console.log('debug info');

// Correct
import log from '../utils/log';
log.debug('debug info');
```

### Frontend: Envelope Access

**Problem:** Custom ESLint rule flags response.data.data

**Solution:** Use extractDataFromResponse utility

```javascript
// Wrong
const data = response.data.data;

// Correct
import { extractDataFromResponse } from '../services/api';
const data = extractDataFromResponse(response);
```

## Configuration Files Reference

- **Python:** `pyproject.toml` - Ruff, mypy, pytest config
- **Frontend ESLint:** `frontend/.eslintrc.json`
- **Frontend Prettier:** `frontend/.prettierrc.json`
- **Pre-commit:** `.pre-commit-config.yaml` (hidden file in root)
- **Makefile:** `Makefile` - Convenience commands

**Want to customize rules?** See [LINTING_CONFIGURATION.md](./LINTING_CONFIGURATION.md) for detailed configuration options.

## Best Practices

1. **Run linters before committing** - Use pre-commit hooks or `make lint-fix`
2. **Fix issues immediately** - Don't accumulate linting debt
3. **Use auto-fix** - Most issues can be fixed automatically
4. **IDE integration** - Set up your IDE to lint on save
5. **Check CI results** - Don't merge PRs with linting failures

## Troubleshooting

### Pre-commit hooks not running

```bash
# Reinstall hooks
pre-commit uninstall
pre-commit install
```

### Ruff not found

```bash
# Ensure you're in the right conda environment
conda activate shu
pip install ruff
```

### ESLint errors in frontend

```bash
cd frontend
npm install  # Ensure dependencies are installed
npm run lint:fix  # Auto-fix issues
```

### mypy cache issues

```bash
# Clear mypy cache
rm -rf .mypy_cache
mypy backend/src/shu
```

## Getting Help

- **Ruff docs:** <https://docs.astral.sh/ruff/>
- **mypy docs:** <https://mypy.readthedocs.io/>
- **ESLint docs:** <https://eslint.org/docs/>
- **Prettier docs:** <https://prettier.io/docs/>
- **Pre-commit docs:** <https://pre-commit.com/>

## Summary

**For new team members:**

1. Run `make setup-hooks` once
2. Code normally - hooks run automatically
3. If hooks fail, run `make lint-fix` to auto-fix
4. Commit again

**For quick checks:**

```bash
make lint        # Check everything
make lint-fix    # Fix everything
```

That's it! The tools handle the rest automatically.
