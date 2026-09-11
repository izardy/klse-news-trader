#!/usr/bin/env python3
"""
Stage 5: Pipeline Orchestrator
Orchestrates all 4 stages in order:
  1. S3 PDF Sync
  2. Iceberg Tables (create if needed)
  3. Bedrock Extraction → Iceberg
  4. Iceberg → SQLite Drain

Can run individual stages via --stage flag.
Records pipeline_state after each stage.
"""
import os
import sys
import json
import time
import logging
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import (
    init_pipeline_state, set_pipeline_state, get_pipeline_state, DB_PATH
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("pipeline")

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
PYTHON = os.environ.get("PIPELINE_PYTHON", "/home/sandbox/miniconda3/envs/ceruk/bin/python")


def run_script(script_name, args=None):
    """Run a pipeline script. Returns (exit_code, stdout, stderr)."""
    script_path = os.path.join(SCRIPTS_DIR, script_name)
    cmd = [PYTHON, script_path]
    if args:
        cmd.extend(args)

    log.info(f"Running: {' '.join(cmd)}")
    start = time.time()

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=3600,  # 1 hour max per stage
    )

    elapsed = time.time() - start
    if result.returncode != 0:
        log.error(f"  Script failed (exit={result.returncode}) in {elapsed:.0f}s")
        log.error(f"  STDERR: {result.stderr[-2000:]}")
    else:
        log.info(f"  Script completed in {elapsed:.0f}s")

    if result.stdout:
        # Log last 1000 chars of stdout
        log.info(f"  STDOUT: ...{result.stdout[-1000:]}")

    return result.returncode, result.stdout, result.stderr


def stage_s3_sync(limit=None):
    """Stage 1: Sync PDFs to S3."""
    log.info("=" * 60)
    log.info("STAGE 1: S3 PDF Sync")
    args = []
    if limit:
        args.extend(["--limit", str(limit)])
    exit_code, stdout, stderr = run_script("sync_pdfs_to_s3.py", args)
    return exit_code == 0


def stage_iceberg_tables():
    """Stage 2: Create Iceberg tables."""
    log.info("=" * 60)
    log.info("STAGE 2: Create Iceberg Tables")
    exit_code, stdout, stderr = run_script("create_iceberg_tables.py")
    return exit_code == 0


def stage_bedrock_extract(limit=None, delay=1.0, db_path=None):
    """Stage 3: Bedrock extraction."""
    log.info("=" * 60)
    log.info("STAGE 3: Bedrock Extraction → Iceberg")
    args = []
    if limit:
        args.extend(["--limit", str(limit)])
    if delay:
        args.extend(["--delay", str(delay)])
    if db_path:
        args.extend(["--db-path", db_path])
    exit_code, stdout, stderr = run_script("bedrock_extract_to_iceberg.py", args)
    return exit_code == 0


def stage_drain(incremental=True, db_path=None):
    """Stage 4: Iceberg → SQLite drain."""
    log.info("=" * 60)
    log.info("STAGE 4: Iceberg → SQLite Drain")
    args = []
    if incremental:
        args.append("--incremental")
    if db_path:
        args.extend(["--db-path", db_path])
    exit_code, stdout, stderr = run_script("drain_iceberg_to_sqlite.py", args)
    return exit_code == 0


def record_stage_state(stage_name, success, details=None):
    """Record stage completion in pipeline_state."""
    state = {
        "stage": stage_name,
        "success": success,
        "timestamp": datetime.utcnow().isoformat(),
    }
    if details:
        state["details"] = details
    set_pipeline_state(f"stage_{stage_name}", json.dumps(state))


def run_full_pipeline(s3_limit=None, bedrock_limit=None, bedrock_delay=1.0, db_path=None):
    """Run the full pipeline."""
    init_pipeline_state(db_path)
    pipeline_start = time.time()
    log.info("=" * 60)
    log.info("STARTING FULL PIPELINE")
    log.info("=" * 60)

    results = {}

    # Stage 1: S3 Sync
    try:
        success = stage_s3_sync(limit=s3_limit)
        record_stage_state("s3_sync", success)
        results["s3_sync"] = success
        if not success:
            log.error("Stage 1 (S3 Sync) failed — aborting pipeline")
            return results
    except Exception as e:
        log.error(f"Stage 1 (S3 Sync) error: {e}")
        record_stage_state("s3_sync", False, {"error": str(e)})
        results["s3_sync"] = False
        return results

    # Stage 2: Iceberg Tables
    try:
        success = stage_iceberg_tables()
        record_stage_state("iceberg_tables", success)
        results["iceberg_tables"] = success
        if not success:
            log.error("Stage 2 (Iceberg Tables) failed — aborting pipeline")
            return results
    except Exception as e:
        log.error(f"Stage 2 (Iceberg Tables) error: {e}")
        record_stage_state("iceberg_tables", False, {"error": str(e)})
        results["iceberg_tables"] = False
        return results

    # Stage 3: Bedrock Extraction
    try:
        success = stage_bedrock_extract(limit=bedrock_limit, delay=bedrock_delay, db_path=db_path)
        record_stage_state("bedrock_extract", success)
        results["bedrock_extract"] = success
    except Exception as e:
        log.error(f"Stage 3 (Bedrock Extract) error: {e}")
        record_stage_state("bedrock_extract", False, {"error": str(e)})
        results["bedrock_extract"] = False

    # Stage 4: Drain (incremental)
    try:
        success = stage_drain(incremental=True, db_path=db_path)
        record_stage_state("drain", success)
        results["drain"] = success
    except Exception as e:
        log.error(f"Stage 4 (Drain) error: {e}")
        record_stage_state("drain", False, {"error": str(e)})
        results["drain"] = False

    elapsed = time.time() - pipeline_start
    log.info("=" * 60)
    log.info(f"PIPELINE COMPLETE in {elapsed:.0f}s")
    log.info(f"Results: {json.dumps(results, indent=2)}")
    set_pipeline_state("last_full_pipeline", json.dumps({
        "timestamp": datetime.utcnow().isoformat(),
        "duration_s": elapsed,
        "results": results,
    }), db_path)

    return results


def run_single_stage(stage_name, db_path=None, **kwargs):
    """Run a single stage."""
    init_pipeline_state(db_path)

    if stage_name == "s3":
        success = stage_s3_sync(limit=kwargs.get("limit"))
        record_stage_state("s3_sync", success)
    elif stage_name == "iceberg":
        success = stage_iceberg_tables()
        record_stage_state("iceberg_tables", success)
    elif stage_name == "bedrock":
        success = stage_bedrock_extract(
            limit=kwargs.get("limit"),
            delay=kwargs.get("delay", 1.0),
            db_path=db_path,
        )
        record_stage_state("bedrock_extract", success)
    elif stage_name == "drain":
        success = stage_drain(incremental=kwargs.get("incremental", True), db_path=db_path)
        record_stage_state("drain", success)
    else:
        log.error(f"Unknown stage: {stage_name}")
        sys.exit(1)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="KLSE Pipeline Orchestrator")
    ap.add_argument(
        "--stage",
        choices=["s3", "iceberg", "bedrock", "drain"],
        help="Run a individual stage (omit for full pipeline)",
    )
    ap.add_argument("--s3-limit", type=int, default=None, help="Limit S3 sync to N PDFs")
    ap.add_argument("--bedrock-limit", type=int, default=None, help="Limit Bedrock extraction to N PDFs")
    ap.add_argument("--bedrock-delay", type=float, default=1.0, help="Delay between Bedrock calls")
    ap.add_argument("--limit", type=int, default=None, help="Generic limit (used for single-stage runs)")
    ap.add_argument("--db-path", type=str, default=None, help="SQLite DB path")
    ap.add_argument("--full-drain", action="store_true", help="Full drain (DELETE+INSERT) instead of incremental")
    args = ap.parse_args()

    if args.stage:
        run_single_stage(
            args.stage,
            db_path=args.db_path,
            limit=args.limit or args.bedrock_limit,
            delay=args.bedrock_delay,
            incremental=not args.full_drain,
        )
    else:
        run_full_pipeline(
            s3_limit=args.s3_limit,
            bedrock_limit=args.bedrock_limit,
            bedrock_delay=args.bedrock_delay,
            db_path=args.db_path,
        )
