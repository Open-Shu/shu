#!/usr/bin/env bash
# Runtime entrypoint that wires jemalloc before exec'ing the app (SHU-731).
#
# jemalloc replaces glibc's malloc via LD_PRELOAD. Compared to glibc with
# MALLOC_ARENA_MAX=2 it tends to release memory back to the kernel more
# aggressively under Python's allocation churn, at the cost of ~5-10% extra
# CPU on allocation-heavy paths.
#
# Operator controls:
#   SHU_DISABLE_JEMALLOC=1  → skip LD_PRELOAD and fall back to glibc
#   LD_PRELOAD already set  → respected as-is (don't clobber operator intent)
set -euo pipefail

if [[ -z "${LD_PRELOAD:-}" && "${SHU_DISABLE_JEMALLOC:-}" != "1" ]]; then
  if [[ -r /etc/shu-jemalloc-path ]]; then
    JEMALLOC_PATH="$(cat /etc/shu-jemalloc-path)"
    if [[ -f "$JEMALLOC_PATH" ]]; then
      export LD_PRELOAD="$JEMALLOC_PATH"
    fi
  fi
fi

exec "$@"
