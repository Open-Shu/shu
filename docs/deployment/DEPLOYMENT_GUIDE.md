# Shu Deployment Guide

This guide covers deployment patterns, worker modes, scaling strategies, and production configurations for Shu.

## Table of Contents

1. [Deployment Modes Overview](#deployment-modes-overview)
2. [Worker Architecture](#worker-architecture)
3. [Docker Compose Deployment](#docker-compose-deployment)
4. [Kubernetes Deployment](#kubernetes-deployment)
5. [Environment Variables Reference](#environment-variables-reference)
6. [Scaling Patterns](#scaling-patterns)
7. [Troubleshooting](#troubleshooting)

---

## Deployment Modes Overview

Shu supports three deployment profiles from a single codebase:

| Mode | Description | Use Case |
|------|-------------|----------|
| **Single-Node** | All components in one process or container | Development, small teams, evaluation |
| **Docker Compose** | Multi-container stack with optional dedicated workers | Local testing, staging environments |
| **Kubernetes** | Horizontally scalable with dedicated worker pools | Production, enterprise workloads |

### Backend Selection

Shu automatically selects backend implementations based on configuration:

- **Queue Backend**: Set `SHU_REDIS_URL` → Redis queues; omit → in-memory queues
- **Cache Backend**: Set `SHU_REDIS_URL` → Redis cache; omit → in-memory cache
- **Worker Mode**: Set `SHU_WORKERS_ENABLED=true` → inline workers; `false` → dedicated worker processes

---

## Worker Architecture

### What Are Workers?

Workers are background processes that consume jobs from queues. Jobs include:
- Document profiling (LLM-powered analysis)
- Plugin feed ingestion (Gmail, Google Drive, etc.)
- Scheduled experience execution
- Maintenance tasks

### Worker Modes

#### Inline Mode (Default)
Workers run in-process with the API server.

**Configuration:**
```bash
SHU_WORKERS_ENABLED=true  # Default
```

**Pros:**
- Simple deployment (single process)
- Suitable for single-node deployments
- Lower resource overhead

**Cons:**
- Cannot scale workers independently from API
- Background jobs compete with API requests for resources

#### Dedicated Mode
Workers run as separate processes/containers.

**Configuration:**
```bash
# API process
SHU_WORKERS_ENABLED=false

# Worker process
python -m shu.worker
```

**Pros:**
- Independent scaling of API and workers
- Workload isolation (API vs background jobs)
- Can scale different workload types independently

**Cons:**
- Requires Redis for queue backend
- More complex deployment

### Workload Types

Shu routes jobs to queues based on workload type:

| WorkloadType | Queue Name | Purpose |
|--------------|------------|---------|
| `INGESTION` | `shu:ingestion` | Plugin feed ingestion (Gmail, Drive, Outlook, etc.) |
| `INGESTION_OCR` | `shu:ingestion_ocr` | OCR/text extraction stage of document pipeline |
| `INGESTION_EMBED` | `shu:ingestion_embed` | Embedding stage of document pipeline |
| `PROFILING` | `shu:profiling` | LLM-powered document/chunk profiling |
| `LLM_WORKFLOW` | `shu:llm_workflow` | Experience execution, chat workflows |
| `MAINTENANCE` | `shu:maintenance` | Cleanup, scheduled maintenance tasks |

Workers can consume all workload types or specific types for targeted scaling.

---

## Docker Compose Deployment

### Basic Stack (Inline Workers)

Start API, Postgres, and Redis with inline workers:

```bash
make up
# or
docker compose -f deployment/compose/docker-compose.yml up -d
```

This starts:
- `shu-postgres` - PostgreSQL with pgvector
- `shu-db-migrate` - Database migrations (one-off)
- `redis` - Redis for caching and queues
- `shu-api` - API server with inline workers (`SHU_WORKERS_ENABLED=true`)

### Full Stack with Frontend

```bash
make up-full
# or
docker compose -f deployment/compose/docker-compose.yml --profile frontend up -d
```

Adds:
- `shu-frontend` - React admin console on port 3000

### Dedicated Workers (Production Pattern)

Start dedicated worker processes:

```bash
# Start base stack first
make up

# Add dedicated workers
make up-worker
# or
docker compose -f deployment/compose/docker-compose.yml --profile worker up -d
```

**Important:** When using dedicated workers, set `SHU_WORKERS_ENABLED=false` on the API service to prevent duplicate job processing.

### Development with Hot-Reload

```bash
# API with hot-reload
make up-dev

# Worker with hot-reload
make up-worker-dev
# or
docker compose -f deployment/compose/docker-compose.yml --profile worker-dev up -d
```

The dev worker mounts source code for live reloading during development.

### Worker CLI Options

The worker entrypoint supports several options:

```bash
# Run worker consuming all workload types (default)
python -m shu.worker

# Run worker consuming specific workload types
python -m shu.worker --workload-types=INGESTION,PROFILING

# Run worker with custom poll interval
python -m shu.worker --poll-interval=0.5

# Run worker with custom shutdown timeout
python -m shu.worker --shutdown-timeout=60
```

### Docker Compose Service Examples

#### Workload-Specific Workers

Scale different workload types independently by creating specialized worker services:

```yaml
# docker-compose.yml
shu-worker-ingestion:
  build:
    context: ../..
    dockerfile: deployment/docker/api/Dockerfile
  command: ["python", "-m", "shu.worker", "--workload-types=INGESTION"]
  environment:
    - SHU_REDIS_URL=redis://redis:6379
    - SHU_DATABASE_URL=postgresql+asyncpg://shu:password@shu-postgres:5432/shu
  depends_on:
    - redis
    - shu-db-migrate
  restart: unless-stopped

shu-worker-llm:
  build:
    context: ../..
    dockerfile: deployment/docker/api/Dockerfile
  command: ["python", "-m", "shu.worker", "--workload-types=LLM_WORKFLOW,PROFILING"]
  environment:
    - SHU_REDIS_URL=redis://redis:6379
    - SHU_DATABASE_URL=postgresql+asyncpg://shu:password@shu-postgres:5432/shu
  depends_on:
    - redis
    - shu-db-migrate
  restart: unless-stopped
```

Then scale each independently:

```bash
docker compose up -d --scale shu-worker-ingestion=5 --scale shu-worker-llm=2
```

### Useful Commands

```bash
# View logs
make logs                    # All services
make logs-worker             # Worker services only
docker compose logs -f shu-worker

# Check status
make ps
docker compose ps

# Stop services
make down

# Rebuild after code changes
docker compose build shu-api shu-worker
```

---

## Kubernetes Deployment

Shu is designed for Kubernetes deployment with health checks, configurable scaling, and monitoring integration.

### Architecture

A typical Kubernetes deployment includes:

- **API Deployment**: Horizontally scalable API servers with `SHU_WORKERS_ENABLED=false`
- **Worker Deployments**: One or more worker deployments per workload type
- **PostgreSQL**: Managed service (Azure Database, RDS, etc.) or in-cluster StatefulSet
- **Redis**: Managed service (Azure Cache, ElastiCache, etc.) or in-cluster StatefulSet
- **Ingress**: NGINX or cloud load balancer for external access

### Example Worker Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: shu-worker-ingestion
  namespace: shu-production
spec:
  replicas: 3
  selector:
    matchLabels:
      app: shu-worker
      workload: ingestion
  template:
    metadata:
      labels:
        app: shu-worker
        workload: ingestion
    spec:
      containers:
      - name: worker
        image: your-registry/shu-api:v1.0.0
        command: ["python", "-m", "shu.worker", "--workload-types=INGESTION"]
        env:
        - name: SHU_REDIS_URL
          valueFrom:
            secretKeyRef:
              name: shu-secrets
              key: redis-url
        - name: SHU_DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: shu-secrets
              key: database-url
        - name: SHU_WORKER_POLL_INTERVAL
          value: "1.0"
        - name: SHU_WORKER_SHUTDOWN_TIMEOUT
          value: "30.0"
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
        livenessProbe:
          exec:
            command: ["pgrep", "-f", "shu.worker"]
          initialDelaySeconds: 30
          periodSeconds: 30
```

### Scaling Strategy

Scale different workload types based on queue depth and processing time:

```bash
# Scale ingestion workers (high volume, fast processing)
kubectl scale deployment shu-worker-ingestion --replicas=10

# Scale LLM workers (lower volume, slow processing)
kubectl scale deployment shu-worker-llm --replicas=5

# Scale API servers
kubectl scale deployment shu-api --replicas=3
```

### Health Checks

Workers don't expose HTTP endpoints, so use process-based health checks:

```yaml
livenessProbe:
  exec:
    command: ["pgrep", "-f", "shu.worker"]
  initialDelaySeconds: 30
  periodSeconds: 30
```

For more sophisticated health checks, consider adding a sidecar that monitors queue processing metrics.

---

## Environment Variables Reference

### Core Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SHU_DATABASE_URL` | *required* | PostgreSQL connection string (asyncpg driver) |
| `SHU_REDIS_URL` | *none* | Redis URL; if unset, uses in-memory backends |
| `SHU_ENVIRONMENT` | `production` | Environment name (production, development, staging) |
| `SHU_LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `SHU_LOG_FORMAT` | `json` | Log format (json, text) |

### API Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SHU_API_HOST` | `0.0.0.0` | API server bind address |
| `SHU_API_PORT` | `8000` | API server port |
| `SHU_WORKERS_ENABLED` | `true` | Run workers inline (true) or use dedicated processes (false) |

### Worker Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SHU_WORKER_POLL_INTERVAL` | `1.0` | Seconds between queue poll attempts when idle |
| `SHU_WORKER_SHUTDOWN_TIMEOUT` | `30.0` | Seconds to wait for current job on graceful shutdown |
| `SHU_WORKER_CONCURRENCY` | `10` | Maximum concurrent jobs per worker process |

### Redis Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SHU_REDIS_CONNECTION_TIMEOUT` | `5` | Connection timeout in seconds |
| `SHU_REDIS_SOCKET_TIMEOUT` | `5` | Socket timeout in seconds |
| `SHU_REDIS_MAX_CONNECTIONS` | `50` | Maximum connections in pool |

---

## Scaling Patterns

### Pattern 1: Single-Node Development

**Use Case:** Local development, evaluation, small teams

**Configuration:**
```bash
SHU_WORKERS_ENABLED=true
# SHU_REDIS_URL not set (uses in-memory backends)
```

**Deployment:**
```bash
# Single process
python -m shu.main

# Or Docker Compose
make up
```

**Characteristics:**
- Single process or container
- In-memory queues and cache
- No horizontal scaling
- Simplest deployment

### Pattern 2: Docker Compose with Dedicated Workers

**Use Case:** Staging, testing production patterns locally

**Configuration:**
```bash
# API
SHU_WORKERS_ENABLED=false
SHU_REDIS_URL=redis://redis:6379

# Workers
SHU_REDIS_URL=redis://redis:6379
```

**Deployment:**
```bash
make up              # Start API + Redis + Postgres
make up-worker       # Start dedicated workers
```

**Characteristics:**
- Multi-container
- Redis-backed queues and cache
- Can scale workers independently
- Mirrors production architecture

### Pattern 3: Kubernetes with Workload-Specific Workers

**Use Case:** Production, enterprise deployments

**Configuration:**
```bash
# API Deployment
SHU_WORKERS_ENABLED=false
SHU_REDIS_URL=redis://redis-service:6379

# Worker Deployments (one per workload type)
SHU_REDIS_URL=redis://redis-service:6379
```

**Deployment:**
```bash
kubectl apply -k deployment/kubernetes/production
```

**Characteristics:**
- Horizontally scalable API and workers
- Independent scaling per workload type
- Managed Redis and PostgreSQL
- Production-grade observability

### Scaling Decision Matrix

| Metric | Single-Node | Docker Compose | Kubernetes |
|--------|-------------|----------------|------------|
| **Users** | 1-10 | 10-100 | 100+ |
| **Documents** | < 10k | 10k-100k | 100k+ |
| **Concurrent Jobs** | < 10 | 10-100 | 100+ |
| **Availability** | Best effort | High | HA with replicas |
| **Complexity** | Low | Medium | High |

---

## Troubleshooting

### Workers Not Processing Jobs

**Symptoms:** Jobs enqueued but never processed

**Diagnosis:**
```bash
# Check if workers are running
docker compose ps | grep worker
kubectl get pods -l app=shu-worker

# Check worker logs
docker compose logs shu-worker
kubectl logs -l app=shu-worker --tail=100

# Check Redis connectivity
redis-cli -h <redis-host> ping
redis-cli -h <redis-host> llen shu:ingestion
```

**Common Causes:**
1. **No workers running**: Start workers with `make up-worker` or deploy worker pods
2. **Workers disabled on API**: Set `SHU_WORKERS_ENABLED=false` when using dedicated workers
3. **Redis connection failure**: Verify `SHU_REDIS_URL` is correct and Redis is accessible
4. **Wrong workload types**: Ensure workers are configured for the correct workload types

### Duplicate Job Processing

**Symptoms:** Same job processed multiple times

**Diagnosis:**
```bash
# Check if both inline and dedicated workers are running
docker compose ps
# Look for both shu-api with WORKERS_ENABLED=true AND shu-worker
```

**Solution:**
Set `SHU_WORKERS_ENABLED=false` on API when using dedicated workers:

```yaml
# docker-compose.yml
shu-api:
  environment:
    - SHU_WORKERS_ENABLED=false  # Disable inline workers
```

### Worker Crashes on Shutdown

**Symptoms:** Workers exit with errors during graceful shutdown

**Diagnosis:**
```bash
# Check shutdown timeout
docker compose logs shu-worker | grep -i shutdown
```

**Solution:**
Increase shutdown timeout to allow jobs to complete:

```bash
SHU_WORKER_SHUTDOWN_TIMEOUT=60  # Increase from default 30s
```

Or use CLI argument:
```bash
python -m shu.worker --shutdown-timeout=60
```

### Queue Backlog Growing

**Symptoms:** Jobs accumulating faster than workers can process

**Diagnosis:**
```bash
# Check queue lengths
redis-cli llen shu:ingestion
redis-cli llen shu:llm_workflow
redis-cli llen shu:profiling
redis-cli llen shu:maintenance

# Check worker count
kubectl get pods -l app=shu-worker
```

**Solution:**
Scale workers for the affected workload type:

```bash
# Docker Compose
docker compose up -d --scale shu-worker-ingestion=5

# Kubernetes
kubectl scale deployment shu-worker-ingestion --replicas=10
```

### In-Memory Queue Lost on Restart

**Symptoms:** Jobs disappear when API restarts

**Cause:** Using in-memory queue backend (no `SHU_REDIS_URL` set)

**Solution:**
For production, always use Redis:

```bash
SHU_REDIS_URL=redis://redis:6379
```

In-memory queues are only suitable for development and single-node deployments where job loss on restart is acceptable.

---

## Additional Resources

- **Architecture Overview**: [docs/ARCHITECTURE.md](../ARCHITECTURE.md)
- **Configuration Guide**: [docs/policies/CONFIGURATION.md](../policies/CONFIGURATION.md)
- **Development Standards**: [docs/policies/DEVELOPMENT_STANDARDS.md](../policies/DEVELOPMENT_STANDARDS.md)
- **Worker Implementation**: `backend/src/shu/worker.py`
- **Workload Routing**: `backend/src/shu/core/workload_routing.py`
- **Queue Backend**: `backend/src/shu/core/queue_backend.py`
