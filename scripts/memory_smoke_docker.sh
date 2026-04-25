#!/usr/bin/env bash
#
# Container smoke test for SHU-731.
#
# Validates that the production image's memory wiring is correct end-to-end:
#   - libjemalloc2 is installed
#   - /etc/shu-jemalloc-path was written at build
#   - entrypoint.sh LD_PRELOADs jemalloc when SHU_DISABLE_JEMALLOC is unset
#   - MALLOC_ARENA_MAX passthrough works
#   - SHU_MEMORY_TRIM_INTERVAL_SECONDS passthrough works
#   - /api/v1/resources/heap-stats is reachable
#
# This is NOT a performance harness — it's wiring verification. Run it
# after touching the Dockerfile, entrypoint, or compose env passthroughs.
# For Python-level A/B iteration use scripts/memory_bench.sh; for
# authoritative sizing numbers use scripts/memory_bench_lab.sh against
# shu-billing-lab.
#
# Usage: ./scripts/memory_smoke_docker.sh [--keep]
#   --keep  leave containers up after the smoke test (default: tear down)
set -euo pipefail

SHU_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$SHU_DIR/deployment/compose/docker-compose.yml"
DC="docker compose -f $COMPOSE_FILE"
API_SERVICE="shu-api-dev"
BASE_URL="http://127.0.0.1:8000"
KEEP=0

[[ "${1:-}" == "--keep" ]] && KEEP=1

cleanup() {
  if [[ "$KEEP" != "1" ]]; then
    echo "==> tearing down"
    $DC --profile dev down --remove-orphans >/dev/null 2>&1 || true
  else
    echo "==> leaving containers up (--keep)"
  fi
}
trap cleanup EXIT

echo "==> bringing up backing services + api"
$DC --profile dev up -d shu-postgres redis shu-db-migrate "$API_SERVICE" >/dev/null

echo "==> waiting for liveness"
for ((i = 0; i < 180; i++)); do
  if curl -fsS "$BASE_URL/api/v1/health/liveness" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

cid="$($DC ps -q "$API_SERVICE")"
[[ -z "$cid" ]] && { echo "FAIL: api container not running" >&2; exit 1; }

fail=0
check() {
  local label="$1" status="$2"
  if [[ "$status" == "0" ]]; then
    printf '  OK    %s\n' "$label"
  else
    printf '  FAIL  %s\n' "$label"
    fail=1
  fi
}

echo "==> checking image-level wiring"
docker exec "$cid" test -f /etc/shu-jemalloc-path
check "/etc/shu-jemalloc-path present" $?

docker exec "$cid" bash -c 'test -f "$(cat /etc/shu-jemalloc-path)"'
check "libjemalloc.so.2 installed at recorded path" $?

echo "==> checking runtime allocator wiring"
# Resolve jemalloc path inside the container.
jpath="$(docker exec "$cid" cat /etc/shu-jemalloc-path)"

# The entrypoint should have set LD_PRELOAD for pid 1 (the Python process).
ld_preload="$(docker exec "$cid" tr '\0' '\n' < /proc/1/environ | grep '^LD_PRELOAD=' || true)"
if [[ "$ld_preload" == "LD_PRELOAD=$jpath" ]]; then
  check "LD_PRELOAD=$jpath on pid 1" 0
else
  echo "  FAIL  LD_PRELOAD mismatch (got: $ld_preload, expected LD_PRELOAD=$jpath)"
  fail=1
fi

# Confirm jemalloc is actually mapped into the process — the only
# authoritative check that LD_PRELOAD took effect.
if docker exec "$cid" grep -q 'libjemalloc' /proc/1/maps; then
  check "libjemalloc mapped into pid 1 address space" 0
else
  check "libjemalloc mapped into pid 1 address space" 1
fi

echo "==> checking env passthrough"
for var in MALLOC_ARENA_MAX SHU_MEMORY_TRIM_INTERVAL_SECONDS; do
  val="$(docker exec "$cid" tr '\0' '\n' < /proc/1/environ | grep "^$var=" || true)"
  if [[ -n "$val" ]]; then
    check "$val" 0
  else
    check "$var not set on pid 1" 1
  fi
done

echo "==> checking admin endpoint reachable"
# 401/403 counts as reachable — we just want to know FastAPI wired the
# route. Passing auth via generate_test_token is overkill for a smoke
# test; the orchestrator-fix A/B harness handles that path.
code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/api/v1/resources/heap-stats" || true)"
if [[ "$code" =~ ^(200|401|403)$ ]]; then
  check "GET /api/v1/resources/heap-stats responded HTTP $code" 0
else
  check "GET /api/v1/resources/heap-stats responded HTTP $code" 1
fi

echo
if [[ "$fail" == "0" ]]; then
  echo "===== container memory wiring OK ====="
  exit 0
else
  echo "===== FAIL: see checks above ====="
  exit 1
fi
