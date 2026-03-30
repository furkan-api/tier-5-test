# Experiment Results

Consolidated results from all R&D experiments. Each section corresponds to a decision that must be made before production scaling.

---

## Aggregation Strategy Comparison (Epic 3.2)

**Date:** 2026-03-26
**Config:** text-embedding-3-small, 512-token chunks, Milvus cosine, top-100 chunks → top-20 docs
**Eval corpus:** 64 queries, 95 documents

| Strategy | Recall@5 | Recall@10 | Recall@20 | NDCG@5 | NDCG@10 | MRR | Hit Rate@5 |
|----------|----------|-----------|-----------|--------|---------|-----|------------|
| **max** | 0.379 | **0.545** | 0.740 | 0.367 | **0.445** | 0.546 | **0.766** |
| mean | **0.382** | 0.510 | **0.756** | **0.378** | 0.442 | **0.569** | 0.766 |
| combsum | 0.303 | 0.522 | 0.708 | 0.252 | 0.352 | 0.427 | 0.688 |

**Decision: max_score**

**Rationale:**
- CombSUM is clearly worst — loses across every metric. Over-rewards long documents with many chunks.
- Max vs Mean is a tradeoff: max wins on Recall@10 (+0.035) and NDCG@10 (+0.003), the primary ranking quality metrics. Mean wins on MRR (+0.023) and Recall@20 (+0.016) — finds the first relevant doc faster but spreads attention more.
- Max is the better default for a system where ranking quality matters most. Mean may be worth revisiting after re-ranking is added (Tier 6).

**Run IDs:** `agg-max`, `agg-mean`, `agg-combsum` (logged in PostgreSQL)

---

## Embedding Model Comparison (Epic 3.3)

**Date:** 2026-03-30
**Config:** 512-token chunks, Milvus cosine, max agg, top-100 chunks → top-20 docs
**Eval corpus:** 67 queries, 95 documents
**All models run locally via HuggingFace TEI on NVIDIA A100.**

| Model | Dim | Recall@5 | Recall@10 | Recall@20 | NDCG@5 | NDCG@10 | MRR | Hit@5 | p50 (ms) | p95 (ms) |
|-------|-----|----------|-----------|-----------|--------|---------|-----|-------|----------|----------|
| **multilingual-e5-large-instruct** | 1024 | **0.540** | **0.660** | **0.752** | **0.668** | **0.698** | **0.798** | **0.836** | 12 | 16 |
| BAAI/bge-m3 | 1024 | 0.497 | 0.649 | 0.719 | 0.641 | 0.683 | 0.780 | 0.806 | 11 | 12 |
| intfloat/e5-large-v2 | 1024 | 0.361 | 0.444 | 0.531 | 0.449 | 0.472 | 0.596 | 0.687 | 11 | 13 |
| BAAI/bge-base-en-v1.5 | 768 | 0.244 | 0.293 | 0.396 | 0.299 | 0.321 | 0.443 | 0.552 | 9 | 11 |

**Decision: intfloat/multilingual-e5-large-instruct**

**Rationale:**
- Wins on every metric. Recall@10 +0.011 over bge-m3, NDCG@10 +0.015, MRR +0.018.
- Instruction-tuned — designed specifically for retrieval tasks.
- English-only models (e5-large-v2, bge-base-en-v1.5) perform poorly on Turkish legal text, confirming multilingual models are necessary.
- bge-base-en-v1.5 worst by far — cross-lingual transfer alone is insufficient for agglutinative Turkish legal text.
- Latency comparable across all models (~10-16ms on A100).

**Turkish synonym check (multilingual-e5-large-instruct):**
- Avg related pair cosine similarity: 0.924
- Avg unrelated pair cosine similarity: 0.887
- Separation: 0.037 (narrow but positive — legal terms have inherently high semantic overlap)

**Run IDs:** `shootout-me5li`, `shootout-bgem3`, `shootout-e5l2`, `shootout-bge15` (logged in PostgreSQL)

---

## Chunk Size Comparison (Epic 3.3)

**Date:** 2026-03-30
**Config:** intfloat/multilingual-e5-large-instruct (1024d), Milvus cosine, max agg, top-100 chunks → top-20 docs
**Eval corpus:** 67 queries, 95 documents

| Chunk Size | Chunks | Recall@5 | Recall@10 | Recall@20 | NDCG@5 | NDCG@10 | MRR | Hit@5 |
|------------|--------|----------|-----------|-----------|--------|---------|-----|-------|
| **1024** | 562 | **0.565** | **0.662** | **0.758** | **0.685** | **0.711** | **0.805** | 0.821 |
| 512 | 1143 | 0.540 | 0.660 | 0.752 | 0.668 | 0.698 | 0.798 | **0.836** |
| 256 | 2495 | 0.517 | 0.654 | 0.734 | 0.666 | 0.702 | 0.804 | **0.836** |

**Decision: 1024 tokens**

**Rationale:**
- Wins on Recall@5, Recall@10, Recall@20, NDCG@5, NDCG@10, and MRR.
- 512 and 256 have slightly better Hit Rate@5 (+0.015) but worse ranking quality.
- 1024-token chunks capture more context per chunk (Turkish legal decisions have long reasoning sections). Larger chunks let the embedding model see more of the legal argument in a single vector.
- Half the storage of 512 (562 vs 1143 vectors) — better for scaling to 50M+ docs.
- Legal documents benefit from larger context windows: key holdings span multiple paragraphs.

**Run IDs:** `chunksize-256`, `chunksize-512`, `chunksize-1024` (logged in PostgreSQL)

---

## LLM Quality: Summary Generation (Tier 5 preview)

*Pending*

---

## LLM Quality: Holding Extraction (Tier 9 preview)

*Pending*

---

## LLM Quality: Citation Treatment Classification (Tier 10 preview)

*Pending*

---

## H100 Throughput Benchmarks

*Pending*

---

## Cumulative Metrics Table

Every configuration tested, in chronological order. This is the single source of truth for all architectural decisions.

| Run ID | Date | Config | Recall@5 | Recall@10 | Recall@20 | NDCG@5 | NDCG@10 | MRR | Hit Rate@5 |
|--------|------|--------|----------|-----------|-----------|--------|---------|-----|------------|
| embed-v1-milvus | 2026-03-26 | text-embedding-3-small, 512tok, Milvus, max agg | 0.379 | 0.545 | 0.750 | 0.367 | 0.446 | 0.549 | 0.781 |
| agg-max | 2026-03-26 | same, max aggregation | 0.379 | 0.545 | 0.740 | 0.367 | 0.445 | 0.546 | 0.766 |
| agg-mean | 2026-03-26 | same, mean aggregation | 0.382 | 0.510 | 0.756 | 0.378 | 0.442 | 0.569 | 0.766 |
| agg-combsum | 2026-03-26 | same, combsum aggregation | 0.303 | 0.522 | 0.708 | 0.252 | 0.352 | 0.427 | 0.688 |
| shootout-bgem3 | 2026-03-30 | BAAI/bge-m3, 512tok, max agg | 0.497 | 0.649 | 0.719 | 0.641 | 0.683 | 0.780 | 0.806 |
| shootout-me5li | 2026-03-30 | multilingual-e5-large-instruct, 512tok, max agg | 0.540 | 0.660 | 0.752 | 0.668 | 0.698 | 0.798 | 0.836 |
| shootout-e5l2 | 2026-03-30 | e5-large-v2, 512tok, max agg | 0.361 | 0.444 | 0.531 | 0.449 | 0.472 | 0.596 | 0.687 |
| shootout-bge15 | 2026-03-30 | bge-base-en-v1.5, 512tok, max agg | 0.244 | 0.293 | 0.396 | 0.299 | 0.321 | 0.443 | 0.552 |
| chunksize-256 | 2026-03-30 | me5li, 256tok, max agg | 0.517 | 0.654 | 0.734 | 0.666 | 0.702 | 0.804 | 0.836 |
| chunksize-512 | 2026-03-30 | me5li, 512tok, max agg | 0.540 | 0.660 | 0.752 | 0.668 | 0.698 | 0.798 | 0.836 |
| chunksize-1024 | 2026-03-30 | me5li, 1024tok, max agg | 0.565 | 0.662 | 0.758 | 0.685 | 0.711 | 0.805 | 0.821 |
