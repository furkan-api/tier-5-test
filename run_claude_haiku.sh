#!/bin/bash
set +e

UV=~/.local/bin/uv
OUT=eval/runs/claude-haiku
STAGES=eval/runs/claude-haiku/_stages
MODEL=claude-haiku-4-5-20251001

$UV run python -m app.ingestion.llm_process --stage metadata --model $MODEL --output-dir $OUT --intermediate-dir $STAGES --force
$UV run python -m app.ingestion.llm_process --stage summary --model $MODEL --output-dir $OUT --intermediate-dir $STAGES --force
$UV run python -m app.ingestion.llm_process --stage citations_decisions --output-dir $OUT --intermediate-dir $STAGES --model $MODEL
$UV run python -m app.ingestion.llm_process --stage citations_laws --output-dir $OUT --intermediate-dir $STAGES --model $MODEL
$UV run python -m app.ingestion.llm_process --merge-only --output-dir $OUT --intermediate-dir $STAGES --force
