#!/usr/bin/env python3
"""
Stage 3: Bedrock Extraction → Iceberg
- Read PDFs from S3 (helang/klse/annual_report_pdf/)
- Extract text (first 50 pages), send to Bedrock Nova Micro
- Extract ALL 16 layers + entity graph data
- Write results to Iceberg tables
- Mark PDF as processed in annual_reports Iceberg table
- Skip already-processed PDFs
- Process in batches, commit every 10 PDFs
"""
import os
import sys
import json
import re
import time
import logging
import tempfile
from datetime import datetime

import boto3
import pdfplumber
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.transforms import IdentityTransform

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import DB_PATH

# ─── Config ───────────────────────────────────────────────────────────────────
BUCKET = "helang"
WAREHOUSE = f"s3://{BUCKET}"
NAMESPACE = "klse"
CATALOG_NAME = "klse"
REGION = "ap-southeast-1"
S3_PREFIX = "klse/annual_report_pdf"
BEDROCK_REGION = "ap-southeast-1"
BEDROCK_MODEL = "apac.amazon.nova-micro-v1:0"
MAX_TOKENS = 16000
MAX_PAGES = 50
MAX_CHARS = 40000
BATCH_SIZE = 10
LOG_FILE = "/tmp/bedrock_extract.log"

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("bedrock_extract")

# ─── Clients ──────────────────────────────────────────────────────────────────
_bedrock_client = None
_s3_client = None


def get_bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _bedrock_client


def get_s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=REGION)
    return _s3_client


# ─── Extraction Prompt (16 layers) ───────────────────────────────────────────

EXTRACTION_PROMPT = """You are a financial data extraction expert specializing in Malaysian public listed companies (Bursa Malaysia).

Analyze the following annual report text and extract ALL available structured financial data. Return ONLY valid JSON, no markdown, no explanation.

The JSON must follow this exact structure:

{
  "company_info": {
    "company_name": "Full legal name",
    "sector": "e.g. Banking, Plantation, Oil & Gas",
    "industry": "More specific industry",
    "incorporation_date": "Date",
    "employee_count": 0,
    "business_description": "Brief description",
    "auditor": "Audit firm name",
    "audit_opinion": "unqualified/qualified/adverse/disclaimer",
    "website": "URL"
  },
  "financial_year": "2024",
  "report_date": "Date of report/financial year end",
  "fiscal_year": "2024",

  "metrics": [
    {
      "metric": "Revenue",
      "layer": "Income Statement",
      "value": 12345.6,
      "currency": "MYR",
      "unit": "millions",
      "fiscal_year": "2024",
      "period": "FY2024",
      "source_page": 87,
      "source_section": "Consolidated Income Statement",
      "reported_or_calculated": "reported",
      "definition": "Total revenue from operations",
      "confidence": 0.95
    }
  ],

  "relationships": [
    {
      "related_name": "Subsidiary Name Sdn Bhd",
      "relationship_type": "subsidiary",
      "fiscal_year": "2024",
      "stake_pct": 100.0,
      "effective_stake_pct": 100.0,
      "source_page": 45,
      "source_section": "Notes to Financial Statements"
    }
  ],

  "directors": [
    {
      "director_name": "Tan Sri Dato' Sri Name",
      "fiscal_year": "2024",
      "role": "Chairman",
      "title": "Tan Sri",
      "tenure_years": 5.0,
      "remuneration_myr": 500000,
      "independence": "independent",
      "source_page": 12,
      "source_section": "Board of Directors"
    }
  ],

  "shareholders": [
    {
      "shareholder_name": "Name",
      "fiscal_year": "2024",
      "shareholder_type": "institution",
      "stake_percent": 35.5,
      "shares_held": 1000000000,
      "is_substantial": 1,
      "is_controlling": 1,
      "source_page": 8,
      "source_section": "Analysis of Shareholdings"
    }
  ]
}

RULES:
- "metrics" array: extract ALL numeric values across ALL 16 layers:
  1. Company Profile, 2. Business Segments, 3. Income Statement, 4. Balance Sheet,
  5. Cash Flow, 6. Segment Financials, 7. Debt & Liquidity, 8. Capital Allocation,
  9. Management & Governance, 10. Risks, 11. Accounting Policies,
  12. Financial Statement Notes, 13. ESG, 14. Management Guidance,
  15. Valuation Inputs, 16. Red Flags
- Each metric must have: metric (canonical name), layer (one of 16 above), value (number or null), currency, unit, fiscal_year, period, source_page, source_section, reported_or_calculated, definition, confidence (0-1)
- "relationships": subsidiary, sister, associate, joint_venture, parent companies
- "directors": all board members with role, tenure, remuneration
- "shareholders": all substantial shareholders (>=5%) and controlling shareholders
- All monetary values in reported currency (typically MYR thousands or millions)
- Numbers are plain numbers, null if not found
- Extract for LATEST financial year primarily
- Be thorough — extract as much data as possible

ANNUAL REPORT TEXT:
"""


# ─── PDF Text Extraction ─────────────────────────────────────────────────────

def download_pdf_from_s3(s3_key, tmpdir):
    """Download PDF from S3 to local temp path."""
    local_path = os.path.join(tmpdir, os.path.basename(s3_key))
    get_s3().download_file(BUCKET, s3_key, local_path)
    return local_path


def extract_pdf_text(pdf_path, max_pages=MAX_PAGES):
    """Extract text from PDF, limited to max_pages."""
    text_parts = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                if i >= max_pages:
                    break
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
    except Exception as e:
        log.error(f"  Error reading PDF {pdf_path}: {e}")
        return ""
    return "\n\n".join(text_parts)


# ─── Bedrock API ─────────────────────────────────────────────────────────────

def call_bedrock(text):
    """Call Bedrock Nova Micro for structured extraction."""
    client = get_bedrock()

    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]

    full_prompt = EXTRACTION_PROMPT + text

    body = json.dumps({
        "messages": [
            {
                "role": "user",
                "content": [{"text": full_prompt}]
            }
        ],
        "inferenceConfig": {
            "max_new_tokens": MAX_TOKENS,
            "temperature": 0.1,
            "topP": 0.9,
        }
    })

    try:
        response = client.invoke_model(
            modelId=BEDROCK_MODEL,
            body=body,
            accept="application/json",
            contentType="application/json",
        )
        result = json.loads(response["body"].read())
        output_text = result["output"]["message"]["content"][0]["text"]

        # Strip markdown fences
        output_text = output_text.strip()
        if output_text.startswith("```"):
            output_text = re.sub(r"^```(?:json)?\s*\n?", "", output_text)
            output_text = re.sub(r"\n?```\s*$", "", output_text)

        data = json.loads(output_text)
        return data
    except json.JSONDecodeError as e:
        log.error(f"  JSON parse error: {e}")
        return {}
    except Exception as e:
        log.error(f"  Bedrock API error: {e}")
        return {}


# ─── Iceberg Write ───────────────────────────────────────────────────────────

def open_table_catalog(table_name):
    """Open catalog for a specific Iceberg table."""
    s3 = get_s3()
    tmpdir = tempfile.mkdtemp(prefix=f"klse-{table_name}-")
    local_db = os.path.join(tmpdir, "catalog.db")
    catalog_key = f"{table_name}/catalog.db"

    try:
        s3.download_file(BUCKET, catalog_key, local_db)
    except Exception:
        pass

    catalog = SqlCatalog(
        CATALOG_NAME,
        uri=f"sqlite:///{local_db}",
        warehouse=WAREHOUSE,
    )
    try:
        catalog.create_namespace(NAMESPACE)
    except Exception:
        pass

    tbl = catalog.load_table(f"{NAMESPACE}.{table_name}")
    return catalog, tbl, catalog_key, local_db, tmpdir


def save_catalog(s3, catalog_key, local_db):
    """Upload catalog back to S3."""
    s3.upload_file(local_db, BUCKET, catalog_key)


def write_metrics_to_iceberg(metrics_rows):
    """Write company_metrics rows to Iceberg."""
    if not metrics_rows:
        return 0

    catalog, tbl, catalog_key, local_db, tmpdir = open_table_catalog("company_metrics")
    try:
        # Build arrow table
        columns = {
            "symbol": [], "report_id": [], "metric": [], "layer": [],
            "value": [], "currency": [], "unit": [], "fiscal_year": [],
            "period": [], "source_page": [], "source_section": [],
            "reported_or_calculated": [], "definition": [],
            "extraction_source": [], "raw_text": [], "confidence": [],
            "extracted_at": [],
        }
        now = datetime.utcnow().isoformat()
        for r in metrics_rows:
            columns["symbol"].append(r.get("symbol", ""))
            columns["report_id"].append(r.get("report_id"))
            columns["metric"].append(r.get("metric", ""))
            columns["layer"].append(r.get("layer", ""))
            columns["value"].append(r.get("value"))
            columns["currency"].append(r.get("currency"))
            columns["unit"].append(r.get("unit"))
            columns["fiscal_year"].append(r.get("fiscal_year", ""))
            columns["period"].append(r.get("period"))
            columns["source_page"].append(r.get("source_page"))
            columns["source_section"].append(r.get("source_section"))
            columns["reported_or_calculated"].append(r.get("reported_or_calculated", "reported"))
            columns["definition"].append(r.get("definition"))
            columns["extraction_source"].append("bedrock-nova-micro")
            columns["raw_text"].append(r.get("raw_text"))
            columns["confidence"].append(r.get("confidence"))
            columns["extracted_at"].append(now)

        arrow_table = pa.table({
            "symbol": pa.array(columns["symbol"], type=pa.string()),
            "report_id": pa.array(columns["report_id"], type=pa.int32()),
            "metric": pa.array(columns["metric"], type=pa.string()),
            "layer": pa.array(columns["layer"], type=pa.string()),
            "value": pa.array(columns["value"], type=pa.float64()),
            "currency": pa.array(columns["currency"], type=pa.string()),
            "unit": pa.array(columns["unit"], type=pa.string()),
            "fiscal_year": pa.array(columns["fiscal_year"], type=pa.string()),
            "period": pa.array(columns["period"], type=pa.string()),
            "source_page": pa.array(columns["source_page"], type=pa.int32()),
            "source_section": pa.array(columns["source_section"], type=pa.string()),
            "reported_or_calculated": pa.array(columns["reported_or_calculated"], type=pa.string()),
            "definition": pa.array(columns["definition"], type=pa.string()),
            "extraction_source": pa.array(columns["extraction_source"], type=pa.string()),
            "raw_text": pa.array(columns["raw_text"], type=pa.string()),
            "confidence": pa.array(columns["confidence"], type=pa.float64()),
            "extracted_at": pa.array(columns["extracted_at"], type=pa.string()),
        })

        tbl.append(arrow_table)
        save_catalog(get_s3(), catalog_key, local_db)
        return len(metrics_rows)
    finally:
        try:
            catalog.close()
        except Exception:
            pass
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def write_relationships_to_iceberg(rows):
    """Write company_relationships rows to Iceberg."""
    if not rows:
        return 0

    catalog, tbl, catalog_key, local_db, tmpdir = open_table_catalog("company_relationships")
    try:
        now = datetime.utcnow().isoformat()
        arrow_table = pa.table({
            "symbol": pa.array([r.get("symbol", "") for r in rows], type=pa.string()),
            "related_name": pa.array([r.get("related_name", "") for r in rows], type=pa.string()),
            "relationship_type": pa.array([r.get("relationship_type", "") for r in rows], type=pa.string()),
            "fiscal_year": pa.array([r.get("fiscal_year", "") for r in rows], type=pa.string()),
            "stake_pct": pa.array([r.get("stake_pct") for r in rows], type=pa.float64()),
            "effective_stake_pct": pa.array([r.get("effective_stake_pct") for r in rows], type=pa.float64()),
            "source_page": pa.array([r.get("source_page") for r in rows], type=pa.int32()),
            "source_section": pa.array([r.get("source_section") for r in rows], type=pa.string()),
            "report_id": pa.array([r.get("report_id") for r in rows], type=pa.int32()),
            "extracted_at": pa.array([now] * len(rows), type=pa.string()),
        })
        tbl.append(arrow_table)
        save_catalog(get_s3(), catalog_key, local_db)
        return len(rows)
    finally:
        try:
            catalog.close()
        except Exception:
            pass
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def write_directors_to_iceberg(rows):
    """Write company_directors rows to Iceberg."""
    if not rows:
        return 0

    catalog, tbl, catalog_key, local_db, tmpdir = open_table_catalog("company_directors")
    try:
        now = datetime.utcnow().isoformat()
        arrow_table = pa.table({
            "symbol": pa.array([r.get("symbol", "") for r in rows], type=pa.string()),
            "director_name": pa.array([r.get("director_name", "") for r in rows], type=pa.string()),
            "fiscal_year": pa.array([r.get("fiscal_year", "") for r in rows], type=pa.string()),
            "role": pa.array([r.get("role") for r in rows], type=pa.string()),
            "title": pa.array([r.get("title") for r in rows], type=pa.string()),
            "tenure_years": pa.array([r.get("tenure_years") for r in rows], type=pa.float64()),
            "remuneration_mym_real": pa.array([r.get("remuneration_myr") for r in rows], type=pa.float64()),
            "independence": pa.array([r.get("independence") for r in rows], type=pa.string()),
            "source_page": pa.array([r.get("source_page") for r in rows], type=pa.int32()),
            "source_section": pa.array([r.get("source_section") for r in rows], type=pa.string()),
            "report_id": pa.array([r.get("report_id") for r in rows], type=pa.int32()),
            "extracted_at": pa.array([now] * len(rows), type=pa.string()),
        })
        tbl.append(arrow_table)
        save_catalog(get_s3(), catalog_key, local_db)
        return len(rows)
    finally:
        try:
            catalog.close()
        except Exception:
            pass
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def write_shareholders_to_iceberg(rows):
    """Write company_shareholders rows to Iceberg."""
    if not rows:
        return 0

    catalog, tbl, catalog_key, local_db, tmpdir = open_table_catalog("company_shareholders")
    try:
        now = datetime.utcnow().isoformat()
        arrow_table = pa.table({
            "symbol": pa.array([r.get("symbol", "") for r in rows], type=pa.string()),
            "shareholder_name": pa.array([r.get("shareholder_name", "") for r in rows], type=pa.string()),
            "fiscal_year": pa.array([r.get("fiscal_year", "") for r in rows], type=pa.string()),
            "shareholder_type": pa.array([r.get("shareholder_type") for r in rows], type=pa.string()),
            "stake_percent": pa.array([r.get("stake_percent") for r in rows], type=pa.float64()),
            "shares_held": pa.array([r.get("shares_held") for r in rows], type=pa.float64()),
            "is_substantial": pa.array([r.get("is_substantial", 0) for r in rows], type=pa.int32()),
            "is_controlling": pa.array([r.get("is_controlling", 0) for r in rows], type=pa.int32()),
            "source_page": pa.array([r.get("source_page") for r in rows], type=pa.int32()),
            "source_section": pa.array([r.get("source_section") for r in rows], type=pa.string()),
            "report_id": pa.array([r.get("report_id") for r in rows], type=pa.int32()),
            "extracted_at": pa.array([now] * len(rows), type=pa.string()),
        })
        tbl.append(arrow_table)
        save_catalog(get_s3(), catalog_key, local_db)
        return len(rows)
    finally:
        try:
            catalog.close()
        except Exception:
            pass
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def mark_report_processed(s3_key, symbol, title, fiscal_year):
    """Mark a PDF as processed in annual_reports Iceberg table."""
    catalog, tbl, catalog_key, local_db, tmpdir = open_table_catalog("annual_reports")
    try:
        now = datetime.utcnow().isoformat()
        arrow_table = pa.table({
            "symbol": pa.array([symbol], type=pa.string()),
            "title": pa.array([title], type=pa.string()),
            "pdf_url": pa.array([f"s3://{BUCKET}/{s3_key}"], type=pa.string()),
            "file_size": pa.array([0], type=pa.int32()),
            "processed": pa.array([1], type=pa.int32()),
            "downloaded_at": pa.array([now], type=pa.string()),
            "created_at": pa.array([now], type=pa.string()),
            "s3_key": pa.array([s3_key], type=pa.string()),
            "fiscal_year": pa.array([fiscal_year or ""], type=pa.string()),
        })
        tbl.append(arrow_table)
        save_catalog(get_s3(), catalog_key, local_db)
    finally:
        try:
            catalog.close()
        except Exception:
            pass
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─── Process Single PDF ──────────────────────────────────────────────────────

def process_single_pdf(s3_key, symbol="UNKNOWN", title="", report_id=None):
    """Process a single PDF: download, extract, call Bedrock, write to Iceberg."""
    log.info(f"Processing: {s3_key}")

    with tempfile.TemporaryDirectory(prefix="klse-pdf-") as tmpdir:
        # Download
        try:
            pdf_path = download_pdf_from_s3(s3_key, tmpdir)
        except Exception as e:
            log.error(f"  Failed to download: {e}")
            return False

        # Extract text
        text = extract_pdf_text(pdf_path)
        if not text:
            log.warning(f"  No text extracted from {s3_key}")
            return False
        log.info(f"  Extracted {len(text)} chars from PDF")

        # Call Bedrock
        data = call_bedrock(text)
        if not data:
            log.warning(f"  No data returned from Bedrock for {s3_key}")
            return False

        fiscal_year = data.get("fiscal_year") or data.get("financial_year") or ""

        # Write metrics
        metrics = data.get("metrics", [])
        if metrics:
            for m in metrics:
                m["symbol"] = symbol
                m["report_id"] = report_id
                if not m.get("fiscal_year"):
                    m["fiscal_year"] = fiscal_year
            write_metrics_to_iceberg(metrics)
            log.info(f"  Wrote {len(metrics)} metrics")

        # Write relationships
        relationships = data.get("relationships", [])
        if relationships:
            for r in relationships:
                r["symbol"] = symbol
                r["report_id"] = report_id
                if not r.get("fiscal_year"):
                    r["fiscal_year"] = fiscal_year
            write_relationships_to_iceberg(relationships)
            log.info(f"  Wrote {len(relationships)} relationships")

        # Write directors
        directors = data.get("directors", [])
        if directors:
            for d in directors:
                d["symbol"] = symbol
                d["report_id"] = report_id
                if not d.get("fiscal_year"):
                    d["fiscal_year"] = fiscal_year
            write_directors_to_iceberg(directors)
            log.info(f"  Wrote {len(directors)} directors")

        # Write shareholders
        shareholders = data.get("shareholders", [])
        if shareholders:
            for s in shareholders:
                s["symbol"] = symbol
                s["report_id"] = report_id
                if not s.get("fiscal_year"):
                    s["fiscal_year"] = fiscal_year
            write_shareholders_to_iceberg(shareholders)
            log.info(f"  Wrote {len(shareholders)} shareholders")

        # Mark as processed
        mark_report_processed(s3_key, symbol, title, fiscal_year)
        log.info(f"  Marked as processed (FY: {fiscal_year})")

        return True


# ─── List PDFs in S3 ─────────────────────────────────────────────────────────

def list_s3_pdfs():
    """List all PDFs in the S3 prefix."""
    s3 = get_s3()
    paginator = s3.get_paginator("list_objects_v2")
    pdfs = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=S3_PREFIX):
        for obj in page.get("Contents", []):
            if obj["Key"].lower().endswith(".pdf"):
                pdfs.append(obj["Key"])
    return sorted(pdfs)


def get_processed_s3_keys():
    """Get set of already-processed S3 keys from annual_reports Iceberg table."""
    try:
        catalog, tbl, _, _, tmpdir = open_table_catalog("annual_reports")
        try:
            arrow = tbl.scan(selected_fields=("s3_key",)).to_arrow()
            keys = arrow.column("s3_key").to_pylist()
            return {k for k in keys if k}
        finally:
            catalog.close()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        return set()


def get_pending_annual_reports(db_path=None):
    """Get annual reports with status='pending' from SQLite."""
    from data.db import get_conn
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, symbol, title, pdf_url, local_path, s3_key FROM annual_reports WHERE status = 'pending' AND s3_key IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]


def mark_annual_report_processed(report_id, status='processed', fiscal_year=None, db_path=None):
    """Update annual_reports status in SQLite."""
    from data.db import get_conn
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE annual_reports SET status = ?, fiscal_year = ?, processed = 1 WHERE id = ?",
            (status, fiscal_year, report_id)
        )


# ─── Main Pipeline ────────────────────────────────────────────────────────────

def run_extraction(limit=None, delay=1.0, db_path=None):
    """Run Bedrock extraction on pending annual reports."""
    log.info("=" * 60)
    log.info("Stage 3: Bedrock Extraction → Iceberg")

    # Get pending reports from SQLite
    pending = get_pending_annual_reports(db_path)
    log.info(f"Found {len(pending)} pending annual reports in DB")

    if limit:
        pending = pending[:limit]
        log.info(f"Limited to first {limit} PDFs")

    success = 0
    errors = 0

    for i, report in enumerate(pending):
        s3_key = report.get("s3_key")
        symbol = report.get("symbol", "UNKNOWN")
        title = report.get("title", "")
        report_id = report.get("id")

        if not s3_key:
            log.warning(f"  Skipping report id={report_id}: no s3_key")
            continue

        log.info(f"\n[{i+1}/{len(pending)}] {s3_key} ({symbol})")
        try:
            result = process_single_pdf(s3_key, symbol=symbol, title=title, report_id=report_id)
            if result:
                success += 1
                # Update status in DB
                mark_annual_report_processed(report_id, status='processed', db_path=db_path)
            else:
                errors += 1
                mark_annual_report_processed(report_id, status='failed', db_path=db_path)
        except Exception as e:
            log.error(f"  ERROR: {e}")
            errors += 1
            mark_annual_report_processed(report_id, status='failed', db_path=db_path)

        # Log progress every batch
        if (i + 1) % BATCH_SIZE == 0:
            log.info(f"--- Batch complete: {i+1}/{len(pending)} processed (success={success}, errors={errors}) ---")

        time.sleep(delay)

    log.info("=" * 60)
    log.info(f"Extraction complete: {success} success, {errors} errors out of {len(pending)}")
    return success, errors


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Bedrock extraction → Iceberg")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of PDFs to process")
    ap.add_argument("--delay", type=float, default=1.0, help="Delay between API calls (seconds)")
    ap.add_argument("--db-path", type=str, default=None, help="SQLite DB path")
    args = ap.parse_args()
    run_extraction(limit=args.limit, delay=args.delay, db_path=args.db_path)
