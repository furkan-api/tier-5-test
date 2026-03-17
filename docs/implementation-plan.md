# Legal Jurisprudence Retrieval System — Tiered Implementation Plan

> Each tier builds on the previous one. Each tier is independently deployable and testable.
> Every epic has concrete acceptance criteria that can be verified with data or observation.
> No time/effort estimates are included — only scope and measurable outcomes.
>
> **Jurisdiction:** Turkish law (Türk Hukuku). All Turkish-specific details reference `docs/turkish-law-reference.md` — the companion cheat sheet for LLM agents working with Turkish legal data. That file covers the full court hierarchy, citation formats, appeal flows, and code transition dates.

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
- Results are logged to a persistent store (CSV or SQLite) with: run ID, timestamp, configuration label, git commit hash
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
- Stores metadata in SQLite (or PostgreSQL for production path)

**Acceptance criteria:**
- [ ] All documents in the corpus are ingested with no failures (zero documents dropped — or dropped docs are logged with reason)
- [ ] Each document has metadata fields: `doc_id`, `esas_no`, `karar_no`, `court`, `daire`, `court_level`, `law_branch`, `decision_date`, `file_path`
- [ ] Esas No and Karar No are extracted correctly for >= 95% of documents (spot-check 30)
- [ ] `SELECT count(*) FROM documents` returns the expected corpus size
- [ ] Court level distribution query produces sensible numbers (most docs should be level 3 Yargıtay/Danıştay daire decisions)
- [ ] Metadata extraction accuracy: spot-check 30 random documents — all have correct Esas No, Karar No, court, daire, and date
- [ ] Re-running ingestion on the same directory produces no duplicates (idempotent)

---

### Epic 2.2 — Fixed-Size Chunking with Document Back-Pointers

**What to build:**
- Chunk each document into fixed-size pieces (512 tokens, 50-token overlap as starting point)
- Each chunk stores: `chunk_id`, `doc_id`, `chunk_index`, `text`, `metadata` (inherited from parent document)
- Store chunks in a table alongside documents

**Acceptance criteria:**
- [ ] Every chunk has a valid `doc_id` back-pointer
- [ ] No chunk exceeds the configured token limit (verify with tokenizer count on 100 random chunks)
- [ ] Reconstructing all chunks for a given `doc_id` in order reproduces the original document text (minus overlap dedup)
- [ ] Total chunk count and average chunks-per-document are logged

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

**Acceptance criteria:**
- [ ] Vector DB contains exactly `num_chunks` embeddings (counts match)
- [ ] A sample query returns chunks from multiple different documents (not all from one doc)
- [ ] Retrieval returns results for all 50 evaluation queries (no empty results)

---

### Epic 3.2 — Document-Level Score Aggregation

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
- [ ] The system returns `doc_id`s (not `chunk_id`s) in the final ranked list
- [ ] Aggregation comparison table shows all three strategies with Recall@5, Recall@10, NDCG@10
- [ ] The chosen strategy is documented with rationale
- [ ] Given a result, the system can return the full document file path for each

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

---

## Tier 4 — Hybrid Retrieval & Usable API

**Goal:** Add BM25, fuse sparse+dense, expose via API and UI. After this tier, a lawyer can actually use the system.

---

### Epic 4.1 — BM25 Index & Hybrid Fusion

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

### Epic 4.2 — REST API

**What to build:**
- `POST /search` with body `{"query": "...", "top_k": 10}`
- Response: `[{"doc_id": "...", "score": 0.85, "court": "...", "date": "...", "case_number": "...", "file_path": "..."}]`
- Health check endpoint: `GET /health`
- OpenAPI/Swagger docs auto-generated

**Acceptance criteria:**
- [ ] API returns valid JSON for all 50 evaluation queries
- [ ] API response time is under 2 seconds (p95) for a single query
- [ ] Invalid requests return proper error responses (400), not 500s
- [ ] Swagger docs are accessible at `/docs`

---

### Epic 4.3 — Minimal Web UI

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

### Tier 4 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Recall@10 | Improvement over Tier 3 | Eval harness |
| NDCG@10 | Improvement over Tier 3 | Eval harness |
| API latency (p95) | < 2 seconds | 50 eval queries through the API |
| Usability | A lawyer can search and view a case document | Manual observation |

---

## Tier 5 — Legal-Aware Chunking & Document Summaries

**Goal:** Replace dumb fixed-size chunking with legal-structure-aware chunking. Add document-level summaries as a second retrieval index. These are independent improvements — either can land first.

---

### Epic 5.1 — Decision Type & Authority Extraction

**What to build:**
- For each document, extract content-level metadata that couldn't be parsed from filenames/front-matter in Tier 2:
  - **`decision_type`**: karar (original decision) / bozma (reversal) / onama (affirmance) / kısmen bozma (partial reversal) / direnme (defiance) — determined by reading the holding section or identifying keywords
  - **`decision_authority`**: `daire_karari` / `genel_kurul_karari` / `ibk` — İBK is a decision type from level-4 courts (HGK/CGK/İDDGK/VDDGK), not a separate court level. İBK decisions are binding on all courts.
- Use heading patterns, keywords (e.g., "BOZULMASINA", "ONANMASINA", "DİRENME KARARI"), or LLM classification
- Store as new fields on the document record

**Acceptance criteria:**
- [ ] Every document has `decision_type` and `decision_authority` fields populated (or `unknown` with logged reason)
- [ ] Spot-check 30 documents: `decision_type` is correct for >= 90%
- [ ] `unknown` rate is < 20% of corpus — if higher, document what patterns are missing

---

### Epic 5.2 — Structure-Aware Legal Chunking

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
  1. Tier 3/4 fixed-size chunks
  2. Structure-aware chunks
- Run eval on both with the same retrieval pipeline
- Also measure: average chunk size (tokens), number of chunks per document, standard deviation of chunk sizes

**Acceptance criteria:**
- [ ] Structure-aware chunks do NOT break mid-sentence (verify on 50 random chunks by reading them)
- [ ] At least 80% of chunks have a valid `section_type` label (not "unknown")
- [ ] A/B comparison table shows Recall@10 and NDCG@10 for both strategies
- [ ] Structure-aware chunking achieves higher Recall@10, OR the delta is documented with analysis of why not

---

### Epic 5.3 — Document Summary Generation

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

### Epic 5.4 — Summary Index & Multi-Level Retrieval

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

### Tier 5 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Decision type accuracy | >= 90% on 30-doc spot-check | Manual review |
| Recall@10 | Improvement over Tier 4 | Eval harness |
| NDCG@10 | Improvement over Tier 4 | Eval harness |
| Chunking quality | No mid-sentence breaks in 50-chunk sample | Manual review |
| Summary quality | Lawyer rating >= 3.5/5 | Blind evaluation |

---

## Tier 6 — Domain Embeddings & Re-Ranking

**Goal:** Swap in legal-specific embeddings and add cross-encoder re-ranking. These are the two highest-ROI improvements based on literature (Anthropic's contextual retrieval study showed re-ranking alone cuts failures by ~20%).

---

### Epic 6.1 — Legal Embedding Model

**What to build:**
- Re-embed all chunks and summaries using a legal-domain embedding model
- Update the vector indices

**R&D task — Legal embedding comparison (head-to-head):**
- Compare exactly these models on the full evaluation set:
  1. Tier 4 winner (general-purpose multilingual model)
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

### Epic 6.2 — Cross-Encoder Re-Ranking

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
- [ ] Re-ranking improves NDCG@10 over Tier 5 (ordering gets better, even if recall stays the same)
- [ ] Comparison table with metrics and latency for all 3 models exists
- [ ] End-to-end API latency (p95) is documented — if > 5 seconds, document which re-ranker stays under 5s
- [ ] 10 specific query examples where re-ranking changed the document ordering, with before/after shown

---

### Epic 6.3 — Contextual Embeddings (Anthropic-Style)

**What to build:**
- Before embedding each chunk, prepend a 50–100 token LLM-generated context explaining the chunk's role in the larger document
- Example context: "This chunk is from Yargıtay 9. HD, Case 2020/12345 E., a bozma (reversal) decision about wrongful termination. It describes the court's reasoning on burden of proof."
- The context generation prompt language (Turkish vs. English) should be tested as part of the R&D task — the embedding model may handle one better than the other.
- Re-embed all chunks with contextual prefixes
- Use prompt caching to reduce LLM cost

**R&D task — Contextual vs. standard embeddings:**
- Compare:
  1. Best non-contextual embeddings (from 6.1)
  2. Same model with contextual prefixes
- Measure: Recall@10, NDCG@10
- Also measure: context generation cost ($ per 1000 documents), storage size increase

**Acceptance criteria:**
- [ ] All chunks have contextual prefixes generated and stored
- [ ] Comparison table: contextual vs. non-contextual on Recall@10, NDCG@10
- [ ] Cost analysis documented: total cost of context generation for full corpus
- [ ] At least 10 queries where contextual embeddings improve results, with explanation of what the context added

---

### Tier 6 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Recall@10 | Improvement over Tier 5, OR no improvement with documented analysis of why and what was tried | Eval harness |
| NDCG@10 | Improvement over Tier 5, OR no improvement with documented analysis | Eval harness |
| API latency (p95) | < 5 seconds (re-ranking adds latency) | Load test |
| R&D artifacts | All comparison tables from 6.1–6.3 exist | File review |

---

## Tier 7 — Citation Graph Construction

**Goal:** Build the knowledge graph of case-to-case citations and the court hierarchy. No retrieval changes yet — this tier is purely about graph data infrastructure.

---

### Epic 7.1 — Citation Extraction Pipeline

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

### Epic 7.2 — Court Hierarchy Graph

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
- [ ] The graph correctly models the bozma→direnme→Genel Kurul flow (Daire → İlk Derece/BAM → same Daire → HGK/CGK if direnme)

---

### Epic 7.3 — Full Citation Network & Graph Statistics

**What to build:**
- Import all citation edges (from 7.1) into the graph alongside the court hierarchy (from 7.2)
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

### Tier 7 — Exit Criteria

| Artifact | Verification |
|----------|-------------|
| Citation edges | Extracted, resolved, stored with precision >= 0.85 |
| Court hierarchy | Complete, verified by a lawyer |
| Graph DB | Loaded, queryable, stats report generated |
| PageRank | Computed, top-10 validated as landmark cases |

**No retrieval metrics change in this tier — it's pure infrastructure. The graph will be used in Tiers 8 and 9.**

---

## Tier 8 — Graph-Powered Ranking

**Goal:** Use the citation graph and court hierarchy to improve retrieval ranking. Add graph traversal as a third retrieval path. Every improvement must beat Tier 6 numbers.

---

### Epic 8.1 — PageRank-Boosted Re-Ranking

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

### Epic 8.2 — Court-Level Boost

**What to build:**
- Apply a court-level boost:
  `boosted_score = score × (1 + beta × court_level / max_court_level)`
- Applied after cross-encoder re-ranking, can be combined with PageRank boost

**R&D task — Court boost calibration:**
- Test beta values: **0 (no boost), 0.1, 0.2, 0.5, 1.0**
- Measure NDCG@10, Authority-weighted NDCG@10, and Recall@10
- For the 10 eval queries that have relevant results from both high and low courts: check that high-court cases rank higher with boosting

**Acceptance criteria:**
- [ ] Beta comparison table with all metrics for all 5 values
- [ ] Chosen beta does NOT decrease Recall@10 by more than 2%
- [ ] On the 10 mixed-court queries: verify high-court cases rank higher than low-court cases with boosting enabled
- [ ] Combined effect of PageRank + court boost (best alpha × best beta) is measured and compared against Tier 6 baseline

---

### Epic 8.3 — Graph Traversal as Retrieval Path

**What to build:**
- Add a third retrieval path: **1-hop citation expansion**
- Given a query, find top-5 documents via dense retrieval, then expand to their citation neighbors (cases they cite + cases that cite them)
- Add neighbors to the candidate pool before re-ranking
- Three-way RRF: BM25 + Dense + Graph-neighbors

**R&D task — Graph retrieval contribution:**
- Compare:
  1. Tier 6 pipeline (BM25 + Dense + Re-ranking)
  2. + Graph traversal (this epic)
- Measure Recall@10, NDCG@10, Authority-weighted NDCG@10
- Count per query: how many documents in the final top-10 were found ONLY through graph traversal?

**Acceptance criteria:**
- [ ] Graph traversal adds at least 5 unique candidate documents per query on average (that weren't found by BM25 or dense)
- [ ] Recall@10 improves over Tier 6, OR the delta is documented with analysis
- [ ] Number of "graph-only finds" that are actually relevant (per eval set) is documented
- [ ] End-to-end latency (p95) is under 8 seconds — if not, document the bottleneck

---

### Epic 8.4 — Metadata Filtering in API

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

### Tier 8 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Recall@10 | Improvement over Tier 6 (or within 2% with significant NDCG gain) | Eval harness |
| NDCG@10 | Improvement over Tier 6 | Eval harness |
| Authority-weighted NDCG@10 | Baselined and tracked (new metric) | Eval harness |
| Graph-only finds | >= 5 unique candidates/query from graph | Logged per query |
| API latency (p95) | < 8 seconds | Load test |

---

## Tier 9 — Conflict Detection & Contradiction Surfacing

**Goal:** The distinguishing feature — detect and surface contradictory precedents. This is what makes the system more than a search engine.

---

### Epic 9.1 — Holding Extraction

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

### Epic 9.2 — Pairwise Conflict Detection

**What to build:**
- Given a set of retrieved documents (top 10), detect contradictions:
  1. Embed all holdings; compute pairwise cosine similarity to find "topically related" pairs (similarity > threshold)
  2. For each related pair, LLM prompt: "Case A holds: [holding]. Case B holds: [holding]. Do these cases reach opposing conclusions on the same legal issue? Respond: AGREES / DISAGREES / UNRELATED, with a one-sentence explanation."
  3. If DISAGREES: create a conflict annotation
- **Prerequisite:** `decision_type` must be populated on documents (Tier 5 Epic 5.1).
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

### Epic 9.3 — Conflict-Aware Result Presentation

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

### Tier 9 — Exit Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Conflict Precision | >= 0.70 | Lawyer evaluation on 10 conflict queries |
| Conflict Recall | >= 0.50 | Against known conflicts in eval set |
| Holding quality | Lawyer rating >= 3.5/5 | 30-document sample |
| UI conflict display | Lawyer can identify higher court in < 5 seconds | Observation |

**If precision/recall targets are not met:** Document which conflict types work (inter-daire, temporal, bozma-direnme) and which don't, and whether the approach is viable with more annotated data or a different similarity threshold.

---

## Tier 10 — Citation Treatment Classification

**Goal:** Classify HOW cases cite each other (follows, distinguishes, overrules, criticizes). This enables "good law / bad law" detection — the most valuable legal feature after basic retrieval.

---

### Epic 10.1 — Citation Treatment Classifier

**What to build:**
- For each citation edge in the graph, classify the treatment into 5 categories:
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
- [ ] Classification runs on all citation edges in the corpus
- [ ] Macro-average F1 >= 0.65 on the 100-edge test set
- [ ] Per-class metrics (precision, recall, F1) exist for all 5 treatment types
- [ ] `OVERRULES` class Recall >= 0.80 (most critical — missing an overruled/bozma'd case is dangerous for lawyers)
- [ ] Context ablation comparison documented (paragraph-only vs. paragraph + summaries)
- [ ] Classification cost for the full corpus is logged

---

### Epic 10.2 — Good Law / Bad Law Status

**What to build:**
- Derive case status from citation treatments:
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

### Epic 10.3 — Treatment-Aware Ranking

**What to build:**
- Modify the ranking pipeline:
  - `overruled` cases get a **penalty** (multiplicative: score × 0.3) — still visible but ranked lower
  - `disputed` cases get a **moderate penalty** (score × 0.6) + a visual flag indicating the legal question is pending HGK/CGK resolution
  - Cases with many `FOLLOWS` treatments get a **boost** (treatment-weighted PageRank: FOLLOWS edges count as 1.5×, CRITICIZES as 0.5×, OVERRULES as 0×)
  - İBK decisions (`decision_authority: ibk`) get NO penalty ever — they are always authoritative
  - Overruled cases show a visual warning in the UI (e.g., red "OVERRULED" / "BOZULMUŞ" badge, with the overruling case linked)

**R&D task — Treatment-aware ranking impact:**
- Compare:
  1. Tier 8 ranking (PageRank + court boost, no treatment awareness)
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
| Treatment F1 (macro-avg) | >= 0.65 | 100-edge annotated test set |
| OVERRULES Recall | >= 0.80 | Per-class metrics |
| Status accuracy | Spot-check: 80%+ correct on 40 sampled cases | Lawyer review |
| NDCG@10 | Improvement or no regression vs. Tier 8 | Eval harness |
| Overruled-in-top-5 | Decreases vs. Tier 8 | Count comparison |

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
  4. Document aggregation + re-ranking (Tier 6 + Tier 8 signals)
  5. Self-correction if needed (12.2)
  6. Conflict detection (Tier 9)
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

## Tier 13 — Advanced Graph RAG & Feedback Loops

**Goal:** Push retrieval quality further with SOTA graph-RAG techniques and build the feedback infrastructure for continuous improvement.

---

### Epic 13.1 — HippoRAG-Style PPR Retrieval

**What to build:**
- Replace simple 1-hop graph traversal with **Personalized PageRank (PPR):**
  - Extract entities from the query
  - Find matching entity nodes in the citation graph
  - Run PPR from those seed nodes — activation spreads through the graph
  - Documents with highest PPR scores become graph-retrieval candidates
- This enables **multi-hop** discovery: finding cases connected through intermediate citations

**R&D task — PPR vs. 1-hop vs. LightRAG:**
- On a 1000-document subset, implement:
  1. Simple 1-hop expansion (current)
  2. PPR with damping factor 0.85 (standard)
  3. PPR with damping factor 0.70 (broader spread)
  4. LightRAG dual-level retrieval (if feasible on the subset)
- Measure: Recall@10, NDCG@10, number of multi-hop finds (cases found via 2+ hops that are relevant)

**Acceptance criteria:**
- [ ] Comparison table with all approaches and metrics
- [ ] Multi-hop analysis: at least 5 specific cases where PPR found a relevant case that 1-hop missed, with the connecting path shown
- [ ] Best PPR config improves Recall@10 over 1-hop on the subset
- [ ] Damping factor comparison shows which value gives best precision/recall tradeoff

---

### Epic 13.2 — Embedding Fine-Tuning on Feedback Data

**Prerequisite:** Epic 13.3 (Feedback Collection) must be deployed first. This epic runs only after 500+ feedback signals are collected.

**What to build:**
- Collect user feedback data (from UI: "Relevant" / "Not Relevant" clicks)
- Once 500+ feedback signals exist, construct training pairs:
  - Positive: (query, relevant_doc_summary) pairs from "Relevant" clicks
  - Negative: (query, irrelevant_doc_summary) pairs from "Not Relevant" clicks
- Fine-tune the embedding model using contrastive learning (e.g., sentence-transformers `MultipleNegativesRankingLoss`)
- Re-index with the fine-tuned model

**R&D task — Fine-tuned vs. base model:**
- Compare:
  1. Base embedding model (Tier 6 winner)
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

### Epic 13.3 — Feedback Collection & Continuous Evaluation

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

### Epic 13.4 — Evaluation Dataset Expansion

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
| Multi-hop finds | >= 5 cases found via PPR that 1-hop missed | Documented examples |
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
Tier 1: Measurement Infrastructure
  │
  ▼
Tier 2: Data Pipeline & Storage
  │
  ▼
Tier 3: Naive Dense Retrieval ──── first baseline numbers
  │
  ▼
Tier 4: Hybrid Retrieval & API ──── first usable system
  │
  ├──────────────────┐
  ▼                  ▼
Tier 5: Legal       Tier 7: Citation Graph ──── graph infrastructure
Chunking &            │                         (no retrieval change)
Summaries             │
  │                   │
  ▼                   ▼
Tier 6: Domain      Tier 8: Graph-Powered
Embeddings &        Ranking
Re-Ranking            │
  │                   ▼
  │                 Tier 9: Conflict Detection
  │                   │
  │                   ▼
  │                 Tier 10: Citation Treatment
  │                   │
  ├───────────────────┘
  ▼
Tier 11: Query Intelligence
  │
  ▼
Tier 12: Agentic Retrieval
  │
  ▼
Tier 13: Advanced Graph RAG & Feedback
```

**Key insight from the dependency map:** Tiers 5–6 (chunking, embeddings, re-ranking) and Tiers 7–10 (graph construction, graph ranking, conflicts, treatments) can be developed **in parallel** by different people/teams. They converge at Tier 11.
