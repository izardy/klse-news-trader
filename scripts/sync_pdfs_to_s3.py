#!/usr/bin/env python3
"""
Stage 1: Sync PDFs from data/pdfs/ALL/ to S3 bucket 'helang' under klse/annual_report_pdf/.
Idempotent — skips already-uploaded files by checking S3 key existence.
Tracks sync state in pipeline_state table.
"""
import os
import sys
import json
import logging
import hashlib
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import get_conn, init_pipeline_state, set_pipeline_state, get_pipeline_state, DB_PATH, insert_annual_report

# ─── Config ───────────────────────────────────────────────────────────────────
BUCKET = "helang"
S3_PREFIX = "klse/annual_report_pdf"
PDF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pdfs", "ALL")
LOG_FILE = "/tmp/s3_sync.log"
MULTIPART_THRESHOLD = 8 * 1024 * 1024  # 8 MB
MAX_WORKERS = 8
REGION = "ap-southeast-1"

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("s3_sync")

# ─── S3 Client ────────────────────────────────────────────────────────────────
_boto_config = Config(
    region_name=REGION,
    max_pool_connections=20,
    retries={"max_attempts": 5, "mode": "adaptive"},
)
s3 = boto3.client("s3", config=_boto_config)
transfer_config = boto3.s3.transfer.TransferConfig(
    multipart_threshold=MULTIPART_THRESHOLD,
    max_concurrency=4,
    multipart_chunksize=MULTIPART_THRESHOLD,
    use_threads=True,
)


def list_all_pdfs():
    """List all PDF files in the source directory."""
    pdfs = []
    for root, dirs, files in os.walk(PDF_DIR):
        for f in files:
            if f.lower().endswith(".pdf"):
                pdfs.append(os.path.join(root, f))
    return sorted(pdfs)


def s3_key_exists(key):
    """Check if an S3 key already exists."""
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


def upload_pdf(pdf_path):
    """Upload a single PDF to S3 with multipart support. Returns (key, size, skipped)."""
    rel_path = os.path.relpath(pdf_path, PDF_DIR)
    key = f"{S3_PREFIX}/{rel_path}"

    if s3_key_exists(key):
        return key, os.path.getsize(pdf_path), True

    file_size = os.path.getsize(pdf_path)
    s3.upload_file(pdf_path, BUCKET, key, Config=transfer_config)
    return key, file_size, False


def register_pdf_in_db(pdf_path, s3_key):
    """Register a newly uploaded PDF in annual_reports with status='pending'."""
    filename = os.path.basename(pdf_path)
    # Try to extract symbol from filename (e.g. "MAYBANK_Annual_Report_2024.pdf")
    symbol = filename.split("_")[0].upper() if "_" in filename else "UNKNOWN"
    try:
        insert_annual_report(
            symbol=symbol,
            title=filename,
            pdf_url=f"s3://{BUCKET}/{s3_key}",
            local_path=pdf_path,
            file_size=os.path.getsize(pdf_path),
            downloaded_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        )
        # Update with s3_key and status
        with get_conn() as conn:
            conn.execute(
                "UPDATE annual_reports SET s3_key = ?, status = 'pending' WHERE pdf_url = ?",
                (s3_key, f"s3://{BUCKET}/{s3_key}")
            )
        return True
    except Exception as e:
        # May already exist (UNIQUE constraint on symbol+pdf_url)
        log.debug(f"  DB register skip: {e}")
        return False


def run_sync(limit=None):
    """Main sync routine."""
    log.info("=" * 60)
    log.info("Stage 1: S3 PDF Sync starting")
    log.info(f"Source: {PDF_DIR}")
    log.info(f"Destination: s3://{BUCKET}/{S3_PREFIX}/")

    init_pipeline_state()

    pdfs = list_all_pdfs()
    total = len(pdfs)
    log.info(f"Found {total} PDFs to sync")

    if limit:
        pdfs = pdfs[:limit]
        log.info(f"Limited to first {limit} PDFs")

    uploaded = 0
    skipped = 0
    errors = 0
    total_bytes = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(upload_pdf, pdf): pdf for pdf in pdfs}
        for i, future in enumerate(as_completed(futures)):
            try:
                key, size, was_skipped = future.result()
                if was_skipped:
                    skipped += 1
                else:
                    uploaded += 1
                    total_bytes += size
                    # Register in DB for pipeline tracking
                    pdf_path = futures[future]
                    register_pdf_in_db(pdf_path, key)
                    log.info(f"  [{i+1}/{len(pdfs)}] UPLOADED: {key} ({size/1024:.0f} KB)")
            except Exception as e:
                errors += 1
                log.error(f"  [{i+1}/{len(pdfs)}] ERROR: {e}")

            # Record progress every 100 files
            if (i + 1) % 100 == 0:
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                log.info(f"Progress: {i+1}/{len(pdfs)} ({rate:.1f} files/s) | uploaded={uploaded} skipped={skipped} errors={errors}")
                set_pipeline_state("s3_sync_progress", json.dumps({
                    "total": len(pdfs),
                    "processed": i + 1,
                    "uploaded": uploaded,
                    "skipped": skipped,
                    "errors": errors,
                }))

    elapsed = time.time() - start_time
    log.info("=" * 60)
    log.info(f"Sync complete in {elapsed:.1f}s")
    log.info(f"  Total: {len(pdfs)} | Uploaded: {uploaded} | Skipped: {skipped} | Errors: {errors}")
    log.info(f"  Data transferred: {total_bytes / 1e6:.1f} MB")

    # Record final state
    set_pipeline_state("s3_sync_last_run", json.dumps({
        "timestamp": datetime.utcnow().isoformat(),
        "total": len(pdfs),
        "uploaded": uploaded,
        "skipped": skipped,
        "errors": errors,
        "bytes_transferred": total_bytes,
        "duration_s": elapsed,
    }))

    return uploaded, skipped, errors


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Sync PDFs to S3")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of PDFs to sync (for testing)")
    args = ap.parse_args()
    run_sync(limit=args.limit)
