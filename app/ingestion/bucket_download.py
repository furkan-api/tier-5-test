#!/usr/bin/env python3
"""
Download decision markdown files from an AWS S3 bucket into the corpus directory.

Reads a text file where each line is a filename (with or without .md extension).
Lines that are blank or start with '#' are ignored.

Usage:
    python -m app.ingestion.bucket_download --list-file decisions.txt
    python -m app.ingestion.bucket_download --list-file decisions.txt --corpus-dir ./corpus
    python -m app.ingestion.bucket_download --list-file decisions.txt --skip-existing
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from app.core.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def read_list_file(list_file: Path) -> list[str]:
    """Parse the decision list file; skip blanks and comment lines."""
    if not list_file.exists():
        log.error("List file not found: %s", list_file)
        sys.exit(1)

    filenames: list[str] = []
    for raw in list_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Normalise: always end with .md
        if not line.endswith(".md"):
            line = line + ".md"
        filenames.append(line)

    return filenames


def build_s3_key(filename: str, prefix: str) -> str:
    """Join optional prefix with filename into an S3 object key."""
    if prefix:
        return prefix.rstrip("/") + "/" + filename
    return filename


def download_decisions(
    filenames: list[str],
    corpus_dir: Path,
    bucket: str,
    prefix: str,
    s3_client,
    skip_existing: bool,
) -> dict[str, int]:
    stats = {"downloaded": 0, "skipped": 0, "errors": 0}

    for filename in filenames:
        dest = corpus_dir / filename

        if skip_existing and dest.exists():
            log.info("Skipped (exists): %s", filename)
            stats["skipped"] += 1
            continue

        key = build_s3_key(filename, prefix)
        try:
            log.info("Downloading s3://%s/%s → %s", bucket, key, dest)
            s3_client.download_file(bucket, key, str(dest))
            stats["downloaded"] += 1
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("404", "NoSuchKey"):
                log.warning("Not found in bucket: %s", key)
            else:
                log.error("S3 error for %s: %s", key, exc)
            stats["errors"] += 1
        except OSError as exc:
            log.error("File write error for %s: %s", dest, exc)
            stats["errors"] += 1

    return stats


def print_summary(filenames: list[str], stats: dict[str, int]) -> None:
    total = len(filenames)
    print("\n" + "=" * 60)
    print("BUCKET DOWNLOAD SUMMARY")
    print("=" * 60)
    print(f"\nRequested : {total}")
    print(f"Downloaded: {stats['downloaded']}")
    print(f"Skipped   : {stats['skipped']}")
    print(f"Errors    : {stats['errors']}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download decision .md files from S3 into the corpus directory"
    )
    parser.add_argument(
        "--list-file",
        type=Path,
        required=True,
        help="Text file with one filename per line (# comments and blank lines ignored)",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
        help="Destination directory (defaults to settings.corpus_dir)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=False,
        help="Skip files that already exist in the corpus directory",
    )
    args = parser.parse_args()

    settings = get_settings()
    corpus_dir: Path = args.corpus_dir or settings.corpus_dir

    if not settings.s3_bucket_name:
        log.error(
            "S3_BUCKET_NAME is not set. Add it to your .env file."
        )
        sys.exit(1)

    if not settings.aws_access_key_id or not settings.aws_secret_access_key:
        log.error(
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set in your .env file."
        )
        sys.exit(1)

    corpus_dir.mkdir(parents=True, exist_ok=True)

    filenames = read_list_file(args.list_file)
    if not filenames:
        log.warning("No filenames found in %s — nothing to download.", args.list_file)
        sys.exit(0)

    log.info(
        "Downloading %d file(s) from s3://%s (prefix: %r) → %s",
        len(filenames),
        settings.s3_bucket_name,
        settings.s3_prefix,
        corpus_dir,
    )

    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
        )
    except NoCredentialsError:
        log.error("AWS credentials could not be resolved.")
        sys.exit(1)

    stats = download_decisions(
        filenames=filenames,
        corpus_dir=corpus_dir,
        bucket=settings.s3_bucket_name,
        prefix=settings.s3_prefix,
        s3_client=s3,
        skip_existing=args.skip_existing,
    )

    print_summary(filenames, stats)

    if stats["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
