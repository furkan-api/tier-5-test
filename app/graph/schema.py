"""
Neo4j schema DDL constants.
Run each statement once to set up constraints and indexes.
All statements use IF NOT EXISTS — safe to re-run.
"""

# ---------------------------------------------------------------------------
# Document constraints & indexes
# ---------------------------------------------------------------------------

CONSTRAINT_DOCUMENT_ID = (
    "CREATE CONSTRAINT document_doc_id IF NOT EXISTS "
    "FOR (d:Document) REQUIRE d.doc_id IS UNIQUE"
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

# ---------------------------------------------------------------------------
# Court constraints & indexes
# ---------------------------------------------------------------------------

CONSTRAINT_COURT_NAME = (
    "CREATE CONSTRAINT court_name IF NOT EXISTS "
    "FOR (c:Court) REQUIRE c.name IS UNIQUE"
)

# ---------------------------------------------------------------------------
# Law entity constraints & indexes
# ---------------------------------------------------------------------------

CONSTRAINT_LAW_CODE = (
    "CREATE CONSTRAINT law_code IF NOT EXISTS "
    "FOR (l:Law) REQUIRE l.code IS UNIQUE"
)

CONSTRAINT_LAW_ARTICLE_ID = (
    "CREATE CONSTRAINT law_article_id IF NOT EXISTS "
    "FOR (a:LawArticle) REQUIRE a.id IS UNIQUE"
)

CONSTRAINT_LEGAL_BRANCH_NAME = (
    "CREATE CONSTRAINT legal_branch_name IF NOT EXISTS "
    "FOR (b:LegalBranch) REQUIRE b.name IS UNIQUE"
)

INDEX_LAW_ARTICLE_CODE = (
    "CREATE INDEX law_article_code IF NOT EXISTS "
    "FOR (a:LawArticle) ON (a.code)"
)

INDEX_LAW_ARTICLE_ARTICLE = (
    "CREATE INDEX law_article_number IF NOT EXISTS "
    "FOR (a:LawArticle) ON (a.article)"
)

# ---------------------------------------------------------------------------
# All statements (idempotent, safe to re-run)
# ---------------------------------------------------------------------------

ALL_STATEMENTS: list[str] = [
    CONSTRAINT_DOCUMENT_ID,
    CONSTRAINT_COURT_NAME,
    CONSTRAINT_LAW_CODE,
    CONSTRAINT_LAW_ARTICLE_ID,
    CONSTRAINT_LEGAL_BRANCH_NAME,
    INDEX_DOCUMENT_ESAS,
    INDEX_DOCUMENT_COURT_LEVEL,
    INDEX_DOCUMENT_LAW_BRANCH,
    INDEX_LAW_ARTICLE_CODE,
    INDEX_LAW_ARTICLE_ARTICLE,
]
