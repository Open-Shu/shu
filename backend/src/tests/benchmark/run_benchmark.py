"""CLI entry point for running full accuracy benchmarks.

Usage:
    # Download dataset first
    python -m tests.benchmark.download_datasets --dataset nfcorpus

    # Run full benchmark (requires running Shu with profiling enabled)
    python -m tests.benchmark.run_benchmark --dataset nfcorpus --output-dir tests/benchmark/.results

    # Reuse existing KB (skip ingestion)
    python -m tests.benchmark.run_benchmark --dataset nfcorpus --reuse-kb <kb-id>

    # Quick test with the small test subset
    python -m tests.benchmark.run_benchmark --dataset test_subset --output-dir tests/benchmark/.results
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from shu.core.logging import get_logger

logger = get_logger(__name__)

DATASETS_DIR = Path(__file__).parent / ".datasets"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Shu RAG Accuracy Benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="Dataset name (e.g., 'nfcorpus', 'scifact', 'test_subset') or path to dataset dir",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / ".results",
        help="Output directory for reports (default: tests/benchmark/.results/)",
    )
    parser.add_argument(
        "--reuse-kb",
        type=str,
        default=None,
        help="Reuse existing KB ID (skip ingestion)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Search result limit per query (default: 100)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.0,
        help="Search score threshold (default: 0.0)",
    )
    parser.add_argument(
        "--stat-test",
        choices=["student", "fisher"],
        default="student",
        help="Statistical test for comparison (default: student)",
    )
    parser.add_argument(
        "--target-status",
        choices=["content_processed", "profile_processed"],
        default="profile_processed",
        help="Wait for documents to reach this status (default: profile_processed)",
    )
    parser.add_argument(
        "--ingestion-timeout",
        type=float,
        default=3600,
        help="Max seconds to wait for ingestion/profiling (default: 3600)",
    )
    parser.add_argument(
        "--exclude-surface",
        action="append",
        default=[],
        dest="exclude_surfaces",
        help="Exclude a surface from multi-surface search (can repeat, e.g. --exclude-surface bm25)",
    )
    parser.add_argument(
        "--weight",
        action="append",
        default=[],
        dest="weights",
        metavar="SURFACE=VALUE",
        help="Set surface weight (can repeat, e.g. --weight chunk_vector=0.42 --weight query_match=0.30)",
    )
    parser.add_argument(
        "--fusion-formula",
        type=str,
        default=None,
        help="Fusion formula override (e.g. 'weighted_average' or 'max_sqrt_mean_max')",
    )
    parser.add_argument(
        "--qrels-split",
        type=str,
        default="test",
        help="Which qrels split to use (default: test, use 'shu_judged' for cross-topic qrels)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def resolve_dataset_dir(dataset: str) -> Path:
    """Resolve dataset name or path to an actual directory."""
    # Direct path
    path = Path(dataset)
    if path.is_dir():
        return path

    # Named dataset in .datasets/
    named = DATASETS_DIR / dataset
    if named.is_dir():
        return named

    raise FileNotFoundError(
        f"Dataset '{dataset}' not found. Looked in:\n"
        f"  - {path}\n"
        f"  - {named}\n"
        f"Run: python -m tests.benchmark.download_datasets --dataset {dataset}"
    )


async def run(args: argparse.Namespace) -> None:
    """Execute the benchmark."""
    from tests.integ.integration_test_runner import IntegrationTestRunner

    from .benchmark_runner import BenchmarkConfig, BenchmarkRunner
    from .report_generator import ReportGenerator

    dataset_dir = resolve_dataset_dir(args.dataset)
    dataset_name = dataset_dir.name

    # Parse --weight SURFACE=VALUE pairs
    weight_overrides: dict[str, float] = {}
    for w in args.weights:
        if "=" not in w:
            logger.error("Invalid --weight format '%s', expected SURFACE=VALUE", w)
            return
        surface, value = w.split("=", 1)
        weight_overrides[surface.strip()] = float(value.strip())

    config = BenchmarkConfig(
        dataset_dir=dataset_dir,
        dataset_name=dataset_name,
        reuse_kb_id=args.reuse_kb,
        target_status=args.target_status,
        ingestion_timeout=args.ingestion_timeout,
        search_limit=args.limit,
        search_threshold=args.threshold,
        stat_test=args.stat_test,
        exclude_surfaces=args.exclude_surfaces,
        weight_overrides=weight_overrides,
        fusion_formula_override=args.fusion_formula,
        qrels_split=args.qrels_split,
    )

    # Set up test infrastructure (gives us client, db, auth)
    runner = IntegrationTestRunner()
    await runner.setup()

    try:
        benchmark = BenchmarkRunner(runner.client, runner.db, runner.auth_headers, config)
        results = await benchmark.run()

        # Generate reports
        report_gen = ReportGenerator()
        files = report_gen.generate_full_report(results, args.output_dir)

        print(f"\nBenchmark complete. Reports written to {args.output_dir}:")
        for f in files:
            print(f"  {f}")

        # Print summary to stdout
        print(f"\n{'Metric':<20} {'Baseline':>12} {'Multi-Surface':>14} {'Delta':>10}")
        print(f"{'-'*20} {'-'*12} {'-'*14} {'-'*10}")
        for metric in config.metrics:
            b = results.baseline_scores.get(metric, 0.0)
            m = results.multi_surface_scores.get(metric, 0.0)
            d = results.deltas.get(metric, 0.0)
            sign = "+" if d >= 0 else ""
            print(f"{metric:<20} {b:>12.4f} {m:>14.4f} {sign}{d:>8.1f}%")

        if results.kb_id:
            print(f"\nKB ID (for --reuse-kb): {results.kb_id}")

    finally:
        await runner.teardown()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
