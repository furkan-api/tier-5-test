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
import re
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

# Court type-level metadata — individual court nodes are created dynamically
# from MongoDB data; this dict only encodes structural facts about each type.
# level: 1=İlk Derece, 2=BAM/BİM, 3=Daire, 4=Yargıtay/Danıştay/AYM
# apex: the next court up in the appeal chain (None for apex courts)
_COURT_TYPE_META: dict[str, dict] = {
    "BAM":        {"level": 2, "pillar": "bam",        "apex": "Yargıtay"},
    "BİM":        {"level": 2, "pillar": "bim",        "apex": "Danıştay"},
    "Yargıtay":   {"level": 4, "pillar": "yargıtay",   "apex": None},
    "Danıştay":   {"level": 4, "pillar": "danıştay",   "apex": None},
    "AYM":        {"level": 4, "pillar": "aym",        "apex": None},
    "Sayıştay":   {"level": 4, "pillar": "sayıştay",   "apex": None},
    "Uyuşmazlık": {"level": 4, "pillar": "uyusmazlik", "apex": None},
    "AİHM":       {"level": 5, "pillar": "aihm",       "apex": None},
    "İlk Derece": {"level": 1, "pillar": "ilk_derece", "apex": None},
    "Tüketici":   {"level": 1, "pillar": "ilk_derece", "apex": None},
}


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


def _derive_parent_court(court: str, daire: str) -> str:
    """Return the static Court node name that is the parent of this daire.

    BAM daireleri MongoDB'de "{Şehir} Bölge Adliye Mahkemesi N. X Dairesi"
    formatındadır; statik hiyerarşide ise "{Şehir} BAM" olarak kayıtlıdır.
    BİM daireleri ise zaten "{Şehir} BİM N. İDD/VDD" formatındadır.
    """
    if court == "BAM":
        m = re.match(r"^(.*?) Bölge Adliye Mahkemesi", daire)
        if m:
            return f"{m.group(1)} BAM"
    elif court == "BİM":
        m = re.match(r"^(.*? BİM)\b", daire)
        if m:
            return m.group(1)
    return court


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
        court = row[1] or ""
        daire = row[2] or ""
        meta  = _COURT_TYPE_META.get(court, {})
        batch.append({
            "doc_id":              row[0],
            "court":               court,
            "daire":               daire,
            "parent_court":        _derive_parent_court(court, daire),
            "parent_court_level":  meta.get("level", 0),
            "parent_court_pillar": meta.get("pillar", ""),
            "apex_court":          meta.get("apex") or "",
            "court_level":         row[3] or 0,
            "esas_no":             row[4] or "",
            "karar_no":            row[5] or "",
            "decision_date":       str(row[6] or ""),
            "law_branch":          row[7] or "",
            "pagerank_score":      float(row[8] or 0.0),
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
        # Step 2: daire Court node'unu oluştur, parent ve apex'e bağla
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
            "WHERE row.parent_court <> '' AND row.parent_court <> row.daire "
            "MERGE (gc:Court {name: row.parent_court}) "
            "ON CREATE SET gc.level  = row.parent_court_level, "
            "              gc.branch = row.law_branch, "
            "              gc.pillar = row.parent_court_pillar "
            "MERGE (dc)-[:PART_OF]->(gc) "
            "WITH gc, row "
            "WHERE row.apex_court <> '' "
            "MERGE (apex:Court {name: row.apex_court}) "
            "ON CREATE SET apex.level  = row.parent_court_level + 2, "
            "              apex.branch = row.law_branch, "
            "              apex.pillar = row.parent_court_pillar "
            "MERGE (gc)-[:APPEALS_TO]->(apex)",
            batch=batch,
        )
        # Step 3: daire boş olan belgeler doğrudan parent court'a bağlanır
        tx.run(
            "UNWIND $batch AS row "
            "WITH row WHERE row.daire = '' AND row.parent_court <> '' "
            "MATCH (d:Document {doc_id: row.doc_id}) "
            "MERGE (gc:Court {name: row.parent_court}) "
            "ON CREATE SET gc.level  = row.parent_court_level, "
            "              gc.branch = row.law_branch, "
            "              gc.pillar = row.parent_court_pillar "
            "MERGE (d)-[:IN_COURT]->(gc)",
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
