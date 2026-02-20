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

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

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

# Specify the command to run on container startup (run ASGI server)
CMD ["uvicorn", "shu.main:app", "--host", "0.0.0.0", "--port", "8000", "--lifespan", "on"]
