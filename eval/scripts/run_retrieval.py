#!/usr/bin/env python3
"""
Batch retrieval runner for evaluation.

Loads gold standard queries, runs them through the retrieval pipeline,
and produces a run file compatible with evaluate.py.

Usage:
    python eval/scripts/run_retrieval.py [--top-k 20] [--top-k-chunks 100]
                                         [--run-id embed-v1] [--output data/runs/embed-v1.json]
                                         [--aggregation max]
"""

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings
from app.core.vectordb import get_client
from app.retrieval.aggregation import STRATEGIES
from app.retrieval.dense import search_chunks
from app.retrieval.embeddings import embed_texts, get_embedding_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

GOLD_STANDARD_DEFAULT = PROJECT_ROOT / "eval" / "gold_standard.json"


def main():
    parser = argparse.ArgumentParser(description="Run retrieval and produce eval run file")
    parser.add_argument("--gold-standard", type=Path, default=GOLD_STANDARD_DEFAULT)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--top-k-chunks", type=int, default=100)
    parser.add_argument("--aggregation", choices=list(STRATEGIES.keys()), default="max")
    parser.add_argument("--run-id", default="embed-v1")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.output is None:
        args.output = PROJECT_ROOT / "data" / "runs" / f"{args.run_id}.json"

    settings = get_settings()
    if not settings.openai_api_key:
        log.error("OPENAI_API_KEY environment variable is required")
        sys.exit(1)

    client = get_client()
    stats = client.get_collection_stats(collection_name=settings.collection_name)
    log.info("Milvus collection '%s': %s vectors", settings.collection_name, stats.get("row_count"))

    with open(args.gold_standard, "r", encoding="utf-8") as f:
        gold = json.load(f)
    queries = gold["queries"]
    log.info("Loaded %d queries from %s", len(queries), args.gold_standard)

    aggregate_fn = STRATEGIES[args.aggregation]

    results = []
    empty_count = 0
    doc_counts = []

    for query in queries:
        chunk_results = search_chunks(client, query["query_text"], top_k_chunks=args.top_k_chunks)
        ranked_docs = aggregate_fn(chunk_results, top_k=args.top_k)
        doc_ids = [doc_id for doc_id, _ in ranked_docs]

        if not doc_ids:
            empty_count += 1
            log.warning("Empty results for query %s: %s", query["query_id"], query["query_text"][:60])

        doc_counts.append(len(doc_ids))
        results.append({"query_id": query["query_id"], "retrieved_docs": doc_ids})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    run_data = {
        "run_id": args.run_id,
        "config_label": f"{settings.embedding_model}, {settings.chunk_max_tokens}-token chunks, "
                        f"Milvus cosine, {args.aggregation} agg, top-{args.top_k_chunks}→top-{args.top_k}",
        "results": results,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(run_data, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("RETRIEVAL SUMMARY")
    print("=" * 60)
    print(f"\nQueries processed: {len(results)}")
    print(f"Empty results: {empty_count}")
    print(f"Avg documents per query: {sum(doc_counts) / len(doc_counts):.1f}")
    print(f"Min documents per query: {min(doc_counts)}")
    print(f"Run file written to: {args.output}")

    if empty_count > 0:
        print(f"\nWARNING: {empty_count} queries returned no results")
    else:
        print("\nAll queries returned results: OK")


if __name__ == "__main__":
    main()
