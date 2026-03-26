# End-to-End Architecture: Ingestion & Inference

How the system works at the graph-complete state (through Tier 10). Each step references the tier/epic where it's built.

---

## INGESTION PIPELINE (Offline, Batch)

Runs once per document, then incrementally as new documents arrive from S3.

```
S3 (50M+ markdown files)
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 1: Parse & Ingest (Tier 2, Epic 2.1)              │
│                                                         │
│ Input:  Raw .md file from S3                            │
│ Output: Row in PG `documents` table                     │
│                                                         │
│ Extract from filename/headers:                          │
│   • doc_id (deterministic hash of court|daire|esas_no)  │
│   • esas_no, karar_no, court, daire                     │
│   • court_level (1-4), law_branch (hukuk/ceza/idari)    │
│   • decision_date                                       │
│                                                         │
│ Store: PostgreSQL `documents` table                     │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 2: Extract Content Metadata (Tier 5, Epic 5.1)    │
│                                                         │
│ Input:  Full document text                              │
│ Output: Additional fields on `documents` row            │
│                                                         │
│ Extract by keywords/LLM:                                │
│   • decision_type: onama/bozma/kısmen_bozma/direnme    │
│   • decision_authority: daire_karari/genel_kurul/ibk    │
│                                                         │
│ Store: PG `documents.decision_type`,                    │
│        `documents.decision_authority`                    │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 3: Chunk (Tier 2→5)                                │
│                                                         │
│ Tier 2: Fixed-size (512 tok, 50 overlap)                │
│ Tier 5: Structure-aware (section boundaries:            │
│          gerekçe, hüküm, iddia, savunma, karşı_oy)     │
│                                                         │
│ Each chunk: chunk_id, doc_id, chunk_index, text,        │
│             section_type (Tier 5+)                       │
│                                                         │
│ Store: PG `chunks` table                                │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 4: Generate Summary (Tier 5, Epic 5.3)            │
│                                                         │
│ Input:  Full document text                              │
│ Output: 300-500 token structured summary                │
│         (legal issue, court, holding, outcome,          │
│          referenced statutes)                            │
│                                                         │
│ Store: PG `documents.summary`                           │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 5: Extract Holding (Tier 9, Epic 9.1)             │
│                                                         │
│ Input:  Full document text                              │
│ Output: 1-3 sentence holding (binding legal principle)  │
│                                                         │
│ Store: PG `documents.holding_text`                      │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 6: Embed (Tier 3→6)                                │
│                                                         │
│ What gets embedded:                                     │
│   • Every chunk (Tier 3+)                               │
│   • Every summary (Tier 5, Epic 5.4)                    │
│   • Contextual prefix + chunk (Tier 6, Epic 6.3)        │
│                                                         │
│ Model: TBD by shootout (Tier 3.3 / 6.1)                │
│                                                         │
│ Store: Milvus `chunks` collection                       │
│        Milvus `summaries` collection (Tier 5+)          │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 7: BM25 Index (Tier 4, Epic 4.1)                  │
│                                                         │
│ Index all chunk texts with Turkish stemming             │
│ (Milvus sparse vectors, or Elasticsearch)               │
│                                                         │
│ Store: Milvus sparse index or Elasticsearch             │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 8: Citation Extraction (Tier 7, Epic 7.1)         │
│                                                         │
│ Input:  Full document text                              │
│ Output: Citation edges (source_doc → target_doc)        │
│                                                         │
│ Method: Regex (Turkish citation patterns) + LLM         │
│ Resolve: target case number → doc_id in corpus          │
│ Unresolved citations logged separately                  │
│                                                         │
│ Store: PG `citations` table or Graph DB                 │
│        (source_doc_id, target_doc_id, snippet)          │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 9: Citation Treatment Classification (Tier 10)    │
│                                                         │
│ For each citation edge, classify:                       │
│   FOLLOWS / DISTINGUISHES / OVERRULES /                 │
│   CRITICIZES / NEUTRAL                                  │
│                                                         │
│ Input:  Citation context paragraph + both summaries     │
│ Method: LLM classification                              │
│                                                         │
│ Store: `citations.treatment` field                      │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 10: Graph Metrics (Tier 7, Epic 7.3 + Tier 10)   │
│                                                         │
│ Compute on full citation graph:                         │
│   • PageRank (authority score)                          │
│   • In-degree, out-degree                               │
│   • Treatment-weighted PageRank (FOLLOWS=1.5x,          │
│     CRITICIZES=0.5x, OVERRULES=0x)                      │
│   • Case status: good_law / overruled / disputed /      │
│     superseded_by_ibk / conflicting_precedent           │
│                                                         │
│ Store: PG `documents.pagerank`, `documents.status`      │
│        Graph DB node properties                         │
└─────────────────────────────────────────────────────────┘
```

### Storage State After Full Ingestion

| Store | What's in it |
|-------|-------------|
| **S3** | Raw markdown files (source of truth) |
| **PostgreSQL** | `documents` (metadata + summary + holding + pagerank + status), `chunks` (text + section_type), `citations` (edges + treatment), `runs`/`query_metrics` (eval) |
| **Milvus** | `chunks` collection (dense vectors), `summaries` collection (dense vectors), potentially sparse BM25 vectors |
| **Graph DB (TBD)** | Court hierarchy (static), citation network (from PG citations), PageRank/degree on nodes |

---

## INFERENCE PIPELINE (Online, Per-Query)

A lawyer sends a query. Here's exactly what happens:

```
Lawyer query: "iş kazası nedeniyle tazminat hesaplama yöntemi"
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 1: Query Analysis (Tier 11, Epic 11.1)            │
│ [NOT in Tier 10 — but noted for completeness]          │
│                                                         │
│ LLM classifies:                                        │
│   query_type: "precedent_search"                        │
│   entities: {statutes: ["TBK m.49"], concepts:          │
│              ["iş kazası", "tazminat hesaplama"]}        │
│   reformulated_query: "..."                             │
│                                                         │
│ Until Tier 11: raw query passes through unchanged       │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 2: Multi-Path Retrieval (Tiers 3-8)               │
│                                                         │
│ Three parallel retrieval paths:                         │
│                                                         │
│ PATH A — Dense (Tier 3+):                               │
│   Embed query → search Milvus `chunks` → top 100       │
│   Also search Milvus `summaries` (Tier 5+) → top 50    │
│                                                         │
│ PATH B — BM25/Sparse (Tier 4+):                         │
│   Tokenize + stem query → BM25 search → top 100        │
│                                                         │
│ PATH C — Graph (Tier 8, Epic 8.3):                      │
│   Take top-5 docs from dense → expand 1-hop in          │
│   citation graph → neighbors become candidates          │
│                                                         │
│ Optional metadata filters applied here:                 │
│   court_level_min, date_after, law_branch, daire        │
│   (Tier 8, Epic 8.4 — Milvus native filtering)         │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 3: Fusion & Document Aggregation (Tiers 3-4)      │
│                                                         │
│ 1. RRF across all paths:                                │
│    RRF_score(d) = Σ 1/(k + rank_i(d)), k=60            │
│                                                         │
│ 2. Chunk → Document aggregation:                        │
│    max/mean/combsum per doc_id → ranked doc list        │
│                                                         │
│ Output: top 30 candidate documents with scores          │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 4: Cross-Encoder Re-Ranking (Tier 6, Epic 6.2)   │
│                                                         │
│ For each of top 30 docs:                                │
│   score = cross_encoder(query, doc.summary)             │
│                                                         │
│ Re-rank by cross-encoder score → top 10                 │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 5: Legal Authority Scoring (Tier 8)               │
│                                                         │
│ Apply on top of cross-encoder score:                    │
│                                                         │
│ a) PageRank boost (Epic 8.1):                           │
│    final = α * reranker_score +                         │
│            (1-α) * normalized_pagerank                   │
│                                                         │
│ b) Court-level boost (Epic 8.2):                        │
│    boosted = score × (1 + β × court_level / 4)          │
│                                                         │
│ c) İBK special boost:                                   │
│    if decision_authority == 'ibk': extra boost           │
│                                                         │
│ d) Treatment-aware penalty (Tier 10, Epic 10.3):        │
│    if status == 'overruled': score × 0.3                 │
│    if status == 'disputed': score × 0.6                  │
│    İBK: never penalized                                  │
│                                                         │
│ Output: top 10 documents, legally re-ranked              │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 6: Conflict Detection (Tier 9, Epic 9.2)         │
│                                                         │
│ On the top 10 results:                                  │
│   1. Compare holdings pairwise (cosine similarity)      │
│   2. If similar pair found → LLM: AGREES/DISAGREES?     │
│   3. Classify conflict type:                            │
│      • inter_daire (different chambers disagree)         │
│      • bozma_direnme (appeal chain conflict)             │
│      • temporal (same chamber changed position)          │
│   4. Check if İBK resolves the conflict                  │
│                                                         │
│ Output: conflict annotations on result set               │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ STEP 7: Response Assembly                               │
│                                                         │
│ For each document in top 10:                            │
│   • doc_id, score                                       │
│   • Metadata: court, daire, esas_no, karar_no, date     │
│   • court_level, decision_type, decision_authority       │
│   • summary, holding_text                                │
│   • pagerank_score                                       │
│   • status: good_law / overruled / disputed / ...        │
│   • if overruled: link to overruling case                │
│                                                         │
│ Plus:                                                    │
│   • conflicts: [{doc_a, doc_b, type, explanation,        │
│                   resolved_by_ibk}]                      │
│                                                         │
│ Return as JSON via POST /search                          │
└─────────────────────────────────────────────────────────┘
```

### Latency Budget (Target: < 8 seconds p95 at Tier 10)

| Step | Expected latency |
|------|-----------------|
| Query embedding | ~100ms (OpenAI API) |
| Milvus dense search (chunks + summaries) | ~50ms |
| BM25 search | ~50ms |
| Graph 1-hop expansion | ~20ms |
| RRF + aggregation | ~10ms |
| Cross-encoder re-ranking (30 docs) | ~2-5s (GPU dependent) |
| Legal authority scoring | ~10ms (lookups) |
| Conflict detection (10 docs, pairwise LLM) | ~2-4s (LLM calls) |
| Response assembly | ~20ms |
| **Total** | **~5-9s** |

Bottleneck: cross-encoder + conflict detection LLM calls. Both are Tier 6+ / Tier 9+ additions.
