"""CLI entry point for cross-topic relevance judgment benchmark.

Two-phase process:
    Phase 1 (collect): Run queries through both search strategies, collect candidates
    Phase 2 (judge):   Read candidates, judge relevance, produce qrels

Usage:
    # Phase 1: Collect candidates (requires running Shu server)
    python -m tests.benchmark.run_relevance_judge collect \
        --dataset nfcorpus \
        --kb-id 63f1d9ff-b6a2-420b-9f06-6a1400bda65f \
        --limit 20 \
        --threshold 0.3

    # Phase 2: Judge relevance (done by LLM reading candidates)
    python -m tests.benchmark.run_relevance_judge judge \
        --dataset nfcorpus

    # Phase 3: Run benchmark with custom qrels
    python -m tests.benchmark.run_benchmark --dataset nfcorpus \
        --reuse-kb <kb-id> --skip-ablation
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

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

    # Phase 2: judge
    judge_parser = subparsers.add_parser(
        "judge",
        help="Prepare candidate batches for LLM relevance judgment",
    )
    judge_parser.add_argument("--dataset", required=True, help="Dataset name")
    judge_parser.add_argument("--batch-size", type=int, default=10, help="Candidates per batch (default: 10)")
    judge_parser.add_argument("--verbose", action="store_true")

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


async def run_judge(args: argparse.Namespace) -> None:
    """Phase 2: Prepare batches for LLM judgment."""
    from .relevance_judge import judge_batch, load_candidates

    dataset_dir = resolve_dataset_dir(args.dataset)
    candidates_path = dataset_dir / "relevance_judge" / "candidates.jsonl"

    if not candidates_path.exists():
        raise FileNotFoundError(
            f"No candidates found at {candidates_path}. Run 'collect' first."
        )

    candidates = load_candidates(candidates_path)
    batches = judge_batch(candidates, batch_size=args.batch_size)

    # Save batches for LLM processing
    output_dir = dataset_dir / "relevance_judge"
    batches_path = output_dir / "batches.jsonl"
    with open(batches_path, "w", encoding="utf-8") as f:
        for batch in batches:
            f.write(json.dumps(batch) + "\n")

    print(f"\nPrepared {len(batches)} batches of {args.batch_size} candidates each")
    print(f"Total candidates: {len(candidates)}")
    print(f"Batches saved to: {batches_path}")
    print("\nNext: Judge each batch and save results to relevance_judge/judgments.jsonl")


async def run_summary(args: argparse.Namespace) -> None:
    """Show comparison summary from completed judgments."""
    from .relevance_judge import comparison_summary, load_candidates, load_judgments

    dataset_dir = resolve_dataset_dir(args.dataset)
    judge_dir = dataset_dir / "relevance_judge"

    candidates = load_candidates(judge_dir / "candidates.jsonl")
    judgments = load_judgments(judge_dir / "judgments.jsonl")

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
    elif args.command == "judge":
        asyncio.run(run_judge(args))
    elif args.command == "summary":
        asyncio.run(run_summary(args))


if __name__ == "__main__":
    main()
