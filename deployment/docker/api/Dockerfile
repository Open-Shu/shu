# syntax=docker/dockerfile:1.6

# Multi-stage build. The standard image includes local text-embedding and OCR
# inference engines (sentence-transformers, torch, transformers, easyocr,
# pytesseract). Pass `--build-arg INCLUDE_LOCAL_INFERENCE=0` to produce the
# slim variant, which excludes those packages — for deployments that route
# embedding and OCR through external APIs and don't need local inference.
#
# Build from the repository root:
#   docker build -f deployment/docker/api/Dockerfile .                                       # standard (with local inference)
#   docker build -f deployment/docker/api/Dockerfile --build-arg INCLUDE_LOCAL_INFERENCE=0 . # slim
#
# Corresponding Makefile targets: `make build-api` (standard) and
# `make build-api-slim`.

# =============================================================================
# Builder stage — install build tools + Python deps, then discard the build
# tooling. Only the resulting site-packages tree carries into the runtime
# image.
# =============================================================================

FROM python:3.11-slim AS builder

ARG INCLUDE_LOCAL_INFERENCE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt requirements-local-inference.txt ./

# pip install --prefix=/install stages the install tree separately from
# system /usr/local. The runtime stage COPYs /install over to its own
# /usr/local. The BuildKit pip cache mount (`--mount=type=cache`)
# accelerates rebuilds — pip reads cached wheels on a re-run instead of
# re-downloading. The mount is not committed to any layer, so it has
# zero effect on final image size.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install -r requirements.txt && \
    if [ "$INCLUDE_LOCAL_INFERENCE" = "1" ]; then \
        echo "INCLUDE_LOCAL_INFERENCE=1 — installing local embedding + OCR engines" && \
        pip install --prefix=/install -r requirements-local-inference.txt ; \
    else \
        echo "INCLUDE_LOCAL_INFERENCE=0 — slim variant, skipping local inference engines" ; \
    fi

# =============================================================================
# Runtime stage — slim base, only the deps + source needed to run the app.
# No build-essential, no git, no pip caches.
# =============================================================================

FROM python:3.11-slim AS runtime

# Build-time metadata baked into the image as default env values.
ARG SHU_APP_VERSION
ARG SHU_GIT_SHA
ARG SHU_BUILD_TIMESTAMP
ARG SHU_DB_RELEASE

# Runtime system deps:
#   curl       — used by HEALTHCHECK
#   libjemalloc2 — LD_PRELOADed by entrypoint to reduce long-running-process
#                  RSS retention vs. glibc's default 8×nproc arenas (SHU-731).
#                  SHU_DISABLE_JEMALLOC=1 clears LD_PRELOAD at runtime.
# build-essential and git are intentionally NOT installed here — they only
# matter during the builder stage's pip install.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      libjemalloc2 \
    && rm -rf /var/lib/apt/lists/* \
    && JEMALLOC_PATH="$(find /usr/lib -name 'libjemalloc.so.2' | head -n1)" \
    && [ -n "$JEMALLOC_PATH" ] && printf '%s\n' "$JEMALLOC_PATH" > /etc/shu-jemalloc-path

# Bring over Python deps from the builder stage. /install becomes /usr/local.
COPY --from=builder /install /usr/local

WORKDIR /app

# Create non-root user + data directories. Done in one layer BEFORE the
# COPY --chown calls so subsequent COPYs can target the user directly.
RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /app/data/plugins /app/data/branding /app/data/attachments /app/data/logs /app/data/ingestion \
    && chown -R appuser:appuser /app

# Application source. COPY --chown avoids the chown-layer duplication that
# would otherwise double the size of every file in /app on the image.
# Tests are excluded via .dockerignore (backend/src/tests).
COPY --chown=appuser:appuser backend/src/ ./src/
COPY --chown=appuser:appuser backend/migrations/ ./migrations/
COPY --chown=appuser:appuser backend/alembic.ini ./alembic.ini
COPY --chown=appuser:appuser backend/scripts/ ./scripts/
# Entrypoint script copied last so edits to it don't invalidate the larger
# source-copy layers above. `--chmod=755` sets the executable bit at COPY
# time, avoiding a separate `RUN chmod` layer and the USER root/appuser
# swap that would otherwise be needed.
COPY --chmod=755 --chown=appuser:appuser deployment/docker/api/entrypoint.sh /usr/local/bin/shu-entrypoint.sh

USER appuser

ENV PYTHONPATH=/app/src \
    SHU_APP_VERSION=${SHU_APP_VERSION:-0.0.0-dev} \
    SHU_GIT_SHA=${SHU_GIT_SHA:-unknown} \
    SHU_BUILD_TIMESTAMP=${SHU_BUILD_TIMESTAMP:-unknown} \
    SHU_DB_RELEASE=${SHU_DB_RELEASE:-}

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health/liveness || exit 1

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/shu-entrypoint.sh"]
CMD ["uvicorn", "shu.main:app", "--host", "0.0.0.0", "--port", "8000", "--lifespan", "on"]
