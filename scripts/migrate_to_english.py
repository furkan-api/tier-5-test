#!/usr/bin/env python3
"""One-shot migration: rename all Turkish identifiers to English.

Transforms:
  - graph_data/nodes/*.jsonl  (node_type, metadata field names, enum values)
  - graph_data/edges/structural_edges.jsonl  (edge_type)
  - graph_data/edges/edge_rules.json
  - graph_data/validation/schema.json
  - graph_data/ontology.json
  - Renames JSONL files to English names

Run from repo root:
    python scripts/migrate_to_english.py
"""

import json, os, pathlib, shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
GD = ROOT / "graph_data"

# ═══════════════════════════════════════════════════════════════════════════════
# MAPPING TABLES
# ═══════════════════════════════════════════════════════════════════════════════

NODE_TYPE_MAP = {
    "kanun": "law",
    "madde": "article",
    "madde_versiyon": "article_version",
    "fikra": "paragraph",
    "bent": "clause",
    "karar": "decision",
    "karar_gerekce": "decision_rationale",
    "karar_bolum": "decision_chunk",
    "mahkeme": "court",
    "hukuk_dali": "legal_branch",
    "kavram": "concept",
}

EDGE_TYPE_MAP = {
    "ICERIR": "CONTAINS",
    "UST_NODE": "PARENT_NODE",
    "VERSIYONU": "VERSION_OF",
    "ATIF_YAPAR": "REFERENCES",
    "GEREKCESI": "RATIONALE_OF",
    "KANUN_YOLU": "APPEAL_CHAIN",
    "VERILDI": "DECIDED_BY",
    "HUKUK_DALI": "LEGAL_BRANCH",
    "UST_DAL": "PARENT_BRANCH",
    "AYNI_KANUN": "SAME_LAW",
    "ILGILI_KAVRAM": "RELATED_CONCEPT",
    "CELISIK_KARAR": "CONFLICTING_DECISION",
    "SEMANTIK_BENZER": "SEMANTIC_SIMILAR",
}

# Metadata field name mapping (Turkish → English)
META_FIELD_MAP = {
    # Law fields
    "kanun_adi": "law_name",
    "kisaltma": "abbreviation",
    "kanun_no": "law_number",
    "kabul_tarihi": "adoption_date",
    "yururluk_tarihi": "effective_date",
    "resmi_gazete_tarihi": "official_gazette_date",
    "resmi_gazete_sayisi": "official_gazette_number",
    "alan": "domain",
    "kaynak_dosya": "source_file",
    "yururlukten_kaldirdigini_kanun": "repealed_law",
    # Article fields
    "kanun_kisaltma": "law_abbreviation",
    "madde_no": "article_number",
    "madde_basligi": "article_title",
    "mulga_mi": "is_repealed",
    "mulga_tarihi": "repeal_date",
    "onceki_duzenleme": "previous_regulation",
    "konu": "subject",
    "hukuk_dali": "legal_branch",
    "fikralar": "paragraphs",
    # Article version fields
    "gecerlilik_bitis_tarihi": "validity_end_date",
    "degistiren_kanun": "amending_law",
    "versiyon": "version",
    # Paragraph fields
    "fikra_no": "paragraph_number",
    "ust_node": "parent_node",
    # Clause fields
    "bent_no": "clause_number",
    # Decision fields
    "mahkeme_turu": "court_type",
    "mahkeme_adi": "court_name",
    "esas_no": "docket_number",
    "karar_no": "decision_number",
    "karar_tarihi": "decision_date",
    "sonuc": "outcome",
    "emsal_niteligi": "precedent_status",
    "atif_yapilan_kanunlar": "referenced_laws",
    "atif_yapilan_kararlar": "referenced_decisions",
    "ilk_derece_ref": "first_instance_ref",
    "bam_ref": "appeals_court_ref",
    "kanun_yolu": "appeal_path",
    "daire": "chamber",
    "bolumler": "chunks",
    # Decision rationale fields
    "ust_karar": "parent_decision",
    "gerekce_turu": "rationale_type",
    "anahtar_ilkeler": "key_principles",
    # Decision chunk fields
    "bolum_turu": "chunk_type",
    "bolum_sira": "chunk_order",
    "bolum_basligi": "chunk_title",
    "karakter_sayisi": "char_count",
    # Court fields
    "sehir": "city",
    "uzmanlik_alani": "specialization",
    # Legal branch fields
    "dal_adi": "branch_name",
    "ust_dal": "parent_branch",
    "aciklama": "description",
    "anahtar_kanunlar": "key_laws",
    # Concept fields
    "kavram_adi": "concept_name",
    "tanim": "definition",
    "esanlamlilar": "synonyms",
    "ilgili_kanunlar": "related_laws",
}

# Inner enum value mappings
COURT_TYPE_MAP = {
    "ilk_derece": "first_instance",
    "bam_istinaf": "appellate",
    "yargitay": "supreme_court",
    "anayasa_mahkemesi": "constitutional_court",
}

RATIONALE_TYPE_MAP = {
    "esasa_iliskin": "substantive",
    "istinaf_denetimi": "appellate_review",
    "temyiz_denetimi": "cassation_review",
    "usule_iliskin": "procedural",
}

CHUNK_TYPE_MAP = {
    "olay_ozeti": "case_summary",
    "taraf_iddialari": "party_claims",
    "delil_degerlendirme": "evidence_evaluation",
    "hukuki_degerlendirme": "legal_evaluation",
    "sonuc_ve_hukum": "conclusion_and_judgment",
    "alt_mahkeme_karari": "lower_court_decision",
    "temyiz_sebepleri": "cassation_grounds",
    "bam_degerlendirme": "appellate_evaluation",
}

REFERENCE_TYPE_MAP = {
    "kanun_maddesi": "law_article",
    "emsal_karar": "precedent_decision",
    "genel_referans": "general_reference",
}

APPEAL_PATH_TYPE_MAP = {
    "istinaf": "appeal",
    "temyiz": "cassation",
}

FILE_RENAME_MAP = {
    "kanunlar.jsonl": "laws.jsonl",
    "maddeler.jsonl": "articles.jsonl",
    "kararlar.jsonl": "decisions.jsonl",
    "mahkemeler.jsonl": "courts.jsonl",
    "hukuk_dallari.jsonl": "legal_branches.jsonl",
    "kavramlar.jsonl": "concepts.jsonl",
    "structural_edges.jsonl": "structural_edges.jsonl",  # keep same
}

# Fields whose values should be mapped through specific enum maps
ENUM_FIELD_MAPS = {
    "court_type": COURT_TYPE_MAP,       # after renaming mahkeme_turu
    "rationale_type": RATIONALE_TYPE_MAP,  # after renaming gerekce_turu
    "chunk_type": CHUNK_TYPE_MAP,        # after renaming bolum_turu
}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def migrate_metadata(meta: dict) -> dict:
    """Rename metadata keys and map enum values."""
    new = {}
    for k, v in meta.items():
        new_key = META_FIELD_MAP.get(k, k)
        # Recurse into nested dicts (e.g. repealed_law)
        if isinstance(v, dict):
            v = migrate_metadata(v)
        new[new_key] = v

    # Map enum values for specific fields
    for field, emap in ENUM_FIELD_MAPS.items():
        if field in new and isinstance(new[field], str):
            new[field] = emap.get(new[field], new[field])

    return new


def migrate_node(node: dict) -> dict:
    """Migrate a single node dict."""
    out = {}
    for k, v in node.items():
        if k == "node_type":
            out[k] = NODE_TYPE_MAP.get(v, v)
        elif k == "metadata":
            out[k] = migrate_metadata(v)
        else:
            out[k] = v
    return out


def migrate_edge(edge: dict) -> dict:
    """Migrate a single edge dict."""
    out = dict(edge)
    if "edge_type" in out:
        out["edge_type"] = EDGE_TYPE_MAP.get(out["edge_type"], out["edge_type"])
    return out


def read_jsonl(path: pathlib.Path) -> list:
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def write_jsonl(path: pathlib.Path, items: list):
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════════════════════════════
# MIGRATE NODE FILES
# ═══════════════════════════════════════════════════════════════════════════════

def migrate_node_files():
    nodes_dir = GD / "nodes"
    for old_name, new_name in FILE_RENAME_MAP.items():
        if old_name == "structural_edges.jsonl":
            continue
        old_path = nodes_dir / old_name
        if not old_path.exists():
            print(f"  SKIP (not found): {old_path}")
            continue
        items = read_jsonl(old_path)
        migrated = [migrate_node(n) for n in items]
        new_path = nodes_dir / new_name
        write_jsonl(new_path, migrated)
        if old_name != new_name:
            old_path.unlink()
        print(f"  {old_name} → {new_name}  ({len(migrated)} nodes)")


# ═══════════════════════════════════════════════════════════════════════════════
# MIGRATE EDGE FILES
# ═══════════════════════════════════════════════════════════════════════════════

def migrate_edge_files():
    edges_dir = GD / "edges"
    se_path = edges_dir / "structural_edges.jsonl"
    if not se_path.exists():
        print("  SKIP: structural_edges.jsonl not found")
        return
    items = read_jsonl(se_path)
    migrated = [migrate_edge(e) for e in items]
    write_jsonl(se_path, migrated)
    print(f"  structural_edges.jsonl  ({len(migrated)} edges)")


# ═══════════════════════════════════════════════════════════════════════════════
# REWRITE edge_rules.json
# ═══════════════════════════════════════════════════════════════════════════════

def migrate_edge_rules():
    path = GD / "edges" / "edge_rules.json"
    new_rules = {
        "description": "Dynamic edge rules — computed during build based on conditions. Static edges are in edges/structural_edges.jsonl.",
        "version": "1.0.0",
        "rules": [
            {
                "rule_id": "same_law_articles",
                "edge_type": "SAME_LAW",
                "description": "Weak edges between articles belonging to the same law",
                "source_node_type": "article",
                "target_node_type": "article",
                "condition": {
                    "type": "metadata_match",
                    "source_field": "metadata.law_number",
                    "target_field": "metadata.law_number"
                },
                "weight": 0.3,
                "bidirectional": True
            },
            {
                "rule_id": "conflicting_decisions",
                "edge_type": "CONFLICTING_DECISION",
                "description": "Conflict edges between decisions in the same legal branch reaching opposite outcomes",
                "source_node_type": "decision",
                "target_node_type": "decision",
                "condition": {
                    "type": "contradictory_decisions",
                    "similarity_threshold": 0.55
                },
                "weight": 0.8,
                "bidirectional": True
            },
            {
                "rule_id": "same_decision_chunks",
                "edge_type": "SAME_LAW",
                "description": "Weak edges between chunks of the same decision — enables inter-chunk graph traversal",
                "source_node_type": "decision_chunk",
                "target_node_type": "decision_chunk",
                "condition": {
                    "type": "metadata_match",
                    "source_field": "metadata.parent_decision",
                    "target_field": "metadata.parent_decision"
                },
                "weight": 0.4,
                "bidirectional": True
            },
            {
                "rule_id": "semantic_similarity",
                "edge_type": "SEMANTIC_SIMILAR",
                "description": "Nodes with high cosine similarity between embedding vectors — runs post-build on Neo4j vector index",
                "source_node_type": "*",
                "target_node_type": "*",
                "condition": {
                    "type": "cosine_similarity",
                    "threshold": 0.82,
                    "max_neighbors": 5,
                    "exclude_self": True,
                    "exclude_existing_edges": True
                },
                "weight_from_similarity": True,
                "bidirectional": True
            }
        ]
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(new_rules, f, indent=2, ensure_ascii=False)
    print(f"  edge_rules.json rewritten")


# ═══════════════════════════════════════════════════════════════════════════════
# REWRITE schema.json
# ═══════════════════════════════════════════════════════════════════════════════

def migrate_schema():
    path = GD / "validation" / "schema.json"
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Apilex Graph Node Validation Schema",
        "description": "JSON Schema for validating graph node data files before ingestion.",
        "type": "array",
        "items": {"$ref": "#/definitions/node"},
        "definitions": {
            "node": {
                "type": "object",
                "required": ["node_id", "node_type", "embed_text"],
                "properties": {
                    "node_id": {
                        "type": "string",
                        "minLength": 1,
                        "pattern": "^[A-Za-z0-9_İÖÜŞÇĞ]+$",
                        "description": "Unique node identifier"
                    },
                    "node_type": {
                        "type": "string",
                        "enum": sorted(NODE_TYPE_MAP.values())
                    },
                    "embed_text": {
                        "type": "string",
                        "minLength": 5,
                        "description": "Semantic text for vector search — must not be empty"
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Additional metadata for the node"
                    }
                },
                "additionalProperties": True
            },
            "edge": {
                "type": "object",
                "required": ["edge_id", "source", "target", "edge_type"],
                "properties": {
                    "edge_id": {
                        "type": "string",
                        "minLength": 1
                    },
                    "source": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Source node's node_id"
                    },
                    "target": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Target node's node_id"
                    },
                    "edge_type": {
                        "type": "string",
                        "enum": sorted(EDGE_TYPE_MAP.values())
                    },
                    "weight": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "default": 1.0
                    },
                    "properties": {
                        "type": "object"
                    }
                },
                "additionalProperties": True
            }
        }
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)
    print(f"  schema.json rewritten")


# ═══════════════════════════════════════════════════════════════════════════════
# REWRITE ontology.json
# ═══════════════════════════════════════════════════════════════════════════════

def migrate_ontology():
    path = GD / "ontology.json"
    ontology = {
        "$schema": "https://apilex.ai/ontology/v1",
        "version": "2.0.0",
        "name": "Apilex Legal Knowledge Graph Ontology",
        "description": "Production-grade knowledge graph ontology for the Turkish legal system. Defines relationships between laws, articles, court decisions, courts, legal branches, and concepts.",
        "domain": "turkish_law",
        "language": "tr",
        "created_at": "2026-03-02",
        "updated_at": "2026-03-02",

        "node_types": {
            "Law": {
                "label": "Law",
                "description": "Legislation such as a law, decree, or regulation",
                "color": "#2563EB",
                "icon": "book",
                "properties": {
                    "node_id":              {"type": "string",   "required": True,  "unique": True, "description": "Unique node identifier, e.g. kanun_tbk_6098"},
                    "node_type":            {"type": "string",   "required": True,  "enum": ["law"]},
                    "embed_text":           {"type": "string",   "required": True,  "indexed": True, "description": "Semantic text for vector search"},
                    "law_name":             {"type": "string",   "required": True,  "description": "Full name of the law"},
                    "abbreviation":         {"type": "string",   "required": True,  "description": "Short name (TBK, TMK, IK, HMK)"},
                    "law_number":           {"type": "integer",  "required": True,  "description": "Law number"},
                    "adoption_date":        {"type": "date",     "required": True,  "description": "Parliament adoption date"},
                    "effective_date":       {"type": "date",     "required": True,  "description": "Effective date"},
                    "official_gazette_date": {"type": "date",    "required": False},
                    "official_gazette_number": {"type": "integer", "required": False},
                    "domain":               {"type": "string[]", "required": False, "description": "Legal domains covered by the law"},
                    "source_file":          {"type": "string",   "required": False, "description": "Raw data source file path"}
                },
                "embed_template": "{law_name} law number {law_number} effective date {effective_date} {domain_join}"
            },

            "Article": {
                "label": "Article",
                "description": "A specific article provision of a law",
                "color": "#059669",
                "icon": "paragraph",
                "properties": {
                    "node_id":              {"type": "string",  "required": True,  "unique": True, "description": "E.g. TBK_M1, HMK_M2"},
                    "node_type":            {"type": "string",  "required": True,  "enum": ["article"]},
                    "embed_text":           {"type": "string",  "required": True,  "indexed": True},
                    "law_number":           {"type": "integer", "required": True},
                    "law_abbreviation":     {"type": "string",  "required": True},
                    "article_number":       {"type": "integer", "required": True},
                    "article_title":        {"type": "string",  "required": False},
                    "effective_date":       {"type": "date",    "required": True},
                    "is_repealed":          {"type": "boolean", "required": True, "default": False},
                    "repeal_date":          {"type": "date",    "required": False},
                    "previous_regulation":  {"type": "string",  "required": False, "description": "Reference to the repealed former law"},
                    "subject":              {"type": "string",  "required": False, "description": "Subject summary of the article"},
                    "legal_branch":         {"type": "string",  "required": False}
                },
                "embed_template": "{law_abbreviation} article {article_number} {article_title} {subject}"
            },

            "ArticleVersion": {
                "label": "ArticleVersion",
                "description": "A specific version of an amended article within a date range",
                "color": "#D97706",
                "icon": "history",
                "properties": {
                    "node_id":              {"type": "string",  "required": True, "unique": True, "description": "E.g. HMK_M3_V1, HMK_M3_V2"},
                    "node_type":            {"type": "string",  "required": True, "enum": ["article_version"]},
                    "embed_text":           {"type": "string",  "required": True, "indexed": True},
                    "law_number":           {"type": "integer", "required": True},
                    "law_abbreviation":     {"type": "string",  "required": True},
                    "article_number":       {"type": "integer", "required": True},
                    "version":              {"type": "integer", "required": True},
                    "article_title":        {"type": "string",  "required": False},
                    "effective_date":       {"type": "date",    "required": True},
                    "validity_end_date":    {"type": "date",    "required": False},
                    "is_repealed":          {"type": "boolean", "required": True},
                    "amending_law":         {"type": "string",  "required": False},
                    "subject":              {"type": "string",  "required": False},
                    "legal_branch":         {"type": "string",  "required": False}
                },
                "embed_template": "{law_abbreviation} article {article_number} version {version} {subject} {effective_date}"
            },

            "Paragraph": {
                "label": "Paragraph",
                "description": "A sub-paragraph of an article — the smallest meaningful unit of law text",
                "color": "#7C3AED",
                "icon": "text",
                "properties": {
                    "node_id":              {"type": "string",  "required": True,  "unique": True, "description": "E.g. TBK_M1_F1"},
                    "node_type":            {"type": "string",  "required": True,  "enum": ["paragraph"]},
                    "embed_text":           {"type": "string",  "required": True,  "indexed": True, "description": "Full text of the paragraph"},
                    "law_number":           {"type": "integer", "required": True},
                    "law_abbreviation":     {"type": "string",  "required": True},
                    "article_number":       {"type": "integer", "required": True},
                    "paragraph_number":     {"type": "integer", "required": True},
                    "effective_date":       {"type": "date",    "required": True},
                    "is_repealed":          {"type": "boolean", "required": True, "default": False},
                    "subject":              {"type": "string",  "required": False},
                    "legal_branch":         {"type": "string",  "required": False},
                    "parent_node":          {"type": "string",  "required": True, "description": "Parent article or article_version node_id"}
                },
                "embed_template": "{embed_text}"
            },

            "Clause": {
                "label": "Clause",
                "description": "A sub-clause of a paragraph (a, b, c or 1, 2, 3 ordered provisions)",
                "color": "#6366F1",
                "icon": "list",
                "properties": {
                    "node_id":              {"type": "string",  "required": True,  "unique": True},
                    "node_type":            {"type": "string",  "required": True,  "enum": ["clause"]},
                    "embed_text":           {"type": "string",  "required": True,  "indexed": True},
                    "law_number":           {"type": "integer", "required": True},
                    "law_abbreviation":     {"type": "string",  "required": True},
                    "article_number":       {"type": "integer", "required": True},
                    "paragraph_number":     {"type": "integer", "required": True},
                    "clause_number":        {"type": "string",  "required": True},
                    "parent_node":          {"type": "string",  "required": True}
                },
                "embed_template": "{embed_text}"
            },

            "Decision": {
                "label": "Decision",
                "description": "Court decision — first instance, appellate (BAM), or cassation (Supreme Court)",
                "color": "#DC2626",
                "icon": "gavel",
                "properties": {
                    "node_id":              {"type": "string",  "required": True,  "unique": True},
                    "node_type":            {"type": "string",  "required": True,  "enum": ["decision"]},
                    "embed_text":           {"type": "string",  "required": True,  "indexed": True},
                    "court_type":           {"type": "string",  "required": True,  "enum": ["first_instance", "appellate", "supreme_court"], "description": "Court tier"},
                    "court_name":           {"type": "string",  "required": True},
                    "docket_number":        {"type": "string",  "required": True},
                    "decision_number":      {"type": "string",  "required": True},
                    "decision_date":        {"type": "date",    "required": True},
                    "subject":              {"type": "string",  "required": True},
                    "outcome":              {"type": "string",  "required": True, "description": "Decision outcome: AFFIRM, REVERSE, ACCEPT, REJECT, etc."},
                    "legal_branch":         {"type": "string",  "required": False},
                    "precedent_status":     {"type": "string",  "required": False},
                    "referenced_laws":      {"type": "string[]", "required": False, "description": "Law/Article references, e.g. TMK/706"},
                    "referenced_decisions": {"type": "string[]", "required": False, "description": "Other decision references"},
                    "first_instance_ref":   {"type": "string",  "required": False, "description": "First instance decision reference (for appeal/cassation)"},
                    "appeals_court_ref":    {"type": "string",  "required": False, "description": "Appellate court decision reference (for cassation)"},
                    "appeal_path":          {"type": "string",  "required": False, "description": "Appeal path status"}
                },
                "embed_template": "{court_name} {docket_number} {subject} {outcome}"
            },

            "DecisionRationale": {
                "label": "DecisionRationale",
                "description": "Legal reasoning text of a court decision — separate node for better semantic retrieval in Graph RAG",
                "color": "#F59E0B",
                "icon": "file-text",
                "properties": {
                    "node_id":              {"type": "string",  "required": True, "unique": True},
                    "node_type":            {"type": "string",  "required": True, "enum": ["decision_rationale"]},
                    "embed_text":           {"type": "string",  "required": True, "indexed": True, "description": "Full rationale text"},
                    "parent_decision":      {"type": "string",  "required": True, "description": "node_id of the parent decision"},
                    "rationale_type":       {"type": "string",  "required": True, "enum": ["substantive", "appellate_review", "cassation_review", "procedural"]},
                    "key_principles":       {"type": "string[]", "required": False, "description": "Key legal principles in the rationale"},
                    "referenced_laws":      {"type": "string[]", "required": False},
                    "referenced_decisions": {"type": "string[]", "required": False},
                    "legal_branch":         {"type": "string",  "required": False}
                },
                "embed_template": "{embed_text}"
            },

            "DecisionChunk": {
                "label": "DecisionChunk",
                "description": "Semantic section/chunk of a court decision. Long decision texts are split into meaningful sections, each stored as a separate node for more precise semantic retrieval in Graph RAG. The Decision→DecisionChunk hierarchy works like the Law→Article→Paragraph hierarchy.",
                "color": "#EA580C",
                "icon": "file-text-split",
                "properties": {
                    "node_id":              {"type": "string",  "required": True, "unique": True, "description": "E.g. KARAR_YRG_1HD_2013_11205_B1"},
                    "node_type":            {"type": "string",  "required": True, "enum": ["decision_chunk"]},
                    "embed_text":           {"type": "string",  "required": True, "indexed": True, "description": "Chunk text — vectorized for semantic search"},
                    "parent_decision":      {"type": "string",  "required": True, "description": "node_id of the parent decision"},
                    "chunk_type":           {"type": "string",  "required": True, "enum": ["case_summary", "party_claims", "evidence_evaluation", "legal_evaluation", "conclusion_and_judgment", "lower_court_decision", "cassation_grounds", "appellate_evaluation"], "description": "Chunk type"},
                    "chunk_order":          {"type": "integer", "required": True, "description": "Order number within the decision (starts at 1)"},
                    "chunk_title":          {"type": "string",  "required": False, "description": "Chunk title (if any)"},
                    "key_principles":       {"type": "string[]", "required": False, "description": "Key legal principles in the chunk"},
                    "referenced_laws":      {"type": "string[]", "required": False},
                    "referenced_decisions": {"type": "string[]", "required": False},
                    "legal_branch":         {"type": "string",  "required": False},
                    "char_count":           {"type": "integer", "required": False, "description": "Chunk text length (characters)"}
                },
                "embed_template": "{embed_text}",
                "chunking_notes": "In real data, decision texts can be 5,000-50,000+ characters. Each chunk targets ~500-2000 characters, split at semantic boundaries. The same approach can be applied to law texts when needed (long paragraphs or detailed explanations)."
            },

            "Court": {
                "label": "Court",
                "description": "Judicial body / court institution — independent node for linking with decisions",
                "color": "#0891B2",
                "icon": "building",
                "properties": {
                    "node_id":              {"type": "string",  "required": True, "unique": True, "description": "E.g. MAHKEME_YRG_9HD — unique court identifier"},
                    "node_type":            {"type": "string",  "required": True, "enum": ["court"]},
                    "embed_text":           {"type": "string",  "required": True, "indexed": True},
                    "court_name":           {"type": "string",  "required": True},
                    "court_type":           {"type": "string",  "required": True, "enum": ["first_instance", "appellate", "supreme_court", "constitutional_court"]},
                    "city":                 {"type": "string",  "required": False},
                    "chamber":              {"type": "string",  "required": False},
                    "specialization":       {"type": "string[]", "required": False, "description": "Specialization areas such as civil, labor, family, commercial"}
                },
                "embed_template": "{court_name} {city} {specialization_join}"
            },

            "LegalBranch": {
                "label": "LegalBranch",
                "description": "Legal branch / sub-branch classification — taxonomy node",
                "color": "#10B981",
                "icon": "tag",
                "properties": {
                    "node_id":              {"type": "string",  "required": True, "unique": True, "description": "E.g. HD_IS_HUKUKU"},
                    "node_type":            {"type": "string",  "required": True, "enum": ["legal_branch"]},
                    "embed_text":           {"type": "string",  "required": True, "indexed": True},
                    "branch_name":          {"type": "string",  "required": True},
                    "parent_branch":        {"type": "string",  "required": False, "description": "Parent legal branch node_id (hierarchy)"},
                    "description":          {"type": "string",  "required": False},
                    "key_laws":             {"type": "string[]", "required": False, "description": "node_id list of key laws in this branch"}
                },
                "embed_template": "{branch_name} {description}"
            },

            "Concept": {
                "label": "Concept",
                "description": "Legal term or concept — concept nodes to improve search quality",
                "color": "#8B5CF6",
                "icon": "lightbulb",
                "properties": {
                    "node_id":              {"type": "string",  "required": True, "unique": True, "description": "E.g. KAVRAM_MUVAZAA"},
                    "node_type":            {"type": "string",  "required": True, "enum": ["concept"]},
                    "embed_text":           {"type": "string",  "required": True, "indexed": True},
                    "concept_name":         {"type": "string",  "required": True},
                    "definition":           {"type": "string",  "required": True, "description": "Legal definition of the concept"},
                    "synonyms":             {"type": "string[]", "required": False, "description": "Synonyms and alternative usages"},
                    "related_laws":         {"type": "string[]", "required": False},
                    "legal_branch":         {"type": "string",  "required": False}
                },
                "embed_template": "{concept_name} {definition} {synonyms_join}"
            }
        },

        "edge_types": {
            "CONTAINS": {
                "description": "Hierarchical containment (law→article, article→paragraph, paragraph→clause, decision→decision_chunk)",
                "direction": "directed",
                "cardinality": "one_to_many",
                "source_types": ["Law", "Article", "ArticleVersion", "Paragraph", "Decision"],
                "target_types": ["Article", "Paragraph", "Clause", "DecisionChunk"],
                "weight_range": [1.0, 1.0],
                "properties": {},
                "semantic_role": "structural"
            },
            "PARENT_NODE": {
                "description": "Child-to-parent reference (paragraph→article, clause→paragraph, decision_chunk→decision)",
                "direction": "directed",
                "cardinality": "many_to_one",
                "source_types": ["Paragraph", "Clause", "ArticleVersion", "DecisionChunk"],
                "target_types": ["Article", "ArticleVersion", "Paragraph", "Decision"],
                "weight_range": [1.0, 1.0],
                "properties": {},
                "semantic_role": "structural"
            },
            "VERSION_OF": {
                "description": "Article versioning — shows which versions are different time-period states of the same article",
                "direction": "directed",
                "cardinality": "many_to_one",
                "source_types": ["ArticleVersion"],
                "target_types": ["Article"],
                "weight_range": [1.0, 1.0],
                "properties": {
                    "validity_start": {"type": "date"},
                    "validity_end":   {"type": "date"}
                },
                "semantic_role": "temporal"
            },
            "REFERENCES": {
                "description": "Cross-reference / citation (decision→article, decision→decision, article→article)",
                "direction": "directed",
                "cardinality": "many_to_many",
                "source_types": ["Decision", "DecisionRationale", "DecisionChunk", "Article"],
                "target_types": ["Article", "Decision"],
                "weight_range": [0.7, 1.0],
                "properties": {
                    "reference_type": {"type": "string", "enum": ["law_article", "precedent_decision", "general_reference"], "required": False}
                },
                "semantic_role": "reference"
            },
            "RATIONALE_OF": {
                "description": "Decision rationale to decision link",
                "direction": "directed",
                "cardinality": "one_to_one",
                "source_types": ["DecisionRationale"],
                "target_types": ["Decision"],
                "weight_range": [1.0, 1.0],
                "properties": {},
                "semantic_role": "structural"
            },
            "APPEAL_CHAIN": {
                "description": "Judicial hierarchy — upper court decision to lower court decision link (cassation/appeal)",
                "direction": "directed",
                "cardinality": "many_to_one",
                "source_types": ["Decision"],
                "target_types": ["Decision"],
                "weight_range": [0.9, 1.0],
                "properties": {
                    "appeal_type": {"type": "string", "enum": ["appeal", "cassation"], "required": False}
                },
                "semantic_role": "judicial_hierarchy"
            },
            "DECIDED_BY": {
                "description": "Decision-to-court relationship",
                "direction": "directed",
                "cardinality": "many_to_one",
                "source_types": ["Decision"],
                "target_types": ["Court"],
                "weight_range": [1.0, 1.0],
                "properties": {},
                "semantic_role": "institutional"
            },
            "LEGAL_BRANCH": {
                "description": "Node's legal branch relationship — taxonomy edge",
                "direction": "directed",
                "cardinality": "many_to_many",
                "source_types": ["Law", "Article", "Decision", "DecisionRationale", "DecisionChunk", "Concept"],
                "target_types": ["LegalBranch"],
                "weight_range": [0.8, 1.0],
                "properties": {},
                "semantic_role": "taxonomy"
            },
            "PARENT_BRANCH": {
                "description": "Legal branch hierarchy (child branch→parent branch)",
                "direction": "directed",
                "cardinality": "many_to_one",
                "source_types": ["LegalBranch"],
                "target_types": ["LegalBranch"],
                "weight_range": [1.0, 1.0],
                "properties": {},
                "semantic_role": "taxonomy"
            },
            "SAME_LAW": {
                "description": "Weak link between articles belonging to the same law",
                "direction": "undirected",
                "cardinality": "many_to_many",
                "source_types": ["Article"],
                "target_types": ["Article"],
                "weight_range": [0.2, 0.4],
                "properties": {},
                "semantic_role": "co-occurrence"
            },
            "RELATED_CONCEPT": {
                "description": "Node's related legal concept",
                "direction": "directed",
                "cardinality": "many_to_many",
                "source_types": ["Article", "Decision", "DecisionRationale", "DecisionChunk", "Paragraph"],
                "target_types": ["Concept"],
                "weight_range": [0.7, 1.0],
                "properties": {},
                "semantic_role": "conceptual"
            },
            "CONFLICTING_DECISION": {
                "description": "Conflict between decisions reaching opposite outcomes in the same legal branch",
                "direction": "undirected",
                "cardinality": "many_to_many",
                "source_types": ["Decision"],
                "target_types": ["Decision"],
                "weight_range": [0.6, 0.9],
                "properties": {
                    "legal_branch": {"type": "string", "required": False}
                },
                "semantic_role": "analytical"
            },
            "SEMANTIC_SIMILAR": {
                "description": "Nodes with high cosine similarity between embedding vectors",
                "direction": "undirected",
                "cardinality": "many_to_many",
                "source_types": ["*"],
                "target_types": ["*"],
                "weight_range": [0.0, 1.0],
                "properties": {
                    "similarity": {"type": "float", "description": "Cosine similarity score"}
                },
                "semantic_role": "semantic"
            }
        },

        "constraints": [
            {
                "type": "uniqueness",
                "target": "node",
                "field": "node_id",
                "description": "Each node_id must be unique"
            },
            {
                "type": "existence",
                "target": "node",
                "fields": ["node_id", "node_type", "embed_text"],
                "description": "Each node must contain node_id, node_type, and embed_text"
            },
            {
                "type": "referential_integrity",
                "target": "edge",
                "description": "Each edge's source and target must point to existing nodes"
            },
            {
                "type": "edge_type_constraint",
                "description": "Edge types can only be created between source_types→target_types defined in the ontology"
            }
        ],

        "indexes": [
            {
                "name": "node_id_unique",
                "type": "uniqueness",
                "label": "LegalNode",
                "property": "node_id"
            },
            {
                "name": "node_type_idx",
                "type": "btree",
                "label": "LegalNode",
                "property": "node_type"
            },
            {
                "name": "law_abbreviation_idx",
                "type": "btree",
                "label": "LegalNode",
                "property": "law_abbreviation"
            },
            {
                "name": "court_type_idx",
                "type": "btree",
                "label": "LegalNode",
                "property": "court_type"
            },
            {
                "name": "node_text_ft",
                "type": "fulltext",
                "label": "LegalNode",
                "property": "embed_text"
            },
            {
                "name": "node_embedding_index",
                "type": "vector",
                "label": "LegalNode",
                "property": "embedding",
                "config": {
                    "dimensions": 384,
                    "similarity_function": "cosine"
                }
            }
        ],

        "build_config": {
            "node_files_dir": "nodes",
            "edge_files_dir": "edges",
            "node_files": [
                "laws.jsonl",
                "articles.jsonl",
                "decisions.jsonl",
                "courts.jsonl",
                "legal_branches.jsonl",
                "concepts.jsonl"
            ],
            "edge_files": [
                "structural_edges.jsonl"
            ],
            "edge_rules_file": "edge_rules.json",
            "validation_schema": "validation/schema.json"
        }
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(ontology, f, indent=2, ensure_ascii=False)
    print(f"  ontology.json rewritten")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=== Migrating Turkish identifiers to English ===\n")

    print("[1/5] Migrating node files …")
    migrate_node_files()

    print("\n[2/5] Migrating edge files …")
    migrate_edge_files()

    print("\n[3/5] Rewriting edge_rules.json …")
    migrate_edge_rules()

    print("\n[4/5] Rewriting schema.json …")
    migrate_schema()

    print("\n[5/5] Rewriting ontology.json …")
    migrate_ontology()

    print("\n=== Migration complete ===")
    print("Next: update Python source code, then run pipeline build.")


if __name__ == "__main__":
    main()
