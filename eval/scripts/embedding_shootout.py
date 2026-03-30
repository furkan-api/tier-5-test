#!/usr/bin/env python3
"""
Embedding model shootout (Epic 3.3).

Compares 4 embedding models on Recall@10, NDCG@10, and query latency (p50, p95).
Each model gets its own Milvus collection. Results logged to PG via evaluate.py.
All models run locally via HuggingFace Text Embeddings Inference (TEI).

Models:
  1. BAAI/bge-m3                        (1024d, multilingual incl. Turkish — current default)
  2. intfloat/multilingual-e5-large-instruct (1024d, MTEB #7, instruction-tuned)
  3. BAAI/bge-base-en-v1.5              (768d, English-only cross-lingual baseline)
  4. intfloat/e5-large-v2                (1024d, English-centric, high quality)

Usage:
    # Run a single model (TEI must be serving this model)
    uv run python eval/scripts/embedding_shootout.py --model bge-m3

    # Run all models sequentially (restarts TEI for each model via docker compose)
    uv run python eval/scripts/embedding_shootout.py --all

    # Turkish synonym similarity check
    uv run python eval/scripts/embedding_shootout.py --model bge-m3 --synonym-check

    # Compare all previously stored shootout runs
    uv run python eval/scripts/embedding_shootout.py --compare
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
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

# Import evaluation harness functions (same directory)
_eval_dir = str(Path(__file__).resolve().parent)
if _eval_dir not in sys.path:
    sys.path.insert(0, _eval_dir)
import evaluate as eval_harness

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

GOLD_STANDARD = PROJECT_ROOT / "eval" / "gold_standard.json"
CORPUS_MANIFEST = PROJECT_ROOT / "eval" / "corpus_manifest.json"


def load_doc_id_map() -> dict[str, str]:
    """Load corpus manifest and build hash_doc_id → filename_doc_id map.

    PG/Milvus uses hash-based doc_ids, gold standard uses filename-based IDs.
    """
    with open(CORPUS_MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    return {
        entry["doc_id"]: entry["filename"].removesuffix(".md")
        for entry in manifest
    }

# ── Model configurations ────────────────────────────────────────────────────

MODEL_CONFIGS = {
    "bge-m3": {
        "dimension": 1024,
        "tei_model_id": "BAAI/bge-m3",
        "short_name": "bgem3",
        "description": "BAAI/bge-m3 (1024d, multilingual incl. Turkish — current default)",
    },
    "multilingual-e5-large-instruct": {
        "dimension": 1024,
        "tei_model_id": "intfloat/multilingual-e5-large-instruct",
        "short_name": "me5li",
        "description": "intfloat/multilingual-e5-large-instruct (1024d, MTEB #7, instruction-tuned)",
    },
    "bge-base-en-v1.5": {
        "dimension": 768,
        "tei_model_id": "BAAI/bge-base-en-v1.5",
        "short_name": "bge15",
        "description": "BAAI/bge-base-en-v1.5 (768d, English-only cross-lingual baseline)",
    },
    "e5-large-v2": {
        "dimension": 1024,
        "tei_model_id": "intfloat/e5-large-v2",
        "short_name": "e5l2",
        "description": "intfloat/e5-large-v2 (1024d, English-centric, high quality)",
    },
}

# Turkish legal synonym/related pairs for embedding quality check.
# (term_a, term_b, is_related)
TURKISH_SYNONYM_PAIRS = [
    ("tazminat", "zarar giderimi", True),
    ("sanık", "fail", True),
    ("mahkumiyet", "ceza", True),
    ("temyiz", "üst mahkeme başvurusu", True),
    ("boşanma", "evlilik birliğinin sona ermesi", True),
    ("iş kazası", "meslek hastalığı", True),
    ("haksız fiil", "kusurlu davranış", True),
    ("icra takibi", "alacağın tahsili", True),
    # Negative pairs (should have lower similarity)
    ("tazminat", "ceza davası", False),
    ("boşanma", "vergi kaçakçılığı", False),
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def embed_batch(client: OpenAI, texts: list[str], model: str) -> list[list[float]]:
    response = client.embeddings.create(model=model, input=texts)
    return [item.embedding for item in response.data]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    a_arr, b_arr = np.array(a), np.array(b)
    return float(np.dot(a_arr, b_arr) / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr)))


def resolve_base_url(args_base_url: str | None) -> str:
    return args_base_url or get_settings().embedding_base_url


def restart_tei(model_id: str, compose_dir: Path, timeout: int = 180) -> None:
    """Restart the TEI docker service with a different model."""
    log.info("Restarting TEI with model: %s", model_id)
    # Stop TEI
    subprocess.run(
        ["docker", "compose", "stop", "tei"],
        cwd=compose_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["docker", "compose", "rm", "-f", "tei"],
        cwd=compose_dir, check=True, capture_output=True,
    )
    # Start with new model via env override
    env = os.environ.copy()
    env["TEI_MODEL_ID"] = model_id
    subprocess.run(
        ["docker", "compose", "up", "-d", "tei"],
        cwd=compose_dir, check=True, capture_output=True, env=env,
    )
    # Wait for health
    base_url = get_settings().embedding_base_url
    health_url = base_url.rstrip("/") + "/health"
    log.info("Waiting for TEI to be ready at %s ...", health_url)
    import urllib.request
    for i in range(timeout):
        try:
            req = urllib.request.urlopen(health_url, timeout=2)
            if req.status == 200:
                log.info("TEI ready after %ds", i)
                return
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError(f"TEI did not become ready after {timeout}s")


def get_current_tei_model() -> str | None:
    """Query TEI /info to get the currently loaded model."""
    base_url = get_settings().embedding_base_url
    try:
        import urllib.request, json as _json
        req = urllib.request.urlopen(base_url.rstrip("/") + "/info", timeout=5)
        data = _json.loads(req.read())
        return data.get("model_id")
    except Exception:
        return None


# ── Corpus indexing ──────────────────────────────────────────────────────────


def embed_corpus(
    client: OpenAI,
    model_name: str,
    config: dict,
    batch_size: int = 100,
    recreate: bool = False,
) -> Collection:
    """Embed all chunks from PG into a model-specific Milvus collection."""
    collection_name = f"shootout_{config['short_name']}"
    dimension = config["dimension"]

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM chunks")
        expected = cur.fetchone()[0]

    if recreate and utility.has_collection(collection_name):
        utility.drop_collection(collection_name)
        log.info("Dropped collection '%s'", collection_name)

    if utility.has_collection(collection_name):
        coll = Collection(collection_name)
        coll.load()
        if coll.num_entities == expected:
            log.info(
                "Collection '%s' already indexed (%d vectors). Skipping embed.",
                collection_name,
                expected,
            )
            return coll
        log.info(
            "Collection '%s' has %d vectors (expected %d). Recreating.",
            collection_name,
            coll.num_entities,
            expected,
        )
        utility.drop_collection(collection_name)

    # Create collection
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="chunk_index", dtype=DataType.INT64),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dimension),
    ]
    schema = CollectionSchema(fields=fields, description=f"Shootout: {model_name}")
    coll = Collection(name=collection_name, schema=schema)
    coll.create_index(
        field_name="vector",
        index_params={
            "metric_type": "COSINE",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        },
    )
    log.info("Created collection '%s' (%dd)", collection_name, dimension)

    with get_connection() as conn:
        cur = conn.cursor(name="chunk_reader")
        cur.itersize = batch_size
        cur.execute("SELECT chunk_id, doc_id, chunk_index, text FROM chunks ORDER BY chunk_id")

        embedded = 0
        t_start = time.time()
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            vectors = embed_batch(client, [r[3] for r in rows], model_name)
            coll.insert(
                [[r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], vectors]
            )
            embedded += len(rows)
            elapsed = time.time() - t_start
            rate = embedded / elapsed if elapsed > 0 else 0
            log.info("[%s] Embedded %d/%d (%.0f/sec)", config["short_name"], embedded, expected, rate)

    coll.flush()
    coll.load()
    log.info("Collection '%s' ready: %d vectors", collection_name, coll.num_entities)
    return coll


# ── Retrieval with latency measurement ───────────────────────────────────────


def run_retrieval_with_latency(
    client: OpenAI,
    model_name: str,
    collection: Collection,
    gold_queries: list[dict],
    doc_id_map: dict[str, str],
    top_k: int = 20,
    top_k_chunks: int = 100,
) -> tuple[list[dict], list[float]]:
    """Run retrieval for all queries. Returns (results, latencies_ms).

    doc_id_map translates hash-based PG doc_ids to filename-based gold standard IDs.
    """
    results = []
    latencies: list[float] = []

    for i, query in enumerate(gold_queries):
        t0 = time.time()

        query_vec = embed_batch(client, [query["query_text"]], model_name)[0]
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

        # Translate hash doc_ids to filename-based IDs used by gold standard
        translated = [doc_id_map.get(doc_id, doc_id) for doc_id, _ in ranked]

        latencies.append((time.time() - t0) * 1000)
        results.append({
            "query_id": query["query_id"],
            "retrieved_docs": translated,
        })

        if (i + 1) % 20 == 0:
            log.info("Queries: %d/%d", i + 1, len(gold_queries))

    return results, latencies


# ── Turkish synonym check ────────────────────────────────────────────────────


def turkish_synonym_check(client: OpenAI, model_name: str) -> None:
    """Embed Turkish legal synonym pairs and report cosine similarity."""
    all_terms = sorted({t for pair in TURKISH_SYNONYM_PAIRS for t in (pair[0], pair[1])})
    vectors = embed_batch(client, all_terms, model_name)
    term_to_vec = dict(zip(all_terms, vectors))

    print(f"\n{'=' * 70}")
    print(f"TURKISH SYNONYM CHECK: {model_name}")
    print(f"{'=' * 70}\n")
    print(f"  {'Term A':<28} {'Term B':<28} {'Cosine':>8} {'Type':>10}")
    print(f"  {'-' * 76}")

    pos_sims, neg_sims = [], []
    for t1, t2, is_related in TURKISH_SYNONYM_PAIRS:
        sim = cosine_similarity(term_to_vec[t1], term_to_vec[t2])
        label = "related" if is_related else "unrelated"
        (pos_sims if is_related else neg_sims).append(sim)
        print(f"  {t1:<28} {t2:<28} {sim:>8.4f} {label:>10}")

    avg_pos = sum(pos_sims) / len(pos_sims)
    avg_neg = sum(neg_sims) / len(neg_sims)
    separation = avg_pos - avg_neg
    verdict = "GOOD" if separation > 0.05 else "WEAK"

    print(f"\n  Avg related similarity:   {avg_pos:.4f}")
    print(f"  Avg unrelated similarity: {avg_neg:.4f}")
    print(f"  Separation:               {separation:.4f}  ({verdict})")


# ── Compare stored runs ──────────────────────────────────────────────────────


def compare_runs() -> None:
    """Load all shootout-* runs from PG and display comparison table."""
    import psycopg2

    settings = get_settings()
    db_url = os.environ.get("DATABASE_URL", settings.database_url)
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        "SELECT run_id, config_label, recall_at_5, recall_at_10, recall_at_20, "
        "ndcg_at_5, ndcg_at_10, mrr, hit_rate_at_5 "
        "FROM runs WHERE run_id LIKE 'shootout-%%' ORDER BY run_id"
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("No shootout runs found in database. Run models first.")
        return

    print(f"\n{'=' * 110}")
    print("EMBEDDING MODEL COMPARISON (Epic 3.3)")
    print(f"{'=' * 110}\n")

    header = (
        f"  {'Run ID':<20} {'Recall@5':>10} {'Recall@10':>10} {'Recall@20':>10} "
        f"{'NDCG@5':>10} {'NDCG@10':>10} {'MRR':>10} {'Hit@5':>10}"
    )
    print(header)
    print("  " + "-" * 100)
    for run_id, _, r5, r10, r20, n5, n10, mrr, h5 in rows:
        print(
            f"  {run_id:<20} {r5:>10.4f} {r10:>10.4f} {r20:>10.4f} "
            f"{n5:>10.4f} {n10:>10.4f} {mrr:>10.4f} {h5:>10.4f}"
        )

    best_r10 = max(rows, key=lambda r: r[3])
    best_n10 = max(rows, key=lambda r: r[6])
    print(f"\n  Best Recall@10: {best_r10[0]} ({best_r10[3]:.4f})")
    print(f"  Best NDCG@10:   {best_n10[0]} ({best_n10[6]:.4f})")

    # Latency data from run files
    runs_dir = PROJECT_ROOT / "data" / "runs"
    latency_found = False
    for run_id, *_ in rows:
        latency_file = runs_dir / f"{run_id}.latency.json"
        if latency_file.exists():
            if not latency_found:
                print(f"\n  {'Run ID':<20} {'p50 (ms)':>10} {'p95 (ms)':>10} {'avg (ms)':>10}")
                print("  " + "-" * 52)
                latency_found = True
            with open(latency_file) as f:
                lat = json.load(f)
            print(f"  {run_id:<20} {lat['p50']:>10.1f} {lat['p95']:>10.1f} {lat['avg']:>10.1f}")


# ── Main ─────────────────────────────────────────────────────────────────────


def run_single_model(
    model_name: str,
    config: dict,
    base_url: str,
    batch_size: int = 100,
    top_k: int = 20,
    top_k_chunks: int = 100,
    recreate: bool = False,
) -> dict:
    """Run the full shootout pipeline for one model. Returns aggregate metrics."""
    client = OpenAI(api_key="local", base_url=base_url)
    model_id = config["tei_model_id"]

    connect_milvus()

    # 1. Embed corpus
    collection = embed_corpus(
        client, model_id, config, batch_size=batch_size, recreate=recreate
    )

    # 2. Retrieval + latency
    gold_data = eval_harness.load_json(GOLD_STANDARD)
    doc_id_map = load_doc_id_map()
    log.info("Running retrieval on %d queries...", len(gold_data["queries"]))
    results, latencies = run_retrieval_with_latency(
        client, model_id, collection, gold_data["queries"], doc_id_map,
        top_k=top_k, top_k_chunks=top_k_chunks,
    )

    # 3. Metrics
    run_id = f"shootout-{config['short_name']}"
    config_label = (
        f"{model_id}, {config['dimension']}d, 512-token chunks, "
        f"max agg, top-{top_k_chunks}->top-{top_k}"
    )
    run_data = {"run_id": run_id, "config_label": config_label, "results": results}
    aggregate, per_query = eval_harness.compute_run_metrics(run_data, gold_data)

    # 4. Latency stats
    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)
    p50 = latencies_sorted[n // 2]
    p95 = latencies_sorted[int(n * 0.95)]
    avg_lat = sum(latencies) / n

    # 5. Save run + latency files
    runs_dir = PROJECT_ROOT / "data" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    run_file = runs_dir / f"{run_id}.json"
    with open(run_file, "w", encoding="utf-8") as f:
        json.dump(run_data, f, ensure_ascii=False, indent=2)

    latency_file = runs_dir / f"{run_id}.latency.json"
    with open(latency_file, "w", encoding="utf-8") as f:
        json.dump({"p50": p50, "p95": p95, "avg": avg_lat, "count": n}, f, indent=2)

    # 6. Log to PG
    settings = get_settings()
    db_url = os.environ.get("DATABASE_URL", settings.database_url)
    db_conn = eval_harness.init_db(db_url)
    try:
        eval_harness.log_run(
            db_conn, run_id, config_label,
            eval_harness.get_git_commit(), aggregate, per_query,
        )
        log.info("Logged run '%s' to PostgreSQL", run_id)
    except SystemExit:
        log.warning("Run '%s' already exists in PG (delete old or use different run-id)", run_id)
    db_conn.close()

    # 7. Print summary
    print(f"\n{'=' * 60}")
    print(f"RESULTS: {model_name} ({config['short_name']})")
    print(f"{'=' * 60}")
    eval_harness.print_metrics_table(aggregate, run_id)
    print(f"  Latency:  p50={p50:.0f}ms  p95={p95:.0f}ms  avg={avg_lat:.0f}ms")
    print(f"  Run file: {run_file}")

    return {**aggregate, "p50": p50, "p95": p95, "avg_latency": avg_lat}


def main():
    parser = argparse.ArgumentParser(description="Embedding model shootout (Epic 3.3)")
    parser.add_argument("--model", choices=list(MODEL_CONFIGS.keys()), help="Model to test")
    parser.add_argument("--all", action="store_true", help="Run all models (restarts TEI for each)")
    parser.add_argument("--base-url", help="Override embedding API base URL")
    parser.add_argument("--batch-size", type=int, default=100, help="Embedding batch size")
    parser.add_argument("--top-k", type=int, default=20, help="Top-K documents")
    parser.add_argument("--top-k-chunks", type=int, default=100, help="Top-K chunks from vector search")
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate Milvus collection")
    parser.add_argument("--synonym-check", action="store_true", help="Run Turkish synonym check only")
    parser.add_argument("--compare", action="store_true", help="Compare all stored shootout runs")
    args = parser.parse_args()

    if args.compare:
        compare_runs()
        return

    base_url = resolve_base_url(args.base_url)

    if args.synonym_check:
        if not args.model:
            parser.error("--model required with --synonym-check")
        config = MODEL_CONFIGS[args.model]
        client = OpenAI(api_key="local", base_url=base_url)
        turkish_synonym_check(client, config["tei_model_id"])
        return

    if args.all:
        models_to_run = list(MODEL_CONFIGS.items())
    elif args.model:
        models_to_run = [(args.model, MODEL_CONFIGS[args.model])]
    else:
        parser.error("--model or --all is required (or use --compare)")

    for model_name, config in models_to_run:
        # Check if TEI is already serving this model
        current = get_current_tei_model()
        needed = config["tei_model_id"]
        if current != needed:
            restart_tei(needed, PROJECT_ROOT)

        run_single_model(
            model_name, config, base_url,
            batch_size=args.batch_size, top_k=args.top_k,
            top_k_chunks=args.top_k_chunks, recreate=args.recreate,
        )

    if args.all:
        print("\n\nAll models complete. Final comparison:\n")
        compare_runs()


if __name__ == "__main__":
    main()
