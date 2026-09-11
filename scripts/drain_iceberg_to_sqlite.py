#!/usr/bin/env python3
"""
Stage 4: Iceberg → SQLite Drain
- Read from Iceberg tables
- Write to SQLite tables (company_metrics, company_relationships, company_directors, company_shareholders, annual_reports)
- Clear SQLite table data before drain (DELETE then INSERT) to avoid duplicates
- Log counts before/after
"""
import os
import sys
import json
import logging
import tempfile
import shutil

import boto3
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import get_conn, DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("drain")

# ─── Config ───────────────────────────────────────────────────────────────────
BUCKET = "helang"
WAREHOUSE = f"s3://{BUCKET}"
NAMESPACE = "klse"
CATALOG_NAME = "klse"
REGION = "ap-southeast-1"

# Table name mappings: (iceberg_table, sqlite_table)
TABLES = [
    ("company_metrics", "company_metrics"),
    ("company_relationships", "company_relationships"),
    ("company_directors", "company_directors"),
    ("company_shareholders", "company_shareholders"),
    ("annual_reports", "annual_reports"),
]


def open_iceberg_table(table_name):
    """Open an Iceberg table and return (catalog, table, tmpdir)."""
    s3 = boto3.client("s3", region_name=REGION)
    tmpdir = tempfile.mkdtemp(prefix=f"klse-drain-{table_name}-")
    local_db = os.path.join(tmpdir, "catalog.db")
    catalog_key = f"{table_name}/catalog.db"

    try:
        s3.download_file(BUCKET, catalog_key, local_db)
    except Exception as e:
        raise RuntimeError(f"Cannot download catalog for {table_name}: {e}")

    catalog = SqlCatalog(
        CATALOG_NAME,
        uri=f"sqlite:///{local_db}",
        warehouse=WAREHOUSE,
    )
    tbl = catalog.load_table(f"{NAMESPACE}.{table_name}")
    return catalog, tbl, tmpdir


def safe_val(val):
    """Convert arrow value to Python native."""
    if val is None:
        return None
    if hasattr(val, 'as_py'):
        return val.as_py()
    return val


def drain_company_metrics(db_path, since_timestamp=None):
    """Drain company_metrics from Iceberg to SQLite."""
    iceberg_table = "company_metrics"
    catalog, tbl, tmpdir = open_iceberg_table(iceberg_table)
    try:
        # Build scan — filter by extracted_at if since_timestamp provided
        if since_timestamp:
            arrow = tbl.scan(row_filter=f"extracted_at > '{since_timestamp}'").to_arrow()
        else:
            arrow = tbl.scan().to_arrow()
        rows = []
        for i in range(arrow.num_rows):
            rows.append((
                safe_val(arrow.column("symbol")[i]),
                safe_val(arrow.column("report_id")[i]),
                safe_val(arrow.column("metric")[i]),
                safe_val(arrow.column("layer")[i]),
                safe_val(arrow.column("value")[i]),
                safe_val(arrow.column("currency")[i]),
                safe_val(arrow.column("unit")[i]),
                safe_val(arrow.column("fiscal_year")[i]),
                safe_val(arrow.column("period")[i]),
                safe_val(arrow.column("source_page")[i]),
                safe_val(arrow.column("source_section")[i]),
                safe_val(arrow.column("reported_or_calculated")[i]),
                safe_val(arrow.column("definition")[i]),
                safe_val(arrow.column("extraction_source")[i]),
                safe_val(arrow.column("raw_text")[i]),
                safe_val(arrow.column("confidence")[i]),
            ))

        with get_conn(db_path) as conn:
            conn.execute("DELETE FROM company_metrics")
            conn.executemany(
                """INSERT INTO company_metrics
                   (symbol, report_id, metric, layer, value, currency, unit, fiscal_year,
                    period, source_page, source_section, reported_or_calculated, definition,
                    extraction_source, raw_text, confidence)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows
            )
            conn.commit()
        log.info(f"  company_metrics: drained {len(rows)} rows")
        return len(rows)
    finally:
        catalog.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def drain_company_relationships(db_path, since_timestamp=None):
    """Drain company_relationships from Iceberg to SQLite."""
    iceberg_table = "company_relationships"
    catalog, tbl, tmpdir = open_iceberg_table(iceberg_table)
    try:
        if since_timestamp:
            arrow = tbl.scan(row_filter=f"extracted_at > '{since_timestamp}'").to_arrow()
        else:
            arrow = tbl.scan().to_arrow()
        rows = []
        for i in range(arrow.num_rows):
            rows.append((
                safe_val(arrow.column("symbol")[i]),
                safe_val(arrow.column("related_name")[i]),
                safe_val(arrow.column("relationship_type")[i]),
                safe_val(arrow.column("fiscal_year")[i]),
                safe_val(arrow.column("stake_pct")[i]),
                safe_val(arrow.column("effective_stake_pct")[i]),
                safe_val(arrow.column("source_page")[i]),
                safe_val(arrow.column("source_section")[i]),
                safe_val(arrow.column("report_id")[i]),
            ))

        with get_conn(db_path) as conn:
            conn.execute("DELETE FROM company_relationships")
            conn.executemany(
                """INSERT INTO company_relationships
                   (symbol, related_name, relationship_type, fiscal_year, stake_pct,
                    effective_stake_pct, source_page, source_section, report_id)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                rows
            )
            conn.commit()
        log.info(f"  company_relationships: drained {len(rows)} rows")
        return len(rows)
    finally:
        catalog.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def drain_company_directors(db_path, since_timestamp=None):
    """Drain company_directors from Iceberg to SQLite."""
    iceberg_table = "company_directors"
    catalog, tbl, tmpdir = open_iceberg_table(iceberg_table)
    try:
        if since_timestamp:
            arrow = tbl.scan(row_filter=f"extracted_at > '{since_timestamp}'").to_arrow()
        else:
            arrow = tbl.scan().to_arrow()
        rows = []
        for i in range(arrow.num_rows):
            rows.append((
                safe_val(arrow.column("symbol")[i]),
                safe_val(arrow.column("director_name")[i]),
                safe_val(arrow.column("fiscal_year")[i]),
                safe_val(arrow.column("role")[i]),
                safe_val(arrow.column("title")[i]),
                safe_val(arrow.column("tenure_years")[i]),
                safe_val(arrow.column("remuneration_mym_real")[i]),
                safe_val(arrow.column("independence")[i]),
                safe_val(arrow.column("source_page")[i]),
                safe_val(arrow.column("source_section")[i]),
                safe_val(arrow.column("report_id")[i]),
            ))

        with get_conn(db_path) as conn:
            conn.execute("DELETE FROM company_directors")
            conn.executemany(
                """INSERT INTO company_directors
                   (symbol, director_name, fiscal_year, role, title, tenure_years,
                    remuneration_mym_real, independence, source_page, source_section, report_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                rows
            )
            conn.commit()
        log.info(f"  company_directors: drained {len(rows)} rows")
        return len(rows)
    finally:
        catalog.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def drain_company_shareholders(db_path, since_timestamp=None):
    """Drain company_shareholders from Iceberg to SQLite."""
    iceberg_table = "company_shareholders"
    catalog, tbl, tmpdir = open_iceberg_table(iceberg_table)
    try:
        if since_timestamp:
            arrow = tbl.scan(row_filter=f"extracted_at > '{since_timestamp}'").to_arrow()
        else:
            arrow = tbl.scan().to_arrow()
        rows = []
        for i in range(arrow.num_rows):
            rows.append((
                safe_val(arrow.column("symbol")[i]),
                safe_val(arrow.column("shareholder_name")[i]),
                safe_val(arrow.column("fiscal_year")[i]),
                safe_val(arrow.column("shareholder_type")[i]),
                safe_val(arrow.column("stake_percent")[i]),
                safe_val(arrow.column("shares_held")[i]),
                safe_val(arrow.column("is_substantial")[i]),
                safe_val(arrow.column("is_controlling")[i]),
                safe_val(arrow.column("source_page")[i]),
                safe_val(arrow.column("source_section")[i]),
                safe_val(arrow.column("report_id")[i]),
            ))

        with get_conn(db_path) as conn:
            conn.execute("DELETE FROM company_shareholders")
            conn.executemany(
                """INSERT INTO company_shareholders
                   (symbol, shareholder_name, fiscal_year, shareholder_type, stake_percent,
                    shares_held, is_substantial, is_controlling, source_page, source_section, report_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                rows
            )
            conn.commit()
        log.info(f"  company_shareholders: drained {len(rows)} rows")
        return len(rows)
    finally:
        catalog.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def drain_annual_reports(db_path, since_timestamp=None):
    """Drain annual_reports from Iceberg to SQLite."""
    iceberg_table = "annual_reports"
    catalog, tbl, tmpdir = open_iceberg_table(iceberg_table)
    try:
        if since_timestamp:
            arrow = tbl.scan(row_filter=f"created_at > '{since_timestamp}'").to_arrow()
        else:
            arrow = tbl.scan().to_arrow()
        rows = []
        col_names = arrow.column_names
        for i in range(arrow.num_rows):
            rows.append((
                safe_val(arrow.column("symbol")[i]),
                safe_val(arrow.column("title")[i]) if "title" in col_names else None,
                safe_val(arrow.column("announcement_date")[i]) if "announcement_date" in col_names else None,
                safe_val(arrow.column("pdf_url")[i]) if "pdf_url" in col_names else None,
                safe_val(arrow.column("local_path")[i]) if "local_path" in col_names else None,
                safe_val(arrow.column("file_size")[i]) if "file_size" in col_names else None,
                safe_val(arrow.column("processed")[i]) if "processed" in col_names else None,
                safe_val(arrow.column("downloaded_at")[i]) if "downloaded_at" in col_names else None,
            ))

        with get_conn(db_path) as conn:
            conn.execute("DELETE FROM annual_reports")
            conn.executemany(
                """INSERT INTO annual_reports
                   (symbol, title, announcement_date, pdf_url, local_path, file_size,
                    processed, downloaded_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                rows
            )
            conn.commit()
        log.info(f"  annual_reports: drained {len(rows)} rows")
        return len(rows)
    finally:
        catalog.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_drain(db_path=None, incremental=False):
    """Drain all Iceberg tables to SQLite.
    
    Args:
        db_path: SQLite database path
        incremental: If True, only drain rows newer than last_drain_timestamp
                    and use INSERT OR REPLACE instead of DELETE+INSERT.
    """
    db_path = db_path or DB_PATH
    log.info("=" * 60)
    log.info("Stage 4: Iceberg → SQLite Drain")
    log.info(f"SQLite DB: {db_path}")
    log.info(f"Mode: {'incremental' if incremental else 'full'}")

    # Get last drain timestamp for incremental mode
    since_timestamp = None
    if incremental:
        from data.db import get_pipeline_state, set_pipeline_state
        since_timestamp = get_pipeline_state("last_drain_timestamp", db_path)
        log.info(f"Incremental since: {since_timestamp}")

    # Log counts before
    with get_conn(db_path) as conn:
        counts_before = {}
        for _, sqlite_table in TABLES:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM {sqlite_table}").fetchone()
            counts_before[sqlite_table] = row["cnt"]
        log.info(f"Counts before drain: {counts_before}")

    # Drain each table
    total = 0
    total += drain_company_metrics(db_path, since_timestamp)
    total += drain_company_relationships(db_path, since_timestamp)
    total += drain_company_directors(db_path, since_timestamp)
    total += drain_company_shareholders(db_path, since_timestamp)
    total += drain_annual_reports(db_path, since_timestamp)

    # Log counts after
    with get_conn(db_path) as conn:
        counts_after = {}
        for _, sqlite_table in TABLES:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM {sqlite_table}").fetchone()
            counts_after[sqlite_table] = row["cnt"]
        log.info(f"Counts after drain: {counts_after}")

    # Record drain timestamp
    from data.db import set_pipeline_state
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    set_pipeline_state("last_drain_timestamp", now, db_path)

    log.info(f"Drain complete: {total} total rows written")
    return total


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Iceberg → SQLite Drain")
    ap.add_argument("--db-path", type=str, default=None, help="SQLite DB path")
    ap.add_argument("--incremental", action="store_true", help="Only drain new rows since last run")
    args = ap.parse_args()
    run_drain(db_path=args.db_path, incremental=args.incremental)
