# Data Preparation Guide — Apilex GraphRAG

This document explains everything needed to prepare a dataset for the Apilex GraphRAG pipeline. After reading this guide, you should be able to write a script that transforms raw legal data into the required `graph_data/` folder structure.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Folder Structure](#2-folder-structure)
3. [Universal Node Format](#3-universal-node-format)
4. [Node Types — Full Specification](#4-node-types--full-specification)
   - 4.1 [Law](#41-law)
   - 4.2 [Article](#42-article)
   - 4.3 [ArticleVersion](#43-articleversion)
   - 4.4 [Paragraph](#44-paragraph)
   - 4.5 [Clause](#45-clause)
   - 4.6 [Decision](#46-decision)
   - 4.7 [DecisionRationale](#47-decisionrationale)
   - 4.8 [DecisionChunk](#48-decisionchunk)
   - 4.9 [Court](#49-court)
   - 4.10 [LegalBranch](#410-legalbranch)
   - 4.11 [Concept](#411-concept)
5. [Edge Format](#5-edge-format)
6. [Edge Types — Full Specification](#6-edge-types--full-specification)
7. [Dynamic Edge Rules](#7-dynamic-edge-rules)
8. [Node ID Conventions](#8-node-id-conventions)
9. [embed_text Guidelines](#9-embed_text-guidelines)
10. [Validation Rules & Constraints](#10-validation-rules--constraints)
11. [Chunking Strategy for Long Documents](#11-chunking-strategy-for-long-documents)
12. [Step-by-Step Data Preparation Workflow](#12-step-by-step-data-preparation-workflow)
13. [Complete Minimal Example](#13-complete-minimal-example)
14. [Common Mistakes to Avoid](#14-common-mistakes-to-avoid)

---

## 1. Overview

The Apilex GraphRAG pipeline ingests **JSON files** from the `graph_data/` folder and builds a Neo4j knowledge graph with vector embeddings for semantic search. The pipeline is **ontology-driven** — it reads `ontology.json` to understand all node/edge types, then validates and inserts data accordingly.

**What the pipeline does automatically:**
- Validates every node and edge against the ontology
- Generates 384-dimensional embeddings from each node's `embed_text` field using `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Caches embeddings incrementally (only re-embeds new/changed nodes)
- Creates explicit (structural) edges from `structural_edges.json`
- Computes dynamic edges from rules in `edge_rules.json`
- Upserts everything into Neo4j with proper constraints and indexes

**What you must provide:**
- Node data files in `graph_data/nodes/` (`.json` or `.jsonl`)
- Edge data files in `graph_data/edges/` (`.json` or `.jsonl`)
- (Optional) updated `edge_rules.json` if you define new dynamic relationships

---

## 2. Folder Structure

```
graph_data/
├── ontology.json                    # DO NOT MODIFY (unless adding new types)
├── nodes/
│   ├── laws.json                    # Law nodes (.json or .jsonl)
│   ├── articles.json                # Article + Paragraph + Clause + ArticleVersion nodes
│   ├── decisions.json               # Decision + DecisionRationale + DecisionChunk nodes
│   ├── courts.json                  # Court nodes
│   ├── legal_branches.json          # LegalBranch nodes
│   └── concepts.json                # Concept nodes
├── edges/
│   ├── structural_edges.json        # All explicit/structural edges (.json or .jsonl)
│   └── edge_rules.json              # Dynamic edge computation rules (always .json)
├── validation/
│   └── schema.json                  # JSON Schema (used by pipeline)
└── embeddings/
    ├── cache.npz                    # Auto-generated — do not create manually
    └── cache_meta.json              # Auto-generated — do not create manually
```

> **JSONL support:** Any node or edge data file can use `.jsonl` (JSON Lines) instead of `.json`. If `ontology.json` lists `decisions.json` but the actual file is `decisions.jsonl`, the pipeline will find it automatically. When no explicit file list is given, the pipeline scans for both `.json` and `.jsonl` files.

**File assignments (which node types go in which file):**

| File | Node types contained |
|------|---------------------|
| `laws.json` | `law` |
| `articles.json` | `article`, `paragraph`, `clause`, `article_version` |
| `decisions.json` | `decision`, `decision_rationale`, `decision_chunk` |
| `courts.json` | `court` |
| `legal_branches.json` | `legal_branch` |
| `concepts.json` | `concept` |

The pipeline loads these files in the order specified in `ontology.json → build_config.node_files`.

---

## 3. Universal Node Format

Every node in every JSON file follows the same top-level structure:

```json
{
  "node_id": "UNIQUE_IDENTIFIER",
  "node_type": "one_of_the_11_types",
  "embed_text": "Semantic text used for vector embedding and search",
  "metadata": {
    // All other properties go here
  }
}
```

### Required fields (all nodes):

| Field | Type | Description |
|-------|------|-------------|
| `node_id` | `string` | Globally unique identifier. Pattern: `^[A-Za-z0-9_İÖÜŞÇĞ]+$` (alphanumeric + underscore + Turkish chars). No spaces, no hyphens. |
| `node_type` | `string` | One of: `law`, `article`, `article_version`, `paragraph`, `clause`, `decision`, `decision_rationale`, `decision_chunk`, `court`, `legal_branch`, `concept` |
| `embed_text` | `string` | Minimum 5 characters. This text is vectorized for semantic search. Should be descriptive and keyword-rich. |
| `metadata` | `object` | All type-specific properties. Always an object, never null. |

### Important: All type-specific properties live inside `metadata`

The pipeline reads `metadata.*` for all properties beyond `node_id`, `node_type`, and `embed_text`. For example, a Law's `law_name` is stored at `metadata.law_name`, not at the top level.

---

## 4. Node Types — Full Specification

### 4.1 Law

Represents a law, decree, or regulation.

```json
{
  "node_id": "kanun_tbk_6098",
  "node_type": "law",
  "embed_text": "Türk Borçlar Kanunu kanun numarası 6098 kabul tarihi 11 Ocak 2011 yürürlük tarihi 1 Temmuz 2012 borç ilişkileri sözleşme haksız fiil sebepsiz zenginleşme",
  "metadata": {
    "law_name": "Türk Borçlar Kanunu",
    "abbreviation": "TBK",
    "law_number": 6098,
    "adoption_date": "2011-01-11",
    "effective_date": "2012-07-01",
    "official_gazette_date": "2011-02-04",
    "official_gazette_number": 27836,
    "domain": ["borç hukuku", "sözleşme hukuku", "haksız fiil"],
    "source_file": "Kanunlar/TBK_6098_Turk_Borclar_Kanunu.txt"
  }
}
```

**metadata fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `law_name` | string | ✅ | Full name of the law |
| `abbreviation` | string | ✅ | Abbreviation (TBK, TMK, İK, HMK) |
| `law_number` | integer | ✅ | Law number |
| `adoption_date` | date (ISO) | ✅ | Parliament approval date |
| `effective_date` | date (ISO) | ✅ | Effective date |
| `official_gazette_date` | date (ISO) | ❌ | Official Gazette date |
| `official_gazette_number` | integer | ❌ | Official Gazette number |
| `domain` | string[] | ❌ | Legal areas covered |
| `source_file` | string | ❌ | Source file path |

**Node ID pattern:** `kanun_{abbreviation_lowercase}_{law_number}` → e.g. `kanun_tbk_6098`

---

### 4.2 Article

Represents a specific article of a law.

```json
{
  "node_id": "HMK_M1",
  "node_type": "article",
  "embed_text": "Hukuk Muhakemeleri Kanunu madde 1 yargı yetkisi medenî yargı yetkisi Türk Milleti adına bağımsız ve tarafsız mahkemelerce kullanılır",
  "metadata": {
    "law_number": 6100,
    "law_abbreviation": "HMK",
    "article_number": 1,
    "article_title": "Yargı Yetkisi",
    "effective_date": "2011-10-01",
    "is_repealed": false,
    "previous_regulation": "1086 sayılı HUMK m.1",
    "subject": "yargı yetkisi, bağımsızlık, tarafsızlık",
    "legal_branch": "usul hukuku",
    "paragraphs": ["HMK_M1_F1"]
  }
}
```

**metadata fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `law_number` | integer | ✅ | Parent law number |
| `law_abbreviation` | string | ✅ | Parent law abbreviation |
| `article_number` | integer | ✅ | Article number |
| `article_title` | string | ❌ | Article title/heading |
| `effective_date` | date (ISO) | ✅ | Effective date |
| `is_repealed` | boolean | ✅ | Whether the article is repealed (`true`/`false`) |
| `repeal_date` | date (ISO) | ❌ | Repeal date (if repealed) |
| `previous_regulation` | string | ❌ | Reference to previous regulation |
| `subject` | string | ❌ | Subject summary |
| `legal_branch` | string | ❌ | Legal branch |
| `paragraphs` | string[] | ❌ | List of child Paragraph `node_id`s |

**Node ID pattern:** `{ABBREVIATION}_M{article_number}` → e.g. `HMK_M1`, `TBK_M19`

For repealed articles, append `_MULGA`: `TMK_M163_MULGA`

---

### 4.3 ArticleVersion

Represents a specific version of an article that has been amended over time.

```json
{
  "node_id": "HMK_M3_V1",
  "node_type": "article_version",
  "embed_text": "HMK madde 3 versiyon 1 sulh hukuk mahkemesi sınırı 2011 yürürlük",
  "metadata": {
    "law_number": 6100,
    "law_abbreviation": "HMK",
    "article_number": 3,
    "versiyon": 1,
    "article_title": "Sulh Hukuk Mahkemesinin Görevi",
    "effective_date": "2011-10-01",
    "validity_end_date": "2020-07-30",
    "is_repealed": false,
    "amending_law": "7251 sayılı Kanun",
    "subject": "sulh hukuk mahkemesi görev sınırı",
    "legal_branch": "usul hukuku"
  }
}
```

**metadata fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `law_number` | integer | ✅ | Parent law number |
| `law_abbreviation` | string | ✅ | Parent law abbreviation |
| `article_number` | integer | ✅ | Article number |
| `versiyon` | integer | ✅ | Version number (1-based) |
| `article_title` | string | ❌ | Article title |
| `effective_date` | date (ISO) | ✅ | Version effective date |
| `validity_end_date` | date (ISO) | ❌ | End of validity date |
| `is_repealed` | boolean | ✅ | Whether repealed |
| `amending_law` | string | ❌ | Amending law reference |
| `subject` | string | ❌ | Subject summary |
| `legal_branch` | string | ❌ | Legal branch |

**Node ID pattern:** `{ABBREVIATION}_M{article_number}_V{versiyon}` → e.g. `HMK_M3_V1`

---

### 4.4 Paragraph

Represents a paragraph within an article — the smallest meaningful unit of law text.

```json
{
  "node_id": "HMK_M1_F1",
  "node_type": "paragraph",
  "embed_text": "Medenî yargı yetkisi, Türk Milleti adına bağımsız ve tarafsız mahkemelerce kullanılır.",
  "metadata": {
    "law_number": 6100,
    "law_abbreviation": "HMK",
    "article_number": 1,
    "paragraph_number": 1,
    "effective_date": "2011-10-01",
    "is_repealed": false,
    "subject": "medenî yargı yetkisinin anayasal dayanağı",
    "legal_branch": "usul hukuku",
    "parent_node": "HMK_M1"
  }
}
```

**metadata fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `law_number` | integer | ✅ | Parent law number |
| `law_abbreviation` | string | ✅ | Parent law abbreviation |
| `article_number` | integer | ✅ | Parent article number |
| `paragraph_number` | integer | ✅ | Paragraph number within the article (1-based) |
| `effective_date` | date (ISO) | ✅ | Effective date |
| `is_repealed` | boolean | ✅ | Whether repealed |
| `subject` | string | ❌ | Subject summary |
| `legal_branch` | string | ❌ | Legal branch |
| `parent_node` | string | ✅ | Parent node_id (Article or ArticleVersion) |

**Node ID pattern:** `{ABBREVIATION}_M{article_number}_F{paragraph_number}` → e.g. `HMK_M1_F1`

For paragraph under a version: `{ABBREVIATION}_M{article_number}_V{versiyon}_F{paragraph_number}` → e.g. `HMK_M3_V1_F1`

**`embed_text` for Paragraph:** Use the **full text** of the paragraph. This is the most important node type for semantic search.

---

### 4.5 Clause

Represents a sub-clause within a paragraph (lettered or numbered items like a, b, c or 1, 2, 3).

```json
{
  "node_id": "IK_M25_F2_B1",
  "node_type": "clause",
  "embed_text": "İşçinin kendi kastından veya derli toplu olmayan yaşayışından yahut içkiye düşkünlüğünden doğacak bir hastalığa veya sakatlığa uğraması...",
  "metadata": {
    "law_number": 4857,
    "law_abbreviation": "IK",
    "article_number": 25,
    "paragraph_number": 2,
    "clause_number": "a",
    "parent_node": "IK_M25_F2"
  }
}
```

**metadata fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `law_number` | integer | ✅ | Parent law number |
| `law_abbreviation` | string | ✅ | Parent law abbreviation |
| `article_number` | integer | ✅ | Parent article number |
| `paragraph_number` | integer | ✅ | Parent paragraph number |
| `clause_number` | string | ✅ | Clause identifier (e.g. "a", "b", "1", "2") |
| `parent_node` | string | ✅ | Parent Paragraph node_id |

**Node ID pattern:** `{ABBREVIATION}_M{article_number}_F{paragraph_number}_B{clause_number}` → e.g. `IK_M25_F2_B1`

---

### 4.6 Decision

Represents a court decision — first instance, appellate (BAM), or cassation (Yargıtay).

```json
{
  "node_id": "KARAR_YRG_1HD_2013_11205",
  "node_type": "decision",
  "embed_text": "Yargıtay 1. Hukuk Dairesi 2013/11205 esas 2014/8321 karar tapu iptali muvazaa temyiz incelemesi TMK madde 706 TBK madde 19 gerçek irade araştırması onama",
  "metadata": {
    "court_type": "supreme_court",
    "court_name": "Yargıtay 1. Hukuk Dairesi",
    "chamber": "1. Hukuk Dairesi",
    "docket_number": "2013/11205",
    "decision_number": "2014/8321",
    "decision_date": "2014-03-12",
    "first_instance_ref": "KARAR_ID_2012_541",
    "appeals_court_ref": null,
    "subject": "tapu iptali - muvazaa temyizi",
    "outcome": "ONAMA",
    "referenced_laws": ["TMK/706", "TBK/19"],
    "referenced_decisions": ["KARAR_ID_2012_541", "YRG_HGK_1996_64"],
    "legal_branch": "eşya hukuku",
    "precedent_status": "yerleşik içtihat",
    "chunks": [
      "KARAR_YRG_1HD_2013_11205_B1",
      "KARAR_YRG_1HD_2013_11205_B2",
      "KARAR_YRG_1HD_2013_11205_B3"
    ]
  }
}
```

**metadata fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `court_type` | string | ✅ | Court level: `first_instance`, `appellate`, `supreme_court` |
| `court_name` | string | ✅ | Full court name |
| `chamber` | string | ❌ | Chamber/division name |
| `docket_number` | string | ✅ | Case number (e.g. "2013/11205") |
| `decision_number` | string | ✅ | Decision number (e.g. "2014/8321") |
| `decision_date` | date (ISO) | ✅ | Decision date |
| `subject` | string | ✅ | Subject of the case |
| `outcome` | string | ✅ | Result: `ONAMA`, `BOZMA`, `KABUL`, `RET`, `KISMI_KABUL`, etc. |
| `legal_branch` | string | ❌ | Legal branch |
| `precedent_status` | string | ❌ | Precedent value description |
| `referenced_laws` | string[] | ❌ | Referenced laws (format: `ABBREVIATION/ARTICLE_NO`) |
| `referenced_decisions` | string[] | ❌ | Referenced decisions (node_ids) |
| `first_instance_ref` | string | ❌ | First instance decision node_id (for appeals) |
| `appeals_court_ref` | string | ❌ | Appellate decision node_id (for cassation) |
| `appeal_path` | string | ❌ | Legal remedy status |
| `chunks` | string[] | ❌ | List of child DecisionChunk node_ids |

**Node ID patterns:**

| Court type | Pattern | Example |
|------------|---------|---------|
| Yargıtay | `KARAR_YRG_{DAIRE_SHORT}_{YEAR}_{NO}` | `KARAR_YRG_1HD_2013_11205` |
| Yargıtay HGK | `KARAR_YRG_HGK_{YEAR}_{NO}` | `KARAR_YRG_HGK_2003_603` |
| BAM (İstinaf) | `KARAR_BAM_{CITY}_{YEAR}_{NO}` | `KARAR_BAM_IST_2020_2110` |
| İlk Derece | `KARAR_ID_{YEAR}_{NO}` | `KARAR_ID_2015_8821` |

---

### 4.7 DecisionRationale

A summary/rationale text of a court decision. One per decision, captures the core legal reasoning.

```json
{
  "node_id": "KARAR_YRG_1HD_2013_11205_GEREKCESI",
  "node_type": "decision_rationale",
  "embed_text": "Tapu devir işleminin muvazaalı olduğu tespit edilmiştir. HGK 1996 tarihli karar esas alınarak ilk derece mahkemesinin bedel ödenmediğini ve mal kaçırma amacını saptayan tespitleri yerinde görülmüştür...",
  "metadata": {
    "parent_decision": "KARAR_YRG_1HD_2013_11205",
    "rationale_type": "cassation_review",
    "referenced_laws": ["TMK/706", "TBK/19"],
    "referenced_decisions": ["YRG_HGK_1996_64"],
    "key_principles": [
      "muvazaa tespiti",
      "gerçek irade araştırması"
    ],
    "legal_branch": "eşya hukuku"
  }
}
```

**metadata fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `parent_decision` | string | ✅ | Parent Decision node_id |
| `rationale_type` | string | ✅ | Rationale type: `substantive`, `appellate_review`, `cassation_review`, `procedural` |
| `referenced_laws` | string[] | ❌ | Referenced laws |
| `referenced_decisions` | string[] | ❌ | Referenced decisions |
| `key_principles` | string[] | ❌ | Key legal principles |
| `legal_branch` | string | ❌ | Legal branch |

**Node ID pattern:** `{DECISION_NODE_ID}_GEREKCESI` → e.g. `KARAR_YRG_1HD_2013_11205_GEREKCESI`

---

### 4.8 DecisionChunk

Semantic chunks of a court decision. Long decisions (5,000–50,000+ chars) are split into meaningful sections, each stored as a separate node for fine-grained semantic retrieval.

```json
{
  "node_id": "KARAR_YRG_1HD_2013_11205_B1",
  "node_type": "decision_chunk",
  "embed_text": "Davacı, murisi A.K.'nın yaşlılık ve hastalık döneminde dava dışı üçüncü kişi F.K. tarafından kandırılarak Ankara ili Çankaya ilçesi 25431 ada 7 parselde kayıtlı taşınmazını tapuda satış suretiyle devrettiğini ileri sürmüştür...",
  "metadata": {
    "parent_decision": "KARAR_YRG_1HD_2013_11205",
    "chunk_type": "case_summary",
    "chunk_order": 1,
    "chunk_title": "Olay Özeti ve Dava Konusu",
    "legal_branch": "eşya hukuku",
    "char_count": 658,
    "key_principles": ["muvazaa tespiti"],
    "referenced_laws": ["TMK/706"],
    "referenced_decisions": []
  }
}
```

**metadata fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `parent_decision` | string | ✅ | Parent Decision node_id |
| `chunk_type` | string | ✅ | Section type (see enum below) |
| `chunk_order` | integer | ✅ | Order within the decision (1-based) |
| `chunk_title` | string | ❌ | Section heading |
| `key_principles` | string[] | ❌ | Key legal principles in this section |
| `referenced_laws` | string[] | ❌ | Law references in this section |
| `referenced_decisions` | string[] | ❌ | Decision references in this section |
| `legal_branch` | string | ❌ | Legal branch |
| `char_count` | integer | ❌ | Character count of embed_text |

**Section types (`chunk_type` enum):**

| Value | Meaning | Description |
|-------|---------|-------------|
| `case_summary` | Case Summary | Facts of the case |
| `party_claims` | Party Claims | Arguments from plaintiff and defendant |
| `evidence_evaluation` | Evidence Review | Evaluation of evidence and expert reports |
| `legal_evaluation` | Legal Analysis | Court's legal reasoning and interpretation |
| `conclusion_and_judgment` | Conclusion & Ruling | Final judgment |
| `lower_court_decision` | Lower Court Decision | Summary of the lower court's ruling (for appeals) |
| `cassation_grounds` | Grounds for Appeal | Reasons stated in the cassation petition |
| `appellate_evaluation` | Appellate Review | Appellate court's analysis |

**Node ID pattern:** `{DECISION_NODE_ID}_B{sequence}` → e.g. `KARAR_YRG_1HD_2013_11205_B1`

**`embed_text` for DecisionChunk:** Use the **full chunk text**. Target 500–2000 characters per chunk, split at semantic boundaries.

---

### 4.9 Court

Represents a judicial body.

```json
{
  "node_id": "MAHKEME_YARGITAY_1_HUKUK_DAIRESI",
  "node_type": "court",
  "embed_text": "Yargıtay 1. Hukuk Dairesi Ankara genel hukuk",
  "metadata": {
    "court_name": "Yargıtay 1. Hukuk Dairesi",
    "court_type": "supreme_court",
    "city": "Ankara",
    "chamber": "1. Hukuk Dairesi",
    "specialization": ["genel hukuk"]
  }
}
```

**metadata fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `court_name` | string | ✅ | Full court name |
| `court_type` | string | ✅ | `first_instance`, `appellate`, `supreme_court`, `constitutional_court` |
| `city` | string | ❌ | City |
| `chamber` | string | ❌ | Chamber/division |
| `specialization` | string[] | ❌ | Specialization areas |

**Node ID pattern:** `MAHKEME_{DESCRIPTIVE_NAME}` → e.g. `MAHKEME_YARGITAY_1_HUKUK_DAIRESI`

---

### 4.10 LegalBranch

Taxonomy node for legal branches. Supports hierarchy (parent-child).

```json
{
  "node_id": "HD_IS_HUKUKU",
  "node_type": "legal_branch",
  "embed_text": "iş hukuku hukuk dalı türk hukuk sistemi",
  "metadata": {
    "branch_name": "iş hukuku",
    "parent_branch": "HD_OZEL_HUKUK",
    "key_laws": ["kanun_ik_4857"]
  }
}
```

**metadata fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `branch_name` | string | ✅ | Branch name |
| `parent_branch` | string | ❌ | Parent branch node_id (null for top-level) |
| `description` | string | ❌ | Description |
| `key_laws` | string[] | ❌ | Key law node_ids for this branch |

**Node ID pattern:** `HD_{DESCRIPTIVE_NAME}` → e.g. `HD_IS_HUKUKU`, `HD_OZEL_HUKUK`

---

### 4.11 Concept

Legal terms and concepts used for search enrichment.

```json
{
  "node_id": "KAVRAM_MUVAZAA",
  "node_type": "concept",
  "embed_text": "Muvazaa danışıklı işlem görünürdeki işlem gerçek irade uyuşmazlığı tarafların üçüncü kişileri aldatmak amacıyla gerçek iradelerine uymayan bir işlem yapmaları TBK madde 19",
  "metadata": {
    "concept_name": "Muvazaa",
    "definition": "Tarafların üçüncü kişileri aldatmak amacıyla gerçek iradelerine uymayan bir işlem yapmaları; danışıklı işlem.",
    "synonyms": ["danışıklı işlem", "görünürdeki işlem", "simülasyon"],
    "related_laws": ["TBK_M19"],
    "legal_branch": "borçlar hukuku"
  }
}
```

**metadata fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `concept_name` | string | ✅ | Concept name |
| `definition` | string | ✅ | Legal definition |
| `synonyms` | string[] | ❌ | Synonyms and alternative terms |
| `related_laws` | string[] | ❌ | Related law node_ids |
| `legal_branch` | string | ❌ | Legal branch |

**Node ID pattern:** `KAVRAM_{CONCEPT_NAME_UPPERCASE}` → e.g. `KAVRAM_MUVAZAA`

---

## 5. Edge Format

All edges go in `graph_data/edges/structural_edges.json` as a JSON array. Every edge has this structure:

```json
{
  "edge_id": "E_000001",
  "source": "kanun_hmk_6100",
  "target": "HMK_M1",
  "edge_type": "CONTAINS",
  "weight": 1.0,
  "properties": {}
}
```

### Required fields:

| Field | Type | Description |
|-------|------|-------------|
| `edge_id` | string | Unique edge identifier. Pattern: `E_{6-digit-zero-padded}` → `E_000001` |
| `source` | string | Source node_id (must exist in node files) |
| `target` | string | Target node_id (must exist in node files) |
| `edge_type` | string | One of the 13 defined edge types |
| `weight` | number | Edge weight 0.0–1.0 (1.0 = strongest relationship) |

### Optional fields:

| Field | Type | Description |
|-------|------|-------------|
| `properties` | object | Edge-type-specific properties (e.g. `reference_type`, `appeal_type`) |

**Edge IDs must be sequential and zero-padded to 6 digits.**

---

## 6. Edge Types — Full Specification

### Structural Edges (you create these)

| Edge Type | Direction | Source → Target | Weight | Description |
|-----------|-----------|-----------------|--------|-------------|
| `CONTAINS` | directed | Law→Article, Article→Paragraph, Paragraph→Clause, Decision→DecisionChunk | 1.0 | Hierarchical containment |
| `PARENT_NODE` | directed | Paragraph→Article, Clause→Paragraph, DecisionChunk→Decision, ArticleVersion→Article | 1.0 | Child→parent back-reference |
| `VERSION_OF` | directed | ArticleVersion→Article | 1.0 | Version link |
| `REFERENCES` | directed | Decision→Article, Decision→Decision, DecisionRationale→Article, DecisionChunk→Article | 0.7–1.0 | Cross-reference/citation |
| `RATIONALE_OF` | directed | DecisionRationale→Decision | 1.0 | Rationale→Decision link |
| `APPEAL_CHAIN` | directed | UpperCourtDecision→LowerCourtDecision | 0.9–1.0 | Judicial hierarchy |
| `DECIDED_BY` | directed | Decision→Court | 1.0 | Decision→Court link |
| `LEGAL_BRANCH` | directed | (any node)→LegalBranch | 0.8–1.0 | Taxonomy classification |
| `PARENT_BRANCH` | directed | LegalBranch→LegalBranch | 1.0 | Branch hierarchy |
| `RELATED_CONCEPT` | directed | Article/Decision/Paragraph→Concept | 0.7–1.0 | Concept association |

### Properties on specific edge types:

**REFERENCES:**
```json
"properties": {
  "reference_type": "law_article"  // or "precedent_decision" or "general_reference"
}
```

**APPEAL_CHAIN:**
```json
"properties": {
  "appeal_type": "cassation"  // or "appeal"
}
```

### Which edges to create for each hierarchy:

**Law hierarchy (per article):**
```
Law ──CONTAINS──→ Article
Article ──CONTAINS──→ Paragraph
Paragraph ←──PARENT_NODE── Paragraph→Article
Paragraph ──CONTAINS──→ Clause (if has sub-clauses)
Clause ←──PARENT_NODE── Clause→Paragraph
```

**Decision hierarchy (per decision):**
```
Decision ──CONTAINS──→ DecisionChunk      (for each chunk)
DecisionChunk ──PARENT_NODE──→ Decision   (reverse for each chunk)
DecisionRationale ──RATIONALE_OF──→ Decision (one per decision)
Decision ──DECIDED_BY──→ Court            (one per decision)
DecisionChunk ──REFERENCES──→ Article     (if chunk references a law article)
```

**Judicial chains:**
```
Yargıtay_Decision ──APPEAL_CHAIN──→ BAM_Decision
BAM_Decision ──APPEAL_CHAIN──→ FirstInstance_Decision
```

---

## 7. Dynamic Edge Rules

Dynamic edges are **computed automatically** at build time based on rules in `edge_rules.json`. You don't have to create these edges manually.

Current rules:

| Rule | Edge Type | What it does |
|------|-----------|-------------|
| `same_law_articles` | `SAME_LAW` | Connects all Article nodes with the same `law_number` |
| `conflicting_decisions` | `CONFLICTING_DECISION` | Connects decisions with similar topics but opposite outcomes |
| `same_decision_chunks` | `SAME_LAW` | Connects all DecisionChunk chunks belonging to the same parent decision |
| `semantic_similarity` | `SEMANTIC_SIMILAR` | Connects nodes with cosine similarity > 0.82 (post-build) |

You can add custom rules by editing `edge_rules.json`. Supported condition types:

- **`metadata_match`**: Connects nodes where a metadata field matches. Specify `source_field` and `target_field`.
- **`contradictory_decisions`**: Connects decision nodes with similar embeddings but different `outcome` values.
- **`cosine_similarity`**: Post-build similarity edges via vector index.

---

## 8. Node ID Conventions

Node IDs **must** follow these rules:

1. **Characters:** Only `A-Z`, `a-z`, `0-9`, `_`, and Turkish characters `İÖÜŞÇĞ`
2. **No spaces, hyphens, dots, or slashes**
3. **Globally unique** across all files
4. **Descriptive** — encode enough info to identify the entity

### Summary of all patterns:

| Type | Pattern | Example |
|------|---------|---------|
| Law | `kanun_{abbreviation}_{no}` | `kanun_tbk_6098` |
| Article | `{ABBREVIATION}_M{no}` | `TBK_M19` |
| ArticleVersion | `{ABBREVIATION}_M{no}_V{ver}` | `HMK_M3_V1` |
| Paragraph | `{ABBREVIATION}_M{no}_F{fno}` | `TBK_M19_F1` |
| Clause | `{ABBREVIATION}_M{no}_F{fno}_B{bno}` | `IK_M25_F2_B1` |
| Decision | `KARAR_{COURT}_{YEAR}_{NO}` | `KARAR_YRG_1HD_2013_11205` |
| DecisionRationale | `{DECISION_ID}_GEREKCESI` | `KARAR_YRG_1HD_2013_11205_GEREKCESI` |
| DecisionChunk | `{DECISION_ID}_B{order}` | `KARAR_YRG_1HD_2013_11205_B1` |
| Court | `MAHKEME_{NAME}` | `MAHKEME_YARGITAY_1_HUKUK_DAIRESI` |
| LegalBranch | `HD_{NAME}` | `HD_IS_HUKUKU` |
| Concept | `KAVRAM_{NAME}` | `KAVRAM_MUVAZAA` |

---

## 9. embed_text Guidelines

The `embed_text` field is **the most critical field** for search quality. It is vectorized and used for semantic retrieval.

### General principles:

1. **No formatting / HTML / markdown** — plain text only
2. **Minimum 5 characters** (enforced by schema validation)
3. **Turkish language** — write in Turkish for optimal retrieval
4. **Keyword-rich** — include all important terms a user might search for
5. **No duplication of node_id** — don't include the raw ID in embed_text

### Per-type guidelines:

| Node Type | embed_text Strategy | Length Target |
|-----------|-------------------|---------------|
| **Law** | `{name} kanun numarası {no} kabul tarihi {date} {legal areas}` | 100–300 chars |
| **Article** | `{law} madde {no} {title} {subject keywords}` | 80–200 chars |
| **Paragraph** | Full paragraph text verbatim | As-is from law |
| **Clause** | Full clause text verbatim | As-is from law |
| **Decision** | `{court} {case_no} {subject} {result} {referenced laws}` | 100–300 chars |
| **DecisionRationale** | Full rationale text | 200–2000 chars |
| **DecisionChunk** | Full chunk text | 500–2000 chars |
| **Court** | `{name} {city} {specialization areas}` | 30–100 chars |
| **LegalBranch** | `{name} hukuk dalı türk hukuk sistemi` | 30–100 chars |
| **Concept** | `{name} {definition} {synonyms}` | 100–500 chars |

### Embedding model details:

- Model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Dimensions: 384
- Similarity: cosine
- Max input tokens: ~128 tokens (roughly 500–700 characters in Turkish)
- **If text exceeds the model's context window, it will be truncated.** Keep critical information at the beginning.

---

## 10. Validation Rules & Constraints

The pipeline enforces these validation rules at build time:

### Node validation:
- ✅ `node_id`, `node_type`, `embed_text` must be present on every node
- ✅ `node_id` must match pattern `^[A-Za-z0-9_İÖÜŞÇĞ]+$`
- ✅ `node_type` must be one of the 11 defined types
- ✅ `embed_text` minimum 5 characters
- ✅ All `node_id` values must be globally unique

### Edge validation:
- ✅ `edge_id`, `source`, `target`, `edge_type` must be present
- ✅ `source` and `target` must reference existing node_ids
- ✅ `edge_type` must be one of the 13 defined types
- ✅ `weight` must be 0.0–1.0
- ⚠️ Edges referencing non-existent nodes are **skipped** (warning, not error)

### File format:
- ✅ Data files can be **JSON** (`.json`) or **JSONL** (`.jsonl`)
- ✅ `.json` files must be JSON arrays of objects: `[{...}, {...}]`
- ✅ `.jsonl` files must have one JSON object per line (blank lines are skipped)
- ✅ `edge_rules.json` and `ontology.json` must always be standard `.json`

### JSON vs JSONL — when to use which:

| Format | Best for | Example |
|--------|----------|--------|
| `.json` | Small/medium files, config files, human-edited data | `concepts.json` (18 nodes) |
| `.jsonl` | Large files, machine-generated data, append-friendly workflows | `decisions.jsonl` (thousands of decisions) |

**JSONL example** (`decisions.jsonl`):
```
{"node_id": "KARAR_YRG_1HD_2013_11205", "node_type": "decision", "embed_text": "...", "metadata": {...}}
{"node_id": "KARAR_YRG_1HD_2013_11205_GEREKCESI", "node_type": "decision_rationale", "embed_text": "...", "metadata": {...}}
{"node_id": "KARAR_YRG_1HD_2013_11205_B1", "node_type": "decision_chunk", "embed_text": "...", "metadata": {...}}
```

### Build behavior:
- Invalid edges (missing target/source) → logged as WARNING, edge skipped
- Duplicate node_ids → last one wins (MERGE operation)
- Missing optional metadata fields → silently ignored

---

## 11. Chunking Strategy for Long Documents

### When to chunk:

| Document Type | Typical Length | Chunk? |
|---------------|---------------|--------|
| Paragraph (law paragraph) | 50–500 chars | ❌ Already granular |
| DecisionRationale (rationale summary) | 200–2000 chars | ❌ Keep as single node |
| Full court decision text | 5,000–50,000+ chars | ✅ Split into DecisionChunk |
| Very long paragraph or detailed rationale | 2000+ chars | ⚠️ Consider chunking |

### How to chunk a court decision:

1. **Identify semantic sections** in the decision text. Common section patterns in Turkish court decisions:
   - "TÜRK MİLLETİ ADINA" → start of decision
   - "İDDİA:" or "Davacı vekili" → party claims
   - "SAVUNMA:" or "Davalı vekili" → defense arguments
   - "DELİLLER:" or "Dosya kapsamına göre" → evidence review
   - "HUKUKİ DEĞERLENDİRME:" → legal analysis
   - "SONUÇ:" or "HÜKÜM:" → conclusion and ruling
   - "TEMYİZ SEBEPLERİ:" → appeal grounds

2. **Target chunk size:** 500–2000 characters per chunk. This fits within the embedding model's context window.

3. **Don't split mid-sentence.** Always split at paragraph or section boundaries.

4. **Assign `chunk_type`** from the 8-value enum based on the section's content.

5. **Sequential numbering:** `chunk_order` starts at 1 and increments per chunk within a decision.

6. **Extract references per chunk:** If a chunk mentions "TMK m.706", add it to that chunk's `referenced_laws`.

### Chunking pseudocode:

```python
def chunk_decision(decision_text: str, decision_node_id: str) -> list[dict]:
    sections = split_by_semantic_boundaries(decision_text)
    chunks = []
    for i, section in enumerate(sections, start=1):
        chunk = {
            "node_id": f"{decision_node_id}_B{i}",
            "node_type": "decision_chunk",
            "embed_text": section["text"],
            "metadata": {
                "parent_decision": decision_node_id,
                "chunk_type": classify_section(section),  # one of 8 types
                "chunk_order": i,
                "chunk_title": section.get("heading", ""),
                "char_count": len(section["text"]),
                "legal_branch": detect_legal_branch(section),
                "referenced_laws": extract_law_refs(section["text"]),
                "referenced_decisions": extract_decision_refs(section["text"]),
                "key_principles": extract_principles(section["text"]),
            }
        }
        chunks.append(chunk)
    return chunks
```

---

## 12. Step-by-Step Data Preparation Workflow

### Step 1: Parse raw texts

Read your source files (laws, decisions) and extract structured information.

### Step 2: Build Law nodes

For each law, create one `law` node with all metadata. Save to `laws.json`.

### Step 3: Build Article → Paragraph → Clause hierarchy

For each article in a law:
1. Create one `article` node
2. For each paragraph in the article, create one `paragraph` node
3. For each sub-clause in a paragraph, create one `clause` node
4. If an article has versions, create `article_version` nodes

Save all to `articles.json`.

### Step 4: Build Decision + DecisionRationale + DecisionChunk

For each court decision:
1. Create one `decision` node (summary-level)
2. Create one `decision_rationale` node (rationale summary)
3. Split the full decision text into semantic sections → create `decision_chunk` nodes
4. Set `chunks` on the parent decision to list chunk node_ids

Save all to `decisions.json`.

### Step 5: Build Court nodes

Create one node per court. Deduplicate — if multiple decisions are from the same court, create only one court node.

Save to `courts.json`.

### Step 6: Build LegalBranch taxonomy

Create the tree of legal branches. Root branches have `parent_branch: null`.

Save to `legal_branches.json`.

### Step 7: Build Concept nodes

Create nodes for key legal concepts referenced in your data.

Save to `concepts.json`.

### Step 8: Build structural edges

Create all edges in `structural_edges.json`. Required edges per entity:

**For each Law:**
- `CONTAINS` → each Article

**For each Article:**
- `CONTAINS` → each Paragraph
- (reciprocal) each Paragraph → `PARENT_NODE` → Article

**For each Paragraph with Clauses:**
- `CONTAINS` → each Clause
- (reciprocal) each Clause → `PARENT_NODE` → Paragraph

**For each Decision:**
- `CONTAINS` → each DecisionChunk
- (reciprocal) each DecisionChunk → `PARENT_NODE` → Decision
- `DECIDED_BY` → Court
- `APPEAL_CHAIN` → lower court Decision (if applicable)
- `LEGAL_BRANCH` → LegalBranch

**For each DecisionRationale:**
- `RATIONALE_OF` → Decision

**For each DecisionChunk with references:**
- `REFERENCES` → referenced Article/Decision nodes

**For each Article / Decision / Concept with a legal branch:**
- `LEGAL_BRANCH` → LegalBranch

**For each LegalBranch with a parent:**
- `PARENT_BRANCH` → parent LegalBranch

### Step 9: Validate

Run validation before building:

```bash
python -c "
import json
for f in ['laws','articles','decisions','courts','legal_branches','concepts']:
    data = json.load(open(f'graph_data/nodes/{f}.json'))
    ids = [n['node_id'] for n in data]
    assert len(ids) == len(set(ids)), f'Duplicate IDs in {f}.json'
    for n in data:
        assert n.get('node_id'), f'Missing node_id in {f}'
        assert n.get('node_type'), f'Missing node_type in {f}'
        assert len(n.get('embed_text','')) >= 5, f'embed_text too short: {n[\"node_id\"]}'
    print(f'{f}.json: OK ({len(data)} nodes)')
"
```

### Step 10: Build the graph

```bash
python -m service pipeline build --clean
```

This will:
1. Load all node files
2. Validate all nodes
3. Load and validate all edges
4. Generate embeddings (uses cache for previously embedded nodes)
5. Upsert into Neo4j
6. Compute dynamic edges

---

## 13. Complete Minimal Example

Here is the smallest valid dataset with one law, one article, one decision, and proper edges.

### `graph_data/nodes/laws.json`

```json
[
  {
    "node_id": "kanun_tbk_6098",
    "node_type": "law",
    "embed_text": "Türk Borçlar Kanunu kanun numarası 6098 borç ilişkileri sözleşme haksız fiil",
    "metadata": {
      "law_name": "Türk Borçlar Kanunu",
      "abbreviation": "TBK",
      "law_number": 6098,
      "adoption_date": "2011-01-11",
      "effective_date": "2012-07-01",
      "domain": ["borç hukuku", "sözleşme hukuku"]
    }
  }
]
```

### `graph_data/nodes/articles.json`

```json
[
  {
    "node_id": "TBK_M19",
    "node_type": "article",
    "embed_text": "Türk Borçlar Kanunu madde 19 muvazaa bir sözleşmenin türünün ve içeriğinin belli edilmesinde tarafların gerçek ortak iradeleri esas alınır",
    "metadata": {
      "law_number": 6098,
      "law_abbreviation": "TBK",
      "article_number": 19,
      "article_title": "İrade Beyanlarında Yorum",
      "effective_date": "2012-07-01",
      "is_repealed": false,
      "subject": "muvazaa, irade beyanı, gerçek irade",
      "legal_branch": "borçlar hukuku",
      "paragraphs": ["TBK_M19_F1"]
    }
  },
  {
    "node_id": "TBK_M19_F1",
    "node_type": "paragraph",
    "embed_text": "Bir sözleşmenin türünün ve içeriğinin belirlenmesinde tarafların yanlışlıkla veya gerçek amaçlarını gizlemek için kullandıkları sözcüklere bakılmaksızın gerçek ve ortak iradeleri esas alınır.",
    "metadata": {
      "law_number": 6098,
      "law_abbreviation": "TBK",
      "article_number": 19,
      "paragraph_number": 1,
      "effective_date": "2012-07-01",
      "is_repealed": false,
      "subject": "muvazaa, gerçek irade",
      "legal_branch": "borçlar hukuku",
      "parent_node": "TBK_M19"
    }
  }
]
```

### `graph_data/nodes/decisions.json`

```json
[
  {
    "node_id": "KARAR_YRG_4HD_2020_1001",
    "node_type": "decision",
    "embed_text": "Yargıtay 4. Hukuk Dairesi 2020/1001 esas sözleşme muvazaa tespit TBK madde 19 onama",
    "metadata": {
      "court_type": "supreme_court",
      "court_name": "Yargıtay 4. Hukuk Dairesi",
      "chamber": "4. Hukuk Dairesi",
      "docket_number": "2020/1001",
      "decision_number": "2021/5678",
      "decision_date": "2021-09-15",
      "subject": "sözleşme muvazaası tespiti",
      "outcome": "ONAMA",
      "referenced_laws": ["TBK/19"],
      "legal_branch": "borçlar hukuku",
      "chunks": [
        "KARAR_YRG_4HD_2020_1001_B1",
        "KARAR_YRG_4HD_2020_1001_B2"
      ]
    }
  },
  {
    "node_id": "KARAR_YRG_4HD_2020_1001_GEREKCESI",
    "node_type": "decision_rationale",
    "embed_text": "TBK m.19 uyarınca tarafların gerçek iradesi araştırılmıştır. Sözleşmenin görünürdeki bedeli ile gerçek bedel arasındaki oransızlık muvazaa karinesi olarak değerlendirilmiştir.",
    "metadata": {
      "parent_decision": "KARAR_YRG_4HD_2020_1001",
      "rationale_type": "cassation_review",
      "referenced_laws": ["TBK/19"],
      "key_principles": ["muvazaa tespiti", "gerçek irade araştırması"],
      "legal_branch": "borçlar hukuku"
    }
  },
  {
    "node_id": "KARAR_YRG_4HD_2020_1001_B1",
    "node_type": "decision_chunk",
    "embed_text": "Davacı, davalı ile aralarında yapılan 15.03.2018 tarihli gayrimenkul satış sözleşmesinin muvazaalı olduğunu iddia etmiştir. Satış bedeli sözleşmede 100.000 TL olarak gösterilmiş; ancak taşınmazın piyasa değeri bilirkişi raporuna göre 750.000 TL olarak tespit edilmiştir.",
    "metadata": {
      "parent_decision": "KARAR_YRG_4HD_2020_1001",
      "chunk_type": "case_summary",
      "chunk_order": 1,
      "chunk_title": "Olay Özeti",
      "legal_branch": "borçlar hukuku",
      "char_count": 312
    }
  },
  {
    "node_id": "KARAR_YRG_4HD_2020_1001_B2",
    "node_type": "decision_chunk",
    "embed_text": "TBK m.19 çerçevesinde tarafların gerçek ve ortak iradeleri değerlendirilmiştir. Bedel oransızlığı açık muvazaa karinesi olup davalı aksini ispat edememiştir. İlk derece mahkemesinin muvazaa tespiti hukuka uygundur.",
    "metadata": {
      "parent_decision": "KARAR_YRG_4HD_2020_1001",
      "chunk_type": "legal_evaluation",
      "chunk_order": 2,
      "chunk_title": "Hukuki Değerlendirme",
      "referenced_laws": ["TBK/19"],
      "key_principles": ["muvazaa tespiti", "bedel oransızlığı"],
      "legal_branch": "borçlar hukuku",
      "char_count": 265
    }
  }
]
```

### `graph_data/nodes/courts.json`

```json
[
  {
    "node_id": "MAHKEME_YARGITAY_4_HUKUK_DAIRESI",
    "node_type": "court",
    "embed_text": "Yargıtay 4. Hukuk Dairesi Ankara genel hukuk",
    "metadata": {
      "court_name": "Yargıtay 4. Hukuk Dairesi",
      "court_type": "supreme_court",
      "city": "Ankara",
      "chamber": "4. Hukuk Dairesi",
      "specialization": ["genel hukuk"]
    }
  }
]
```

### `graph_data/nodes/legal_branches.json`

```json
[
  {
    "node_id": "HD_OZEL_HUKUK",
    "node_type": "legal_branch",
    "embed_text": "ozel hukuk hukuk dalı türk hukuk sistemi",
    "metadata": {
      "branch_name": "ozel hukuk",
      "parent_branch": null,
      "key_laws": []
    }
  },
  {
    "node_id": "HD_BORCLAR_HUKUKU",
    "node_type": "legal_branch",
    "embed_text": "borçlar hukuku hukuk dalı türk hukuk sistemi",
    "metadata": {
      "branch_name": "borçlar hukuku",
      "parent_branch": "HD_OZEL_HUKUK",
      "key_laws": ["kanun_tbk_6098"]
    }
  }
]
```

### `graph_data/nodes/concepts.json`

```json
[
  {
    "node_id": "KAVRAM_MUVAZAA",
    "node_type": "concept",
    "embed_text": "Muvazaa danışıklı işlem görünürdeki işlem gerçek irade uyuşmazlığı TBK madde 19",
    "metadata": {
      "concept_name": "Muvazaa",
      "definition": "Tarafların gerçek iradelerine uymayan bir işlem yapmaları; danışıklı işlem.",
      "synonyms": ["danışıklı işlem", "görünürdeki işlem"],
      "related_laws": ["TBK_M19"],
      "legal_branch": "borçlar hukuku"
    }
  }
]
```

### `graph_data/edges/structural_edges.json`

```json
[
  {
    "edge_id": "E_000001",
    "source": "kanun_tbk_6098",
    "target": "TBK_M19",
    "edge_type": "CONTAINS",
    "weight": 1.0
  },
  {
    "edge_id": "E_000002",
    "source": "TBK_M19",
    "target": "TBK_M19_F1",
    "edge_type": "CONTAINS",
    "weight": 1.0
  },
  {
    "edge_id": "E_000003",
    "source": "TBK_M19_F1",
    "target": "TBK_M19",
    "edge_type": "PARENT_NODE",
    "weight": 1.0
  },
  {
    "edge_id": "E_000004",
    "source": "KARAR_YRG_4HD_2020_1001_GEREKCESI",
    "target": "KARAR_YRG_4HD_2020_1001",
    "edge_type": "RATIONALE_OF",
    "weight": 1.0
  },
  {
    "edge_id": "E_000005",
    "source": "KARAR_YRG_4HD_2020_1001",
    "target": "MAHKEME_YARGITAY_4_HUKUK_DAIRESI",
    "edge_type": "DECIDED_BY",
    "weight": 1.0
  },
  {
    "edge_id": "E_000006",
    "source": "KARAR_YRG_4HD_2020_1001",
    "target": "KARAR_YRG_4HD_2020_1001_B1",
    "edge_type": "CONTAINS",
    "weight": 1.0
  },
  {
    "edge_id": "E_000007",
    "source": "KARAR_YRG_4HD_2020_1001_B1",
    "target": "KARAR_YRG_4HD_2020_1001",
    "edge_type": "PARENT_NODE",
    "weight": 1.0
  },
  {
    "edge_id": "E_000008",
    "source": "KARAR_YRG_4HD_2020_1001",
    "target": "KARAR_YRG_4HD_2020_1001_B2",
    "edge_type": "CONTAINS",
    "weight": 1.0
  },
  {
    "edge_id": "E_000009",
    "source": "KARAR_YRG_4HD_2020_1001_B2",
    "target": "KARAR_YRG_4HD_2020_1001",
    "edge_type": "PARENT_NODE",
    "weight": 1.0
  },
  {
    "edge_id": "E_000010",
    "source": "KARAR_YRG_4HD_2020_1001_B2",
    "target": "TBK_M19",
    "edge_type": "REFERENCES",
    "weight": 0.9,
    "properties": { "reference_type": "law_article" }
  },
  {
    "edge_id": "E_000011",
    "source": "KARAR_YRG_4HD_2020_1001",
    "target": "HD_BORCLAR_HUKUKU",
    "edge_type": "LEGAL_BRANCH",
    "weight": 0.9
  },
  {
    "edge_id": "E_000012",
    "source": "KAVRAM_MUVAZAA",
    "target": "HD_BORCLAR_HUKUKU",
    "edge_type": "LEGAL_BRANCH",
    "weight": 0.9
  },
  {
    "edge_id": "E_000013",
    "source": "HD_BORCLAR_HUKUKU",
    "target": "HD_OZEL_HUKUK",
    "edge_type": "PARENT_BRANCH",
    "weight": 1.0
  },
  {
    "edge_id": "E_000014",
    "source": "TBK_M19",
    "target": "KAVRAM_MUVAZAA",
    "edge_type": "RELATED_CONCEPT",
    "weight": 0.9
  }
]
```

---

## 14. Common Mistakes to Avoid

| # | Mistake | Consequence | Fix |
|---|---------|-------------|-----|
| 1 | Using hyphens or spaces in `node_id` | Schema validation fails | Use underscores only |
| 2 | Referencing a node_id in an edge that doesn't exist in any node file | Edge silently skipped (warning) | Ensure all referenced IDs exist |
| 3 | Putting metadata fields at top level instead of inside `metadata` | Fields ignored by pipeline | Wrap in `metadata: { ... }` |
| 4 | Empty or < 5 char `embed_text` | Node validation fails | Write descriptive text |
| 5 | Duplicate `node_id` across files | Last one silently wins | Ensure global uniqueness |
| 6 | Missing reciprocal edges (CONTAINS without PARENT_NODE) | Graph traversal is one-directional | Always create both directions |
| 7 | Very long `embed_text` (> 700 chars) on non-chunk nodes | Truncated by embedding model | Keep summary nodes concise |
| 8 | Not creating DECIDED_BY edges for decisions | Decisions disconnected from courts | Always link Decision→Court |
| 9 | Forgetting RATIONALE_OF edge for DecisionRationale | Rationale orphaned from decision | Always link DecisionRationale→Decision |
| 10 | Non-sequential edge_ids | No error, but hard to maintain | Use sequential `E_000001` pattern |
| 11 | Chunk text too small (< 100 chars) | Poor embedding quality | Merge with adjacent chunk |
| 12 | Not setting `chunks` on parent Decision | Query engine can't group chunks | List all chunk IDs in parent |

---

## Appendix: Edge Type Reference Card

```
STRUCTURAL HIERARCHY:
  Law ──CONTAINS──→ Article ──CONTAINS──→ Paragraph ──CONTAINS──→ Clause
  Paragraph ──PARENT_NODE──→ Article
  Clause ──PARENT_NODE──→ Paragraph
  ArticleVersion ──VERSION_OF──→ Article
  ArticleVersion ──PARENT_NODE──→ Article

DECISION STRUCTURE:
  Decision ──CONTAINS──→ DecisionChunk
  DecisionChunk ──PARENT_NODE──→ Decision
  DecisionRationale ──RATIONALE_OF──→ Decision
  Decision ──DECIDED_BY──→ Court

JUDICIAL CHAIN:
  Yargıtay_Decision ──APPEAL_CHAIN──→ BAM_Decision ──APPEAL_CHAIN──→ FirstInstance_Decision

CROSS-REFERENCES:
  Decision/DecisionChunk/DecisionRationale ──REFERENCES──→ Article/Decision

TAXONOMY:
  (any) ──LEGAL_BRANCH──→ LegalBranch
  LegalBranch ──PARENT_BRANCH──→ LegalBranch

CONCEPTS:
  Article/Decision/Paragraph ──RELATED_CONCEPT──→ Concept

DYNAMIC (auto-computed):
  Article ~~SAME_LAW~~ Article               (same law_number)
  DecisionChunk ~~SAME_LAW~~ DecisionChunk   (same parent_decision)
  Decision ~~CONFLICTING_DECISION~~ Decision  (same topic, opposite result)
  * ~~SEMANTIC_SIMILAR~~ *                    (cosine > 0.82)
```
