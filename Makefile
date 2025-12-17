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
	docker compose -f $(COMPOSE_FILE) --profile frontend --profile dev build shu-api-dev shu-frontend shu-db-migrate


# Docker Compose targets
# - make up:      API + Postgres + Redis (backend only)
# - make up-full: Full stack including frontend
# - make up-dev:  Backend with hot-reload (port 8001)
# - make up-full-dev: Full stack with hot-reload backend

.PHONY: up up-full up-dev up-full-dev down logs ps

up:
	docker compose -f $(COMPOSE_FILE) up -d

up-full:
	docker compose -f $(COMPOSE_FILE) --profile frontend up -d

up-dev:
	docker compose -f $(COMPOSE_FILE) --profile dev up -d shu-api-dev shu-postgres shu-db-migrate redis

up-full-dev:
	docker compose -f $(COMPOSE_FILE) --profile dev --profile frontend up -d shu-api-dev shu-postgres shu-db-migrate redis shu-frontend

down:
	docker compose -f $(COMPOSE_FILE) down --remove-orphans || true
	-docker rm -f shu-frontend shu-api-dev 2>/dev/null || true

logs:
	docker compose -f $(COMPOSE_FILE) logs -f

ps:
	docker compose -f $(COMPOSE_FILE) ps