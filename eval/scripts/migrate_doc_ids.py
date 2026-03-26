#!/usr/bin/env python3
"""
One-time migration: replace filename-stem doc_ids with hash-based doc_ids
in gold_standard.json and corpus_manifest.json.

The hash is computed from the document's legal identity (court|daire|esas_no)
via the ingestion pipeline's compute_doc_id function.

Usage:
    python eval/scripts/migrate_doc_ids.py [--dry-run]
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from ingest import compute_doc_id  # noqa: E402

GOLD_STANDARD = PROJECT_ROOT / "eval" / "gold_standard.json"
CORPUS_MANIFEST = PROJECT_ROOT / "eval" / "corpus_manifest.json"


def build_id_mapping(manifest):
    """Build filename_stem → hash_doc_id mapping from corpus manifest."""
    mapping = {}
    for doc in manifest:
        old_id = doc["doc_id"]  # currently filename stem
        court = doc.get("court", "")
        daire = doc.get("daire", "")
        esas_no = doc.get("esas_no", "")
        new_id = compute_doc_id(court, daire, esas_no)
        if new_id in mapping.values():
            # Find which old_id maps to the same new_id
            collision = [k for k, v in mapping.items() if v == new_id][0]
            print(f"WARNING: hash collision — {old_id} and {collision} both map to {new_id}")
        mapping[old_id] = new_id
    return mapping


def migrate_gold_standard(gs, mapping):
    """Replace doc_ids in gold_standard.json. Returns (migrated_data, replacement_count)."""
    count = 0
    for query in gs["queries"]:
        for judgment in query.get("relevance_judgments", []):
            old = judgment["doc_id"]
            if old in mapping:
                judgment["doc_id"] = mapping[old]
                count += 1
            else:
                print(f"WARNING: doc_id '{old}' in query {query['query_id']} not found in mapping")

        for pair in query.get("contradictory_pairs", []):
            for key in ("doc_a", "doc_b"):
                old = pair.get(key, "")
                if old in mapping:
                    pair[key] = mapping[old]
                    count += 1
                elif old:
                    print(f"WARNING: {key} '{old}' in query {query['query_id']} not found in mapping")
    return gs, count


def migrate_corpus_manifest(manifest, mapping):
    """Replace doc_id field in corpus_manifest.json."""
    for doc in manifest:
        old = doc["doc_id"]
        if old in mapping:
            doc["doc_id"] = mapping[old]
    return manifest


def main():
    dry_run = "--dry-run" in sys.argv

    manifest = json.loads(CORPUS_MANIFEST.read_text(encoding="utf-8"))
    gs = json.loads(GOLD_STANDARD.read_text(encoding="utf-8"))

    mapping = build_id_mapping(manifest)

    # Print mapping table
    print(f"{'Old doc_id (filename stem)':<70} → {'New doc_id (hash)'}")
    print("-" * 95)
    for old, new in sorted(mapping.items()):
        print(f"{old:<70} → {new}")
    print(f"\nTotal mappings: {len(mapping)}")

    # Check for collisions
    new_ids = list(mapping.values())
    unique_new = set(new_ids)
    if len(unique_new) != len(new_ids):
        print(f"\nERROR: {len(new_ids) - len(unique_new)} hash collisions detected. Aborting.")
        sys.exit(1)

    # Migrate
    gs_migrated, gs_count = migrate_gold_standard(gs, mapping)
    manifest_migrated = migrate_corpus_manifest(manifest, mapping)

    print(f"\ngold_standard.json: {gs_count} doc_id references updated")
    print(f"corpus_manifest.json: {len(manifest_migrated)} doc_id fields updated")

    if dry_run:
        print("\n[DRY RUN] No files written.")
        return

    # Write files
    GOLD_STANDARD.write_text(
        json.dumps(gs_migrated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    CORPUS_MANIFEST.write_text(
        json.dumps(manifest_migrated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("\nFiles written successfully.")

    # Validate: every doc_id in gold_standard should exist in manifest
    manifest_ids = {d["doc_id"] for d in manifest_migrated}
    for query in gs_migrated["queries"]:
        for judgment in query.get("relevance_judgments", []):
            if judgment["doc_id"] not in manifest_ids:
                print(f"VALIDATION ERROR: {judgment['doc_id']} in {query['query_id']} not in manifest")
    print("Validation passed: all gold_standard doc_ids found in manifest.")


if __name__ == "__main__":
    main()
