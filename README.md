# Legal Jurisprudence Retrieval System

A retrieval system for Turkish case law (içtihat). Given a legal query from a lawyer, it returns ranked whole court decision documents — not snippets, not generated answers. Think Westlaw/LexisNexis, but for Turkish law.

Production target: 50M+ documents. The local corpus (95 docs) is an evaluation fixture.

## Project Structure

```
app/                        FastAPI microservice
  core/                     Config (Pydantic Settings), DB helpers, Milvus helpers
  api/routes/               REST endpoints (POST /search, GET /health)
  ingestion/                Offline pipelines: ingest, chunk, embed
  retrieval/                Search: dense vector search, document aggregation
  models.py                 Pydantic request/response models

eval/                       Evaluation infrastructure
  gold_standard.json        64 queries with graded relevance judgments
  corpus_manifest.json      Parsed metadata for each corpus document
  scripts/                  Eval harness, batch retrieval runner, validation
  tests/                    Toy example with hand-computed expected values

corpus/                     95 court decisions (eval fixture)
                            Covers: Yargıtay (Hukuk + Ceza daireleri, HGK, İBK),
                            Danıştay, BAM, BİM, İlk Derece, AYM
                            Period: 1974–2026

docs/
  implementation-plan.md    13-tier implementation roadmap with acceptance criteria
  research.md               SOTA research: RAG, GraphRAG, KAG, legal AI, embeddings
  turkish-law-reference.md  Turkish court hierarchy, citation formats, legal codes
```

## Current Status

**Tier 1 — Measurement Infrastructure** (complete)
- Gold standard dataset: 64 queries, 95 docs, graded relevance (0-3)
- Evaluation harness: Recall@K, NDCG@K, MRR, Hit Rate, run comparison
- Second lawyer validation of 20+ queries: pending

**Tier 2 — Data Pipeline** (complete)
- Document ingestion: corpus → PostgreSQL with metadata
- Fixed-size chunking: 512 tokens, 50 overlap, document back-pointers

**Tier 3 — Naive Dense Retrieval** (Epic 3.1 complete)
- Embedding: text-embedding-3-small (OpenAI, 1536d) → Milvus
- Vector search: cosine similarity, IVF_FLAT index
- Document aggregation: max chunk score per document
- REST API: FastAPI with search and health endpoints

### Baseline Metrics (Epic 3.1)

| Metric | Value |
|--------|-------|
| Recall@5 | 0.38 |
| Recall@10 | 0.55 |
| Recall@20 | 0.74 |
| NDCG@5 | 0.37 |
| NDCG@10 | 0.45 |
| MRR | 0.55 |
| Hit Rate@5 | 0.78 |

**Next:** Epic 3.2 (aggregation strategy comparison), Epic 3.3 (embedding model shootout)

## Quick Start

```bash
# Start PostgreSQL + Milvus
docker compose up -d

# Install
pip install -e .

# Ingest corpus → PostgreSQL
python3 -m app.ingestion.ingest

# Chunk documents
python3 -m app.ingestion.chunk

# Embed chunks → Milvus (requires OPENAI_API_KEY)
python3 -m app.ingestion.embed

# Start API
python3 -m uvicorn app.main:app

# Search
curl -X POST localhost:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "iş kazası nedeniyle tazminat", "top_k": 10}'

# Run evaluation
python3 eval/scripts/run_retrieval.py --run-id my-run
python3 eval/scripts/evaluate.py --run-file data/runs/my-run.json --run-id my-run

# Compare two runs
python3 eval/scripts/evaluate.py --run-id run-a --run-id run-b
```

## Infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Metadata DB | PostgreSQL 16 | Documents, chunks, eval runs |
| Vector DB | Milvus 2.5 | Embeddings, similarity search |
| Embeddings | OpenAI text-embedding-3-small | Dense vectors (1536d) |
| API | FastAPI | Search endpoint |
| Config | Pydantic Settings | Environment-based configuration |

## Requirements

- Python 3.9+
- Docker (for PostgreSQL + Milvus)
- `OPENAI_API_KEY` environment variable (or `.env` file)
