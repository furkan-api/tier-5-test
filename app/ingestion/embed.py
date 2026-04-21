#!/usr/bin/env python3
"""
Embedding pipeline: chunks table (PostgreSQL) → Milvus vector index.

Usage:
    python -m app.ingestion.embed [--recreate]
"""

import argparse
import logging
import sys
import time

from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)

from app.core.config import get_settings
from app.core.db import get_connection
from app.retrieval.embeddings import embed_texts, get_embedding_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def _fetch_existing_chunk_ids(collection: Collection) -> set[str]:
    ids: set[str] = set()
    iterator = collection.query_iterator(
        expr="",
        output_fields=["chunk_id"],
        batch_size=10000,
    )
    try:
        while True:
            batch = iterator.next()
            if not batch:
                break
            for item in batch:
                ids.add(item["chunk_id"])
    finally:
        iterator.close()
    return ids


def create_collection(name: str, dimension: int) -> Collection:
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="chunk_index", dtype=DataType.INT64),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dimension),
    ]
    schema = CollectionSchema(fields=fields, description="Document chunk embeddings")
    collection = Collection(name=name, schema=schema)

    index_params = {
        "metric_type": "COSINE",
        "index_type": "IVF_FLAT",
        "params": {"nlist": 128},
    }
    collection.create_index(field_name="vector", index_params=index_params)
    log.info("Created collection '%s' (%dd, cosine, IVF_FLAT)", name, dimension)
    return collection


def main():
    parser = argparse.ArgumentParser(description="Embed chunks and index in Milvus")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate the collection")
    args = parser.parse_args()

    settings = get_settings()
    embedding_client = get_embedding_client()

    for attempt in range(1, 4):
        try:
            connections.connect(uri=settings.milvus_uri, timeout=30)
            log.info("Connected to Milvus: %s", settings.milvus_uri)
            break
        except Exception as e:
            log.warning("Milvus connect attempt %d failed: %s", attempt, e)
            if attempt < 3:
                time.sleep(5)
            else:
                raise

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM chunks")
        total_chunks = cur.fetchone()[0]
        log.info("Total chunks in PostgreSQL: %d", total_chunks)

        if total_chunks == 0:
            log.error("No chunks found. Run python -m app.ingestion.chunk first.")
            sys.exit(1)

        collection_name = settings.collection_name
        dimension = settings.embedding_dimension

        if args.recreate and utility.has_collection(collection_name):
            utility.drop_collection(collection_name)
            log.info("Dropped existing collection '%s'", collection_name)

        existing_chunk_ids: set[str] = set()
        if utility.has_collection(collection_name):
            collection = Collection(collection_name)
            collection.load()
            collection.flush()
            existing = collection.num_entities
            if existing == total_chunks:
                log.info("Collection already has %d vectors (matches chunk count). Nothing to do.", existing)
                print_verification(collection, embedding_client, settings, total_chunks)
                return
            if existing > total_chunks:
                log.warning("Collection has %d vectors, more than %d chunks. Dropping and recreating.", existing, total_chunks)
                utility.drop_collection(collection_name)
                collection = create_collection(collection_name, dimension)
            else:
                log.info("Resuming: collection has %d/%d vectors. Loading existing chunk_ids...", existing, total_chunks)
                existing_chunk_ids = _fetch_existing_chunk_ids(collection)
                log.info("Loaded %d existing chunk_ids; will skip these.", len(existing_chunk_ids))
        else:
            collection = create_collection(collection_name, dimension)

        cur.close()
        cur = conn.cursor(name="chunk_reader")
        cur.itersize = args.batch_size
        cur.execute("SELECT chunk_id, doc_id, chunk_index, text FROM chunks ORDER BY chunk_id")

        embedded = 0
        skipped = 0
        t_start = time.time()

        while True:
            rows = cur.fetchmany(args.batch_size)
            if not rows:
                break

            if existing_chunk_ids:
                new_rows = [r for r in rows if r[0] not in existing_chunk_ids]
                skipped += len(rows) - len(new_rows)
                rows = new_rows
                if not rows:
                    continue

            chunk_ids = [r[0] for r in rows]
            doc_ids = [r[1] for r in rows]
            chunk_indices = [r[2] for r in rows]
            texts = [r[3] for r in rows]

            vectors = embed_texts(embedding_client, texts, model=settings.embedding_model)
            collection.insert([chunk_ids, doc_ids, chunk_indices, vectors])

            embedded += len(rows)
            elapsed = time.time() - t_start
            rate = embedded / elapsed if elapsed > 0 else 0
            log.info(
                "Embedded %d (skipped %d) / %d chunks (%.0f chunks/sec)",
                embedded, skipped, total_chunks, rate,
            )

        collection.flush()
        collection.load()

    print_verification(collection, embedding_client, settings, total_chunks)


def print_verification(collection, embedding_client, settings, expected_count):
    print("\n" + "=" * 60)
    print("EMBEDDING SUMMARY")
    print("=" * 60)

    actual_count = collection.num_entities
    match = "OK" if actual_count == expected_count else "MISMATCH"
    print(f"\nMilvus vectors: {actual_count}")
    print(f"PostgreSQL chunks: {expected_count}")
    print(f"Count check: {match}")

    print("\n" + "-" * 60)
    print("SAMPLE SEARCH: 'iş kazası nedeniyle tazminat'")
    print("-" * 60)

    query_vec = embed_texts(embedding_client, ["iş kazası nedeniyle tazminat"], model=settings.embedding_model)[0]
    results = collection.search(
        data=[query_vec],
        anns_field="vector",
        param={"metric_type": "COSINE", "params": {"nprobe": 16}},
        limit=10,
        output_fields=["chunk_id", "doc_id"],
    )

    doc_ids_seen = set()
    for hit in results[0]:
        doc_id = hit.entity.get("doc_id")
        chunk_id = hit.entity.get("chunk_id")
        doc_ids_seen.add(doc_id)
        print(f"  score={hit.score:.4f}  doc_id={doc_id}  chunk_id={chunk_id}")

    print(f"\nUnique documents in top 10: {len(doc_ids_seen)}")
    if len(doc_ids_seen) >= 2:
        print("Multi-document check: OK")
    else:
        print("WARNING: All results from a single document")


if __name__ == "__main__":
    main()
