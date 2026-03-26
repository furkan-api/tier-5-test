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

*Pending*

---

## Chunk Size Comparison (Epic 3.3)

*Pending*

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
