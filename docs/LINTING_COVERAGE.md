# Linting Coverage - What's Automated vs Manual

This document maps your coding standards to what can be automatically enforced by linters vs what requires manual code review.

## ✅ Fully Automated (Linters Enforce)

### Python Backend

| Standard | Tool | Rule | Status |
|----------|------|------|--------|
| Type hints on all functions | mypy | `disallow_untyped_defs` | ✅ Enforced |
| Docstrings on public functions | Ruff | `D` (pydocstyle) | ✅ Enforced |
| PEP8 compliance | Ruff | `E`, `W` | ✅ Enforced |
| Import sorting | Ruff | `I` (isort) | ✅ Enforced |
| Imports at top of file | Ruff | `E402` | ✅ Enforced |
| Trailing newlines | Pre-commit | `end-of-file-fixer` | ✅ Enforced |
| Trailing whitespace | Pre-commit | `trailing-whitespace` | ✅ Enforced |
| Timezone-aware datetimes | Ruff | `DTZ` | ✅ Enforced |
| No print statements | Ruff | `T20` | ✅ Enforced |
| Unused imports/variables | Ruff | `F` (pyflakes) | ✅ Enforced |
| Commented-out code | Ruff | `ERA` | ✅ Enforced |
| Security issues | Ruff | `S` (bandit) | ✅ Enforced |
| Code complexity | Ruff | `PLR` (pylint) | ⚠️ Warned |
| Naming conventions | Ruff | `N` (pep8-naming) | ✅ Enforced |

### Frontend (React/JavaScript)

| Standard | Tool | Rule | Status |
|----------|------|------|--------|
| Functional components | ESLint | Manual review | ⚠️ Partial |
| No console.log | ESLint | `no-console` | ✅ Enforced |
| React hooks rules | ESLint | `react-hooks/*` | ✅ Enforced |
| Unused variables | ESLint | `no-unused-vars` | ✅ Enforced |
| Code formatting | Prettier | All rules | ✅ Enforced |
| Components < 500 LOC | ESLint | `max-lines` | ⚠️ Warned |
| Function complexity | ESLint | `complexity` | ⚠️ Warned |
| Magic numbers | ESLint | `no-magic-numbers` | ⚠️ Warned |
| Envelope access pattern | ESLint | Custom rule | ✅ Enforced |

### General

| Standard | Tool | Rule | Status |
|----------|------|------|--------|
| No secrets in code | Pre-commit | `detect-secrets` | ✅ Enforced |
| YAML syntax | Pre-commit | `check-yaml` | ✅ Enforced |
| No large files | Pre-commit | `check-added-large-files` | ✅ Enforced |
| No merge conflicts | Pre-commit | `check-merge-conflict` | ✅ Enforced |
| LF line endings | Pre-commit | `mixed-line-ending` | ✅ Enforced |

## ⚠️ Partially Automated (Warnings Only)

These rules generate warnings but don't fail the build. They should be reviewed during code review.

### Python

| Standard | Why Warning Only | Review Focus |
|----------|------------------|--------------|
| Too many arguments | Can be legitimate | Check if refactoring needed |
| Code complexity | Context-dependent | Check if simplification possible |
| Magic numbers | Many false positives | Check if constants needed |

### Frontend

| Standard | Why Warning Only | Review Focus |
|----------|------------------|--------------|
| File length (500 LOC) | Gradual migration | Check if splitting makes sense |
| Function length | Context-dependent | Check if refactoring needed |
| Complexity | Context-dependent | Check if simplification possible |

## ❌ Manual Review Required

These standards cannot be automatically enforced and require human code review.

### Architecture & Design

| Standard | Why Manual | Review Checklist |
|----------|------------|------------------|
| No hardcoded config values | Context-dependent | ✓ ConfigurationManager used? |
| Dependency injection | Pattern detection hard | ✓ No direct instantiation? |
| Separation of concerns | Architectural | ✓ Routers have no business logic? |
| No Pydantic models in routers | Pattern detection | ✓ Models in schemas/? |
| Repository pattern used | Architectural | ✓ No SQL in services? |
| Constants in separate files | Subjective threshold | ✓ Large constants extracted? |

### Testing

| Standard | Why Manual | Review Checklist |
|----------|------------|------------------|
| Unit tests for new code | Coverage tools help | ✓ Tests written? |
| Test quality | Subjective | ✓ Tests meaningful? |
| Integration tests | Coverage tools help | ✓ API endpoints tested? |
| Test naming convention | Partially automated | ✓ test_*.py naming? |

### Frontend Patterns

| Standard | Why Manual | Review Checklist |
|----------|------------|------------------|
| React Query for server state | Pattern detection hard | ✓ No direct axios in components? |
| Material-UI components | Too many variations | ✓ MUI used consistently? |
| Custom hooks for complex logic | Subjective | ✓ Logic extracted to hooks? |
| Hook naming (use prefix) | Partially automated | ✓ Hooks start with 'use'? |

### Security

| Standard | Why Manual | Review Checklist |
|----------|------------|------------------|
| No credentials in logs | Context-dependent | ✓ Sensitive data sanitized? |
| Input validation | Business logic | ✓ Validation appropriate? |
| Authentication checks | Business logic | ✓ Auth required where needed? |
| Self-registered users inactive | Business logic | ✓ is_active=False default? |

### Database

| Standard | Why Manual | Review Checklist |
|----------|------------|------------------|
| No breaking migrations | Requires understanding | ✓ Backward compatible? |
| Additive migrations only | Requires understanding | ✓ No drops/renames? |
| Eager loading used | Pattern detection hard | ✓ selectinload used? |

## 🔧 Configuration to Improve Coverage

### Enable Stricter Rules (Optional)

You can make warnings into errors to enforce them:

**Python - `pyproject.toml`:**
```toml
[tool.ruff.lint]
ignore = [
    # Remove these to enforce:
    # "PLR0913",  # Too many arguments
    # "PLR2004",  # Magic value comparison
]
```

**Frontend - `frontend/.eslintrc.json`:**
```json
"rules": {
  "max-lines": ["error", { "max": 500 }],  // Change warn to error
  "complexity": ["error", 10]  // Change warn to error
}
```

### Add Custom Rules

For patterns that are project-specific, you can add custom ESLint rules:

**Example: Enforce ConfigurationManager usage**
```javascript
// Would require custom ESLint plugin
"no-direct-config-instantiation": "error"
```

This is complex and may not be worth the effort for all patterns.

## 📊 Coverage Summary

| Category | Automated | Warned | Manual | Total |
|----------|-----------|--------|--------|-------|
| Python Backend | 14 | 3 | 6 | 23 |
| Frontend | 9 | 3 | 5 | 17 |
| Architecture | 0 | 0 | 6 | 6 |
| Testing | 0 | 0 | 4 | 4 |
| Security | 1 | 0 | 4 | 5 |
| Database | 0 | 0 | 3 | 3 |
| **Total** | **24** | **6** | **28** | **58** |

**Automation Rate: 41% fully automated, 10% warned, 49% manual**

## 🎯 Recommendations

### High Priority for Automation
These could be automated with more effort:

1. **Test file naming** - Custom pre-commit hook
2. **Hook naming (use prefix)** - ESLint rule exists
3. **No SQL in services** - Custom AST analysis
4. **Constants extraction** - Threshold-based rule

### Not Worth Automating
These require human judgment:

1. **Code quality** - Subjective
2. **Architecture patterns** - Context-dependent
3. **Test quality** - Requires understanding
4. **Business logic validation** - Domain-specific

### Improve Manual Review
For standards that can't be automated:

1. **Use PR template** - Checklist in `.github/PULL_REQUEST_TEMPLATE.md` ✅ Done
2. **Code review guidelines** - Document what to look for
3. **Examples** - Show good vs bad patterns
4. **Training** - Educate team on patterns

## 📝 Using This Information

### For Developers
- **Green (✅)**: Linters will catch these automatically
- **Yellow (⚠️)**: Pay attention to warnings
- **Red (❌)**: Extra care needed, will be reviewed

### For Reviewers
Focus code review time on:
1. Architecture and design patterns
2. Business logic correctness
3. Test quality and coverage
4. Security considerations
5. Database migration safety

Don't spend time on:
1. Code formatting (Prettier handles it)
2. Import sorting (Ruff handles it)
3. Type hints (mypy catches missing ones)
4. Basic syntax (linters catch it)

## 🔄 Continuous Improvement

As linting tools evolve:
1. **Review quarterly** - Check for new rules
2. **Add rules gradually** - Don't overwhelm team
3. **Measure impact** - Track time saved in reviews
4. **Gather feedback** - Ask team about pain points

## 📚 Related Documentation

- **Configuration:** `docs/LINTING_CONFIGURATION.md`
- **Quick Start:** `LINTING_QUICKSTART.md`
- **Full Guide:** `docs/LINTING_GUIDE.md`
- **Policy:** `docs/policies/LINTING_POLICY.md`
- **Coding Standards:** `docs/policies/DEVELOPMENT_STANDARDS.md`
