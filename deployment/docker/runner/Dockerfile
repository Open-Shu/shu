# syntax=docker/dockerfile:1.6

# Minimal utility runner image with bash, curl, jq for cluster jobs
FROM alpine:3.20

RUN apk add --no-cache bash curl jq ca-certificates && \
    update-ca-certificates

# Default shell (overridden by k8s command in CronJob)
SHELL ["/bin/bash", "-lc"]

# Basic health check (no-op); jobs will override command
CMD ["bash", "-lc", "echo runner ready"]

