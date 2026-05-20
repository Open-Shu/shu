# Shu Local Development Makefile

COMPOSE_FILE ?= deployment/compose/docker-compose.yml

# Version metadata from git (used in build args)
GIT_SHA        := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
VERSION        := $(shell git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || echo 0.0.0-dev)
BUILD_TIMESTAMP:= $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")
DB_RELEASE     := $(shell ls -1 backend/alembic/versions 2>/dev/null | grep -E '^[0-9]{3}_.+\.py$$' | cut -d _ -f1 | sort | tail -n1)

.PHONY: build-api build-api-slim build-fe build-runner build-all \
        publish-api publish-api-slim publish-fe publish-runner publish-all \
        _require-registry

# ----------------------------------------------------------------------------
# Local image builds (no push).
#
# `build-api` is the standard image. It includes local text-embedding and
# OCR inference engines (sentence-transformers, torch, transformers, easyocr,
# pytesseract) so the app can run those workloads without external API
# dependencies.
#
# `build-api-slim` excludes those packages. Use it for deployments that
# route embedding and OCR through external APIs (e.g. SHU_LOCAL_EMBEDDING_ENABLED=false
# plus an external OCR engine), where the local libraries are dead weight.
# Tag suffix `-slim` keeps the two variants distinct in the local image cache.
# ----------------------------------------------------------------------------

build-api:
	docker build \
	  --build-arg SHU_APP_VERSION=$(VERSION) \
	  --build-arg SHU_GIT_SHA=$(GIT_SHA) \
	  --build-arg SHU_BUILD_TIMESTAMP=$(BUILD_TIMESTAMP) \
	  --build-arg SHU_DB_RELEASE=$(DB_RELEASE) \
	  -f deployment/docker/api/Dockerfile \
	  -t shu-api:latest \
	  -t shu-api:$(VERSION) .

build-api-slim:
	docker build \
	  --build-arg INCLUDE_LOCAL_INFERENCE=0 \
	  --build-arg SHU_APP_VERSION=$(VERSION) \
	  --build-arg SHU_GIT_SHA=$(GIT_SHA) \
	  --build-arg SHU_BUILD_TIMESTAMP=$(BUILD_TIMESTAMP) \
	  --build-arg SHU_DB_RELEASE=$(DB_RELEASE) \
	  -f deployment/docker/api/Dockerfile \
	  -t shu-api:$(VERSION)-slim \
	  -t shu-api:latest-slim .

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

# ----------------------------------------------------------------------------
# Build + push to a container registry (multi-platform-aware).
#
# Architecture policy (SHU-779):
#   * `build-*` targets above produce HOST-ARCH images for local `docker run`.
#     On Apple Silicon Macs that's `linux/arm64`. Fast iteration, no QEMU.
#   * `publish-*` targets below produce `linux/amd64` images via `docker buildx
#     build --platform linux/amd64 --push`. This matches the production
#     consumer (DOKS amd64 nodes). Builds on Apple Silicon use QEMU
#     emulation for the amd64 step, which is slow but correct.
#   * When an arm64-consuming cluster materializes (sovereignty-tier ARM
#     DOKS, customer on-prem ARM), bump the specific publish target to
#     `--platform linux/amd64,linux/arm64`. Don't pre-emptively multi-arch
#     everything — the second arch doubles build time and registry storage
#     for no benefit when the consumer base is amd64-only.
#
# Buildx uses --push to upload directly to the registry without loading
# the image into the local docker daemon. No separate `docker tag` /
# `docker push` steps; the -t flags become tags in the registry.
#
# Usage:
#   make publish-api      REGISTRY=<your-registry-prefix>
#   make publish-api-slim REGISTRY=<your-registry-prefix>
#   make publish-fe       REGISTRY=<your-registry-prefix>
#   make publish-runner   REGISTRY=<your-registry-prefix>
#   make publish-all      REGISTRY=<your-registry-prefix>
#
# Each image gets three tags pushed:
#   :$(VERSION)              — e.g. 1.2.3 (mutable; latest build for that version wins)
#   :$(VERSION)-$(GIT_SHA)   — e.g. 1.2.3-abc1234 (immutable; bisect-friendly)
#   :latest                  — convenience; do not pin downstream consumers to this
#
# Authenticate to the registry (`docker login <registry>` or `doctl registry
# login` for DOCR) out-of-band before invoking these targets. buildx uses
# the local docker config's registry credentials.
# ----------------------------------------------------------------------------

PUBLISH_PLATFORMS ?= linux/amd64

_require-registry:
	@if [ -z "$(REGISTRY)" ]; then \
	  echo "ERROR: REGISTRY is required, e.g. make publish-api REGISTRY=ghcr.io/my-org" ; \
	  exit 1 ; \
	fi

publish-api: _require-registry
	docker buildx build --platform $(PUBLISH_PLATFORMS) --push \
	  --build-arg SHU_APP_VERSION=$(VERSION) \
	  --build-arg SHU_GIT_SHA=$(GIT_SHA) \
	  --build-arg SHU_BUILD_TIMESTAMP=$(BUILD_TIMESTAMP) \
	  --build-arg SHU_DB_RELEASE=$(DB_RELEASE) \
	  -f deployment/docker/api/Dockerfile \
	  -t $(REGISTRY)/shu-api:$(VERSION) \
	  -t $(REGISTRY)/shu-api:$(VERSION)-$(GIT_SHA) \
	  -t $(REGISTRY)/shu-api:latest .

publish-api-slim: _require-registry
	docker buildx build --platform $(PUBLISH_PLATFORMS) --push \
	  --build-arg INCLUDE_LOCAL_INFERENCE=0 \
	  --build-arg SHU_APP_VERSION=$(VERSION) \
	  --build-arg SHU_GIT_SHA=$(GIT_SHA) \
	  --build-arg SHU_BUILD_TIMESTAMP=$(BUILD_TIMESTAMP) \
	  --build-arg SHU_DB_RELEASE=$(DB_RELEASE) \
	  -f deployment/docker/api/Dockerfile \
	  -t $(REGISTRY)/shu-api:$(VERSION)-slim \
	  -t $(REGISTRY)/shu-api:$(VERSION)-$(GIT_SHA)-slim \
	  -t $(REGISTRY)/shu-api:latest-slim .

publish-fe: _require-registry
	docker buildx build --platform $(PUBLISH_PLATFORMS) --push \
	  --build-arg SHU_APP_VERSION=$(VERSION) \
	  --build-arg SHU_GIT_SHA=$(GIT_SHA) \
	  --build-arg SHU_BUILD_TIMESTAMP=$(BUILD_TIMESTAMP) \
	  -f deployment/docker/frontend/Dockerfile \
	  -t $(REGISTRY)/shu-frontend:$(VERSION) \
	  -t $(REGISTRY)/shu-frontend:$(VERSION)-$(GIT_SHA) \
	  -t $(REGISTRY)/shu-frontend:latest .

publish-runner: _require-registry
	docker buildx build --platform $(PUBLISH_PLATFORMS) --push \
	  --build-arg SHU_APP_VERSION=$(VERSION) \
	  --build-arg SHU_GIT_SHA=$(GIT_SHA) \
	  --build-arg SHU_BUILD_TIMESTAMP=$(BUILD_TIMESTAMP) \
	  -f deployment/docker/runner/Dockerfile \
	  -t $(REGISTRY)/shu-runner:$(VERSION) \
	  -t $(REGISTRY)/shu-runner:$(VERSION)-$(GIT_SHA) \
	  -t $(REGISTRY)/shu-runner:latest .

publish-all: publish-api publish-api-slim publish-fe publish-runner

# Docker Compose build targets
# Note: Docker Compose v2 uses buildx by default, no explicit builder management needed
compose-build:
	docker compose -f $(COMPOSE_FILE) --profile frontend build shu-api shu-frontend shu-db-migrate

compose-build-dev:
	docker compose -f $(COMPOSE_FILE) --profile dev build shu-api-dev shu-db-migrate


# Docker Compose targets
# - make up:           API + Postgres + Redis (backend only, inline workers)
# - make up-full:           Full stack including frontend
# - make up-dev:            Backend with hot-reload
# - make up-full-dev:       Full stack with hot-reload backend
# - make up-dev-slim:       Backend with hot-reload, slim image (external embedding only)
# - make up-full-dev-slim:  Full stack with hot-reload backend, slim image
# - make up-worker:         Dedicated worker (production)
# - make up-worker-dev:     Dedicated worker with hot-reload

.PHONY: up up-full up-dev up-full-dev up-dev-slim up-full-dev-slim up-worker up-worker-dev up-workers up-split up-dev-split down logs logs-worker ps enable-bm25

up:
	docker compose -f $(COMPOSE_FILE) up -d

up-full:
	docker compose -f $(COMPOSE_FILE) --profile frontend up -d

up-dev:
	docker compose -f $(COMPOSE_FILE) --profile dev up -d shu-api-dev shu-postgres shu-db-migrate redis

up-full-dev:
	docker compose -f $(COMPOSE_FILE) --profile dev up -d shu-api-dev shu-postgres shu-db-migrate redis shu-frontend-dev

# Slim API image (no torch / sentence-transformers / pytesseract). Requires an
# external embedding model registered in llm_models — slim bakes
# SHU_LOCAL_EMBEDDING_ENABLED=false. Mutually exclusive with up-dev / up-full-dev
# (port 8000 + shu-api-dev network alias). Run `make down` between switches.
up-dev-slim:
	docker compose -f $(COMPOSE_FILE) --profile dev-slim up -d shu-api-dev-slim shu-postgres shu-db-migrate redis

up-full-dev-slim:
	docker compose -f $(COMPOSE_FILE) --profile dev-slim --profile dev up -d shu-api-dev-slim shu-postgres shu-db-migrate redis shu-frontend-dev

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

# Start dev API with inline workers disabled + workload-specific workers
up-dev-split:
	SHU_WORKERS_ENABLED=false docker compose -f $(COMPOSE_FILE) --profile dev --profile workers up -d shu-api-dev shu-postgres shu-db-migrate redis shu-worker-ingestion shu-worker-llm shu-worker-maintenance

down:
	docker compose -f $(COMPOSE_FILE) --profile dev --profile dev-slim --profile frontend --profile worker --profile worker-dev --profile workers down --remove-orphans || true
	-docker rm -f shu-frontend shu-frontend-dev shu-api-dev shu-api-dev-slim shu-worker shu-worker-dev shu-worker-ingestion shu-worker-llm shu-worker-maintenance 2>/dev/null || true

logs:
	docker compose -f $(COMPOSE_FILE) logs -f

logs-worker:
	docker compose -f $(COMPOSE_FILE) logs -f shu-worker shu-worker-dev shu-worker-ingestion shu-worker-llm shu-worker-maintenance

ps:
	docker compose -f $(COMPOSE_FILE) --profile worker --profile worker-dev --profile workers ps

# Enable BM25 full-text search (requires ParadeDB pg_search extension installed in PostgreSQL)
# Run this after installing pg_search to create the BM25 index if the migration ran without it.
# Derives the psql connection URL from SHU_DATABASE_URL in .env (strips the +asyncpg driver).
enable-bm25:
	$(eval DB_URL := $(shell grep -m1 '^SHU_DATABASE_URL' .env 2>/dev/null | cut -d= -f2- | tr -d '"' | sed 's|+asyncpg||'))
	@if [ -z "$(DB_URL)" ]; then echo "ERROR: SHU_DATABASE_URL not found in .env"; exit 1; fi
	@echo "Connecting to database from SHU_DATABASE_URL..."
	@echo "Checking for pg_search extension..."
	@psql "$(DB_URL)" -tAc "SELECT 1 FROM pg_extension WHERE extname = 'pg_search'" | grep -q 1 || \
		{ echo "ERROR: pg_search extension not installed. Install ParadeDB first."; exit 1; }
	@echo "pg_search found. Creating BM25 index (if not exists)..."
	@psql "$(DB_URL)" -c " \
		DO \$$\$$ \
		BEGIN \
			IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_documents_bm25') THEN \
				EXECUTE 'CREATE INDEX ix_documents_bm25 ON documents \
					USING bm25 ( \
						id, \
						(title::pdb.simple(''stemmer=english'', ''stopwords_language=english'')), \
						(content::pdb.simple(''stemmer=english'', ''stopwords_language=english'')) \
					) \
					WITH (key_field=''id'')'; \
				RAISE NOTICE 'BM25 index created successfully.'; \
			ELSE \
				RAISE NOTICE 'BM25 index already exists — nothing to do.'; \
			END IF; \
		END \$$\$$;"
	@echo "Done. Set SHU_MULTI_SURFACE_BM25_WEIGHT to a value > 0 (e.g. 0.15) to enable BM25 in search."

# Linting and formatting targets
.PHONY: lint lint-python lint-frontend format format-python format-frontend lint-fix lint-changed lint-staged lint-uncommitted lint-pr

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

# Run pre-commit hooks on changed files (same as GHA/PR checks)
# This runs ALL the hooks that run in CI: ruff, ruff-format, bandit, detect-secrets, etc.
lint-pr:
	@echo "Running pre-commit hooks on changed files (same as PR checks)..."
	@if ! command -v pre-commit >/dev/null 2>&1; then \
		echo "Error: pre-commit not found. Run 'make setup-hooks' first."; \
		exit 1; \
	fi
	@BASE=$${GITHUB_BASE_REF:-$$(git show-ref --verify --quiet refs/heads/main && echo main || echo master)}; \
	BRANCH=$$(git rev-parse --abbrev-ref HEAD); \
	echo "Comparing against base branch: $$BASE"; \
	pre-commit run --from-ref $$BASE --to-ref $$BRANCH

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
