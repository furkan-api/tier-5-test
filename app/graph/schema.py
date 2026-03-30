"""
Neo4j schema DDL constants.
Run each statement once to set up constraints and indexes.
All statements use IF NOT EXISTS — safe to re-run.
"""

CONSTRAINT_DOCUMENT_ID = (
    "CREATE CONSTRAINT document_doc_id IF NOT EXISTS "
    "FOR (d:Document) REQUIRE d.doc_id IS UNIQUE"
)

CONSTRAINT_COURT_NAME = (
    "CREATE CONSTRAINT court_name IF NOT EXISTS "
    "FOR (c:Court) REQUIRE c.name IS UNIQUE"
)

INDEX_DOCUMENT_ESAS = (
    "CREATE INDEX document_esas IF NOT EXISTS "
    "FOR (d:Document) ON (d.esas_no)"
)

INDEX_DOCUMENT_COURT_LEVEL = (
    "CREATE INDEX document_court_level IF NOT EXISTS "
    "FOR (d:Document) ON (d.court_level)"
)

INDEX_DOCUMENT_LAW_BRANCH = (
    "CREATE INDEX document_law_branch IF NOT EXISTS "
    "FOR (d:Document) ON (d.law_branch)"
)

ALL_STATEMENTS: list[str] = [
    CONSTRAINT_DOCUMENT_ID,
    CONSTRAINT_COURT_NAME,
    INDEX_DOCUMENT_ESAS,
    INDEX_DOCUMENT_COURT_LEVEL,
    INDEX_DOCUMENT_LAW_BRANCH,
]
