#!/usr/bin/env python3
"""
Evaluation harness for Turkish legal jurisprudence retrieval.

Computes: Recall@K, NDCG@K, MRR, Hit Rate@5.
Logs results to SQLite. Compares runs by metric deltas and per-query wins/losses.

Usage:
    # Evaluate a run file against gold standard
    python eval/scripts/evaluate.py --run-file path/to/run.json

    # Compare two previously logged runs
    python eval/scripts/evaluate.py --run-id baseline-v1 --run-id experiment-v2

    # Show per-query breakdown for a stored run
    python eval/scripts/evaluate.py --run-id baseline-v1 --per-query
"""

import argparse
import json
import math
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_GOLD = EVAL_DIR / "gold_standard.json"
DEFAULT_DB = EVAL_DIR / "results.db"

METRIC_NAMES = [
    "recall_at_5", "recall_at_10", "recall_at_20",
    "ndcg_at_5", "ndcg_at_10",
    "mrr", "hit_rate_at_5",
]

METRIC_DISPLAY = {
    "recall_at_5": "Recall@5",
    "recall_at_10": "Recall@10",
    "recall_at_20": "Recall@20",
    "ndcg_at_5": "NDCG@5",
    "ndcg_at_10": "NDCG@10",
    "mrr": "MRR",
    "hit_rate_at_5": "Hit Rate@5",
}


# ---------------------------------------------------------------------------
# Metric computation (pure functions)
# ---------------------------------------------------------------------------

def recall_at_k(retrieved, relevant, k):
    if not relevant:
        return 0.0
    found = len(set(retrieved[:k]) & relevant)
    return found / len(relevant)


def ndcg_at_k(retrieved, relevance_map, k):
    dcg = 0.0
    for i, doc_id in enumerate(retrieved[:k], start=1):
        rel = relevance_map.get(doc_id, 0)
        dcg += (2 ** rel - 1) / math.log2(i + 1)

    ideal_rels = sorted(relevance_map.values(), reverse=True)
    idcg = 0.0
    for i, rel in enumerate(ideal_rels[:k], start=1):
        idcg += (2 ** rel - 1) / math.log2(i + 1)

    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def reciprocal_rank(retrieved, relevant):
    for i, doc_id in enumerate(retrieved, start=1):
        if doc_id in relevant:
            return 1.0 / i
    return 0.0


def hit_rate_at_k(retrieved, relevant, k):
    if set(retrieved[:k]) & relevant:
        return 1.0
    return 0.0


def compute_query_metrics(retrieved, relevance_map):
    relevant = {doc_id for doc_id, grade in relevance_map.items() if grade >= 1}
    return {
        "recall_at_5": recall_at_k(retrieved, relevant, 5),
        "recall_at_10": recall_at_k(retrieved, relevant, 10),
        "recall_at_20": recall_at_k(retrieved, relevant, 20),
        "ndcg_at_5": ndcg_at_k(retrieved, relevance_map, 5),
        "ndcg_at_10": ndcg_at_k(retrieved, relevance_map, 10),
        "mrr": reciprocal_rank(retrieved, relevant),
        "hit_rate_at_5": hit_rate_at_k(retrieved, relevant, 5),
    }


def compute_run_metrics(run_data, gold_data):
    gold_by_qid = {}
    for q in gold_data["queries"]:
        relevance_map = {j["doc_id"]: j["relevance"] for j in q["relevance_judgments"]}
        gold_by_qid[q["query_id"]] = relevance_map

    per_query = []
    missing = []
    for result in run_data["results"]:
        qid = result["query_id"]
        if qid not in gold_by_qid:
            missing.append(qid)
            continue
        metrics = compute_query_metrics(result["retrieved_docs"], gold_by_qid[qid])
        metrics["query_id"] = qid
        per_query.append(metrics)

    if missing:
        print(f"WARNING: {len(missing)} queries in run file not found in gold standard: {missing[:5]}", file=sys.stderr)

    if not per_query:
        aggregate = {m: 0.0 for m in METRIC_NAMES}
        return aggregate, per_query

    aggregate = {}
    for m in METRIC_NAMES:
        aggregate[m] = sum(pq[m] for pq in per_query) / len(per_query)

    return aggregate, per_query


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_git_commit():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# SQLite storage
# ---------------------------------------------------------------------------

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id       TEXT PRIMARY KEY,
            timestamp    TEXT NOT NULL,
            config_label TEXT NOT NULL DEFAULT '',
            git_commit   TEXT NOT NULL DEFAULT 'unknown',
            recall_at_5  REAL NOT NULL,
            recall_at_10 REAL NOT NULL,
            recall_at_20 REAL NOT NULL,
            ndcg_at_5    REAL NOT NULL,
            ndcg_at_10   REAL NOT NULL,
            mrr          REAL NOT NULL,
            hit_rate_at_5 REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS query_metrics (
            run_id       TEXT NOT NULL,
            query_id     TEXT NOT NULL,
            recall_at_5  REAL NOT NULL,
            recall_at_10 REAL NOT NULL,
            recall_at_20 REAL NOT NULL,
            ndcg_at_5    REAL NOT NULL,
            ndcg_at_10   REAL NOT NULL,
            mrr          REAL NOT NULL,
            hit_rate_at_5 REAL NOT NULL,
            PRIMARY KEY (run_id, query_id),
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        )
    """)
    conn.commit()
    return conn


def log_run(conn, run_id, config_label, git_commit, aggregate, per_query):
    ts = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            "INSERT INTO runs (run_id, timestamp, config_label, git_commit, "
            "recall_at_5, recall_at_10, recall_at_20, ndcg_at_5, ndcg_at_10, mrr, hit_rate_at_5) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, ts, config_label, git_commit,
             aggregate["recall_at_5"], aggregate["recall_at_10"], aggregate["recall_at_20"],
             aggregate["ndcg_at_5"], aggregate["ndcg_at_10"],
             aggregate["mrr"], aggregate["hit_rate_at_5"]),
        )
    except sqlite3.IntegrityError:
        print(f"ERROR: run_id '{run_id}' already exists. Use a different run_id or delete the old one.", file=sys.stderr)
        sys.exit(1)

    for pq in per_query:
        conn.execute(
            "INSERT INTO query_metrics (run_id, query_id, "
            "recall_at_5, recall_at_10, recall_at_20, ndcg_at_5, ndcg_at_10, mrr, hit_rate_at_5) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, pq["query_id"],
             pq["recall_at_5"], pq["recall_at_10"], pq["recall_at_20"],
             pq["ndcg_at_5"], pq["ndcg_at_10"],
             pq["mrr"], pq["hit_rate_at_5"]),
        )
    conn.commit()


def load_run_aggregate(conn, run_id):
    row = conn.execute(
        "SELECT recall_at_5, recall_at_10, recall_at_20, ndcg_at_5, ndcg_at_10, mrr, hit_rate_at_5 "
        "FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        print(f"ERROR: run_id '{run_id}' not found in database.", file=sys.stderr)
        sys.exit(1)
    return dict(zip(METRIC_NAMES, row))


def load_run_per_query(conn, run_id):
    rows = conn.execute(
        "SELECT query_id, recall_at_5, recall_at_10, recall_at_20, ndcg_at_5, ndcg_at_10, mrr, hit_rate_at_5 "
        "FROM query_metrics WHERE run_id = ? ORDER BY query_id", (run_id,)
    ).fetchall()
    result = []
    for row in rows:
        d = {"query_id": row[0]}
        for i, m in enumerate(METRIC_NAMES):
            d[m] = row[i + 1]
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_metrics_table(aggregate, label=""):
    header = f"  Metrics: {label}" if label else "  Metrics"
    print(header)
    print("  " + "-" * 30)
    for m in METRIC_NAMES:
        print(f"  {METRIC_DISPLAY[m]:<15} {aggregate[m]:>8.4f}")
    print()


def print_comparison_table(agg_a, agg_b, run_id_a, run_id_b):
    w_metric = 15
    w_val = 12
    header = f"{'Metric':<{w_metric}} {run_id_a:>{w_val}} {run_id_b:>{w_val}} {'Delta':>{w_val}}"
    sep = "-" * len(header)
    print(f"\n  Comparing: {run_id_a} vs {run_id_b}")
    print(f"  {sep}")
    print(f"  {header}")
    print(f"  {sep}")
    for m in METRIC_NAMES:
        delta = agg_b[m] - agg_a[m]
        sign = "+" if delta >= 0 else ""
        print(f"  {METRIC_DISPLAY[m]:<{w_metric}} {agg_a[m]:>{w_val}.4f} {agg_b[m]:>{w_val}.4f} {sign}{delta:>{w_val - 1}.4f}")
    print(f"  {sep}")
    print()


def print_per_query_diff(pq_a, pq_b, run_id_a, run_id_b):
    by_qid_a = {pq["query_id"]: pq for pq in pq_a}
    by_qid_b = {pq["query_id"]: pq for pq in pq_b}
    all_qids = sorted(set(by_qid_a) | set(by_qid_b))

    sort_metric = "ndcg_at_10"
    rows = []
    for qid in all_qids:
        a_val = by_qid_a.get(qid, {}).get(sort_metric, 0.0)
        b_val = by_qid_b.get(qid, {}).get(sort_metric, 0.0)
        delta = b_val - a_val
        if delta < -0.0001:
            status = "REGRESSED"
        elif delta > 0.0001:
            status = "improved"
        else:
            status = "unchanged"
        rows.append((qid, a_val, b_val, delta, status))

    rows.sort(key=lambda r: r[3])

    print(f"  Per-query {METRIC_DISPLAY[sort_metric]} changes (regressions first):")
    print(f"  {'Query':<10} {run_id_a:>12} {run_id_b:>12} {'Delta':>10} {'Status':<12}")
    print("  " + "-" * 58)
    for qid, a_val, b_val, delta, status in rows:
        sign = "+" if delta >= 0 else ""
        print(f"  {qid:<10} {a_val:>12.4f} {b_val:>12.4f} {sign}{delta:>9.4f} {status:<12}")
    print()


def print_per_query_breakdown(per_query):
    if not per_query:
        print("  No per-query data.")
        return
    header = f"  {'Query':<10}" + "".join(f" {METRIC_DISPLAY[m]:>12}" for m in METRIC_NAMES)
    print(header)
    print("  " + "-" * (10 + 13 * len(METRIC_NAMES)))
    for pq in sorted(per_query, key=lambda x: x["query_id"]):
        row = f"  {pq['query_id']:<10}" + "".join(f" {pq[m]:>12.4f}" for m in METRIC_NAMES)
        print(row)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Evaluation harness for Turkish legal jurisprudence retrieval."
    )
    parser.add_argument("--run-file", type=Path, help="Path to run results JSON file")
    parser.add_argument("--run-id", action="append", default=[], help="Run ID(s). Two = compare mode.")
    parser.add_argument("--per-query", action="store_true", help="Show per-query breakdown")
    parser.add_argument("--gold-standard", type=Path, default=DEFAULT_GOLD, help="Path to gold standard JSON")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to SQLite results database")
    parser.add_argument("--config-label", type=str, default="", help="Configuration label for this run")
    args = parser.parse_args()

    # Mode 1: Compare two stored runs
    if len(args.run_id) == 2 and not args.run_file:
        conn = init_db(args.db)
        agg_a = load_run_aggregate(conn, args.run_id[0])
        agg_b = load_run_aggregate(conn, args.run_id[1])
        print_comparison_table(agg_a, agg_b, args.run_id[0], args.run_id[1])
        pq_a = load_run_per_query(conn, args.run_id[0])
        pq_b = load_run_per_query(conn, args.run_id[1])
        print_per_query_diff(pq_a, pq_b, args.run_id[0], args.run_id[1])
        conn.close()
        return

    # Mode 2: Show per-query breakdown for a stored run
    if len(args.run_id) == 1 and not args.run_file and args.per_query:
        conn = init_db(args.db)
        agg = load_run_aggregate(conn, args.run_id[0])
        print_metrics_table(agg, args.run_id[0])
        pq = load_run_per_query(conn, args.run_id[0])
        print_per_query_breakdown(pq)
        conn.close()
        return

    # Mode 3: Evaluate a run file
    if args.run_file:
        run_data = load_json(args.run_file)
        gold_data = load_json(args.gold_standard)

        run_id = args.run_id[0] if args.run_id else run_data.get("run_id", args.run_file.stem)
        config_label = args.config_label or run_data.get("config_label", "")

        aggregate, per_query = compute_run_metrics(run_data, gold_data)

        print_metrics_table(aggregate, run_id)
        if args.per_query:
            print_per_query_breakdown(per_query)

        conn = init_db(args.db)
        git_commit = run_data.get("git_commit", get_git_commit())
        log_run(conn, run_id, config_label, git_commit, aggregate, per_query)
        conn.close()
        print(f"  Logged as run_id='{run_id}' to {args.db}")
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
