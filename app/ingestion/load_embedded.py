#!/usr/bin/env python3
"""
Load pre-computed embeddings from S3 directly into Milvus.

Bypasses the embed.py step by reading pre-embedded JSON files from the
smoke-test-embedded S3 prefix. Each JSON file contains chunks with
embeddings already computed by OpenAI text-embedding-3-small.

Processes S3 keys in windows of --window-size to avoid OOM (100k futures
all in memory at once would exhaust RAM on a 8GB instance).

Usage:
    python -m app.ingestion.load_embedded [--recreate] [--workers 20] [--window-size 500]
"""

import argparse
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)

from app.core.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# How many files to hold in-flight at once.
# Each JSON ≈ 200KB → window_size=500 ≈ 100MB peak per window.
DEFAULT_WINDOW = 500
DEFAULT_WORKERS = 20
DEFAULT_MILVUS_BATCH = 2000


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
    collection.create_index(
        field_name="vector",
        index_params={
            "metric_type": "COSINE",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        },
    )
    log.info("Created collection '%s' (%dd, cosine, IVF_FLAT)", name, dimension)
    return collection


def list_s3_keys(s3_client, bucket: str, prefix: str) -> list[str]:
    keys = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix.rstrip("/") + "/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])
    return keys


def flush_milvus(collection: Collection, buf: dict) -> int:
    if not buf["chunk_ids"]:
        return 0
    collection.insert([
        buf["chunk_ids"],
        buf["doc_ids"],
        buf["chunk_indices"],
        buf["vectors"],
    ])
    n = len(buf["chunk_ids"])
    buf["chunk_ids"].clear()
    buf["doc_ids"].clear()
    buf["chunk_indices"].clear()
    buf["vectors"].clear()
    return n


def process_window(s3_client, bucket: str, keys: list[str], workers: int):
    """Download and parse a window of S3 keys in parallel. Returns list of chunk lists."""

    def fetch(key: str):
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=key)
            data = json.loads(obj["Body"].read())
            return data.get("chunks", [])
        except Exception as e:
            log.warning("Failed to read %s: %s", key, e)
            return None

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, key): key for key in keys}
        for future in as_completed(futures):
            chunk_list = future.result()
            results.append(chunk_list)
    return results


def main():
    parser = argparse.ArgumentParser(description="Load S3 pre-embedded JSONs into Milvus")
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate collection")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Parallel S3 threads per window (default: {DEFAULT_WORKERS})")
    parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW,
                        help=f"Files per parallel window — controls peak RAM (default: {DEFAULT_WINDOW})")
    parser.add_argument("--milvus-batch", type=int, default=DEFAULT_MILVUS_BATCH,
                        help=f"Vectors per Milvus insert call (default: {DEFAULT_MILVUS_BATCH})")
    args = parser.parse_args()

    settings = get_settings()

    connections.connect(uri=settings.milvus_uri)
    log.info("Connected to Milvus: %s", settings.milvus_uri)

    collection_name = settings.collection_name
    dimension = settings.embedding_dimension

    if args.recreate and utility.has_collection(collection_name):
        utility.drop_collection(collection_name)
        log.info("Dropped collection '%s'", collection_name)

    if utility.has_collection(collection_name):
        col = Collection(collection_name)
        if col.num_entities > 0 and col.indexes:
            log.info("Collection '%s' already has %d vectors. Use --recreate to reload.",
                     collection_name, col.num_entities)
            col.load()
            return
        log.info("Collection exists but empty/no-index — dropping")
        utility.drop_collection(collection_name)

    collection = create_collection(collection_name, dimension)

    # Boto3 client with enlarged connection pool to match thread count
    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
        config=Config(max_pool_connections=args.workers + 5),
    )

    log.info("Listing s3://%s/%s/ ...", settings.s3_bucket_name, settings.s3_embedded_prefix)
    all_keys = list_s3_keys(s3, settings.s3_bucket_name, settings.s3_embedded_prefix)
    log.info("Found %d JSON files", len(all_keys))

    buf = {"chunk_ids": [], "doc_ids": [], "chunk_indices": [], "vectors": []}
    total_vectors = 0
    total_files = 0
    errors = 0
    t_start = time.time()

    for win_start in range(0, len(all_keys), args.window_size):
        window_keys = all_keys[win_start: win_start + args.window_size]

        chunk_lists = process_window(s3, settings.s3_bucket_name, window_keys, args.workers)

        for chunk_list in chunk_lists:
            if chunk_list is None:
                errors += 1
                continue
            for chunk in chunk_list:
                buf["chunk_ids"].append(chunk["chunk_id"])
                buf["doc_ids"].append(chunk["doc_id"])
                buf["chunk_indices"].append(chunk["chunk_index"])
                buf["vectors"].append(chunk["embedding"])

                if len(buf["chunk_ids"]) >= args.milvus_batch:
                    total_vectors += flush_milvus(collection, buf)

            total_files += 1

        elapsed = time.time() - t_start
        rate = total_files / elapsed if elapsed > 0 else 0
        eta = (len(all_keys) - total_files) / rate if rate > 0 else 0
        log.info("Progress: %d/%d files | %d vectors | %.1f files/sec | ETA %.0fs",
                 total_files, len(all_keys), total_vectors, rate, eta)

    # Flush remainder
    total_vectors += flush_milvus(collection, buf)

    collection.flush()
    collection.load()

    elapsed = time.time() - t_start
    log.info("Done: %d files, %d vectors in %.1fs (%.1f files/sec)",
             total_files, total_vectors, elapsed, total_files / elapsed if elapsed else 0)

    print("\n" + "=" * 60)
    print("LOAD EMBEDDED SUMMARY")
    print("=" * 60)
    print(f"Files processed : {total_files}")
    print(f"Vectors loaded  : {total_vectors}")
    print(f"Collection size : {collection.num_entities}")
    match = "OK" if collection.num_entities == total_vectors else "MISMATCH"
    print(f"Count check     : {match}")
    print(f"Elapsed         : {elapsed:.1f}s")
    if errors:
        print(f"Errors          : {errors}")


if __name__ == "__main__":
    main()
