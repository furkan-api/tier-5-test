#!/usr/bin/env python3
"""
Chunk size comparison (Epic 3.3).

Compares 3 chunk sizes (256, 512, 1024 tokens) using the configured embedding
model. Each size gets its own in-memory chunking pass and Milvus collection
(the PG chunks table is NOT modified).

Usage:
    # Run a single chunk size
    python eval/scripts/chunk_size_comparison.py --chunk-size 256

    # Override model (e.g. after embedding shootout picks a winner)
    python eval/scripts/chunk_size_comparison.py --chunk-size 1024 \\
        --model text-embedding-3-large --dimension 3072 \\
        --base-url https://api.openai.com/v1

    # Compare all stored chunk-size runs
    python eval/scripts/chunk_size_comparison.py --compare
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

import tiktoken
from openai import OpenAI
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    utility,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings
from app.core.db import get_connection
from app.core.vectordb import connect_milvus
from app.retrieval.aggregation import max_score

_eval_dir = str(Path(__file__).resolve().parent)
if _eval_dir not in sys.path:
    sys.path.insert(0, _eval_dir)
import evaluate as eval_harness

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

ENCODING = tiktoken.get_encoding("cl100k_base")
GOLD_STANDARD = PROJECT_ROOT / "eval" / "gold_standard.json"
CORPUS_MANIFEST = PROJECT_ROOT / "eval" / "corpus_manifest.json"


def load_doc_id_map() -> dict[str, str]:
    """Load corpus manifest and build hash_doc_id → filename_doc_id map."""
    with open(CORPUS_MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    return {
        entry["doc_id"]: entry["filename"].removesuffix(".md")
        for entry in manifest
    }
CHUNK_SIZES = [256, 512, 1024]
DEFAULT_OVERLAP = 50


# ── Chunking (in-memory, does NOT touch PG chunks table) ────────────────────


def chunk_text(text: str, max_tokens: int, overlap: int) -> list[tuple[str, int]]:
    """Split text into overlapping token windows. Returns [(text, token_count)]."""
    tokens = ENCODING.encode(text)
    if not tokens:
        return []
    step = max_tokens - overlap
    chunks = []
    for start in range(0, len(tokens), step):
        window = tokens[start : start + max_tokens]
        chunks.append((ENCODING.decode(window), len(window)))
        if start + max_tokens >= len(tokens):
            break
    return chunks


def chunk_corpus(
    corpus_dir: Path, max_tokens: int, overlap: int
) -> list[tuple[str, str, int, str]]:
    """Read documents from PG, chunk in memory.

    Returns list of (chunk_id, doc_id, chunk_index, text).
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT doc_id, file_path FROM documents ORDER BY doc_id")
        documents = cur.fetchall()

    all_chunks: list[tuple[str, str, int, str]] = []
    for doc_id, file_path in documents:
        full_path = corpus_dir.parent / file_path
        if not full_path.exists():
            log.warning("File not found: %s (doc_id=%s)", full_path, doc_id)
            continue

        text = full_path.read_text(encoding="utf-8")
        for idx, (chunk_txt, _) in enumerate(chunk_text(text, max_tokens, overlap)):
            chunk_id = hashlib.sha256(f"{doc_id}|{idx}".encode()).hexdigest()[:16]
            all_chunks.append((chunk_id, doc_id, idx, chunk_txt))

    return all_chunks


# ── Embedding & indexing ─────────────────────────────────────────────────────


def embed_and_index(
    client: OpenAI,
    model_name: str,
    chunks: list[tuple[str, str, int, str]],
    collection_name: str,
    dimension: int,
    batch_size: int = 100,
    recreate: bool = False,
) -> Collection:
    """Embed chunks and insert into a dedicated Milvus collection."""
    if recreate and utility.has_collection(collection_name):
        utility.drop_collection(collection_name)

    if utility.has_collection(collection_name):
        coll = Collection(collection_name)
        coll.load()
        if coll.num_entities == len(chunks):
            log.info("Collection '%s' already has %d vectors. Skipping.", collection_name, len(chunks))
            return coll
        log.info(
            "Collection '%s' count mismatch (%d vs %d). Recreating.",
            collection_name,
            coll.num_entities,
            len(chunks),
        )
        utility.drop_collection(collection_name)

    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="chunk_index", dtype=DataType.INT64),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dimension),
    ]
    schema = CollectionSchema(fields=fields, description=f"Chunk size experiment: {collection_name}")
    coll = Collection(name=collection_name, schema=schema)
    coll.create_index(
        field_name="vector",
        index_params={
            "metric_type": "COSINE",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        },
    )
    log.info("Created collection '%s' (%dd, %d chunks)", collection_name, dimension, len(chunks))

    t_start = time.time()
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c[3] for c in batch]
        resp = client.embeddings.create(model=model_name, input=texts)
        vecs = [item.embedding for item in resp.data]
        coll.insert(
            [[c[0] for c in batch], [c[1] for c in batch], [c[2] for c in batch], vecs]
        )
        done = min(i + batch_size, len(chunks))
        elapsed = time.time() - t_start
        rate = done / elapsed if elapsed > 0 else 0
        log.info("[%s] Embedded %d/%d (%.0f/sec)", collection_name, done, len(chunks), rate)

    coll.flush()
    coll.load()
    return coll


# ── Retrieval ────────────────────────────────────────────────────────────────


def run_retrieval(
    client: OpenAI,
    model_name: str,
    collection: Collection,
    gold_queries: list[dict],
    doc_id_map: dict[str, str],
    top_k: int = 20,
    top_k_chunks: int = 100,
) -> list[dict]:
    """Run retrieval for all gold-standard queries."""
    results = []
    for i, query in enumerate(gold_queries):
        resp = client.embeddings.create(model=model_name, input=[query["query_text"]])
        query_vec = resp.data[0].embedding

        hits = collection.search(
            data=[query_vec],
            anns_field="vector",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=top_k_chunks,
            output_fields=["chunk_id", "doc_id"],
        )
        chunk_results = [
            {"chunk_id": h.entity.get("chunk_id"), "doc_id": h.entity.get("doc_id"), "score": h.score}
            for h in hits[0]
        ]
        ranked = max_score(chunk_results, top_k=top_k)
        translated = [doc_id_map.get(doc_id, doc_id) for doc_id, _ in ranked]
        results.append({
            "query_id": query["query_id"],
            "retrieved_docs": translated,
        })
        if (i + 1) % 20 == 0:
            log.info("Queries: %d/%d", i + 1, len(gold_queries))

    return results


# ── Compare stored runs ──────────────────────────────────────────────────────


def compare_runs() -> None:
    """Load all chunksize-* runs from PG and display comparison table."""
    import psycopg2

    settings = get_settings()
    db_url = os.environ.get("DATABASE_URL", settings.database_url)
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        "SELECT run_id, config_label, recall_at_5, recall_at_10, recall_at_20, "
        "ndcg_at_5, ndcg_at_10, mrr, hit_rate_at_5 "
        "FROM runs WHERE run_id LIKE 'chunksize-%%' ORDER BY run_id"
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("No chunk-size runs found in database. Run experiments first.")
        return

    print(f"\n{'=' * 110}")
    print("CHUNK SIZE COMPARISON (Epic 3.3)")
    print(f"{'=' * 110}\n")

    header = (
        f"  {'Run ID':<20} {'Chunks':>8} {'Recall@5':>10} {'Recall@10':>10} "
        f"{'Recall@20':>10} {'NDCG@5':>10} {'NDCG@10':>10} {'MRR':>10} {'Hit@5':>10}"
    )
    print(header)
    print("  " + "-" * 108)

    for run_id, config_label, r5, r10, r20, n5, n10, mrr, h5 in rows:
        # Extract chunk count from run file if available
        run_file = PROJECT_ROOT / "data" / "runs" / f"{run_id}.json"
        chunk_count = "-"
        if run_file.exists():
            with open(run_file) as f:
                meta = json.load(f)
            chunk_count = str(meta.get("chunk_count", "-"))
        print(
            f"  {run_id:<20} {chunk_count:>8} {r5:>10.4f} {r10:>10.4f} "
            f"{r20:>10.4f} {n5:>10.4f} {n10:>10.4f} {mrr:>10.4f} {h5:>10.4f}"
        )

    best_r10 = max(rows, key=lambda r: r[3])
    best_n10 = max(rows, key=lambda r: r[6])
    print(f"\n  Best Recall@10: {best_r10[0]} ({best_r10[3]:.4f})")
    print(f"  Best NDCG@10:   {best_n10[0]} ({best_n10[6]:.4f})")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Chunk size comparison (Epic 3.3)")
    parser.add_argument("--chunk-size", type=int, choices=CHUNK_SIZES, help="Chunk size to test")
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP, help="Token overlap (default: 50)")
    parser.add_argument("--model", help="Override embedding model (default: from settings)")
    parser.add_argument("--dimension", type=int, help="Override embedding dimension")
    parser.add_argument("--base-url", help="Override embedding API base URL")
    parser.add_argument("--batch-size", type=int, default=100, help="Embedding batch size")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--top-k-chunks", type=int, default=100)
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate collection")
    parser.add_argument("--compare", action="store_true", help="Compare all stored chunk-size runs")
    args = parser.parse_args()

    if args.compare:
        compare_runs()
        return

    if not args.chunk_size:
        parser.error("--chunk-size required (256, 512, or 1024) — or use --compare")

    settings = get_settings()
    model_name = args.model or settings.embedding_model
    dimension = args.dimension or settings.embedding_dimension
    base_url = args.base_url or settings.embedding_base_url
    api_key = os.environ.get("OPENAI_API_KEY", settings.openai_api_key or "local")

    client = OpenAI(api_key=api_key, base_url=base_url)
    connect_milvus()

    # 1. Chunk corpus in memory
    log.info("Chunking corpus: %d tokens, %d overlap", args.chunk_size, args.overlap)
    chunks = chunk_corpus(settings.corpus_dir, args.chunk_size, args.overlap)
    log.info("Produced %d chunks from corpus", len(chunks))

    # 2. Embed and index
    collection_name = f"chunksize_{args.chunk_size}"
    collection = embed_and_index(
        client, model_name, chunks, collection_name, dimension,
        batch_size=args.batch_size, recreate=args.recreate,
    )

    # 3. Retrieval
    gold_data = eval_harness.load_json(GOLD_STANDARD)
    doc_id_map = load_doc_id_map()
    log.info("Running retrieval on %d queries...", len(gold_data["queries"]))
    results = run_retrieval(
        client, model_name, collection, gold_data["queries"], doc_id_map,
        top_k=args.top_k, top_k_chunks=args.top_k_chunks,
    )

    # 4. Metrics
    run_id = f"chunksize-{args.chunk_size}"
    config_label = (
        f"{model_name}, {args.chunk_size}-token chunks ({args.overlap} overlap), "
        f"max agg, top-{args.top_k_chunks}->top-{args.top_k}"
    )
    run_data = {
        "run_id": run_id,
        "config_label": config_label,
        "chunk_count": len(chunks),
        "results": results,
    }
    aggregate, per_query = eval_harness.compute_run_metrics(run_data, gold_data)

    # 5. Save run file
    runs_dir = PROJECT_ROOT / "data" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_file = runs_dir / f"{run_id}.json"
    with open(run_file, "w", encoding="utf-8") as f:
        json.dump(run_data, f, ensure_ascii=False, indent=2)

    # 6. Log to PG
    db_url = os.environ.get("DATABASE_URL", settings.database_url)
    db_conn = eval_harness.init_db(db_url)
    try:
        eval_harness.log_run(
            db_conn, run_id, config_label,
            eval_harness.get_git_commit(), aggregate, per_query,
        )
        log.info("Logged run '%s' to PostgreSQL", run_id)
    except SystemExit:
        log.warning("Run '%s' already exists in PG", run_id)
    db_conn.close()

    # 7. Print summary
    print(f"\n{'=' * 60}")
    print(f"RESULTS: {args.chunk_size}-token chunks")
    print(f"{'=' * 60}")
    print(f"  Chunks: {len(chunks)}")
    eval_harness.print_metrics_table(aggregate, run_id)
    print(f"  Run file: {run_file}")


if __name__ == "__main__":
    main()
