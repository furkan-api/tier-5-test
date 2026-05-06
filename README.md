# Legal Jurisprudence Retrieval System

A retrieval system for Turkish case law (içtihat). Given a legal query from a lawyer, it returns ranked whole court decision documents — not snippets, not generated answers. Think Westlaw/LexisNexis, but for Turkish law.

Production target: 50M+ documents. The local corpus (95 docs) is an evaluation fixture.

## Project Structure

```
app/                        FastAPI microservice
  core/                     Config (Pydantic Settings), DB helpers, Milvus helpers
  api/routes/               REST endpoints (POST /search, GET /health)
  ingestion/                Offline pipelines: ingest, chunk, embed, llm_process, verify_citations
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
                            Danıştay, BAM, BİM, İlk Derece, AYM,
                            Uyuşmazlık Mahkemesi, AİHM
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

`app.ingestion.llm_process` is a **staged extraction pipeline**: each corpus markdown file is processed by 4 independent stages, each with its own system prompt and (optionally) its own model / endpoint. Splitting the work this way bounds per-call output volume — important when targeting smaller models with low output token caps — and lets you re-run any single stage in isolation when iterating on its prompt.

| Stage | Prompt | Produces |
|-------|--------|----------|
| `metadata` | `extract_metadata.md` | court_type, court, case/decision/date, decision_type, is_final, finality_basis, decision_outcome, decision_outcome_raw, vote_unanimity, has_dissent, dissent_summary, appellants, appeal_outcomes_by_role, subject, keywords, legal_issues, legal_concepts, dispositive_reasoning, fact_pattern |
| `summary` | `extract_summary.md` | summary |
| `citations_decisions` | `extract_cited_court_decisions.md` | cited_court_decisions |
| `citations_laws` | `extract_cited_law_articles.md` | cited_law_articles |

Each stage writes a per-document intermediate JSON to `eval/llm_extractions/_stages/` (e.g. `<stem>.metadata.json`, `<stem>.summary.json`). A merge step combines the four into the canonical `<stem>.json` under `eval/llm_extractions/`. Intermediates are kept on disk after merge so any field in the final JSON can be traced back to the prompt + model that produced it. Malformed or truncated responses are preserved as `<stem>.<stage>.json.raw.txt` for inspection. `eval/scripts/score_extractions.py` then compares any output folder against a user-supplied gold-standard folder and reports per-field, per-file, and aggregate metrics (exact / id / bool / fuzzy-text / set-F1 / matched-F1 / nested-object).

The original single-pass prompt (`decision_extraction_v2.md`) stays in the repo for A/B comparison but is no longer used by the runner.

### Backends

Two backends, chosen automatically:

- **Native Gemini** (default). Uses `google.genai` against `GEMINI_API_KEY`.
- **OpenAI-compatible** — Ollama, vLLM, LM Studio, llama.cpp's server, etc. Activated whenever `LLM_EXTRACT_BASE_URL` (or `--base-url`) is set.

### Configuration (`.env`)

Global defaults — apply to every stage unless overridden:

| Setting | Purpose |
|---------|---------|
| `LLM_EXTRACT_MODEL` | Default model for every stage (default `gemini-2.5-flash-lite`) |
| `LLM_EXTRACT_BASE_URL` | Default OpenAI-compatible endpoint, e.g. `http://localhost:11434/v1` |
| `LLM_EXTRACT_API_KEY` | Default key for the endpoint |
| `LLM_EXTRACT_OUTPUT_DIR` | Where merged extractions are written |
| `LLM_STAGES_INTERMEDIATE_DIR` | Where per-stage intermediates live (default `<output_dir>/_stages`) |
| `LLM_EXTRACT_GOLD_DIR` | Default `--gold` for the scorer |

Per-stage overrides — set any of these to use a different model/backend for one stage. Unset values fall back to the global defaults above:

| Setting | Purpose |
|---------|---------|
| `LLM_STAGE_METADATA_{MODEL,BASE_URL,API_KEY,PROMPT}` | Override the metadata stage |
| `LLM_STAGE_SUMMARY_{MODEL,BASE_URL,API_KEY,PROMPT}` | Override the summary stage |
| `LLM_STAGE_CITATIONS_DECISIONS_{MODEL,BASE_URL,API_KEY,PROMPT}` | Override the cited-court-decisions stage |
| `LLM_STAGE_CITATIONS_LAWS_{MODEL,BASE_URL,API_KEY,PROMPT}` | Override the cited-law-articles stage |

CLI flags (`--stage`, `--model`, `--base-url`, `--api-key`, `--corpus-dir`, `--output-dir`, `--intermediate-dir`, `--limit`, `--force`, `--merge-only`, `--no-merge`, plus a substring `filter` positional). When running all 4 stages at once, configure each via env (the CLI `--model`/`--base-url`/`--api-key` only apply when paired with `--stage <name>`).

### Usage

```bash
# Default: run all 4 stages with the global model, then merge
uv run python -m app.ingestion.llm_process

# Filter by filename substring; first 50 yargıtay decisions
uv run python -m app.ingestion.llm_process yargitay --limit 50

# Re-run only one stage (e.g. after improving its prompt) with a different model
uv run python -m app.ingestion.llm_process --stage summary --model gemini-2.5-pro

# Run only the law-citations stage on a local Ollama model
uv run python -m app.ingestion.llm_process \
    --stage citations_laws \
    --base-url http://localhost:11434/v1 \
    --api-key ollama \
    --model qwen2.5:14b

# Re-merge intermediates after fixing a stage (no extraction calls)
uv run python -m app.ingestion.llm_process --merge-only

# Mix-and-match per-stage models via env (no CLI override at all)
LLM_STAGE_METADATA_MODEL=gemini-2.5-pro \
LLM_STAGE_SUMMARY_MODEL=gemini-2.5-flash \
LLM_STAGE_CITATIONS_DECISIONS_MODEL=qwen2.5:14b \
LLM_STAGE_CITATIONS_DECISIONS_BASE_URL=http://localhost:11434/v1 \
LLM_STAGE_CITATIONS_DECISIONS_API_KEY=ollama \
LLM_STAGE_CITATIONS_LAWS_MODEL=qwen2.5:14b \
LLM_STAGE_CITATIONS_LAWS_BASE_URL=http://localhost:11434/v1 \
LLM_STAGE_CITATIONS_LAWS_API_KEY=ollama \
uv run python -m app.ingestion.llm_process

# Score a result folder against eval/llm_extractions_gold/
uv run python eval/scripts/score_extractions.py eval/llm_extractions

# Side-by-side comparison
uv run python eval/scripts/score_extractions.py \
    eval/llm_extractions eval/llm_extractions_qwen \
    --names gemini qwen
```

Each prompt revision or model swap should land in a fresh `--output-dir` so previous runs stay available for the multi-folder comparison mode.

### End-to-end recipe (extract → verify → score → test)

Canonical sequence after `corpus/` and `eval/llm_extractions_gold/` are populated:

```bash
# 1. Stage all 4 extractions and merge into eval/llm_extractions/<stem>.json
#    (skip with --merge-only if intermediates already exist).
uv run python -m app.ingestion.llm_process

# 2. Audit citations against the source markdowns. Writes
#    eval/llm_extractions/<stem>.verification.json next to each JSON and
#    logs which files contain hallucinations.
uv run python -m app.ingestion.verify_citations

# 3. Score the merged outputs against the gold standard. Writes
#    eval/scores/llm_extractions_score.json and .txt (gitignored).
uv run python eval/scripts/score_extractions.py eval/llm_extractions

# 4. Run the unit tests (staged-pipeline invariants, scorer compatibility,
#    citation-verifier behavior).
uv run python -m unittest app.tests.test_llm_process app.tests.test_verify_citations
```

If a stage hits transient API errors (e.g. Gemini 503), re-run step 1 **without** `--force` — the runner skips docs that already have an intermediate and only retries the missing ones.

### Citation hallucination check

`app.ingestion.verify_citations` audits each `<stem>.json` produced by `llm_process` against the matching `<stem>.md` in the corpus, flagging any `cited_court_decisions` or `cited_law_articles` entry that cannot be traced back to the source. Court decisions verify when their `case_number` or `decision_number` appears in the source; law articles verify when the `law_number`, a known abbreviation (`TBK`, `HMK`, `İYUK`, …), or the full law name appears. Per-file audits are written next to the JSONs as `<stem>.verification.json`.

```bash
# Audit the default extraction directory
uv run python -m app.ingestion.verify_citations

# Audit a different run, write an aggregate summary
uv run python -m app.ingestion.verify_citations \
    --extraction-dir eval/llm_extractions_qwen \
    --summary eval/llm_extractions_qwen/_summary.json

# Strict mode: rewrite each JSON to drop hallucinated citations.
# Originals are preserved as <stem>.unverified.json.
uv run python -m app.ingestion.verify_citations --strict
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

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (package manager)
- Docker (for PostgreSQL + Milvus)
- `OPENAI_API_KEY` and/or `GEMINI_API_KEY` (or `.env` file). For local LLM
  extraction set `LLM_EXTRACT_BASE_URL` to your Ollama / vLLM / LM Studio
  endpoint and no cloud key is needed.
