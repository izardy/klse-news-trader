# Annual Report → Structured Company Metrics Extraction
## Design Spec (2026-09-10)

Extract financial/company data from the downloaded Bursa annual-report PDFs
(`data/pdfs/`) into a granular, traceable table using a Bedrock LLM.

## Extraction Layers (15)
Per company, extract into these layers:
1. Company Profile
2. Business Segments
3. Income Statement
4. Balance Sheet
5. Cash Flow
6. Segment Financials
7. Debt & Liquidity
8. Capital Allocation
9. Management & Governance
10. Risks
11. Accounting Policies
12. Financial Statement Notes
13. ESG
14. Management Guidance
15. Valuation Inputs
16. Red Flags

## Storage Schema (metric-level, one row per metric instance)
| column | example |
|---|---|
| metric | Revenue |
| value | 12450 |
| currency | USD |
| unit | millions |
| fiscal_year | 2025 |
| period | FY2025 / Q1 2025 / 31-Dec-2025 |
| source_page | 87 |
| source_section | Consolidated Statements of Operations |
| reported_or_calculated | reported |
| definition | Revenue generated from ... |

### Design principles
- **metric**: canonical metric name (normalized, e.g. "Revenue", "Net Income", "Total Assets", "Total Debt", "ROE", "Free Cash Flow")
- **value**: numeric only
- **reported_or_calculated**: `reported` = directly from report; `calculated` = derived by model (e.g. ROE = Net Income/Equity, FCF = OCF - Capex)
- **source_page**: exact PDF page for traceability/re-audit
- **source_section**: the report section where it came from
- **unit**: millions / thousands / raw / percent / x (ratio)
- A company-year = many rows (one per metric); dimensional querying by (company, fiscal_year, metric)