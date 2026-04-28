#!/usr/bin/env python3
"""
Graph building pipeline: PostgreSQL documents → citations + Neo4j graph.

Fourth step in the ingestion pipeline:
    ingest.py → chunk.py → embed.py → build_graph.py

Usage:
    python -m app.ingestion.build_graph [--no-neo4j] [--skip-pagerank]

Flags:
    --no-neo4j      Populate PG citations table only; skip Neo4j sync.
    --skip-pagerank Skip batch PageRank computation (useful for incremental runs).
"""

import argparse
import hashlib
import json
import logging
import traceback
from pathlib import Path

import boto3
from botocore.config import Config

from app.core.config import get_settings
from app.core.db import get_connection
from app.graph.citation_extractor import extract_citations
from app.graph.law_extractor import extract_law_references
from app.graph.resolver import resolve_citations

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

_CHECKPOINT_FILE = Path("neo4j_sync_checkpoint.json")


def _load_checkpoint() -> dict:
    if _CHECKPOINT_FILE.exists():
        try:
            data = json.loads(_CHECKPOINT_FILE.read_text())
            log.info("Loaded checkpoint: %s", data)
            return data
        except Exception as exc:
            log.warning("Could not read checkpoint file, starting fresh: %s", exc)
    return {}


def _save_checkpoint(data: dict) -> None:
    _CHECKPOINT_FILE.write_text(json.dumps(data))

GRAPH_SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS citations (
        citation_id    TEXT PRIMARY KEY,
        source_doc_id  TEXT NOT NULL REFERENCES documents(doc_id),
        target_doc_id  TEXT REFERENCES documents(doc_id),
        daire          TEXT NOT NULL DEFAULT '',
        esas_no        TEXT NOT NULL DEFAULT '',
        karar_no       TEXT NOT NULL DEFAULT '',
        snippet        TEXT NOT NULL DEFAULT '',
        confidence     FLOAT NOT NULL DEFAULT 0.0,
        extracted_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_citations_source ON citations(source_doc_id)",
    "CREATE INDEX IF NOT EXISTS idx_citations_target ON citations(target_doc_id)",
    "CREATE INDEX IF NOT EXISTS idx_citations_esas ON citations(esas_no)",
    """CREATE TABLE IF NOT EXISTS unresolved_citations (
        id             SERIAL PRIMARY KEY,
        source_doc_id  TEXT NOT NULL REFERENCES documents(doc_id),
        raw_text       TEXT NOT NULL,
        daire          TEXT,
        esas_no        TEXT,
        karar_no       TEXT,
        reason         TEXT NOT NULL,
        extracted_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
]

_ADD_GRAPH_COLUMNS = [
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS pagerank_score FLOAT DEFAULT 0.0",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS citation_in_degree INTEGER DEFAULT 0",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS citation_out_degree INTEGER DEFAULT 0",
    "ALTER TABLE citations ADD COLUMN IF NOT EXISTS daire TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE unresolved_citations ADD COLUMN IF NOT EXISTS daire TEXT",
    "ALTER TABLE unresolved_citations ADD COLUMN IF NOT EXISTS karar_no TEXT",
]


def _citation_id(source_doc_id: str, esas_no: str, karar_no: str) -> str:
    raw = f"{source_doc_id}|{esas_no}|{karar_no}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def init_graph_schema(conn) -> None:
    with conn.cursor() as cur:
        for stmt in GRAPH_SCHEMA_STATEMENTS:
            cur.execute(stmt)
        for stmt in _ADD_GRAPH_COLUMNS:
            cur.execute(stmt)
    conn.commit()


def _load_documents(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT doc_id, file_path, filename FROM documents")
        return [
            {"doc_id": row[0], "file_path": row[1], "filename": row[2]}
            for row in cur.fetchall()
        ]


def _read_document_text(doc: dict, settings, s3_client) -> str | None:
    """Read document text: try local disk first, fall back to S3."""
    # Try local file
    try:
        return open(doc["file_path"], encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        pass

    # Fall back to S3
    filename = doc.get("filename") or doc["file_path"].split("/")[-1]
    if not filename.endswith(".md"):
        filename += ".md"
    key = f"{settings.s3_prefix.rstrip('/')}/{filename}"
    try:
        obj = s3_client.get_object(Bucket=settings.s3_bucket_name, Key=key)
        return obj["Body"].read().decode("utf-8")
    except Exception as e:
        log.warning("Could not read %s from S3 (%s): %s", filename, key, e)
        return None


def _upsert_citations(conn, resolved) -> int:
    if not resolved:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO citations
               (citation_id, source_doc_id, target_doc_id, daire, esas_no, karar_no, snippet, confidence)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (citation_id) DO UPDATE SET
                   target_doc_id = EXCLUDED.target_doc_id,
                   daire         = EXCLUDED.daire,
                   confidence    = EXCLUDED.confidence,
                   snippet       = EXCLUDED.snippet,
                   extracted_at  = now()
            """,
            [
                (
                    _citation_id(c.source_doc_id, c.esas_no, c.karar_no),
                    c.source_doc_id,
                    c.target_doc_id,
                    c.daire,
                    c.esas_no,
                    c.karar_no,
                    c.snippet[:500],
                    c.confidence,
                )
                for c in resolved
            ],
        )
    conn.commit()
    return len(resolved)


def _upsert_unresolved(conn, unresolved) -> int:
    if not unresolved:
        return 0
    with conn.cursor() as cur:
        cur.execute("TRUNCATE unresolved_citations")
        cur.executemany(
            """INSERT INTO unresolved_citations
               (source_doc_id, raw_text, daire, esas_no, karar_no, reason)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            [
                (u.source_doc_id, u.raw_text, u.daire, u.esas_no, u.karar_no, u.reason)
                for u in unresolved
            ],
        )
    conn.commit()
    return len(unresolved)


def print_summary(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM citations")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM citations WHERE target_doc_id IS NOT NULL")
        resolved_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM unresolved_citations")
        unresolved_count = cur.fetchone()[0]
        cur.execute(
            "SELECT d.court, d.daire, COUNT(*) as in_deg "
            "FROM citations c JOIN documents d ON c.target_doc_id = d.doc_id "
            "GROUP BY d.court, d.daire ORDER BY in_deg DESC LIMIT 10"
        )
        top_cited = cur.fetchall()
        cur.execute(
            "SELECT doc_id, pagerank_score FROM documents "
            "ORDER BY pagerank_score DESC LIMIT 10"
        )
        top_pagerank = cur.fetchall()

    print("\n=== Citation Graph Summary ===")
    print(f"Total citations extracted : {total + unresolved_count}")
    print(f"Resolved citations        : {resolved_count}")
    print(f"Unresolved citations      : {unresolved_count}")
    rate = resolved_count / (total + unresolved_count) * 100 if (total + unresolved_count) else 0
    print(f"Resolution rate           : {rate:.1f}%")
    print("\nTop 10 most-cited documents:")
    for court, daire, count in top_cited:
        print(f"  {count:3d}  {court} — {daire}")
    print("\nTop 10 documents by PageRank:")
    for doc_id, score in top_pagerank:
        print(f"  {score:.4f}  {doc_id}")


def _sync_to_neo4j(conn, resolved, all_law_refs) -> None:
    """Sync all data from PostgreSQL to Neo4j. Separated so it can run standalone."""
    import time
    from app.core.graphdb import connect_neo4j, get_session, reconnect_neo4j
    from neo4j.exceptions import ServiceUnavailable, WriteServiceUnavailable
    from app.graph.neo4j_sync import (
        init_schema,
        upsert_court_hierarchy,
        upsert_legal_branches,
        upsert_laws,
        upsert_documents,
        upsert_citations as neo4j_upsert_citations,
        upsert_law_references,
    )

    log.info("Connecting to Neo4j...")
    connect_neo4j()

    ckpt = _load_checkpoint()

    if not ckpt.get("schema_done"):
        log.info("Initialising Neo4j schema...")
        with get_session() as session:
            init_schema(session)
            upsert_court_hierarchy(session)
            upsert_legal_branches(session)
            upsert_laws(session)
        ckpt["schema_done"] = True
        _save_checkpoint(ckpt)

    if not ckpt.get("docs_done"):
        docs_offset = ckpt.get("docs_offset", 0)

        def _on_doc_progress(n: int) -> None:
            ckpt["docs_offset"] = n
            _save_checkpoint(ckpt)

        log.info("Upserting document nodes...")
        n_docs = upsert_documents(conn, start_offset=docs_offset, on_progress=_on_doc_progress)
        log.info("Upserted %d document nodes", n_docs)
        ckpt["docs_done"] = True
        ckpt["docs_offset"] = n_docs
        _save_checkpoint(ckpt)

    if not ckpt.get("citations_done"):
        log.info("Upserting CITES relationships...")
        for attempt in range(3):
            try:
                with get_session() as session:
                    n_cites = neo4j_upsert_citations(session, resolved)
                log.info("Upserted %d CITES relationships", n_cites)
                ckpt["citations_done"] = True
                _save_checkpoint(ckpt)
                break
            except (ServiceUnavailable, WriteServiceUnavailable) as exc:
                if attempt < 2:
                    log.warning("Citations connection lost, reconnecting: %s", exc)
                    time.sleep(2 ** attempt)
                    reconnect_neo4j()
                else:
                    raise

    if not ckpt.get("law_refs_done"):
        log.info("Upserting REFERENCES_LAW relationships...")
        for attempt in range(3):
            try:
                with get_session() as session:
                    n_law_refs = upsert_law_references(session, all_law_refs)
                log.info("Upserted %d REFERENCES_LAW relationships", n_law_refs)
                ckpt["law_refs_done"] = True
                _save_checkpoint(ckpt)
                break
            except (ServiceUnavailable, WriteServiceUnavailable) as exc:
                if attempt < 2:
                    log.warning("Law refs connection lost, reconnecting: %s", exc)
                    time.sleep(2 ** attempt)
                    reconnect_neo4j()
                else:
                    raise

    _CHECKPOINT_FILE.unlink(missing_ok=True)
    log.info("Neo4j sync complete")


def _load_resolved_from_pg(conn):
    """Reload resolved citations from PostgreSQL (for --neo4j-only mode)."""
    from app.graph.resolver import ResolvedCitation
    with conn.cursor() as cur:
        cur.execute(
            "SELECT citation_id, source_doc_id, target_doc_id, daire, esas_no, karar_no, snippet, confidence "
            "FROM citations WHERE target_doc_id IS NOT NULL"
        )
        return [
            ResolvedCitation(
                source_doc_id=r[1], target_doc_id=r[2], daire=r[3] or "",
                esas_no=r[4] or "", karar_no=r[5] or "",
                snippet=r[6] or "", confidence=r[7] or 0.0,
            )
            for r in cur.fetchall()
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build citation graph from corpus")
    parser.add_argument("--no-neo4j", action="store_true", help="Skip Neo4j sync")
    parser.add_argument("--neo4j-only", action="store_true",
                        help="Skip extraction; sync existing PG data to Neo4j only")
    parser.add_argument("--skip-pagerank", action="store_true", help="Skip PageRank computation")
    args = parser.parse_args()

    settings = get_settings()

    with get_connection(settings.database_url) as conn:
        # 1. Initialise PG schema
        log.info("Initialising graph schema...")
        init_graph_schema(conn)

        if args.neo4j_only:
            # Load existing resolved citations from PG and sync to Neo4j directly
            resolved = _load_resolved_from_pg(conn)
            log.info("Loaded %d resolved citations from PostgreSQL", len(resolved))
            try:
                _sync_to_neo4j(conn, resolved, [])
            except Exception as e:
                log.error("Neo4j sync failed: %s", e)
                log.error("Traceback:\n%s", traceback.format_exc())
            print_summary(conn)
            return

        s3_client = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
            config=Config(max_pool_connections=20),
        )

        # 2. Load documents
        docs = _load_documents(conn)
        log.info("Loaded %d documents", len(docs))

        # 3. Extract citations and law references (local disk first, S3 fallback)
        all_raw = []
        all_law_refs = []
        missing = 0
        for idx, doc in enumerate(docs):
            text = _read_document_text(doc, settings, s3_client)
            if text is None:
                missing += 1
                continue
            all_raw.extend(extract_citations(doc["doc_id"], text))
            all_law_refs.extend(extract_law_references(doc["doc_id"], text))
            if (idx + 1) % 5000 == 0:
                log.info("Extraction: %d/%d docs", idx + 1, len(docs))

        if missing:
            log.warning("Skipped %d documents (not found locally or in S3)", missing)
        log.info(
            "Extracted %d case citation references, %d law article references",
            len(all_raw), len(all_law_refs),
        )

        # 4. Load canonical daire names from MongoDB for fuzzy resolution.
        known_daires: list[str] | None = None
        if settings.mongo_url:
            try:
                from pymongo import MongoClient
                mongo_client = MongoClient(settings.mongo_url, serverSelectionTimeoutMS=5000)
                known_daires = [
                    d for d in mongo_client["data-team"]["tr-ictihat-v2"].distinct("court_name")
                    if d
                ]
                log.info("Loaded %d canonical daire names from MongoDB", len(known_daires))
            except Exception as e:
                log.warning("Could not load daire names from MongoDB (%s) — fuzzy fallback disabled", e)

        # 5. Resolve citations against documents table
        resolved, unresolved = resolve_citations(all_raw, conn, known_daires=known_daires)
        log.info("Resolved: %d  |  Unresolved: %d", len(resolved), len(unresolved))

        # 6. Persist to PostgreSQL
        _upsert_citations(conn, resolved)
        _upsert_unresolved(conn, unresolved)
        log.info("Citations written to PostgreSQL")

        # 7. Sync to Neo4j (unless --no-neo4j)
        if not args.no_neo4j:
            try:
                _sync_to_neo4j(conn, resolved, all_law_refs)
            except Exception as e:
                log.error("Neo4j sync failed (non-fatal): %s", e)
                log.error("Traceback:\n%s", traceback.format_exc())
                log.warning("Citation data is in PostgreSQL. Re-run with --neo4j-only once Neo4j is up.")

        # 7. Compute and write back PageRank (unless --skip-pagerank)
        if not args.skip_pagerank:
            from app.graph.metrics import (
                compute_pagerank_networkx,
                write_pagerank_to_postgres,
                compute_in_out_degree,
                write_degree_to_postgres,
            )

            log.info("Computing PageRank...")
            scores = compute_pagerank_networkx(conn)
            n_written = write_pagerank_to_postgres(scores, conn)
            log.info("PageRank written to %d documents", n_written)

            log.info("Computing in/out-degree...")
            degrees = compute_in_out_degree(conn)
            write_degree_to_postgres(degrees, conn)
            log.info("Degree metrics written")

            if not args.no_neo4j:
                try:
                    from app.core.graphdb import get_neo4j_driver
                    from app.graph.metrics import write_pagerank_to_neo4j
                    write_pagerank_to_neo4j(scores, get_neo4j_driver())
                    log.info("PageRank written to Neo4j")
                except Exception as e:
                    log.warning("Could not write PageRank to Neo4j: %s", e)

        print_summary(conn)


if __name__ == "__main__":
    main()
