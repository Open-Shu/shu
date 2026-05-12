"""Retrieval Quality Analysis — Per-strategy evaluation of search result quality.

Traditional IR benchmarks (BEIR, NDCG, MAP) score systems against a fixed set of
pre-determined relevant documents. This penalizes systems that find relevant documents
the benchmark didn't anticipate — which is exactly what multi-surface search does.

This module evaluates each search strategy on the quality of what it actually returned:
- Of the top-k documents you returned, how many were relevant?
- For each query, which strategy returned more relevant documents?
- Which relevant documents did each strategy exclusively discover?

Requires candidates.jsonl (from collection phase) and judgments.jsonl (from judging phase).

Usage:
    python -m tests.benchmark.retrieval_quality_analysis --dataset nfcorpus
"""

from __future__ import annotations

import json
from shu.core.logging import get_logger
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

logger = get_logger(__name__)


@dataclass
class StrategyResults:
    """Per-strategy evaluation results."""

    name: str
    precision_at_5: float = 0.0
    precision_at_10: float = 0.0
    highly_relevant_at_10: float = 0.0  # avg count of score=2 docs in top-10
    avg_relevant_at_10: float = 0.0  # avg count of score>0 docs in top-10
    avg_result_count: float = 0.0  # avg docs returned per query
    mrr: float = 0.0  # mean reciprocal rank of first relevant doc
    queries_with_relevant: int = 0  # queries with at least 1 relevant doc in top-10
    total_queries: int = 0


@dataclass
class HeadToHead:
    """Head-to-head comparison between two strategies."""

    multi_surface_wins: int = 0
    baseline_wins: int = 0
    ties: int = 0
    total_queries: int = 0
    # Per-query details for reporting
    win_examples: list[dict] = field(default_factory=list)
    loss_examples: list[dict] = field(default_factory=list)


@dataclass
class ExclusiveDiscovery:
    """Documents found by one strategy but not the other."""

    ms_only_relevant: int = 0  # relevant docs found only by multi-surface
    ms_only_highly_relevant: int = 0
    bl_only_relevant: int = 0  # relevant docs found only by baseline
    bl_only_highly_relevant: int = 0
    both_relevant: int = 0  # relevant docs found by both
    # Examples of multi-surface exclusive discoveries
    examples: list[dict] = field(default_factory=list)


@dataclass
class ThresholdSurvival:
    """Results surviving a score threshold."""

    threshold: float
    baseline_total: int = 0
    baseline_survivors: int = 0
    baseline_survivors_relevant: int = 0
    ms_total: int = 0
    ms_survivors: int = 0
    ms_survivors_relevant: int = 0


@dataclass
class AnalysisResults:
    """Complete analysis results."""

    baseline: StrategyResults = field(default_factory=lambda: StrategyResults(name="baseline"))
    multi_surface: StrategyResults = field(default_factory=lambda: StrategyResults(name="multi_surface"))
    head_to_head: HeadToHead = field(default_factory=HeadToHead)
    exclusive_discovery: ExclusiveDiscovery = field(default_factory=ExclusiveDiscovery)
    threshold_survival: list[ThresholdSurvival] = field(default_factory=list)
    # Query type segmentation
    question_results: dict | None = None
    phrase_results: dict | None = None


def load_data(dataset_dir: Path) -> tuple[list[dict], dict[tuple[str, str], dict]]:
    """Load candidates and judgments, return indexed for lookup."""
    judge_dir = dataset_dir / "relevance_judge"

    candidates = []
    with open(judge_dir / "candidates.jsonl", encoding="utf-8") as f:
        for line in f:
            candidates.append(json.loads(line))

    judgments = {}
    with open(judge_dir / "judgments.jsonl", encoding="utf-8") as f:
        for line in f:
            j = json.loads(line)
            judgments[(j["query_id"], j["doc_id"])] = j

    logger.info("Loaded %d candidates, %d judgments", len(candidates), len(judgments))
    return candidates, judgments


def _is_question(query_text: str) -> bool:
    """Heuristic: is this a natural question or a topic phrase?"""
    if "?" in query_text:
        return True
    first_word = query_text.lower().split()[0] if query_text.strip() else ""
    return first_word in (
        "what", "how", "why", "when", "where", "which",
        "do", "does", "is", "are", "can", "should", "will",
    )


def _build_per_query_results(
    candidates: list[dict],
    judgments: dict[tuple[str, str], dict],
) -> dict[str, dict]:
    """Build per-query result lists for each strategy.

    Returns: {query_id: {
        "query_text": str,
        "baseline": [(doc_id, score, relevance), ...],  # sorted by score desc
        "multi_surface": [(doc_id, score, relevance), ...],
    }}
    """
    per_query: dict[str, dict] = {}

    for c in candidates:
        qid = c["query_id"]
        if qid not in per_query:
            per_query[qid] = {
                "query_text": c["query_text"],
                "baseline": [],
                "multi_surface": [],
            }

        relevance = judgments.get((c["query_id"], c["doc_id"]), {}).get("score", 0)

        if c["source"] in ("baseline", "both"):
            per_query[qid]["baseline"].append((
                c["doc_id"], c["baseline_score"], relevance, c["doc_title"],
            ))
        if c["source"] in ("multi_surface", "both"):
            per_query[qid]["multi_surface"].append((
                c["doc_id"], c["multi_surface_score"], relevance, c["doc_title"],
            ))

    # Sort each by score descending
    for qid, data in per_query.items():
        data["baseline"].sort(key=lambda x: x[1], reverse=True)
        data["multi_surface"].sort(key=lambda x: x[1], reverse=True)

    return per_query


def _evaluate_strategy(
    per_query: dict[str, dict],
    strategy: str,
    k_values: list[int] = [5, 10],
) -> StrategyResults:
    """Evaluate a single strategy on its own returned results."""
    results = StrategyResults(name=strategy)
    total_queries = len(per_query)
    if total_queries == 0:
        return results

    results.total_queries = total_queries
    p5_sum = 0.0
    p10_sum = 0.0
    hr10_sum = 0.0
    rel10_sum = 0.0
    count_sum = 0.0
    mrr_sum = 0.0
    queries_with_rel = 0

    for qid, data in per_query.items():
        docs = data[strategy]
        count_sum += len(docs)

        # Precision@5
        top5 = docs[:5]
        relevant_in_5 = sum(1 for _, _, rel, _ in top5 if rel > 0)
        p5_sum += relevant_in_5 / min(5, max(len(top5), 1))

        # Precision@10 and related
        top10 = docs[:10]
        relevant_in_10 = sum(1 for _, _, rel, _ in top10 if rel > 0)
        highly_relevant_in_10 = sum(1 for _, _, rel, _ in top10 if rel == 2)
        p10_sum += relevant_in_10 / min(10, max(len(top10), 1))
        hr10_sum += highly_relevant_in_10
        rel10_sum += relevant_in_10

        if relevant_in_10 > 0:
            queries_with_rel += 1

        # MRR — reciprocal rank of first relevant doc
        first_rel_rank = 0
        for rank, (_, _, rel, _) in enumerate(docs, 1):
            if rel > 0:
                first_rel_rank = rank
                break
        if first_rel_rank > 0:
            mrr_sum += 1.0 / first_rel_rank

    results.precision_at_5 = p5_sum / total_queries
    results.precision_at_10 = p10_sum / total_queries
    results.highly_relevant_at_10 = hr10_sum / total_queries
    results.avg_relevant_at_10 = rel10_sum / total_queries
    results.avg_result_count = count_sum / total_queries
    results.mrr = mrr_sum / total_queries
    results.queries_with_relevant = queries_with_rel

    return results


def _head_to_head(
    per_query: dict[str, dict],
    k: int = 10,
) -> HeadToHead:
    """Compare strategies head-to-head per query."""
    h2h = HeadToHead(total_queries=len(per_query))

    for qid, data in per_query.items():
        ms_top = data["multi_surface"][:k]
        bl_top = data["baseline"][:k]

        ms_relevant = sum(1 for _, _, rel, _ in ms_top if rel > 0)
        bl_relevant = sum(1 for _, _, rel, _ in bl_top if rel > 0)

        example = {
            "query_id": qid,
            "query_text": data["query_text"],
            "ms_relevant": ms_relevant,
            "bl_relevant": bl_relevant,
            "ms_count": len(ms_top),
            "bl_count": len(bl_top),
        }

        if ms_relevant > bl_relevant:
            h2h.multi_surface_wins += 1
            if len(h2h.win_examples) < 10:
                h2h.win_examples.append(example)
        elif bl_relevant > ms_relevant:
            h2h.baseline_wins += 1
            if len(h2h.loss_examples) < 10:
                h2h.loss_examples.append(example)
        else:
            h2h.ties += 1

    return h2h


def _exclusive_discovery(
    candidates: list[dict],
    judgments: dict[tuple[str, str], dict],
) -> ExclusiveDiscovery:
    """Find relevant documents each strategy exclusively discovered."""
    discovery = ExclusiveDiscovery()

    for c in candidates:
        rel = judgments.get((c["query_id"], c["doc_id"]), {}).get("score", 0)
        if rel == 0:
            continue

        if c["source"] == "multi_surface":
            discovery.ms_only_relevant += 1
            if rel == 2:
                discovery.ms_only_highly_relevant += 1
            if len(discovery.examples) < 15:
                justification = judgments.get(
                    (c["query_id"], c["doc_id"]), {}
                ).get("justification", "")
                discovery.examples.append({
                    "query_text": c["query_text"],
                    "doc_title": c["doc_title"],
                    "doc_id": c["doc_id"],
                    "relevance": rel,
                    "multi_surface_score": c["multi_surface_score"],
                    "justification": justification,
                    "surface_scores": c.get("surface_scores", {}),
                })
        elif c["source"] == "baseline":
            discovery.bl_only_relevant += 1
            if rel == 2:
                discovery.bl_only_highly_relevant += 1
        else:  # both
            discovery.both_relevant += 1

    return discovery


def _threshold_survival(
    candidates: list[dict],
    judgments: dict[tuple[str, str], dict],
    thresholds: list[float] = [0.2, 0.3, 0.4, 0.5],
) -> list[ThresholdSurvival]:
    """Compute threshold survival rates for each strategy."""
    results = []

    for threshold in thresholds:
        ts = ThresholdSurvival(threshold=threshold)

        for c in candidates:
            rel = judgments.get((c["query_id"], c["doc_id"]), {}).get("score", 0)

            if c["baseline_score"] > 0:
                ts.baseline_total += 1
                if c["baseline_score"] >= threshold:
                    ts.baseline_survivors += 1
                    if rel > 0:
                        ts.baseline_survivors_relevant += 1

            if c["multi_surface_score"] > 0:
                ts.ms_total += 1
                if c["multi_surface_score"] >= threshold:
                    ts.ms_survivors += 1
                    if rel > 0:
                        ts.ms_survivors_relevant += 1

        results.append(ts)

    return results


def run_analysis(dataset_dir: Path) -> AnalysisResults:
    """Run the full retrieval quality analysis."""
    candidates, judgments = load_data(dataset_dir)
    per_query = _build_per_query_results(candidates, judgments)

    results = AnalysisResults()
    results.baseline = _evaluate_strategy(per_query, "baseline")
    results.multi_surface = _evaluate_strategy(per_query, "multi_surface")
    results.head_to_head = _head_to_head(per_query)
    results.exclusive_discovery = _exclusive_discovery(candidates, judgments)
    results.threshold_survival = _threshold_survival(candidates, judgments)

    # Query type segmentation
    question_queries = {qid: data for qid, data in per_query.items() if _is_question(data["query_text"])}
    phrase_queries = {qid: data for qid, data in per_query.items() if not _is_question(data["query_text"])}

    if question_queries:
        results.question_results = {
            "count": len(question_queries),
            "baseline": _evaluate_strategy(question_queries, "baseline"),
            "multi_surface": _evaluate_strategy(question_queries, "multi_surface"),
            "head_to_head": _head_to_head(question_queries),
        }
    if phrase_queries:
        results.phrase_results = {
            "count": len(phrase_queries),
            "baseline": _evaluate_strategy(phrase_queries, "baseline"),
            "multi_surface": _evaluate_strategy(phrase_queries, "multi_surface"),
            "head_to_head": _head_to_head(phrase_queries),
        }

    return results


def format_report(results: AnalysisResults) -> str:
    """Format analysis results as a readable report."""
    lines = []
    w = lines.append

    w("# Retrieval Quality Analysis")
    w("")
    w("Each search strategy is evaluated on the quality of what it actually returned —")
    w("not penalized for returning different documents than the other strategy.")
    w("")

    # Main comparison table
    bl = results.baseline
    ms = results.multi_surface

    w("## Result Quality: Top-10 Documents Returned")
    w("")
    w("| Metric | Baseline (Chunk Similarity) | Multi-Surface | Delta |")
    w("|--------|---------------------------|---------------|-------|")

    def _row(label, bl_val, ms_val, fmt=".1f", is_pct=False):
        suffix = "%" if is_pct else ""
        delta = ms_val - bl_val
        delta_pct = ((ms_val - bl_val) / bl_val * 100) if bl_val > 0 else 0
        w(f"| {label} | {bl_val:{fmt}}{suffix} | {ms_val:{fmt}}{suffix} | {delta_pct:+.1f}% |")

    _row("Avg relevant docs in top-10", bl.avg_relevant_at_10, ms.avg_relevant_at_10)
    _row("Avg highly relevant (score=2) in top-10", bl.highly_relevant_at_10, ms.highly_relevant_at_10)
    _row("Precision@10", bl.precision_at_10 * 100, ms.precision_at_10 * 100, ".1f", True)
    _row("Precision@5", bl.precision_at_5 * 100, ms.precision_at_5 * 100, ".1f", True)
    _row("MRR (first relevant result)", bl.mrr, ms.mrr, ".3f")

    w(f"| Queries with ≥1 relevant in top-10 | {bl.queries_with_relevant}/{bl.total_queries} | {ms.queries_with_relevant}/{ms.total_queries} | |")
    w(f"| Avg results returned per query | {bl.avg_result_count:.1f} | {ms.avg_result_count:.1f} | |")
    w("")

    # Head-to-head
    h2h = results.head_to_head
    w("## Head-to-Head: Per-Query Comparison")
    w("")
    w(f"For each query, which strategy returned more relevant documents in its top-10?")
    w("")
    w(f"- **Multi-surface wins: {h2h.multi_surface_wins}** ({h2h.multi_surface_wins / h2h.total_queries * 100:.1f}%)")
    w(f"- **Baseline wins: {h2h.baseline_wins}** ({h2h.baseline_wins / h2h.total_queries * 100:.1f}%)")
    w(f"- **Ties: {h2h.ties}** ({h2h.ties / h2h.total_queries * 100:.1f}%)")
    w(f"- Total queries: {h2h.total_queries}")
    w("")

    # Exclusive discovery
    disc = results.exclusive_discovery
    w("## Exclusive Discovery")
    w("")
    w("Relevant documents found by one strategy that the other didn't return at all.")
    w("")
    w(f"- Multi-surface found **{disc.ms_only_relevant}** relevant docs baseline missed ({disc.ms_only_highly_relevant} highly relevant)")
    w(f"- Baseline found **{disc.bl_only_relevant}** relevant docs multi-surface missed ({disc.bl_only_highly_relevant} highly relevant)")
    w(f"- Found by both: {disc.both_relevant}")
    w("")

    if disc.examples:
        w("### Cross-Topic Discovery Examples (multi-surface only)")
        w("")
        for ex in disc.examples[:10]:
            scores = ex.get("surface_scores", {})
            top_surface = max(scores, key=scores.get) if scores else "unknown"
            top_score = scores.get(top_surface, 0)
            w(f"- **Query**: {ex['query_text']}")
            w(f"  **Document**: {ex['doc_title']}")
            w(f"  **Relevance**: {'Highly relevant' if ex['relevance'] == 2 else 'Partially relevant'}")
            w(f"  **Found by**: {top_surface} ({top_score:.1%})")
            if ex.get("justification"):
                w(f"  **Why**: {ex['justification']}")
            w("")

    # Threshold survival
    w("## Threshold Survival")
    w("")
    w("At progressively higher score thresholds, how many results survive and what fraction are relevant?")
    w("")
    w("| Threshold | Baseline Survivors | BL Precision | MS Survivors | MS Precision |")
    w("|-----------|-------------------|-------------|-------------|-------------|")
    for ts in results.threshold_survival:
        bl_prec = ts.baseline_survivors_relevant / ts.baseline_survivors * 100 if ts.baseline_survivors > 0 else 0
        ms_prec = ts.ms_survivors_relevant / ts.ms_survivors * 100 if ts.ms_survivors > 0 else 0
        w(f"| {ts.threshold:.1f} | {ts.baseline_survivors} / {ts.baseline_total} | {bl_prec:.1f}% | {ts.ms_survivors} / {ts.ms_total} | {ms_prec:.1f}% |")
    w("")

    # Query type segmentation
    if results.question_results:
        qr = results.question_results
        w(f"## Natural Questions ({qr['count']} queries)")
        w("")
        q_bl = qr["baseline"]
        q_ms = qr["multi_surface"]
        q_h2h = qr["head_to_head"]
        w(f"| Metric | Baseline | Multi-Surface |")
        w(f"|--------|----------|---------------|")
        w(f"| Avg relevant in top-10 | {q_bl.avg_relevant_at_10:.1f} | {q_ms.avg_relevant_at_10:.1f} |")
        w(f"| Precision@10 | {q_bl.precision_at_10 * 100:.1f}% | {q_ms.precision_at_10 * 100:.1f}% |")
        w(f"| MRR | {q_bl.mrr:.3f} | {q_ms.mrr:.3f} |")
        w(f"| Wins | {q_h2h.baseline_wins} | {q_h2h.multi_surface_wins} |")
        w("")

    if results.phrase_results:
        pr = results.phrase_results
        w(f"## Topic Phrases ({pr['count']} queries)")
        w("")
        p_bl = pr["baseline"]
        p_ms = pr["multi_surface"]
        p_h2h = pr["head_to_head"]
        w(f"| Metric | Baseline | Multi-Surface |")
        w(f"|--------|----------|---------------|")
        w(f"| Avg relevant in top-10 | {p_bl.avg_relevant_at_10:.1f} | {p_ms.avg_relevant_at_10:.1f} |")
        w(f"| Precision@10 | {p_bl.precision_at_10 * 100:.1f}% | {p_ms.precision_at_10 * 100:.1f}% |")
        w(f"| MRR | {p_bl.mrr:.3f} | {p_ms.mrr:.3f} |")
        w(f"| Wins | {p_h2h.baseline_wins} | {p_h2h.multi_surface_wins} |")
        w("")

    return "\n".join(lines)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Retrieval Quality Analysis")
    parser.add_argument("--dataset", required=True, help="Dataset name or path")
    parser.add_argument("--output", type=Path, default=None, help="Output file (default: stdout)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    datasets_dir = Path(__file__).parent / ".datasets"
    dataset_dir = Path(args.dataset) if Path(args.dataset).is_dir() else datasets_dir / args.dataset

    results = run_analysis(dataset_dir)
    report = format_report(results)

    if args.output:
        args.output.write_text(report, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
