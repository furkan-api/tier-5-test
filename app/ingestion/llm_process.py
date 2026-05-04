#!/usr/bin/env python3
"""
LLM-based extraction of structured records from Turkish court decisions.

For each `*.md` file in the corpus directory, calls a Gemini model with the
extraction system prompt, parses the response as JSON, and writes one
`<stem>.json` next to the others in the output directory. Malformed JSON is
preserved as `<stem>.raw.txt` for inspection.

Usage:
    python -m app.ingestion.llm_process                       # all files
    python -m app.ingestion.llm_process <substring>           # filename filter
    python -m app.ingestion.llm_process --force               # overwrite existing
    python -m app.ingestion.llm_process --limit 50            # process first N matches
    python -m app.ingestion.llm_process --output-dir DIR      # override output
    python -m app.ingestion.llm_process --model MODEL_ID      # override model
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from app.core.config import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LLM extraction over corpus markdown files.",
    )
    parser.add_argument(
        "filter", nargs="?", default=None,
        help="Only process files whose name contains this substring.",
    )
    parser.add_argument("--corpus-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--system-prompt", type=Path, default=None)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N files (after filtering).")
    parser.add_argument("--force", action="store_true",
                        help="Reprocess files even when an output already exists.")
    return parser.parse_args(argv)


def select_files(
    corpus_dir: Path,
    output_dir: Path,
    needle: str | None,
    force: bool,
    limit: int | None,
) -> tuple[list[Path], list[Path]]:
    """Return (to_process, skipped_existing) for the given selection rules."""
    all_files = sorted(corpus_dir.glob("*.md"))
    if needle:
        all_files = [f for f in all_files if needle in f.name]

    to_process: list[Path] = []
    skipped: list[Path] = []
    for path in all_files:
        if not force and (output_dir / f"{path.stem}.json").exists():
            skipped.append(path)
            continue
        to_process.append(path)
        if limit is not None and len(to_process) >= limit:
            break
    return to_process, skipped


class GeminiExtractor:
    """Thin wrapper around `google.genai` with a fixed system prompt + schema."""

    def __init__(self, *, api_key: str, model: str, system_prompt: str):
        from google import genai
        from google.genai import types

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            temperature=0,
        )

    def extract(self, *, filename: str, body: str) -> str:
        user_message = f"Filename: {filename}\n\n---\n\n{body}"
        response = self._client.models.generate_content(
            model=self._model,
            contents=user_message,
            config=self._config,
        )
        return response.text or ""


def write_output(
    output_dir: Path, stem: str, payload: Any
) -> Path:
    out_path = output_dir / f"{stem}.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path


def write_raw(output_dir: Path, stem: str, raw: str) -> Path:
    raw_path = output_dir / f"{stem}.raw.txt"
    raw_path.write_text(raw, encoding="utf-8")
    return raw_path


def process_files(
    files: Iterable[Path],
    extractor: GeminiExtractor,
    output_dir: Path,
) -> dict[str, int]:
    """Run the extractor over the given files. Returns counts of outcomes."""
    stats = {"ok": 0, "invalid_json": 0, "errors": 0}
    for path in files:
        log.info("[process] %s", path.name)
        t0 = time.time()
        body = path.read_text(encoding="utf-8")
        try:
            raw = extractor.extract(filename=path.name, body=body)
        except Exception as exc:
            log.error("[error]   %s: %s", path.name, exc)
            stats["errors"] += 1
            continue

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.error("[error]   %s: invalid JSON (%s)", path.name, exc)
            write_raw(output_dir, path.stem, raw)
            stats["invalid_json"] += 1
            continue

        out_path = write_output(output_dir, path.stem, parsed)
        stats["ok"] += 1
        log.info("[ok]      %s -> %s (%.1fs)",
                 path.name, out_path.name, time.time() - t0)
    return stats


def resolve_paths(
    args: argparse.Namespace, settings: Settings
) -> tuple[Path, Path, Path]:
    corpus_dir = (args.corpus_dir or settings.corpus_dir).resolve()
    output_dir = (args.output_dir or settings.llm_extract_output_dir).resolve()
    system_prompt_path = (
        args.system_prompt or settings.llm_extract_system_prompt
    ).resolve()
    return corpus_dir, output_dir, system_prompt_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    settings = get_settings()

    if not settings.gemini_api_key:
        log.error("GEMINI_API_KEY is not set in environment / .env")
        return 1

    corpus_dir, output_dir, system_prompt_path = resolve_paths(args, settings)
    if not corpus_dir.is_dir():
        log.error("Corpus directory not found: %s", corpus_dir)
        return 1
    if not system_prompt_path.is_file():
        log.error("System prompt not found: %s", system_prompt_path)
        return 1
    output_dir.mkdir(parents=True, exist_ok=True)

    files, skipped = select_files(
        corpus_dir, output_dir, args.filter, args.force, args.limit,
    )
    log.info(
        "Corpus=%s  output=%s  to_process=%d  skipped=%d  model=%s",
        corpus_dir, output_dir, len(files), len(skipped),
        args.model or settings.llm_extract_model,
    )
    if not files:
        log.info("Nothing to do.")
        return 0

    system_prompt = system_prompt_path.read_text(encoding="utf-8")
    extractor = GeminiExtractor(
        api_key=settings.gemini_api_key,
        model=args.model or settings.llm_extract_model,
        system_prompt=system_prompt,
    )
    stats = process_files(files, extractor, output_dir)
    log.info("Done — ok=%d invalid_json=%d errors=%d skipped=%d",
             stats["ok"], stats["invalid_json"], stats["errors"], len(skipped))
    return 0 if stats["errors"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
