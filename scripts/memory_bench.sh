#!/usr/bin/env bash
#
# Local memory harness for SHU-731.
#
# Runs N variants of the Shu backend back-to-back against the same corpus,
# samples RSS continuously, records pre/post-trim deltas, and writes a
# per-variant CSV plus a summary table.
#
# Intended use: fast A/B iteration on Python-level changes (orchestrator
# fixes, etc.) on the developer's host. The lab is the authoritative gate
# for allocator variants — see scripts/memory_bench_lab.sh for that.
#
# Usage:
#   ./scripts/memory_bench.sh \
#       --corpus-dir data/attachments \
#       --variants baseline,post-fix \
#       --duration 900
#
# Variants for pure orchestrator-fix A/B (Python-heap changes translate
# directly to RSS on any OS):
#   baseline, post-fix
#
# Variants that exercise Linux-only allocator behavior (glibc arena cap,
# malloc_trim, jemalloc) produce meaningful numbers ONLY on a Linux host
# or in the lab. On macOS they run but the effect is a no-op.
set -euo pipefail

SHU_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

CORPUS_DIR=""
VARIANTS="glibc-default,glibc-arena2,jemalloc,jemalloc-trim,glibc-arena2-trim"
OUT_DIR="$SHU_DIR/scripts/memory_bench_results"
BASE_PORT=8765
DRAIN_TIMEOUT=1200
UPLOAD_CONCURRENCY=4
MAX_FILES=0
KB_NAME="memory-harness-$(date +%s)"
TARGET_RSS_MB=500
JEMALLOC_PATH="${JEMALLOC_PATH:-}"

usage() {
  sed -n '1,/^set -euo pipefail/p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

while (( $# )); do
  case "$1" in
    --corpus-dir) CORPUS_DIR="$2"; shift 2 ;;
    --variants) VARIANTS="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --port) BASE_PORT="$2"; shift 2 ;;
    --drain-timeout) DRAIN_TIMEOUT="$2"; shift 2 ;;
    --concurrency) UPLOAD_CONCURRENCY="$2"; shift 2 ;;
    --max-files) MAX_FILES="$2"; shift 2 ;;
    --kb-name) KB_NAME="$2"; shift 2 ;;
    --target-mb) TARGET_RSS_MB="$2"; shift 2 ;;
    --jemalloc-path) JEMALLOC_PATH="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

[[ -z "$CORPUS_DIR" ]] && { echo "--corpus-dir is required" >&2; exit 1; }
[[ ! -d "$CORPUS_DIR" ]] && { echo "corpus dir not found: $CORPUS_DIR" >&2; exit 1; }

resolve_jemalloc() {
  local candidates=(
    "/usr/lib/x86_64-linux-gnu/libjemalloc.so.2"
    "/usr/lib/aarch64-linux-gnu/libjemalloc.so.2"
    "/usr/local/lib/libjemalloc.so.2"
    "/opt/homebrew/lib/libjemalloc.2.dylib"
    "/usr/local/lib/libjemalloc.2.dylib"
  )
  for c in "${candidates[@]}"; do
    [[ -f "$c" ]] && { echo "$c"; return 0; }
  done
  echo "libjemalloc not found — set JEMALLOC_PATH or install libjemalloc2" >&2
  return 1
}

# Prints KEY=VALUE lines for a variant. Baseline/post-fix variants
# intentionally leave everything unset — they compare Python-heap behavior
# only, which is what matters for orchestrator-fix A/B on macOS.
variant_env() {
  case "$1" in
    baseline|post-fix)
      # No allocator overrides. Before/after is a git checkout on the user's side.
      ;;
    glibc-default)
      ;;
    glibc-arena2)
      echo "MALLOC_ARENA_MAX=2"
      ;;
    jemalloc)
      [[ -z "$JEMALLOC_PATH" ]] && JEMALLOC_PATH="$(resolve_jemalloc)"
      echo "LD_PRELOAD=$JEMALLOC_PATH"
      echo "DYLD_INSERT_LIBRARIES=$JEMALLOC_PATH"
      ;;
    jemalloc-trim)
      [[ -z "$JEMALLOC_PATH" ]] && JEMALLOC_PATH="$(resolve_jemalloc)"
      echo "LD_PRELOAD=$JEMALLOC_PATH"
      echo "DYLD_INSERT_LIBRARIES=$JEMALLOC_PATH"
      echo "SHU_MEMORY_TRIM_INTERVAL_SECONDS=60"
      ;;
    glibc-arena2-trim)
      echo "MALLOC_ARENA_MAX=2"
      echo "SHU_MEMORY_TRIM_INTERVAL_SECONDS=60"
      ;;
    *) echo "unknown variant: $1" >&2; return 1 ;;
  esac
}

sample_rss_kb() {
  ps -o rss= -p "$1" 2>/dev/null | tr -d ' ' || true
}

wait_ready() {
  local port="$1" tries=120
  for ((i = 0; i < tries; i++)); do
    if curl -fsS "http://127.0.0.1:${port}/api/v1/health/liveness" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

run_variant() {
  local variant="$1" variant_dir="$2" port="$3"
  mkdir -p "$variant_dir"

  local env_lines
  env_lines="$(variant_env "$variant")"
  printf '%s\n' "$env_lines" > "$variant_dir/env.txt"

  echo "==> [$variant] starting uvicorn on :$port"
  (
    cd "$SHU_DIR"
    set -a
    # shellcheck disable=SC1090
    if [[ -n "$env_lines" ]]; then
      eval "$env_lines"
    fi
    set +a
    exec uvicorn shu.main:app \
      --host 127.0.0.1 --port "$port" --lifespan on \
      --app-dir "$SHU_DIR/backend/src" \
      >"$variant_dir/uvicorn.log" 2>&1
  ) &
  local srv_pid=$!
  echo "$srv_pid" > "$variant_dir/uvicorn.pid"
  trap "kill $srv_pid 2>/dev/null || true" EXIT

  if ! wait_ready "$port"; then
    echo "==> [$variant] server did not become ready — see $variant_dir/uvicorn.log" >&2
    kill "$srv_pid" 2>/dev/null || true
    return 2
  fi

  (
    local start_s
    start_s="$(date +%s)"
    echo "t_s,rss_kb" > "$variant_dir/rss.csv"
    while kill -0 "$srv_pid" 2>/dev/null; do
      local now rss
      now="$(date +%s)"
      rss="$(sample_rss_kb "$srv_pid")"
      [[ -n "$rss" ]] && echo "$((now - start_s)),${rss}" >> "$variant_dir/rss.csv"
      sleep 2
    done
  ) &
  local sampler_pid=$!

  echo "==> [$variant] running upload driver"
  local load_rc=0
  python3 "$SHU_DIR/scripts/memory_load.py" \
    --base-url "http://127.0.0.1:${port}" \
    --corpus-dir "$CORPUS_DIR" \
    --kb-name "$KB_NAME" \
    --concurrency "$UPLOAD_CONCURRENCY" \
    --drain-timeout "$DRAIN_TIMEOUT" \
    --max-files "$MAX_FILES" \
    --generate-token \
    --shu-repo "$SHU_DIR" \
    --output "$variant_dir/load.json" \
    --token-out "$variant_dir/.token" || load_rc=$?

  local pre_rss post_rss token
  token="$(cat "$variant_dir/.token" 2>/dev/null || echo '')"
  pre_rss="$(sample_rss_kb "$srv_pid")"
  curl -fsS "http://127.0.0.1:${port}/api/v1/resources/heap-stats" \
       -H "Authorization: Bearer $token" \
       > "$variant_dir/heap_pre.json" 2>/dev/null || true

  curl -fsS -X POST "http://127.0.0.1:${port}/api/v1/resources/heap-stats/trim" \
       -H "Authorization: Bearer $token" \
       > "$variant_dir/heap_post.json" 2>/dev/null || true
  sleep 2
  post_rss="$(sample_rss_kb "$srv_pid")"

  echo "==> [$variant] shutting down uvicorn"
  kill -TERM "$srv_pid" 2>/dev/null || true
  wait "$srv_pid" 2>/dev/null || true
  kill "$sampler_pid" 2>/dev/null || true
  trap - EXIT

  local peak_kb=0
  if [[ -f "$variant_dir/rss.csv" ]]; then
    peak_kb="$(awk -F, 'NR>1 && $2+0 > m {m=$2+0} END {print m+0}' "$variant_dir/rss.csv")"
  fi

  printf '%s,%s,%s,%s,%s,%s\n' \
    "$variant" \
    "${peak_kb:-0}" \
    "${pre_rss:-0}" \
    "${post_rss:-0}" \
    "$load_rc" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    >> "$RUN_DIR/variants.csv"

  echo "==> [$variant] done  peak=${peak_kb}kB pre_trim=${pre_rss}kB post_trim=${post_rss}kB"
}

TS="$(date +%Y%m%dT%H%M%S)"
RUN_DIR="$OUT_DIR/$TS"
mkdir -p "$RUN_DIR"
echo "variant,peak_rss_kb,pre_trim_rss_kb,post_trim_rss_kb,load_rc,finished_at_utc" > "$RUN_DIR/variants.csv"

echo "==> run dir: $RUN_DIR"
echo "==> corpus:  $CORPUS_DIR"
echo "==> variants: $VARIANTS"

IFS=',' read -r -a VARIANT_LIST <<< "$VARIANTS"
port="$BASE_PORT"
overall_rc=0
for variant in "${VARIANT_LIST[@]}"; do
  variant_dir="$RUN_DIR/$variant"
  if ! run_variant "$variant" "$variant_dir" "$port"; then
    echo "==> variant failed: $variant" >&2
    overall_rc=2
  fi
  port=$((port + 1))
done

echo
echo "===================== summary (kB) ====================="
column -s, -t < "$RUN_DIR/variants.csv"
echo "========================================================"
echo
target_kb=$((TARGET_RSS_MB * 1024))
awk -F, -v target="$target_kb" 'NR>1 && $4+0>0 && $4+0<=target {print "  WINNER: "$1" (post_trim "$4" kB <= "target" kB target)"}' "$RUN_DIR/variants.csv" \
  || echo "  No variant met the ${TARGET_RSS_MB} MB post-trim target."

exit "$overall_rc"
