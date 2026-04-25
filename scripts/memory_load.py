#!/usr/bin/env python3
"""Upload driver for the memory harness (SHU-731).

Uploads a corpus of documents to a running Shu backend and waits for the
ingestion + profiling pipeline to drain. Designed to be driven by
``scripts/memory_bench.sh`` but can be run standalone:

    python scripts/memory_load.py \\
        --base-url http://localhost:8000 \\
        --corpus-dir /path/to/pdfs \\
        --kb-name memory-harness \\
        --concurrency 4 \\
        --drain-timeout 900

Authentication: pass ``--token`` / ``$SHU_HARNESS_TOKEN`` with a JWT, or
``--generate-token`` which shells out to ``backend/scripts/generate_test_token.py``
and extracts the token.

The driver is intentionally I/O-bound and allocator-light so its own memory
profile does not perturb the server-side measurement.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover
    print("httpx is required. Install via: pip install httpx", file=sys.stderr)
    sys.exit(1)


CORPUS_EXTENSIONS = {".pdf", ".md", ".txt", ".docx"}


def _iter_corpus(corpus_dir: Path) -> list[Path]:
    if not corpus_dir.is_dir():
        raise FileNotFoundError(f"corpus directory not found: {corpus_dir}")
    files = sorted(p for p in corpus_dir.rglob("*") if p.is_file() and p.suffix.lower() in CORPUS_EXTENSIONS)
    if not files:
        raise FileNotFoundError(f"no supported documents in {corpus_dir} (extensions: {CORPUS_EXTENSIONS})")
    return files


def _generate_token(shu_repo: Path) -> str:
    """Run backend/scripts/generate_test_token.py and extract the token."""
    script = shu_repo / "backend" / "scripts" / "generate_test_token.py"
    if not script.exists():
        raise FileNotFoundError(f"generate_test_token.py not found at {script}")
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(shu_repo / "backend" / "src"))
    out = subprocess.run(
        [sys.executable, str(script)],
        cwd=shu_repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    match = re.search(r"^Token:\s*(\S+)", out, re.MULTILINE)
    if not match:
        raise RuntimeError(f"could not parse token from generate_test_token.py output:\n{out}")
    return match.group(1)


async def _ensure_kb(client: httpx.AsyncClient, name: str) -> str:
    """Return the id of a KB with the given name, creating it if absent."""
    resp = await client.get("/api/v1/knowledge-bases")
    resp.raise_for_status()
    body = resp.json()
    kbs = body.get("data", body) or []
    if isinstance(kbs, dict):
        kbs = kbs.get("items", kbs.get("knowledge_bases", []))
    for kb in kbs:
        if kb.get("name") == name:
            return kb["id"]
    resp = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": name, "description": f"SHU-731 memory harness KB ({name})"},
    )
    resp.raise_for_status()
    data = resp.json().get("data", resp.json())
    return data["id"]


async def _upload_one(client: httpx.AsyncClient, kb_id: str, path: Path, sem: asyncio.Semaphore) -> dict:
    async with sem:
        start = time.monotonic()
        with path.open("rb") as fh:
            files = {"files": (path.name, fh, "application/octet-stream")}
            try:
                resp = await client.post(
                    f"/api/v1/knowledge-bases/{kb_id}/documents/upload",
                    files=files,
                    timeout=httpx.Timeout(300.0, connect=30.0),
                )
            except httpx.HTTPError as exc:
                return {"filename": path.name, "ok": False, "error": str(exc), "elapsed_s": time.monotonic() - start}
        ok = 200 <= resp.status_code < 300
        return {
            "filename": path.name,
            "ok": ok,
            "status": resp.status_code,
            "body": (resp.json() if ok else resp.text[:400]),
            "elapsed_s": time.monotonic() - start,
        }


async def _count_in_flight(client: httpx.AsyncClient, kb_id: str) -> dict[str, int]:
    """Best-effort pipeline-drain check via the documents listing endpoint.

    Returns counts of documents in each pipeline state. We treat a document
    as "in flight" if its status is not RAG_PROCESSED/ERROR. If the listing
    endpoint is unavailable, returns {"unknown": 1} so callers fall back to
    a time-based drain.
    """
    try:
        resp = await client.get(
            f"/api/v1/knowledge-bases/{kb_id}/documents",
            params={"limit": 1000},
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return {"unknown": 1}
    payload = resp.json()
    docs = payload.get("data", payload)
    if isinstance(docs, dict):
        docs = docs.get("items", docs.get("documents", []))
    counts: dict[str, int] = {}
    for doc in docs or []:
        # The list endpoint returns ``processing_status``. ``status`` is kept
        # as a best-effort fallback for older schemas or future renames.
        status = (doc.get("processing_status") or doc.get("status") or "unknown").lower()
        counts[status] = counts.get(status, 0) + 1
    return counts


async def _drain(client: httpx.AsyncClient, kb_id: str, timeout: float, terminal: set[str]) -> dict:
    """Poll until all non-terminal documents have drained, or timeout."""
    deadline = time.monotonic() + timeout
    last: dict[str, int] = {}
    while time.monotonic() < deadline:
        counts = await _count_in_flight(client, kb_id)
        last = counts
        in_flight = sum(n for s, n in counts.items() if s not in terminal and s != "unknown")
        if "unknown" in counts:
            # Can't observe drain — fall back to a conservative fixed wait.
            await asyncio.sleep(30)
            return {"drained": False, "reason": "listing_unavailable", "counts": counts}
        if in_flight == 0:
            return {"drained": True, "counts": counts}
        await asyncio.sleep(5)
    return {"drained": False, "reason": "timeout", "counts": last}


async def _run(args: argparse.Namespace) -> int:
    token = args.token or os.environ.get("SHU_HARNESS_TOKEN")
    if not token and args.generate_token:
        token = _generate_token(Path(args.shu_repo).resolve())
    if not token:
        print(
            "ERROR: no auth token. Pass --token, SHU_HARNESS_TOKEN, or --generate-token.",
            file=sys.stderr,
        )
        return 2
    if args.token_out:
        Path(args.token_out).write_text(token)

    corpus = _iter_corpus(Path(args.corpus_dir))
    if args.max_files:
        corpus = corpus[: args.max_files]

    headers = {"Authorization": f"Bearer {token}"}
    start_wall = time.time()

    async with httpx.AsyncClient(base_url=args.base_url, headers=headers, timeout=60.0) as client:
        kb_id = args.kb_id or await _ensure_kb(client, args.kb_name)

        print(f"[load] kb_id={kb_id} files={len(corpus)} concurrency={args.concurrency}")
        sem = asyncio.Semaphore(args.concurrency)
        upload_task_list = [asyncio.create_task(_upload_one(client, kb_id, p, sem)) for p in corpus]
        uploaded = []
        errors = 0
        for coro in asyncio.as_completed(upload_task_list):
            result = await coro
            uploaded.append(result)
            if not result.get("ok"):
                errors += 1
            if len(uploaded) % 10 == 0 or len(uploaded) == len(corpus):
                print(f"[load] uploaded {len(uploaded)}/{len(corpus)}  errors={errors}")

        upload_seconds = time.time() - start_wall
        print(f"[load] upload phase done in {upload_seconds:.1f}s  errors={errors}")

        drain_result: dict = {"drained": False, "reason": "skipped"}
        if not args.no_drain:
            terminal = {s.lower() for s in args.terminal_states.split(",") if s.strip()}
            print(f"[load] draining (timeout={args.drain_timeout}s terminal={sorted(terminal)})")
            drain_result = await _drain(client, kb_id, args.drain_timeout, terminal)
            print(f"[load] drain result: {json.dumps(drain_result)}")

    summary = {
        "kb_id": kb_id,
        "files_requested": len(corpus),
        "upload_errors": errors,
        "upload_seconds": upload_seconds,
        "total_seconds": time.time() - start_wall,
        "drain": drain_result,
    }
    if args.output:
        Path(args.output).write_text(json.dumps(summary, indent=2, default=str))
    print("[load] summary:", json.dumps(summary, indent=2, default=str))
    return 0 if errors == 0 and drain_result.get("drained") in (True, False) else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Memory harness upload driver (SHU-731)")
    p.add_argument("--base-url", default=os.environ.get("SHU_HARNESS_BASE_URL", "http://localhost:8000"))
    p.add_argument("--token", default=None, help="JWT; falls back to SHU_HARNESS_TOKEN env")
    p.add_argument("--generate-token", action="store_true", help="Shell out to generate_test_token.py")
    p.add_argument(
        "--shu-repo",
        default=str(Path(__file__).resolve().parent.parent),
        help="Path to shu/ repo (for generate_test_token.py lookup)",
    )
    p.add_argument("--kb-id", default=None, help="Upload to existing KB id (skips --kb-name lookup)")
    p.add_argument("--kb-name", default="memory-harness", help="KB name to create/reuse when --kb-id not given")
    p.add_argument("--corpus-dir", required=True, help="Directory containing documents to upload")
    p.add_argument("--max-files", type=int, default=0, help="Cap on number of files (0 = all)")
    p.add_argument("--concurrency", type=int, default=4, help="Concurrent upload requests")
    p.add_argument("--drain-timeout", type=float, default=1200.0, help="Seconds to wait for pipeline drain")
    p.add_argument(
        "--terminal-states",
        default="rag_processed,profile_processed,error,failed",
        help="Comma-separated pipeline statuses treated as terminal during drain. "
        "Defaults cover the post-profiling states (rag_processed = profiling done, "
        "profile_processed = artifact embedding done) plus explicit errors.",
    )
    p.add_argument("--no-drain", action="store_true", help="Skip drain polling (upload only)")
    p.add_argument("--output", default=None, help="Write JSON summary to this path")
    p.add_argument("--token-out", default=None, help="Write the resolved JWT to this file (for the bench script)")
    return p


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
