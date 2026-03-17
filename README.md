# Legal Jurisprudence Retrieval System

A retrieval system for Turkish case law (içtihat). Given a legal query from a lawyer, it returns ranked whole court decision documents — not snippets, not generated answers. Think Westlaw/LexisNexis, but for Turkish law.

This is a research project following a measurement-first approach: we build evaluation infrastructure before any retrieval system, so we can measure whether each approach actually works.

## Project Structure

```
corpus/                 95 court decisions in markdown format
                        Covers: Yargıtay (Hukuk + Ceza daireleri, HGK, İBK),
                        Danıştay, BAM, BİM, İlk Derece, AYM
                        Period: 1974–2026
                        Filename convention: {daire}-e-{esas}-k-{karar}-t-{tarih}-1.md

docs/
  implementation-plan.md      7-tier implementation roadmap, each tier with acceptance criteria
  research.md                 SOTA research: RAG, GraphRAG, KAG, legal AI, embedding models
  turkish-law-reference.md    Turkish court hierarchy, citation formats, legal codes (LLM cheat sheet)

eval/
  gold_standard.json          64 queries with graded relevance judgments (the eval dataset)
  corpus_manifest.json        Parsed metadata for each corpus document
  scripts/                    Evaluation harness, validation, build tools
  tests/                      Toy example with hand-computed expected values
  See eval/README.md for full documentation.
```

## Current Status

**Tier 1 — Measurement Infrastructure** (current)

Build the tools to know whether retrieval works, before building retrieval.

| Component | Status |
|-----------|--------|
| Gold standard dataset (64 queries, 95 docs) | Done |
| Evaluation harness (Recall@K, NDCG@K, MRR, Hit Rate) | Done |
| Run comparison mode (metric deltas + per-query wins/losses) | Done |
| Second lawyer validation of 20+ queries | Pending |

**Tiers 2–7** are not started: data pipeline, indexing, hybrid retrieval, embedding model selection, re-ranking, knowledge graphs, production API/UI. See `docs/implementation-plan.md` for the full roadmap.

## Quick Start

```bash
# Validate the evaluation dataset
python3 eval/scripts/validate_schema.py

# Run evaluation harness tests
python3 eval/scripts/test_evaluate.py

# Evaluate a retrieval run
python3 eval/scripts/evaluate.py --run-file path/to/run.json

# Compare two runs
python3 eval/scripts/evaluate.py --run-id run-a --run-id run-b
```

## Requirements

Python 3.10+, standard library only (no external dependencies).
