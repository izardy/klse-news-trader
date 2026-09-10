"""Seed metric_meta with canonical metrics grouped by the 16 extraction layers.
Run: python -m scripts.seed_metric_meta   (from klse-news-trader root)

This defines the controlled vocabulary used when extracting annual-report PDFs
into company_metrics. Keeps metric names normalized across companies/years.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LAYERS = [
    "Company Profile", "Business Segments", "Income Statement", "Balance Sheet",
    "Cash Flow", "Segment Financials", "Debt & Liquidity", "Capital Allocation",
    "Management & Governance", "Risks", "Accounting Policies", "Financial Statement Notes",
    "ESG", "Management Guidance", "Valuation Inputs", "Red Flags",
    "Group Structure",
]

# metric -> (layer, unit_hint, reported_or_calculated, is_ratio, definition)
METRICS = {
    # Company Profile
    "Company Name": ("Company Profile", "raw", "reported", 0, "Registered legal name"),
    "Financial Year End": ("Company Profile", "raw", "reported", 0, "Fiscal year end date"),
    "Sector": ("Company Profile", "raw", "reported", 0, "Bursa sector classification"),
    "Industry": ("Company Profile", "raw", "reported", 0, "Sub-industry classification"),
    "Incorporation Date": ("Company Profile", "raw", "reported", 0, "Company incorporation date"),
    "Employee Count": ("Company Profile", "raw", "reported", 0, "Number of employees"),
    "Headquarters": ("Company Profile", "raw", "reported", 0, "Registered head office location"),
    "Auditor": ("Company Profile", "raw", "reported", 0, "External auditor name"),
    "Audit Opinion": ("Company Profile", "raw", "reported", 0, "Unqualified/qualified/etc."),
    "Fiscal Year Covered": ("Company Profile", "raw", "reported", 0, "Period covered by this report"),
    # Business Segments
    "Primary Business": ("Business Segments", "raw", "reported", 0, "Main revenue-generating activities"),
    "Segment Description": ("Business Segments", "raw", "reported", 0, "How segments are organized"),
    # Income Statement
    "Revenue": ("Income Statement", "millions", "reported", 0, "Total revenue/sales"),
    "Cost of Goods Sold": ("Income Statement", "millions", "reported", 0, "COGS"),
    "Gross Profit": ("Income Statement", "millions", "reported", 0, "Revenue - COGS"),
    "Gross Profit Margin": ("Income Statement", "percent", "calculated", 1, "Gross Profit / Revenue"),
    "Operating Expenses": ("Income Statement", "millions", "reported", 0, "SG&A + R&D + other opex"),
    "EBIT": ("Income Statement", "millions", "reported", 0, "Earnings before interest and tax"),
    "Operating Income": ("Income Statement", "millions", "reported", 0, "Operating profit"),
    "EBITDA": ("Income Statement", "millions", "reported", 0, "EBIT + D&A"),
    "Interest Expense": ("Income Statement", "millions", "reported", 0, "Net finance costs"),
    "Profit Before Tax": ("Income Statement", "millions", "reported", 0, "Pretax income"),
    "Tax Expense": ("Income Statement", "millions", "reported", 0, "Income tax"),
    "Net Income": ("Income Statement", "millions", "reported", 0, "Profit after tax"),
    "Net Profit Margin": ("Income Statement", "percent", "calculated", 1, "Net Income / Revenue"),
    "EPS": ("Income Statement", "raw", "reported", 0, "Earnings per share (sen/MYR)"),
    "Diluted EPS": ("Income Statement", "raw", "reported", 0, "Fully diluted EPS"),
    "Operating Margin": ("Income Statement", "percent", "calculated", 1, "EBIT / Revenue"),
    # Balance Sheet
    "Total Assets": ("Balance Sheet", "millions", "reported", 0, "Total assets"),
    "Total Liabilities": ("Balance Sheet", "millions", "reported", 0, "Total liabilities"),
    "Total Equity": ("Balance Sheet", "millions", "reported", 0, "Shareholders' equity"),
    "Cash and Equivalents": ("Balance Sheet", "millions", "reported", 0, "Cash + equivalents"),
    "Inventory": ("Balance Sheet", "millions", "reported", 0, "Closing inventory"),
    "Receivables": ("Balance Sheet", "millions", "reported", 0, "Trade receivables"),
    "Payables": ("Balance Sheet", "millions", "reported", 0, "Trade payables"),
    "PPE": ("Balance Sheet", "millions", "reported", 0, "Property plant equipment"),
    "Goodwill": ("Balance Sheet", "millions", "reported", 0, "Goodwill intangible"),
    "Intangible Assets": ("Balance Sheet", "millions", "reported", 0, "Excl. goodwill"),
    "Current Assets": ("Balance Sheet", "millions", "reported", 0, "Total current assets"),
    "Current Liabilities": ("Balance Sheet", "millions", "reported", 0, "Total current liabilities"),
    "Short-term Debt": ("Balance Sheet", "millions", "reported", 0, "Current portion of debt"),
    "Long-term Debt": ("Balance Sheet", "millions", "reported", 0, "Non-current borrowings"),
    # Cash Flow
    "Operating Cash Flow": ("Cash Flow", "millions", "reported", 0, "Cash from operations"),
    "Investing Cash Flow": ("Cash Flow", "millions", "reported", 0, "Cash from investing"),
    "Financing Cash Flow": ("Cash Flow", "millions", "reported", 0, "Cash from financing"),
    "Capital Expenditure": ("Cash Flow", "millions", "reported", 0, "Capex (additions to PPE)"),
    "Free Cash Flow": ("Cash Flow", "millions", "calculated", 0, "OCF - Capex"),
    "Net Change in Cash": ("Cash Flow", "millions", "reported", 0, "Net cash movement"),
    "Cash Conversion Cycle": ("Cash Flow", "days", "calculated", 1, "DSO + DIO - DPO"),
    # Segment Financials
    "Segment Revenue": ("Segment Financials", "millions", "reported", 0, "Revenue by segment"),
    "Segment Profit": ("Segment Financials", "millions", "reported", 0, "Profit by segment"),
    "Segment Assets": ("Segment Financials", "millions", "reported", 0, "Assets by segment"),
    "Segment Margin": ("Segment Financials", "percent", "calculated", 1, "Segment profit / revenue"),
    # Debt & Liquidity
    "Total Debt": ("Debt & Liquidity", "millions", "reported", 0, "ST debt + LT debt"),
    "Net Debt": ("Debt & Liquidity", "millions", "calculated", 0, "Total debt - cash"),
    "Debt to Equity": ("Debt & Liquidity", "x", "calculated", 1, "Total debt / equity"),
    "Debt to Assets": ("Debt & Liquidity", "x", "calculated", 1, "Total debt / assets"),
    "Current Ratio": ("Debt & Liquidity", "x", "calculated", 1, "Current assets / current liabilities"),
    "Quick Ratio": ("Debt & Liquidity", "x", "calculated", 1, "(CA - inventory) / CL"),
    "Interest Coverage": ("Debt & Liquidity", "x", "calculated", 1, "EBIT / interest expense"),
    # Capital Allocation
    "Dividend Per Share": ("Capital Allocation", "raw", "reported", 0, "DPS (sen/MYR)"),
    "Dividend Payout Ratio": ("Capital Allocation", "percent", "calculated", 1, "Dividends / net income"),
    "Share Buyback Amount": ("Capital Allocation", "millions", "reported", 0, "Shares repurchased"),
    "Capital Return": ("Capital Allocation", "millions", "calculated", 0, "Dividends + buybacks"),
    # Management & Governance
    "Board Size": ("Management & Governance", "raw", "reported", 0, "Number of directors"),
    "Independent Directors": ("Management & Governance", "raw", "reported", 0, "Count of independents"),
    "Independent Director Ratio": ("Management & Governance", "percent", "calculated", 1, "Independent / board size"),
    "Executive Chairman": ("Management & Governance", "raw", "reported", 0, "Yes/No/Name"),
    "CEO Name": ("Management & Governance", "raw", "reported", 0, "Chief executive name"),
    "Top Executives": ("Management & Governance", "raw", "reported", 0, "Key management names"),
    "Key Management Remuneration": ("Management & Governance", "millions", "reported", 0, "Total KMP compensation"),
    # Risks
    "Risk Factor": ("Risks", "raw", "reported", 0, "Identified risk description"),
    "Risk Count": ("Risks", "raw", "reported", 0, "Number of disclosed risks"),
    "Contingent Liability": ("Risks", "raw", "reported", 0, "Disclosed contingent liability"),
    # Accounting Policies
    "Revenue Recognition": ("Accounting Policies", "raw", "reported", 0, "Key revenue policy"),
    "Depreciation Method": ("Accounting Policies", "raw", "reported", 0, "e.g. straight-line"),
    "Inventory Valuation": ("Accounting Policies", "raw", "reported", 0, "e.g. weighted-average"),
    "Lease Accounting": ("Accounting Policies", "raw", "reported", 0, "IFRS16 adoption note"),
    # Financial Statement Notes
    "Related Party Transactions": ("Financial Statement Notes", "raw", "reported", 0, "RPT disclosure"),
    "Subsequent Events": ("Financial Statement Notes", "raw", "reported", 0, "Post-report events"),
    "Off-Balance Sheet": ("Financial Statement Notes", "raw", "reported", 0, "Off-balance-sheet items"),
    # ESG
    "Carbon Emissions Scope1": ("ESG", "raw", "reported", 0, "Scope 1 tCO2e"),
    "Carbon Emissions Scope2": ("ESG", "raw", "reported", 0, "Scope 2 tCO2e"),
    "Renewable Energy Share": ("ESG", "percent", "reported", 1, "RE as % of usage"),
    "Employee Turnover": ("ESG", "percent", "reported", 1, "Staff turnover rate"),
    "Female Board Ratio": ("ESG", "percent", "reported", 1, "Women on board %"),
    "Safety Incidents": ("ESG", "raw", "reported", 0, "Lost time injury count"),
    # Management Guidance
    "Guidance": ("Management Guidance", "raw", "reported", 0, "Forward-looking management statement"),
    "Revenue Guidance": ("Management Guidance", "raw", "reported", 0, "Expected revenue outlook"),
    "Expansion Plans": ("Management Guidance", "raw", "reported", 0, "Capex/dividend/business plans"),
    # Valuation Inputs
    "Share Count": ("Valuation Inputs", "raw", "reported", 0, "Total shares outstanding"),
    "Share Price": ("Valuation Inputs", "raw", "reported", 0, "Reference share price (if given)"),
    "Book Value Per Share": ("Valuation Inputs", "raw", "calculated", 0, "Equity / shares"),
    # Red Flags
    "Going Concern Warning": ("Red Flags", "raw", "reported", 0, "Going concern uncertainty"),
    "Qualified Opinion": ("Red Flags", "raw", "reported", 0, "Non-unqualified audit opinion"),
    "Related Party Risk": ("Red Flags", "raw", "reported", 0, "Significant RPT dependency"),
    "Unusual Auditor Change": ("Red Flags", "raw", "reported", 0, "Auditor resignation/change"),
    "Negative FCF": ("Red Flags", "raw", "calculated", 0, "Free cash flow negative"),
    # Group Structure / Sister Companies (added per user request)
    "Subsidiary": ("Group Structure", "raw", "reported", 0, "Direct/indirect subsidiary name + stake %"),
    "Sister Company": ("Group Structure", "raw", "reported", 0, "Related company under same parent/promoter"),
    "Associate": ("Group Structure", "raw", "reported", 0, "Associate company (20-50% stake)"),
    "Joint Venture": ("Group Structure", "raw", "reported", 0, "JV partner + stake %"),
    "Parent Company": ("Group Structure", "raw", "reported", 0, "Ultimate/immediate parent + stake %"),
    "Subsidiary Count": ("Group Structure", "raw", "reported", 0, "Number of subsidiaries"),
    "Subsidiary Stake": ("Group Structure", "percent", "reported", 1, "Group's effective ownership of subsidiary"),
    "Principal Subsidiary": ("Group Structure", "raw", "reported", 0, "Major/most significant subsidiary"),
    # People / Governance Identity (added per user request)
    "Director Name": ("Management & Governance", "raw", "reported", 0, "Board member full name"),
    "Director Role": ("Management & Governance", "raw", "reported", 0, "Chairman/ED/NED/CEO/etc."),
    "Director Tenure": ("Management & Governance", "raw", "reported", 0, "Years on board"),
    "Director Remuneration": ("Management & Governance", "millions", "reported", 0, "Annual director fee"),
    "Major Shareholder": ("Management & Governance", "raw", "reported", 0, "Shareholder + stake %"),
    "Shareholder Stake": ("Management & Governance", "percent", "reported", 1, "Individual/institutional holding %"),
    "Top 5 Shareholders": ("Management & Governance", "raw", "reported", 0, "Top shareholders list"),
    "Substantial Shareholder": ("Management & Governance", "raw", "reported", 0, "Holder with >=5% stake"),
}


def seed_metric_meta(db_path=None):
    from data.db import get_conn
    with get_conn(db_path) as conn:
        for metric, (layer, unit, roc, is_ratio, definition) in METRICS.items():
            conn.execute(
                "INSERT OR REPLACE INTO metric_meta (metric, layer, definition, unit_hint, reported_or_calculated, is_ratio) VALUES (?,?,?,?,?,?)",
                (metric, layer, definition, unit, roc, is_ratio)
            )
        conn.commit()
    print(f"Seeded {len(METRICS)} metrics across {len(LAYERS)} layers")


def validate_layers():
    """Ensure every seeded metric maps to a valid layer."""
    bad = [m for m, (l, *_ ) in METRICS.items() if l not in LAYERS]
    if bad:
        print(f"WARN: metrics with invalid layer: {bad}")
    else:
        print(f"All {len(METRICS)} metrics map to valid layers")


if __name__ == "__main__":
    seed_metric_meta()
    validate_layers()