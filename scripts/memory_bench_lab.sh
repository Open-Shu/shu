#!/usr/bin/env bash
#
# Lab memory harness for SHU-731 — the authoritative gate for sizing
# decisions (≤500 MB per instance under the SHU-710 workload).
#
# Runs each variant against an actual Kubernetes deployment by patching
# the configmap, rolling the deployment, running the upload driver through
# the cluster's gateway, sampling `kubectl top pod`, and calling the admin
# endpoints to force trim + capture heap-stats.
#
# Default target: `shu-billing-lab` namespace on the local Docker Desktop
# cluster. Override with --namespace / --deployment / --configmap.
#
# Usage:
#   ./scripts/memory_bench_lab.sh \
#       --corpus-dir data/attachments \
#       --variants glibc-default,glibc-arena2,jemalloc,jemalloc-trim,glibc-arena2-trim \
#       --base-url http://localhost \
#       --admin-token "$SHU_LAB_ADMIN_TOKEN"
#
# Prerequisites:
#   - kubectl context pointing at the target cluster
#   - The shu-billing-lab (or equivalent) overlay already applied
#   - An admin JWT passed via --admin-token or SHU_LAB_ADMIN_TOKEN
#
# Output:
#   scripts/memory_bench_lab_results/<timestamp>/
#     variants.csv
#     <variant>/
#       configmap-patch.yaml           # exact patch applied
#       kubectl-top.csv                # t_s,cpu_m,memory_mb (kubectl top pod)
#       heap_pre.json                  # pre-trim heap stats
#       heap_post.json                 # post-trim heap stats + RSS delta
#       load.json                      # driver summary
#       pod-logs.txt                   # pod logs for the run
set -euo pipefail

SHU_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

NAMESPACE="shu-billing-lab"
DEPLOYMENT="shu-api"
CONFIGMAP="shu-config"
BASE_URL="http://localhost"
ADMIN_TOKEN="${SHU_LAB_ADMIN_TOKEN:-}"
CORPUS_DIR=""
VARIANTS="glibc-default,glibc-arena2,jemalloc,jemalloc-trim,glibc-arena2-trim"
OUT_DIR="$SHU_DIR/scripts/memory_bench_lab_results"
DRAIN_TIMEOUT=1200
UPLOAD_CONCURRENCY=4
MAX_FILES=0
KB_NAME="memory-harness-lab-$(date +%s)"
TARGET_RSS_MB=500
SAMPLE_INTERVAL=5

usage() {
  sed -n '1,/^set -euo pipefail/p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

while (( $# )); do
  case "$1" in
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --deployment) DEPLOYMENT="$2"; shift 2 ;;
    --configmap) CONFIGMAP="$2"; shift 2 ;;
    --base-url) BASE_URL="$2"; shift 2 ;;
    --admin-token) ADMIN_TOKEN="$2"; shift 2 ;;
    --corpus-dir) CORPUS_DIR="$2"; shift 2 ;;
    --variants) VARIANTS="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --drain-timeout) DRAIN_TIMEOUT="$2"; shift 2 ;;
    --concurrency) UPLOAD_CONCURRENCY="$2"; shift 2 ;;
    --max-files) MAX_FILES="$2"; shift 2 ;;
    --kb-name) KB_NAME="$2"; shift 2 ;;
    --target-mb) TARGET_RSS_MB="$2"; shift 2 ;;
    --sample-interval) SAMPLE_INTERVAL="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

[[ -z "$CORPUS_DIR" ]] && { echo "--corpus-dir is required" >&2; exit 1; }
[[ ! -d "$CORPUS_DIR" ]] && { echo "corpus dir not found: $CORPUS_DIR" >&2; exit 1; }
[[ -z "$ADMIN_TOKEN" ]] && { echo "--admin-token (or SHU_LAB_ADMIN_TOKEN) is required" >&2; exit 1; }

# Abort early if kubectl can't reach the cluster — faster to fail here than
# after the first variant starts.
kubectl -n "$NAMESPACE" get deploy "$DEPLOYMENT" >/dev/null

variant_patch_data() {
  # Echo the configmap data block lines this variant wants applied.
  case "$1" in
    glibc-default)
      echo "MALLOC_ARENA_MAX: \"\""
      echo "SHU_MEMORY_TRIM_INTERVAL_SECONDS: \"0\""
      echo "SHU_DISABLE_JEMALLOC: \"1\""
      ;;
    glibc-arena2)
      echo "MALLOC_ARENA_MAX: \"2\""
      echo "SHU_MEMORY_TRIM_INTERVAL_SECONDS: \"0\""
      echo "SHU_DISABLE_JEMALLOC: \"1\""
      ;;
    jemalloc)
      echo "MALLOC_ARENA_MAX: \"\""
      echo "SHU_MEMORY_TRIM_INTERVAL_SECONDS: \"0\""
      echo "SHU_DISABLE_JEMALLOC: \"\""
      ;;
    jemalloc-trim)
      echo "MALLOC_ARENA_MAX: \"\""
      echo "SHU_MEMORY_TRIM_INTERVAL_SECONDS: \"60\""
      echo "SHU_DISABLE_JEMALLOC: \"\""
      ;;
    glibc-arena2-trim)
      echo "MALLOC_ARENA_MAX: \"2\""
      echo "SHU_MEMORY_TRIM_INTERVAL_SECONDS: \"60\""
      echo "SHU_DISABLE_JEMALLOC: \"1\""
      ;;
    *) echo "unknown variant: $1" >&2; return 1 ;;
  esac
}

apply_variant() {
  local variant="$1" variant_dir="$2"
  local patch_file="$variant_dir/configmap-patch.yaml"
  {
    echo "apiVersion: v1"
    echo "kind: ConfigMap"
    echo "metadata:"
    echo "  name: $CONFIGMAP"
    echo "  namespace: $NAMESPACE"
    echo "data:"
    variant_patch_data "$variant" | sed 's/^/  /'
  } > "$patch_file"

  kubectl -n "$NAMESPACE" patch configmap "$CONFIGMAP" --patch-file "$patch_file"
  # Force rollout so new pods pick up the configmap.
  kubectl -n "$NAMESPACE" rollout restart deploy "$DEPLOYMENT"
  kubectl -n "$NAMESPACE" rollout status deploy "$DEPLOYMENT" --timeout=300s
}

sample_pod_memory() {
  # Emits "vmrss_kb,working_set_kb" for the current pod. Uses /proc/1/status
  # and /sys/fs/cgroup/memory.{current,stat} via kubectl exec — no metrics
  # server required (Docker Desktop K8s doesn't ship one). Working set is
  # memory.current - inactive_file, matching how the kubelet makes OOM
  # decisions (and what `kubectl top` would return if available).
  local pod
  pod="$(kubectl -n "$NAMESPACE" get pod -l "app=$DEPLOYMENT" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  [[ -z "$pod" ]] && return 0
  kubectl -n "$NAMESPACE" exec "$pod" -- bash -c '
    vmrss=$(awk "/^VmRSS:/ {print \$2}" /proc/1/status)
    if [ -r /sys/fs/cgroup/memory.current ]; then
      mem_current=$(cat /sys/fs/cgroup/memory.current)
      inactive_file=$(awk "/^inactive_file / {print \$2}" /sys/fs/cgroup/memory.stat 2>/dev/null || echo 0)
    elif [ -r /sys/fs/cgroup/memory/memory.usage_in_bytes ]; then
      mem_current=$(cat /sys/fs/cgroup/memory/memory.usage_in_bytes)
      inactive_file=$(awk "/^total_inactive_file / {print \$2}" /sys/fs/cgroup/memory/memory.stat 2>/dev/null || echo 0)
    else
      mem_current=0
      inactive_file=0
    fi
    working_set=$(( (mem_current - inactive_file) / 1024 ))
    echo "${vmrss},${working_set}"
  ' 2>/dev/null
}

curl_admin() {
  curl -fsS "$@" -H "Authorization: Bearer $ADMIN_TOKEN"
}

run_variant() {
  local variant="$1" variant_dir="$2"
  mkdir -p "$variant_dir"

  echo "==> [$variant] patching configmap + rolling deployment"
  apply_variant "$variant" "$variant_dir"
  sleep 10  # metrics-server catch-up

  # Sampler in background.
  {
    echo "t_s,vmrss_kb,working_set_kb"
    local start_s
    start_s="$(date +%s)"
    while true; do
      local now line
      now="$(date +%s)"
      line="$(sample_pod_memory || true)"
      [[ -n "$line" ]] && echo "$((now - start_s)),$line"
      sleep "$SAMPLE_INTERVAL"
    done
  } > "$variant_dir/pod-memory.csv" &
  local sampler_pid=$!
  trap "kill $sampler_pid 2>/dev/null || true" EXIT

  echo "==> [$variant] running upload driver via $BASE_URL"
  local load_rc=0
  SHU_HARNESS_TOKEN="$ADMIN_TOKEN" python3 "$SHU_DIR/scripts/memory_load.py" \
    --base-url "$BASE_URL" \
    --corpus-dir "$CORPUS_DIR" \
    --kb-name "$KB_NAME" \
    --concurrency "$UPLOAD_CONCURRENCY" \
    --drain-timeout "$DRAIN_TIMEOUT" \
    --max-files "$MAX_FILES" \
    --output "$variant_dir/load.json" || load_rc=$?

  echo "==> [$variant] capturing heap stats + forcing trim"
  curl_admin "$BASE_URL/api/v1/resources/heap-stats" > "$variant_dir/heap_pre.json" || true
  curl_admin -X POST "$BASE_URL/api/v1/resources/heap-stats/trim" > "$variant_dir/heap_post.json" || true
  sleep 5

  kill "$sampler_pid" 2>/dev/null || true
  wait "$sampler_pid" 2>/dev/null || true
  trap - EXIT

  # Capture pod logs for offline review — `job_memory_delta` lines and
  # trim-task startup messages live here.
  local pod
  pod="$(kubectl -n "$NAMESPACE" get pod -l "app=$DEPLOYMENT" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  if [[ -n "$pod" ]]; then
    kubectl -n "$NAMESPACE" logs "$pod" --tail=5000 > "$variant_dir/pod-logs.txt" 2>&1 || true
  fi

  # Extract post-trim numbers from heap_post.json.
  local pre_rss post_rss freed peak_vmrss_kb peak_working_set_kb
  pre_rss="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["data"]["before_rss_bytes"])' "$variant_dir/heap_post.json" 2>/dev/null || echo 0)"
  post_rss="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["data"]["after_rss_bytes"])' "$variant_dir/heap_post.json" 2>/dev/null || echo 0)"
  freed="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["data"]["freed_bytes"])' "$variant_dir/heap_post.json" 2>/dev/null || echo 0)"
  peak_vmrss_kb="$(awk -F, 'NR>1 && $2+0 > m {m=$2+0} END {print m+0}' "$variant_dir/pod-memory.csv")"
  peak_working_set_kb="$(awk -F, 'NR>1 && $3+0 > m {m=$3+0} END {print m+0}' "$variant_dir/pod-memory.csv")"

  printf '%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$variant" \
    "${peak_vmrss_kb:-0}" \
    "${peak_working_set_kb:-0}" \
    "${pre_rss:-0}" \
    "${post_rss:-0}" \
    "${freed:-0}" \
    "$load_rc" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    >> "$RUN_DIR/variants.csv"

  echo "==> [$variant] done  peak_vmrss_kb=${peak_vmrss_kb} peak_working_set_kb=${peak_working_set_kb} pre_trim=${pre_rss} post_trim=${post_rss} freed=${freed}"
}

TS="$(date +%Y%m%dT%H%M%S)"
RUN_DIR="$OUT_DIR/$TS"
mkdir -p "$RUN_DIR"
echo "variant,peak_vmrss_kb,peak_working_set_kb,pre_trim_rss_bytes,post_trim_rss_bytes,trim_freed_bytes,load_rc,finished_at_utc" \
  > "$RUN_DIR/variants.csv"

echo "==> run dir:    $RUN_DIR"
echo "==> namespace:  $NAMESPACE"
echo "==> deployment: $DEPLOYMENT"
echo "==> corpus:     $CORPUS_DIR"
echo "==> variants:   $VARIANTS"

IFS=',' read -r -a VARIANT_LIST <<< "$VARIANTS"
overall_rc=0
for variant in "${VARIANT_LIST[@]}"; do
  variant_dir="$RUN_DIR/$variant"
  if ! run_variant "$variant" "$variant_dir"; then
    echo "==> variant failed: $variant" >&2
    overall_rc=2
  fi
done

echo
echo "===================== summary ========================="
column -s, -t < "$RUN_DIR/variants.csv"
echo "======================================================="
echo
target_bytes=$((TARGET_RSS_MB * 1024 * 1024))
awk -F, -v target="$target_bytes" 'NR>1 && $5+0>0 && $5+0<=target {print "  MEETS TARGET: "$1" (post_trim_rss "$5" bytes <= "target" bytes)"}' "$RUN_DIR/variants.csv" \
  || echo "  No variant met the ${TARGET_RSS_MB} MB post-trim RSS target."

exit "$overall_rc"
