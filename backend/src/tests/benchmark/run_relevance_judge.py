"""CLI entry point for cross-topic relevance judgment benchmark.

Two-phase process:
    Phase 1 (collect): Run queries through both search strategies, collect candidates
    Phase 2 (summary): Show comparison summary from completed judgments

Relevance judging itself is performed externally (e.g., by Claude reading
full documents) and results saved to judgments.jsonl.

Usage:
    # Phase 1: Collect candidates (requires running Shu server)
    python -m tests.benchmark.run_relevance_judge collect \
        --dataset nfcorpus \
        --kb-id 63f1d9ff-b6a2-420b-9f06-6a1400bda65f \
        --limit 20 \
        --threshold 0.3

    # Phase 2: Show comparison summary (after external judging)
    python -m tests.benchmark.run_relevance_judge summary \
        --dataset nfcorpus

    # Then: Run benchmark with custom qrels
    python -m tests.benchmark.run_benchmark --dataset nfcorpus \
        --reuse-kb <kb-id>
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
        description="Cross-Topic Relevance Judgment Benchmark (SHU-647)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Phase 1: collect
    collect_parser = subparsers.add_parser(
        "collect",
        help="Collect candidate documents from baseline and multi-surface search",
    )
    collect_parser.add_argument("--dataset", required=True, help="Dataset name (e.g., nfcorpus)")
    collect_parser.add_argument("--kb-id", required=True, help="Knowledge base ID")
    collect_parser.add_argument("--limit", type=int, default=20, help="Top-k results per strategy (default: 20)")
    collect_parser.add_argument("--threshold", type=float, default=0.0, help="Minimum score threshold (default: 0.0)")
    collect_parser.add_argument("--verbose", action="store_true")

    # Summary: show comparison after judgments are complete
    summary_parser = subparsers.add_parser(
        "summary",
        help="Show comparison summary from completed judgments",
    )
    summary_parser.add_argument("--dataset", required=True, help="Dataset name")
    summary_parser.add_argument("--verbose", action="store_true")

    return parser.parse_args()


def resolve_dataset_dir(dataset: str) -> Path:
    path = Path(dataset)
    if path.is_dir():
        return path
    named = DATASETS_DIR / dataset
    if named.is_dir():
        return named
    raise FileNotFoundError(f"Dataset '{dataset}' not found at {path} or {named}")


async def run_collect(args: argparse.Namespace) -> None:
    """Phase 1: Collect candidates from both search strategies."""
    from tests.integ.integration_test_runner import IntegrationTestRunner

    from .beir_loader import BeirLoader
    from .corpus_ingestor import CorpusIngestor
    from .relevance_judge import CandidateCollector, save_candidates

    dataset_dir = resolve_dataset_dir(args.dataset)
    loader = BeirLoader(dataset_dir, name=args.dataset)
    dataset = loader.load()

    # Filter to test queries with qrels
    queries_with_qrels = {qid: dataset.queries[qid] for qid in dataset.qrels if qid in dataset.queries}
    dataset.queries = queries_with_qrels
    logger.info("Loaded %d queries with qrels", len(dataset.queries))

    # Set up test infrastructure
    runner = IntegrationTestRunner()
    await runner.setup()

    try:
        # Build ID map
        ingestor = CorpusIngestor(runner.db, args.kb_id, user_id="benchmark")
        id_map = await ingestor.build_id_map(profiled_only=True)
        logger.info("ID map: %d entries", len(id_map))

        # Filter qrels to profiled docs
        profiled_beir_ids = set(id_map.values())
        dataset.qrels = {
            qid: {did: rel for did, rel in docs.items() if did in profiled_beir_ids}
            for qid, docs in dataset.qrels.items()
        }
        dataset.qrels = {qid: docs for qid, docs in dataset.qrels.items() if docs}
        dataset.queries = {qid: q for qid, q in dataset.queries.items() if qid in dataset.qrels}
        logger.info("Filtered to %d queries after profiled-doc filter", len(dataset.queries))

        # Collect candidates
        collector = CandidateCollector(
            client=runner.client,
            kb_id=args.kb_id,
            auth_headers=runner.auth_headers,
            id_map=id_map,
        )
        candidates = await collector.collect(
            dataset.queries,
            dataset,
            limit=args.limit,
            threshold=args.threshold,
        )

        # Save candidates
        output_dir = dataset_dir / "relevance_judge"
        output_dir.mkdir(exist_ok=True)
        save_candidates(candidates, output_dir / "candidates.jsonl")

        print(f"\nCollected {len(candidates)} candidates from {len(dataset.queries)} queries")
        print(f"Saved to: {output_dir / 'candidates.jsonl'}")

    finally:
        await runner.teardown()


async def run_summary(args: argparse.Namespace) -> None:
    """Show comparison summary from completed judgments."""
    from .relevance_judge import comparison_summary, load_candidates, load_judgments

    dataset_dir = resolve_dataset_dir(args.dataset)
    judge_dir = dataset_dir / "relevance_judge"

    candidates_path = judge_dir / "candidates.jsonl"
    judgments_path = judge_dir / "judgments.jsonl"

    if not candidates_path.exists():
        raise FileNotFoundError(
            f"No candidates found at {candidates_path}. Run 'collect' first."
        )
    if not judgments_path.exists():
        raise FileNotFoundError(
            f"No judgments found at {judgments_path}. Run 'judge' first."
        )

    candidates = load_candidates(candidates_path)
    judgments = load_judgments(judgments_path)

    summary = comparison_summary(judgments, candidates)
    print(summary)


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.command == "collect":
        asyncio.run(run_collect(args))
    elif args.command == "summary":
        asyncio.run(run_summary(args))


if __name__ == "__main__":
    main()
