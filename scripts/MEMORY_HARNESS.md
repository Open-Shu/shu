# Memory harness (SHU-731)

Three tools for three jobs. Pick the right one for what you're trying to learn.

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

The `baseline` and `post-fix` variants apply no env overrides — you switch Python code via `git checkout` between runs, same corpus, same driver. Compare peak + post-trim RSS from `variants.csv`.

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

Purpose: the real gate. Runs each variant against a live deployment, patching the configmap + rolling the deployment + sampling `kubectl top pod` + hitting the admin endpoints.

```bash
export SHU_LAB_ADMIN_TOKEN=eyJhbGciOi...   # admin JWT
./scripts/memory_bench_lab.sh \
    --corpus-dir data/attachments \
    --namespace shu-billing-lab \
    --deployment shu-api \
    --configmap shu-config \
    --base-url http://localhost \
    --variants glibc-default,glibc-arena2,jemalloc,jemalloc-trim,glibc-arena2-trim \
    --drain-timeout 1200
```

Per-variant it:

1. Writes + applies a targeted `kubectl patch configmap` (`MALLOC_ARENA_MAX`, `SHU_MEMORY_TRIM_INTERVAL_SECONDS`, `SHU_DISABLE_JEMALLOC`).
2. `kubectl rollout restart deploy` and waits for healthy rollout.
3. Samples `kubectl top pod` every 5s (Kubernetes working-set metric — what OOM decisions are made on).
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
