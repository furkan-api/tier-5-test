"""
All Neo4j write/read operations for the legal graph.

All functions take an open neo4j.Session as first argument.
Session management is the caller's responsibility.
"""

from __future__ import annotations

from neo4j import Session

from app.graph import schema as _schema
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

# Court hierarchy data: (name, level, branch, pillar)
# level: 1=İlk Derece, 2=BAM/BİM, 3=Daire, 4=HGK/CGK/İBK
_COURTS = [
    # Adli yargı — Yargıtay
    ("Yargıtay",            4, "adli",  "yargıtay"),
    ("Yargıtay HGK",        4, "hukuk", "yargıtay"),
    ("Yargıtay CGK",        4, "ceza",  "yargıtay"),
    ("Yargıtay 1. HD",      3, "hukuk", "yargıtay"),
    ("Yargıtay 2. HD",      3, "hukuk", "yargıtay"),
    ("Yargıtay 3. HD",      3, "hukuk", "yargıtay"),
    ("Yargıtay 4. HD",      3, "hukuk", "yargıtay"),
    ("Yargıtay 5. HD",      3, "hukuk", "yargıtay"),
    ("Yargıtay 6. HD",      3, "hukuk", "yargıtay"),
    ("Yargıtay 7. HD",      3, "hukuk", "yargıtay"),
    ("Yargıtay 8. HD",      3, "hukuk", "yargıtay"),
    ("Yargıtay 9. HD",      3, "hukuk", "yargıtay"),
    ("Yargıtay 10. HD",     3, "hukuk", "yargıtay"),
    ("Yargıtay 11. HD",     3, "hukuk", "yargıtay"),
    ("Yargıtay 12. HD",     3, "hukuk", "yargıtay"),
    ("Yargıtay 1. CD",      3, "ceza",  "yargıtay"),
    ("Yargıtay 2. CD",      3, "ceza",  "yargıtay"),
    ("Yargıtay 3. CD",      3, "ceza",  "yargıtay"),
    ("Yargıtay 4. CD",      3, "ceza",  "yargıtay"),
    ("Yargıtay 5. CD",      3, "ceza",  "yargıtay"),
    ("Yargıtay 6. CD",      3, "ceza",  "yargıtay"),
    ("Yargıtay 7. CD",      3, "ceza",  "yargıtay"),
    ("Yargıtay 8. CD",      3, "ceza",  "yargıtay"),
    ("Yargıtay 9. CD",      3, "ceza",  "yargıtay"),
    ("Yargıtay 10. CD",     3, "ceza",  "yargıtay"),
    ("Yargıtay 11. CD",     3, "ceza",  "yargıtay"),
    ("Yargıtay 12. CD",     3, "ceza",  "yargıtay"),
    # Adli yargı — BAM (representative selection; all 17 operational)
    ("İstanbul BAM",        2, "adli",  "bam"),
    ("Ankara BAM",          2, "adli",  "bam"),
    ("İzmir BAM",           2, "adli",  "bam"),
    ("Bursa BAM",           2, "adli",  "bam"),
    ("Antalya BAM",         2, "adli",  "bam"),
    ("Samsun BAM",          2, "adli",  "bam"),
    ("Konya BAM",           2, "adli",  "bam"),
    ("Gaziantep BAM",       2, "adli",  "bam"),
    ("Erzurum BAM",         2, "adli",  "bam"),
    ("Diyarbakır BAM",      2, "adli",  "bam"),
    ("Sakarya BAM",         2, "adli",  "bam"),
    ("Trabzon BAM",         2, "adli",  "bam"),
    ("Adana BAM",           2, "adli",  "bam"),
    ("Kayseri BAM",         2, "adli",  "bam"),
    ("Van BAM",             2, "adli",  "bam"),
    ("Denizli BAM",         2, "adli",  "bam"),
    ("Tekirdağ BAM",        2, "adli",  "bam"),
    # Adli yargı — İlk derece (representative types)
    ("Asliye Hukuk Mahkemesi",  1, "hukuk", "ilk_derece"),
    ("Asliye Ceza Mahkemesi",   1, "ceza",  "ilk_derece"),
    ("Ağır Ceza Mahkemesi",     1, "ceza",  "ilk_derece"),
    ("İş Mahkemesi",            1, "hukuk", "ilk_derece"),
    ("Aile Mahkemesi",          1, "hukuk", "ilk_derece"),
    ("Asliye Ticaret Mahkemesi",1, "hukuk", "ilk_derece"),
    # İdari yargı — Danıştay
    ("Danıştay",            4, "idari",  "danıştay"),
    ("Danıştay İDDGK",      4, "idari",  "danıştay"),
    ("Danıştay VDDK",       4, "vergi",  "danıştay"),
    ("Danıştay 1. D",       3, "idari",  "danıştay"),
    ("Danıştay 2. D",       3, "idari",  "danıştay"),
    ("Danıştay 3. D",       3, "vergi",  "danıştay"),
    ("Danıştay 4. D",       3, "vergi",  "danıştay"),
    ("Danıştay 5. D",       3, "idari",  "danıştay"),
    ("Danıştay 6. D",       3, "idari",  "danıştay"),
    ("Danıştay 7. D",       3, "vergi",  "danıştay"),
    ("Danıştay 8. D",       3, "idari",  "danıştay"),
    ("Danıştay 9. D",       3, "vergi",  "danıştay"),
    ("Danıştay 10. D",      3, "idari",  "danıştay"),
    ("Danıştay 11. D",      3, "idari",  "danıştay"),
    ("Danıştay 12. D",      3, "idari",  "danıştay"),
    ("Danıştay 13. D",      3, "idari",  "danıştay"),
    ("Danıştay 14. D",      3, "idari",  "danıştay"),
    ("Danıştay 15. D",      3, "idari",  "danıştay"),
    # İdari yargı — BİM
    ("İstanbul BİM",        2, "idari",  "bim"),
    ("Ankara BİM",          2, "idari",  "bim"),
    ("İzmir BİM",           2, "idari",  "bim"),
    # İdari yargı — İlk derece
    ("İdare Mahkemesi",     1, "idari",  "ilk_derece"),
    ("Vergi Mahkemesi",     1, "vergi",  "ilk_derece"),
    # Anayasa Mahkemesi
    ("Anayasa Mahkemesi",   4, "anayasa", "aym"),
]

# Appeals-to edges: (lower_court_name, higher_court_name)
_APPEALS_TO = [
    # Adli yargı appeal chain
    ("Asliye Hukuk Mahkemesi",      "İstanbul BAM"),
    ("Asliye Ceza Mahkemesi",       "İstanbul BAM"),
    ("Ağır Ceza Mahkemesi",         "İstanbul BAM"),
    ("İş Mahkemesi",                "İstanbul BAM"),
    ("Aile Mahkemesi",              "İstanbul BAM"),
    ("Asliye Ticaret Mahkemesi",    "İstanbul BAM"),
    ("İstanbul BAM",    "Yargıtay"),
    ("Ankara BAM",      "Yargıtay"),
    ("İzmir BAM",       "Yargıtay"),
    ("Bursa BAM",       "Yargıtay"),
    ("Antalya BAM",     "Yargıtay"),
    ("Samsun BAM",      "Yargıtay"),
    ("Konya BAM",       "Yargıtay"),
    ("Gaziantep BAM",   "Yargıtay"),
    ("Erzurum BAM",     "Yargıtay"),
    ("Diyarbakır BAM",  "Yargıtay"),
    ("Sakarya BAM",     "Yargıtay"),
    ("Trabzon BAM",     "Yargıtay"),
    ("Adana BAM",       "Yargıtay"),
    ("Kayseri BAM",     "Yargıtay"),
    ("Van BAM",         "Yargıtay"),
    ("Denizli BAM",     "Yargıtay"),
    ("Tekirdağ BAM",    "Yargıtay"),
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
    # İdari yargı appeal chain
    ("İdare Mahkemesi",  "İstanbul BİM"),
    ("Vergi Mahkemesi",  "İstanbul BİM"),
    ("İstanbul BİM",    "Danıştay"),
    ("Ankara BİM",      "Danıştay"),
    ("İzmir BİM",       "Danıştay"),
    ("Danıştay 1. D",   "Danıştay İDDGK"),
    ("Danıştay 2. D",   "Danıştay İDDGK"),
    ("Danıştay 5. D",   "Danıştay İDDGK"),
    ("Danıştay 6. D",   "Danıştay İDDGK"),
    ("Danıştay 8. D",   "Danıştay İDDGK"),
    ("Danıştay 10. D",  "Danıştay İDDGK"),
    ("Danıştay 11. D",  "Danıştay İDDGK"),
    ("Danıştay 12. D",  "Danıştay İDDGK"),
    ("Danıştay 13. D",  "Danıştay İDDGK"),
    ("Danıştay 14. D",  "Danıştay İDDGK"),
    ("Danıştay 15. D",  "Danıştay İDDGK"),
    ("Danıştay 3. D",   "Danıştay VDDK"),
    ("Danıştay 4. D",   "Danıştay VDDK"),
    ("Danıştay 7. D",   "Danıştay VDDK"),
    ("Danıştay 9. D",   "Danıştay VDDK"),
]


def upsert_court_hierarchy(session: Session) -> None:
    """Create or update all Court nodes and APPEALS_TO relationships."""
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
# Document nodes
# ---------------------------------------------------------------------------

_BATCH_SIZE = 500


def upsert_documents(session: Session, conn) -> int:
    """
    Batch-upsert Document nodes from PostgreSQL.
    Also creates IN_COURT edges to Court nodes.
    Returns count of documents upserted.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT doc_id, court, daire, court_level, esas_no, karar_no, "
            "decision_date, law_branch, pagerank_score "
            "FROM documents"
        )
        rows = cur.fetchall()

    total = 0
    batch = []
    for row in rows:
        batch.append({
            "doc_id":        row[0],
            "court":         row[1] or "",
            "daire":         row[2] or "",
            "court_level":   row[3] or 0,
            "esas_no":       row[4] or "",
            "karar_no":      row[5] or "",
            "decision_date": row[6] or "",
            "law_branch":    row[7] or "",
            "pagerank_score": float(row[8] or 0.0),
        })
        if len(batch) >= _BATCH_SIZE:
            _upsert_doc_batch(session, batch)
            total += len(batch)
            batch = []
    if batch:
        _upsert_doc_batch(session, batch)
        total += len(batch)
    return total


def _upsert_doc_batch(session: Session, batch: list[dict]) -> None:
    session.run(
        "UNWIND $batch AS row "
        "MERGE (d:Document {doc_id: row.doc_id}) "
        "SET d.court = row.court, d.daire = row.daire, "
        "    d.court_level = row.court_level, d.esas_no = row.esas_no, "
        "    d.karar_no = row.karar_no, d.decision_date = row.decision_date, "
        "    d.law_branch = row.law_branch, d.pagerank_score = row.pagerank_score "
        "WITH d, row "
        "MATCH (c:Court) WHERE c.name = row.court OR c.name CONTAINS row.daire "
        "MERGE (d)-[:IN_COURT]->(c)",
        batch=batch,
    )


# ---------------------------------------------------------------------------
# Citation relationships
# ---------------------------------------------------------------------------

def upsert_citations(session: Session, resolved: list[ResolvedCitation]) -> int:
    """
    Batch-upsert CITES relationships from resolved citations.
    Returns count of relationships upserted.
    """
    if not resolved:
        return 0
    batch = [
        {
            "source": c.source_doc_id,
            "target": c.target_doc_id,
            "confidence": c.confidence,
            "snippet": c.snippet[:500],  # truncate for storage
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
    In appellate law, being cited is as important as citing.

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
