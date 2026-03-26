#!/usr/bin/env python3
"""
Fixed-size chunking pipeline: documents table + corpus/*.md → chunks table.

Usage:
    python -m app.ingestion.chunk
"""

import argparse
import hashlib
import logging
import sys
from pathlib import Path

import psycopg2
import tiktoken

from app.core.config import get_settings
from app.core.db import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

ENCODING = tiktoken.get_encoding("cl100k_base")

SCHEMA_STATEMENTS = [
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


def compute_chunk_id(doc_id, chunk_index):
    key = f"{doc_id}|{chunk_index}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def chunk_document(text, max_tokens=512, overlap=50):
    tokens = ENCODING.encode(text)
    if not tokens:
        return []

    step = max_tokens - overlap
    chunks = []
    for start in range(0, len(tokens), step):
        chunk_tokens = tokens[start:start + max_tokens]
        chunk_text = ENCODING.decode(chunk_tokens)
        chunks.append((chunk_text, len(chunk_tokens)))
        if start + max_tokens >= len(tokens):
            break
    return chunks


def main():
    parser = argparse.ArgumentParser(description="Chunk documents into fixed-size pieces")
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--overlap", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    max_tokens = args.max_tokens or settings.chunk_max_tokens
    overlap = args.overlap or settings.chunk_overlap
    corpus_dir = settings.corpus_dir

    with get_connection() as conn:
        conn.autocommit = True
        cur = conn.cursor()
        for stmt in SCHEMA_STATEMENTS:
            cur.execute(stmt)
        conn.autocommit = False

        cur.execute("SELECT doc_id, file_path FROM documents ORDER BY doc_id")
        documents = cur.fetchall()
        log.info("Found %d documents in database", len(documents))

        total_chunks = 0
        for doc_id, file_path in documents:
            full_path = corpus_dir.parent / file_path
            if not full_path.exists():
                log.warning("File not found: %s (doc_id=%s)", full_path, doc_id)
                continue

            text = full_path.read_text(encoding="utf-8")
            chunks = chunk_document(text, max_tokens, overlap)

            for idx, (chunk_text, token_count) in enumerate(chunks):
                chunk_id = compute_chunk_id(doc_id, idx)
                cur.execute(
                    """INSERT INTO chunks (chunk_id, doc_id, chunk_index, text, token_count)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (chunk_id) DO UPDATE SET
                        text = EXCLUDED.text,
                        token_count = EXCLUDED.token_count""",
                    (chunk_id, doc_id, idx, chunk_text, token_count),
                )

            total_chunks += len(chunks)

        conn.commit()

        cur.execute("SELECT count(*) FROM chunks")
        total = cur.fetchone()[0]
        cur.execute("SELECT count(DISTINCT doc_id) FROM chunks")
        docs = cur.fetchone()[0]
        cur.execute("SELECT min(token_count), max(token_count), avg(token_count) FROM chunks")
        mn, mx, av = cur.fetchone()

        print(f"\nTotal chunks: {total}")
        print(f"Documents chunked: {docs}")
        print(f"Average chunks per document: {total / docs if docs else 0:.1f}")
        print(f"Token counts: min={mn}, max={mx}, avg={av:.0f}")


if __name__ == "__main__":
    main()
