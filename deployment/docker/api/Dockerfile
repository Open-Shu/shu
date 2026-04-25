# syntax=docker/dockerfile:1.6

# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Build-time metadata (optionally provided)
ARG SHU_APP_VERSION
ARG SHU_GIT_SHA
ARG SHU_BUILD_TIMESTAMP
ARG SHU_DB_RELEASE


# Set the working directory in the container
WORKDIR /app

# Install system dependencies. libjemalloc2 is LD_PRELOADed below to reduce
# long-running-process RSS retention vs. glibc's default 8×nproc arenas
# (SHU-731). SHU_DISABLE_JEMALLOC=1 at runtime clears LD_PRELOAD if an
# operator needs to fall back to glibc for comparison.
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    libjemalloc2 \
    && rm -rf /var/lib/apt/lists/* \
    # Record the concrete jemalloc path once so runtime doesn't have to
    # guess multi-arch paths (amd64 vs arm64 live in different dirs).
    && JEMALLOC_PATH="$(find /usr/lib -name 'libjemalloc.so.2' | head -n1)" \
    && [ -n "$JEMALLOC_PATH" ] && printf '%s\n' "$JEMALLOC_PATH" > /etc/shu-jemalloc-path

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt (cache wheels for faster rebuilds)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code into the container at /app
# NOTE: These COPY commands assume the build context is the repository root.
# Build from repo root using: docker build -f deployment/docker/api/Dockerfile .
# When using docker-compose, the context is set in the compose file (see deployment/compose/docker-compose.yml).
COPY backend/src/ ./src/
COPY backend/migrations/ ./migrations/
COPY backend/alembic.ini ./alembic.ini
COPY backend/scripts/ ./scripts/

# Create plugin and data directories, and non-root user to run the application
RUN mkdir -p /app/data/plugins /app/data/branding /app/data/attachments /app/data/logs /app/data/ingestion \
    && adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app
USER appuser

# Define runtime environment via Kubernetes/Compose; only set PYTHONPATH here
ENV PYTHONPATH=/app/src \
    SHU_APP_VERSION=${SHU_APP_VERSION:-0.0.0-dev} \
    SHU_GIT_SHA=${SHU_GIT_SHA:-unknown} \
    SHU_BUILD_TIMESTAMP=${SHU_BUILD_TIMESTAMP:-unknown} \
    SHU_DB_RELEASE=${SHU_DB_RELEASE:-}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health/liveness || exit 1

# Expose port
EXPOSE 8000

# Entrypoint wires jemalloc via LD_PRELOAD at startup (SHU-731). Done as an
# entrypoint rather than a hard-coded ENV so operators can disable it via
# SHU_DISABLE_JEMALLOC=1 without needing to build a new image, and so we
# don't bake an arch-specific path into the image metadata.
COPY deployment/docker/api/entrypoint.sh /usr/local/bin/shu-entrypoint.sh
USER root
RUN chmod +x /usr/local/bin/shu-entrypoint.sh
USER appuser
ENTRYPOINT ["/usr/local/bin/shu-entrypoint.sh"]

# Specify the command to run on container startup (run ASGI server)
CMD ["uvicorn", "shu.main:app", "--host", "0.0.0.0", "--port", "8000", "--lifespan", "on"]
