# Shu Local Development Makefile

COMPOSE_FILE ?= deployment/compose/docker-compose.yml

# Version metadata from git (used in build args)
GIT_SHA        := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
VERSION        := $(shell git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || echo 0.0.0-dev)
BUILD_TIMESTAMP:= $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")
DB_RELEASE     := $(shell ls -1 backend/alembic/versions 2>/dev/null | grep -E '^[0-9]{3}_.+\.py$$' | cut -d _ -f1 | sort | tail -n1)

.PHONY: build-api build-fe build-runner build-all

# Build local Docker images
build-api:
	docker build \
	  --build-arg SHU_APP_VERSION=$(VERSION) \
	  --build-arg SHU_GIT_SHA=$(GIT_SHA) \
	  --build-arg SHU_BUILD_TIMESTAMP=$(BUILD_TIMESTAMP) \
	  --build-arg SHU_DB_RELEASE=$(DB_RELEASE) \
	  -f deployment/docker/api/Dockerfile \
	  -t shu-api:latest \
	  -t shu-api:$(VERSION) .

build-fe:
	docker build \
	  --build-arg SHU_APP_VERSION=$(VERSION) \
	  --build-arg SHU_GIT_SHA=$(GIT_SHA) \
	  --build-arg SHU_BUILD_TIMESTAMP=$(BUILD_TIMESTAMP) \
	  -f deployment/docker/frontend/Dockerfile \
	  -t shu-frontend:latest \
	  -t shu-frontend:$(VERSION) .

build-runner:
	docker build \
	  --build-arg SHU_APP_VERSION=$(VERSION) \
	  --build-arg SHU_GIT_SHA=$(GIT_SHA) \
	  --build-arg SHU_BUILD_TIMESTAMP=$(BUILD_TIMESTAMP) \
	  -f deployment/docker/runner/Dockerfile \
	  -t shu-runner:latest \
	  -t shu-runner:$(VERSION) .

build-all: build-api build-fe build-runner

# Docker Compose build targets
# Note: Docker Compose v2 uses buildx by default, no explicit builder management needed
compose-build:
	docker compose -f $(COMPOSE_FILE) --profile frontend build shu-api shu-frontend shu-db-migrate

compose-build-dev:
	docker compose -f $(COMPOSE_FILE) --profile dev build shu-api-dev shu-db-migrate


# Docker Compose targets
# - make up:           API + Postgres + Redis (backend only, inline workers)
# - make up-full:      Full stack including frontend
# - make up-dev:       Backend with hot-reload
# - make up-full-dev:  Full stack with hot-reload backend
# - make up-worker:    Dedicated worker (production)
# - make up-worker-dev: Dedicated worker with hot-reload

.PHONY: up up-full up-dev up-full-dev up-worker up-worker-dev down logs logs-worker ps

up:
	docker compose -f $(COMPOSE_FILE) up -d

up-full:
	docker compose -f $(COMPOSE_FILE) --profile frontend up -d

up-dev:
	docker compose -f $(COMPOSE_FILE) --profile dev up -d shu-api-dev shu-postgres shu-db-migrate redis

up-full-dev:
	docker compose -f $(COMPOSE_FILE) --profile dev up -d shu-api-dev shu-postgres shu-db-migrate redis shu-frontend-dev

# Start dedicated worker (requires redis and db-migrate to be running)
up-worker:
	docker compose -f $(COMPOSE_FILE) --profile worker up -d shu-worker

# Start dedicated worker with hot-reload for development
up-worker-dev:
	docker compose -f $(COMPOSE_FILE) --profile worker-dev up -d shu-worker-dev

# Start workload-specific workers (ingestion, llm, maintenance)
# IMPORTANT: Set SHU_WORKERS_ENABLED=false on shu-api when using these
up-workers:
	docker compose -f $(COMPOSE_FILE) --profile workers up -d shu-worker-ingestion shu-worker-llm shu-worker-maintenance

# Start API with inline workers disabled + workload-specific workers
up-split:
	SHU_WORKERS_ENABLED=false docker compose -f $(COMPOSE_FILE) --profile workers up -d

down:
	docker compose -f $(COMPOSE_FILE) --profile worker --profile worker-dev --profile workers down --remove-orphans || true
	-docker rm -f shu-frontend shu-api-dev shu-worker shu-worker-dev shu-worker-ingestion shu-worker-llm shu-worker-maintenance 2>/dev/null || true

logs:
	docker compose -f $(COMPOSE_FILE) logs -f

logs-worker:
	docker compose -f $(COMPOSE_FILE) logs -f shu-worker shu-worker-dev shu-worker-ingestion shu-worker-llm shu-worker-maintenance

ps:
	docker compose -f $(COMPOSE_FILE) --profile worker --profile worker-dev --profile workers ps

# Linting and formatting targets
.PHONY: lint lint-python lint-frontend format format-python format-frontend lint-fix lint-changed lint-staged lint-uncommitted

# Run all linters
lint: lint-python lint-frontend

# Python linting
lint-python:
	@echo "Running Ruff linter..."
	ruff check backend/
	@echo "Running mypy type checker..."
	mypy backend/src/shu
	@echo "Running Bandit security checker..."
	bandit -c pyproject.toml -r backend/src/shu

# Frontend linting
lint-frontend:
	@echo "Running ESLint..."
	cd frontend && npm run lint

# Format all code
format: format-python format-frontend

# Python formatting
format-python:
	@echo "Running Ruff formatter..."
	ruff format backend/

# Frontend formatting
format-frontend:
	@echo "Running Prettier..."
	cd frontend && npm run format
	@echo "Running ESLint fix..."
	cd frontend && npm run lint:fix

# Auto-fix linting issues
lint-fix:
	@echo "Auto-fixing Python issues..."
	ruff check --fix backend/
	ruff format backend/
	@echo "Formatting frontend with Prettier..."
	cd frontend && npm run format
	@echo "Auto-fixing frontend ESLint issues..."
	cd frontend && npm run lint:fix

# Check only uncommitted changes (modified/untracked files)
lint-uncommitted:
	@echo "Checking uncommitted Python files..."
	@UNCOMMITTED_PY=$$(git status --short | grep -E '^\s*[MAU\?].*\.py$$' | awk '{print $$2}' || true); \
	if [ -n "$$UNCOMMITTED_PY" ]; then \
		echo "Uncommitted files: $$UNCOMMITTED_PY"; \
		echo "$$UNCOMMITTED_PY" | xargs ruff check; \
		echo "$$UNCOMMITTED_PY" | xargs mypy --follow-imports=silent --no-error-summary 2>/dev/null || true; \
	else \
		echo "No uncommitted Python files"; \
	fi
	@echo "Checking uncommitted frontend files..."
	@UNCOMMITTED_JS=$$(git status --short | grep -E '^\s*[MAU\?].*\.(js|jsx|ts|tsx)$$' | awk '{print $$2}' | grep '^frontend/' || true); \
	if [ -n "$$UNCOMMITTED_JS" ]; then \
		echo "Uncommitted files: $$UNCOMMITTED_JS"; \
		FRONTEND_FILES=$$(echo "$$UNCOMMITTED_JS" | sed 's|^frontend/||'); cd frontend && echo "$$FRONTEND_FILES" | xargs npx eslint 2>/dev/null || true; \
	else \
		echo "No uncommitted frontend files"; \
	fi

# Check only changed files (git diff against PR base or main/master)
lint-changed:
	@echo "Checking changed Python files..."
	@BASE=$${GITHUB_BASE_REF:-$$(git show-ref --verify --quiet refs/heads/main && echo main || echo master)}; \
	BRANCH=$$(git rev-parse --abbrev-ref HEAD); \
	CHANGED_PY=$$(git diff --name-only --diff-filter=ACMR $$BASE...$$BRANCH 2>/dev/null | grep '\.py$$' || true); \
	if [ -n "$$CHANGED_PY" ]; then \
		echo "Comparing against: $$BASE"; \
		echo "Changed files: $$CHANGED_PY"; \
		echo "$$CHANGED_PY" | xargs ruff check; \
		echo "$$CHANGED_PY" | xargs mypy --follow-imports=silent --no-error-summary 2>/dev/null || true; \
	else \
		echo "No Python files changed (comparing against $$BASE)"; \
	fi
	@echo "Checking changed frontend files..."
	@BASE=$${GITHUB_BASE_REF:-$$(git show-ref --verify --quiet refs/heads/main && echo main || echo master)}; \
	BRANCH=$$(git rev-parse --abbrev-ref HEAD); \
	CHANGED_JS=$$(git diff --name-only --diff-filter=ACMR $$BASE...$$BRANCH 2>/dev/null | grep -E '\.(js|jsx|ts|tsx)$$' | grep '^frontend/' || true); \
	if [ -n "$$CHANGED_JS" ]; then \
		echo "Changed files: $$CHANGED_JS"; \
		FRONTEND_FILES=$$(echo "$$CHANGED_JS" | sed 's|^frontend/||'); cd frontend && echo "$$FRONTEND_FILES" | xargs npx eslint 2>/dev/null || true; \
	else \
		echo "No frontend files changed"; \
	fi

# Check only staged files (git diff --cached)
lint-staged:
	@echo "Checking staged Python files..."
	@STAGED_PY=$$(git diff --cached --name-only --diff-filter=ACMR | grep '\.py$$' || true); \
	if [ -n "$$STAGED_PY" ]; then \
		echo "Staged files: $$STAGED_PY"; \
		echo "$$STAGED_PY" | xargs ruff check; \
		echo "$$STAGED_PY" | xargs mypy --follow-imports=silent --no-error-summary; \
	else \
		echo "No Python files staged"; \
	fi
	@echo "Checking staged frontend files..."
	@STAGED_JS=$$(git diff --cached --name-only --diff-filter=ACMR | grep -E '\.(js|jsx|ts|tsx)$$' | grep '^frontend/' || true); \
	if [ -n "$$STAGED_JS" ]; then \
		echo "Staged files: $$STAGED_JS"; \
		FRONTEND_FILES=$$(echo "$$STAGED_JS" | sed 's|^frontend/||'); cd frontend && echo "$$FRONTEND_FILES" | xargs npx eslint; \
	else \
		echo "No frontend files staged"; \
	fi

# Pre-commit setup
.PHONY: setup-hooks install-hooks

setup-hooks:
	@echo "Installing pre-commit..."
	pip install pre-commit
	@echo "Installing pre-commit hooks..."
	pre-commit install
	@echo "Running pre-commit on all files..."
	pre-commit run --all-files

install-hooks: setup-hooks

# Testing and coverage
.PHONY: test test-unit test-cov coverage coverage-report coverage-html coverage-open

# Run unit tests
test-unit:
	@echo "Running unit tests..."
	python -m pytest backend/src/tests/unit

# Run tests with coverage
test-cov:
	@echo "Running tests with coverage..."
	python -m pytest backend/src/tests/unit --cov=backend/src/shu --cov-report=term-missing --cov-report=html --cov-report=xml

# Alias for test-cov
coverage: test-cov

# Generate coverage report (after running tests)
coverage-report:
	@echo "Generating coverage report..."
	coverage report --show-missing

# Generate HTML coverage report
coverage-html:
	@echo "Generating HTML coverage report..."
	coverage html
	@echo "Report generated in htmlcov/index.html"

# Open HTML coverage report in browser
coverage-open: coverage-html
	@echo "Opening coverage report..."
	@command -v open >/dev/null 2>&1 && open htmlcov/index.html || \
	command -v xdg-open >/dev/null 2>&1 && xdg-open htmlcov/index.html || \
	echo "Please open htmlcov/index.html in your browser"
