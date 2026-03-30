# Legal Jurisprudence Retrieval System — Tiered Implementation Plan

> Each tier builds on the previous one. Each tier is independently deployable and testable.
> Every epic has concrete acceptance criteria that can be verified with data or observation.
> No time/effort estimates are included — only scope and measurable outcomes.
>
> **Jurisdiction:** Turkish law (Türk Hukuku). All Turkish-specific details reference `docs/turkish-law-reference.md` — the companion cheat sheet for LLM agents working with Turkish legal data. That file covers the full court hierarchy, citation formats, appeal flows, and code transition dates.

> **System role:** This is a context assembly backend for a downstream query-making LLM. The system's job is to retrieve and assemble the right constellation of documents so the LLM can reason correctly about Turkish legal questions. The critical failure mode is **missing documents that change the legal answer** — e.g., returning 5 semantically similar ONAMA decisions when a BOZMA exists that reverses the key precedent. All design decisions prioritise retrieval completeness over presentation.

---

## Tier 1 — Measurement Infrastructure

**Goal:** Before building any retrieval, build the tools to know whether retrieval works. Nothing else gets built until this tier is done.

---

### Epic 1.1 — Gold Standard Evaluation Dataset

**What to build:**
- Curate a test set of **minimum 50 legal queries** with known relevant case law documents (ground truth)
- Each query should have:
  - The query text (natural language, as a lawyer would phrase it)
  - A list of known relevant document IDs (the "correct answers")
  - Relevance grades: 3 = directly on point, 2 = related, 1 = tangentially relevant, 0 = irrelevant
  - For at least 10 queries: annotate which results have contradictory/opposing holdings
- Source these from practicing Turkish lawyers — no existing Turkish legal retrieval benchmarks exist (COLIEE is Japanese/Canadian, BSARD is Belgian)
- Queries must be in Turkish and reflect how Turkish lawyers actually search (e.g., "iş kazası nedeniyle tazminat", "TCK m.157 dolandırıcılık emsal karar")
- Define a schema for the query file (JSON or CSV) and document it
- Each query should also have: `law_branch` (hukuk/ceza/idari), `relevant_court` (Yargıtay daire, Danıştay daire, BAM, etc.), `query_type`, `difficulty`

**Acceptance criteria:**
- [ ] 50+ queries with graded relevance judgments exist in a structured JSON/CSV file
- [ ] At least 10 queries are tagged with known contradictory precedent pairs
- [ ] All three judicial branches are covered: adli yargı (hukuk + ceza), idari yargı, and at least 2 queries for Uyuşmazlık Mahkemesi or AYM bireysel başvuru
- [ ] Queries span at least 5 different Yargıtay/Danıştay daireler
- [ ] A second lawyer (not the one who created the set) has validated at least 20 queries
- [ ] Schema is documented: field names, types, allowed values, an example entry

---

### Epic 1.2 — Evaluation Harness

**What to build:**
- A script that takes `(query, retrieved_document_ids, ground_truth)` and computes:
  - **Recall@5, Recall@10, Recall@20**
  - **NDCG@5, NDCG@10** (using the graded relevance scores)
  - **MRR** (mean reciprocal rank)
  - **Hit Rate@5** (at least one relevant doc in top 5)
- Results are logged to PostgreSQL with: run ID, timestamp, configuration label, git commit hash
- A comparison command: given two run IDs, show metric deltas and per-query wins/losses

**Acceptance criteria:**
- [ ] Running `python evaluate.py --run-id X --run-id Y` outputs a table showing metric differences between two configurations
- [ ] Per-query breakdown is available (not just averages) to identify which queries improved/regressed
- [ ] All metrics match expected values on a hand-crafted toy example (3 queries, 10 documents, manually computed expected values documented)
- [ ] The toy example and expected values are checked into the repo as a test

---

### Tier 1 — Exit Criteria

| Artifact | Verification |
|----------|-------------|
| Eval dataset file | Exists, passes schema validation, has 50+ entries |
| Eval harness script | Runs on toy example, produces correct metrics |
| Comparison mode | Produces diff table for two run IDs |

---

## Tier 2 — Data Pipeline & Storage

**Goal:** Get all jurisprudence documents ingested, parsed, and stored with metadata. No retrieval yet — just the data foundation.

---

### Epic 2.1 — Document Ingestion Pipeline

**What to build:**
- A pipeline that reads jurisprudence markdown files from a configurable directory
- Extracts Turkish-specific metadata from each file:
  - **Esas No** (docket number, e.g., `2021/1234`) — identifies the case at filing
  - **Karar No** (decision number, e.g., `2022/5678`) — identifies the decision
  - **Daire/Mahkeme** (chamber/court, e.g., `Yargıtay 4. Hukuk Dairesi`, `Danıştay 7. Daire`)
  - **Karar Tarihi** (decision date)
  - **Law branch:** `hukuk` / `ceza` / `idari` — inferred from court name
  - **Court level:** 1=İlk Derece, 2=BAM/BİM, 3=Yargıtay Daire/Danıştay Daire, 4=Yargıtay HGK/CGK/Danıştay İDDGK/VDDGK
  - **Note:** `decision_type` (bozma/onama/direnme) and `decision_authority` (daire_karari/genel_kurul_karari/ibk) require reading document content — these are extracted in Tier 5, not here.
- Assigns a unique document ID (deterministic hash of Esas No + Karar No + Court, so re-ingestion is idempotent)
- Stores metadata in PostgreSQL

**Acceptance criteria:**
- [x] All documents in the corpus are ingested with no failures (zero documents dropped — or dropped docs are logged with reason)
- [x] Each document has metadata fields: `doc_id`, `esas_no`, `karar_no`, `court`, `daire`, `court_level`, `law_branch`, `decision_date`, `file_path`
- [x] Esas No and Karar No are extracted correctly for >= 95% of documents (spot-check 30)
- [x] `SELECT count(*) FROM documents` returns the expected corpus size
- [x] Court level distribution query produces sensible numbers (most docs should be level 3 Yargıtay/Danıştay daire decisions)
- [x] Metadata extraction accuracy: spot-check 30 random documents — all have correct Esas No, Karar No, court, daire, and date
- [x] Re-running ingestion on the same directory produces no duplicates (idempotent)

---

### Epic 2.2 — Fixed-Size Chunking with Document Back-Pointers

**What to build:**
- Chunk each document into fixed-size pieces (512 tokens, 50-token overlap as starting point)
- Each chunk stores: `chunk_id`, `doc_id`, `chunk_index`, `text`, `metadata` (inherited from parent document)
- Store chunks in a table alongside documents

**Acceptance criteria:**
- [x] Every chunk has a valid `doc_id` back-pointer
- [x] No chunk exceeds the configured token limit (verify with tokenizer count on 100 random chunks)
- [x] Reconstructing all chunks for a given `doc_id` in order reproduces the original document text (minus overlap dedup)
- [x] Total chunk count and average chunks-per-document are logged

---

### Tier 2 — Exit Criteria

| Artifact | Verification |
|----------|-------------|
| Document store | All corpus documents ingested with metadata |
| Chunk store | All documents chunked, back-pointers intact |
| Idempotency | Re-run produces same state |

---

## Tier 3 — Naive Dense Retrieval Baseline

**Goal:** First working retrieval. Embed chunks, search by cosine similarity, aggregate to document level. Establish the first real baseline numbers.

---

### Epic 3.1 — Embedding & Vector Index

**What to build:**
- Embed all chunks using an embedding model (start with `text-embedding-3-small` which supports Turkish, or `multilingual-e5-large`)
- **Note:** The corpus is in Turkish. Multilingual models are the safer default, but English-only models (like `BGE-base-en-v1.5`) can sometimes work via cross-lingual transfer — include them in the Tier 3 shootout to verify empirically.
- Store embeddings in a vector database (Qdrant, Chroma, or FAISS)
- Implement cosine similarity search: query → top-k chunks

> **Implementation note (completed):** Milvus chosen as vector DB (not Qdrant/FAISS) — the dataset is scaling to 50M+ docs (~750M vectors) next month. Milvus's distributed architecture, S3-native storage, and GPU-accelerated indexing are needed at this scale. IVF_FLAT index used for current eval corpus; will switch to IVF_PQ or HNSW at production scale. Project restructured into FastAPI microservice (`app/`) ahead of Epic 4.2 — see `app/core/`, `app/ingestion/`, `app/retrieval/`, `app/api/`.
>
> **Baseline (text-embedding-3-small, Milvus, max-score aggregation):** Recall@5=0.38, Recall@10=0.55, Recall@20=0.74, NDCG@10=0.45, MRR=0.55, Hit Rate@5=0.78

**Acceptance criteria:**
- [x] Vector DB contains exactly `num_chunks` embeddings (counts match) — 1143/1143
- [x] A sample query returns chunks from multiple different documents — 3 unique docs in top 10
- [x] Retrieval returns results for all 50 evaluation queries (no empty results) — 64/64 queries returned results

---

### Epic 3.2 — Document-Level Score Aggregation

> **Implementation note (completed):** All three aggregation strategies implemented in `app/retrieval/aggregation.py`. R&D comparison completed 2026-03-26. **Winner: max_score** — best Recall@10 (0.545) and NDCG@10 (0.445). CombSUM worst across all metrics. Mean trades Recall@10 for MRR. Full results in `docs/experiment-results.md`.

**What to build:**
- Given top-k retrieved chunks (top 100), aggregate scores to the document level
- Implement three aggregation strategies:
  1. **Max-score:** document score = highest chunk score
  2. **Mean-score:** document score = average of all its retrieved chunk scores
  3. **CombSUM:** document score = sum of all its retrieved chunk scores
- Return top-N documents (not chunks), with file path to the full document

**R&D task — Aggregation strategy comparison:**
- Run all three aggregation strategies through the eval harness
- Measure Recall@5, Recall@10, NDCG@10 for each
- Also measure: average number of unique documents in top-100 chunks (diversity metric)

**Acceptance criteria:**
- [x] The system returns `doc_id`s (not `chunk_id`s) in the final ranked list
- [x] Aggregation comparison table shows all three strategies with Recall@5, Recall@10, NDCG@10 — see `docs/experiment-results.md`
- [x] The chosen strategy is documented with rationale — max_score wins on primary metrics
- [x] Given a result, the system can return the full document file path for each — via PG metadata join in API

---

### Epic 3.3 — Baseline Numbers & First R&D Comparisons

**R&D task — Embedding model shootout (4 models):**
- Re-index with exactly these four models on the same chunk set:
  1. `text-embedding-3-small` (OpenAI, 1536d, multilingual including Turkish)
  2. `multilingual-e5-large` (open-source, 1024d, strong on Turkish)
  3. `text-embedding-3-large` (OpenAI, 3072d, multilingual including Turkish)
  4. `BGE-base-en-v1.5` (open-source, 768d, English-only — include as a cross-lingual transfer baseline)
- Compare Recall@10, NDCG@10, and query latency (p50, p95)
- **Turkish-specific check:** Verify Turkish legal terms are embedded meaningfully — test with known synonym pairs (e.g., "tazminat" ↔ "zarar giderimi", "sanık" ↔ "fail")

**R&D task — Chunk size comparison:**
- Using the winning embedding model, re-index with three chunk sizes: **256, 512, 1024 tokens**
- Measure Recall@10 and NDCG@10 for each
- Pick the winner based on data

**Acceptance criteria:**
- [ ] Embedding model comparison table exists with Recall@10, NDCG@10, and latency for all 4 models
- [ ] Chunk size comparison table exists with Recall@10 and NDCG@10 for all 3 sizes
- [ ] Both choices are documented with rationale
- [ ] All results are logged in the cumulative metrics table (first entries)

---

### Tier 3 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Recall@10 | Measured and logged (this IS the baseline) | Eval harness |
| NDCG@10 | Measured and logged | Eval harness |
| MRR | Measured and logged | Eval harness |
| Hit Rate@5 | Measured and logged | Eval harness |

**These numbers are the anchor. Every subsequent tier must demonstrably improve on them.**

> **Implementation note — Embedding infrastructure (completed):** BAAI/bge-m3 (1024 dimensions) running locally via Hugging Face Text Embeddings Inference (TEI) Docker service on NVIDIA A100 GPU. OpenAI-compatible API at `localhost:8080`. Provider-agnostic: `app/retrieval/embeddings.py` uses the OpenAI client library pointed at `settings.embedding_base_url`, so switching to OpenAI or any other OpenAI-compatible provider requires only config changes (`embedding_base_url`, `embedding_model`, `embedding_dimension`). Docker service defined as `tei` in `docker-compose.yml`.

---

## Tier 4 — Graph Foundation

**Goal:** Make the citation network the second retrieval pillar alongside dense search. After this tier, retrieval is graph-augmented: dense similarity combined with citation authority via HippoRAG-style Personalized PageRank. The graph also establishes the court hierarchy as a queryable structure that subsequent tiers (legal authority scoring, conflict detection) will rely on.

---

### Epic 4.1 — Citation Extraction & PostgreSQL Storage

**What to build:**
- Regex-based citation extractor covering all Turkish citation formats:
  1. Full Yargıtay named: `Yargıtay N. Hukuk Dairesi'nin ... YYYY/NNNN Esas ... YYYY/NNNN Karar`
  2. Abbreviated: `Y.N.HD`, `Yargıtay N. HD` + esas/karar numbers
  3. HGK/CGK: `Hukuk Genel Kurulu`, `HGK`, `CGK` + esas/karar
  4. Danıştay: `Danıştay N. Dairesi`, `D.N.D.` + esas/karar
  5. İBK: `İçtihadı Birleştirme` + year/number
  6. BAM/BİM: city + `Bölge Adliye Mahkemesi N. Dairesi`
- Resolver: match extracted esas_no against `documents` table — confidence 1.0 = exact esas+karar+court match, 0.9 = esas+court, 0.7 = esas-only (single unambiguous match)
- Self-citations dropped silently
- Store resolved citations in new `citations` table; unresolvable citations logged in `unresolved_citations` (not dropped — preserved for future corpus growth)
- Add `pagerank_score FLOAT`, `citation_in_degree INTEGER`, `citation_out_degree INTEGER` columns to `documents`
- Offline pipeline: `app/ingestion/build_graph.py` (fourth step after embed.py), with `--no-neo4j` and `--skip-pagerank` flags

**New PG schema:**
```sql
citations(citation_id TEXT PK,            -- sha256[:16] of source_doc_id|esas_no|karar_no
          source_doc_id TEXT FK, target_doc_id TEXT FK NULLABLE,
          esas_no TEXT, karar_no TEXT, snippet TEXT, confidence FLOAT, extracted_at TIMESTAMPTZ)
unresolved_citations(id SERIAL PK, source_doc_id TEXT FK, raw_text TEXT, esas_no TEXT, reason TEXT, extracted_at TIMESTAMPTZ)
```

**Acceptance criteria:**
- [ ] `citations` table populated; extraction summary logged (total extracted / resolved / unresolved / resolution rate %)
- [ ] Resolution rate baseline established and logged — expected to be low on 95-doc corpus since most cited cases are outside it
- [ ] `unresolved_citations` contains all non-matchable raw citations with reason field
- [ ] Re-running `build_graph.py` is idempotent (upserts, no duplicates)

---

### Epic 4.2 — Neo4j Graph Build & Court Hierarchy

**What to build:**
- Neo4j 5.18-community added to docker-compose with APOC plugin
- `app/core/graphdb.py`: driver singleton + `get_session()` context manager (mirrors `vectordb.py` pattern)
- `app/graph/schema.py`: Cypher DDL string constants (constraints + indexes)
- `app/graph/neo4j_sync.py`:
  - `init_schema(session)`: run all DDL constraints and indexes
  - `upsert_court_hierarchy(session)`: static Court nodes + `APPEALS_TO` edges (Yargıtay=level3, BAM/BİM=level2, İlk Derece=level1; reference `docs/turkish-law-reference.md` Section 1 for full list)
  - `upsert_documents(session, conn)`: batch MERGE Document nodes from PG + `IN_COURT` edges (batch 500/tx)
  - `upsert_citations(session, resolved)`: batch MERGE `CITES` relationships from PG citations
  - `get_citation_neighbors(session, doc_ids, hops=1)`: **bidirectional** traversal (both "cites" and "is cited by") — in appellate law, being cited is as important as citing
- `app/ingestion/build_graph.py` calls all of the above as the fourth pipeline step

**Acceptance criteria:**
- [ ] Neo4j browser at :7474 shows Court nodes and APPEALS_TO hierarchy edges
- [ ] All 95 Document nodes exist in Neo4j with IN_COURT edges
- [ ] CITES relationship count matches PG `citations` table count
- [ ] `--no-neo4j` flag works (PG-only mode — skips Neo4j sync, populates PG only)

---

### Epic 4.3 — Graph-Augmented Retrieval (PPR Re-scoring)

**What to build:**
- `app/graph/metrics.py`:
  - `compute_pagerank_networkx(conn, alpha=0.85)`: reads citations from PG, builds `nx.DiGraph` including isolated nodes (no edges), runs networkx PageRank, normalizes scores to [0,1]
  - Write-back: `documents.pagerank_score` in PG and `Document.pagerank_score` in Neo4j
  - `compute_in_out_degree(conn)` + write-back to PG `documents` columns
- `app/retrieval/graph_retrieval.py`:
  - `compute_ppr_scores(seed_ids, all_candidates, conn, alpha=0.85)`: fetches citation subgraph for candidates from PG, builds nx.DiGraph, seeds PPR to query's dense results, normalizes; graceful degradation to uniform seed scores if no citation edges exist
  - `expand_and_rescore(dense_results, session, conn, top_k_seeds=5, hops=1, graph_weight=0.3)`: top-5 dense docs as seeds → get citation neighbors → PPR across seeds + neighbors → `final = (1 − w) × dense_norm + w × ppr` → sort by final score
  - `expand_and_rescore_fallback(dense_results)`: pure passthrough, for when Neo4j is unavailable
- `SearchRequest.use_graph: bool = True` (default on; allows A/B testing with `use_graph=false`)
- `DocumentResult` adds: `graph_score: float`, `is_graph_expansion: bool`, `pagerank_score: float`
- Search route: calls `expand_and_rescore()` wrapped in try/except — any exception logs WARNING and falls through to `expand_and_rescore_fallback()`. **The API must remain available when Neo4j is down.**

**Why PPR over raw in-degree:** PPR personalizes authority flow to the query's specific seed documents. A document cited by 50 irrelevant cases scores lower than one cited by 3 cases that are seeds — this is the core HippoRAG insight applied to legal citation networks.

**Acceptance criteria:**
- [ ] `POST /search` with `use_graph=true` returns results end-to-end
- [ ] `use_graph=false` returns pure dense results (unchanged from Tier 3)
- [ ] Recall@10 (graph) >= Recall@10 (dense) − 0.02 — no regression allowed; graph gains are expected at production corpus scale
- [ ] Kill Neo4j → API continues serving via dense fallback (no 500 errors)
- [ ] `documents.pagerank_score` > 0 for at least one document that has a resolved citation

---

### Tier 4 — Exit Criteria

| Criterion | Target | How to Verify |
|-----------|--------|---------------|
| Graph retrieval active | `use_graph=true` returns results | Manual API call |
| Citation resolution baseline | Rate logged, unresolved tracked | `build_graph.py` summary output |
| No Recall regression | Recall@10(graph) ≥ Recall@10(dense) − 0.02 | Eval harness: `graph-v1` vs `agg-max` run |
| Graceful degradation | API serves results when Neo4j is down | Kill Neo4j, run 5 queries |
| PageRank populated | Non-zero score for ≥ 1 doc with resolved citation | SQL: `SELECT COUNT(*) FROM documents WHERE pagerank_score > 0` |
| Court hierarchy in Neo4j | Court nodes + APPEALS_TO edges visible | Neo4j browser :7474 |

> **Implementation note — PPR scope:** Epic 4.3 (PPR Re-scoring) implements the HippoRAG-style Personalized PageRank that was originally planned as "Tier 13 — Advanced Graph RAG". That tier has been retired as its core content is complete. See `app/retrieval/graph_retrieval.py` for the implementation.

---

## Tier 5 — Legal Intelligence Extraction

**Goal:** Extract the legal metadata that transforms the citation graph from a flat network into a semantically rich legal knowledge graph. Without this tier, the retrieval system returns semantically similar documents that may all say the same thing. With it, the system knows which decisions were reversed (BOZMA), which citations are supportive vs. adversarial, and can assemble context that includes both sides of a legal question for the downstream LLM.

**Why now (right after Tier 4):** Disposition and treatment data are prerequisites for retrieval diversification. Delaying them means all subsequent tiers operate on a flat citation graph that cannot distinguish supporting from opposing authority. The extraction itself is cheap — regex at 95%+ accuracy for dispositions; keyword heuristics on existing `snippet` fields for treatment.

---

### Epic 5.1 — Disposition Extraction (SONUC Section Parsing)

**What to build:**
- Regex-based extractor that reads the SONUC/HUKUM section (last ~200 lines) of each document
- Extracts two fields:
  - **`disposition`**: `onama` / `bozma` / `kismen_bozma` / `red` / `kabul` / `direnme` / `unknown`
  - **`voting_method`**: `oy_birligi` (unanimous) / `oy_coklugu` (majority) / `unknown`
- Regex targets (confirmed by 95-document corpus analysis):
  - `BOZULMASINA` -> `bozma` (43% of corpus)
  - `ONANMASINA` -> `onama` (26%)
  - `REDDINE` -> `red` (16%)
  - `KABULUNE` -> `kabul` (11%)
  - `KISMEN BOZULMASINA` or `KISMEN ONANMASINA` -> `kismen_bozma`
  - `DIRENME` in SONUC context -> `direnme`
  - `oy birligiyle` -> `oy_birligi` (found in 36+ files)
  - `oy cokluguyla` -> `oy_coklugu`
- New file: `app/graph/disposition_extractor.py`
- New PG columns on `documents`: `disposition TEXT DEFAULT 'unknown'`, `voting_method TEXT DEFAULT 'unknown'`
- New Neo4j Document node properties: `disposition`, `voting_method`
- Integration into `app/ingestion/build_graph.py` pipeline (after document loading, before citation extraction)

**Acceptance criteria:**
- [ ] `disposition` populated for >= 90% of corpus documents (non-`unknown`)
- [ ] Spot-check 30 documents: `disposition` correct for >= 95%
- [ ] `voting_method` populated for >= 40% of corpus
- [ ] `unknown` rate < 15% — if higher, document which SONUC patterns are missing
- [ ] Pipeline is idempotent (re-run produces same results)
- [ ] Distribution logged: count per disposition type, count per voting method

---

### Epic 5.2 — Decision Type & Authority Classification

**What to build:**
- Higher-level classification inferred from court name, court level, and document metadata:
  - **`decision_type`**: `karar` (original first-instance) / `temyiz_incelemesi` (Yargitay appellate review) / `istinaf_incelemesi` (BAM review) / `unknown`
  - **`decision_authority`**: `daire_karari` / `genel_kurul_karari` / `ibk` / `unknown`
- Rules:
  - court_level=3 (Daire) -> `temyiz_incelemesi` + `daire_karari`
  - court_level=4 + HGK/CGK -> `temyiz_incelemesi` + `genel_kurul_karari`
  - court_level=4 + IBK -> `ibk`
  - court_level=2 (BAM/BIM) -> `istinaf_incelemesi`
  - court_level=1 (Ilk Derece) -> `karar`
- New PG columns on `documents`: `decision_type TEXT`, `decision_authority TEXT`
- New Neo4j Document node properties: `decision_type`, `decision_authority`

**Acceptance criteria:**
- [ ] Every document has `decision_type` and `decision_authority` populated
- [ ] Spot-check 30 documents: correct for >= 95%
- [ ] HGK/CGK documents correctly classified as `genel_kurul_karari`
- [ ] BAM documents correctly classified as `istinaf_incelemesi`

---

### Epic 5.3 — Citation Treatment Classification (Keyword Heuristics)

**What to build:**
- For each citation edge (PG `citations` table + Neo4j CITES), classify the treatment using keyword heuristics on the existing `snippet` field (120-char context window already captured by `app/graph/citation_extractor.py`):
  - **`AFFIRMS`** — snippet contains: "onanmasina", "isabetli", "uygun", "ayni gorusde", "yerinde"
  - **`REVERSES`** — snippet contains: "bozulmasina", "bozma", "isabetsiz", "yanlis", "hatali"
  - **`FOLLOWS`** — snippet contains: "uygun olarak", "dogrultusunda", "emsal", "yerlesik ictihat"
  - **`DISTINGUISHES`** — snippet contains: "farkli", "uygulanmaz", "bu davada", "ayrik"
  - **`NEUTRAL`** — default when no keyword match
- New file: `app/graph/treatment_classifier.py`
- New PG column: `citations.treatment TEXT DEFAULT 'NEUTRAL'`
- New Neo4j CITES relationship property: `treatment`
- Approach: add `treatment` as a property on existing CITES edges (not separate relationship types) to avoid breaking `get_citation_neighbors()` in `app/graph/neo4j_sync.py`

**Changes to existing files:**
- `app/graph/neo4j_sync.py`: `upsert_citations()` adds `r.treatment = row.treatment`
- `app/graph/neo4j_sync.py`: `get_citation_neighbors()` gains optional `treatment_filter` parameter
- `app/graph/resolver.py`: `ResolvedCitation` gains `treatment: str = "NEUTRAL"` field
- `app/ingestion/build_graph.py`: new step after citation resolution runs treatment classification

**Acceptance criteria:**
- [ ] `treatment` field populated for all citation edges
- [ ] On citations where snippet clearly indicates BOZMA or ONAMA: precision >= 0.90
- [ ] Distribution logged: count per treatment type
- [ ] Spot-check 20 AFFIRMS and 20 REVERSES classifications
- [ ] Neo4j CITES edges have queryable `treatment` property

---

### Epic 5.4 — Retrieval Diversification via Disposition & Treatment

**What to build:**
- Modify `app/retrieval/graph_retrieval.py` `expand_and_rescore()` to be disposition-aware:
  - When top-5 seed documents all share the same disposition (e.g., all `onama`), actively seek the opposing disposition by following REVERSES/AFFIRMS edges
  - If seed doc S has a CITES edge with `treatment='REVERSES'` pointing to doc T, include T as a high-priority expansion candidate
  - If seed doc S has `disposition='bozma'`, look for the original decision it reversed (follow CITES edges where S is source)
- New field on `GraphExpandedResult`: `expansion_reason: str` (values: `seed`, `citation_neighbor`, `opposing_disposition`, `reversal_chain`)
- New fields on `DocumentResult` in `app/models.py`: `disposition`, `voting_method`, `decision_type`, `decision_authority`
- New eval metric: **Disposition Diversity@10** — count of distinct dispositions in top-10, averaged across queries

**Acceptance criteria:**
- [ ] For queries where both ONAMA and BOZMA exist on the same legal issue, both dispositions appear in top-10
- [ ] When a seed document was BOZMA'd, the reversing decision appears in expanded results
- [ ] `expansion_reason` correctly labels why each non-seed document was included
- [ ] No regression in Recall@10
- [ ] Disposition Diversity@10 baselined and tracked in eval harness

---

### Tier 5 — Exit Criteria

| Criterion | Target | How to Verify |
|-----------|--------|---------------|
| Disposition coverage | >= 90% non-unknown | `SELECT COUNT(*) FROM documents WHERE disposition != 'unknown'` |
| Disposition accuracy | >= 95% on 30-doc check | Manual review |
| Treatment coverage | All citation edges | `SELECT COUNT(*) FROM citations WHERE treatment IS NOT NULL` |
| Treatment precision (clear cases) | >= 0.90 | 40-edge spot-check |
| Disposition Diversity@10 | Baselined and tracked | Eval harness with new metric |
| No Recall regression | Recall@10 >= Tier 4 baseline | Eval harness |

---

## Tier 6 — Hybrid Retrieval & Usable API

**Goal:** Add BM25, fuse sparse+dense, expose via API and UI. After this tier, a lawyer can actually use the system.

> **Note:** The primary consumer of this API is a downstream LLM, not a human user. The web UI (Epic 6.3) is useful for debugging and lawyer validation during development but is not the primary interface.

---

### Epic 6.1 — BM25 Index & Hybrid Fusion

**What to build:**
- Build a BM25 index over the same chunks (using `rank_bm25`, Elasticsearch, or Tantivy)
- **Turkish NLP consideration:** Turkish is agglutinative — suffixes change word forms drastically (e.g., "mahkemesinin" → "mahkeme"). Options to test include:
  - Elasticsearch with the `turkish` analyzer (built-in Snowball stemmer)
  - Zemberek-based stemming for a custom BM25 pipeline
  - A Turkish legal stopword list (e.g., "sayılı", "tarihli", "numaralı", "esas", "karar" are near-universal in legal documents)
- Implement **Reciprocal Rank Fusion (RRF)** to combine BM25 and dense results:
  `RRF_score(d) = Σ 1/(k + rank_i(d))` where k=60, across both retrievers
- Apply document-level aggregation after fusion

**R&D task — Hybrid vs. single retriever:**
- Compare three configurations:
  1. Dense only (Tier 3 baseline)
  2. BM25 only
  3. Hybrid (RRF)
- Measure Recall@10, NDCG@10
- Find 5 specific queries where hybrid wins over dense-only and 5 where it wins over BM25-only (qualitative evidence showing WHY hybrid helps)

**R&D task — Turkish stemming impact on BM25:**
- Compare three BM25 configurations:
  1. No stemming (raw tokens)
  2. Turkish stemming (Elasticsearch `turkish` analyzer or Zemberek)
  3. Turkish stemming + legal stopword list
- Measure Recall@10 for BM25-only on the eval set
- Pick the winner based on data

**Acceptance criteria:**
- [ ] BM25 returns results for all 50 evaluation queries
- [ ] Hybrid Recall@10 >= max(dense-only, BM25-only) on the evaluation set
- [ ] Comparison table with all three configs exists
- [ ] 10 qualitative examples (5 hybrid > dense, 5 hybrid > BM25) documented with explanations of what each retriever caught that the other missed
- [ ] BM25 stemming comparison table exists with Recall@10 for all 3 stemming configs

---

### Epic 6.2 — REST API

> **Implementation note (completed early):** Built as part of FastAPI restructure during Tier 3. The API is at `app/api/routes/search.py`, served by `app/main.py`. Response includes full document metadata (court, daire, esas_no, karar_no, decision_date, score).

**What to build:**
- `POST /search` with body `{"query": "...", "top_k": 10}`
- Response: `[{"doc_id": "...", "score": 0.85, "court": "...", "date": "...", "case_number": "...", "file_path": "..."}]`
- Health check endpoint: `GET /health`
- OpenAPI/Swagger docs auto-generated

**Acceptance criteria:**
- [x] API returns valid JSON for all 50 evaluation queries
- [ ] API response time is under 2 seconds (p95) for a single query — not yet benchmarked at scale
- [x] Invalid requests return proper error responses (400), not 500s — FastAPI/Pydantic validation
- [x] Swagger docs are accessible at `/docs`

---

### Epic 6.3 — Minimal Web UI

**What to build:**
- A web UI (Streamlit or Gradio): search box, submit button, results list
- Results show: case number, court, date, relevance score
- Each result has a link/button to view the full document content
- No login, no auth — just a local tool for now

**Acceptance criteria:**
- [ ] UI renders and is usable in a browser
- [ ] Results display all metadata fields (court, date, case number, score)
- [ ] Clicking a result opens/shows the full case document
- [ ] A non-developer (e.g., a lawyer) can use the UI without instructions — verified by having one person try it cold

---

### Tier 6 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Recall@10 | Improvement over Tier 5 | Eval harness |
| NDCG@10 | Improvement over Tier 5 | Eval harness |
| API latency (p95) | < 2 seconds | 50 eval queries through the API |
| Usability | A lawyer can search and view a case document | Manual observation |

---

## Tier 7 — Legal-Aware Chunking & Document Summaries

**Goal:** Replace fixed-size chunking with legal-structure-aware chunking. Add document-level summaries as a second retrieval index. These are independent improvements — either can land first.

---

> **Note:** Decision type and authority extraction has been moved to Tier 5, Epic 5.2.

---

### Epic 7.1 — Structure-Aware Legal Chunking

**What to build:**
- Replace fixed-size chunking with a Turkish legal-document-aware parser
- Identify Turkish case law sections using heading patterns, numbered paragraphs, or LLM classification:
  - **Taraflar / Başvurucu** (parties)
  - **İddia** (prosecution's claim / plaintiff's claim)
  - **Savunma** (defense)
  - **İlk Derece Kararı / BAM Kararı** (lower court decisions, if this is an appeal)
  - **Gerekçe** (reasoning — the most important section for retrieval)
  - **Hüküm** (disposition/holding)
  - **Karşı Oy / Muhalefet Şerhi** (dissenting opinions — important for conflict detection)
- Chunk at section boundaries; if a section exceeds 1024 tokens, split at paragraph boundaries within the section
- Each chunk carries a `section_type` label (iddia, savunma, gerekçe, hüküm, karşı_oy, etc.)
- **Special handling for Danıştay decisions:** These often have a distinct structure with "İstemin Özeti" (summary of claim) and "Danıştay Tetkik Hakimi Düşüncesi" (reporter judge's opinion)

**R&D task — Chunking strategy A/B test:**
- Index the same corpus with:
  1. Tier 3 fixed-size chunks (current baseline)
  2. Structure-aware chunks
- Run eval on both with the same retrieval pipeline
- Also measure: average chunk size (tokens), number of chunks per document, standard deviation of chunk sizes

**Acceptance criteria:**
- [ ] Structure-aware chunks do NOT break mid-sentence (verify on 50 random chunks by reading them)
- [ ] At least 80% of chunks have a valid `section_type` label (not "unknown")
- [ ] A/B comparison table shows Recall@10 and NDCG@10 for both strategies
- [ ] Structure-aware chunking achieves higher Recall@10, OR the delta is documented with analysis of why not

---

### Epic 7.2 — Document Summary Generation

**What to build:**
- For each case law document, generate an LLM summary (300–500 tokens) capturing: legal issue, court, holding, key facts, outcome
- Store summaries in the document metadata table

**R&D task — Summary prompt comparison:**
- Test three summary prompts on a 20-document sample:
  1. Generic: "Summarize this legal case in 300-500 words"
  2. Structured (English): "Extract and describe: (1) Legal issue, (2) Court and chamber, (3) Holding/disposition (onama/bozma/direnme/red/kabul), (4) Key reasoning, (5) Referenced statutes with article numbers, (6) Decision authority level (İBK, HGK, or daire kararı)"
  3. Structured (Turkish): Same structured prompt but written in Turkish — test whether prompt language affects summary quality on Turkish documents
- A lawyer blind-rates each summary on a 1–5 scale ("How well does this summary capture the key retrievable aspects of the case?")
- Use the higher-rated prompt for the full corpus

**Acceptance criteria:**
- [ ] Every document has a summary stored
- [ ] Lawyer average rating >= 3.5/5 on 20-document sample (for the winning prompt)
- [ ] Prompt comparison: average rating for all three variants, with which won
- [ ] Summary generation cost for the full corpus is logged (total tokens, total $)

---

### Epic 7.3 — Summary Index & Multi-Level Retrieval

**What to build:**
- Embed all summaries and store in a separate vector index (or a separate collection in the same vector DB)
- At query time, search BOTH the chunk index and the summary index
- Fuse results: a document can be retrieved through its chunks OR its summary
- Document-level aggregation now considers scores from both sources

**R&D task — Summary index contribution:**
- Compare:
  1. Chunk-only retrieval (Tier 4)
  2. Summary-only retrieval
  3. Chunk + Summary fused retrieval
- Measure Recall@10, NDCG@10 for each
- Count: how many documents in the top-10 were found ONLY through the summary index?

**Acceptance criteria:**
- [ ] Fused retrieval Recall@10 >= chunk-only Recall@10
- [ ] Comparison table with all three configs exists
- [ ] At least 5 specific queries are identified where the summary index retrieves a correct document that chunk-only missed
- [ ] If summary-only retrieval works surprisingly well, document this as a potential simplification option

---

### Tier 7 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Recall@10 | Improvement over Tier 6 | Eval harness |
| NDCG@10 | Improvement over Tier 6 | Eval harness |
| Chunking quality | No mid-sentence breaks in 50-chunk sample | Manual review |
| Summary quality | Lawyer rating >= 3.5/5 | Blind evaluation |

---

## Tier 8 — Domain Embeddings & Re-Ranking

**Goal:** Swap in legal-specific embeddings and add cross-encoder re-ranking. These are the two highest-ROI improvements based on literature (Anthropic's contextual retrieval study showed re-ranking alone cuts failures by ~20%).

---

### Epic 8.1 — Legal Embedding Model

**What to build:**
- Re-embed all chunks and summaries using a legal-domain embedding model
- Update the vector indices

**R&D task — Legal embedding comparison (head-to-head):**

> **Note:** BAAI/bge-m3 (1024 dimensions) is the current baseline, deployed via TEI on A100 (see Tier 3 implementation note). The comparison should include bge-m3 vs. voyage-law-2 vs. GTE-Qwen2.

- Compare exactly these models on the full evaluation set:
  1. `BAAI/bge-m3` (current baseline, 1024d, multilingual, running via TEI)
  2. `voyage-law-2` (Voyage AI, commercial, 1024d, 16K tokens) — trained primarily on English legal text, may underperform on Turkish
  3. `GTE-Qwen2-7B-instruct` (open-source, 8192 tokens, multilingual — test if long-context helps with Turkish legal text)
- If `voyage-law-2` underperforms on Turkish, add `multilingual-e5-large-instruct` (open-source, strong Turkish support) as a replacement candidate
- Measure: Recall@5, Recall@10, NDCG@10
- Also measure per-law-branch performance: break down by hukuk (civil), ceza (criminal), idari (administrative)
- **Turkish semantic test:** Create 10 test pairs of semantically equivalent Turkish legal phrases to verify the model captures Turkish legal semantics (e.g., "haksız fiil tazminatı" ↔ "hukuka aykırı eylemden doğan zarar", "iş sözleşmesinin feshi" ↔ "işten çıkarma"). These should be Turkish↔Turkish pairs, not cross-language.
- If one model dominates on one branch but loses on another, document this

**Acceptance criteria:**
- [ ] Comparison table with all tested models and all metrics exists
- [ ] Per-area-of-law breakdown shows whether the domain model helps more for some areas
- [ ] The chosen model's Recall@10 vs. Tier 4 baseline delta is documented
- [ ] If `voyage-law-2` is chosen: cost per 1000 queries and cost to re-embed the full corpus are documented

---

### Epic 8.2 — Cross-Encoder Re-Ranking

**What to build:**
- Add a cross-encoder re-ranking stage after hybrid retrieval + document aggregation
- Pipeline: Hybrid retrieval (top 100 chunks) → RRF → Document aggregation (top 30 docs) → Cross-encoder scores each (query, document_summary) pair → Re-rank → Return top 10

**R&D task — Re-ranker model comparison:**
- Test three re-rankers on the eval set:
  1. `bge-reranker-v2-gemma` (open-source, large)
  2. `Cohere Rerank v3.5` (commercial API)
  3. `jina-reranker-v2-base-multilingual` (open-source, explicitly supports Turkish)
- Measure: Recall@10, NDCG@10, and per-query latency added by re-ranking (p50, p95)
- Document the quality vs. latency tradeoff: scatter plot of NDCG@10 vs. p95 latency

**Acceptance criteria:**
- [ ] Re-ranking improves NDCG@10 over Tier 6 (ordering gets better, even if recall stays the same)
- [ ] Comparison table with metrics and latency for all 3 models exists
- [ ] End-to-end API latency (p95) is documented — if > 5 seconds, document which re-ranker stays under 5s
- [ ] 10 specific query examples where re-ranking changed the document ordering, with before/after shown

---

### Epic 8.3 — Contextual Embeddings (Anthropic-Style)

**What to build:**
- Before embedding each chunk, prepend a 50–100 token LLM-generated context explaining the chunk's role in the larger document
- Example context: "This chunk is from Yargıtay 9. HD, Case 2020/12345 E., a bozma (reversal) decision about wrongful termination. It describes the court's reasoning on burden of proof."
- The context generation prompt language (Turkish vs. English) should be tested as part of the R&D task — the embedding model may handle one better than the other.
- Re-embed all chunks with contextual prefixes
- Use prompt caching to reduce LLM cost

**R&D task — Contextual vs. standard embeddings:**
- Compare:
  1. Best non-contextual embeddings (from 8.1)
  2. Same model with contextual prefixes
- Measure: Recall@10, NDCG@10
- Also measure: context generation cost ($ per 1000 documents), storage size increase

**Acceptance criteria:**
- [ ] All chunks have contextual prefixes generated and stored
- [ ] Comparison table: contextual vs. non-contextual on Recall@10, NDCG@10
- [ ] Cost analysis documented: total cost of context generation for full corpus
- [ ] At least 10 queries where contextual embeddings improve results, with explanation of what the context added

---

### Tier 8 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Recall@10 | Improvement over Tier 7, OR no improvement with documented analysis of why and what was tried | Eval harness |
| NDCG@10 | Improvement over Tier 7, OR no improvement with documented analysis | Eval harness |
| API latency (p95) | < 5 seconds (re-ranking adds latency) | Load test |
| R&D artifacts | All comparison tables from 8.1–8.3 exist | File review |

---

## Tier 9 — Graph-Powered Ranking & Authority Scoring

**Goal:** Deepen the citation graph built in Tier 4 and use it to improve retrieval ranking. Combines graph data quality (LLM extraction, court hierarchy validation, statistics) with graph-powered ranking (PageRank boost, court-level boost, three-way fusion, treatment-weighted PageRank). Every ranking improvement must beat Tier 8 numbers.

> **Note:** Basic citation extraction (regex, 6 patterns), Neo4j setup, court hierarchy nodes (74 Court nodes, APPEALS_TO edges), and PageRank were built in Tier 4. The epics below extend and validate that foundation.

---

### Epic 9.1 — Citation Extraction Quality (LLM vs. Regex R&D)

**What to build:**
- A pipeline that extracts case-to-case citations from each jurisprudence document
- For each citation: `source_doc_id`, `target_case_reference` (the cited case number)
- Resolve `target_case_reference` to a `target_doc_id` in our corpus (fuzzy matching on case number)
- Store as edges: `citations(source_doc_id, target_doc_id, citation_text_snippet)`
- Handle unresolved citations (cited case not in our corpus) — log them separately

**R&D task — Citation extraction methods:**
- Compare two approaches on a 100-document sample:
  1. **Regex-based:** Use the Turkish citation regex patterns defined in `docs/turkish-law-reference.md` Section 11 (covers Yargıtay, Danıştay, BAM, statute references, and İBK formats)
  2. **LLM-based:** Prompt an LLM to extract all case references and statute references from each document
- Ground truth: 20 manually-annotated documents with all citations marked
- Measure: Precision (extracted citations that are real / total extracted) and Recall (known citations found / total known)
- **Also extract:** Cross-references between Yargıtay daireleri (inter-chamber citations are key for conflict detection)

**Acceptance criteria:**
- [ ] Citation extraction runs on the full corpus without errors
- [ ] Precision >= 0.85 and Recall >= 0.70 on the 20-document annotated sample
- [ ] Comparison table: regex vs. LLM extraction with precision, recall, F1, and cost
- [ ] Corpus-wide stats: total citation edges, average citations per document, % of citations resolved to a doc in our corpus, % unresolved
- [ ] Unresolved citations are logged to a file for future corpus expansion

---

### Epic 9.2 — Court Hierarchy Validation & Extended Graph

> **Note:** The court hierarchy is already implemented in `app/graph/neo4j_sync.py` (74 Court nodes, APPEALS_TO edges). Remaining work is lawyer validation and subject-matter daire mappings.

**What to build:**
- Define the Turkish court hierarchy as a static graph (manually — reference `docs/turkish-law-reference.md` Section 1):
  - **Four judicial pillars:** Adli Yargı, İdari Yargı, Anayasa Mahkemesi, Uyuşmazlık Mahkemesi
  - **Four court levels within each pillar:**
    - Level 1: İlk Derece Mahkemeleri (first instance courts)
    - Level 2: BAM/BİM (regional appellate courts)
    - Level 3: Yargıtay Daireleri / Danıştay Daireleri (supreme court chambers)
    - Level 4: Yargıtay HGK/CGK / Danıştay İDDGK/VDDGK (general assemblies)
  - **AYM:** Anayasa Mahkemesi — separate pillar, not in adli/idari hierarchy
  - Reference `docs/turkish-law-reference.md` Sections 1-3 for the complete list of courts, all numbered daireleri, and their subject-matter jurisdictions. Do NOT hardcode these in the graph definition — read them from the reference file, as daire assignments change annually.
  - **İBK is NOT a court level** — it's a decision type (`decision_authority: ibk`) issued by level-4 courts (HGK/CGK/İDDGK/VDDGK). Model it as a property on the decision, not as a node in the court hierarchy.
  - `HIGHER_THAN` directed edges within each pillar
  - `APPEALS_TO` edges: İlk Derece → BAM → Yargıtay Daire → HGK/CGK (for direnme); İdare/Vergi Mah. → BİM → Danıştay Daire → İDDGK/VDDGK
  - Each court node has: `level` (1–4), `pillar` (adli/idari/anayasa), `branch` (hukuk/ceza for adli, vergi/idari for idari yargı)
- Link each case document to its specific daire/mahkeme: `DECIDED_BY(case, court)` edges
- **Model daire subject-matter jurisdiction:** Each Yargıtay HD/CD has specific case types (e.g., 4. HD = haksız fiil/tazminat, 9. HD = iş hukuku). Store as properties on the court node. Reference `docs/turkish-law-reference.md` Sections 2-3.
- Store in Neo4j (or NetworkX for prototyping, with a migration path to Neo4j)

**Acceptance criteria:**
- [ ] Court hierarchy graph has all Turkish courts including all active Yargıtay and Danıştay daireleri per `docs/turkish-law-reference.md` (verified by a lawyer)
- [ ] Every document in the corpus is linked to exactly one court node (zero unlinked docs, or unlinked docs are logged)
- [ ] Court distribution query: `RETURN court.name, count(cases)` produces sensible numbers — Yargıtay daireleri should have the bulk of cases
- [ ] Daire subject-matter mappings are stored and verified (e.g., 9. HD cases are all labor law)
- [ ] Visual rendering of the court hierarchy exported as an image
- [ ] The graph correctly models the bozma->direnme->Genel Kurul flow (Daire -> İlk Derece/BAM -> same Daire -> HGK/CGK if direnme)

---

### Epic 9.3 — Full Citation Network Statistics & Quality Report

> **Note:** PageRank, in-degree, and out-degree are already computed in `app/graph/metrics.py`. Remaining work is quality reporting.

**What to build:**
- Import all citation edges into the graph alongside the court hierarchy
- Compute and store:
  - **PageRank** on the citation network
  - **In-degree** (number of cases citing this case)
  - **Out-degree** (number of cases this case cites)
- Generate graph quality report: connected components, average path length, top-20 cases by PageRank

**Acceptance criteria:**
- [ ] Full graph is loaded with both citation edges and court hierarchy
- [ ] PageRank, in-degree, out-degree stored as properties on every case node
- [ ] Graph quality report exists documenting: node count, edge count, number of connected components, average in-degree, top-20 cases by PageRank
- [ ] Top 10 cases by PageRank are reviewed by a lawyer and confirmed as genuinely important/landmark cases
- [ ] Graph is queryable: a Cypher query like "find all cases that cite Case X" returns correct results (verify on 5 known cases)

---

### Epic 9.4 — PageRank-Boosted Re-Ranking

**What to build:**
- After the cross-encoder re-ranking stage, apply a PageRank signal:
  `final_score = alpha * reranker_score + (1 - alpha) * normalized_pagerank`
- **İBK special treatment:** İçtihadı Birleştirme Kararları are binding on ALL courts. They are issued by level-4 courts but carry higher authority than regular level-4 decisions. If an İBK exists on the queried topic, it should appear in the top results. Consider a dedicated boost for `decision_authority: ibk` documents.

**R&D task — PageRank alpha calibration:**
- Test alpha values: **1.0 (no PageRank), 0.95, 0.90, 0.85, 0.80**
- Measure NDCG@10 and Recall@10 for each
- Also define and measure **Authority-weighted NDCG@10**: multiply each ground-truth relevance grade by a weight derived from `court_level` (1–4) and `decision_authority` (İBK gets the highest weight, e.g., 5; HGK/CGK kararı=4; daire kararı at its court_level)
- **İBK recall check:** For queries where a relevant İBK exists, verify it appears in top-3 with every alpha value

**Acceptance criteria:**
- [ ] Alpha comparison table with NDCG@10, Authority-weighted NDCG@10, and Recall@10 for all 5 alpha values
- [ ] The chosen alpha does NOT decrease Recall@10 by more than 2% compared to alpha=1.0 (no PageRank)
- [ ] Authority-weighted NDCG@10 improves (higher-authority cases rise in ranking)

---

### Epic 9.5 — Court-Level Boost

**What to build:**
- Apply a court-level boost:
  `boosted_score = score x (1 + beta x court_level / max_court_level)`
- Applied after cross-encoder re-ranking, can be combined with PageRank boost

**R&D task — Court boost calibration:**
- Test beta values: **0 (no boost), 0.1, 0.2, 0.5, 1.0**
- Measure NDCG@10, Authority-weighted NDCG@10, and Recall@10
- For the 10 eval queries that have relevant results from both high and low courts: check that high-court cases rank higher with boosting

**Acceptance criteria:**
- [ ] Beta comparison table with all metrics for all 5 values
- [ ] Chosen beta does NOT decrease Recall@10 by more than 2%
- [ ] On the 10 mixed-court queries: verify high-court cases rank higher than low-court cases with boosting enabled
- [ ] Combined effect of PageRank + court boost (best alpha x best beta) is measured and compared against Tier 8 baseline

---

### Epic 9.6 — Full Three-Way Fusion (BM25 + Dense + Graph)

> **Note:** Graph expansion already exists in Tier 4 (`app/retrieval/graph_retrieval.py`). What remains is adding BM25 as the third retrieval path (depends on Tier 6).

**What to build:**
- Add a third retrieval path: **1-hop citation expansion**
- Given a query, find top-5 documents via dense retrieval, then expand to their citation neighbors (cases they cite + cases that cite them)
- Add neighbors to the candidate pool before re-ranking
- Three-way RRF: BM25 + Dense + Graph-neighbors

**R&D task — Graph retrieval contribution:**
- Compare:
  1. Tier 8 pipeline (BM25 + Dense + Re-ranking)
  2. + Graph traversal (this epic)
- Measure Recall@10, NDCG@10, Authority-weighted NDCG@10
- Count per query: how many documents in the final top-10 were found ONLY through graph traversal?

**Acceptance criteria:**
- [ ] Graph traversal adds at least 5 unique candidate documents per query on average (that weren't found by BM25 or dense)
- [ ] Recall@10 improves over Tier 8, OR the delta is documented with analysis
- [ ] Number of "graph-only finds" that are actually relevant (per eval set) is documented
- [ ] End-to-end latency (p95) is under 8 seconds — if not, document the bottleneck

---

### Epic 9.7 — Metadata Filtering in API

**What to build:**
- API now accepts filters: `{"query": "...", "filters": {"court_level_min": 2, "date_after": "2015-01-01", "jurisdiction": "..."}}`
- Pre-retrieval filtering (filter before scoring)
- Results include `court_level` (integer), `date`, `jurisdiction`, `pagerank_score` in the response

**Acceptance criteria:**
- [ ] Filtering by `court_level_min: 3` returns ONLY Yargıtay/Danıştay daire-level and above (verify on 10 queries)
- [ ] Filtering by `date_after` returns ONLY cases after that date (verify on 10 queries)
- [ ] Filtering by `law_branch: "ceza"` returns only criminal cases (verify on 10 queries)
- [ ] Filtering by `daire: "Yargıtay 9. HD"` returns only labor law cases from that chamber
- [ ] Combined filters work (court + date + branch together)
- [ ] Empty filter results return `[]`, not an error
- **Future consideration (Tier 11):** Temporal code regime filtering (e.g., old 818 sayılı BK vs. new 6098 sayılı TBK) — see `docs/turkish-law-reference.md` Section 6 for transition dates. This requires query understanding to infer which code regime the lawyer cares about, so defer to Tier 11.

---

### Epic 9.8 — Treatment-Weighted PageRank

**What to build:**
- Use the `treatment` property from Tier 5 (Epic 5.3) to weight citation edges in PageRank computation
- Edge weights by treatment type:
  - `AFFIRMS` edges count 1.5x
  - `REVERSES` edges count 0x (blocked — reversed authority should not flow)
  - `FOLLOWS` edges count 1.5x
  - `DISTINGUISHES` edges count 0.5x
  - `NEUTRAL` edges count 1x
- Modifies `app/graph/metrics.py` to accept optional edge weights in PageRank computation
- New field: `treatment_weighted_pagerank` on documents (separate from existing `pagerank_score` to allow comparison)

**Acceptance criteria:**
- [ ] Treatment-weighted PageRank computed for all documents with citation edges
- [ ] Comparison table: standard PageRank vs. treatment-weighted PageRank top-20 — document which cases moved up/down and why
- [ ] Documents whose primary citations are REVERSES edges should have lower treatment-weighted PageRank
- [ ] Documents with many AFFIRMS/FOLLOWS inbound edges should have higher treatment-weighted PageRank

---

### Tier 9 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Citation extraction quality | Precision >= 0.85, Recall >= 0.70 on 20-doc annotated sample | Manual annotation |
| Court hierarchy | Complete, verified by a lawyer, all active daireler present | Lawyer review |
| Graph DB | Loaded, queryable, stats report generated | Neo4j browser |
| PageRank | Top-10 by PageRank validated as landmark cases by a lawyer | Lawyer review |
| Recall@10 | Improvement over Tier 8 (or within 2% with significant NDCG gain) | Eval harness |
| NDCG@10 | Improvement over Tier 8 | Eval harness |
| Authority-weighted NDCG@10 | Baselined and tracked (new metric) | Eval harness |
| Graph-only finds | >= 5 unique candidates/query from graph | Logged per query |
| API latency (p95) | < 8 seconds | Load test |

---

## Tier 10 — Conflict Detection & Contradiction Surfacing

**Goal:** The distinguishing feature — detect and surface contradictory precedents. This is what makes the system more than a search engine. Now depends on Tier 5 data (dispositions, treatments) rather than extracting them internally. All conflict/treatment output is structured for consumption by a downstream LLM.

---

### Epic 10.1 — Holding Extraction

**What to build:**
- For each case document, extract the **core holding** (the legal rule/principle established):
  - LLM prompt: "What is the binding legal holding of this case? State it as a single declarative sentence. If this is an appeal decision (onama/bozma/direnme), state the appellate court's conclusion."
  - Store as a new field: `holding_text` on each document
  - Also extract `decision_outcome`: onama (affirmed), bozma (reversed), kısmen bozma (partially reversed), red (dismissed), kabul (accepted), direnme (defiance of reversal)
- This becomes the basis for contradiction comparison
- **Turkish nuance:** In bozma decisions, the holding is the Yargıtay daire's reversal rationale, not the original court's decision. In direnme, the İlk Derece/BAM is reasserting its original holding against the Yargıtay daire.

**R&D task — Holding extraction quality:**
- Extract holdings for 30 documents
- A lawyer rates each on a 1–5 scale: "Does this accurately capture the binding holding?"
- If average < 3.5: iterate on the prompt (test 2–3 variants)
- **Also test:** Turkish-language prompt vs. English-language prompt — LLMs may reason better in English even on Turkish documents. Let the data decide.

**Acceptance criteria:**
- [ ] Every document has a `holding_text` field
- [ ] Lawyer average rating >= 3.5/5 on 30-document sample
- [ ] If multiple prompts were tested: comparison table with average ratings for each
- [ ] Holdings are concise (1–3 sentences, not paragraph-length summaries)

---

### Epic 10.2 — Pairwise Conflict Detection

**What to build:**
- Given a set of retrieved documents (top 10), detect contradictions:
  1. Embed all holdings; compute pairwise cosine similarity to find "topically related" pairs (similarity > threshold)
  2. For each related pair, LLM prompt: "Case A holds: [holding]. Case B holds: [holding]. Do these cases reach opposing conclusions on the same legal issue? Respond: AGREES / DISAGREES / UNRELATED, with a one-sentence explanation."
  3. If DISAGREES: create a conflict annotation
- **Prerequisite:** `decision_type` must be populated on documents (Tier 5, Epic 5.1 for disposition and Epic 5.2 for decision type).
- **Turkish-specific conflict types to detect:**
  - **Inter-daire conflicts:** Two different Yargıtay daireleri reaching opposite conclusions on the same legal issue (e.g., 4. HD vs 11. HD on tazminat computation). These are the most common and most important — they are what triggers İBK proceedings.
  - **Bozma-direnme chains:** A Yargıtay daire reverses (bozma), the lower court defies (direnme) — this is an active conflict that goes to HGK/CGK for resolution. Requires `decision_type` to identify bozma and direnme cases and the citation graph to trace the chain.
  - **Temporal conflicts:** Same daire changing its position over time (eski içtihat vs. yeni içtihat). The more recent decision prevails.
  - **İBK resolution:** If an İBK exists on the conflicted topic, flag it as "resolved by İBK" and link to the İBK decision.
- Add to API: `"conflicts": [{"doc_ids": ["X", "Y"], "conflict_type": "inter_daire|bozma_direnme|temporal", "explanation": "...", "court_levels": [3, 3], "resolved_by_ibk": null|"İBK_doc_id"}]`

**R&D task — Conflict detection accuracy:**
- From the eval set, take the 10 queries tagged with known contradictory precedent pairs
- Run conflict detection on top-10 results for each
- Measure:
  - **Conflict Precision:** Of flagged conflicts, how many are real? (lawyer-verified)
  - **Conflict Recall:** Of known conflicts, how many were found?
- Also test: cosine similarity thresholds of 0.6, 0.7, 0.8 for the "topically related" filter — which gives the best precision/recall tradeoff?

**Acceptance criteria:**
- [ ] Conflict detection runs on all eval queries without errors
- [ ] Conflict Precision >= 0.70 (70%+ of flagged conflicts are real)
- [ ] Conflict Recall >= 0.50 (at least half of known conflicts detected)
- [ ] Similarity threshold comparison table exists
- [ ] API includes `conflicts` in response
- [ ] UI displays conflicts — e.g., a "Conflicting decisions found" banner with grouped case pairs and the explanation
- [ ] At least 3 bozma-direnme chains are identified in the corpus and verified by a lawyer, showing the full path: İlk Derece karar → Daire bozma → İlk Derece direnme → HGK/CGK resolution (if exists)

---

### Epic 10.3 — Conflict-Aware Result Presentation

**What to build:**
- When conflicts are detected, restructure the UI results:
  - Group conflicting cases together visually
  - Show which court is higher (and therefore which holding is authoritative in which jurisdiction)
  - Show the date of each conflicting case (more recent may indicate evolving law)
  - Add a "Conflict Summary" section at the top of results when conflicts exist
- No re-ranking changes — just presentation

**Acceptance criteria:**
- [ ] When a conflict exists in results, the UI shows a visual grouping (not just a flat list)
- [ ] Court level is displayed next to each conflicting case (so the lawyer can see hierarchy)
- [ ] A lawyer can understand, within 5 seconds of seeing the conflict UI, which case is from the higher court
- [ ] When no conflicts exist, the UI looks the same as before (no empty "no conflicts" noise)

---

### Epic 10.4 — Treatment Classification Refinement (LLM)

> **Note:** Extends Tier 5 keyword heuristics (Epic 5.3). Only runs on edges where the keyword heuristic returned NEUTRAL.

**What to build:**
- For each citation edge in the graph where treatment is NEUTRAL (from Tier 5 keyword heuristics), classify the treatment into 5 categories using LLM:
  - `FOLLOWS` — citing case agrees with and applies the cited case
  - `DISTINGUISHES` — citing case limits the cited case's applicability
  - `OVERRULES` — citing case explicitly overturns the cited case
  - `CRITICIZES` — citing case disagrees but doesn't formally overrule
  - `NEUTRAL` — simple reference without judgment
- **Turkish context for the classifier:** The LLM prompt must understand that in Turkish law:
  - **Onama** (affirmance) maps to `FOLLOWS` — the higher court upholds the lower court's decision
  - **Bozma** (reversal) maps to `OVERRULES` — the higher court reverses the lower court's decision
  - **Direnme** and **uyma** are NOT citation treatments — they are the lower court's *response* to a bozma. Direnme (defiance) means the lower court maintained its original position; uyma (compliance) means it accepted the reversal. These are stored as `decision_type` on the responding case, not as edge treatments.
  - When one Yargıtay daire cites another daire's conflicting decision, this is typically `CRITICIZES` or `DISTINGUISHES`, not `OVERRULES` — daireleri cannot overrule each other.
- Use an LLM: provide the citation context paragraph + both case summaries
- Store treatment as an edge property in the graph

**R&D task — Treatment classification accuracy:**
- Manually annotate treatments for 100 citation edges (ground truth, with lawyer)
- Run the LLM classifier on all 100
- Measure per-class precision, recall, F1-score
- Test two context levels:
  1. Citation paragraph only
  2. Citation paragraph + both case summaries
- Compare accuracy to determine if summaries help

**Acceptance criteria:**
- [ ] Classification runs on all citation edges where Tier 5 keyword heuristic returned NEUTRAL
- [ ] Macro-average F1 >= 0.65 on the 100-edge test set
- [ ] Per-class metrics (precision, recall, F1) exist for all 5 treatment types
- [ ] `OVERRULES` class Recall >= 0.80 (most critical — missing an overruled/bozma'd case is dangerous for lawyers)
- [ ] Context ablation comparison documented (paragraph-only vs. paragraph + summaries)
- [ ] Classification cost for the full corpus is logged

---

### Epic 10.5 — Good Law / Bad Law Status

**What to build:**
- Derive case status from citation treatments (combining Tier 5 keyword heuristics and Epic 10.4 LLM refinements):
  - `overruled` — a higher court issued bozma (reversal) against this case, AND the lower court complied (uyma). The holding is dead.
  - `disputed` — a bozma was issued but the lower court issued direnme (defiance). The legal question is unresolved, pending HGK/CGK.
  - `superseded_by_ibk` — an İBK decision has settled the legal question differently. The case's holding is no longer authoritative.
  - `conflicting_precedent` — another court at the same level (e.g., a different Yargıtay daire) has reached the opposite conclusion. Neither is overruled — they coexist until HGK/CGK or İBK resolves the conflict.
  - `criticized` — `CRITICIZES` edges exist but no `OVERRULES`
  - `good_law` — no negative treatments, or predominantly `FOLLOWS`
  - `unknown` — insufficient citation data
- **Turkish-specific rule:** A Yargıtay daire kararı does NOT overrule another daire's decision. Only HGK/CGK or İBK can resolve inter-daire conflicts. If two daireleri disagree, both are technically "good law" within their own jurisdiction until resolved — flag these as `conflicting_precedent`.
- Store status on each case node
- Add to API response: `"status": "good_law"` or `"status": "overruled", "overruled_by": "..."`. The UI layer handles Turkish-language display (e.g., showing "BOZULMUŞ" for `overruled`, "DİRENME AŞAMASINDA" for `disputed`).

**Acceptance criteria:**
- [ ] Every document has a `status` field
- [ ] Spot-check 20 cases flagged as `overruled` — verify with a lawyer that bozma was issued and the lower court complied (uyma)
- [ ] Spot-check 20 cases marked `good_law` — verify they haven't been reversed
- [ ] Spot-check 10 cases flagged as `disputed` — verify the direnme is real and HGK/CGK hasn't resolved it yet
- [ ] Status distribution is logged (how many good_law, overruled, disputed, superseded_by_ibk, conflicting_precedent, criticized, unknown)
- [ ] If `unknown` > 50% of corpus: document what citation data is missing and what would fix it

---

### Epic 10.6 — Treatment-Aware Ranking

**What to build:**
- Modify the ranking pipeline:
  - `overruled` cases get a **penalty** (multiplicative: score x 0.3) — still visible but ranked lower
  - `disputed` cases get a **moderate penalty** (score x 0.6) + a visual flag indicating the legal question is pending HGK/CGK resolution
  - Cases with many `FOLLOWS` treatments get a **boost** (treatment-weighted PageRank: FOLLOWS edges count as 1.5x, CRITICIZES as 0.5x, OVERRULES as 0x)
  - İBK decisions (`decision_authority: ibk`) get NO penalty ever — they are always authoritative
  - Overruled cases show a visual warning in the UI (e.g., red "OVERRULED" / "BOZULMUŞ" badge, with the overruling case linked)

**R&D task — Treatment-aware ranking impact:**
- Compare:
  1. Tier 9 ranking (PageRank + court boost, no treatment awareness)
  2. Treatment-aware ranking (this epic)
- Measure NDCG@10, Authority-weighted NDCG@10
- Count: how many overruled cases appear in top-5 before vs. after?

**Acceptance criteria:**
- [ ] Comparison table: treatment-aware vs. unaware on NDCG@10, AW-NDCG@10
- [ ] Overruled cases in top-5 decreases after treatment-aware ranking (count before vs. after)
- [ ] Overruled cases are NOT removed entirely — they still appear if highly relevant, just ranked lower and flagged
- [ ] UI shows visual badge for overruled cases with a link to the overruling case (UI layer handles Turkish display: "BOZULMUŞ", "DİRENME", etc.)
- [ ] Disputed (direnme) cases show a distinct badge indicating the legal question is pending resolution

---

### Tier 10 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Conflict Precision | >= 0.70 | Lawyer evaluation on 10 conflict queries |
| Conflict Recall | >= 0.50 | Against known conflicts in eval set |
| Holding quality | Lawyer rating >= 3.5/5 | 30-document sample |
| UI conflict display | Lawyer can identify higher court in < 5 seconds | Observation |
| Treatment F1 (macro-avg) | >= 0.65 | 100-edge annotated test set |
| OVERRULES Recall | >= 0.80 | Per-class metrics |
| Status accuracy | Spot-check: 80%+ correct on 40 sampled cases | Lawyer review |
| NDCG@10 | Improvement or no regression vs. Tier 9 | Eval harness |
| Overruled-in-top-5 | Decreases vs. Tier 9 | Count comparison |

**If precision/recall targets are not met:** Document which conflict types work (inter-daire, temporal, bozma-direnme) and which don't, and whether the approach is viable with more annotated data or a different similarity threshold.

**If Treatment F1 < 0.65:** Document per-class analysis — identify which treatment types are reliably classified and which aren't. Consider merging underperforming classes (e.g., CRITICIZES + DISTINGUISHES → NEGATIVE_NON_OVERRULE) or dropping them to NEUTRAL.

---

## Tier 11 — Query Intelligence

**Goal:** Make the system smarter about understanding what the lawyer is asking for. LLM-powered query analysis, reformulation, and entity extraction.

---

### Epic 11.1 — Query Classification & Entity Extraction

**What to build:**
- An LLM step before retrieval that produces structured JSON. Must handle Turkish legal entity patterns:
  ```json
  {
    "query_type": "precedent_search",
    "entities": {"statutes": ["TCK m.302"], "courts": ["Yargıtay"], "concepts": ["meşru müdafaa"]},
    "reformulated_query": "...",
    "date_range_hint": {"after": "2010-01-01"}
  }
  ```
- **Turkish entity extraction patterns** (regex + LLM hybrid):
  - Statute references: `TCK m.157`, `TBK m.49`, `6098 sayılı Kanun m.112`, `İYUK m.7`
  - Court references: `Yargıtay 4. HD`, `Danıştay 7. D.`, `İstanbul BAM 12. HD`
  - Case numbers: `2021/1234 E.`, `2022/5678 K.`
  - Legal concepts: `haksız fiil`, `iş kazası`, `kira tespit`, `kamulaştırma`
- Log all classifications for analysis

**R&D task — Query reformulation impact:**
- Compare on the full eval set:
  1. Original queries (as-is)
  2. LLM-reformulated queries
- Measure Recall@10, NDCG@10
- Analyze the 10 largest improvements and 10 largest regressions — what patterns emerge?

**Acceptance criteria:**
- [ ] Classification produces valid JSON for all 50 eval queries (no parse failures)
- [ ] Entity extraction finds at least one entity in >= 80% of queries
- [ ] Reformulation comparison table: Recall@10 original vs. reformulated
- [ ] Regression analysis: root causes documented for queries where reformulation hurt

---

### Epic 11.2 — Entity-Targeted Retrieval

**What to build:**
- When the query classifier extracts statute references or case numbers, add a **targeted lookup** path:
  - If a statute is mentioned → find all cases in the graph that have an `INTERPRETS` edge to that statute
  - If a specific case number is mentioned → find that case and its citation neighbors
- Add these targeted results to the candidate pool alongside BM25 + Dense + Graph results

**R&D task — Entity-targeted contribution:**
- For the subset of eval queries that mention statutes or case numbers:
  - Count how many correct documents are found ONLY through entity-targeted lookup
  - Measure Recall@10 with and without entity-targeted retrieval on this subset

**Acceptance criteria:**
- [ ] When a statute reference is in the query, the system finds cases interpreting that statute from the graph
- [ ] When a case number is in the query, the system finds that case directly (if in corpus) plus its neighbors
- [ ] Recall@10 on statute/case-number queries improves with entity-targeted retrieval
- [ ] For queries WITHOUT entities, the pipeline is unchanged (no regression)

---

### Epic 11.3 — HyDE (Hypothetical Document Embeddings)

**What to build:**
- Before embedding the query, use an LLM to generate a **hypothetical ideal case summary** that would answer the query
- Embed this hypothetical summary instead of (or alongside) the raw query
- This bridges the gap between query language and document language

**R&D task — HyDE impact:**
- Compare on the full eval set:
  1. Raw query embedding
  2. HyDE-generated hypothetical summary embedding
  3. Both (average of raw + HyDE embeddings)
- Measure Recall@10, NDCG@10 for each
- Analyze: which types of queries benefit most from HyDE? (broad vs. specific)

**Acceptance criteria:**
- [ ] HyDE comparison table with Recall@10 and NDCG@10 for all 3 configs
- [ ] Analysis of which query types benefit (broad topic? specific fact pattern?)
- [ ] HyDE generation latency per query is documented (p50, p95)
- [ ] If HyDE hurts performance on some query types, document which and why

---

### Tier 11 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Recall@10 | Improvement over Tier 10 (or within 1% with clear NDCG gain) | Eval harness |
| NDCG@10 | Improvement over Tier 10 | Eval harness |
| Entity extraction | Entities found in >= 80% of queries | Manual check on 20 queries |
| Latency (p95) | < 10 seconds (query understanding adds overhead) | Load test |

---

## Tier 12 — Agentic Retrieval

**Goal:** Replace the fixed pipeline with an LLM agent that dynamically chooses retrieval strategies, evaluates results, and retries when needed. This is the capstone — the system that thinks about HOW to search, not just searches.

---

### Epic 12.1 — Strategy Router

**What to build:**
- An LLM agent that receives the query classification (from 11.1) and routes to a retrieval strategy:
  - **Fast factual lookup** → Dense + BM25 only (skip graph, skip re-ranking for speed)
  - **Precedent search** → Dense + BM25 + Graph traversal + entity-targeted + full re-ranking
  - **Broad topic exploration** → Summary index + Graph community + HyDE
  - **Specific case/statute lookup** → Entity-targeted first, expand from there
- Log every routing decision

**R&D task — Routing accuracy:**
- Manually label all 50 eval queries with the "ideal strategy" (based on which strategy produces the best results per query, measured empirically)
- Compare: agent routing decisions vs. ideal strategy
- Measure routing accuracy (% of queries where agent chose the ideal or near-ideal strategy)

**Acceptance criteria:**
- [ ] Agent routing logs show it uses different strategies for different queries (not always the same)
- [ ] Routing accuracy >= 60% (agent chooses the empirically best strategy at least 60% of the time)
- [ ] For misrouted queries: analyze what the agent got wrong and whether a better prompt/few-shot examples would help

---

### Epic 12.2 — Self-Corrective Retrieval (CRAG-Style)

**What to build:**
- After initial retrieval, the agent evaluates the results:
  - Checks if top results have relevance scores above a confidence threshold
  - If not: reformulates the query (different angle, broader/narrower scope) and retries (max 2 retries)
  - If a retry produces better results (higher confidence), use those instead
- Log: which queries triggered retries, what the reformulation was, whether the retry improved results

**R&D task — Self-correction value:**
- Compare:
  1. Fixed pipeline (no retries)
  2. Agent with self-correction (retries on low-confidence results)
- Measure: Recall@10, NDCG@10, average retries per query, average latency
- For queries that triggered retries: measure before/after Recall@10

**Acceptance criteria:**
- [ ] Agent triggers retries on at least some queries (not 0%, not 100% — selective)
- [ ] Comparison table: fixed vs. self-corrective on Recall@10, NDCG@10, latency
- [ ] Retry analysis: for queries where retry happened, show improvement rate (what % of retries actually helped)
- [ ] Agent latency is not more than 2× the fixed pipeline latency on average

---

### Epic 12.3 — End-to-End Agent Pipeline

**What to build:**
- Integrate all components into a single agentic pipeline:
  1. Query classification + entity extraction (Tier 11)
  2. Strategy routing (12.1)
  3. Multi-path retrieval (BM25 + Dense + Graph + Entity-targeted + Summary, as selected by router)
  4. Document aggregation + re-ranking (Tier 8 + Tier 9 signals)
  5. Self-correction if needed (12.2)
  6. Conflict detection (Tier 10)
  7. Treatment-aware status (Tier 10)
  8. Final ranked results with conflict annotations and case status
- This is the complete system

**R&D task — Full agent vs. full fixed pipeline:**
- Compare the agentic pipeline against the best fixed pipeline (all strategies always run):
  - Recall@10, NDCG@10, Authority-weighted NDCG@10, Conflict Recall
  - Average latency, p95 latency
  - Per-query analysis: which queries does the agent win on? Which does it lose?

**Acceptance criteria:**
- [ ] Agent processes all 50 eval queries without crashes
- [ ] Recall@10 >= best fixed pipeline (or within 1% with clear NDCG/latency advantage)
- [ ] Full comparison table with all metrics exists
- [ ] Per-query win/loss analysis documented
- [ ] The system returns: ranked documents + full file paths + court metadata + conflict annotations + case status (good_law/overruled)

---

### Tier 12 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Recall@10 | Best across all tiers | Eval harness |
| NDCG@10 | Best across all tiers | Eval harness |
| Authority-weighted NDCG@10 | Best across all tiers | Eval harness |
| Conflict Precision | >= 0.70 | Lawyer evaluation |
| Conflict Recall | >= 0.60 | Against known conflicts |
| Case status accuracy | >= 80% on spot-check | Lawyer review |
| Routing accuracy | >= 60% | Against empirically optimal strategy |
| API latency (p95) | < 12 seconds | Load test |

---

## Tier 13 — Feedback Loops & Fine-Tuning

**Goal:** Push retrieval quality further and build the feedback infrastructure for continuous improvement.

> **Retired:** Epic 13.1 (HippoRAG-style PPR) has been implemented in Tier 4, Epic 4.3. See `app/retrieval/graph_retrieval.py`.

---

### Epic 13.1 — Embedding Fine-Tuning on Feedback Data

**Prerequisite:** Epic 13.2 (Feedback Collection) must be deployed first. This epic runs only after 500+ feedback signals are collected.

**What to build:**
- Collect user feedback data (from UI: "Relevant" / "Not Relevant" clicks)
- Once 500+ feedback signals exist, construct training pairs:
  - Positive: (query, relevant_doc_summary) pairs from "Relevant" clicks
  - Negative: (query, irrelevant_doc_summary) pairs from "Not Relevant" clicks
- Fine-tune the embedding model using contrastive learning (e.g., sentence-transformers `MultipleNegativesRankingLoss`)
- Re-index with the fine-tuned model

**R&D task — Fine-tuned vs. base model:**
- Compare:
  1. Base embedding model (Tier 8 winner)
  2. Fine-tuned model (this epic)
- Measure on a held-out test set (20% of feedback data NOT used for training):
  - Recall@10, NDCG@10
- Also measure on the original 50-query eval set (to check for overfitting to feedback patterns)

**Acceptance criteria:**
- [ ] Fine-tuned model improves Recall@10 on the held-out feedback test set
- [ ] Fine-tuned model does NOT regress by more than 2% on the original 50-query eval set (no overfitting)
- [ ] Training curve (loss over epochs) is logged
- [ ] If insufficient feedback data exists (< 500 signals): document what's needed and defer this epic

---

### Epic 13.2 — Feedback Collection & Continuous Evaluation

**What to build:**
- UI feedback buttons: for each result, "Relevant" / "Not Relevant"
- Store: `(query, doc_id, relevant: bool, timestamp, user_id)`
- Automated weekly evaluation:
  1. Use accumulated feedback as expanding ground truth
  2. Re-run eval harness
  3. Log metrics over time
- Dashboard/report: metrics trend over time, worst-performing query categories

**Acceptance criteria:**
- [ ] Feedback buttons exist in UI, clicks are persisted to database
- [ ] After collecting 100 feedback signals (even from internal testing): eval harness runs with feedback-augmented ground truth
- [ ] Metrics-over-time chart is generated (even if only 2–3 data points initially)
- [ ] Report identifying top-5 worst-performing query categories (based on negative feedback) exists

---

### Epic 13.3 — Evaluation Dataset Expansion

**What to build:**
- Use feedback data + lawyer input to expand the eval set from 50 to 100+ queries
- Add new query types discovered through real usage that weren't in the original set
- Add new contradiction pairs discovered through conflict detection
- Re-baseline all metrics on the expanded eval set

**Acceptance criteria:**
- [ ] Eval set grows to 100+ queries
- [ ] New queries cover failure modes discovered in production usage (documented)
- [ ] All tiers' metrics are re-run on the expanded eval set and logged (new baseline row in the cumulative table)
- [ ] At least 20 new contradictory precedent pairs are annotated

---

### Tier 13 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Recall@10 | Improvement over Tier 12 (on expanded eval set) | Eval harness |
| Feedback signals | >= 500 collected | Database count |
| Eval set size | >= 100 queries | File row count |
| Metrics trend | Visible improvement over time | Time-series chart |

---

## Cumulative Metrics Tracking

Every tier's eval results must be recorded in a single persistent table:

```
| Run ID | Tier | Date | Config Description              | Recall@5 | Recall@10 | NDCG@10 | AW-NDCG@10 | MRR  | HitRate@5 | Conflict P | Conflict R | Latency p95 |
|--------|------|------|---------------------------------|----------|-----------|---------|-------------|------|-----------|------------|------------|-------------|
| 001    | 3    | ...  | Naive dense, BGE-base, 512tok   | ...      | ...       | ...     | -           | ...  | ...       | -          | -          | ...         |
| 002    | 3    | ...  | Naive dense, embed-3-large      | ...      | ...       | ...     | -           | ...  | ...       | -          | -          | ...         |
| 003    | 4    | ...  | + BM25 hybrid RRF               | ...      | ...       | ...     | -           | ...  | ...       | -          | -          | ...         |
| ...    | ...  | ...  | ...                             | ...      | ...       | ...     | ...         | ...  | ...       | ...        | ...        | ...         |
```

**Rules:**
1. Every R&D comparison adds rows to this table
2. No configuration is promoted without being in this table
3. Regressions are acceptable if documented with rationale (e.g., "Recall@10 dropped 1% but NDCG@10 improved 8%")
4. This table is the single source of truth for all architectural decisions
5. When the eval set expands (Tier 13), add a column noting which eval set version was used, and re-run key configs on the new set

---

## Tier Dependency Map

```
Tier 1: Measurement Infrastructure                          [DONE]
  │
  v
Tier 2: Data Pipeline & Storage                             [DONE]
  │
  v
Tier 3: Naive Dense Retrieval (TEI/bge-m3 on A100)          [DONE]
  │
  v
Tier 4: Graph Foundation (citations, Neo4j, PPR, PageRank)   [DONE]
  │
  v
Tier 5: Legal Intelligence Extraction
  │     (disposition, treatment keywords, retrieval diversification)
  │
  ├─────────────────────────┐
  v                         v
Tier 6: Hybrid Retrieval    Tier 7: Legal-Aware Chunking
  (BM25, RRF, API)           & Summaries
  │                         │
  └─────────┬───────────────┘
            v
Tier 8: Domain Embeddings & Re-Ranking
  │
  v
Tier 9: Graph-Powered Ranking & Authority Scoring
  │     (consolidated: PageRank boost, court boost,
  │      3-way fusion, treatment-weighted PageRank)
  │
  v
Tier 10: Conflict Detection & Treatment Refinement
  │      (holdings, pairwise conflicts, LLM treatment,
  │       good-law/bad-law, treatment-aware ranking)
  │
  v
Tier 11: Query Intelligence
  │
  v
Tier 12: Agentic Retrieval
  │
  v
Tier 13: Feedback Loops & Fine-Tuning
```
