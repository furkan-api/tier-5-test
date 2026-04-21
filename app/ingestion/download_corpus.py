#!/usr/bin/env python3
"""
Download all markdown files from an S3 prefix into the corpus directory.

Unlike bucket_download.py (which requires a list file), this script
downloads everything under the configured S3_PREFIX, making it suitable
for bulk operations like smoke tests.

Usage:
    python -m app.ingestion.download_corpus [--skip-existing] [--workers 20]
    uv run python -m app.ingestion.download_corpus [--skip-existing] [--workers 20]
"""

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from app.core.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def list_s3_keys(s3_client, bucket: str, prefix: str) -> list[str]:
    keys = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix.rstrip("/") + "/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".md"):
                keys.append(key)
    return keys


def download_one(s3_client, bucket: str, key: str, dest: Path, skip_existing: bool) -> str:
    filename = Path(key).name
    local_path = dest / filename

    if skip_existing and local_path.exists():
        return "skipped"

    try:
        s3_client.download_file(bucket, key, str(local_path))
        return "downloaded"
    except ClientError as e:
        log.warning("S3 error for %s: %s", key, e)
        return "error"


def main():
    parser = argparse.ArgumentParser(description="Download all corpus MDs from S3")
    parser.add_argument("--skip-existing", action="store_true", default=True,
                        help="Skip files already in corpus dir (default: true)")
    parser.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    parser.add_argument("--workers", type=int, default=20,
                        help="Parallel download threads (default: 20)")
    parser.add_argument("--corpus-dir", type=Path, default=None)
    args = parser.parse_args()

    settings = get_settings()
    corpus_dir: Path = args.corpus_dir or settings.corpus_dir
    corpus_dir.mkdir(parents=True, exist_ok=True)

    if not settings.s3_bucket_name:
        log.error("S3_BUCKET_NAME not set in .env")
        sys.exit(1)

    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
    )

    log.info("Listing s3://%s/%s/ ...", settings.s3_bucket_name, settings.s3_prefix)
    keys = list_s3_keys(s3, settings.s3_bucket_name, settings.s3_prefix)
    log.info("Found %d .md files", len(keys))

    stats = {"downloaded": 0, "skipped": 0, "error": 0}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(download_one, s3, settings.s3_bucket_name, key, corpus_dir, args.skip_existing): key
            for key in keys
        }
        done = 0
        for future in as_completed(futures):
            result = future.result()
            stats[result] += 1
            done += 1
            if done % 1000 == 0:
                log.info("Progress: %d/%d (dl=%d skip=%d err=%d)",
                         done, len(keys), stats["downloaded"], stats["skipped"], stats["error"])

    print("\n" + "=" * 60)
    print("CORPUS DOWNLOAD SUMMARY")
    print("=" * 60)
    print(f"Total   : {len(keys)}")
    print(f"Downloaded : {stats['downloaded']}")
    print(f"Skipped : {stats['skipped']}")
    print(f"Errors  : {stats['error']}")


if __name__ == "__main__":
    main()
