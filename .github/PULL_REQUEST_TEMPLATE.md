## Description
<!-- Provide a brief description of the changes in this PR -->

## Type of Change
<!-- Mark the relevant option with an "x" -->
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update
- [ ] Refactoring (no functional changes)
- [ ] Performance improvement
- [ ] Test coverage improvement

## Related Issues
<!-- Link to related issues using #issue_number -->
Closes #

## Changes Made
<!-- List the main changes made in this PR -->
-
-
-

## Testing
<!-- Describe the tests you ran and how to reproduce them -->
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Manual testing performed
- [ ] All tests passing locally

## Code Quality Checklist
<!-- All items must be checked before merging -->

### Backend (Python)

- [ ] Type hints added to all new functions/classes
- [ ] Docstrings added to all public functions/classes
- [ ] Unit tests written for new code
- [ ] ConfigurationManager used (no hardcoded values)
- [ ] Dependency injection used (no direct instantiation)
- [ ] Timezone-aware datetimes used
- [ ] No breaking migration changes
- [ ] Files end with trailing newlines

### Frontend (React/JavaScript)

- [ ] Functional components with hooks used
- [ ] log.js used instead of console.*
- [ ] React Query used for server state
- [ ] Envelope utilities used (extractDataFromResponse)
- [ ] Material-UI components used
- [ ] Components < 500 LOC
- [ ] Custom hooks for complex logic

### API

- [ ] ShuResponse helper used
- [ ] Envelope format followed
- [ ] Proper HTTP status codes
- [ ] Pydantic schemas defined in schemas/

### Linting

- [ ] `make lint` passes locally
- [ ] Pre-commit hooks pass
- [ ] CI linting checks pass
- [ ] No linting warnings ignored without justification

### Documentation

- [ ] Code comments added where needed
- [ ] README updated (if applicable)
- [ ] API docs updated (if applicable)
- [ ] Migration guide provided (if breaking change)

### Security

- [ ] No hardcoded secrets or credentials
- [ ] No sensitive data in logs
- [ ] Input validation added
- [ ] Authentication/authorization checked

## Screenshots (if applicable)
<!-- Add screenshots for UI changes -->

## Deployment Notes
<!-- Any special deployment considerations? -->
- [ ] Database migrations required
- [ ] Environment variables added/changed
- [ ] Dependencies added/updated
- [ ] Configuration changes needed

## Reviewer Notes
<!-- Anything specific you want reviewers to focus on? -->

## Post-Merge Tasks
<!-- Tasks to complete after merging -->
- [ ] Update documentation
- [ ] Notify stakeholders
- [ ] Monitor logs/metrics
- [ ] Update related issues

---

**By submitting this PR, I confirm that:**

- [ ] I have run `make lint-fix` and all linting checks pass
- [ ] I have tested my changes locally
- [ ] I have followed the coding standards in `docs/policies/DEVELOPMENT_STANDARDS.md`
- [ ] I have read and followed the linting policy in `docs/policies/LINTING_POLICY.md`
