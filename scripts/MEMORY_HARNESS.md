# Memory + CPU harness (SHU-731, extended for SHU-739)

Three tools for three jobs. Pick the right one for what you're trying to learn.

> **SHU-739 protocol additions.** The harness now samples CPU alongside RSS:
> `memory_bench.sh` records `pcpu_avg` (decay-averaged % from `ps`) and a
> per-window `peak_cores` derived from cumulative CPU-time deltas — that
> latter number is what reflects burst spikes. `memory_bench_lab.sh` reads
> `/sys/fs/cgroup/cpu.stat` (cgroup v2) or `cpuacct.usage` (v1) and reports
> the same per-window `peak_cores`. The burst corpus is
> `docs/.pdf-corpus/` (27 PDFs as of 2026-04-29). After the SHU-739 queue
> split, the in-flight concurrency at each ingestion stage is gated by its
> own per-process semaphore: `SHU_INGESTION_CLASSIFY_MAX_CONCURRENT_JOBS`
> for the classifier, `SHU_INGESTION_TEXT_MAX_CONCURRENT_JOBS` for text
> extraction, and `SHU_OCR_MAX_CONCURRENT_JOBS` for the OCR call — not
> `SHU_WORKER_CONCURRENCY`. The upload driver's `--concurrency` flag
> controls how fast the queue fills, not how fast jobs are processed; the
> per-stage caps decide that.

| Tool | Environment | Use for | Authoritative? |
| --- | --- | --- | --- |
| `memory_bench.sh` | native `uvicorn` on the developer host | A/B iteration on Python-level changes (orchestrator fixes, session lifecycle, Pydantic schema use) | No — host allocator may differ from lab |
| `memory_smoke_docker.sh` | local Docker via `make up-full-dev` | One-shot wiring check after touching the Dockerfile / entrypoint / compose env passthrough | No — not a sizing tool |
| `memory_bench_lab.sh` | Kubernetes (default: `shu-billing-lab`) | Sizing decisions, allocator variant comparison, the ≤500 MB/instance target | **Yes** — runs against the real target environment |

## 1. `memory_bench.sh` — native A/B on your host

Purpose: fast loop for Python-level memory changes. The orchestrator fixes in SHU-731 (ORM identity-map expunge, column-only summaries query, no `chunk_profiles` accumulation) are Python-heap changes — they show up in RSS on any OS, so local iteration is valid.

```bash
make up-dev                      # Postgres + Redis only
./scripts/memory_bench.sh \
    --corpus-dir data/attachments \
    --variants baseline,post-fix \
    --max-files 50 \
    --drain-timeout 600
```

The `baseline` and `post-fix` variants apply no env overrides — you switch Python code via `git checkout` between runs, same corpus, same driver. Compare peak + post-trim RSS, plus `peak_pcpu` (decay-averaged) and `peak_cores` (per-window, spike-sensitive), from `variants.csv`.

For SHU-739 burst measurements, the canonical command is:

```bash
make up-dev
./scripts/memory_bench.sh \
    --corpus-dir docs/.pdf-corpus \
    --variants baseline,post-fix \
    --concurrency 6 \
    --drain-timeout 1200
```

The `--concurrency 6` is the upload-driver concurrency — how many parallel uploads the harness pushes — not the in-flight processing concurrency, which is per-stage capped (see the protocol blockquote above). Six parallel uploads is enough to populate the queue quickly so downstream stages see saturation. Per-sample columns in `proc.csv`: `t_s, rss_kb, pcpu_avg, cputime_s`. `peak_cores` in `variants.csv` is the max per-window cores used over the run (delta cputime / delta interval); this is the number that captures the synchronized spike.

Allocator variants (`glibc-default`, `glibc-arena2`, `jemalloc`, `jemalloc-trim`, `glibc-arena2-trim`) run on this harness too, but:

- `MALLOC_ARENA_MAX` is a no-op on macOS (Darwin, not glibc).
- `malloc_trim(0)` doesn't exist on macOS.
- `jemalloc` via `LD_PRELOAD` works on Linux; on macOS it needs `DYLD_INSERT_LIBRARIES` and an unsigned Python — often blocked by SIP.

So treat allocator variants on this harness as rough-sketch at best. For allocator decisions, use the lab harness.

## 2. `memory_smoke_docker.sh` — container wiring smoke test

Purpose: verify the image still boots with jemalloc correctly `LD_PRELOAD`ed and env passthrough wired, after Dockerfile / entrypoint / compose changes.

```bash
./scripts/memory_smoke_docker.sh
```

Checks:

- `/etc/shu-jemalloc-path` is present (written at image build).
- The recorded path exists and is readable.
- `LD_PRELOAD` on pid 1 matches the recorded jemalloc path.
- `libjemalloc` is actually mapped into pid 1's address space (the real test).
- `MALLOC_ARENA_MAX` and `SHU_MEMORY_TRIM_INTERVAL_SECONDS` are visible in pid 1's environment.
- `/api/v1/resources/heap-stats` responds.

Not a performance test. Run after every Dockerfile / entrypoint / compose touch. Takes ~60s.

## 3. `memory_bench_lab.sh` — authoritative sizing in Kubernetes

Purpose: the real gate. Runs each variant against a live deployment, patching the configmap + rolling the deployment + sampling per-pod cgroup stats directly via `kubectl exec` + hitting the admin endpoints.

Generate the admin token from inside the lab pod (the deployed image ships
`scripts/generate_test_token.py`):

```bash
POD=$(kubectl -n shu-billing-lab get pod -l app=shu-api \
        -o jsonpath='{.items[0].metadata.name}')
export SHU_LAB_ADMIN_TOKEN=$(kubectl exec -n shu-billing-lab "$POD" -- \
        python /app/scripts/generate_test_token.py \
        | awk '/^Token:/ {print $2}')
```

Then invoke the harness:

```bash
./scripts/memory_bench_lab.sh \
    --corpus-dir data/attachments \
    --namespace shu-billing-lab \
    --deployment shu-api \
    --configmap shu-config \
    --base-url http://localhost \
    --variants glibc-default,glibc-arena2,jemalloc,jemalloc-trim,glibc-arena2-trim \
    --drain-timeout 1200
```

For SHU-739 burst measurements (single variant matching prod, instrumented CPU + RSS):

```bash
./scripts/memory_bench_lab.sh \
    --corpus-dir docs/.pdf-corpus \
    --namespace shu-billing-lab \
    --variants glibc-arena2-trim \
    --concurrency 6 \
    --drain-timeout 1800
```

Per-variant it:

1. Writes + applies a targeted `kubectl patch configmap` (`MALLOC_ARENA_MAX`, `SHU_MEMORY_TRIM_INTERVAL_SECONDS`, `SHU_DISABLE_JEMALLOC`).
2. `kubectl rollout restart deploy` and waits for healthy rollout.
3. Samples per-pod `/proc/1/status` (VmRSS), `memory.{current,stat}` (working set — what kubelet OOM decisions are made on), and `cpu.stat` / `cpuacct.usage` (cumulative CPU usec) every 5s via `kubectl exec`. The post-run summary derives `peak_cores` from the cgroup CPU-usage deltas — same definition as the native harness, so numbers compare directly.
4. Runs the upload driver through the cluster gateway.
5. Captures `/api/v1/resources/heap-stats` (pre) + `/heap-stats/trim` (post) for RSS + freed-bytes delta.
6. Pulls pod logs — `job_memory_delta` lines, per-job, for offline attribution.

Output: `scripts/memory_bench_lab_results/<timestamp>/variants.csv` plus per-variant configmap patches, sample CSVs, heap stats JSON, and pod logs.

### Why this is the gate

The lab runs on Docker Desktop's Kubernetes — same Debian/glibc container image, same cgroup enforcement, same `memory.current - inactive_file` accounting as production. Local native numbers don't predict lab numbers when the allocator matters; local Docker-on-Mac numbers don't either (Docker Desktop is a Linux VM with fixed memory, no realistic cgroup pressure). The lab is the only local environment that reflects production.

## Variants

| Variant | `MALLOC_ARENA_MAX` | jemalloc | `SHU_MEMORY_TRIM_INTERVAL_SECONDS` |
| --- | --- | --- | --- |
| `glibc-default` | unset (= glibc default 8×nproc) | disabled via `SHU_DISABLE_JEMALLOC=1` | 0 |
| `glibc-arena2` | `2` | disabled | 0 |
| `jemalloc` | unset | enabled (image default) | 0 |
| `jemalloc-trim` | unset | enabled | 60 |
| `glibc-arena2-trim` | `2` | disabled | 60 |

## Pipe the admin endpoints into your investigation

The `/api/v1/resources/heap-stats/*` endpoints are the real diagnostic layer — the harness is just one caller.

- `GET /heap-stats` — gc stats, top object types, asyncio tasks, RSS, tracemalloc top-N (when enabled).
- `POST /heap-stats/trim` — forces `gc.collect() + malloc_trim(0)`, returns before/after RSS.
- `POST /heap-stats/tracemalloc/start` (+ nframes) → `snapshot` → run workload → `GET /heap-stats/tracemalloc/diff` — line-level attribution of what a workload retained.

`docs/deployment/DEPLOYMENT_GUIDE.md#memory-and-per-pod-sizing` has the cluster-side incident-response walkthrough using these.
