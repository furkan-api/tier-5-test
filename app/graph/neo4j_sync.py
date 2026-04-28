"""
All Neo4j write/read operations for the legal graph.

Node labels:
  Document    — a court decision (one per document in the corpus)
  Court       — a court or chamber (static hierarchy + dynamic daire nodes)
  LegalBranch — law branch: hukuk, ceza, idari, vergi, anayasa
  Law         — a specific statute (e.g. TCK, TBK)
  LawArticle  — a specific article of a statute (e.g. TCK 302)

Relationships:
  (Document)-[:CITES]->(Document)            — case-level citation
  (Document)-[:IN_COURT]->(Court)            — the court that issued the decision
  (Court)-[:PART_OF]->(Court)               — daire → general court
  (Court)-[:APPEALS_TO]->(Court)             — hierarchical appeal chain
  (Document)-[:IN_BRANCH]->(LegalBranch)    — law branch of the document
  (Document)-[:REFERENCES_LAW]->(LawArticle) — law article cited in the decision
  (LawArticle)-[:PART_OF]->(Law)             — article belongs to statute
  (Law)-[:BELONGS_TO]->(LegalBranch)         — statute belongs to law branch

All functions take an open neo4j.Session as first argument.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from neo4j import Driver, Session
from neo4j.exceptions import ServiceUnavailable, WriteServiceUnavailable

from app.graph import schema as _schema

log = logging.getLogger(__name__)
from app.graph.law_extractor import LAW_REGISTRY, RawLawReference
from app.graph.resolver import ResolvedCitation


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_schema(session: Session) -> None:
    """Create constraints and indexes. Idempotent (uses IF NOT EXISTS)."""
    for stmt in _schema.ALL_STATEMENTS:
        session.run(stmt)


# ---------------------------------------------------------------------------
# Static court hierarchy
# ---------------------------------------------------------------------------

# (name, level, branch, pillar)
# level: 1=İlk Derece, 2=BAM/BİM, 3=Daire, 4=HGK/CGK/İBK/AYM
_COURTS = [
    # Adli yargı — Yargıtay (12 HD + 12 CD active per 26/06/2025 iş bölümü)
    ("Yargıtay",             4, "adli",  "yargıtay"),
    ("Yargıtay HGK",         4, "hukuk", "yargıtay"),
    ("Yargıtay CGK",         4, "ceza",  "yargıtay"),
    ("Yargıtay 1. HD",       3, "hukuk", "yargıtay"),
    ("Yargıtay 2. HD",       3, "hukuk", "yargıtay"),
    ("Yargıtay 3. HD",       3, "hukuk", "yargıtay"),
    ("Yargıtay 4. HD",       3, "hukuk", "yargıtay"),
    ("Yargıtay 5. HD",       3, "hukuk", "yargıtay"),
    ("Yargıtay 6. HD",       3, "hukuk", "yargıtay"),
    ("Yargıtay 7. HD",       3, "hukuk", "yargıtay"),
    ("Yargıtay 8. HD",       3, "hukuk", "yargıtay"),
    ("Yargıtay 9. HD",       3, "hukuk", "yargıtay"),
    ("Yargıtay 10. HD",      3, "hukuk", "yargıtay"),
    ("Yargıtay 11. HD",      3, "hukuk", "yargıtay"),
    ("Yargıtay 12. HD",      3, "hukuk", "yargıtay"),
    ("Yargıtay 1. CD",       3, "ceza",  "yargıtay"),
    ("Yargıtay 2. CD",       3, "ceza",  "yargıtay"),
    ("Yargıtay 3. CD",       3, "ceza",  "yargıtay"),
    ("Yargıtay 4. CD",       3, "ceza",  "yargıtay"),
    ("Yargıtay 5. CD",       3, "ceza",  "yargıtay"),
    ("Yargıtay 6. CD",       3, "ceza",  "yargıtay"),
    ("Yargıtay 7. CD",       3, "ceza",  "yargıtay"),
    ("Yargıtay 8. CD",       3, "ceza",  "yargıtay"),
    ("Yargıtay 9. CD",       3, "ceza",  "yargıtay"),
    ("Yargıtay 10. CD",      3, "ceza",  "yargıtay"),
    ("Yargıtay 11. CD",      3, "ceza",  "yargıtay"),
    ("Yargıtay 12. CD",      3, "ceza",  "yargıtay"),
    # Adli yargı — BAM (17 operational)
    ("İstanbul BAM",         2, "adli",  "bam"),
    ("Ankara BAM",           2, "adli",  "bam"),
    ("İzmir BAM",            2, "adli",  "bam"),
    ("Bursa BAM",            2, "adli",  "bam"),
    ("Antalya BAM",          2, "adli",  "bam"),
    ("Samsun BAM",           2, "adli",  "bam"),
    ("Konya BAM",            2, "adli",  "bam"),
    ("Gaziantep BAM",        2, "adli",  "bam"),
    ("Erzurum BAM",          2, "adli",  "bam"),
    ("Diyarbakır BAM",       2, "adli",  "bam"),
    ("Sakarya BAM",          2, "adli",  "bam"),
    ("Trabzon BAM",          2, "adli",  "bam"),
    ("Adana BAM",            2, "adli",  "bam"),
    ("Kayseri BAM",          2, "adli",  "bam"),
    ("Van BAM",              2, "adli",  "bam"),
    ("Denizli BAM",          2, "adli",  "bam"),
    ("Tekirdağ BAM",         2, "adli",  "bam"),
    # Adli yargı — İlk derece (representative types)
    ("Asliye Hukuk Mahkemesi",   1, "hukuk", "ilk_derece"),
    ("Asliye Ceza Mahkemesi",    1, "ceza",  "ilk_derece"),
    ("Ağır Ceza Mahkemesi",      1, "ceza",  "ilk_derece"),
    ("İş Mahkemesi",             1, "hukuk", "ilk_derece"),
    ("Aile Mahkemesi",           1, "hukuk", "ilk_derece"),
    ("Asliye Ticaret Mahkemesi", 1, "hukuk", "ilk_derece"),
    ("Sulh Hukuk Mahkemesi",     1, "hukuk", "ilk_derece"),
    ("İcra Mahkemesi",           1, "hukuk", "ilk_derece"),
    ("Tüketici Mahkemesi",       1, "hukuk", "ilk_derece"),
    # İdari yargı — Danıştay (per 2020/62 + 2023/33 iş bölümü)
    ("Danıştay",             4, "idari",  "danıştay"),
    ("Danıştay İDDK",        4, "idari",  "danıştay"),
    ("Danıştay VDDK",        4, "vergi",  "danıştay"),
    ("Danıştay 2. D",        3, "idari",  "danıştay"),
    ("Danıştay 3. D",        3, "vergi",  "danıştay"),
    ("Danıştay 4. D",        3, "idari",  "danıştay"),
    ("Danıştay 5. D",        3, "idari",  "danıştay"),
    ("Danıştay 6. D",        3, "idari",  "danıştay"),
    ("Danıştay 7. D",        3, "vergi",  "danıştay"),
    ("Danıştay 8. D",        3, "idari",  "danıştay"),
    ("Danıştay 9. D",        3, "vergi",  "danıştay"),
    ("Danıştay 10. D",       3, "idari",  "danıştay"),
    ("Danıştay 12. D",       3, "idari",  "danıştay"),
    ("Danıştay 13. D",       3, "idari",  "danıştay"),
    # İdari yargı — BİM (12 operational per May 2025)
    ("İstanbul BİM",         2, "idari",  "bim"),
    ("Ankara BİM",           2, "idari",  "bim"),
    ("İzmir BİM",            2, "idari",  "bim"),
    ("Adana BİM",            2, "idari",  "bim"),
    ("Bursa BİM",            2, "idari",  "bim"),
    ("Erzurum BİM",          2, "idari",  "bim"),
    ("Gaziantep BİM",        2, "idari",  "bim"),
    ("Konya BİM",            2, "idari",  "bim"),
    ("Samsun BİM",           2, "idari",  "bim"),
    ("Antalya BİM",          2, "idari",  "bim"),
    ("Diyarbakır BİM",       2, "idari",  "bim"),
    ("Kayseri BİM",          2, "idari",  "bim"),
    # İdari yargı — İlk derece
    ("İdare Mahkemesi",      1, "idari",  "ilk_derece"),
    ("Vergi Mahkemesi",      1, "vergi",  "ilk_derece"),
    # Anayasa Mahkemesi
    ("Anayasa Mahkemesi",    4, "anayasa", "aym"),
    # Uyuşmazlık
    ("Uyuşmazlık Mahkemesi", 4, "karma",   "uyusmazlik"),
]

# (lower_court_name, higher_court_name)
_APPEALS_TO = [
    # Adli yargı
    ("Asliye Hukuk Mahkemesi",   "İstanbul BAM"),
    ("Asliye Ceza Mahkemesi",    "İstanbul BAM"),
    ("Ağır Ceza Mahkemesi",      "İstanbul BAM"),
    ("İş Mahkemesi",             "İstanbul BAM"),
    ("Aile Mahkemesi",           "İstanbul BAM"),
    ("Asliye Ticaret Mahkemesi", "İstanbul BAM"),
    ("Sulh Hukuk Mahkemesi",     "İstanbul BAM"),
    ("İstanbul BAM",  "Yargıtay"),
    ("Ankara BAM",    "Yargıtay"),
    ("İzmir BAM",     "Yargıtay"),
    ("Bursa BAM",     "Yargıtay"),
    ("Antalya BAM",   "Yargıtay"),
    ("Samsun BAM",    "Yargıtay"),
    ("Konya BAM",     "Yargıtay"),
    ("Gaziantep BAM", "Yargıtay"),
    ("Erzurum BAM",   "Yargıtay"),
    ("Diyarbakır BAM","Yargıtay"),
    ("Sakarya BAM",   "Yargıtay"),
    ("Trabzon BAM",   "Yargıtay"),
    ("Adana BAM",     "Yargıtay"),
    ("Kayseri BAM",   "Yargıtay"),
    ("Van BAM",       "Yargıtay"),
    ("Denizli BAM",   "Yargıtay"),
    ("Tekirdağ BAM",  "Yargıtay"),
    # Daire → HGK/CGK (direnme path)
    ("Yargıtay 1. HD",  "Yargıtay HGK"),
    ("Yargıtay 2. HD",  "Yargıtay HGK"),
    ("Yargıtay 3. HD",  "Yargıtay HGK"),
    ("Yargıtay 4. HD",  "Yargıtay HGK"),
    ("Yargıtay 5. HD",  "Yargıtay HGK"),
    ("Yargıtay 6. HD",  "Yargıtay HGK"),
    ("Yargıtay 7. HD",  "Yargıtay HGK"),
    ("Yargıtay 8. HD",  "Yargıtay HGK"),
    ("Yargıtay 9. HD",  "Yargıtay HGK"),
    ("Yargıtay 10. HD", "Yargıtay HGK"),
    ("Yargıtay 11. HD", "Yargıtay HGK"),
    ("Yargıtay 12. HD", "Yargıtay HGK"),
    ("Yargıtay 1. CD",  "Yargıtay CGK"),
    ("Yargıtay 2. CD",  "Yargıtay CGK"),
    ("Yargıtay 3. CD",  "Yargıtay CGK"),
    ("Yargıtay 4. CD",  "Yargıtay CGK"),
    ("Yargıtay 5. CD",  "Yargıtay CGK"),
    ("Yargıtay 6. CD",  "Yargıtay CGK"),
    ("Yargıtay 7. CD",  "Yargıtay CGK"),
    ("Yargıtay 8. CD",  "Yargıtay CGK"),
    ("Yargıtay 9. CD",  "Yargıtay CGK"),
    ("Yargıtay 10. CD", "Yargıtay CGK"),
    ("Yargıtay 11. CD", "Yargıtay CGK"),
    ("Yargıtay 12. CD", "Yargıtay CGK"),
    # İdari yargı
    ("İdare Mahkemesi",  "İstanbul BİM"),
    ("Vergi Mahkemesi",  "İstanbul BİM"),
    ("İstanbul BİM",    "Danıştay"),
    ("Ankara BİM",      "Danıştay"),
    ("İzmir BİM",       "Danıştay"),
    ("Adana BİM",       "Danıştay"),
    ("Bursa BİM",       "Danıştay"),
    ("Erzurum BİM",     "Danıştay"),
    ("Gaziantep BİM",   "Danıştay"),
    ("Konya BİM",       "Danıştay"),
    ("Samsun BİM",      "Danıştay"),
    ("Antalya BİM",     "Danıştay"),
    ("Diyarbakır BİM",  "Danıştay"),
    ("Kayseri BİM",     "Danıştay"),
    # Danıştay daire → kurul (ısrar path)
    ("Danıştay 2. D",   "Danıştay İDDK"),
    ("Danıştay 4. D",   "Danıştay İDDK"),
    ("Danıştay 5. D",   "Danıştay İDDK"),
    ("Danıştay 6. D",   "Danıştay İDDK"),
    ("Danıştay 8. D",   "Danıştay İDDK"),
    ("Danıştay 10. D",  "Danıştay İDDK"),
    ("Danıştay 12. D",  "Danıştay İDDK"),
    ("Danıştay 13. D",  "Danıştay İDDK"),
    ("Danıştay 3. D",   "Danıştay VDDK"),
    ("Danıştay 7. D",   "Danıştay VDDK"),
    ("Danıştay 9. D",   "Danıştay VDDK"),
]


def upsert_court_hierarchy(session: Session) -> None:
    """Create or update all static Court nodes and APPEALS_TO relationships."""
    for name, level, branch, pillar in _COURTS:
        session.run(
            "MERGE (c:Court {name: $name}) "
            "SET c.level = $level, c.branch = $branch, c.pillar = $pillar",
            name=name, level=level, branch=branch, pillar=pillar,
        )
    for lower, higher in _APPEALS_TO:
        session.run(
            "MATCH (lo:Court {name: $lower}) "
            "MATCH (hi:Court {name: $higher}) "
            "MERGE (lo)-[:APPEALS_TO]->(hi)",
            lower=lower, higher=higher,
        )


# ---------------------------------------------------------------------------
# Legal branches (static)
# ---------------------------------------------------------------------------

_LEGAL_BRANCHES = [
    ("hukuk",   "Hukuk (Medeni/Özel)"),
    ("ceza",    "Ceza Hukuku"),
    ("idari",   "İdare Hukuku"),
    ("vergi",   "Vergi Hukuku"),
    ("anayasa", "Anayasa Hukuku"),
    ("karma",   "Karma / Diğer"),
]


def upsert_legal_branches(session: Session) -> None:
    """Create static LegalBranch nodes."""
    for name, label in _LEGAL_BRANCHES:
        session.run(
            "MERGE (b:LegalBranch {name: $name}) SET b.label = $label",
            name=name, label=label,
        )


# ---------------------------------------------------------------------------
# Laws (from registry)
# ---------------------------------------------------------------------------

def upsert_laws(session: Session) -> None:
    """
    Create Law nodes from LAW_REGISTRY and link each to its LegalBranch.
    LegalBranch nodes must exist before calling this (call upsert_legal_branches first).
    """
    batch = [
        {
            "code":      code,
            "full_name": info["full_name"],
            "law_no":    info.get("law_no"),
            "branch":    info["branch"],
        }
        for code, info in LAW_REGISTRY.items()
    ]
    session.run(
        "UNWIND $batch AS row "
        "MERGE (l:Law {code: row.code}) "
        "SET l.full_name = row.full_name, l.law_no = row.law_no, l.branch = row.branch "
        "WITH l, row "
        "MATCH (b:LegalBranch {name: row.branch}) "
        "MERGE (l)-[:BELONGS_TO]->(b)",
        batch=batch,
    )


# ---------------------------------------------------------------------------
# Document nodes
# ---------------------------------------------------------------------------

_BATCH_SIZE = 100
_MAX_BATCH_RETRIES = 5


def _upsert_batch_with_retry(batch: list[dict]) -> None:
    """Run _upsert_doc_batch with reconnect-on-failure backoff."""
    from app.core.graphdb import get_neo4j_driver, reconnect_neo4j

    for attempt in range(_MAX_BATCH_RETRIES):
        try:
            driver = get_neo4j_driver()
            with driver.session(database="neo4j", default_access_mode="WRITE") as s:
                _upsert_doc_batch(s, batch)
            return
        except (ServiceUnavailable, WriteServiceUnavailable) as exc:
            if attempt < _MAX_BATCH_RETRIES - 1:
                wait = min(2 ** attempt, 30)
                log.warning(
                    "Batch connection lost (attempt %d/%d), reconnecting in %ds: %s",
                    attempt + 1, _MAX_BATCH_RETRIES, wait, exc,
                )
                time.sleep(wait)
                reconnect_neo4j()
            else:
                raise


def upsert_documents(
    conn,
    start_offset: int = 0,
    on_progress: Callable[[int], None] | None = None,
) -> int:
    """
    Batch-upsert Document nodes from PostgreSQL.

    start_offset: skip the first N rows (for resume after failure).
    on_progress:  called with the running total after each batch, for checkpointing.

    Returns total count of documents upserted (including any skipped by offset).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT doc_id, court, daire, court_level, esas_no, karar_no, "
            "decision_date, law_branch, pagerank_score "
            "FROM documents ORDER BY doc_id"
        )
        rows = cur.fetchall()

    if start_offset:
        log.info("Resuming document upsert from offset %d / %d", start_offset, len(rows))

    total = start_offset
    batch: list[dict] = []
    for row in rows[start_offset:]:
        batch.append({
            "doc_id":         row[0],
            "court":          row[1] or "",
            "daire":          row[2] or "",
            "court_level":    row[3] or 0,
            "esas_no":        row[4] or "",
            "karar_no":       row[5] or "",
            "decision_date":  str(row[6] or ""),
            "law_branch":     row[7] or "",
            "pagerank_score": float(row[8] or 0.0),
        })
        if len(batch) >= _BATCH_SIZE:
            _upsert_batch_with_retry(batch)
            total += len(batch)
            batch = []
            if total % 5000 == 0:
                log.info("Documents upserted: %d", total)
            if on_progress:
                on_progress(total)
    if batch:
        _upsert_batch_with_retry(batch)
        total += len(batch)
        if on_progress:
            on_progress(total)
    return total


def _upsert_doc_batch(session: Session, batch: list[dict]) -> None:
    def _work(tx):
        # Step 1: upsert Document nodes with all scalar properties
        tx.run(
            "UNWIND $batch AS row "
            "MERGE (d:Document {doc_id: row.doc_id}) "
            "SET d.court         = row.court, "
            "    d.daire         = row.daire, "
            "    d.court_level   = row.court_level, "
            "    d.esas_no       = row.esas_no, "
            "    d.karar_no      = row.karar_no, "
            "    d.decision_date = row.decision_date, "
            "    d.law_branch    = row.law_branch, "
            "    d.pagerank_score = row.pagerank_score",
            batch=batch,
        )
        # Step 2: link to specific daire Court node
        tx.run(
            "UNWIND $batch AS row "
            "WITH row WHERE row.daire <> '' "
            "MATCH (d:Document {doc_id: row.doc_id}) "
            "MERGE (dc:Court {name: row.daire}) "
            "ON CREATE SET dc.level  = row.court_level, "
            "              dc.branch = row.law_branch, "
            "              dc.pillar = 'corpus' "
            "MERGE (d)-[:IN_COURT]->(dc) "
            "WITH dc, row "
            "WHERE row.court <> '' AND row.court <> row.daire "
            "OPTIONAL MATCH (gc:Court {name: row.court}) "
            "FOREACH (_ IN CASE WHEN gc IS NOT NULL THEN [1] ELSE [] END | "
            "  MERGE (dc)-[:PART_OF]->(gc) "
            ")",
            batch=batch,
        )
        # Step 3: fallback for documents with empty daire
        tx.run(
            "UNWIND $batch AS row "
            "WITH row WHERE row.daire = '' AND row.court <> '' "
            "MATCH (d:Document {doc_id: row.doc_id}) "
            "OPTIONAL MATCH (gc:Court {name: row.court}) "
            "FOREACH (_ IN CASE WHEN gc IS NOT NULL THEN [1] ELSE [] END | "
            "  MERGE (d)-[:IN_COURT]->(gc) "
            ")",
            batch=batch,
        )
        # Step 4: link to LegalBranch
        tx.run(
            "UNWIND $batch AS row "
            "WITH row WHERE row.law_branch <> '' "
            "MATCH (d:Document {doc_id: row.doc_id}) "
            "OPTIONAL MATCH (lb:LegalBranch {name: row.law_branch}) "
            "FOREACH (_ IN CASE WHEN lb IS NOT NULL THEN [1] ELSE [] END | "
            "  MERGE (d)-[:IN_BRANCH]->(lb) "
            ")",
            batch=batch,
        )

    session.execute_write(_work)


# ---------------------------------------------------------------------------
# Law article references
# ---------------------------------------------------------------------------

_LAW_REF_BATCH = 200


def upsert_law_references(session: Session, refs: list[RawLawReference]) -> int:
    """
    Upsert LawArticle nodes and REFERENCES_LAW edges.

    For each unique (law_code, article) across all refs:
      - MERGE LawArticle {id: "{code}_{article}"}
      - MERGE (LawArticle)-[:PART_OF]->(Law)
      - MERGE (Document)-[:REFERENCES_LAW]->(LawArticle) with paragraph/subparagraph

    Law nodes must exist before calling (call upsert_laws first).
    Returns number of references upserted.
    """
    if not refs:
        return 0

    batch = [
        {
            "doc_id":      r.source_doc_id,
            "code":        r.law_code,
            "article_id":  f"{r.law_code}_{r.article}",
            "article":     r.article,
            "paragraph":   r.paragraph,
            "subparagraph": r.subparagraph,
        }
        for r in refs
    ]

    total = 0
    for i in range(0, len(batch), _LAW_REF_BATCH):
        chunk = batch[i: i + _LAW_REF_BATCH]
        # Create LawArticle nodes and link to Law
        session.run(
            "UNWIND $batch AS row "
            "MERGE (la:LawArticle {id: row.article_id}) "
            "ON CREATE SET la.code = row.code, la.article = row.article "
            "WITH la, row "
            "OPTIONAL MATCH (l:Law {code: row.code}) "
            "FOREACH (_ IN CASE WHEN l IS NOT NULL THEN [1] ELSE [] END | "
            "  MERGE (la)-[:PART_OF]->(l) "
            ")",
            batch=chunk,
        )
        # Create REFERENCES_LAW edges from Document to LawArticle
        session.run(
            "UNWIND $batch AS row "
            "MATCH (d:Document {doc_id: row.doc_id}) "
            "MATCH (la:LawArticle {id: row.article_id}) "
            "MERGE (d)-[r:REFERENCES_LAW]->(la) "
            "SET r.paragraph    = row.paragraph, "
            "    r.subparagraph = row.subparagraph",
            batch=chunk,
        )
        total += len(chunk)

    return total


# ---------------------------------------------------------------------------
# Case-level citation relationships
# ---------------------------------------------------------------------------

def upsert_citations(session: Session, resolved: list[ResolvedCitation]) -> int:
    """
    Batch-upsert CITES relationships from resolved case citations.
    Returns count of relationships upserted.
    """
    if not resolved:
        return 0
    batch = [
        {
            "source":     c.source_doc_id,
            "target":     c.target_doc_id,
            "confidence": c.confidence,
            "snippet":    c.snippet[:500],
        }
        for c in resolved
    ]
    session.run(
        "UNWIND $batch AS row "
        "MATCH (src:Document {doc_id: row.source}) "
        "MATCH (tgt:Document {doc_id: row.target}) "
        "MERGE (src)-[r:CITES]->(tgt) "
        "SET r.confidence = row.confidence, r.snippet = row.snippet",
        batch=batch,
    )
    return len(batch)


# ---------------------------------------------------------------------------
# Graph query
# ---------------------------------------------------------------------------

def get_citation_neighbors(
    session: Session,
    doc_ids: list[str],
    hops: int = 1,
) -> dict[str, list[str]]:
    """
    Return citation neighbors (bidirectional) for the given seed doc_ids.

    Uses undirected traversal so both "cites" and "is cited by" are captured.

    Returns {seed_doc_id: [neighbor_doc_id, ...]} — seeds excluded from values.
    """
    if not doc_ids:
        return {}
    result = session.run(
        f"MATCH (d:Document) WHERE d.doc_id IN $doc_ids "
        f"MATCH (d)-[:CITES*1..{hops}]-(neighbor:Document) "
        f"WHERE NOT neighbor.doc_id IN $doc_ids "
        f"RETURN d.doc_id AS seed, collect(DISTINCT neighbor.doc_id) AS neighbors",
        doc_ids=doc_ids,
    )
    return {row["seed"]: row["neighbors"] for row in result}
