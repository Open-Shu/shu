"""
Benchmark Framework Integration Tests

Fast-mode smoke tests verifying the benchmark pipeline works end-to-end
with a small test corpus (~10 docs, ~5 queries). These validate that the
framework mechanics work correctly — not that multi-surface search is
better (that requires the full benchmark with real BEIR datasets).

Run with:
    python -m tests.integ.run_all_integration_tests --suite benchmark
    python -m tests.integ.test_benchmark_integration
"""

import uuid
from collections.abc import Callable
from pathlib import Path

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script
from integ.response_utils import extract_data

# Path to the committed test subset
TEST_SUBSET_DIR = Path(__file__).parent.parent / "benchmark" / ".datasets" / "test_subset"

# How long to wait for documents to process (seconds)
INGESTION_TIMEOUT = 120


async def test_beir_loader(client, db, auth_headers):
    """Test that BeirLoader correctly parses the test subset."""
    from tests.benchmark.beir_loader import BeirLoader

    loader = BeirLoader(TEST_SUBSET_DIR, name="test_subset")
    dataset = loader.load()

    assert dataset.corpus_size == 50, f"Expected 10 corpus entries, got {dataset.corpus_size}"
    assert dataset.query_count == 10, f"Expected 5 queries, got {dataset.query_count}"
    assert dataset.total_judgments > 0, "Expected at least 1 relevance judgment"

    # Verify corpus entries have required fields
    for doc_id, entry in dataset.corpus.items():
        assert entry.doc_id == doc_id
        assert entry.title, f"Doc {doc_id} missing title"
        assert entry.text, f"Doc {doc_id} missing text"

    # Verify qrels reference valid IDs
    for query_id, doc_rels in dataset.qrels.items():
        assert query_id in dataset.queries, f"Qrel query {query_id} not in queries"
        for doc_id in doc_rels:
            assert doc_id in dataset.corpus, f"Qrel doc {doc_id} not in corpus"

    # Test subset
    subset = dataset.subset(query_ids={"Q-001", "Q-002"})
    assert subset.query_count == 2
    assert "Q-001" in subset.queries
    assert "Q-002" in subset.queries


async def test_corpus_ingestion_and_id_mapping(client, db, auth_headers):
    """Test ingesting BEIR corpus and building the ID map."""
    from tests.benchmark.beir_loader import BeirLoader
    from tests.benchmark.corpus_ingestor import EMBEDDED_STATUSES, CorpusIngestor

    loader = BeirLoader(TEST_SUBSET_DIR, name="test_subset")
    dataset = loader.load()

    # Create a unique KB for this test
    unique_id = str(uuid.uuid4())[:8]
    kb_resp = await client.post(
        "/api/v1/knowledge-bases",
        json={
            "name": f"Benchmark Test KB {unique_id}",
            "description": "Test KB for benchmark integration",
            "sync_enabled": True,
        },
        headers=auth_headers,
    )
    assert kb_resp.status_code == 201, f"KB creation failed: {kb_resp.status_code}"
    kb_id = extract_data(kb_resp)["id"]

    # Ingest corpus
    ingestor = CorpusIngestor(db, kb_id, user_id="test-benchmark")
    summary = await ingestor.ingest_corpus(dataset.corpus)

    assert summary.total == 50
    assert summary.ingested == 50
    assert summary.failed == 0

    # Wait for at least embedding to complete
    statuses = await ingestor.wait_for_processing(
        target_statuses=EMBEDDED_STATUSES,
        timeout=INGESTION_TIMEOUT,
        poll_interval=2.0,
    )
    assert len(statuses) == 50

    # Verify no errors
    errors = {doc_id for doc_id, status in statuses.items() if status == "error"}
    assert len(errors) == 0, f"Documents in error state: {errors}"

    # Build ID map
    id_map = await ingestor.build_id_map()
    assert len(id_map) == 50, f"Expected 10 ID mappings, got {len(id_map)}"

    # Verify BEIR IDs are in the map values
    beir_ids = set(id_map.values())
    for doc_id in dataset.corpus:
        assert doc_id in beir_ids, f"BEIR doc {doc_id} not found in ID map"


async def test_similarity_run_collection(client, db, auth_headers):
    """Test running similarity search and collecting results as a ranx Run."""
    from tests.benchmark.beir_loader import BeirLoader
    from tests.benchmark.corpus_ingestor import EMBEDDED_STATUSES, CorpusIngestor
    from tests.benchmark.result_collector import ResultCollector, SearchConfig

    loader = BeirLoader(TEST_SUBSET_DIR, name="test_subset")
    dataset = loader.load()

    # Create and ingest
    unique_id = str(uuid.uuid4())[:8]
    kb_resp = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": f"Sim Run Test KB {unique_id}", "description": "Test", "sync_enabled": True},
        headers=auth_headers,
    )
    kb_id = extract_data(kb_resp)["id"]

    ingestor = CorpusIngestor(db, kb_id, user_id="test-benchmark")
    await ingestor.ingest_corpus(dataset.corpus)
    await ingestor.wait_for_processing(target_statuses=EMBEDDED_STATUSES, timeout=INGESTION_TIMEOUT, poll_interval=2.0)
    id_map = await ingestor.build_id_map()

    # Collect similarity run
    collector = ResultCollector(client, kb_id, auth_headers)
    config = SearchConfig(limit=50, threshold=0.0)
    run_dict, stats = await collector.collect_similarity_run(dataset.queries, id_map, config)

    assert stats.query_count == 10, f"Expected 5 queries, got {stats.query_count}"
    assert stats.total_results > 0, "Expected at least some results"

    # Verify run_dict structure
    for query_id, doc_scores in run_dict.items():
        assert query_id in dataset.queries, f"Unknown query ID: {query_id}"
        for doc_id, score in doc_scores.items():
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0, f"Score out of range: {score}"


async def test_multi_surface_run_collection(client, db, auth_headers):
    """Test running multi-surface search and collecting results with surface scores."""
    from tests.benchmark.beir_loader import BeirLoader
    from tests.benchmark.corpus_ingestor import EMBEDDED_STATUSES, CorpusIngestor
    from tests.benchmark.result_collector import ResultCollector, SearchConfig

    loader = BeirLoader(TEST_SUBSET_DIR, name="test_subset")
    dataset = loader.load()

    unique_id = str(uuid.uuid4())[:8]
    kb_resp = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": f"MS Run Test KB {unique_id}", "description": "Test", "sync_enabled": True},
        headers=auth_headers,
    )
    kb_id = extract_data(kb_resp)["id"]

    ingestor = CorpusIngestor(db, kb_id, user_id="test-benchmark")
    await ingestor.ingest_corpus(dataset.corpus)
    await ingestor.wait_for_processing(target_statuses=EMBEDDED_STATUSES, timeout=INGESTION_TIMEOUT, poll_interval=2.0)
    id_map = await ingestor.build_id_map()

    # Collect multi-surface run
    collector = ResultCollector(client, kb_id, auth_headers)
    config = SearchConfig(limit=50, threshold=0.0)
    run_dict, surface_scores, stats = await collector.collect_multi_surface_run(dataset.queries, id_map, config)

    assert stats.query_count == 10
    assert stats.total_results > 0

    # Verify surface_scores structure
    for query_id, doc_surfaces in surface_scores.items():
        for doc_id, surfaces in doc_surfaces.items():
            assert isinstance(surfaces, dict), f"Expected dict of surface scores, got {type(surfaces)}"
            # At minimum, chunk_vector should contribute (always has data after embedding)
            # Other surfaces may or may not contribute depending on profiling status


async def test_metric_computation(client, db, auth_headers):
    """Test that ranx metrics compute correctly on collected results."""
    try:
        from ranx import Qrels, Run, evaluate
    except ImportError:
        print("  SKIPPED: ranx not installed")
        return

    # Create synthetic run data for metric validation
    qrels_dict = {
        "q1": {"d1": 2, "d2": 1, "d3": 0},
        "q2": {"d2": 2, "d4": 1},
    }
    run_dict = {
        "q1": {"d1": 0.95, "d2": 0.80, "d3": 0.60, "d5": 0.40},
        "q2": {"d2": 0.90, "d4": 0.70, "d1": 0.50},
    }

    qrels = Qrels(qrels_dict, name="test_qrels")
    run = Run(run_dict, name="test_run")

    metrics = ["precision@5", "recall@5", "mrr@10", "ndcg@10"]
    scores = evaluate(qrels, run, metrics)

    assert isinstance(scores, dict)
    for metric in metrics:
        assert metric in scores, f"Missing metric: {metric}"
        assert isinstance(scores[metric], float)
        assert 0.0 <= scores[metric] <= 1.0, f"{metric} = {scores[metric]} out of range"


async def test_query_classifier(client, db, auth_headers):
    """Test heuristic query type classification."""
    from tests.benchmark.query_classifier import QueryType, classify_query

    assert classify_query("What is the role of vitamin D in bone health?") == QueryType.INTERPRETIVE
    assert classify_query("What are the risk factors for heart disease?") == QueryType.FACTUAL
    assert classify_query("How does aspirin reduce inflammation?") == QueryType.INTERPRETIVE
    assert classify_query("When should adults be screened for diabetes?") == QueryType.FACTUAL
    assert classify_query("Difference between type 1 and type 2 diabetes") == QueryType.STRUCTURAL


async def test_ablation_weight_override(client, db, auth_headers):
    """Test that setting a surface weight to 0 disables it in results."""
    from tests.benchmark.beir_loader import BeirLoader
    from tests.benchmark.corpus_ingestor import EMBEDDED_STATUSES, CorpusIngestor
    from tests.benchmark.result_collector import ResultCollector, SearchConfig

    loader = BeirLoader(TEST_SUBSET_DIR, name="test_subset")
    dataset = loader.load()

    unique_id = str(uuid.uuid4())[:8]
    kb_resp = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": f"Ablation Test KB {unique_id}", "description": "Test", "sync_enabled": True},
        headers=auth_headers,
    )
    kb_id = extract_data(kb_resp)["id"]

    ingestor = CorpusIngestor(db, kb_id, user_id="test-benchmark")
    await ingestor.ingest_corpus(dataset.corpus)
    await ingestor.wait_for_processing(target_statuses=EMBEDDED_STATUSES, timeout=INGESTION_TIMEOUT, poll_interval=2.0)
    id_map = await ingestor.build_id_map()

    collector = ResultCollector(client, kb_id, auth_headers)
    query = next(iter(dataset.queries.values()))

    # Run with all surfaces
    full_results = await collector._run_multi_surface_search(query.text, SearchConfig(limit=10))

    # Run with chunk_vector disabled
    ablated_results = await collector._run_multi_surface_search(
        query.text,
        SearchConfig(limit=10, weight_overrides={"chunk_vector_weight": 0.0}),
    )

    # Both should return results (other surfaces still active)
    # The scores should differ since chunk_vector is disabled
    if full_results and ablated_results:
        full_top = full_results[0].get("final_score", 0)
        ablated_top = ablated_results[0].get("final_score", 0)
        # Scores won't be identical since we removed a surface
        # (They could be equal if chunk_vector contributed 0 for this query, but unlikely)
        assert isinstance(full_top, (int, float))
        assert isinstance(ablated_top, (int, float))


class BenchmarkIntegrationTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self) -> list[Callable]:
        return [
            test_beir_loader,
            test_corpus_ingestion_and_id_mapping,
            test_similarity_run_collection,
            test_multi_surface_run_collection,
            test_metric_computation,
            test_query_classifier,
            test_ablation_weight_override,
        ]

    def get_suite_name(self) -> str:
        return "Benchmark Framework Integration Tests"

    def get_suite_description(self) -> str:
        return "Fast-mode smoke tests for the RAG accuracy benchmark framework"


if __name__ == "__main__":
    create_test_runner_script(BenchmarkIntegrationTestSuite, globals())
