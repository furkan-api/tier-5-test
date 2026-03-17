#!/usr/bin/env python3
"""
One-time migration: normalize corpus filenames to consistent slug format
and update all references in gold_standard.json and corpus_manifest.json.

Derives slugs from the original filenames (not parsed metadata, which can
be misclassified). Run with --dry-run first to preview changes.
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = EVAL_DIR.parent
CORPUS_DIR = PROJECT_ROOT / "corpus"
GOLD_STANDARD = EVAL_DIR / "gold_standard.json"
CORPUS_MANIFEST = EVAL_DIR / "corpus_manifest.json"

TURKISH_MAP = {
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g",
    "ı": "i", "İ": "i", "ö": "o", "Ö": "o",
    "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
    "â": "a", "î": "i", "û": "u",
}


def slugify(text):
    for tr_char, ascii_char in TURKISH_MAP.items():
        text = text.replace(tr_char, ascii_char)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text


def slug_from_filename(stem):
    """Parse the old filename pattern and produce a clean slug.

    Old patterns:
      "1. Hukuk Dairesi 2007_9815 E. , 2007_11607 K."
      "Hukuk Genel Kurulu 2012_1141 E. , 2013_282 K."
      "Danıştay 9. Daire Başkanlığı  2019_2591 E. , 2021_4815 K."
      "T.C. YARGITAY 22. HUKUK DAİRESİ E. 2016_6543 K. 2016_12675 T. 28.4.2016"
      "Yargıtay 2. Hukuk Dairesi 2023_7132 Esas 2024_4132 Karar Sayılı Kararı"
      "2013-7449"  (AYM başvuru numarası)
    """
    s = stem

    # Pattern 1: "N. Hukuk/Ceza Dairesi YYYY_N E. , YYYY_N K."
    m = re.match(
        r"(\d+)\.\s+(Hukuk Dairesi|Ceza Dairesi)\s+(\d{4})[/_](\d+)\s*E\.?\s*[,.]?\s*(\d{4})[/_](\d+)\s*K",
        s, re.IGNORECASE,
    )
    if m:
        num, daire, ey, en, ky, kn = m.groups()
        daire_slug = slugify(f"{num} {daire}")
        return f"{daire_slug}-e-{ey}-{en}-k-{ky}-{kn}-1"

    # Pattern 2: "Hukuk Genel Kurulu YYYY_N E. , YYYY_N K." (with or without Karar)
    m = re.match(
        r"Hukuk Genel Kurulu\s+(\d{4})[/_](\d+)\s*E\.?\s*[,.]?\s*(?:(\d{4})[/_](\d+)\s*K)?",
        s,
    )
    if m:
        ey, en, ky, kn = m.groups()
        slug = f"hukuk-genel-kurulu-e-{ey}-{en}"
        if ky and kn:
            slug += f"-k-{ky}-{kn}"
        return slug + "-1"

    # Pattern 3: "Danıştay N. Daire Başkanlığı YYYY_N E. , YYYY_N K."
    m = re.match(
        r"Danıştay\s+(\d+)\.\s+Daire\s+Başkanlığı\s+(\d{4})[/_](\d+)\s*E\.?\s*[,.]?\s*(\d{4})[/_](\d+)\s*K",
        s, re.IGNORECASE,
    )
    if m:
        num, ey, en, ky, kn = m.groups()
        return f"danistay-{num}-daire-e-{ey}-{en}-k-{ky}-{kn}-1"

    # Pattern 4: "T.C. YARGITAY N. HUKUK/CEZA DAİRESİ E. YYYY_N K. YYYY_N T. D.M.YYYY"
    m = re.match(
        r"T\.C\.\s+YARGITAY\s+(\d+)\.\s+(HUKUK DAİRESİ|CEZA DAİRESİ)\s+E\.?\s*(\d{4})[/_](\d+)\s+K\.?\s*(\d{4})[/_](\d+)",
        s, re.IGNORECASE,
    )
    if m:
        num, daire, ey, en, ky, kn = m.groups()
        daire_slug = slugify(f"{num} {daire}")
        slug = f"{daire_slug}-e-{ey}-{en}-k-{ky}-{kn}"
        # Try to extract date
        dm = re.search(r"T\.?\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
        if dm:
            slug += f"-t-{int(dm.group(1))}-{int(dm.group(2))}-{dm.group(3)}"
        return slug + "-1"

    # Pattern 5: "Yargıtay N. Hukuk Dairesi YYYY_N Esas YYYY_N Karar..."
    m = re.match(
        r"Yargıtay\s+(\d+)\.\s+(Hukuk Dairesi|Ceza Dairesi|HD|CD)\s+(\d{4})[/_](\d+)\s*Esas\s+(\d{4})[/_](\d+)\s*Karar",
        s, re.IGNORECASE,
    )
    if m:
        num, daire, ey, en, ky, kn = m.groups()
        if daire.upper() in ("HD", "HUKUK DAIRESI"):
            daire = "Hukuk Dairesi"
        else:
            daire = "Ceza Dairesi"
        daire_slug = slugify(f"{num} {daire}")
        return f"{daire_slug}-e-{ey}-{en}-k-{ky}-{kn}-1"

    # Pattern 6: "Yargıtay N. HD E_ YYYY_N K_ YYYY_N (Kapatılan)..."
    m = re.match(
        r"Yargıtay\s+(\d+)\.\s+(HD|CD)\s+E[_:]\s*(\d{4})[/_](\d+)\s+K[_:]\s*(\d{4})[/_](\d+)",
        s,
    )
    if m:
        num, daire, ey, en, ky, kn = m.groups()
        daire_name = "hukuk-dairesi" if daire == "HD" else "ceza-dairesi"
        return f"{num}-{daire_name}-e-{ey}-{en}-k-{ky}-{kn}-1"

    # Pattern 7: "9. Hukuk Dairesi 2016_26476 E. , 2020_7547 K."
    # (same as pattern 1 but sometimes preceded by "Yargıtay")
    # Already covered by pattern 1

    # Pattern 8: "YYYY-NNNNN" (AYM başvuru numarası)
    m = re.match(r"(\d{4})-(\d+)$", s)
    if m:
        return f"aym-b-{m.group(1)}-{m.group(2)}-1"

    # Fallback: just slugify
    return slugify(s)


def compute_rename_map():
    """Returns {old_stem: new_stem} for files that need renaming."""
    rename_map = {}
    used_slugs = set()

    corpus_files = sorted(CORPUS_DIR.glob("*.md"))

    # First pass: collect already-good slugs
    for f in corpus_files:
        stem = f.stem
        if re.match(r"^[a-z0-9][-a-z0-9]*$", stem):
            used_slugs.add(stem)

    # Second pass: generate slugs for bad filenames
    for f in corpus_files:
        stem = f.stem
        if re.match(r"^[a-z0-9][-a-z0-9]*$", stem):
            continue

        new_slug = slug_from_filename(stem)

        # Deduplicate
        if new_slug in used_slugs:
            i = 2
            while f"{new_slug}-{i}" in used_slugs:
                i += 1
            new_slug = f"{new_slug}-{i}"

        rename_map[stem] = new_slug
        used_slugs.add(new_slug)

    return rename_map


def main():
    dry_run = "--dry-run" in sys.argv

    rename_map = compute_rename_map()

    if not rename_map:
        print("All filenames are already normalized.")
        return

    print(f"{'[DRY RUN] ' if dry_run else ''}Renaming {len(rename_map)} files:\n")
    for old, new in sorted(rename_map.items()):
        print(f"  {old}")
        print(f"    → {new}\n")

    if dry_run:
        print("Run without --dry-run to apply.")
        return

    # 1. Rename actual files
    for old_stem, new_stem in rename_map.items():
        old_path = CORPUS_DIR / f"{old_stem}.md"
        new_path = CORPUS_DIR / f"{new_stem}.md"
        if old_path.exists():
            old_path.rename(new_path)
        else:
            print(f"  WARNING: {old_path.name} not found, skipping")

    # 2. Update corpus_manifest.json
    manifest = json.loads(CORPUS_MANIFEST.read_text(encoding="utf-8"))
    for doc in manifest:
        if doc["doc_id"] in rename_map:
            new_stem = rename_map[doc["doc_id"]]
            doc["doc_id"] = new_stem
            doc["filename"] = f"{new_stem}.md"
    CORPUS_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 3. Update gold_standard.json
    gold = json.loads(GOLD_STANDARD.read_text(encoding="utf-8"))
    ref_updates = 0
    for query in gold["queries"]:
        for j in query.get("relevance_judgments", []):
            if j["doc_id"] in rename_map:
                j["doc_id"] = rename_map[j["doc_id"]]
                ref_updates += 1
        for cp in query.get("contradictory_pairs", []):
            for key in ("doc_a", "doc_b"):
                if cp.get(key) in rename_map:
                    cp[key] = rename_map[cp[key]]
                    ref_updates += 1
    GOLD_STANDARD.write_text(
        json.dumps(gold, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Renamed {len(rename_map)} files")
    print(f"Updated corpus_manifest.json")
    print(f"Updated gold_standard.json ({ref_updates} doc_id references)")
    print("\nRun validate_schema.py to verify.")


if __name__ == "__main__":
    main()
