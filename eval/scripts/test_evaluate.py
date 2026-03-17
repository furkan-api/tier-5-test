#!/usr/bin/env python3
"""
Test the evaluation harness against hand-computed expected values.

Toy example: 3 queries, 10 documents. All expected values were computed
by hand and verified independently. See the plan for derivations.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Allow importing evaluate.py from the same directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate import (
    compute_query_metrics,
    compute_run_metrics,
    init_db,
    load_json,
    load_run_aggregate,
    load_run_per_query,
    log_run,
)

TESTS_DIR = Path(__file__).resolve().parent.parent / "tests"
TOL = 1e-4


def close(a, b):
    return abs(a - b) < TOL


def assert_close(actual, expected, label):
    assert close(actual, expected), f"{label}: expected {expected:.4f}, got {actual:.4f}"


# ---------------------------------------------------------------------------
# Test 1: Per-query metrics against hand-computed values
# ---------------------------------------------------------------------------

def test_per_query_metrics():
    gold = load_json(TESTS_DIR / "toy_gold_standard.json")
    run = load_json(TESTS_DIR / "toy_run.json")

    _, per_query = compute_run_metrics(run, gold)
    by_qid = {pq["query_id"]: pq for pq in per_query}

    # TQ1: retrieved D04(0), D01(3), D03(1), D10(0), D08(0), D02(2), D09(0), D07(0), D06(0), D05(0)
    tq1 = by_qid["TQ1"]
    assert_close(tq1["recall_at_5"], 0.6667, "TQ1 recall@5")
    assert_close(tq1["recall_at_10"], 1.0000, "TQ1 recall@10")
    assert_close(tq1["recall_at_20"], 1.0000, "TQ1 recall@20")
    assert_close(tq1["ndcg_at_5"], 0.5234, "TQ1 ndcg@5")
    assert_close(tq1["ndcg_at_10"], 0.6372, "TQ1 ndcg@10")
    assert_close(tq1["mrr"], 0.5000, "TQ1 mrr")
    assert_close(tq1["hit_rate_at_5"], 1.0000, "TQ1 hit_rate@5")

    # TQ2: retrieved D05(3), D08(0), D06(3), D07(0), D10(0), ...
    tq2 = by_qid["TQ2"]
    assert_close(tq2["recall_at_5"], 1.0000, "TQ2 recall@5")
    assert_close(tq2["recall_at_10"], 1.0000, "TQ2 recall@10")
    assert_close(tq2["ndcg_at_5"], 0.9197, "TQ2 ndcg@5")
    assert_close(tq2["ndcg_at_10"], 0.9197, "TQ2 ndcg@10")
    assert_close(tq2["mrr"], 1.0000, "TQ2 mrr")
    assert_close(tq2["hit_rate_at_5"], 1.0000, "TQ2 hit_rate@5")

    # TQ3: retrieved D10(0), D08(0), D07(0), D04(0), D06(0), D09(1), D01(2), ...
    tq3 = by_qid["TQ3"]
    assert_close(tq3["recall_at_5"], 0.0000, "TQ3 recall@5")
    assert_close(tq3["recall_at_10"], 1.0000, "TQ3 recall@10")
    assert_close(tq3["ndcg_at_5"], 0.0000, "TQ3 ndcg@5")
    assert_close(tq3["ndcg_at_10"], 0.3735, "TQ3 ndcg@10")
    assert_close(tq3["mrr"], 0.1667, "TQ3 mrr")
    assert_close(tq3["hit_rate_at_5"], 0.0000, "TQ3 hit_rate@5")

    print("  PASS: per-query metrics match hand-computed values")


# ---------------------------------------------------------------------------
# Test 2: Aggregate metrics
# ---------------------------------------------------------------------------

def test_aggregate_metrics():
    gold = load_json(TESTS_DIR / "toy_gold_standard.json")
    run = load_json(TESTS_DIR / "toy_run.json")

    aggregate, _ = compute_run_metrics(run, gold)

    assert_close(aggregate["recall_at_5"], 0.5556, "mean recall@5")
    assert_close(aggregate["recall_at_10"], 1.0000, "mean recall@10")
    assert_close(aggregate["recall_at_20"], 1.0000, "mean recall@20")
    assert_close(aggregate["ndcg_at_5"], 0.4811, "mean ndcg@5")
    assert_close(aggregate["ndcg_at_10"], 0.6435, "mean ndcg@10")
    assert_close(aggregate["mrr"], 0.5556, "mean mrr")
    assert_close(aggregate["hit_rate_at_5"], 0.6667, "mean hit_rate@5")

    print("  PASS: aggregate metrics match hand-computed values")


# ---------------------------------------------------------------------------
# Test 3: Edge cases
# ---------------------------------------------------------------------------

def test_edge_cases():
    # Empty retrieved list
    metrics = compute_query_metrics([], {"D01": 3, "D02": 1})
    for m in metrics:
        assert metrics[m] == 0.0, f"empty retrieved: {m} should be 0.0, got {metrics[m]}"

    # No relevant docs (all relevance=0)
    metrics = compute_query_metrics(["D01", "D02"], {"D01": 0, "D02": 0})
    for m in metrics:
        assert metrics[m] == 0.0, f"no relevant docs: {m} should be 0.0, got {metrics[m]}"

    # Perfect retrieval: single relevant doc at rank 1
    metrics = compute_query_metrics(["D01", "D02"], {"D01": 3, "D02": 0})
    assert_close(metrics["recall_at_5"], 1.0, "perfect single recall@5")
    assert_close(metrics["ndcg_at_5"], 1.0, "perfect single ndcg@5")
    assert_close(metrics["mrr"], 1.0, "perfect single mrr")
    assert_close(metrics["hit_rate_at_5"], 1.0, "perfect single hit_rate@5")

    print("  PASS: edge cases handled correctly")


# ---------------------------------------------------------------------------
# Test 4: SQLite round-trip and comparison
# ---------------------------------------------------------------------------

def test_sqlite_roundtrip():
    gold = load_json(TESTS_DIR / "toy_gold_standard.json")
    run = load_json(TESTS_DIR / "toy_run.json")
    aggregate, per_query = compute_run_metrics(run, gold)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    try:
        conn = init_db(db_path)
        log_run(conn, "test-run-1", "test label", "abc123", aggregate, per_query)

        loaded_agg = load_run_aggregate(conn, "test-run-1")
        for m in aggregate:
            assert_close(loaded_agg[m], aggregate[m], f"roundtrip {m}")

        loaded_pq = load_run_per_query(conn, "test-run-1")
        assert len(loaded_pq) == len(per_query), "roundtrip per-query count mismatch"

        # Log a second run (perfect ordering) and verify comparison loads both
        perfect_run = {
            "run_id": "perfect-run",
            "results": [
                {"query_id": "TQ1", "retrieved_docs": ["D01", "D02", "D03", "D04"]},
                {"query_id": "TQ2", "retrieved_docs": ["D05", "D06", "D07", "D08"]},
                {"query_id": "TQ3", "retrieved_docs": ["D01", "D09", "D10"]},
            ],
        }
        agg2, pq2 = compute_run_metrics(perfect_run, gold)
        log_run(conn, "test-run-2", "perfect", "abc123", agg2, pq2)

        loaded_agg2 = load_run_aggregate(conn, "test-run-2")
        assert_close(loaded_agg2["ndcg_at_5"], 1.0, "perfect run ndcg@5")
        assert_close(loaded_agg2["mrr"], 1.0, "perfect run mrr")

        conn.close()
        print("  PASS: SQLite round-trip and comparison data correct")
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\nRunning evaluation harness tests...")
    test_per_query_metrics()
    test_aggregate_metrics()
    test_edge_cases()
    test_sqlite_roundtrip()
    print("\nALL TESTS PASSED\n")
