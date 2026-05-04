# Legal Jurisprudence Retrieval System

A retrieval system for Turkish case law (içtihat). Given a legal query from a lawyer, it returns ranked whole court decision documents — not snippets, not generated answers. Think Westlaw/LexisNexis, but for Turkish law.

Production target: 50M+ documents. The local corpus (95 docs) is an evaluation fixture.

## Project Structure

```
app/                        FastAPI microservice
  core/                     Config (Pydantic Settings), DB helpers, Milvus helpers
  api/routes/               REST endpoints (POST /search, GET /health)
  ingestion/                Offline pipelines: ingest, chunk, embed, llm_process
    prompts/                Extraction system prompts (e.g. decision_extraction_v2.md)
  retrieval/                Search: dense vector search, document aggregation
  models.py                 Pydantic request/response models

eval/                       Evaluation infrastructure
  gold_standard.json        64 queries with graded relevance judgments
  corpus_manifest.json      Parsed metadata for each corpus document
  scripts/                  Eval harness, retrieval runner, llm-extraction scorer
  tests/                    Toy example with hand-computed expected values
  llm_extractions/          Default output dir for LLM extraction runs
  llm_extractions_gold/     Drop your gold-standard JSON folder here

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

# Install (uses uv — https://docs.astral.sh/uv/)
uv sync

# Ingest corpus → PostgreSQL
uv run python -m app.ingestion.ingest

# Chunk documents
uv run python -m app.ingestion.chunk

# Embed chunks → Milvus (requires OPENAI_API_KEY)
uv run python -m app.ingestion.embed

# Start API
uv run uvicorn app.main:app

# Search
curl -X POST localhost:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "iş kazası nedeniyle tazminat", "top_k": 10}'

# Run evaluation
uv run python eval/scripts/run_retrieval.py --run-id my-run
uv run python eval/scripts/evaluate.py --run-file data/runs/my-run.json --run-id my-run

# Compare two runs
uv run python eval/scripts/evaluate.py --run-id run-a --run-id run-b
```

## LLM-based Decision Extraction

`app.ingestion.llm_process` reads each corpus markdown file, sends it to an LLM with the extraction system prompt at `app/ingestion/prompts/decision_extraction_v2.md`, and writes one structured `<stem>.json` per document under `eval/llm_extractions/`. Malformed responses are preserved as `<stem>.raw.txt` for inspection. `eval/scripts/score_extractions.py` then compares any output folder against a user-supplied gold-standard folder and reports per-field, per-file, and aggregate metrics (exact / id / bool / fuzzy-text / set-F1 / matched-F1 / nested-object).

### Backends

Two backends, chosen automatically:

- **Native Gemini** (default). Uses `google.genai` against `GEMINI_API_KEY`.
- **OpenAI-compatible** — Ollama, vLLM, LM Studio, llama.cpp's server, etc. Activated whenever `LLM_EXTRACT_BASE_URL` (or `--base-url`) is set.

### Configuration (`.env`)

| Setting | Purpose |
|---------|---------|
| `LLM_EXTRACT_MODEL` | Model id (default `gemini-2.5-flash-lite`) |
| `LLM_EXTRACT_SYSTEM_PROMPT` | Path to prompt file |
| `LLM_EXTRACT_OUTPUT_DIR` | Where extractions are written |
| `LLM_EXTRACT_BASE_URL` | OpenAI-compatible endpoint, e.g. `http://localhost:11434/v1` |
| `LLM_EXTRACT_API_KEY` | Key for the endpoint (local servers usually accept any non-empty value) |
| `LLM_EXTRACT_GOLD_DIR` | Default `--gold` for the scorer |

CLI flags (`--model`, `--system-prompt`, `--output-dir`, `--base-url`, `--api-key`, `--limit`, `--force`, plus a substring `filter` positional) override `.env` per-run.

### Usage

```bash
# Gemini (default), first 50 yargıtay decisions
uv run python -m app.ingestion.llm_process yargitay --limit 50

# Local model via Ollama
uv run python -m app.ingestion.llm_process \
    --base-url http://localhost:11434/v1 \
    --api-key ollama \
    --model qwen2.5:14b \
    --output-dir eval/llm_extractions_qwen

# Score a result folder against eval/llm_extractions_gold/
uv run python eval/scripts/score_extractions.py eval/llm_extractions_qwen

# Side-by-side comparison
uv run python eval/scripts/score_extractions.py \
    eval/llm_extractions eval/llm_extractions_qwen \
    --names gemini qwen
```

Each prompt revision or model swap should land in a fresh `--output-dir` so previous runs stay available for the multi-folder comparison mode.

## Infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Metadata DB | PostgreSQL 16 | Documents, chunks, eval runs |
| Vector DB | Milvus 2.5 | Embeddings, similarity search |
| Embeddings | OpenAI text-embedding-3-small | Dense vectors (1536d) |
| API | FastAPI | Search endpoint |
| Config | Pydantic Settings | Environment-based configuration |

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (package manager)
- Docker (for PostgreSQL + Milvus)
- `OPENAI_API_KEY` and/or `GEMINI_API_KEY` (or `.env` file). For local LLM
  extraction set `LLM_EXTRACT_BASE_URL` to your Ollama / vLLM / LM Studio
  endpoint and no cloud key is needed.
