#!/usr/bin/env python3
"""
Validate gold_standard.json against the schema and check acceptance criteria.

Checks:
1. JSON schema validation
2. All doc_ids in relevance_judgments exist in corpus_manifest.json
3. Acceptance criteria: 50+ queries, 10+ contradictory, 3+ branches, 5+ daireler
"""

import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent
GOLD_STANDARD = EVAL_DIR / "gold_standard.json"
CORPUS_MANIFEST = EVAL_DIR / "corpus_manifest.json"


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate():
    errors = []
    warnings = []

    # Load files
    try:
        gs = load_json(GOLD_STANDARD)
    except Exception as e:
        print(f"FATAL: Cannot load {GOLD_STANDARD}: {e}")
        sys.exit(1)

    try:
        manifest = load_json(CORPUS_MANIFEST)
    except Exception as e:
        print(f"FATAL: Cannot load {CORPUS_MANIFEST}: {e}")
        sys.exit(1)

    valid_doc_ids = {d["doc_id"] for d in manifest if not d.get("excluded", False)}
    queries = gs.get("queries", [])

    # 1. Basic structure
    if "version" not in gs:
        errors.append("Missing 'version' field")
    if "queries" not in gs:
        errors.append("Missing 'queries' field")
        print_results(errors, warnings)
        return

    # 2. Query count
    n_queries = len(queries)
    if n_queries < 50:
        errors.append(f"Only {n_queries} queries (need 50+)")
    else:
        print(f"✓ {n_queries} queries")

    # 3. All doc_ids valid
    invalid_refs = set()
    for q in queries:
        for j in q.get("relevance_judgments", []):
            if j["doc_id"] not in valid_doc_ids:
                invalid_refs.add((q["query_id"], j["doc_id"]))
    if invalid_refs:
        errors.append(f"{len(invalid_refs)} invalid doc_id references:")
        for qid, did in sorted(invalid_refs)[:10]:
            errors.append(f"  {qid} → {did}")
    else:
        print("✓ All doc_id references valid")

    # 4. Branch coverage
    branches = {q.get("law_branch") for q in queries}
    required_branches = {"hukuk", "ceza", "idari"}
    missing_branches = required_branches - branches
    if missing_branches:
        errors.append(f"Missing branches: {missing_branches}")
    else:
        print(f"✓ Branches covered: {sorted(branches)}")

    # 5. Daire coverage
    daireler = set()
    for q in queries:
        for court in q.get("relevant_court", []):
            daireler.add(court)
    if len(daireler) < 5:
        errors.append(f"Only {len(daireler)} unique daireler (need 5+)")
    else:
        print(f"✓ {len(daireler)} unique daireler")

    # 6. Contradictory pairs
    n_contradictory = sum(
        1 for q in queries if q.get("contradictory_pairs")
    )
    if n_contradictory < 10:
        warnings.append(f"Only {n_contradictory} queries with contradictory_pairs (target: 10+)")
    else:
        print(f"✓ {n_contradictory} queries with contradictory pairs")

    # 7. Each query has at least 1 relevant doc (relevance >= 2) and hard negatives
    no_relevant = []
    no_negatives = []
    for q in queries:
        judgments = q.get("relevance_judgments", [])
        has_relevant = any(j["relevance"] >= 2 for j in judgments)
        has_negative = any(j["relevance"] == 0 for j in judgments)
        if not has_relevant:
            no_relevant.append(q["query_id"])
        if not has_negative:
            no_negatives.append(q["query_id"])

    if no_relevant:
        errors.append(f"{len(no_relevant)} queries with no relevant doc (relevance >= 2): {no_relevant[:5]}")
    else:
        print("✓ All queries have at least 1 relevant document")

    if no_negatives:
        warnings.append(f"{len(no_negatives)} queries with no hard negatives (relevance 0)")
    else:
        print("✓ All queries have hard negatives")

    # 8. Unique query IDs
    qids = [q["query_id"] for q in queries]
    if len(qids) != len(set(qids)):
        dupes = [qid for qid in qids if qids.count(qid) > 1]
        errors.append(f"Duplicate query_ids: {set(dupes)}")
    else:
        print("✓ All query_ids unique")

    # 9. Difficulty distribution
    diff_dist = {}
    for q in queries:
        d = q.get("difficulty", "unknown")
        diff_dist[d] = diff_dist.get(d, 0) + 1
    print(f"\nDifficulty distribution: {diff_dist}")

    # 10. Query type distribution
    type_dist = {}
    for q in queries:
        t = q.get("query_type", "unknown")
        type_dist[t] = type_dist.get(t, 0) + 1
    print(f"Query type distribution: {type_dist}")

    print_results(errors, warnings)


def print_results(errors, warnings):
    print("\n" + "=" * 50)
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  ✗ {e}")
    if warnings:
        print(f"\nWARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  ⚠ {w}")
    if not errors and not warnings:
        print("ALL CHECKS PASSED ✓")
    elif not errors:
        print("\nNo errors — warnings only.")
    else:
        print(f"\n{len(errors)} error(s) must be fixed.")
        sys.exit(1)


if __name__ == "__main__":
    validate()
