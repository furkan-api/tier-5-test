#!/usr/bin/env python3
"""
Load pre-computed embeddings from S3 into Milvus AND PostgreSQL.

Single-source-of-truth ingestion: reads smoke-test-embedded JSONs from S3
and populates:
  - PostgreSQL: documents + chunks tables (from JSON metadata)
  - Milvus: chunk vectors (from JSON embeddings)

doc_id is computed fresh from metadata (court|daire|esas_no|karar_no)
to ensure consistency between PG and Milvus, regardless of what was
stored in the JSON's top-level doc_id field.

Usage:
    python -m app.ingestion.load_embedded [--recreate] [--workers 20] [--window-size 500]
"""

import argparse
import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config
from psycopg2.extras import Json
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

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    force=True
)
log = logging.getLogger(__name__)
# Force unbuffered output
import sys
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__

DEFAULT_WINDOW = 500
DEFAULT_WORKERS = 20
DEFAULT_MILVUS_BATCH = 2000

PG_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS documents (
        doc_id         TEXT PRIMARY KEY,
        filename       TEXT NOT NULL,
        esas_no        TEXT NOT NULL DEFAULT '',
        karar_no       TEXT NOT NULL DEFAULT '',
        court          TEXT NOT NULL DEFAULT '',
        daire          TEXT NOT NULL DEFAULT '',
        court_level    INTEGER NOT NULL DEFAULT 0,
        law_branch     TEXT NOT NULL DEFAULT '',
        decision_date  TEXT NOT NULL DEFAULT '',
        file_path      TEXT NOT NULL,
        topic_keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
        ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_documents_filename ON documents(filename)",
    """CREATE TABLE IF NOT EXISTS chunks (
        chunk_id    TEXT PRIMARY KEY,
        doc_id      TEXT NOT NULL REFERENCES documents(doc_id),
        chunk_index INTEGER NOT NULL,
        text        TEXT NOT NULL,
        token_count INTEGER NOT NULL,
        UNIQUE (doc_id, chunk_index)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id)",
]


def compute_doc_id(court: str, daire: str, esas_no: str, karar_no: str = "") -> str:
    key = f"{court}|{daire}|{esas_no}|{karar_no}".strip()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def compute_chunk_id(doc_id: str, chunk_index: int) -> str:
    key = f"{doc_id}|{chunk_index}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def init_pg_schema(conn) -> None:
    conn.autocommit = True
    with conn.cursor() as cur:
        for stmt in PG_SCHEMA:
            cur.execute(stmt)
    conn.autocommit = False


def create_milvus_collection(name: str, dimension: int) -> Collection:
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
    log.info("Created Milvus collection '%s' (%dd)", name, dimension)
    return collection


def list_s3_keys_batch(s3_client, bucket: str, prefix: str, batch_size: int = 1000):
    """Generator yielding lists of S3 keys in batches."""
    paginator = s3_client.get_paginator("list_objects_v2")
    batch = []
    total = 0
    for page in paginator.paginate(
        Bucket=bucket,
        Prefix=prefix.rstrip("/") + "/",
        PaginationConfig={"PageSize": batch_size}
    ):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                batch.append(obj["Key"])
                total += 1
                if len(batch) >= batch_size:
                    log.info("S3: yielding batch of %d (total %d so far)", len(batch), total)
                    yield batch
                    batch = []
    if batch:
        log.info("S3: yielding final batch of %d (total %d)", len(batch), total)
        yield batch
    log.info("S3 listing complete: %d total JSON files", total)


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
    for v in buf.values():
        v.clear()
    return n


def upsert_pg(conn, doc_data: list[dict], chunk_data: list[dict]) -> tuple[int, int]:
    if not doc_data:
        return 0, 0
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO documents
               (doc_id, filename, esas_no, karar_no, court, daire, court_level,
                law_branch, decision_date, file_path, topic_keywords)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (doc_id) DO UPDATE SET
                 filename=EXCLUDED.filename, esas_no=EXCLUDED.esas_no,
                 karar_no=EXCLUDED.karar_no, court=EXCLUDED.court,
                 daire=EXCLUDED.daire, court_level=EXCLUDED.court_level,
                 law_branch=EXCLUDED.law_branch, decision_date=EXCLUDED.decision_date,
                 file_path=EXCLUDED.file_path, topic_keywords=EXCLUDED.topic_keywords,
                 ingested_at=now()""",
            [
                (
                    d["doc_id"], d["filename"], d["esas_no"], d["karar_no"],
                    d["court"], d["daire"], d["court_level"], d["law_branch"],
                    d["decision_date"], d["file_path"], Json(d["topic_keywords"]),
                )
                for d in doc_data
            ],
        )
        cur.executemany(
            """INSERT INTO chunks (chunk_id, doc_id, chunk_index, text, token_count)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (chunk_id) DO UPDATE SET
                 text=EXCLUDED.text, token_count=EXCLUDED.token_count""",
            [
                (c["chunk_id"], c["doc_id"], c["chunk_index"], c["text"], c["token_count"])
                for c in chunk_data
            ],
        )
    conn.commit()
    return len(doc_data), len(chunk_data)


def process_window(s3_client, bucket: str, keys: list[str], workers: int) -> list:
    def fetch(key: str):
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=key)
            return json.loads(obj["Body"].read())
        except Exception as e:
            log.warning("Failed to read %s: %s", key, e)
            return None

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, key): key for key in keys}
        for future in as_completed(futures):
            results.append(future.result())
    return results


def main():
    parser = argparse.ArgumentParser(description="Load S3 embedded JSONs → Milvus + PostgreSQL")
    parser.add_argument("--recreate", action="store_true", help="Drop Milvus collection + clear PG tables")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW)
    parser.add_argument("--milvus-batch", type=int, default=DEFAULT_MILVUS_BATCH)
    args = parser.parse_args()

    settings = get_settings()

    # Retry Milvus connection up to 3 times (network can be flaky)
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

    collection_name = settings.collection_name
    dimension = settings.embedding_dimension

    if args.recreate and utility.has_collection(collection_name):
        utility.drop_collection(collection_name)
        log.info("Dropped Milvus collection '%s'", collection_name)

    if utility.has_collection(collection_name):
        col = Collection(collection_name)
        if col.num_entities > 0 and col.indexes:
            log.info("Milvus collection has %d vectors. Use --recreate to reload.", col.num_entities)
            col.load()
            return
        utility.drop_collection(collection_name)

    collection = create_milvus_collection(collection_name, dimension)

    with get_connection() as conn:
        log.info("Connected to PostgreSQL")
        init_pg_schema(conn)

        if args.recreate:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE chunks CASCADE")
                cur.execute("TRUNCATE documents CASCADE")
            conn.commit()
            log.info("Cleared PostgreSQL documents + chunks tables")

        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
            config=Config(max_pool_connections=max(50, args.workers * 2)),
        )

        log.info("Listing s3://%s/%s/ (streaming 1000-key batches)...", settings.s3_bucket_name, settings.s3_embedded_prefix)
        log.info("Creating S3 batch generator...")

        milvus_buf = {"chunk_ids": [], "doc_ids": [], "chunk_indices": [], "vectors": []}
        pg_docs: list[dict] = []
        pg_chunks: list[dict] = []

        total_files = 0
        total_vectors = 0
        total_pg_docs = 0
        total_pg_chunks = 0
        errors = 0
        t_start = time.time()

        # Process S3 keys in batches as they stream in (don't wait for full listing)
        log.info("Iterating batches...")
        for batch_keys in list_s3_keys_batch(s3, settings.s3_bucket_name, settings.s3_embedded_prefix):
            log.info("Got batch of %d keys", len(batch_keys))
            # Further subdivide batch into windows for memory efficiency
            for win_start in range(0, len(batch_keys), args.window_size):
                window_keys = batch_keys[win_start: win_start + args.window_size]
                file_data = process_window(s3, settings.s3_bucket_name, window_keys, args.workers)

                for data in file_data:
                    if data is None:
                        errors += 1
                        continue

                    meta = data.get("metadata", {})

                    # Recompute doc_id from metadata for consistency
                    doc_id = compute_doc_id(
                        meta.get("court", ""),
                        meta.get("daire", ""),
                        meta.get("esas_no", ""),
                        meta.get("karar_no", ""),
                    )
                    filename = meta.get("filename", "")

                    pg_docs.append({
                        "doc_id": doc_id,
                        "filename": filename,
                        "esas_no": meta.get("esas_no", ""),
                        "karar_no": meta.get("karar_no", ""),
                        "court": meta.get("court", ""),
                        "daire": meta.get("daire", ""),
                        "court_level": meta.get("court_level", 0),
                        "law_branch": meta.get("law_branch", ""),
                        "decision_date": meta.get("decision_date", ""),
                        "file_path": f"corpus/{filename}",
                        "topic_keywords": meta.get("topic_keywords", []),
                    })

                    for idx, chunk in enumerate(data.get("chunks", [])):
                        chunk_id = compute_chunk_id(doc_id, chunk["chunk_index"])
                        pg_chunks.append({
                            "chunk_id": chunk_id,
                            "doc_id": doc_id,
                            "chunk_index": chunk["chunk_index"],
                            "text": chunk["text"],
                            "token_count": chunk["token_count"],
                        })
                        milvus_buf["chunk_ids"].append(chunk_id)
                        milvus_buf["doc_ids"].append(doc_id)
                        milvus_buf["chunk_indices"].append(chunk["chunk_index"])
                        milvus_buf["vectors"].append(chunk["embedding"])

                        if len(milvus_buf["chunk_ids"]) >= args.milvus_batch:
                            total_vectors += flush_milvus(collection, milvus_buf)

                    total_files += 1

                    if len(pg_docs) >= 2000:
                        n_d, n_c = upsert_pg(conn, pg_docs, pg_chunks)
                        total_pg_docs += n_d
                        total_pg_chunks += n_c
                        pg_docs.clear()
                        pg_chunks.clear()

            elapsed = time.time() - t_start
            rate = total_files / elapsed if elapsed > 0 else 0
            log.info("Progress: %d files | %d vectors | %d docs | %.1f/sec",
                     total_files, total_vectors, total_pg_docs, rate)

        # Final flush
        total_vectors += flush_milvus(collection, milvus_buf)
        if pg_docs:
            n_d, n_c = upsert_pg(conn, pg_docs, pg_chunks)
            total_pg_docs += n_d
            total_pg_chunks += n_c

    collection.flush()
    collection.load()

    elapsed = time.time() - t_start
    print("\n" + "=" * 60)
    print("LOAD EMBEDDED SUMMARY")
    print("=" * 60)
    print(f"Files processed  : {total_files}")
    print(f"Milvus vectors   : {collection.num_entities}")
    print(f"PG documents     : {total_pg_docs}")
    print(f"PG chunks        : {total_pg_chunks}")
    print(f"Elapsed          : {elapsed:.1f}s")
    if errors:
        print(f"Errors           : {errors}")


if __name__ == "__main__":
    main()
