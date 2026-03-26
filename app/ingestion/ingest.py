#!/usr/bin/env python3
"""
Document ingestion pipeline: corpus/*.md → PostgreSQL.

Usage:
    python -m app.ingestion.ingest [--corpus-dir ./corpus]
"""

import argparse
import hashlib
import logging
import sys
import uuid
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

from app.core.config import get_settings
from app.core.db import get_connection

# Import parsing logic from the existing corpus manifest builder
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "eval" / "scripts"))
from build_corpus_manifest import parse_file  # noqa: E402

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


def compute_doc_id(court, daire, esas_no):
    key = f"{court}|{daire}|{esas_no}".strip()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def init_db(conn):
    conn.autocommit = True
    cur = conn.cursor()
    for stmt in SCHEMA_STATEMENTS:
        cur.execute(stmt)
    conn.autocommit = False


def ingest_file(cur, parsed):
    filename = parsed["doc_id"]

    if parsed["excluded"]:
        cur.execute(
            """INSERT INTO excluded_documents (filename, exclude_reason)
               VALUES (%s, %s)
               ON CONFLICT (filename) DO UPDATE SET exclude_reason = EXCLUDED.exclude_reason""",
            (filename, parsed["exclude_reason"]),
        )
        log.info("Skipped (excluded): %s — %s", filename, parsed["exclude_reason"])
        return "skipped"

    if not parsed["esas_no"]:
        log.warning("Missing esas_no: %s — inserting with filename-based hash", filename)

    doc_id = compute_doc_id(parsed["court"], parsed["daire"], parsed["esas_no"])

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

    file_path = str(Path("corpus") / parsed["filename"])
    topic_keywords = parsed.get("topic_keywords", [])

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
            doc_id, filename, parsed["esas_no"], parsed["karar_no"],
            parsed["court"], parsed["daire"], parsed["court_level"],
            parsed["law_branch"], parsed["decision_date"], file_path,
            Json(topic_keywords),
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
    args = parser.parse_args()

    settings = get_settings()
    corpus_dir = args.corpus_dir or settings.corpus_dir

    if not corpus_dir.exists():
        log.error("Corpus directory not found: %s", corpus_dir)
        sys.exit(1)

    md_files = sorted(corpus_dir.glob("*.md"))
    log.info("Found %d markdown files in %s", len(md_files), corpus_dir)

    with get_connection() as conn:
        init_db(conn)
        cur = conn.cursor()
        run_id = str(uuid.uuid4())
        stats = {"ingested": 0, "skipped": 0, "errors": 0}

        for filepath in md_files:
            try:
                parsed = parse_file(filepath)
                result = ingest_file(cur, parsed)
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


if __name__ == "__main__":
    main()
