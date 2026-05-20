"""CLI entry point for running answer-utility evaluation (SHU-647).

Automates the manual case study workflow from SHU-651: for each query,
collects baseline and multi-surface results, sends them to an LLM judge
via Shu's model configuration system, and aggregates verdicts.

Usage:
    # Run evaluation against SciFact with a specific model config
    python -m tests.benchmark.run_answer_utility_eval \\
        --dataset scifact --reuse-kb <kb-id> --model-config <config-id>

    # With weight tuning
    python -m tests.benchmark.run_answer_utility_eval \\
        --dataset scifact --reuse-kb <kb-id> --model-config <config-id> \\
        --weight chunk_vector=0.40 --weight query_match=0.30 \\
        --fusion-formula weighted_average

    # Aggregate existing results only (no new LLM calls)
    python -m tests.benchmark.run_answer_utility_eval \\
        --aggregate --dataset scifact --model claude-haiku-4-5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shu.core.logging import get_logger

logger = get_logger(__name__)

DATASETS_DIR = Path(__file__).parent / ".datasets"
CASE_STUDY_DIR = Path(__file__).parent / ".case-study"

# Judgment prompt — same criteria as SHU-651 export
JUDGMENT_PROMPT = """Please evaluate these two result sets on the following criteria.
Each document may contain multiple retrieved chunks.
Some results include annotations (synopsis, matched query, summary) —
use these to understand why each document was retrieved, but judge
on the chunk content itself.

## 1. Retrieval Relevance (traditional IR)
Which set contains more documents that are relevant to the query?
Which set ranks the most relevant documents higher?

## 2. Answer Utility
If these chunks were the only context available to answer the query,
which set better equips you to give a thorough, accurate answer?
What information is available in one set but missing from the other?

## Judgment
For each criterion, state which set is better (Set A, Set B, or Tie)
with brief reasoning.

## Output Format

Include your full reasoning above, then end your response with a structured
verdict block exactly like this (copy the template, fill in values):

```verdict
judge_model: [your model name and version]
retrieval_relevance: [Set A | Set B | Tie]
answer_utility: [Set A | Set B | Tie]
overall: [Set A | Set B | Tie]
confidence: [high | medium | low]
notes: [one sentence on the key differentiator]
```"""


# ---------- Data structures ----------

@dataclass
class Verdict:
    """Parsed verdict from an LLM judge response."""

    query_id: str
    judge_model: str = ""
    retrieval_relevance: str = ""
    answer_utility: str = ""
    overall: str = ""
    confidence: str = ""
    notes: str = ""
    full_response: str = ""
    error: str | None = None


@dataclass
class AggregateReport:
    """Summary statistics from a set of verdicts."""

    total: int = 0
    ms_wins: int = 0
    bl_wins: int = 0
    ties: int = 0
    errors: int = 0
    high_conf: int = 0
    medium_conf: int = 0
    low_conf: int = 0
    verdicts: list[Verdict] = field(default_factory=list)


# ---------- Verdict parsing ----------

def parse_verdict_block(text: str) -> dict[str, str]:
    """Extract fields from a ```verdict block."""
    match = re.search(r"```verdict\s*\n(.*?)```", text, re.DOTALL)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).strip().split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def _deblind(value: str, ms_is_set_a: bool) -> str:
    """Convert blinded verdict (Set A/Set B) back to strategy names."""
    v = value.strip()
    if v == "Tie":
        return "Tie"
    if "Set A" in v:
        return "Multi-Surface" if ms_is_set_a else "Baseline"
    if "Set B" in v:
        return "Baseline" if ms_is_set_a else "Multi-Surface"
    return v


def _deblind_text(text: str, ms_is_set_a: bool) -> str:
    """Replace all Set A/Set B references in free text with strategy names."""
    set_a_name = "Multi-Surface" if ms_is_set_a else "Baseline"
    set_b_name = "Baseline" if ms_is_set_a else "Multi-Surface"
    return text.replace("Set A", set_a_name).replace("Set B", set_b_name)


def parse_verdict(query_id: str, response_text: str, ms_is_set_a: bool = False) -> Verdict:
    """Parse a full LLM response into a Verdict, de-blinding Set A/B labels."""
    fields = parse_verdict_block(response_text)
    if not fields:
        return Verdict(
            query_id=query_id,
            full_response=response_text,
            error="No verdict block found in response",
        )
    return Verdict(
        query_id=query_id,
        judge_model=fields.get("judge_model", ""),
        retrieval_relevance=_deblind(fields.get("retrieval_relevance", ""), ms_is_set_a),
        answer_utility=_deblind(fields.get("answer_utility", ""), ms_is_set_a),
        overall=_deblind(fields.get("overall", ""), ms_is_set_a),
        confidence=fields.get("confidence", ""),
        notes=_deblind_text(fields.get("notes", ""), ms_is_set_a),
        full_response=response_text,
    )


# ---------- Prompt formatting (Python port of exportForJudgment.js) ----------

def _format_chunk(chunk: dict, index: int) -> str:
    """Format a single chunk with annotations."""
    score = chunk.get("score", 0)
    promoted = chunk.get("promoted", False)
    suffix = ", promoted — document-level match" if promoted else ""
    lines = [f"### Chunk {index} (score: {score:.4f}{suffix})"]

    matched_query = chunk.get("matched_query")
    if matched_query:
        lines.append(f'> Matched query: "{matched_query}"')

    summary = chunk.get("summary")
    if summary:
        lines.append(f"> Summary: {summary}")

    lines.append("")
    lines.append(chunk.get("content", "") or "(no content)")
    return "\n".join(lines)


def _format_document(doc: dict, rank: int, *, show_surfaces: bool = False) -> str:
    """Format a document with its chunks."""
    score = doc.get("final_score") or doc.get("score", 0)
    lines = [f"## Document {rank}: {doc.get('document_title', 'Unknown')} (score: {score:.4f})"]

    title_summary = doc.get("title_summary")
    if title_summary:
        lines.append(f"> {title_summary}")

    if show_surfaces:
        surface_scores = doc.get("surface_scores", {})
        if surface_scores:
            surfaces = ", ".join(f"{s}={v:.4f}" for s, v in surface_scores.items())
            lines.append(f"Surfaces: {surfaces}")

    synopsis = doc.get("synopsis")
    if synopsis:
        lines.append("")
        lines.append(f"> Synopsis: {synopsis}")

    chunks = doc.get("chunks", [])
    for i, chunk in enumerate(chunks):
        lines.append("")
        lines.append(_format_chunk(chunk, i + 1))

    if not chunks and not synopsis:
        lines.append("")
        lines.append("(no chunks retrieved for this document)")

    return "\n".join(lines)


def _group_baseline_by_document(results: list[dict]) -> list[dict]:
    """Group baseline chunk results by document."""
    groups: dict[str, dict] = {}
    for result in results:
        doc_id = result.get("document_id", "")
        if doc_id not in groups:
            groups[doc_id] = {
                "document_title": result.get("document_title", "Unknown"),
                "score": result.get("similarity_score", 0),
                "chunks": [],
            }
        group = groups[doc_id]
        score = result.get("similarity_score", 0)
        group["score"] = max(group["score"], score)
        group["chunks"].append({
            "score": score,
            "content": result.get("content", "(no content)"),
        })
    return list(groups.values())


def format_comparison_prompt(
    query_id: str,
    query_text: str,
    baseline_results: list[dict],
    formatted_results: list[dict],
    top_n: int = 10,
) -> tuple[str, bool]:
    """Format a blinded comparison prompt for the LLM judge.

    Randomly assigns baseline and multi-surface to Set A / Set B to
    eliminate positional bias. Each call is independent, but LLMs can
    exhibit systematic preference for the first or second set shown.

    Returns:
        Tuple of (prompt_text, ms_is_set_a) where ms_is_set_a indicates
        whether multi-surface was assigned to Set A.
    """
    import random

    baseline_docs = _group_baseline_by_document(baseline_results)[:top_n]
    ms_docs = formatted_results[:top_n]

    baseline_formatted = [_format_document(doc, i + 1) for i, doc in enumerate(baseline_docs)]
    ms_formatted = [_format_document(doc, i + 1, show_surfaces=True) for i, doc in enumerate(ms_docs)]

    ms_is_set_a = random.choice([True, False])

    if ms_is_set_a:
        set_a_docs, set_b_docs = ms_formatted, baseline_formatted
    else:
        set_a_docs, set_b_docs = baseline_formatted, ms_formatted

    sections = [
        "# Query",
        f"{query_id}: {query_text}",
        "",
        "# Set A",
        *set_a_docs,
        "",
        "# Set B",
        *set_b_docs,
        "",
        "# Judgment Prompt",
        "",
        JUDGMENT_PROMPT,
    ]
    return "\n".join(sections), ms_is_set_a


# ---------- LLM Judge ----------

async def call_judge(
    prompt: str,
    model_config_id: str,
    client,
    auth_headers: dict,
) -> str:
    """Send a judgment prompt to Shu's chat API and return the response.

    Creates a temporary conversation, sends the prompt, collects the
    response via SSE, and deletes the conversation.
    """
    from tests.integ.helpers.api_helpers import process_streaming_result

    # Create temporary conversation
    conv_resp = await client.post(
        "/api/v1/chat/conversations",
        json={"title": "Answer Utility Judge", "model_configuration_id": model_config_id},
        headers=auth_headers,
    )
    if conv_resp.status_code != 200:
        raise RuntimeError(f"Failed to create conversation: {conv_resp.status_code} {conv_resp.text}")

    conv_data = conv_resp.json().get("data", {})
    conversation_id = conv_data["id"]

    try:
        # Send the prompt
        msg_resp = await client.post(
            f"/api/v1/chat/conversations/{conversation_id}/send",
            json={"message": prompt, "rag_rewrite_mode": "no_rag"},
            headers=auth_headers,
        )
        if msg_resp.status_code != 200:
            raise RuntimeError(f"Failed to send message: {msg_resp.status_code} {msg_resp.text}")

        result = await process_streaming_result(msg_resp)
        if isinstance(result, dict):
            return result.get("content", "")
        return str(result or "")
    finally:
        # Clean up conversation
        await client.delete(
            f"/api/v1/chat/conversations/{conversation_id}",
            headers=auth_headers,
        )


# ---------- Collection ----------

async def collect_results_for_query(
    query_text: str,
    kb_id: str,
    client,
    auth_headers: dict,
    search_limit: int = 10,
    threshold: float = 0.0,
    weight_overrides: dict[str, float] | None = None,
    fusion_formula: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Collect baseline and multi-surface results for a single query.

    Returns:
        Tuple of (baseline_results, formatted_results).
    """
    # Baseline: similarity search
    bl_payload: dict[str, Any] = {
        "query": query_text,
        "query_type": "similarity",
        "limit": search_limit,
        "similarity_threshold": threshold,
        "rag_rewrite_mode": "raw_query",
    }
    bl_resp = await client.post(f"/api/v1/query/{kb_id}/search", json=bl_payload, headers=auth_headers)
    if bl_resp.status_code != 200:
        logger.error("Baseline search failed (%d): %s", bl_resp.status_code, query_text[:80])
    bl_data = bl_resp.json().get("data", {}) if bl_resp.status_code == 200 else {}
    baseline_results = bl_data.get("results", [])

    # Multi-surface search
    ms_payload: dict[str, Any] = {
        "query": query_text,
        "query_type": "multi_surface",
        "limit": search_limit,
        "similarity_threshold": threshold,
        "rag_rewrite_mode": "raw_query",
    }
    if weight_overrides:
        ms_payload.update(weight_overrides)
    if fusion_formula:
        ms_payload["fusion_formula"] = fusion_formula

    ms_resp = await client.post(f"/api/v1/query/{kb_id}/search", json=ms_payload, headers=auth_headers)
    if ms_resp.status_code != 200:
        logger.error("Multi-surface search failed (%d): %s", ms_resp.status_code, query_text[:80])
    ms_data = ms_resp.json().get("data", {}) if ms_resp.status_code == 200 else {}
    formatted_results = ms_data.get("formatted_results", [])

    return baseline_results, formatted_results


# ---------- Aggregation ----------

def aggregate_verdicts(verdicts: list[Verdict]) -> AggregateReport:
    """Compute summary statistics from a list of verdicts."""
    report = AggregateReport(total=len(verdicts), verdicts=verdicts)
    for v in verdicts:
        if v.error:
            report.errors += 1
            continue
        overall = v.overall.lower().strip()
        if "multi" in overall:
            report.ms_wins += 1
        elif "baseline" in overall:
            report.bl_wins += 1
        else:
            report.ties += 1

        conf = v.confidence.lower().strip()
        if conf == "high":
            report.high_conf += 1
        elif conf == "medium":
            report.medium_conf += 1
        elif conf == "low":
            report.low_conf += 1
    return report


def format_aggregate_report(report: AggregateReport, corpus: str, model_filter: str | None = None) -> str:
    """Generate a markdown summary report."""
    decided = report.ms_wins + report.bl_wins
    ms_pct = (report.ms_wins / decided * 100) if decided > 0 else 0
    bl_pct = (report.bl_wins / decided * 100) if decided > 0 else 0

    title = f"# Answer Utility Report: {corpus}"
    if model_filter:
        title += f" ({model_filter})"

    lines = [
        title,
        f"Queries evaluated: {report.total}",
        "",
        "| Outcome | Count | % |",
        "|---------|-------|---|",
        f"| Multi-Surface wins | {report.ms_wins} | {ms_pct:.0f}% |",
        f"| Baseline wins | {report.bl_wins} | {bl_pct:.0f}% |",
        f"| Tie | {report.ties} | {report.ties / report.total * 100:.0f}% |" if report.total > 0 else "| Tie | 0 | 0% |",
    ]

    if report.errors > 0:
        lines.append(f"| Errors | {report.errors} | — |")

    lines.extend([
        "",
        f"MS wins **{ms_pct:.0f}%** of decided matchups ({report.ms_wins} of {decided}).",
        "",
        f"Confidence: {report.high_conf} high, {report.medium_conf} medium, {report.low_conf} low",
        "",
        "## Per-Query Detail",
        "",
        "| Query | Model | Relevance | Utility | Overall | Confidence | Notes |",
        "|-------|-------|-----------|---------|---------|------------|-------|",
    ])

    for v in report.verdicts:
        if v.error:
            lines.append(f"| {v.query_id} | — | ERROR | — | — | — | {v.error} |")
        else:
            lines.append(
                f"| {v.query_id} | {v.judge_model} | {v.retrieval_relevance} | {v.answer_utility} "
                f"| {v.overall} | {v.confidence} | {v.notes} |"
            )

    return "\n".join(lines)


def read_verdicts_from_disk(corpus_dir: Path, model_filter: str | None = None) -> list[Verdict]:
    """Read and parse verdict blocks from existing case study files."""
    verdicts = []
    for md_file in sorted(corpus_dir.glob("*.md")):
        # Skip summary/meta files
        if md_file.stem.startswith("_"):
            continue
        # Filter by model name if specified
        if model_filter and model_filter not in md_file.stem:
            continue

        text = md_file.read_text()
        # Extract query_id and model from filename: q{id}_{model}.md
        stem = md_file.stem
        parts = stem.split("_", 1)
        query_id = parts[0]
        model_from_filename = parts[1] if len(parts) > 1 else ""

        # Extract ms_is_set_a from de-blinded footer
        ms_is_set_a = False
        deblind_match = re.search(r"Set A = (Multi-Surface|Baseline)", text)
        if deblind_match:
            ms_is_set_a = deblind_match.group(1) == "Multi-Surface"

        verdict = parse_verdict(query_id, text, ms_is_set_a)
        # Override judge_model with filename-derived model (more reliable than LLM self-ID)
        if model_from_filename:
            verdict.judge_model = model_from_filename
        verdicts.append(verdict)
    return verdicts


# ---------- Main pipeline ----------

async def run_evaluation(args: argparse.Namespace) -> None:
    """Run the full evaluation pipeline."""
    from tests.integ.integration_test_runner import IntegrationTestRunner

    from .beir_loader import BeirLoader

    dataset_dir = _resolve_dataset_dir(args.dataset)
    corpus_name = dataset_dir.name

    # Load queries
    loader = BeirLoader(dataset_dir)
    queries = loader._load_queries()
    if args.qrels_only:
        qrels = loader._load_qrels("test")
        before = len(queries)
        queries = {qid: q for qid, q in queries.items() if qid in qrels}
        logger.info("Filtered to %d queries with qrels (from %d total)", len(queries), before)
    if args.queries:
        target_ids = {qid.strip() for qid in args.queries.split(",")}
        queries = {qid: q for qid, q in queries.items() if qid in target_ids}
        missing = target_ids - set(queries.keys())
        if missing:
            logger.warning("Query IDs not found in dataset: %s", ", ".join(sorted(missing)))
    if args.max_queries:
        query_items = list(queries.items())[:args.max_queries]
        queries = dict(query_items)
    logger.info("Evaluating %d queries from %s", len(queries), corpus_name)

    # Parse weight overrides
    weight_overrides: dict[str, float] = {}
    for w in (args.weights or []):
        if "=" not in w:
            logger.error("Invalid --weight format '%s', expected SURFACE=VALUE", w)
            return
        surface, value = w.split("=", 1)
        weight_overrides[surface.strip()] = float(value.strip())

    # Convert to API payload format
    weight_payload: dict[str, Any] = {}
    weight_name_map = {
        "chunk_vector": "chunk_vector_weight",
        "query_match": "query_match_weight",
        "synopsis_match": "synopsis_match_weight",
        "bm25": "bm25_weight",
        "chunk_summary": "chunk_summary_weight",
    }
    for surface, value in weight_overrides.items():
        api_key = weight_name_map.get(surface, f"{surface}_weight")
        weight_payload[api_key] = value

    # Set up output directory
    output_dir = CASE_STUDY_DIR / corpus_name
    if args.run_name:
        output_dir = output_dir / args.run_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Set up test infrastructure
    runner = IntegrationTestRunner()
    await runner.setup()

    try:
        # Look up actual model name from model config (don't trust LLM self-identification)
        mc_resp = await runner.client.get(
            f"/api/v1/model-configurations/{args.model_config}",
            headers=runner.auth_headers,
        )
        mc_data = mc_resp.json().get("data", {})
        actual_model_name = mc_data.get("model_name", "unknown")
        # Sanitize for filename
        actual_model_name = re.sub(r"[^a-zA-Z0-9._-]", "-", actual_model_name).strip("-")
        logger.info("Judge model: %s", actual_model_name)

        total = len(queries)
        completed = 0
        sem = asyncio.Semaphore(args.concurrency)

        async def eval_query(query_id: str, query) -> Verdict:
            nonlocal completed
            query_text = query.text if hasattr(query, "text") else str(query)

            async with sem:
                # Collect results
                baseline, formatted = await collect_results_for_query(
                    query_text=query_text,
                    kb_id=args.reuse_kb,
                    client=runner.client,
                    auth_headers=runner.auth_headers,
                    search_limit=args.limit,
                    threshold=args.threshold,
                    weight_overrides=weight_payload or None,
                    fusion_formula=args.fusion_formula,
                )

                if not baseline and not formatted:
                    logger.warning("No results for query %s, skipping", query_id)
                    return Verdict(query_id=query_id, error="No results above threshold from either strategy")

                # Format blinded comparison prompt (randomized A/B assignment)
                prompt, ms_is_set_a = format_comparison_prompt(
                    query_id=query_id,
                    query_text=query_text,
                    baseline_results=baseline,
                    formatted_results=formatted,
                    top_n=args.limit,
                )

                # Call LLM judge
                try:
                    response_text = await call_judge(
                        prompt=prompt,
                        model_config_id=args.model_config,
                        client=runner.client,
                        auth_headers=runner.auth_headers,
                    )
                except Exception as e:
                    logger.error("Judge call failed for %s: %s", query_id, e)
                    return Verdict(query_id=query_id, error=str(e))

                # Parse verdict and de-blind Set A/B → strategy names
                verdict = parse_verdict(query_id, response_text, ms_is_set_a)
                # Override judge_model with actual model name (LLMs misidentify themselves)
                verdict.judge_model = actual_model_name

                # Write per-query result with de-blinded footer
                set_a_label = "Multi-Surface" if ms_is_set_a else "Baseline"
                set_b_label = "Baseline" if ms_is_set_a else "Multi-Surface"
                footer = (
                    f"\n\n---\n"
                    f"_De-blinded: Set A = {set_a_label}, Set B = {set_b_label}_\n"
                    f"_Verdict: {verdict.overall} (confidence: {verdict.confidence})_\n"
                )
                output_file = output_dir / f"q{query_id}_{actual_model_name}.md"
                output_file.write_text(response_text + footer)

                completed += 1
                logger.info(
                    "[%d/%d] %s → %s (confidence: %s)",
                    completed, total, query_id, verdict.overall, verdict.confidence,
                )
                return verdict

        verdicts = await asyncio.gather(
            *[eval_query(qid, q) for qid, q in queries.items()]
        )

        # Aggregate and write report
        report = aggregate_verdicts(verdicts)
        report_text = format_aggregate_report(report, corpus_name)
        report_file = output_dir / "_summary.md"
        report_file.write_text(report_text)
        logger.info("Summary written to %s", report_file)
        print(report_text)

    finally:
        await runner.teardown()


async def run_aggregate_only(args: argparse.Namespace) -> None:
    """Aggregate existing case study results without running new evaluations."""
    corpus_name = args.dataset
    corpus_dir = CASE_STUDY_DIR / corpus_name
    if args.run_name:
        corpus_dir = corpus_dir / args.run_name

    if not corpus_dir.is_dir():
        logger.error("No case study directory found at %s", corpus_dir)
        return

    verdicts = read_verdicts_from_disk(corpus_dir, args.model)
    if not verdicts:
        logger.warning("No verdict files found in %s", corpus_dir)
        return

    report = aggregate_verdicts(verdicts)
    report_text = format_aggregate_report(report, corpus_name, args.model)

    report_file = corpus_dir / "_summary.md"
    report_file.write_text(report_text)
    logger.info("Summary written to %s", report_file)
    print(report_text)


# ---------- CLI ----------

def _resolve_dataset_dir(dataset: str) -> Path:
    """Resolve dataset name to directory."""
    path = Path(dataset)
    if path.is_dir():
        return path
    named = DATASETS_DIR / dataset
    if named.is_dir():
        return named
    raise FileNotFoundError(f"Dataset '{dataset}' not found at {path} or {named}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Answer-Utility Evaluation for Multi-Surface Retrieval (SHU-647)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--dataset", required=True, help="Dataset name (e.g., 'scifact', 'nfcorpus')")
    parser.add_argument("--reuse-kb", type=str, help="Knowledge base ID to search against")
    parser.add_argument("--model-config", type=str, help="Model configuration ID for the LLM judge")
    parser.add_argument("--limit", type=int, default=10, help="Number of results per strategy (default: 10)")
    parser.add_argument("--threshold", type=float, default=0.3, help="Score threshold (default: 0.3)")
    parser.add_argument("--queries", type=str, default=None, help="Comma-separated query IDs to evaluate (e.g. '822,1303,219')")
    parser.add_argument("--qrels-only", action="store_true", help="Only evaluate queries that have qrels (for BEIR comparison)")
    parser.add_argument("--run-name", type=str, default=None, help="Name for this run (creates subfolder under corpus, e.g. 'tuned-weights-v1')")
    parser.add_argument(
        "--weight", action="append", default=[], dest="weights", metavar="SURFACE=VALUE",
        help="Surface weight override (can repeat, e.g. --weight chunk_vector=0.40)",
    )
    parser.add_argument("--fusion-formula", type=str, default=None, help="Fusion formula override")
    parser.add_argument("--max-queries", type=int, default=None, help="Max queries to evaluate (default: all)")
    parser.add_argument("--concurrency", type=int, default=1, help="Max concurrent LLM judge calls (default: 1)")
    parser.add_argument("--aggregate", action="store_true", help="Aggregate existing results only (no LLM calls)")
    parser.add_argument("--model", type=str, default=None, help="Filter aggregation by model name substring")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main():
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.aggregate:
        asyncio.run(run_aggregate_only(args))
    else:
        if not args.reuse_kb:
            logger.error("--reuse-kb is required for evaluation runs")
            return
        if not args.model_config:
            logger.error("--model-config is required for evaluation runs")
            return
        asyncio.run(run_evaluation(args))


if __name__ == "__main__":
    main()
