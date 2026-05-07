#!/bin/bash
set +e

UV=~/.local/bin/uv
OUT=eval/runs/gemini-flash
STAGES=eval/runs/gemini-flash/_stages

$UV run python -m app.ingestion.llm_process --stage metadata --output-dir $OUT --intermediate-dir $STAGES --model gemini-2.5-flash
$UV run python -m app.ingestion.llm_process --stage summary --output-dir $OUT --intermediate-dir $STAGES --model gemini-2.5-flash
$UV run python -m app.ingestion.llm_process --stage citations_decisions --output-dir $OUT --intermediate-dir $STAGES --model gemini-2.5-flash
$UV run python -m app.ingestion.llm_process --stage citations_laws --output-dir $OUT --intermediate-dir $STAGES --model gemini-2.5-flash
$UV run python -m app.ingestion.llm_process --merge-only --output-dir $OUT --intermediate-dir $STAGES
