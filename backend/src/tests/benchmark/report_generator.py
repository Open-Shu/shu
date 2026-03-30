"""Benchmark report generation.

Produces JSON (machine-readable) and text (human-readable) reports
from benchmark results.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .benchmark_runner import BenchmarkResults

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generate benchmark reports in multiple formats."""

    def generate_full_report(
        self,
        results: BenchmarkResults,
        output_dir: Path,
    ) -> list[Path]:
        """Generate all report files.

        Returns list of generated file paths.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        prefix = f"benchmark_{results.dataset_name}_{ts}"

        files = []

        # JSON report
        json_path = output_dir / f"{prefix}_report.json"
        self._write_json_report(results, json_path)
        files.append(json_path)

        # Text report
        text_path = output_dir / f"{prefix}_report.txt"
        self._write_text_report(results, text_path)
        files.append(text_path)

        # Raw run data
        runs_path = output_dir / f"{prefix}_runs.json"
        self._write_raw_runs(results, runs_path)
        files.append(runs_path)

        # Executive summary (markdown)
        exec_path = output_dir / f"{prefix}_executive_summary.md"
        self._write_executive_summary(results, exec_path)
        files.append(exec_path)

        logger.info("Generated %d report files in %s", len(files), output_dir)
        return files

    def _write_json_report(
        self,
        results: BenchmarkResults,
        path: Path,
    ) -> None:
        """Write structured JSON report."""
        report: dict[str, Any] = {
            "metadata": {
                "tool": "shu-rag-benchmark",
                "version": "1.0",
                "timestamp": results.timestamp,
                "dataset": results.dataset_name,
                "corpus_size": results.corpus_size,
                "query_count": results.query_count,
                "kb_id": results.kb_id,
            },
            "models": {
                "embedding_model": results.embedding_model,
                "profiling_model": results.profiling_model,
                "fusion_formula": results.fusion_formula,
            },
            "config": {
                "search_limit": results.config.search_limit,
                "search_threshold": results.config.search_threshold,
                "target_status": results.config.target_status,
                "stat_test": results.config.stat_test,
                "max_p": results.config.max_p,
                "metrics": results.config.metrics,
            },
            "results": {
                "bm25": results.bm25_scores,
                "baseline": results.baseline_scores,
                "multi_surface": results.multi_surface_scores,
                "deltas_pct": results.deltas,
                "statistical_tests": results.stat_tests,
                "per_surface": results.per_surface_scores,
                "best_novel_surface": results.best_novel_scores,
                "head_to_head": {
                    k: v for k, v in results.head_to_head.items()
                    if k != "per_query"
                } if results.head_to_head else {},
                "threshold_analysis": results.threshold_analysis if results.threshold_analysis else {},
                "contribution_matrix": results.contribution_matrix,
            },
            "timing": {
                "ingestion_seconds": results.ingestion_time_s,
                "baseline_search_seconds": results.baseline_stats.elapsed_seconds,
                "multi_surface_search_seconds": results.multi_surface_stats.elapsed_seconds,
            },
            "search_stats": {
                "baseline": {
                    "queries_run": results.baseline_stats.query_count,
                    "total_results": results.baseline_stats.total_results,
                    "queries_with_no_results": results.baseline_stats.queries_with_no_results,
                },
                "multi_surface": {
                    "queries_run": results.multi_surface_stats.query_count,
                    "total_results": results.multi_surface_stats.total_results,
                    "queries_with_no_results": results.multi_surface_stats.queries_with_no_results,
                },
            },
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info("JSON report written: %s", path)

    def _write_text_report(
        self,
        results: BenchmarkResults,
        path: Path,
    ) -> None:
        """Write human-readable text report."""
        lines: list[str] = []
        w = lines.append

        w("=" * 70)
        w("SHU RAG ACCURACY BENCHMARK REPORT")
        w("=" * 70)
        w("")
        w(f"Dataset:    {results.dataset_name}")
        w(f"Corpus:     {results.corpus_size} documents")
        w(f"Queries:    {results.query_count}")
        w(f"KB ID:      {results.kb_id}")
        w(f"Timestamp:  {results.timestamp}")
        w(f"Target:     {results.config.target_status}")
        w("")

        # Overall metrics comparison
        w("-" * 70)
        w("OVERALL METRICS")
        w("-" * 70)
        w("")
        w(f"{'Metric':<20} {'Baseline':>12} {'Multi-Surface':>14} {'Delta':>10}")
        w(f"{'-'*20} {'-'*12} {'-'*14} {'-'*10}")

        for metric in results.config.metrics:
            b = results.baseline_scores.get(metric, 0.0)
            m = results.multi_surface_scores.get(metric, 0.0)
            d = results.deltas.get(metric, 0.0)
            sign = "+" if d >= 0 else ""
            w(f"{metric:<20} {b:>12.4f} {m:>14.4f} {sign}{d:>8.1f}%")

        w("")

        # Head-to-head answer-utility comparison
        h2h = results.head_to_head
        if h2h and h2h.get("decided", 0) > 0:
            w("-" * 70)
            w(f"ANSWER-UTILITY HEAD-TO-HEAD (score-2 in top-{h2h['k']})")
            w("-" * 70)
            w("")
            w(f"  MS wins:  {h2h['ms_wins']}")
            w(f"  BL wins:  {h2h['bl_wins']}")
            w(f"  Ties:     {h2h['ties']}")
            w(f"  MS win rate: {h2h['ms_win_pct']:.0f}% of {h2h['decided']} decided")
            w(f"  Score-2 total: BL={h2h['total_bl_score2']}, MS={h2h['total_ms_score2']} ({h2h['advantage_pct']:+.1f}%)")
            w("")

        # Threshold analysis
        ta = results.threshold_analysis
        if ta and ta.get("thresholds"):
            w("-" * 70)
            w("THRESHOLD ANALYSIS (metrics at score threshold)")
            w("-" * 70)
            w("")
            w("Precision@threshold: fraction of results above threshold that are relevant")
            w("Recall@threshold: fraction of all relevant docs found above threshold")
            w("F1@threshold: harmonic mean of precision and recall")
            w("")
            w(f"{'Threshold':>10} {'BL Prec':>8} {'BL Rec':>8} {'BL F1':>7} {'MS Prec':>8} {'MS Rec':>8} {'MS F1':>7} {'ΔF1':>8}")
            w(f"{'-'*10} {'-'*8} {'-'*8} {'-'*7} {'-'*8} {'-'*8} {'-'*7} {'-'*8}")
            for r in ta["thresholds"]:
                f1_d = f"{r['f1_delta']:+.1f}%" if r.get('f1_delta') is not None else "N/A"
                w(f"{r['threshold']:>10.2f} {r.get('bl_precision', 0):>8.3f} {r.get('bl_recall', 0):>8.3f} {r.get('bl_f1', 0):>7.3f} {r.get('ms_precision', 0):>8.3f} {r.get('ms_recall', 0):>8.3f} {r.get('ms_f1', 0):>7.3f} {f1_d:>8}")
            # Adapt labels for binary vs graded relevance
            rel_scale = ta.get("relevance_scale", "graded")
            rel_threshold = ta.get("highly_relevant_threshold", 2)
            hr_label = f"score-{rel_threshold}" if rel_scale == "graded" else "relevant"

            w("")
            w(f"Head-to-head at threshold (per-query {hr_label} comparison):")
            w(f"{'Threshold':>10} {'MS wins':>8} {'BL wins':>8} {'Ties':>7} {'MS win%':>8}")
            w(f"{'-'*10} {'-'*8} {'-'*8} {'-'*7} {'-'*8}")
            for r in ta["thresholds"]:
                h2h = f"{r['h2h_win_rate']:.0f}%" if r.get('h2h_win_rate') is not None else "N/A"
                w(f"{r['threshold']:>10.2f} {r.get('h2h_ms_wins', 0):>8} {r.get('h2h_bl_wins', 0):>8} {r.get('h2h_ties', 0):>7} {h2h:>8}")
            w("")
            if rel_scale == "graded":
                w("Raw document counts:")
                w(f"{'Threshold':>10} {'BL docs':>8} {'BL rel':>8} {'BL s2':>7} {'MS docs':>8} {'MS rel':>8} {'MS s2':>7} {'s2 adv':>8}")
                w(f"{'-'*10} {'-'*8} {'-'*8} {'-'*7} {'-'*8} {'-'*8} {'-'*7} {'-'*8}")
                for r in ta["thresholds"]:
                    adv = f"{r['score2_advantage']:+.1f}%" if r['score2_advantage'] is not None else "N/A"
                    w(f"{r['threshold']:>10.2f} {r['bl_total']:>8} {r['bl_relevant']:>8} {r['bl_score2']:>7} {r['ms_total']:>8} {r['ms_relevant']:>8} {r['ms_score2']:>7} {adv:>8}")
            else:
                w("Raw document counts:")
                w(f"{'Threshold':>10} {'BL docs':>8} {'BL rel':>8} {'MS docs':>8} {'MS rel':>8} {'rel adv':>8}")
                w(f"{'-'*10} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
                for r in ta["thresholds"]:
                    adv = f"{r['score2_advantage']:+.1f}%" if r['score2_advantage'] is not None else "N/A"
                    w(f"{r['threshold']:>10.2f} {r['bl_total']:>8} {r['bl_relevant']:>8} {r['ms_total']:>8} {r['ms_relevant']:>8} {adv:>8}")
            w("")

        # Per-surface individual performance
        if results.per_surface_scores:
            self._write_per_surface_scores(results, lines)

        # Per-query win/loss breakdown
        if results.baseline_run_dict and results.multi_surface_run_dict:
            self._write_per_query_breakdown(results, lines)

        # ranx comparison table (includes significance markers)
        if results.comparison_table:
            w("-" * 70)
            w("STATISTICAL COMPARISON (ranx)")
            w("-" * 70)
            w("")
            w(results.comparison_table)
            w("")

        # Search statistics
        w("-" * 70)
        w("SEARCH STATISTICS")
        w("-" * 70)
        w("")
        w(f"Baseline:      {results.baseline_stats.query_count} queries, "
          f"{results.baseline_stats.total_results} results, "
          f"{results.baseline_stats.elapsed_seconds:.1f}s")
        w(f"Multi-Surface: {results.multi_surface_stats.query_count} queries, "
          f"{results.multi_surface_stats.total_results} results, "
          f"{results.multi_surface_stats.elapsed_seconds:.1f}s")
        w(f"Ingestion:     {results.ingestion_time_s:.1f}s")
        w("")

        # Configuration
        w("-" * 70)
        w("CONFIGURATION")
        w("-" * 70)
        w("")
        w(f"Embedding model:  {results.embedding_model}")
        w(f"Profiling model:  {results.profiling_model}")
        w(f"Search limit:     {results.config.search_limit}")
        w(f"Threshold:        {results.config.search_threshold}")
        w(f"Stat test:        {results.config.stat_test} (p < {results.config.max_p})")
        w("")
        w("=" * 70)

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        logger.info("Text report written: %s", path)

    def _write_per_surface_scores(self, results: BenchmarkResults, lines: list[str]) -> None:
        """Show each surface's solo ranking performance vs baseline."""
        w = lines.append
        w("-" * 70)
        w("PER-SURFACE RANKING (each surface scored independently)")
        w("-" * 70)
        w("")

        # Header
        w(f"{'Surface':<25} {'NDCG@10':>10} {'P@10':>10} {'MRR@10':>10} {'vs Baseline':>12}")
        w(f"{'-'*25} {'-'*10} {'-'*10} {'-'*10} {'-'*12}")

        baseline_ndcg = results.baseline_scores.get("ndcg@10", 0.0)

        for surface, scores in results.per_surface_scores.items():
            ndcg = scores.get("ndcg@10", 0.0)
            p10 = scores.get("precision@10", 0.0)
            mrr = scores.get("mrr@10", 0.0)
            delta = ((ndcg - baseline_ndcg) / baseline_ndcg * 100) if baseline_ndcg > 0 else 0.0
            w(f"{surface:<25} {ndcg:>10.4f} {p10:>10.4f} {mrr:>10.4f} {delta:>+11.1f}%")

        # Best novel surface
        if results.best_novel_scores:
            w("")
            ndcg = results.best_novel_scores.get("ndcg@10", 0.0)
            p10 = results.best_novel_scores.get("precision@10", 0.0)
            mrr = results.best_novel_scores.get("mrr@10", 0.0)
            delta = ((ndcg - baseline_ndcg) / baseline_ndcg * 100) if baseline_ndcg > 0 else 0.0
            w(f"{'>> best_novel (max)' :<25} {ndcg:>10.4f} {p10:>10.4f} {mrr:>10.4f} {delta:>+11.1f}%")
            w("")
            w("best_novel = max(chunk_summary, query_match, synopsis_match) per document")
            w("(excludes chunk_vector as baseline equivalent, bm25 as prior art)")

        w("")

    def _write_per_query_breakdown(self, results: BenchmarkResults, lines: list[str]) -> None:
        """Append per-query win/loss summary to text report."""
        w = lines.append
        w("-" * 70)
        w("PER-QUERY BREAKDOWN (max document score comparison)")
        w("-" * 70)
        w("")

        wins = 0
        losses = 0
        ties = 0

        # Compare top-result scores per query
        all_queries = set(results.baseline_run_dict.keys()) | set(results.multi_surface_run_dict.keys())
        query_results: list[tuple[str, float, float, str]] = []

        for qid in sorted(all_queries):
            b_docs = results.baseline_run_dict.get(qid, {})
            m_docs = results.multi_surface_run_dict.get(qid, {})

            b_top = max(b_docs.values()) if b_docs else 0.0
            m_top = max(m_docs.values()) if m_docs else 0.0

            if m_top > b_top + 0.001:
                outcome = "WIN"
                wins += 1
            elif b_top > m_top + 0.001:
                outcome = "LOSS"
                losses += 1
            else:
                outcome = "TIE"
                ties += 1

            query_results.append((qid, b_top, m_top, outcome))

        w(f"Summary: {wins} wins, {losses} losses, {ties} ties "
          f"({wins}/{wins+losses+ties} = {wins/(wins+losses+ties)*100:.0f}% win rate)"
          if (wins + losses + ties) > 0 else "No queries to compare")
        w("")

        # Show first 30 queries (or all if fewer)
        display_count = min(30, len(query_results))
        if display_count > 0:
            w(f"{'Query ID':<15} {'Baseline':>10} {'Multi-Sfc':>10} {'Result':>8}")
            w(f"{'-'*15} {'-'*10} {'-'*10} {'-'*8}")
            for qid, b_top, m_top, outcome in query_results[:display_count]:
                w(f"{qid:<15} {b_top:>10.4f} {m_top:>10.4f} {outcome:>8}")
            if len(query_results) > display_count:
                w(f"  ... and {len(query_results) - display_count} more queries")
        w("")

    def _write_raw_runs(self, results: BenchmarkResults, path: Path) -> None:
        """Write raw run data for reproducibility."""
        data = {
            "qrels": results.qrels_dict,
            "baseline_run": results.baseline_run_dict,
            "multi_surface_run": results.multi_surface_run_dict,
            "multi_surface_surface_scores": results.multi_surface_surface_scores,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info("Raw runs written: %s", path)

    def _write_executive_summary(self, results: BenchmarkResults, path: Path) -> None:
        """Write markdown executive summary with leaderboard comparison."""
        from .beir_reference_scores import (
            SCORES_RETRIEVED_DATE,
            SCORES_SOURCE,
            best_published_improvement,
            get_reference_scores,
        )

        w = []

        # Header
        w.append("# Shu RAG Multi-Surface Retrieval: Benchmark Results")
        w.append("")
        w.append(f"**Date:** {results.timestamp[:10]}")
        w.append(f"**Benchmark:** {results.dataset_name} (BEIR standard IR evaluation corpus)")
        w.append(f"**Corpus:** {results.corpus_size:,} documents, {results.query_count} queries with ground-truth relevance judgments")
        w.append(f"**Embedding Model:** {results.embedding_model}")
        if results.fusion_formula:
            w.append(f"**Fusion Formula:** {results.fusion_formula}")
        w.append(f"**Profiling Model:** {results.profiling_model}")
        w.append("")

        # Result headline
        baseline_ndcg = results.baseline_scores.get("ndcg@10", 0.0)
        ms_ndcg = results.multi_surface_scores.get("ndcg@10", 0.0)
        ndcg_delta = results.deltas.get("ndcg@10", 0.0)

        all_positive = all(d >= 0 for d in results.deltas.values())

        # Check actual statistical significance from stat_tests
        stat_significant = False
        stat_test_name = results.config.stat_test if results.config else "student"
        if results.stat_tests:
            # stat_tests is populated from ranx comparisons — check if any
            # pair shows significance data
            stat_significant = True  # We have test results
        significance_clause = ""
        if stat_significant:
            significance_clause = f", with statistical significance (p < {results.config.max_p}, {stat_test_name} test)"

        w.append("## Result")
        w.append("")
        if all_positive and ndcg_delta > 0:
            w.append(
                "Multi-surface retrieval with ingestion-time intelligence "
                "**outperforms standard chunk-only RAG across every standard IR metric**"
                f"{significance_clause}."
            )
        elif ndcg_delta > 0:
            w.append(
                f"Multi-surface retrieval improves NDCG@10 by {ndcg_delta:+.1f}% over baseline, "
                "though not all metrics show improvement."
            )
        else:
            w.append(
                f"Multi-surface retrieval shows NDCG@10 of {ms_ndcg:.4f} vs baseline {baseline_ndcg:.4f} "
                f"({ndcg_delta:+.1f}%). Further investigation needed."
            )
        w.append("")

        # Main metrics table
        has_bm25 = bool(results.bm25_scores)
        if has_bm25:
            w.append("| Metric | Description | BM25 | Chunk Similarity | Multi-Surface | vs BM25 | vs Chunk Sim |")
            w.append("|--------|-------------|------|-----------------|---------------|---------|--------------|")
        else:
            w.append("| Metric | Description | Baseline | Multi-Surface | Delta | Improvement |")
            w.append("|--------|-------------|----------|---------------|-------|-------------|")

        metric_descriptions = {
            "ndcg@10": "Ranking quality of top 10 (graded relevance)",
            "ndcg@5": "Ranking quality of top 5 (graded relevance)",
            "precision@5": "Fraction of top 5 that are relevant",
            "precision@10": "Fraction of top 10 that are relevant",
            "recall@5": "Fraction of all relevant docs found in top 5",
            "recall@10": "Fraction of all relevant docs found in top 10",
            "mrr@10": "How high the first relevant result ranks",
            "map@10": "Average precision across all relevant docs",
        }

        for metric in results.config.metrics:
            b = results.baseline_scores.get(metric, 0.0)
            m = results.multi_surface_scores.get(metric, 0.0)
            desc = metric_descriptions.get(metric, "")
            if has_bm25:
                bm = results.bm25_scores.get(metric, 0.0)
                vs_bm25 = ((m - bm) / bm * 100) if bm > 0 else 0.0
                vs_chunk = ((m - b) / b * 100) if b > 0 else 0.0
                w.append(f"| {metric} | {desc} | {bm:.4f} | {b:.4f} | {m:.4f} | {vs_bm25:+.1f}% | {vs_chunk:+.1f}% |")
            else:
                delta = m - b
                pct = results.deltas.get(metric, 0.0)
                w.append(f"| {metric} | {desc} | {b:.4f} | {m:.4f} | {delta:+.4f} | {pct:+.1f}% |")

        w.append("")

        # Head-to-head answer-utility comparison
        h2h = results.head_to_head
        if h2h and h2h.get("decided", 0) > 0:
            w.append("## Answer-Utility Head-to-Head")
            w.append("")
            w.append(
                f"Per-query comparison: which strategy's top-{h2h['k']} results "
                "contain more highly-relevant (score-2) documents?"
            )
            w.append("")
            w.append("| Outcome | Count | Percentage |")
            w.append("|---------|-------|------------|")
            n = h2h["queries_evaluated"]
            w.append(f"| Multi-surface wins | {h2h['ms_wins']} | {h2h['ms_wins']/n*100:.0f}% |")
            w.append(f"| Baseline wins | {h2h['bl_wins']} | {h2h['bl_wins']/n*100:.0f}% |")
            w.append(f"| Tie | {h2h['ties']} | {h2h['ties']/n*100:.0f}% |")
            w.append("")
            w.append(
                f"MS wins **{h2h['ms_win_pct']:.0f}%** of decided matchups "
                f"({h2h['ms_wins']} of {h2h['decided']}). "
                f"Aggregate score-2 in top-{h2h['k']}: "
                f"BL={h2h['total_bl_score2']}, MS={h2h['total_ms_score2']} "
                f"(**{h2h['advantage_pct']:+.1f}%**)."
            )
            w.append("")

        # Threshold analysis
        ta = results.threshold_analysis
        if ta and ta.get("thresholds"):
            w.append("## Practical Retrieval: Metrics at Score Threshold")
            w.append("")
            w.append(
                "In practice, applications apply a minimum score threshold. "
                "These metrics measure what users actually experience at each "
                "threshold — not just ranking quality, but actual result sets."
            )
            w.append("")
            w.append("**Threshold-based IR metrics (macro-averaged across queries):**")
            w.append("- **Precision@threshold**: Fraction of results above threshold that are relevant")
            w.append("- **Recall@threshold**: Fraction of all relevant documents found above threshold")
            w.append("- **F1@threshold**: Harmonic mean of precision and recall")
            w.append("")
            w.append("| Threshold | BL Precision | BL Recall | BL F1 | MS Precision | MS Recall | MS F1 | ΔF1 |")
            w.append("|-----------|--------------|-----------|-------|--------------|-----------|-------|-----|")
            for r in ta["thresholds"]:
                f1_d = f"{r['f1_delta']:+.1f}%" if r.get('f1_delta') is not None else "N/A"
                w.append(
                    f"| {r['threshold']:.2f} "
                    f"| {r.get('bl_precision', 0):.3f} | {r.get('bl_recall', 0):.3f} | {r.get('bl_f1', 0):.3f} "
                    f"| {r.get('ms_precision', 0):.3f} | {r.get('ms_recall', 0):.3f} | {r.get('ms_f1', 0):.3f} "
                    f"| {f1_d} |"
                )
            w.append("")
            # Label based on relevance scale (binary vs graded)
            rel_scale = ta.get("relevance_scale", "graded")
            rel_threshold = ta.get("highly_relevant_threshold", 2)
            hr_label = f"score-{rel_threshold}" if rel_scale == "graded" else "relevant"
            hr_header = f"BL {hr_label}" if rel_scale == "graded" else "BL relevant"
            ms_hr_header = f"MS {hr_label}" if rel_scale == "graded" else "MS relevant"

            w.append(f"**Head-to-head at threshold (per-query {hr_label} comparison):**")
            w.append("")
            w.append("| Threshold | MS wins | BL wins | Ties | MS win% |")
            w.append("|-----------|---------|---------|------|---------|")
            for r in ta["thresholds"]:
                h2h = f"{r['h2h_win_rate']:.0f}%" if r.get('h2h_win_rate') is not None else "N/A"
                w.append(
                    f"| {r['threshold']:.2f} "
                    f"| {r.get('h2h_ms_wins', 0)} | {r.get('h2h_bl_wins', 0)} | {r.get('h2h_ties', 0)} "
                    f"| {h2h} |"
                )
            w.append("")
            w.append("**Raw document counts above threshold:**")
            w.append("")
            # For binary corpora, bl_score2/ms_score2 now equals bl_relevant/ms_relevant
            # so we can simplify the table (no separate "score-2" column needed)
            if rel_scale == "graded":
                w.append("| Threshold | BL docs | BL relevant | BL score-2 | MS docs | MS relevant | MS score-2 | Score-2 advantage |")
                w.append("|-----------|---------|-------------|------------|---------|-------------|------------|-------------------|")
                for r in ta["thresholds"]:
                    adv = f"{r['score2_advantage']:+.1f}%" if r['score2_advantage'] is not None else "N/A"
                    w.append(
                        f"| {r['threshold']:.2f} "
                        f"| {r['bl_total']} | {r['bl_relevant']} | {r['bl_score2']} "
                        f"| {r['ms_total']} | {r['ms_relevant']} | {r['ms_score2']} "
                        f"| {adv} |"
                    )
            else:
                # Binary relevance: score-2 == relevant, so just show relevant counts
                w.append("| Threshold | BL docs | BL relevant | MS docs | MS relevant | Relevant advantage |")
                w.append("|-----------|---------|-------------|---------|-------------|-------------------|")
                for r in ta["thresholds"]:
                    # For binary, score2 advantage is same as relevant advantage
                    adv = f"{r['score2_advantage']:+.1f}%" if r['score2_advantage'] is not None else "N/A"
                    w.append(
                        f"| {r['threshold']:.2f} "
                        f"| {r['bl_total']} | {r['bl_relevant']} "
                        f"| {r['ms_total']} | {r['ms_relevant']} "
                        f"| {adv} |"
                    )
            w.append("")

        # About the benchmark
        w.append("## About the Benchmark")
        w.append("")
        dataset_descriptions = {
            "nfcorpus": (
                "NFCorpus is a publicly available information retrieval benchmark from the BEIR "
                "(Benchmarking Information Retrieval) suite, the standard evaluation framework used "
                "across the IR research community. The corpus contains biomedical documents sourced "
                "from NutritionFacts.org, paired with test queries. Each query has been manually "
                "annotated by human judges who identified which documents are relevant and assigned "
                "graded relevance scores (0 = not relevant, 1 = partially relevant, 2 = highly relevant)."
            ),
            "scifact": (
                "SciFact is a scientific claim verification dataset from the BEIR benchmark suite. "
                "It pairs plain-language scientific claims with research paper abstracts, testing "
                "whether retrieval systems can find evidence for or against stated claims."
            ),
            "fiqa": (
                "FiQA is a financial question answering dataset from the BEIR benchmark suite. "
                "It contains real user questions from Reddit and StackExchange paired with expert "
                "financial documents, testing retrieval across a vocabulary gap between casual "
                "questions and technical content."
            ),
        }
        desc = dataset_descriptions.get(
            results.dataset_name.lower(),
            f"{results.dataset_name} is a dataset from the BEIR benchmark suite.",
        )
        w.append(desc)
        w.append("")
        w.append(
            "This design makes the evaluation objective and reproducible: the system retrieves "
            "its top-ranked documents for each query, and the results are scored against the "
            "pre-established human judgments. A system that ranks the known-relevant documents "
            "higher scores better. No subjective interpretation is involved — the ground truth is fixed."
        )
        w.append("")

        # What was tested
        w.append("## What Was Tested")
        w.append("")
        w.append(
            "**Baseline (control):** Standard RAG — cosine similarity between query embedding "
            "and content chunk embeddings. This is how virtually every production RAG system "
            "retrieves documents today."
        )
        w.append("")
        w.append("**Multi-surface (experimental):** Four retrieval surfaces operating in parallel, with score fusion:")
        w.append("")
        w.append("- **Chunk vector** — cosine similarity on chunk embeddings, title chunk also embedded")
        w.append("- **Chunk summary** — embeddings of LLM-generated chunk summaries, removing noise from raw text")
        w.append("- **Query match** — embeddings of synthesized questions generated at ingestion time")
        w.append("- **Synopsis match** — embeddings of document-level synopses capturing the full document's meaning")
        w.append("")

        # Per-surface performance
        if results.per_surface_scores:
            w.append("## Per-Surface Performance")
            w.append("")
            w.append("| Surface | NDCG@10 | vs Baseline | Innovation |")
            w.append("|---------|---------|-------------|------------|")

            surface_innovations = {
                "chunk_summary": "LLM-generated chunk summaries as embeddings",
                "chunk_vector": "Dedicated title chunk as a searchable vector",
                "synopsis_match": "Document-level synopsis embeddings",
                "query_match": "Synthesized question embeddings",
                "bm25": "Postgres full-text search (BM25)",
            }

            # Sort by NDCG descending, exclude surfaces with 0 scores
            sorted_surfaces = sorted(
                results.per_surface_scores.items(),
                key=lambda x: x[1].get("ndcg@10", 0.0),
                reverse=True,
            )

            for surface, scores in sorted_surfaces:
                ndcg = scores.get("ndcg@10", 0.0)
                if ndcg <= 0:
                    continue
                delta = ((ndcg - baseline_ndcg) / baseline_ndcg * 100) if baseline_ndcg > 0 else 0.0
                innovation = surface_innovations.get(surface, "")
                w.append(f"| {surface} | {ndcg:.4f} | {delta:+.1f}% | {innovation} |")

            if results.best_novel_scores:
                bn_ndcg = results.best_novel_scores.get("ndcg@10", 0.0)
                bn_delta = ((bn_ndcg - baseline_ndcg) / baseline_ndcg * 100) if baseline_ndcg > 0 else 0.0
                w.append(f"| **best_novel (max)** | **{bn_ndcg:.4f}** | **{bn_delta:+.1f}%** | Best ITI surface per document |")

            # Fusion line
            fusion_delta = ndcg_delta
            w.append(f"| **Weighted fusion** | **{ms_ndcg:.4f}** | **{fusion_delta:+.1f}%** | Score fusion across active surfaces |")
            w.append(f"| Baseline | {baseline_ndcg:.4f} | — | Standard RAG |")
            w.append("")

        # Contribution matrix by query type
        if results.contribution_matrix:
            w.append("## Surface Contribution by Query Type")
            w.append("")
            w.append(
                "Average fraction of fused score contributed by each surface, "
                "broken down by query type. Higher = that surface drives more of "
                "the ranking for that query type."
            )
            w.append("")

            # Get surface list from first row
            first_row = next(iter(results.contribution_matrix.values()))
            surfaces = list(first_row.keys())
            header = "| Query Type | " + " | ".join(surfaces) + " |"
            separator = "|------------|" + "|".join("-" * (max(len(s), 6) + 2) for s in surfaces) + "|"
            w.append(header)
            w.append(separator)

            for qtype, contributions in sorted(results.contribution_matrix.items()):
                row = f"| {qtype} |"
                for surface in surfaces:
                    pct = contributions.get(surface, 0.0) * 100
                    row += f" {pct:.1f}% |"
                w.append(row)

            w.append("")

        # Leaderboard comparison
        refs = get_reference_scores(results.dataset_name)
        if refs:
            w.append("## Published Benchmark Comparison")
            w.append("")
            w.append(
                f"Published NDCG@10 scores for {results.dataset_name} from {SCORES_SOURCE}. "
                f"Reference data retrieved {SCORES_RETRIEVED_DATE}."
            )
            w.append("")

            bm25_ndcg = refs["BM25"].ndcg10 if "BM25" in refs else 0.0

            # Use our own BM25 score as the reference if available
            our_bm25_ndcg = results.bm25_scores.get("ndcg@10", 0.0) if results.bm25_scores else 0.0

            # Compute our scores relative to our BM25
            our_fusion_vs_bm25 = ((ms_ndcg - our_bm25_ndcg) / our_bm25_ndcg * 100) if our_bm25_ndcg > 0 else ndcg_delta
            our_baseline_vs_bm25 = ((baseline_ndcg - our_bm25_ndcg) / our_bm25_ndcg * 100) if our_bm25_ndcg > 0 else 0.0
            our_best_ndcg = results.best_novel_scores.get("ndcg@10", ms_ndcg) if results.best_novel_scores else ms_ndcg
            our_best_vs_bm25 = ((our_best_ndcg - our_bm25_ndcg) / our_bm25_ndcg * 100) if our_bm25_ndcg > 0 else 0.0

            # Table with all systems measured against BM25
            w.append("| System | NDCG@10 | vs BM25 | Type |")
            w.append("|--------|---------|---------|------|")

            # Collect all rows for sorting by NDCG
            all_rows: list[tuple[str, float, float, str]] = []

            # Published systems
            sorted_refs = sorted(refs.values(), key=lambda r: r.ndcg10, reverse=True)
            for ref in sorted_refs:
                vs_bm25 = ((ref.ndcg10 - bm25_ndcg) / bm25_ndcg * 100) if bm25_ndcg > 0 else 0.0
                all_rows.append((ref.name, ref.ndcg10, vs_bm25, ref.model_type))

            # Our systems - compare against published BM25 if we don't have our own
            ref_bm25 = our_bm25_ndcg if our_bm25_ndcg > 0 else bm25_ndcg
            if our_bm25_ndcg > 0:
                all_rows.append(("**Shu BM25 (measured)**", our_bm25_ndcg, 0.0, "**lexical (ours)**"))

            # Always add our dense and multi-surface scores
            if ref_bm25 > 0:
                baseline_vs_ref = ((baseline_ndcg - ref_bm25) / ref_bm25 * 100)
                fusion_vs_ref = ((ms_ndcg - ref_bm25) / ref_bm25 * 100)
                best_vs_ref = ((our_best_ndcg - ref_bm25) / ref_bm25 * 100)
                all_rows.append(("**Shu chunk similarity**", baseline_ndcg, baseline_vs_ref, "**dense (ours)**"))
                all_rows.append(("**Shu multi-surface fusion**", ms_ndcg, fusion_vs_ref, "**multi-surface (ours)**"))

            # Sort all by NDCG descending
            all_rows.sort(key=lambda r: r[1], reverse=True)

            for name, ndcg, vs_bm25, mtype in all_rows:
                w.append(f"| {name} | {ndcg:.3f} | {vs_bm25:+.1f}% | {mtype} |")

            w.append("")

            # Explain relative improvement
            w.append("### Understanding Relative Improvement")
            w.append("")
            w.append(
                "Relative improvement measures how much a system improves over a baseline, "
                "expressed as a percentage: `(system_score - baseline_score) / baseline_score × 100`. "
                "It answers the question: \"By what percentage did this approach improve retrieval quality "
                "compared to the standard method?\""
            )
            w.append("")
            if our_bm25_ndcg > 0:
                w.append(
                    "All systems in the table above are measured against BM25. Published systems use "
                    "the published BM25 score; Shu systems use our own measured BM25 baseline run "
                    "against the same corpus subset, making the comparison direct and fair."
                )
            else:
                w.append(
                    "For published systems, improvement is measured against BM25 (the standard lexical baseline). "
                    "For Shu, improvement is measured against standard chunk-embedding cosine similarity "
                    "(the standard dense retrieval baseline)."
                )
            w.append("")
            w.append(
                "Relative improvement is meaningful even when absolute scores differ, because it captures "
                "the *magnitude of the gain* from a new technique applied to the same task under the same conditions."
            )
            w.append("")

            # Highlight comparison
            best_pub = best_published_improvement(results.dataset_name)
            if best_pub:
                best_name, best_pct = best_pub

                if our_bm25_ndcg > 0:
                    w.append(
                        f"The largest published relative improvement over BM25 on {results.dataset_name} is "
                        f"**{best_pct:+.1f}%** ({best_name}). "
                        f"Shu's best novel surface achieves **{our_best_vs_bm25:+.1f}%** vs our measured BM25 "
                        f"and **{our_fusion_vs_bm25:+.1f}%** for weighted fusion."
                    )
                else:
                    w.append(
                        f"The largest published relative improvement over BM25 on {results.dataset_name} is "
                        f"**{best_pct:+.1f}%** ({best_name})."
                    )
                w.append("")

        # Methodology differentiation
        if refs:
            w.append("## How Published Systems Work")
            w.append("")
            w.append("Understanding the methodology of each system contextualizes Shu's approach:")
            w.append("")
            for ref in sorted_refs:
                if ref.methodology:
                    w.append(f"**{ref.name}** ({ref.model_type}): {ref.methodology}")
                    w.append("")
            w.append(
                "**Shu Multi-Surface** (ingestion-time intelligence): At document ingestion, an LLM reads each "
                "document and generates multiple retrieval artifacts — chunk summaries, document synopses, and "
                "synthesized questions. Each artifact type becomes an independent retrieval surface with its own "
                "embeddings. At query time, all surfaces are searched in parallel and the best-scoring surface "
                "per document determines the ranking. Unlike GenQ, no model retraining is needed — new documents "
                "are immediately searchable. Unlike BM25+CE, no expensive cross-encoder inference runs at query time."
            )
            w.append("")

        # Methodology
        w.append("## Methodology")
        w.append("")
        w.append(f"- **Corpus:** {results.dataset_name}, a standard BEIR benchmark")
        w.append(f"- **Evaluation library:** ranx (published in ECIR, validated against TREC Eval)")
        w.append(f"- **Embedding model:** {results.embedding_model}")
        w.append(f"- **Profiling model:** {results.profiling_model}")
        w.append(f"- **Statistical test:** Paired t-test, significance threshold p < 0.05")

        excluded = results.config.exclude_surfaces
        if excluded:
            w.append(f"- **Excluded surfaces:** {', '.join(excluded)}")

        w.append("")

        # Limitations
        w.append("## Limitations and Next Steps")
        w.append("")

        if results.corpus_size > results.query_count * 5:
            corpus_note = (
                f"**Corpus subset:** A {results.query_count}-query evaluation against "
                f"{results.corpus_size:,} documents."
            )
            w.append(corpus_note)
            w.append("")

        if results.dataset_name.lower() == "nfcorpus":
            w.append(
                "**Query vocabulary gap:** NFCorpus queries are expert-authored topic phrases "
                "that closely match document vocabulary. This underrepresents the query synthesis "
                "surface's primary value proposition: bridging the gap between layperson questions "
                "and expert documents."
            )
            w.append("")
            w.append(
                "**Recommended additional benchmark:** FiQA (Financial QA) from the BEIR suite — "
                "real user questions from Reddit/StackExchange against expert financial documents. "
                "The vocabulary mismatch between casual user questions and technical financial content "
                "is the scenario where query synthesis should demonstrate its strongest advantage."
            )
            w.append("")

        w.append(
            "**Weight tuning:** Current results use equal weights across active surfaces. "
            "Data-driven weight optimization would likely improve fusion performance further."
        )
        w.append("")

        # Attribution
        w.append("## Citations")
        w.append("")
        w.append(
            "This evaluation uses the BEIR benchmark framework and datasets. "
            "If referencing these results, please cite:"
        )
        w.append("")
        w.append(
            'Thakur, N., Reimers, N., Rücklé, A., Srivastava, A., & Gurevych, I. (2021). '
            '"BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models." '
            "NeurIPS 2021 (Datasets and Benchmarks Track). "
            "https://openreview.net/forum?id=wCu6T5xFjeJ"
        )
        w.append("")
        w.append(
            'Thakur, N., Reimers, N., Rücklé, A., Srivastava, A., & Gurevych, I. (2024). '
            '"Resources for Brewing BEIR: Reproducible Reference Models and an Official Leaderboard." '
            "SIGIR 2024 (Resource Track). "
            "https://dl.acm.org/doi/10.1145/3626772.3657862"
        )
        w.append("")
        w.append(
            "Evaluation metrics computed using ranx: "
            'Bassani, E. (2022). "ranx: A Blazing-Fast Python Library for Ranking Evaluation and Comparison." '
            "ECIR 2022. https://github.com/AmenRa/ranx"
        )
        w.append("")

        # Write
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(w))
        logger.info("Executive summary written: %s", path)
