# Linting Configuration Guide

This guide explains how to customize the linting rules for your project.

## Configuration Files

| File | Purpose | Language |
|------|---------|----------|
| `pyproject.toml` | Python linting (Ruff, mypy) | Python |
| `frontend/.eslintrc.json` | JavaScript/React linting | JavaScript |
| `frontend/.prettierrc.json` | Code formatting | JavaScript |
| `.pre-commit-config.yaml` | Pre-commit hooks | All |

## Common Customizations

### 1. Allow console.log in Frontend

**File:** `frontend/.eslintrc.json`

**Current (blocks console.log):**
```json
"rules": {
  "no-console": ["warn", { "allow": ["warn", "error"] }]
}
```

**Option A - Allow all console statements:**
```json
"rules": {
  "no-console": "off"
}
```

**Option B - Allow console.log but make it an error:**
```json
"rules": {
  "no-console": ["error", { "allow": ["log", "warn", "error"] }]
}
```

**Option C - Only warn (don't fail build):**
```json
"rules": {
  "no-console": "warn"
}
```

### 2. Allow Print Statements in Python

**File:** `pyproject.toml`

**Current (blocks print statements):**
```toml
[tool.ruff.lint]
select = [
    "T20",    # flake8-print (catch print statements)
    # ... other rules
]
```

**Option A - Remove T20 entirely:**
```toml
[tool.ruff.lint]
select = [
    # "T20",    # Commented out - allows print everywhere
    # ... other rules
]
```

**Option B - Allow in specific files (already configured for scripts):**
```toml
[tool.ruff.lint.per-file-ignores]
"backend/scripts/*.py" = ["T20"]  # Already there
"backend/src/shu/debug/*.py" = ["T20"]  # Add your own
```

### 3. Disable Timezone-Aware Datetime Requirement

**File:** `pyproject.toml`

**Current (requires timezone-aware datetimes):**
```toml
select = [
    "DTZ",    # flake8-datetimez
]
```

**To disable:**
```toml
select = [
    # "DTZ",    # Commented out - allows naive datetimes
]
```

**Or ignore specific rules:**
```toml
ignore = [
    "DTZ001",  # Allow datetime.now() without timezone
    "DTZ005",  # Allow datetime.now() without timezone
]
```

### 4. Change Line Length

**Python - File:** `pyproject.toml`
```toml
[tool.ruff]
line-length = 120  # Change from 100 to 120
```

**Frontend - File:** `frontend/.prettierrc.json`
```json
{
  "printWidth": 120  // Change from 100 to 120
}
```

### 5. Disable Type Checking

**File:** `pyproject.toml`

**Option A - Make it less strict:**
```toml
[tool.mypy]
disallow_untyped_defs = false  # Don't require type hints
disallow_incomplete_defs = false
```

**Option B - Disable mypy in pre-commit:**
Edit `.pre-commit-config.yaml` and comment out the mypy hook:
```yaml
  # - repo: https://github.com/pre-commit/mirrors-mypy
  #   rev: v1.13.0
  #   hooks:
  #     - id: mypy
```

### 6. Disable Specific ESLint Rules

**File:** `frontend/.eslintrc.json`

```json
"rules": {
  "react/prop-types": "off",  // Already disabled
  "react-hooks/exhaustive-deps": "off",  // Disable dependency warnings
  "no-unused-vars": "off",  // Allow unused variables
  "prefer-const": "off"  // Allow 'let' everywhere
}
```

### 7. Allow Longer Functions/Classes

**File:** `pyproject.toml`

```toml
[tool.ruff.lint]
ignore = [
    "PLR0913",  # Too many arguments (already ignored)
    "PLR0915",  # Too many statements
    "C901",     # Too complex
]
```

### 8. Disable Pre-commit Hooks Temporarily

**Per-commit basis:**
```bash
git commit --no-verify -m "emergency fix"
```

**Disable specific hook:**
Edit `.pre-commit-config.yaml`:
```yaml
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.4
    hooks:
      - id: ruff
        stages: [manual]  # Only run when explicitly called
```

### 9. Relax Rules for Tests

**File:** `pyproject.toml`

```toml
[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["ARG", "PLR2004", "S101"]  # Add more rules
"backend/src/tests/**/*.py" = ["ARG", "PLR2004", "S101"]
```

### 10. Custom Ignore Patterns

**Python - File:** `pyproject.toml`
```toml
[tool.ruff]
exclude = [
    "migrations/versions",
    "generated_code",
    "third_party",
]
```

**Frontend - File:** `frontend/.eslintignore`
Create this file:
```
build/
node_modules/
*.config.js
```

## Rule Reference

### Python (Ruff) Rule Categories

| Code | Category | Description | Recommendation |
|------|----------|-------------|----------------|
| E, W | pycodestyle | PEP8 style | Keep enabled |
| F | pyflakes | Logical errors | Keep enabled |
| I | isort | Import sorting | Keep enabled |
| N | pep8-naming | Naming conventions | Keep enabled |
| UP | pyupgrade | Modern Python syntax | Keep enabled |
| B | flake8-bugbear | Bug detection | Keep enabled |
| DTZ | datetimez | Timezone awareness | Optional |
| T20 | flake8-print | No print statements | Optional |
| ARG | unused-arguments | Unused args | Optional for tests |
| PLR | pylint refactor | Code quality | Some can be relaxed |

Full list: https://docs.astral.sh/ruff/rules/

### ESLint Rules

| Rule | Description | Recommendation |
|------|-------------|----------------|
| no-console | No console statements | Optional |
| no-unused-vars | No unused variables | Keep enabled |
| react-hooks/rules-of-hooks | Hook rules | Keep enabled |
| react-hooks/exhaustive-deps | Hook dependencies | Keep enabled |
| prefer-const | Use const when possible | Keep enabled |

Full list: https://eslint.org/docs/rules/

## Severity Levels

### ESLint
- `"off"` or `0` - Disabled
- `"warn"` or `1` - Warning (doesn't fail build)
- `"error"` or `2` - Error (fails build)

### Ruff
- Add to `select` - Enable rule
- Add to `ignore` - Disable rule
- Add to `per-file-ignores` - Disable for specific files

## Testing Configuration Changes

After changing configuration:

```bash
# Validate configuration
pre-commit validate-config

# Test on specific file
ruff check backend/src/shu/api/chat.py
eslint frontend/src/App.js

# Test on all files (dry run)
ruff check backend/ --no-fix
cd frontend && npm run lint
```

## Recommended Configurations

### Strict (Production)
- All rules enabled
- Type checking required
- No console/print statements
- Timezone-aware datetimes

### Moderate (Default - Current)
- Most rules enabled
- Type checking required
- Console.warn/error allowed
- Some complexity rules relaxed

### Relaxed (Development)
- Basic rules only (E, W, F)
- Type checking optional
- Console statements allowed
- Complexity rules disabled

## Migration Strategy

If you have a large codebase with many violations:

1. **Start with warnings only** - Change errors to warnings
2. **Fix incrementally** - Fix one rule category at a time
3. **Use per-file-ignores** - Ignore legacy code, enforce on new code
4. **Gradually tighten** - Enable more rules over time

Example incremental approach:
```toml
# Week 1: Only critical rules
select = ["E", "F"]

# Week 2: Add imports and naming
select = ["E", "F", "I", "N"]

# Week 3: Add bug detection
select = ["E", "F", "I", "N", "B"]

# Week 4: Add all rules
select = ["E", "F", "I", "N", "B", "UP", "C4", "DTZ", "T20", ...]
```

## Getting Help

- **Ruff rules:** https://docs.astral.sh/ruff/rules/
- **ESLint rules:** https://eslint.org/docs/rules/
- **Prettier options:** https://prettier.io/docs/en/options.html
- **Pre-commit hooks:** https://pre-commit.com/hooks.html

## Common Questions

**Q: Can I disable linting for a single line?**

Python:
```python
result = some_function()  # noqa: ARG001
```

JavaScript:
```javascript
// eslint-disable-next-line no-console
console.log('debug');
```

**Q: Can I have different rules for different directories?**

Yes, use per-file-ignores in `pyproject.toml` or create separate `.eslintrc.json` files in subdirectories.

**Q: How do I see what rule is being violated?**

```bash
# Python - shows rule codes
ruff check backend/

# Frontend - shows rule names
npm run lint
```

**Q: Can I auto-fix only specific rules?**

```bash
# Python - fix only import sorting
ruff check --select I --fix backend/

# Frontend - fix only specific rule
eslint --fix --rule 'no-console: off' src/
```
