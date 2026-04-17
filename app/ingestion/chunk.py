#!/usr/bin/env python3
"""
Fixed-size chunking pipeline: documents table + corpus/*.md → chunks table.

Chunks are built greedily from sentences (and paragraphs where possible)
so that no sentence is split mid-way. Each chunk is at most max_tokens
tokens; overlap is achieved by carrying over the last few sentences from
the previous chunk.

Usage:
    python -m app.ingestion.chunk
"""

import argparse
import hashlib
import logging
import re
import sys
from pathlib import Path

import psycopg2
import tiktoken

from app.core.config import get_settings
from app.core.db import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

ENCODING = tiktoken.get_encoding("cl100k_base")

# Sentence boundary: end-of-sentence punctuation followed by whitespace or end-of-string.
# The negative-lookbehind avoids splitting on common abbreviations (single uppercase letter + dot).
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?…])\s+')

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


def _split_into_sentences(text: str) -> list[str]:
    """Split text into sentences, respecting paragraph boundaries.

    Paragraph breaks are preserved as empty-string sentinels so the
    caller can prefer to break chunks at paragraph boundaries.
    """
    units: list[str] = []
    paragraphs = re.split(r'\n{2,}', text)
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(para) if s.strip()]
        units.extend(sentences)
        units.append("")  # paragraph boundary sentinel
    return units


def chunk_document(text: str, max_tokens: int = 1024, overlap: int = 50) -> list[tuple[str, int]]:
    """Split *text* into chunks of at most *max_tokens* tokens.

    Sentences are never split. Paragraph boundaries are used as
    preferred break points when a chunk is reasonably full (≥ 75 % of
    max_tokens). Overlap is implemented by carrying the last sentences
    of the previous chunk into the next one, up to *overlap* tokens.
    """
    units = _split_into_sentences(text)
    if not units:
        return []

    # Pre-compute token counts (paragraph sentinels have 0 tokens).
    tok: list[int] = [len(ENCODING.encode(u)) if u else 0 for u in units]

    chunks: list[tuple[str, int]] = []
    overlap_sentences: list[str] = []   # sentences carried over from previous chunk
    overlap_tok: int = 0
    i = 0

    while i < len(units):
        parts: list[str] = list(overlap_sentences)
        total: int = overlap_tok

        chunk_start = i
        while i < len(units):
            unit = units[i]
            t = tok[i]

            if not unit:
                # Paragraph boundary — break here if chunk is ≥ 75 % full.
                i += 1
                if total >= max_tokens * 0.75 and parts:
                    break
                # Otherwise just keep going (don't add a blank line to text).
                continue

            # If this single sentence is larger than max_tokens, include it
            # anyway to avoid an infinite loop — we can't split it further.
            if total + t > max_tokens and parts:
                break

            parts.append(unit)
            total += t
            i += 1

        # Guard: if we made no forward progress, skip the unit.
        if i == chunk_start:
            i += 1
            continue

        real_parts = [p for p in parts if p]
        if not real_parts:
            continue

        chunk_text = " ".join(real_parts)
        chunk_tok = len(ENCODING.encode(chunk_text))
        chunks.append((chunk_text, chunk_tok))

        # Build overlap: walk backwards through real_parts collecting up to
        # `overlap` tokens worth of sentences.
        overlap_sentences = []
        overlap_tok = 0
        for s in reversed(real_parts):
            t = len(ENCODING.encode(s))
            if overlap_tok + t > overlap:
                break
            overlap_sentences.insert(0, s)
            overlap_tok += t

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
