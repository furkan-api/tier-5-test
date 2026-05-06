#!/usr/bin/env python3
"""
Document ingestion pipeline: filenames → MongoDB metadata → PostgreSQL.
Metadata is fetched from MongoDB (tr-ictihat-v2) by filename.

Modes:
  --corpus-dir ./corpus   Read filenames from local directory (default)
  --from-s3               Read filenames from S3 prefix (no download)

Usage:
    python -m app.ingestion.ingest [--corpus-dir ./corpus]
    python -m app.ingestion.ingest --from-s3
"""

import argparse
import logging
import sys
import unicodedata
import uuid
from pathlib import Path

import boto3
from psycopg2.extras import Json
from pymongo import MongoClient

from app.core.config import get_settings
from app.core.db import get_connection

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "eval" / "scripts"))
from build_corpus_manifest import infer_law_branch, infer_court_level  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

SCHEMA_STATEMENTS = [
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
    """CREATE TABLE IF NOT EXISTS excluded_documents (
        filename       TEXT PRIMARY KEY,
        exclude_reason TEXT NOT NULL,
        ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS ingest_log (
        id          SERIAL PRIMARY KEY,
        run_id      TEXT NOT NULL,
        timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
        total_files INTEGER NOT NULL,
        ingested    INTEGER NOT NULL,
        skipped     INTEGER NOT NULL,
        errors      INTEGER NOT NULL,
        corpus_dir  TEXT NOT NULL
    )""",
]

_MONGO_COURT_MAP = {
    "yargitay": "Yargıtay",
    "danistay": "Danıştay",
    "aym": "AYM",
    "anayasa_mahkemesi": "AYM",
    "bam": "BAM",
    "bim": "BİM",
    "ticaret": "İlk Derece",
    "is": "İlk Derece",
    "idare": "İlk Derece",
    "sayistay": "Sayıştay",
    "uyusmazlik": "Uyuşmazlık",
    "tuketici": "İlk Derece",
    "icra_hukuk": "İlk Derece",
    "icra_ceza": "İlk Derece",
    "aihm": "AİHM",
    "fikri_sinai_hukuk": "İlk Derece",
    "fikri_sinai_ceza": "İlk Derece",
    "asliye_ceza": "İlk Derece",
    "agir_ceza": "İlk Derece"
}


_MONGO_PROJECTION = {
    "filename": 1, "document_id": 1, "court": 1, "court_name": 1,
    "case_no": 1, "decision_no": 1, "decision_date": 1, "keywords": 1, "_id": 0,
}


def get_mongo_collection(settings):
    client = MongoClient(settings.mongo_url, serverSelectionTimeoutMS=8000)
    return client["data-team"]["tr-ictihat-v2"]


def prefetch_mongo_docs(mongo_col, corpus_filenames: list[str]) -> dict[str, dict]:
    """
    Fetch only the MongoDB documents whose filenames match the corpus.

    Sends one $in query per batch of filenames so we never transfer documents
    that have no corresponding corpus file.
    """
    _BATCH = 1000
    normalized = [unicodedata.normalize("NFC", f) for f in corpus_filenames]
    index: dict[str, dict] = {}
    for i in range(0, len(normalized), _BATCH):
        batch = normalized[i: i + _BATCH]
        for doc in mongo_col.find({"filename": {"$in": batch}}, _MONGO_PROJECTION):
            fname = unicodedata.normalize("NFC", doc.get("filename") or "")
            if fname:
                index[fname] = doc
    return index


def fetch_from_mongo(filename: str, mongo_index: dict[str, dict]) -> dict | None:
    return mongo_index.get(unicodedata.normalize("NFC", filename))


def list_s3_filenames(settings) -> list[str]:
    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
    )
    prefix = settings.s3_prefix.rstrip("/") + "/"
    log.info("Listing s3://%s/%s ...", settings.s3_bucket_name, prefix)
    paginator = s3.get_paginator("list_objects_v2")
    filenames = []
    for page in paginator.paginate(Bucket=settings.s3_bucket_name, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".md"):
                filenames.append(Path(key).name)
    log.info("Found %d .md files in S3", len(filenames))
    return sorted(filenames)


def build_doc(filename: str, mongo_doc: dict) -> dict:  # noqa: E302
    court_raw = (mongo_doc.get("court") or "").lower()
    court = _MONGO_COURT_MAP.get(court_raw, court_raw)
    daire = mongo_doc.get("court_name") or ""

    doc = {
        "doc_id": mongo_doc.get("document_id") or Path(filename).stem,
        "filename": filename,
        "court": court,
        "daire": daire,
        "esas_no": mongo_doc.get("case_no") or "",
        "karar_no": mongo_doc.get("decision_no") or "",
        "decision_date": str(mongo_doc.get("decision_date") or ""),
        "topic_keywords": mongo_doc.get("keywords") or [],
        "law_branch": "",
        "court_level": 0,
    }

    if court:
        doc["law_branch"] = infer_law_branch(court, daire)
        doc["court_level"] = infer_court_level(court, daire)

    return doc



DROP_STATEMENTS = [
    "DROP TABLE IF EXISTS ingest_log",
    "DROP TABLE IF EXISTS excluded_documents",
    "DROP TABLE IF EXISTS documents",
]


def init_db(conn, recreate: bool = False):
    conn.autocommit = True
    cur = conn.cursor()
    if recreate:
        for stmt in DROP_STATEMENTS:
            cur.execute(stmt)
        log.info("Existing tables dropped")
    for stmt in SCHEMA_STATEMENTS:
        cur.execute(stmt)
    conn.autocommit = False


def ingest_file(cur, doc):
    doc_id = doc["doc_id"]
    filename = doc["filename"]

    cur.execute(
        "SELECT filename FROM documents WHERE doc_id = %s AND filename != %s",
        (doc_id, filename),
    )
    existing = cur.fetchone()
    if existing:
        reason = f"duplicate: same legal identity as {existing[0]}"
        cur.execute(
            """INSERT INTO excluded_documents (filename, exclude_reason)
               VALUES (%s, %s)
               ON CONFLICT (filename) DO UPDATE SET exclude_reason = EXCLUDED.exclude_reason""",
            (filename, reason),
        )
        log.warning("Skipped (duplicate): %s — %s", filename, reason)
        return "skipped"

    file_path = doc["filename"]

    cur.execute(
        """INSERT INTO documents
           (doc_id, filename, esas_no, karar_no, court, daire, court_level,
            law_branch, decision_date, file_path, topic_keywords)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (doc_id) DO UPDATE SET
            filename = EXCLUDED.filename,
            esas_no = EXCLUDED.esas_no,
            karar_no = EXCLUDED.karar_no,
            court = EXCLUDED.court,
            daire = EXCLUDED.daire,
            court_level = EXCLUDED.court_level,
            law_branch = EXCLUDED.law_branch,
            decision_date = EXCLUDED.decision_date,
            file_path = EXCLUDED.file_path,
            topic_keywords = EXCLUDED.topic_keywords,
            ingested_at = now()""",
        (
            doc_id, filename, doc["esas_no"], doc["karar_no"],
            doc["court"], doc["daire"], doc["court_level"],
            doc["law_branch"], doc["decision_date"], file_path,
            Json(doc["topic_keywords"]),
        ),
    )
    return "ingested"


def print_summary(cur):
    print("\n" + "=" * 60)
    print("INGESTION SUMMARY")
    print("=" * 60)

    cur.execute("SELECT count(*) FROM documents")
    print(f"\nTotal documents: {cur.fetchone()[0]}")

    cur.execute("SELECT count(*) FROM excluded_documents")
    print(f"Excluded documents: {cur.fetchone()[0]}")

    print("\nCourt level distribution:")
    cur.execute("SELECT court_level, count(*) FROM documents GROUP BY court_level ORDER BY court_level")
    for level, n in cur.fetchall():
        print(f"  Level {level}: {n}")

    print("\nLaw branch distribution:")
    cur.execute("SELECT law_branch, count(*) FROM documents GROUP BY law_branch ORDER BY count(*) DESC")
    for branch, n in cur.fetchall():
        print(f"  {branch}: {n}")


def main():
    parser = argparse.ArgumentParser(description="Ingest corpus markdown files into PostgreSQL")
    parser.add_argument("--corpus-dir", type=Path, default=None)
    parser.add_argument("--from-s3", action="store_true",
                        help="Read filenames from S3 prefix instead of local corpus dir (no download)")
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate all tables before ingesting")
    args = parser.parse_args()

    settings = get_settings()

    if not settings.mongo_url:
        log.error("MONGO_URL is not set in .env")
        sys.exit(1)

    if args.from_s3:
        if not settings.s3_bucket_name or not settings.s3_prefix:
            log.error("S3_BUCKET_NAME and S3_PREFIX must be set in .env for --from-s3 mode")
            sys.exit(1)
        try:
            filenames = list_s3_filenames(settings)
        except Exception as e:
            log.error("S3 listing failed: %s", e)
            sys.exit(1)
        source_label = f"s3://{settings.s3_bucket_name}/{settings.s3_prefix}"
    else:
        corpus_dir = args.corpus_dir or settings.corpus_dir
        if not corpus_dir.exists():
            log.error("Corpus directory not found: %s", corpus_dir)
            sys.exit(1)
        filenames = sorted(f.name for f in corpus_dir.glob("*.md"))
        log.info("Found %d markdown files in %s", len(filenames), corpus_dir)
        source_label = str(corpus_dir)

    try:
        mongo_col = get_mongo_collection(settings)
        mongo_col.find_one({"filename": "__ping__"})
        log.info("MongoDB connected")
    except Exception as e:
        log.error("MongoDB connection failed: %s", e)
        sys.exit(1)

    log.info("Prefetching MongoDB documents for %d corpus files...", len(filenames))
    mongo_index = prefetch_mongo_docs(mongo_col, filenames)
    log.info("Loaded %d matching MongoDB documents", len(mongo_index))

    with get_connection() as conn:
        init_db(conn, recreate=args.recreate)
        cur = conn.cursor()
        run_id = str(uuid.uuid4())
        stats = {"ingested": 0, "skipped": 0, "errors": 0}

        for i, filename in enumerate(filenames, 1):
            try:
                mongo_doc = fetch_from_mongo(filename, mongo_index)
                if not mongo_doc:
                    stats["skipped"] += 1
                    continue
                doc = build_doc(filename, mongo_doc)
                result = ingest_file(cur, doc)
                stats[result] += 1
            except Exception as e:
                log.error("Error processing %s: %s", filename, e)
                stats["errors"] += 1

            if i % 1000 == 0:
                conn.commit()
                log.info("Progress: %d/%d — ingested=%d skipped=%d errors=%d",
                         i, len(filenames), stats["ingested"], stats["skipped"], stats["errors"])

        cur.execute(
            "INSERT INTO ingest_log (run_id, total_files, ingested, skipped, errors, corpus_dir) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (run_id, len(filenames), stats["ingested"], stats["skipped"], stats["errors"], source_label),
        )
        conn.commit()
        print_summary(cur)
        log.info("Run complete — ingested=%d skipped=%d errors=%d", stats["ingested"], stats["skipped"], stats["errors"])


if __name__ == "__main__":
    main()
