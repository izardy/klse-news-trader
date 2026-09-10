"""
Annual report PDF processing pipeline.
1. Extract text from PDFs using pdfplumber
2. Call AWS Bedrock (Nova Micro) to extract structured financial data
3. Store in SQLite tables: company_profile, company_fundamentals, segment_breakdown, risk_factors, dividend_history
"""
import os
import sys
import json
import re
import time
import boto3
import pdfplumber

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import (
    get_unprocessed_reports, mark_report_processed,
    upsert_company_profile, upsert_fundamentals,
    insert_segment, insert_risk_factor, insert_dividend,
    get_table_counts,
)

# Bedrock config
BEDROCK_REGION = "ap-southeast-1"
BEDROCK_MODEL = "apac.amazon.nova-micro-v1:0"  # cross-region inference profile
MAX_TOKENS = 8000

# Bedrock client (reused across calls)
_bedrock_client = None


def get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _bedrock_client


# ─── Extraction prompt ─────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """You are a financial data extraction expert. Analyze the following annual report text from a Malaysian public listed company (Bursa Malaysia).

Extract ALL available structured financial data and return it as a valid JSON object. Use null for any field that cannot be found.

Return ONLY valid JSON, no markdown, no explanation. The JSON must follow this exact structure:

{
  "company_info": {
    "company_name": "Full legal name",
    "sector": "e.g. Banking, Plantation, Oil & Gas, Telecommunications, Healthcare, Property, Consumer, Industrial, Technology, Gaming",
    "industry": "More specific industry classification",
    "incorporation_date": "Date of incorporation",
    "employee_count": 0,
    "business_description": "Brief description of main business activities",
    "auditor": "Name of audit firm",
    "audit_opinion": "unqualified/qualified/adverse/disclaimer",
    "website": "Company website URL"
  },
  "financial_year": "2024",
  "report_date": "Date of the report or financial year end",
  "income_statement": {
    "revenue": 0,
    "cost_of_goods_sold": 0,
    "gross_profit": 0,
    "operating_expenses": 0,
    "depreciation": 0,
    "amortization": 0,
    "operating_profit": 0,
    "ebitda": 0,
    "interest_expense": 0,
    "profit_before_tax": 0,
    "tax_expense": 0,
    "net_profit": 0,
    "eps": 0
  },
  "balance_sheet": {
    "total_assets": 0,
    "total_liabilities": 0,
    "total_equity": 0,
    "cash_and_equivalents": 0,
    "inventory": 0,
    "receivables": 0,
    "payables": 0,
    "property_plant_equipment": 0,
    "intangible_assets": 0,
    "current_assets": 0,
    "current_liabilities": 0
  },
  "cash_flow": {
    "operating_cash_flow": 0,
    "investing_cash_flow": 0,
    "financing_cash_flow": 0,
    "capex": 0,
    "free_cash_flow": 0
  },
  "key_ratios": {
    "roe": 0,
    "roa": 0,
    "debt_to_equity": 0,
    "current_ratio": 0,
    "quick_ratio": 0,
    "interest_coverage": 0,
    "gross_margin": 0,
    "operating_margin": 0,
    "net_margin": 0
  },
  "segments": [
    {
      "segment_type": "business or geography",
      "segment_name": "Name of segment",
      "segment_revenue": 0,
      "segment_profit": 0,
      "segment_assets": 0,
      "revenue_percentage": 0
    }
  ],
  "risks": [
    {
      "risk_category": "market/credit/operational/regulatory/liquidity/foreign_exchange/interest_rate/compliance/technology/other",
      "risk_description": "Description of the risk"
    }
  ],
  "dividends": [
    {
      "dividend_type": "interim/final/special/total",
      "dividend_per_share": 0,
      "dividend_yield": 0,
      "payout_ratio": 0,
      "ex_date": "Ex-dividend date",
      "payment_date": "Payment date"
    }
  ],
  "related_party_transactions": [
    "Description of significant related party transaction"
  ],
  "contingent_liabilities": [
    "Description of contingent liability"
  ],
  "subsequent_events": [
    "Description of significant event after reporting period"
  ]
}

IMPORTANT:
- All monetary values should be in the REPORTED CURRENCY (typically MYR thousands or millions). Keep the unit consistent.
- Numbers should be plain numbers, not strings. Use null if not found.
- Extract data for the LATEST financial year primarily. If multi-year data is available, use the most recent.
- For segments, include ALL business segments and geographic segments mentioned.
- For risks, extract from the "Risk Factors" or "Statement of Risk" section.
- Be thorough - extract as much data as possible from the text provided.

ANNUAL REPORT TEXT:
"""


def extract_pdf_text(pdf_path: str, max_pages: int = 50) -> str:
    """Extract text from a PDF file. Limit pages to avoid token overflow."""
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
        print(f"  Error reading PDF {pdf_path}: {e}")
        return ""
    return "\n\n".join(text_parts)


def call_bedrock_extraction(text: str) -> dict:
    """Call AWS Bedrock Nova Micro to extract structured financial data."""
    client = get_bedrock_client()

    # Truncate text to fit within token limits (~40K chars for Nova Micro)
    max_chars = 40000
    if len(text) > max_chars:
        text = text[:max_chars]

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

        # Parse JSON from response
        # Handle potential markdown code fences
        output_text = output_text.strip()
        if output_text.startswith("```"):
            output_text = re.sub(r"^```(?:json)?\s*\n?", "", output_text)
            output_text = re.sub(r"\n?```\s*$", "", output_text)

        data = json.loads(output_text)
        return data
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}")
        print(f"  Raw response (first 500 chars): {output_text[:500]}")
        return {}
    except Exception as e:
        print(f"  Bedrock API error: {e}")
        return {}


def safe_float(val) -> float:
    """Safely convert a value to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def safe_int(val) -> int:
    """Safely convert a value to int."""
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def store_extraction(symbol: str, data: dict, db_path: str = None):
    """Store extracted data in SQLite tables."""
    if not data:
        return

    # 1. Company profile
    info = data.get("company_info", {})
    if info:
        profile_fields = {}
        if info.get("company_name"):
            profile_fields["company_name"] = info["company_name"]
        if info.get("sector"):
            profile_fields["sector"] = info["sector"]
        if info.get("industry"):
            profile_fields["industry"] = info["industry"]
        if info.get("incorporation_date"):
            profile_fields["incorporation_date"] = info["incorporation_date"]
        if info.get("employee_count"):
            profile_fields["employee_count"] = safe_int(info["employee_count"])
        if info.get("business_description"):
            profile_fields["business_description"] = info["business_description"]
        if info.get("auditor"):
            profile_fields["auditor"] = info["auditor"]
        if info.get("audit_opinion"):
            profile_fields["audit_opinion"] = info["audit_opinion"]
        if info.get("website"):
            profile_fields["website"] = info["website"]
        if profile_fields:
            upsert_company_profile(symbol, profile_fields, db_path=db_path)

    # 2. Fundamentals
    fy = data.get("financial_year", "")
    if not fy:
        # Try to extract from report_date
        rd = data.get("report_date", "")
        if rd:
            year_match = re.search(r"20\d{2}", rd)
            if year_match:
                fy = year_match.group()

    if fy:
        fund_fields = {}
        if data.get("report_date"):
            fund_fields["report_date"] = data["report_date"]

        # Income statement
        inc = data.get("income_statement", {})
        for key in ["revenue", "cost_of_goods_sold", "gross_profit", "operating_expenses",
                     "depreciation", "amortization", "operating_profit", "ebitda",
                     "interest_expense", "profit_before_tax", "tax_expense", "net_profit", "eps"]:
            if inc.get(key) is not None:
                fund_fields[key] = safe_float(inc[key])

        # Balance sheet
        bs = data.get("balance_sheet", {})
        for key in ["total_assets", "total_liabilities", "total_equity",
                     "cash_and_equivalents", "inventory", "receivables", "payables",
                     "property_plant_equipment", "intangible_assets",
                     "current_assets", "current_liabilities"]:
            if bs.get(key) is not None:
                fund_fields[key] = safe_float(bs[key])

        # Cash flow
        cf = data.get("cash_flow", {})
        for key in ["operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
                     "capex", "free_cash_flow"]:
            if cf.get(key) is not None:
                fund_fields[key] = safe_float(cf[key])

        # Ratios
        ratios = data.get("key_ratios", {})
        for key in ["roe", "roa", "debt_to_equity", "current_ratio", "quick_ratio",
                     "interest_coverage", "gross_margin", "operating_margin", "net_margin"]:
            if ratios.get(key) is not None:
                fund_fields[key] = safe_float(ratios[key])

        # Store raw extraction
        fund_fields["raw_extraction"] = json.dumps(data)

        if len(fund_fields) > 1:  # more than just raw_extraction
            upsert_fundamentals(symbol, str(fy), fund_fields, db_path=db_path)

    # 3. Segments
    segments = data.get("segments", [])
    for seg in segments:
        if seg.get("segment_name"):
            insert_segment(
                symbol=symbol,
                financial_year=str(fy),
                segment_type=seg.get("segment_type"),
                segment_name=seg["segment_name"],
                revenue=safe_float(seg.get("segment_revenue")),
                profit=safe_float(seg.get("segment_profit")),
                assets=safe_float(seg.get("segment_assets")),
                revenue_pct=safe_float(seg.get("revenue_percentage")),
                db_path=db_path,
            )

    # 4. Risk factors
    risks = data.get("risks", [])
    for risk in risks:
        if risk.get("risk_description"):
            insert_risk_factor(
                symbol=symbol,
                financial_year=str(fy),
                risk_category=risk.get("risk_category", "other"),
                risk_description=risk["risk_description"],
                db_path=db_path,
            )

    # 5. Dividends
    dividends = data.get("dividends", [])
    for div in dividends:
        insert_dividend(
            symbol=symbol,
            financial_year=str(fy),
            dividend_type=div.get("dividend_type"),
            dps=safe_float(div.get("dividend_per_share")),
            dividend_yield=safe_float(div.get("dividend_yield")),
            payout_ratio=safe_float(div.get("payout_ratio")),
            ex_date=div.get("ex_date"),
            payment_date=div.get("payment_date"),
            db_path=db_path,
        )


def process_single_report(report: dict, db_path: str = None) -> bool:
    """Process a single annual report. Returns True on success."""
    pdf_path = report.get("local_path")
    symbol = report["symbol"]
    report_id = report["id"]

    if not pdf_path or not os.path.exists(pdf_path):
        print(f"  PDF not found: {pdf_path}")
        return False

    print(f"  Extracting text from PDF ({os.path.getsize(pdf_path) / 1024:.0f} KB)...", end=" ", flush=True)
    text = extract_pdf_text(pdf_path, max_pages=50)
    if not text:
        print("no text extracted")
        return False
    print(f"{len(text)} chars")

    print(f"  Calling Bedrock for extraction...", end=" ", flush=True)
    data = call_bedrock_extraction(text)
    if not data:
        print("no data returned")
        return False
    print(f"OK (FY: {data.get('financial_year', 'unknown')})")

    # Store in DB
    store_extraction(symbol, data, db_path=db_path)

    # Mark as processed
    mark_report_processed(report_id, db_path=db_path)

    return True


def process_all_reports(db_path: str = None, delay: float = 1.0) -> int:
    """Process all unprocessed annual reports. Returns count of successfully processed."""
    reports = get_unprocessed_reports(db_path=db_path)
    if not reports:
        print("No unprocessed reports found.")
        return 0

    print(f"=== Processing {len(reports)} annual reports ===")
    success = 0
    for i, report in enumerate(reports):
        print(f"\n[{i+1}/{len(reports)}] {report['symbol']}: {report['title'][:60]}")
        try:
            if process_single_report(report, db_path=db_path):
                success += 1
        except Exception as e:
            print(f"  ERROR: {e}")
        time.sleep(delay)

    print(f"\n=== Processing complete: {success}/{len(reports)} reports processed ===")
    return success


if __name__ == "__main__":
    from data.db import init_db
    init_db()
    process_all_reports()
