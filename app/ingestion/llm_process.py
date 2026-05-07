#!/usr/bin/env python3
"""
Staged LLM extraction of structured records from Turkish court decisions.

Each `*.md` file in the corpus is processed in 4 independent stages, each
with its own system prompt and (optionally) its own model / endpoint:

  1. metadata             — court info, outcome, IRAC, fact pattern, concepts
  2. summary              — 2-5 Turkish sentence narrative
  3. citations_decisions  — `cited_court_decisions[]`
  4. citations_laws       — `cited_law_articles[]`

Each stage writes a per-document intermediate JSON to the staging
directory (default `eval/llm_extractions/_stages/`). The merge step
combines the four intermediates into the canonical `<stem>.json` in the
output directory. Intermediates are preserved on disk after merge so a
suspect field can always be traced back to the stage (and prompt + model)
that produced it.

Two backends, chosen automatically per stage:
  * If the stage has a `base_url` configured, an OpenAI-compatible client
    is used (Ollama, vLLM, LM Studio, llama.cpp's server, Gemini's
    /v1beta/openai/ endpoint, etc.).
  * Otherwise the native `google.genai` SDK is used against `gemini_api_key`.

CLI:
    python -m app.ingestion.llm_process                       # all stages + merge
    python -m app.ingestion.llm_process <substring>           # filename filter
    python -m app.ingestion.llm_process --stage metadata      # single stage
    python -m app.ingestion.llm_process --merge-only          # re-merge existing
    python -m app.ingestion.llm_process --no-merge            # stages only
    python -m app.ingestion.llm_process --stage summary --model gemini-2.5-pro
    python -m app.ingestion.llm_process --stage citations_laws \\
        --base-url http://localhost:11434/v1 --model qwen2.5:14b
    python -m app.ingestion.llm_process --force               # ignore existing
    python -m app.ingestion.llm_process --limit 50            # first N matches
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, NamedTuple

from app.core.config import Settings, get_settings


class ExtractResult(NamedTuple):
    """Return type for extractor backends.

    `truncated` is True when the model stopped because it ran out of output
    tokens (Gemini `MAX_TOKENS`, OpenAI `length`). Truncation usually
    yields invalid JSON — surfacing it as its own signal makes the failure
    actionable instead of being lumped into `invalid_json`.
    """
    text: str
    truncated: bool


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Stage:
    """One extraction stage.

    `output_keys` are the keys this stage's response is expected to carry
    (excluding `file`, which every stage echoes for auditing). They drive
    the merge step: when combining intermediates, keys outside this set
    are silently dropped, so an over-eager model that emits other fields
    cannot pollute the merged result.
    """
    name: str
    prompt_attr: str         # name of the Settings attribute holding the prompt path
    model_attr: str          # name of the Settings attribute holding the per-stage model override (str | None)
    base_url_attr: str
    api_key_attr: str
    intermediate_suffix: str  # e.g. ".metadata.json"
    output_keys: tuple[str, ...]


STAGES: tuple[Stage, ...] = (
    Stage(
        name="metadata",
        prompt_attr="llm_stage_metadata_prompt",
        model_attr="llm_stage_metadata_model",
        base_url_attr="llm_stage_metadata_base_url",
        api_key_attr="llm_stage_metadata_api_key",
        intermediate_suffix=".metadata.json",
        output_keys=(
            "court_type", "court", "case_number", "decision_number",
            "decision_date", "decision_type", "is_final", "finality_basis",
            "decision_outcome", "decision_outcome_raw", "vote_unanimity",
            "has_dissent", "dissent_summary", "appellants",
            "appeal_outcomes_by_role", "subject", "keywords", "legal_issues",
            "legal_concepts", "dispositive_reasoning", "fact_pattern",
        ),
    ),
    Stage(
        name="summary",
        prompt_attr="llm_stage_summary_prompt",
        model_attr="llm_stage_summary_model",
        base_url_attr="llm_stage_summary_base_url",
        api_key_attr="llm_stage_summary_api_key",
        intermediate_suffix=".summary.json",
        output_keys=("summary",),
    ),
    Stage(
        name="citations_decisions",
        prompt_attr="llm_stage_citations_decisions_prompt",
        model_attr="llm_stage_citations_decisions_model",
        base_url_attr="llm_stage_citations_decisions_base_url",
        api_key_attr="llm_stage_citations_decisions_api_key",
        intermediate_suffix=".citations_decisions.json",
        output_keys=("cited_court_decisions",),
    ),
    Stage(
        name="citations_laws",
        prompt_attr="llm_stage_citations_laws_prompt",
        model_attr="llm_stage_citations_laws_model",
        base_url_attr="llm_stage_citations_laws_base_url",
        api_key_attr="llm_stage_citations_laws_api_key",
        intermediate_suffix=".citations_laws.json",
        output_keys=("cited_law_articles",),
    ),
)

STAGE_NAMES = tuple(s.name for s in STAGES)


def get_stage(name: str) -> Stage:
    for s in STAGES:
        if s.name == name:
            return s
    raise ValueError(f"unknown stage {name!r}; expected one of {STAGE_NAMES}")


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------
def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run staged LLM extraction over corpus markdown files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "filter", nargs="?", default=None,
        help="Only process files whose name contains this substring.",
    )
    parser.add_argument("--corpus-dir", type=Path, default=None)
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Where merged `<stem>.json` files land. Defaults to "
             "settings.llm_extract_output_dir.",
    )
    parser.add_argument(
        "--intermediate-dir", type=Path, default=None,
        help="Where per-stage `<stem>.<stage>.json` files land. Defaults "
             "to settings.llm_stages_intermediate_dir.",
    )
    parser.add_argument(
        "--stage", choices=STAGE_NAMES, default=None,
        help="Run a single stage instead of all four. Without this flag, "
             "all stages run in order followed by a merge.",
    )
    parser.add_argument(
        "--merge-only", action="store_true",
        help="Skip extraction; just merge existing intermediates into "
             "`<stem>.json`. Useful after fixing a single stage.",
    )
    parser.add_argument(
        "--no-merge", action="store_true",
        help="Run extraction stages but skip the merge step.",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Override the model for the selected stage. Only meaningful "
             "with --stage; ignored when running all stages (use the "
             "per-stage env vars LLM_STAGE_<NAME>_MODEL instead).",
    )
    parser.add_argument(
        "--base-url", type=str, default=None,
        help="Override the OpenAI-compatible endpoint for the selected stage.",
    )
    parser.add_argument(
        "--api-key", type=str, default=None,
        help="Override the API key for the selected stage.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N files per stage (after filtering).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Reprocess files even when an intermediate already exists.",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# File selection
# ---------------------------------------------------------------------------
def select_files(
    corpus_dir: Path,
    intermediate_dir: Path,
    stage: Stage,
    needle: str | None,
    force: bool,
    limit: int | None,
) -> tuple[list[Path], list[Path]]:
    """Return (to_process, skipped_existing) for one stage."""
    all_files = sorted(corpus_dir.glob("*.md"))
    if needle:
        all_files = [f for f in all_files if needle in f.name]

    to_process: list[Path] = []
    skipped: list[Path] = []
    for path in all_files:
        intermediate = intermediate_dir / f"{path.stem}{stage.intermediate_suffix}"
        if not force and intermediate.exists():
            skipped.append(path)
            continue
        to_process.append(path)
        if limit is not None and len(to_process) >= limit:
            break
    return to_process, skipped


# ---------------------------------------------------------------------------
# Extractor backends
# ---------------------------------------------------------------------------
class GeminiExtractor:
    """Thin wrapper around `google.genai` with a fixed system prompt."""

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

    def extract(self, *, filename: str, body: str) -> ExtractResult:
        user_message = f"Filename: {filename}\n\n---\n\n{body}"
        response = self._client.models.generate_content(
            model=self._model,
            contents=user_message,
            config=self._config,
        )
        truncated = False
        try:
            reason = response.candidates[0].finish_reason
            # `finish_reason` is an enum on the SDK; compare by .name to
            # stay version-agnostic. Falls back to str(reason).
            name = getattr(reason, "name", None) or str(reason)
            truncated = name.upper() == "MAX_TOKENS"
        except (AttributeError, IndexError, TypeError):
            pass
        return ExtractResult(text=response.text or "", truncated=truncated)


class OpenAICompatibleExtractor:
    """OpenAI-compatible chat-completions client. Works with Ollama, vLLM,
    LM Studio, llama.cpp server, and any other server exposing the
    `/v1/chat/completions` interface."""

    def __init__(self, *, api_key: str, base_url: str, model: str,
                 system_prompt: str):
        from openai import OpenAI

        # Local servers often require *some* key; fall back to a placeholder.
        self._client = OpenAI(api_key=api_key or "not-needed", base_url=base_url)
        self._model = model
        self._system_prompt = system_prompt

    def extract(self, *, filename: str, body: str) -> ExtractResult:
        user_message = f"Filename: {filename}\n\n---\n\n{body}"
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        choice = response.choices[0]
        truncated = (getattr(choice, "finish_reason", None) == "length")
        return ExtractResult(text=choice.message.content or "", truncated=truncated)


class AnthropicExtractor:
    """Anthropic Claude client via the `anthropic` SDK."""

    def __init__(self, *, api_key: str, model: str, system_prompt: str):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._system_prompt = system_prompt

    def extract(self, *, filename: str, body: str) -> ExtractResult:
        user_message = f"Filename: {filename}\n\n---\n\n{body}"
        response = self._client.messages.create(
            model=self._model,
            max_tokens=8096,
            temperature=0,
            system=self._system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        truncated = (response.stop_reason == "max_tokens")
        text = response.content[0].text if response.content else ""
        # Strip markdown code fences Claude sometimes adds despite instructions
        text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text.strip())
        return ExtractResult(text=text, truncated=truncated)


# ---------------------------------------------------------------------------
# Per-stage configuration resolution
# ---------------------------------------------------------------------------
def _stage_setting(settings: Settings, stage: Stage, attr: str,
                   global_attr: str) -> Any:
    """Return the per-stage value if set, else the global fallback."""
    val = getattr(settings, attr)
    if val in (None, ""):
        return getattr(settings, global_attr)
    return val


def resolve_stage_config(
    stage: Stage, args: argparse.Namespace, settings: Settings,
) -> dict[str, Any]:
    """Resolve the (model, base_url, api_key, prompt_path) tuple for a stage.

    Precedence: CLI override > per-stage setting > global llm_extract_* setting.
    CLI overrides only apply when the user is running this single stage —
    otherwise we ignore them, since "--model X" is ambiguous when 4
    different stages might want different models.
    """
    cli_applies = (args.stage == stage.name)

    model = (args.model if cli_applies and args.model else None) \
        or _stage_setting(settings, stage, stage.model_attr, "llm_extract_model")
    base_url = (args.base_url if cli_applies and args.base_url else None) \
        or _stage_setting(settings, stage, stage.base_url_attr, "llm_extract_base_url")
    api_key = (args.api_key if cli_applies and args.api_key else None) \
        or _stage_setting(settings, stage, stage.api_key_attr, "llm_extract_api_key")

    prompt_path = getattr(settings, stage.prompt_attr)

    return {
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "prompt_path": prompt_path,
    }


def build_extractor(stage: Stage, cfg: dict[str, Any], settings: Settings,
                    system_prompt: str):
    """Choose backend: OpenAI-compat (base_url set) > Anthropic > Gemini."""
    if cfg["base_url"]:
        api_key = cfg["api_key"] or settings.gemini_api_key
        log.info("[%s] OpenAI-compatible backend: %s (model=%s)",
                 stage.name, cfg["base_url"], cfg["model"])
        return OpenAICompatibleExtractor(
            api_key=api_key, base_url=cfg["base_url"],
            model=cfg["model"], system_prompt=system_prompt,
        )
    api_key = cfg["api_key"] or ""
    if (api_key or settings.anthropic_api_key) and (cfg["model"] or "").startswith("claude"):
        key = api_key if api_key else settings.anthropic_api_key
        log.info("[%s] Anthropic backend (model=%s)", stage.name, cfg["model"])
        return AnthropicExtractor(
            api_key=key, model=cfg["model"], system_prompt=system_prompt,
        )
    if not settings.gemini_api_key:
        raise RuntimeError(
            f"[{stage.name}] No backend configured — set GEMINI_API_KEY, "
            "ANTHROPIC_API_KEY with a claude-* model, or --base-url."
        )
    log.info("[%s] Native Gemini backend (model=%s)", stage.name, cfg["model"])
    return GeminiExtractor(
        api_key=settings.gemini_api_key,
        model=cfg["model"], system_prompt=system_prompt,
    )


# ---------------------------------------------------------------------------
# Per-stage extraction loop
# ---------------------------------------------------------------------------
def _intermediate_path(intermediate_dir: Path, stem: str, stage: Stage) -> Path:
    return intermediate_dir / f"{stem}{stage.intermediate_suffix}"


def write_intermediate(intermediate_dir: Path, stem: str, stage: Stage,
                       payload: Any) -> Path:
    out_path = _intermediate_path(intermediate_dir, stem, stage)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path


def write_raw(intermediate_dir: Path, stem: str, stage: Stage, raw: str) -> Path:
    raw_path = intermediate_dir / f"{stem}{stage.intermediate_suffix}.raw.txt"
    raw_path.write_text(raw, encoding="utf-8")
    return raw_path


def process_stage(
    stage: Stage,
    files: Iterable[Path],
    extractor,
    intermediate_dir: Path,
) -> dict[str, int]:
    """Run one stage over many files. Outcomes mirror the legacy contract.

    Buckets are mutually exclusive:
      * ok            — JSON parsed cleanly, intermediate written
      * truncated     — model hit max_output_tokens; raw saved alongside
      * invalid_json  — model returned malformed JSON (not a length cut-off)
      * errors        — exception during the extract call
    """
    stats = {"ok": 0, "truncated": 0, "invalid_json": 0, "errors": 0}
    for path in files:
        log.info("[%s] [process] %s", stage.name, path.name)
        t0 = time.time()
        body = path.read_text(encoding="utf-8")
        try:
            result = extractor.extract(filename=path.name, body=body)
        except Exception as exc:
            log.error("[%s] [error]   %s: %s", stage.name, path.name, exc)
            stats["errors"] += 1
            continue

        # Backwards compat: older fakes may still return a bare string.
        if isinstance(result, ExtractResult):
            raw, truncated = result.text, result.truncated
        else:
            raw, truncated = result, False

        if truncated:
            log.error("[%s] [truncated] %s: model hit max_output_tokens "
                      "(%d chars produced) — saving raw output",
                      stage.name, path.name, len(raw))
            write_raw(intermediate_dir, path.stem, stage, raw)
            stats["truncated"] += 1
            continue

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.error("[%s] [error]   %s: invalid JSON (%s)",
                      stage.name, path.name, exc)
            write_raw(intermediate_dir, path.stem, stage, raw)
            stats["invalid_json"] += 1
            continue

        out_path = write_intermediate(intermediate_dir, path.stem, stage, parsed)
        stats["ok"] += 1
        log.info("[%s] [ok]      %s -> %s (%.1fs)",
                 stage.name, path.name, out_path.name, time.time() - t0)
    return stats


# ---------------------------------------------------------------------------
# Backwards-compat wrapper: legacy single-pass `process_files`
# ---------------------------------------------------------------------------
def write_output(output_dir: Path, stem: str, payload: Any) -> Path:
    """Write `<stem>.json` to output_dir. Used by tests and the merge step."""
    out_path = output_dir / f"{stem}.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path


def process_files(
    files: Iterable[Path],
    extractor,
    output_dir: Path,
) -> dict[str, int]:
    """Single-prompt extraction loop. Retained for the legacy test harness
    that pre-dates the staged pipeline; not used by `main()` anymore.

    Each input file's response is written verbatim to `<stem>.json` (no
    merge, no per-stage filtering). Outcome buckets match `process_stage`.
    """
    stats = {"ok": 0, "truncated": 0, "invalid_json": 0, "errors": 0}
    for path in files:
        log.info("[process] %s", path.name)
        t0 = time.time()
        body = path.read_text(encoding="utf-8")
        try:
            result = extractor.extract(filename=path.name, body=body)
        except Exception as exc:
            log.error("[error]   %s: %s", path.name, exc)
            stats["errors"] += 1
            continue

        if isinstance(result, ExtractResult):
            raw, truncated = result.text, result.truncated
        else:
            raw, truncated = result, False

        if truncated:
            log.error("[truncated] %s: model hit max_output_tokens "
                      "(%d chars produced) — saving raw output",
                      path.name, len(raw))
            output_dir.joinpath(f"{path.stem}.raw.txt").write_text(
                raw, encoding="utf-8",
            )
            stats["truncated"] += 1
            continue

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.error("[error]   %s: invalid JSON (%s)", path.name, exc)
            output_dir.joinpath(f"{path.stem}.raw.txt").write_text(
                raw, encoding="utf-8",
            )
            stats["invalid_json"] += 1
            continue

        out_path = write_output(output_dir, path.stem, parsed)
        stats["ok"] += 1
        log.info("[ok]      %s -> %s (%.1fs)",
                 path.name, out_path.name, time.time() - t0)
    return stats


# ---------------------------------------------------------------------------
# Merge: combine the 4 stage intermediates into the canonical `<stem>.json`
# ---------------------------------------------------------------------------
# The order in which keys appear in the merged JSON. Mirrors the layout
# established by the legacy single-pass schema so that downstream tools
# (scorer, verifier, graph builder) see the same shape.
MERGED_KEY_ORDER: tuple[str, ...] = (
    "file",
    "court_type", "court", "case_number", "decision_number",
    "decision_date", "decision_type",
    "is_final", "finality_basis",
    "decision_outcome", "decision_outcome_raw",
    "vote_unanimity", "has_dissent", "dissent_summary",
    "appellants", "appeal_outcomes_by_role",
    "subject", "summary",
    "keywords", "legal_issues", "legal_concepts",
    "dispositive_reasoning", "fact_pattern",
    "cited_court_decisions", "cited_law_articles",
)


def merge_stage_payloads(
    stem: str, stage_payloads: dict[str, dict],
) -> dict:
    """Combine per-stage payloads into one dict shaped like the legacy schema.

    Keys outside each stage's declared `output_keys` are dropped — this
    prevents an over-eager model that emitted, say, a `summary` field
    inside the metadata stage from clobbering the dedicated summary
    stage's value.
    """
    merged: dict[str, Any] = {"file": f"{stem}.md"}
    for stage in STAGES:
        payload = stage_payloads.get(stage.name)
        if not payload:
            continue
        # Each stage echoes `file`; we already set it from the stem so
        # the merged value is consistent across stages.
        for key in stage.output_keys:
            if key in payload:
                merged[key] = payload[key]

    # Re-key in the canonical order for stable downstream diffing.
    ordered: dict[str, Any] = {}
    for k in MERGED_KEY_ORDER:
        if k in merged:
            ordered[k] = merged[k]
    # Anything unexpected (shouldn't happen given the filter above) goes last.
    for k, v in merged.items():
        if k not in ordered:
            ordered[k] = v
    return ordered


def merge_one_document(
    stem: str, intermediate_dir: Path, output_dir: Path,
) -> tuple[Path | None, dict[str, list[str]]]:
    """Merge all available stage intermediates for one doc.

    Returns (merged_output_path or None if NO stage produced output,
    detail dict mapping `present`/`missing` -> stage-name list).
    """
    stage_payloads: dict[str, dict] = {}
    present: list[str] = []
    missing: list[str] = []
    for stage in STAGES:
        path = _intermediate_path(intermediate_dir, stem, stage)
        if not path.exists():
            missing.append(stage.name)
            continue
        try:
            stage_payloads[stage.name] = json.loads(
                path.read_text(encoding="utf-8")
            )
            present.append(stage.name)
        except json.JSONDecodeError as exc:
            log.error("[merge] %s: invalid JSON in %s (%s)",
                      stem, path.name, exc)
            missing.append(stage.name)

    detail = {"present": present, "missing": missing}
    if not stage_payloads:
        return None, detail

    merged = merge_stage_payloads(stem, stage_payloads)
    out_path = write_output(output_dir, stem, merged)
    return out_path, detail


def discover_stems(intermediate_dir: Path) -> list[str]:
    """Find every `<stem>` that has at least one stage intermediate on disk."""
    stems: set[str] = set()
    for stage in STAGES:
        suffix = stage.intermediate_suffix
        for p in intermediate_dir.glob(f"*{suffix}"):
            stems.add(p.name[: -len(suffix)])
    return sorted(stems)


def merge_all(
    intermediate_dir: Path, output_dir: Path, *, needle: str | None = None,
) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {"merged": 0, "skipped_no_intermediates": 0,
             "complete": 0, "partial": 0}
    stems = discover_stems(intermediate_dir)
    if needle:
        stems = [s for s in stems if needle in s]

    for stem in stems:
        out_path, detail = merge_one_document(stem, intermediate_dir, output_dir)
        if out_path is None:
            stats["skipped_no_intermediates"] += 1
            continue
        stats["merged"] += 1
        if detail["missing"]:
            stats["partial"] += 1
            log.warning("[merge] %s: partial — present=%s missing=%s",
                        stem, detail["present"], detail["missing"])
        else:
            stats["complete"] += 1
            log.info("[merge] %s: complete (all %d stages)",
                     stem, len(STAGES))
    return stats


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------
def run_stage(
    stage: Stage,
    args: argparse.Namespace,
    settings: Settings,
    corpus_dir: Path,
    intermediate_dir: Path,
) -> dict[str, int]:
    cfg = resolve_stage_config(stage, args, settings)
    prompt_path: Path = cfg["prompt_path"]
    if not prompt_path.is_file():
        raise RuntimeError(
            f"[{stage.name}] system prompt not found: {prompt_path}"
        )
    system_prompt = prompt_path.read_text(encoding="utf-8")

    files, skipped = select_files(
        corpus_dir, intermediate_dir, stage,
        args.filter, args.force, args.limit,
    )
    log.info("[%s] to_process=%d  skipped=%d  prompt=%s",
             stage.name, len(files), len(skipped), prompt_path.name)
    if not files:
        return {"ok": 0, "truncated": 0, "invalid_json": 0, "errors": 0,
                "skipped": len(skipped)}

    extractor = build_extractor(stage, cfg, settings, system_prompt)
    stats = process_stage(stage, files, extractor, intermediate_dir)
    stats["skipped"] = len(skipped)
    return stats


def resolve_paths(
    args: argparse.Namespace, settings: Settings,
) -> tuple[Path, Path, Path]:
    corpus_dir = (args.corpus_dir or settings.corpus_dir).resolve()
    output_dir = (args.output_dir or settings.llm_extract_output_dir).resolve()
    intermediate_dir = (args.intermediate_dir
                        or settings.llm_stages_intermediate_dir).resolve()
    return corpus_dir, output_dir, intermediate_dir


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    settings = get_settings()

    corpus_dir, output_dir, intermediate_dir = resolve_paths(args, settings)
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.merge_only:
        if not intermediate_dir.is_dir():
            log.error("Intermediate directory not found: %s", intermediate_dir)
            return 1
        log.info("Merge-only mode — intermediate=%s  output=%s",
                 intermediate_dir, output_dir)
        m = merge_all(intermediate_dir, output_dir, needle=args.filter)
        log.info("Merge done — merged=%d complete=%d partial=%d empty=%d",
                 m["merged"], m["complete"], m["partial"],
                 m["skipped_no_intermediates"])
        return 0

    if not corpus_dir.is_dir():
        log.error("Corpus directory not found: %s", corpus_dir)
        return 1

    if args.stage:
        stages_to_run: tuple[Stage, ...] = (get_stage(args.stage),)
    else:
        # Reject ambiguous CLI overrides when running all stages.
        if args.model or args.base_url or args.api_key:
            log.error(
                "--model/--base-url/--api-key apply to a single stage; "
                "either pass --stage <name> or set the per-stage env vars "
                "LLM_STAGE_<NAME>_{MODEL,BASE_URL,API_KEY}.",
            )
            return 1
        stages_to_run = STAGES

    overall_errors = 0
    for stage in stages_to_run:
        try:
            stats = run_stage(stage, args, settings, corpus_dir, intermediate_dir)
        except RuntimeError as exc:
            log.error("%s", exc)
            overall_errors += 1
            continue
        log.info(
            "[%s] Done — ok=%d truncated=%d invalid_json=%d errors=%d skipped=%d",
            stage.name, stats["ok"], stats["truncated"],
            stats["invalid_json"], stats["errors"], stats["skipped"],
        )
        overall_errors += stats["errors"]

    if args.no_merge or args.stage:
        # Single-stage runs and explicit --no-merge skip the merge step.
        if args.stage and not args.no_merge:
            log.info("Single-stage run — merge skipped (run without "
                     "--stage, or use --merge-only, to regenerate "
                     "<stem>.json from the intermediates).")
        return 0 if overall_errors == 0 else 2

    log.info("Merging intermediates -> %s", output_dir)
    m = merge_all(intermediate_dir, output_dir, needle=args.filter)
    log.info("Merge done — merged=%d complete=%d partial=%d empty=%d",
             m["merged"], m["complete"], m["partial"],
             m["skipped_no_intermediates"])

    return 0 if overall_errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
