#!/bin/bash
set -e

UV=~/.local/bin/uv
OUT=eval/runs/gemini-flash-lite
STAGES=eval/runs/gemini-flash-lite/_stages

$UV run python -m app.ingestion.llm_process --stage citations_decisions --output-dir $OUT --intermediate-dir $STAGES
$UV run python -m app.ingestion.llm_process --stage citations_laws --output-dir $OUT --intermediate-dir $STAGES
$UV run python -m app.ingestion.llm_process --merge-only --output-dir $OUT --intermediate-dir $STAGES
