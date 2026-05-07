#!/bin/bash
set -e

UV=~/.local/bin/uv
OUT=eval/runs/claude-3-haiku
STAGES=eval/runs/claude-3-haiku/_stages
MODEL=claude-3-haiku-20240307

$UV run python -m app.ingestion.llm_process --stage metadata --model $MODEL --output-dir $OUT --intermediate-dir $STAGES
$UV run python -m app.ingestion.llm_process --stage summary --model $MODEL --output-dir $OUT --intermediate-dir $STAGES
$UV run python -m app.ingestion.llm_process --stage citations_decisions --model $MODEL --output-dir $OUT --intermediate-dir $STAGES
$UV run python -m app.ingestion.llm_process --stage citations_laws --model $MODEL --output-dir $OUT --intermediate-dir $STAGES
$UV run python -m app.ingestion.llm_process --merge-only --output-dir $OUT --intermediate-dir $STAGES
