# Legal Jurisprudence Retrieval System: SOTA Research Report

> **Date:** March 2026
> **Scope:** RAG, KAG, and Graph-based retrieval approaches for legal case law document retrieval

---

## 1. Problem Definition

### What We Are Building

A **jurisprudence (case law) retrieval system** that, given a legal query or case description, returns **whole case law documents** (not snippets or generated answers) that are relevant to the user's legal research needs.

### Why This Is Hard (Beyond Standard RAG)

| Challenge | Description |
|-----------|-------------|
| **Whole-document retrieval** | Lawyers need the full case file, not a 500-token chunk. Standard RAG optimizes for passage-level retrieval — we need document-level ranking. |
| **Court hierarchy matters** | A Supreme Court ruling overrides a conflicting Appeals Court decision. The system must understand and encode this authority structure. |
| **Contradictory precedents** | Different courts (or the same court at different times) may issue opposing rulings on the same legal issue. The system must **surface both sides**, not pick one. |
| **Temporal dynamics** | A case can be overruled, distinguished, or affirmed by later decisions. Recency alone doesn't determine authority — a 30-year-old Supreme Court precedent can override yesterday's lower court ruling. |
| **Citation networks** | Cases form a citation graph. A case's importance is partly determined by how many later cases cite it and with what treatment (followed, distinguished, overruled). |
| **Legal specificity** | Legal language is precise and domain-specific. General-purpose embeddings miss nuances (e.g., "consideration" has a very specific meaning in contract law). |
| **Not a Q&A system** | We are NOT building a system that answers legal questions. We are building a **retrieval/search system** that finds and ranks the most relevant case law documents, similar to what Westlaw or LexisNexis does — but potentially smarter. |

### The Core Task

```
INPUT:  A lawyer's query describing a legal situation, issue, or topic
OUTPUT: A ranked list of full jurisprudence documents (case law files),
        with metadata about court level, date, citation treatment,
        and any conflicting decisions flagged
```

---

## 2. SOTA RAG Architectures (2024–2026)

### 2.1 RAG Taxonomy

The field has evolved through three generations (Gao et al., "RAG for LLMs: A Survey", arXiv:2312.10997):

| Generation | Description |
|-----------|-------------|
| **Naive RAG** | Chunk → Embed → Retrieve top-k → Stuff into LLM prompt. Simple but loses document context, can't handle multi-hop reasoning. |
| **Advanced RAG** | Adds pre-retrieval optimization (query rewriting, HyDE) and post-retrieval processing (re-ranking, compression). |
| **Modular RAG** | Composable pipeline with specialized modules. The dominant paradigm today. |

### 2.2 Key RAG Variants

#### GraphRAG (Microsoft, 2024)
- **Paper:** arXiv:2404.16130 | **Repo:** github.com/microsoft/graphrag (31k+ stars)
- Builds a knowledge graph from documents using LLM extraction, then applies the **Hierarchical Leiden algorithm** for community detection
- **Indexing pipeline:** Text segmentation → Entity/relationship extraction → Community detection → Community summarization → Embedding
- **Query modes:**
  - **Global Search:** Map-reduce over community summaries — answers thematic questions across entire corpus
  - **Local Search:** Entity-centric expansion through graph neighborhood
  - **DRIFT Search:** Combines local + community info with follow-up sub-questions
- **Relevance to our problem:** Community detection could identify legal topic clusters. Graph structure naturally models case-to-case citations.
- **Weakness:** Very expensive indexing (many LLM calls per chunk). Complex setup.

#### KAG — Knowledge Augmented Generation (Ant Group/OpenSPG, 2024)
- **Paper:** arXiv:2409.13731 | **Repo:** github.com/OpenSPG/KAG
- Goes beyond RAG by combining structured knowledge graph reasoning with text retrieval
- **Three components:**
  - **kg-builder:** Constructs knowledge using DIKW hierarchy. Supports schema-free (open extraction) and schema-constrained (domain ontology) modes
  - **kg-solver:** Logical reasoning engine with planning, reasoning, and retrieval operators. Transforms natural language into combined language-symbol problem-solving
  - **kag-model:** Fine-tuned models for the pipeline
- **Key innovation — Mutual Indexing:** Bidirectional cross-references between graph entities and source text chunks. During reasoning, the solver traverses seamlessly between structured KG facts and original text.
- **Relevance to our problem:** Schema-constrained mode is ideal for legal domain — we can define ontology for courts, statutes, legal concepts. Logical reasoning handles "which court's decision takes precedence?" type queries.
- **Weakness:** Tied to OpenSPG ecosystem. Complex setup.

#### LightRAG (EMNLP 2025)
- **Paper:** arXiv:2410.05779 | **Repo:** github.com/HKUDS/LightRAG
- Simpler, faster alternative to GraphRAG with **dual-level retrieval:**
  - **Low-level (Local):** Entity-centric vector similarity
  - **High-level (Global):** Relationship-based graph traversal
- Six query modes: naive, local, global, hybrid, mix, bypass
- Supports Neo4j, PostgreSQL, MongoDB, Milvus, Qdrant backends
- **Benchmarks:** 67.6% comprehensiveness vs NaiveRAG's 32.4%. Significantly faster than GraphRAG.
- **Relevance:** Good balance of graph-awareness and simplicity. Could model case relationships without GraphRAG's overhead.

#### RAPTOR (Stanford, 2024)
- **Paper:** arXiv:2401.18059 | **Repo:** github.com/parthsarthi03/raptor
- Builds a **recursive tree** via clustering and summarization:
  1. Leaf nodes = original chunks
  2. **GMM clustering** with UMAP dimensionality reduction (soft clustering — chunks can belong to multiple clusters)
  3. LLM summarizes each cluster → new higher-level node
  4. Recurse until convergence
- **Retrieval:** "Collapsed tree" mode flattens all levels into one retrieval pool — queries match at any abstraction level
- **Relevance:** Could create hierarchical summaries of case law by topic area, enabling both specific and thematic retrieval.

#### HippoRAG (2024)
- **Paper:** arXiv:2405.14831 | **Repo:** github.com/OSU-NLP-Group/HippoRAG
- Inspired by hippocampal memory — uses **Personalized PageRank (PPR)** on a knowledge graph
- OpenIE extracts entities/relations → query entities seed PPR → activation spreads through graph
- **Cheapest graph approach** for indexing (no LLM calls for graph traversal at query time)
- **Excels at multi-hop retrieval** — finding documents connected through intermediate entities
- **Relevance:** PPR is essentially what legal citation analysis does — propagating "importance" through a citation network. Natural fit.

#### Self-RAG & Corrective RAG (CRAG)
- **Self-RAG** (arXiv:2310.11511): LLM decides when/what to retrieve using reflection tokens
- **CRAG** (arXiv:2401.15884): Evaluates retrieval quality; triggers corrective actions (query rewrite, web search) if confidence is low
- **Relevance:** Could detect when initial retrieval misses important opposing precedents and trigger expanded search.

#### Agentic RAG (2024–2026)
- Emerging dominant paradigm where an LLM agent orchestrates retrieval dynamically
- **Patterns:** Query routing, orchestrator-workers, iterative retrieval, self-correction
- An agent can: decompose complex legal queries → route to different retrieval strategies (vector, graph, keyword) → evaluate results → refine and re-retrieve
- **Key reference:** Anthropic's "Building Effective Agents" (anthropic.com/research/building-effective-agents)
- **Relevance:** Highly relevant — a legal retrieval agent could decide whether to search by statute, by topic, by citation network, or by court hierarchy depending on the query type.

### 2.3 Architecture Comparison

| System | Indexing Cost | Query Cost | Multi-hop | Global Queries | Best For |
|--------|-------------|-----------|-----------|---------------|----------|
| **GraphRAG** | Very High | Medium-High | Moderate | Excellent | Theme extraction, corpus summarization |
| **KAG** | High | Medium | Good | Good | Domain-specific Q&A with ontology |
| **LightRAG** | Medium-High | Low-Medium | Moderate | Good | General graph RAG, simpler deployment |
| **RAPTOR** | Medium | Low | Low | Good | Long-document hierarchical retrieval |
| **HippoRAG** | Low-Medium | Low | Excellent | Weak | Associative multi-hop, citation networks |
| **Agentic RAG** | Varies | High | Excellent | Varies | Complex multi-strategy queries |

---

## 3. Document-Level Retrieval (Whole-File Return)

This is critical for our use case — lawyers need the full case document.

### 3.1 Approaches

#### Hierarchical / Two-Stage Retrieval
1. **Stage 1 (Recall):** Retrieve candidate chunks via embedding similarity or BM25
2. **Stage 2 (Document Aggregation):** Aggregate chunk-level scores to document-level scores, then rank documents
- **Aggregation methods:** max-score, mean-score, CombSUM across chunks belonging to the same document

#### Parent-Child / Small-to-Big Retrieval (LlamaIndex)
- Embed **small, specific chunks** (128–256 tokens) for retrieval precision
- At retrieval time, return the **parent document** (or larger section)
- `AutoMergingRetriever`: If enough child chunks from the same parent are retrieved, automatically merges up to the parent
- **This is the most directly applicable pattern for our use case**

#### Document Summary Index
- Generate a summary for each case law document
- Retrieve by matching queries against summaries
- Return the full document when a summary matches
- LlamaIndex's `DocumentSummaryIndex` implements this
- **Highly relevant:** Each jurisprudence file could have an LLM-generated summary capturing key legal issues, court, date, and outcome

#### Proposition-Based Indexing (Dense X Retrieval, arXiv:2312.06648)
- Convert documents into atomic propositions
- Embed propositions but store back-pointers to source documents
- Retrieve propositions → return full documents
- **Relevant:** Legal cases could be decomposed into discrete legal propositions/holdings

#### ColBERT / Late-Interaction for Document Ranking
- Token-level MaxSim scoring naturally aggregates to document level
- More expressive than single-vector approaches for long documents
- **RAGatouille** library makes ColBERT easy to use

### 3.2 Recommended Strategy for Our System

```
┌─────────────────────────────────────────────────┐
│           MULTI-LEVEL INDEXING                   │
├─────────────────────────────────────────────────┤
│ Level 1: Document summaries (LLM-generated)     │
│ Level 2: Section/heading-level chunks           │
│ Level 3: Paragraph-level chunks (fine-grained)  │
│ Level 4: Legal propositions (atomic holdings)   │
│                                                 │
│ All levels maintain back-pointers to the        │
│ source document for whole-file retrieval        │
└─────────────────────────────────────────────────┘
```

Retrieval flow:
1. Query matches against all levels simultaneously
2. Scores aggregate to the document level
3. Documents are re-ranked considering court hierarchy and citation importance
4. Full documents are returned, ranked

---

## 4. Legal-Domain-Specific Research

### 4.1 Legal RAG Systems in Practice

#### NyayaAI (Indian Legal AI) — Most Sophisticated Open System
- **Repo:** github.com/krishang118/NyayaAI
- **Scale:** 154,068 nodes, 725,563 edges in knowledge graph
- **Data:** 56,025 judgments from Supreme Court and 5 High Courts (2000–2024)
- **Ontology:** IndiLegalOnt — formal legal ontology
- **Hybrid Retrieval Strategy (key innovation):**
  - SBERT semantic similarity: **70% weight**
  - Graph neural context (3-layer GAT with 4 attention heads): **15% weight**
  - Symbolic legal features (court hierarchy, citations, statutory grounding): **15% weight**
  - Result: P@5 of **0.89** vs text-only **0.74**
- **Court hierarchy weighting:** Supreme Court decisions ranked higher than High Court decisions
- **PageRank:** Used for importance scoring within the citation network
- **GAT performance:** 96.55% F1-score on entity classification

#### LexRAG — Legal RAG Benchmark
- **Repo:** github.com/CSHaitao/LexRAG
- 1,013 multi-turn conversations (5 rounds each) simulating legal consultations
- Three corpus types: 17,228 statutory provisions, legal books, case law
- Tests dense (BGE, GTE + FAISS) and sparse (BM25, QLD) retrieval
- Evaluates with ROUGE, BLEU, BERTScore, NDCG, Recall, MRR + LLM-as-judge

#### Graph-Augmented Legal RAG (German Law)
- **Repo:** github.com/TilmanLudewigtHaufe/GraphAugmented-Legal-RAG (71 stars)
- Knowledge graphs from unstructured legal text
- TF-IDF + cosine similarity + graph structure

#### Justicio (Spanish Law)
- **Repo:** github.com/bukosabino/justicio (139 stars)
- RAG for Spain's official gazette (BOE)
- 1,200-char chunks with metadata, hybrid search, daily ETL for legislative updates

### 4.2 Legal Embeddings & NLP Models

| Model | Type | Domain | Notes |
|-------|------|--------|-------|
| **voyage-law-2** | Embedding (1024d, 16K tokens) | Legal | Domain-specific from Voyage AI. Best commercial option for legal retrieval. |
| **Legal-BERT** | Language model | Legal | Pre-trained on court decisions, legislation, contracts. Outperforms general BERT on legal tasks. |
| **Saul-7B** | LLM (7B params) | Legal | Based on Mistral-7B, instruction-tuned for legal tasks (Equall AI). |
| **InLegalBERT** | Language model | Indian legal | Used for court judgment summarization. |
| **BGE-en-icl** | Embedding (4096d) | General | Few-shot in-context learning; can be adapted to legal with examples. |
| **GTE-Qwen2** | Embedding (8192 tokens) | General | Strong open-source, long context. |
| **jina-embeddings-v3** | Embedding (8192 tokens) | General | Task-specific LoRA adapters; could be fine-tuned for legal. |

**Key insight:** `voyage-law-2` is the only production-ready legal-specific embedding model. For open-source, fine-tuning BGE or GTE on legal corpora is the recommended path.

### 4.3 Commercial Legal AI Platforms (What They Do)

| Platform | Approach |
|----------|----------|
| **Westlaw (Thomson Reuters)** | Boolean + NL search + editorial metadata (headnotes, key numbers, legal topic taxonomy). **KeyCite** tracks citation treatments (followed, distinguished, overruled). |
| **LexisNexis** | **Shepard's Citations** — the gold standard for precedent tracking. Lexis+ AI adds generative capabilities. |
| **CaseText/CoCounsel** | GPT-4-powered legal AI (acquired by Thomson Reuters). Legal-specific RAG with firm-specific corpora. |
| **Harvey AI** | Custom LLMs + legal RAG for contract analysis, research, due diligence. |
| **vLex (Vincent AI)** | Multi-jurisdictional legal research AI. |

**Key takeaway:** The commercial leaders all rely on **curated metadata and citation treatment tracking** — not just text similarity. Westlaw's editorial key number system and Shepard's/KeyCite citation analysis are their core differentiators that pure RAG cannot replicate without explicit modeling.

### 4.4 Handling Contradictory Precedents

This is the hardest unsolved problem in legal AI. Current approaches:

| Approach | How It Works | Limitations |
|----------|-------------|-------------|
| **Court hierarchy weighting** (NyayaAI) | Supreme Court > Appeals > District in ranking scores | Doesn't handle conflicts within the same court level |
| **Temporal recency** | More recent decisions ranked higher | Legally naive — old Supreme Court precedent can override new lower court rulings |
| **Citation treatment analysis** (Westlaw/LexisNexis) | Track whether a case was followed, distinguished, overruled, or criticized | Requires massive manual annotation or NLP classification |
| **Conflict detection** (Legal Research Assistant) | Semantic comparison across documents to flag contradictions | High false positive rate; hard to define "contradiction" precisely |
| **Multi-dimensional presentation** | Surface authorities from all sides, organized by jurisdiction, court level, date, citation frequency | Defers judgment to the lawyer (which may be correct behavior) |
| **Citation network PageRank** | Cases cited more frequently and by higher courts get higher authority scores | Doesn't capture the direction of citation (approving vs. criticizing) |

**Recommended approach for our system:** Combine court hierarchy weighting + citation network analysis + explicit conflict surfacing. **Do not try to resolve contradictions** — surface them with context and let the lawyer decide. This is both more legally correct and more tractable.

### 4.5 Academic Benchmarks & Datasets

| Benchmark | Description |
|-----------|-------------|
| **COLIEE** | Annual competition: statute retrieval, statute entailment, case law retrieval, case law entailment. Canadian + Japanese corpora. Running since 2014. |
| **LegalBench** (arXiv:2308.11462) | 162 tasks from 40 contributors. Community-driven legal reasoning benchmark. |
| **CUAD** (NeurIPS 2021) | 13,000+ labels in 510 commercial contracts. |
| **LEXTREME** | Multi-lingual, multi-task legal benchmark. |
| **MultiLegalPile** | 689GB multilingual legal corpus. |
| **BSARD** | Belgian Statutory Article Retrieval Dataset. |
| **CAIL** | Chinese AI & Law Challenge. |

---

## 5. Chunking Strategies for Legal Documents

### 5.1 General SOTA Chunking

| Strategy | How It Works | Pros | Cons |
|----------|-------------|------|------|
| **Fixed-size** (256–1024 tokens, 10–20% overlap) | Split at token count | Simple, predictable | Breaks mid-sentence/clause |
| **Semantic chunking** | Embed sentences; split where cosine similarity drops | Semantically coherent chunks | Variable size; slower |
| **Hierarchical chunking** | Chunk at multiple granularities (sentence, paragraph, section) | Multi-scale retrieval | More storage; complex indexing |
| **Late chunking** (Jina AI, arXiv:2409.04701) | Run full document through long-context model FIRST, then chunk the contextualized embeddings | Each chunk retains full-document context | Requires long-context embedding model |
| **Structure-aware** | Parse by headings, sections, tables | Respects document structure | Requires document parsing |
| **LLM-based / Agentic** | LLM identifies natural boundaries | Highest quality | Expensive |

### 5.2 Legal-Specific Chunking Requirements

Legal documents **cannot** be split at arbitrary boundaries. Key principles:

1. **Preserve clause and section boundaries** — splitting mid-clause destroys legal meaning
2. **Maintain structural hierarchy** — a case opinion has distinct sections (facts, issues, holdings, reasoning, disposition) that should be preserved
3. **Keep metadata attached** — court name, date, case number, judges, parties must travel with every chunk
4. **Section-aware splitting** — use document structure (headings, numbered paragraphs) as natural chunk boundaries

**Recommended:** Structure-aware chunking using document parsing (e.g., Unstructured.io, docling/IBM, LlamaParse) + hierarchical indexing at section and paragraph levels, with full-document summaries as a top-level index.

---

## 6. Retrieval & Re-Ranking Pipeline

### 6.1 Hybrid Retrieval (Table Stakes)

Combining sparse + dense retrieval is now the baseline, not the innovation:

- **BM25 (sparse):** Catches exact legal terms, case numbers, statute references (e.g., "Article 301/2 TPC")
- **Dense embeddings:** Captures semantic similarity, paraphrases, conceptual matches
- **Fusion:** Reciprocal Rank Fusion (RRF) or alpha-weighted combination

**Anthropic's Contextual Retrieval** (anthropic.com/engineering/contextual-retrieval) showed:
- Contextual Embeddings + BM25 + Reranking = **67% reduction** in retrieval failures vs naive embedding

### 6.2 Re-Ranking Stack

Production-grade multi-stage pipeline:

```
Stage 1: Hybrid retrieval (BM25 + dense) → top 50-100 candidates
    ↓
Stage 2: Cross-encoder re-ranking → top 10-20
    ↓
Stage 3: (Optional) LLM listwise reranking → top 3-5
    ↓
Stage 4: Legal-specific re-ranking (court hierarchy, citation weight, temporal)
    ↓
OUTPUT: Ranked full documents with conflict annotations
```

### 6.3 SOTA Re-Ranking Models

| Model | Type | Notes |
|-------|------|-------|
| **Cohere Rerank v3.5** | Cross-encoder | Top commercial performer |
| **bge-reranker-v2-gemma** | Cross-encoder | Best open-source |
| **jina-reranker-v2** | Cross-encoder | Good open-source option |
| **ColBERTv2 / ColBERTv2.5** | Late-interaction | Can serve as both retriever and reranker |
| **RankGPT** (arXiv:2304.09542) | LLM-based listwise | LLM generates a permutation of passages. Expensive but accurate. |

### 6.4 Legal-Specific Re-Ranking Signals

Beyond semantic relevance, legal retrieval should incorporate:

| Signal | Weight Factor | Rationale |
|--------|--------------|-----------|
| **Court level** | Higher court = higher weight | Supreme Court > Appeals > District |
| **Citation count** | More-cited = higher weight | Indicates established precedent |
| **Citation treatment** | Followed > Distinguished > Criticized > Overruled | A case that has been overruled should be flagged, not buried |
| **Temporal recency** | Mild boost for recent decisions | Recent cases may reflect current legal thinking, but old high-court cases can still be authoritative |
| **Jurisdictional relevance** | Same jurisdiction = higher weight | Out-of-jurisdiction cases are persuasive, not binding |
| **PageRank in citation graph** | Higher PageRank = higher authority | Structural importance in the citation network |

---

## 7. Knowledge Graphs for Legal Retrieval

### 7.1 Why Knowledge Graphs Matter for Law

Legal documents form a **natural graph structure:**

```
                 ┌──────────────┐
                 │  STATUTE     │
                 │ (Article X)  │
                 └──────┬───────┘
                        │ interprets
           ┌────────────┼────────────┐
           ▼            ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │ Supreme  │ │ Supreme  │ │ Appeals  │
    │ Court    │ │ Court    │ │ Court    │
    │ Case A   │ │ Case B   │ │ Case C   │
    └────┬─────┘ └────┬─────┘ └─────┬────┘
         │ overrules   │ follows     │ cites
         ▼             ▼             ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │ Appeals  │ │ District │ │ District │
    │ Case D   │ │ Case E   │ │ Case F   │
    └──────────┘ └──────────┘ └──────────┘
```

Standard vector search **misses these structural relationships entirely.**

### 7.2 Recommended Graph Schema

```
Node Types:
  - Case (properties: court, date, parties, outcome, summary, embedding)
  - Statute (properties: article_number, text, effective_date)
  - LegalConcept (properties: name, definition, area_of_law)
  - Court (properties: name, level, jurisdiction)
  - Judge (properties: name, court_history)

Edge Types:
  - CITES (case → case, with treatment: follows/distinguishes/overrules/criticizes)
  - INTERPRETS (case → statute)
  - DECIDED_BY (case → court)
  - AUTHORED_BY (case → judge)
  - RELATES_TO (case → legal_concept)
  - SUPERSEDES (statute → statute)
  - HIGHER_THAN (court → court)
```

### 7.3 Tools for Implementation

| Tool | Role | Notes |
|------|------|-------|
| **Neo4j** | Graph database | Production-ready. Built-in vector index. Cypher query language. Best for citation networks. |
| **NetworkX** | Graph analysis | Python library. Good for prototyping, PageRank computation, community detection. In-memory only. |
| **OpenSPG** | KG framework | KAG backend. Schema-driven construction. More complex but powerful. |
| **LlamaIndex PropertyGraphIndex** | RAG + KG integration | KG query engines with LLM reasoning. |

---

## 8. Evaluation Metrics

### 8.1 Retrieval Metrics

| Metric | What It Measures | Relevance |
|--------|-----------------|-----------|
| **Recall@k** | Fraction of relevant documents in top-k | Critical — measures if we're finding all relevant cases |
| **NDCG@k** | Ranking quality (position-sensitive) | Important — higher-authority cases should rank higher |
| **MRR** | Average reciprocal rank of first relevant result | Useful for single-best-case queries |
| **MAP** | Average precision across recall levels | Overall retrieval quality |
| **Hit Rate@k** | Whether at least one relevant document is in top-k | Baseline usability metric |

### 8.2 Legal-Specific Evaluation

- **Authority-weighted NDCG:** Weight the "ground truth" relevance by court level — finding the Supreme Court precedent should matter more than finding a district court case on the same issue.
- **Conflict recall:** When contradictory precedents exist, does the system surface both sides?
- **Citation coverage:** For a given legal issue, what fraction of the key precedents does the system retrieve?

### 8.3 RAG Evaluation Frameworks

| Framework | Key Metrics |
|-----------|------------|
| **RAGAS** (arXiv:2309.15217) | Faithfulness, answer relevance, context relevance, context recall |
| **DeepEval** | Similar to RAGAS + bias/toxicity |
| **ARES** (Stanford) | LLM judges with statistical guarantees |
| **LLM-as-Judge** | Use strong LLM to evaluate on custom legal dimensions |

---

## 9. Open-Source Frameworks

| Framework | Strengths for Our Use Case |
|-----------|---------------------------|
| **LlamaIndex** | `DocumentSummaryIndex` for document-level retrieval. `AutoMergingRetriever` for hierarchical. `PropertyGraphIndex` for KG. `LlamaParse` for PDF parsing. Most complete. |
| **LangChain / LangGraph** | `ParentDocumentRetriever` for hierarchical. `EnsembleRetriever` for hybrid. LangGraph for agentic workflows. |
| **Haystack** | Production-oriented pipelines. Strong evaluation. |
| **RAGFlow** | Deep document understanding. Excellent PDF/table parsing. |
| **Kotaemon** | Open-source RAG UI for document QA. Good for prototyping. |

---

## 10. Recommended Architecture for Legal Jurisprudence Retrieval

Based on the research, here is a proposed high-level architecture:

```
┌─────────────────────────────────────────────────────────────────┐
│                        INGESTION PIPELINE                       │
├─────────────────────────────────────────────────────────────────┤
│  1. Parse case law documents (PDF/MD) → extract structure       │
│  2. Extract metadata (court, date, parties, case number)        │
│  3. Structure-aware chunking (preserve legal sections)          │
│  4. Generate document summaries (LLM)                           │
│  5. Extract entities & relationships → Knowledge Graph          │
│  6. Embed at multiple levels (summary, section, paragraph)      │
│  7. Build citation network (case → case, case → statute)        │
│  8. Compute graph metrics (PageRank, court hierarchy weights)   │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                        STORAGE LAYER                            │
├─────────────────────────────────────────────────────────────────┤
│  Vector DB (embeddings at all levels) ←→ Graph DB (citations,   │
│  court hierarchy, legal concepts)  ←→ BM25 Index (keyword)     │
│  Document Store (full case law files)                           │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     RETRIEVAL PIPELINE                           │
├─────────────────────────────────────────────────────────────────┤
│  1. Query analysis (classify query type, extract legal entities) │
│  2. Hybrid retrieval (BM25 + dense embedding + graph traversal) │
│  3. Document-level score aggregation                            │
│  4. Cross-encoder re-ranking                                    │
│  5. Legal re-ranking (court hierarchy, citation weight, recency)│
│  6. Conflict detection (flag opposing decisions)                │
│  7. Return ranked full documents with metadata & conflict notes  │
└─────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Recommendation | Rationale |
|----------|---------------|-----------|
| **Embedding model** | `voyage-law-2` (commercial) or fine-tuned BGE/GTE (open-source) | Domain-specific embeddings significantly outperform general models on legal text |
| **Graph database** | Neo4j | Production-ready, built-in vector index, Cypher for citation traversal |
| **Graph approach** | HippoRAG-style PPR on citation network + KAG-style schema-constrained KG | PPR naturally fits citation importance; schema ensures clean legal ontology |
| **Chunking** | Structure-aware with hierarchical indexing + document summaries | Must preserve legal clause boundaries; multi-level retrieval enables document-level return |
| **Retrieval fusion** | RRF across BM25 + dense + graph results, then document-level aggregation | Hybrid retrieval is table stakes; document aggregation ensures whole-file return |
| **Re-ranking** | Cross-encoder (bge-reranker-v2-gemma) + legal authority scoring | Combines semantic relevance with legal authority signals |
| **Conflict handling** | Surface both sides with metadata, don't resolve | Legally correct; lawyers need to see the full picture |
| **Framework** | LlamaIndex (strongest document-level retrieval primitives) or custom | DocumentSummaryIndex + AutoMergingRetriever + PropertyGraphIndex cover our needs |

---

## 11. Key Papers & References

| Paper / Resource | Year | Topic | ArXiv / Link |
|-----------------|------|-------|--------------|
| Gao et al., "RAG for LLMs: A Survey" | 2024 | Comprehensive RAG taxonomy | arXiv:2312.10997 |
| Edge et al., "GraphRAG" | 2024 | Graph-based RAG with communities | arXiv:2404.16130 |
| KAG (Ant Group) | 2024 | Knowledge-augmented generation | arXiv:2409.13731 |
| Guo et al., "LightRAG" | 2025 | Lightweight graph RAG | arXiv:2410.05779 |
| Sarthi et al., "RAPTOR" | 2024 | Tree-structured retrieval | arXiv:2401.18059 |
| Gutiérrez et al., "HippoRAG" | 2024 | Neuroscience-inspired graph RAG | arXiv:2405.14831 |
| Yan et al., "Corrective RAG" | 2024 | Self-correcting retrieval | arXiv:2401.15884 |
| Asai et al., "Self-RAG" | 2023 | Self-reflective retrieval | arXiv:2310.11511 |
| Günther et al., "Late Chunking" | 2024 | Contextual chunk embeddings | arXiv:2409.04701 |
| Sun et al., "RankGPT" | 2024 | LLM-based reranking | arXiv:2304.09542 |
| Es et al., "RAGAS" | 2024 | RAG evaluation framework | arXiv:2309.15217 |
| Chen et al., "Dense X Retrieval" | 2024 | Proposition-based indexing | arXiv:2312.06648 |
| Guha et al., "LegalBench" | 2023 | Legal reasoning benchmark | arXiv:2308.11462 |
| Anthropic, "Contextual Retrieval" | 2024 | Hybrid retrieval best practices | anthropic.com/engineering |
| Anthropic, "Building Effective Agents" | 2024 | Agentic system design patterns | anthropic.com/research |

### Key Open-Source Repositories

| Repo | Stars | Relevance |
|------|-------|-----------|
| microsoft/graphrag | 31k+ | Graph-based RAG reference implementation |
| OpenSPG/KAG | — | Knowledge-augmented generation framework |
| HKUDS/LightRAG | — | Simpler graph RAG alternative |
| krishang118/NyayaAI | — | Most complete open legal AI with KG (154K nodes) |
| freelawproject/courtlistener | 864 | Open case law with citation tracking |
| maastrichtlawtech/case-law-explorer | — | Citation network analysis platform |
| CSHaitao/LexRAG | 35 | Legal RAG benchmark |
| TilmanLudewigtHaufe/GraphAugmented-Legal-RAG | 71 | Graph + RAG for legal texts |
| NirDiamant/RAG_Techniques | — | Comprehensive RAG techniques collection |
| explodinggradients/ragas | — | RAG evaluation framework |

---

## 12. Open Questions & Next Steps

1. **Which jurisdiction?** The court hierarchy structure, citation conventions, and available datasets vary dramatically by country. Turkish law (Yargitay/Danistay hierarchy) will require custom modeling.
2. **Citation treatment classification:** Do we need to classify citation treatments (follows/overrules/distinguishes) automatically, or can we rely on metadata if available?
3. **Embedding fine-tuning:** Should we fine-tune an open-source embedding model on our specific jurisprudence corpus, or is `voyage-law-2` sufficient?
4. **Graph construction:** Manual/semi-automatic KG construction (higher quality) vs. fully automated LLM extraction (scalable but noisy)?
5. **Evaluation dataset:** We need a gold-standard set of queries with known relevant case law documents to evaluate retrieval quality.
6. **Scale:** How many case law documents are we indexing? This affects architecture choices significantly (10K vs 1M documents).
