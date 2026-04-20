#!/usr/bin/env python3
"""
Document ingestion pipeline: corpus/*.md → PostgreSQL.
Metadata is fetched from MongoDB (tr-ictihat-v2) by filename.

Usage:
    python -m app.ingestion.ingest [--corpus-dir ./corpus]
"""

import argparse
import logging
import sys
import unicodedata
import uuid
from pathlib import Path

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
    "ceza_ilk": "İlk Derece",
    "hukuk_ilk": "İlk Derece",
    "fikri_sinai": "İlk Derece",
    "sayistay": "Sayıştay",
    "uyusmazlik": "Uyuşmazlık",
    "tuketici": "Tüketici"
}


def get_mongo_collection(settings):
    client = MongoClient(settings.mongo_url, serverSelectionTimeoutMS=8000)
    return client["data-team"]["tr-ictihat-v2"]


def fetch_from_mongo(filename: str, mongo_col) -> dict | None:
    return mongo_col.find_one({"filename": unicodedata.normalize("NFC", filename)})


def build_doc(filepath: Path, mongo_doc: dict) -> dict:
    court_raw = (mongo_doc.get("court") or "").lower()
    court = _MONGO_COURT_MAP.get(court_raw, court_raw)
    daire = mongo_doc.get("court_name") or ""

    doc = {
        "doc_id": mongo_doc.get("document_id") or filepath.stem,
        "filename": filepath.name,
        "court": court,
        "daire": daire,
        "esas_no": mongo_doc.get("case_no") or "",
        "karar_no": mongo_doc.get("decision_no") or "",
        "decision_date": mongo_doc.get("decision_date") or "",
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

    file_path = str(Path("corpus") / doc["filename"])

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
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate all tables before ingesting")
    args = parser.parse_args()

    settings = get_settings()
    corpus_dir = args.corpus_dir or settings.corpus_dir

    if not corpus_dir.exists():
        log.error("Corpus directory not found: %s", corpus_dir)
        sys.exit(1)

    if not settings.mongo_url:
        log.error("mongo_url is not set in .env")
        sys.exit(1)

    md_files = sorted(corpus_dir.glob("*.md"))
    log.info("Found %d markdown files in %s", len(md_files), corpus_dir)

    try:
        mongo_col = get_mongo_collection(settings)
        mongo_col.find_one({"filename": "__ping__"})
        log.info("MongoDB connected")
    except Exception as e:
        log.error("MongoDB connection failed: %s", e)
        sys.exit(1)

    with get_connection() as conn:
        init_db(conn, recreate=args.recreate)
        cur = conn.cursor()
        run_id = str(uuid.uuid4())
        stats = {"ingested": 0, "skipped": 0, "errors": 0}

        for filepath in md_files:
            try:
                mongo_doc = fetch_from_mongo(filepath.name, mongo_col)
                if not mongo_doc:
                    log.warning("No MongoDB record for %s — skipping", filepath.name)
                    stats["skipped"] += 1
                    continue
                doc = build_doc(filepath, mongo_doc)
                result = ingest_file(cur, doc)
                stats[result] += 1
            except Exception as e:
                log.error("Error processing %s: %s", filepath.name, e)
                stats["errors"] += 1

        cur.execute(
            "INSERT INTO ingest_log (run_id, total_files, ingested, skipped, errors, corpus_dir) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (run_id, len(md_files), stats["ingested"], stats["skipped"], stats["errors"], str(corpus_dir)),
        )
        conn.commit()
        print_summary(cur)
        log.info("Run complete — ingested=%d skipped=%d errors=%d", stats["ingested"], stats["skipped"], stats["errors"])


if __name__ == "__main__":
    main()
