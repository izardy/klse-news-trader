"""
Database schema and CRUD functions for KLSE News Trader.
Tables: stocks, prices, news, news_sentiment, ml_features, recommendations, annual_reports
"""
import sqlite3
import os
from contextlib import contextmanager
from typing import Optional

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "klse.db")
DB_PATH = os.environ.get("DB_PATH", _DEFAULT_DB)

SCHEMA_SQL = """
-- Stock universe master table
CREATE TABLE IF NOT EXISTS stocks (
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sector TEXT,
    bursa_code TEXT,             -- Bursa Malaysia company code for announcements lookup
    is_active INTEGER DEFAULT 1,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Price data (OHLCV daily bars)
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,          -- YYYY-MM-DD
    open REAL,
    high REAL,
    low REAL,
    close REAL NOT NULL,
    volume INTEGER,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, date),
    FOREIGN KEY (symbol) REFERENCES stocks(symbol)
);
CREATE INDEX IF NOT EXISTS idx_prices_symbol_date ON prices(symbol, date);

-- News articles
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,        -- 'bursa', 'klsescreener', 'iceberg'
    title TEXT NOT NULL,
    url TEXT UNIQUE,
    published_at TIMESTAMP,
    content TEXT,
    matched_symbol TEXT,         -- FK to stocks if matched
    raw_text TEXT,               -- original scraped text
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (matched_symbol) REFERENCES stocks(symbol)
);
CREATE INDEX IF NOT EXISTS idx_news_symbol ON news(matched_symbol);
CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at);

-- Sentiment analysis results per news article
CREATE TABLE IF NOT EXISTS news_sentiment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id INTEGER NOT NULL,
    symbol TEXT,
    sentiment TEXT,              -- 'positive', 'negative', 'neutral'
    confidence REAL,             -- 0.0 - 1.0
    score REAL,                  -- -1.0 to 1.0
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (news_id) REFERENCES news(id),
    FOREIGN KEY (symbol) REFERENCES stocks(symbol)
);
CREATE INDEX IF NOT EXISTS idx_sentiment_symbol ON news_sentiment(symbol);

-- ML feature vectors per stock per day
CREATE TABLE IF NOT EXISTS ml_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    features TEXT NOT NULL,      -- JSON blob
    target REAL,                 -- next-day return or label
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, date),
    FOREIGN KEY (symbol) REFERENCES stocks(symbol)
);
CREATE INDEX IF NOT EXISTS idx_ml_features_symbol ON ml_features(symbol, date);

-- Trading recommendations from ML model
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,          -- recommendation date
    action TEXT NOT NULL,        -- 'BUY', 'SELL', 'HOLD'
    confidence REAL,             -- model confidence 0-1
    reason TEXT,                 -- human-readable rationale
    model_version TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (symbol) REFERENCES stocks(symbol)
);
CREATE INDEX IF NOT EXISTS idx_rec_symbol_date ON recommendations(symbol, date);

-- Annual reports downloaded from Bursa Malaysia
CREATE TABLE IF NOT EXISTS annual_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    title TEXT NOT NULL,
    announcement_date TEXT,
    pdf_url TEXT NOT NULL,
    local_path TEXT,
    file_size INTEGER,
    processed INTEGER DEFAULT 0,  -- 1 if PDF has been extracted & processed
    downloaded_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, pdf_url),
    FOREIGN KEY (symbol) REFERENCES stocks(symbol)
);
CREATE INDEX IF NOT EXISTS idx_annual_reports_symbol ON annual_reports(symbol);

-- ============================================================
-- MACROECONOMIC DATA (for MY stock analysis + ML features)
-- ============================================================

-- Macroeconomic indicators (monthly/quarterly data points)
CREATE TABLE IF NOT EXISTS macro_indicators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,              -- YYYY-MM-DD (month-end or quarter-end)
    indicator_name TEXT NOT NULL,    -- 'OPR', 'CPI_inflation', 'GDP_growth', 'USD_MYR', etc.
    value REAL,
    unit TEXT,                       -- 'percent', 'MYR', 'index', etc.
    source TEXT,                     -- 'BNM', 'DOSM', 'FRED', 'tradingeconomics'
    frequency TEXT,                  -- 'monthly', 'quarterly', 'daily'
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, indicator_name)
);
CREATE INDEX IF NOT EXISTS idx_macro_indicator_date ON macro_indicators(indicator_name, date);
CREATE INDEX IF NOT EXISTS idx_macro_date ON macro_indicators(date);

-- Macro economic calendar (upcoming events)
CREATE TABLE IF NOT EXISTS macro_calendar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT NOT NULL,
    event_name TEXT NOT NULL,        -- 'BNM OPR Meeting', 'CPI Release', etc.
    country TEXT DEFAULT 'MY',
    importance TEXT,                 -- 'high', 'medium', 'low'
    previous_value REAL,
    forecast_value REAL,
    actual_value REAL,
    unit TEXT,
    source TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_macro_calendar_date ON macro_calendar(event_date);

-- Sector sensitivity to macro indicators (for ML feature engineering)
CREATE TABLE IF NOT EXISTS sector_sensitivity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sector TEXT NOT NULL,            -- 'Banks', 'Plantation', 'Technology', etc.
    indicator_name TEXT NOT NULL,    -- 'OPR', 'USD_MYR', 'palm_oil_price', etc.
    correlation_direction TEXT,      -- 'positive', 'negative', 'neutral'
    impact_magnitude REAL,           -- 0-1 scale of how strongly this affects the sector
    notes TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(sector, indicator_name)
);

-- Company profile (static info, one row per stock)
CREATE TABLE IF NOT EXISTS company_profile (
    symbol TEXT PRIMARY KEY,
    company_name TEXT,
    sector TEXT,
    industry TEXT,
    incorporation_date TEXT,
    employee_count INTEGER,
    business_description TEXT,
    auditor TEXT,
    audit_opinion TEXT,           -- 'unqualified', 'qualified', etc.
    website TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (symbol) REFERENCES stocks(symbol)
);

-- Company fundamentals (one row per stock per financial year)
CREATE TABLE IF NOT EXISTS company_fundamentals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    financial_year TEXT NOT NULL,  -- e.g. '2024' or 'FY2024'
    report_date TEXT,

    -- Income statement
    revenue REAL,
    cost_of_goods_sold REAL,
    gross_profit REAL,
    operating_expenses REAL,
    depreciation REAL,
    amortization REAL,
    operating_profit REAL,
    ebitda REAL,
    interest_expense REAL,
    profit_before_tax REAL,
    tax_expense REAL,
    net_profit REAL,
    eps REAL,                      -- earnings per share

    -- Balance sheet
    total_assets REAL,
    total_liabilities REAL,
    total_equity REAL,
    cash_and_equivalents REAL,
    inventory REAL,
    receivables REAL,
    payables REAL,
    property_plant_equipment REAL, -- PPE
    intangible_assets REAL,
    current_assets REAL,
    current_liabilities REAL,

    -- Cash flow
    operating_cash_flow REAL,
    investing_cash_flow REAL,
    financing_cash_flow REAL,
    capex REAL,
    free_cash_flow REAL,

    -- Key ratios
    roe REAL,                      -- return on equity %
    roa REAL,                      -- return on assets %
    debt_to_equity REAL,
    current_ratio REAL,
    quick_ratio REAL,
    interest_coverage REAL,
    gross_margin REAL,             -- %
    operating_margin REAL,         -- %
    net_margin REAL,               -- %

    -- Metadata
    raw_extraction TEXT,           -- full JSON from Bedrock
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, financial_year),
    FOREIGN KEY (symbol) REFERENCES stocks(symbol)
);
CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol ON company_fundamentals(symbol);
CREATE INDEX IF NOT EXISTS idx_fundamentals_year ON company_fundamentals(financial_year);

-- Segment breakdown (per stock per year)
CREATE TABLE IF NOT EXISTS segment_breakdown (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    financial_year TEXT NOT NULL,
    segment_type TEXT,             -- 'business' or 'geography'
    segment_name TEXT NOT NULL,
    segment_revenue REAL,
    segment_profit REAL,
    segment_assets REAL,
    revenue_percentage REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (symbol) REFERENCES stocks(symbol)
);
CREATE INDEX IF NOT EXISTS idx_segment_symbol ON segment_breakdown(symbol, financial_year);

-- Risk factors (per stock per year)
CREATE TABLE IF NOT EXISTS risk_factors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    financial_year TEXT NOT NULL,
    risk_category TEXT,            -- 'market', 'credit', 'operational', 'regulatory', etc.
    risk_description TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (symbol) REFERENCES stocks(symbol)
);
CREATE INDEX IF NOT EXISTS idx_risk_symbol ON risk_factors(symbol, financial_year);

-- Dividend history (per stock per year)
CREATE TABLE IF NOT EXISTS dividend_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    financial_year TEXT NOT NULL,
    dividend_type TEXT,            -- 'interim', 'final', 'special', 'total'
    dividend_per_share REAL,       -- in MYR
    dividend_yield REAL,           -- %
    payout_ratio REAL,             -- %
    ex_date TEXT,
    payment_date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (symbol) REFERENCES stocks(symbol)
);
CREATE INDEX IF NOT EXISTS idx_dividend_symbol ON dividend_history(symbol, financial_year);
"""


@contextmanager
def get_conn(db_path: str = None):
    """Context manager for SQLite connections."""
    path = db_path or DB_PATH
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str = None):
    """Initialize database with schema."""
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
    return True


# ─── Stock CRUD ───────────────────────────────────────────────────────────────

def upsert_stock(symbol: str, name: str, sector: str = None,
                 bursa_code: str = None, db_path: str = None):
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO stocks (symbol, name, sector, bursa_code) VALUES (?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                   name=excluded.name,
                   sector=COALESCE(excluded.sector, stocks.sector),
                   bursa_code=COALESCE(excluded.bursa_code, stocks.bursa_code)""",
            (symbol, name, sector, bursa_code)
        )


def get_stock(symbol: str, db_path: str = None):
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM stocks WHERE symbol = ?", (symbol,)).fetchone()
        return dict(row) if row else None


def get_all_stocks(db_path: str = None) -> list:
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM stocks WHERE is_active = 1 ORDER BY symbol").fetchall()
        return [dict(r) for r in rows]


# ─── Price CRUD ───────────────────────────────────────────────────────────────

def upsert_price(symbol: str, date: str, open_p: float, high_p: float,
                 low_p: float, close_p: float, volume: int, db_path: str = None):
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO prices (symbol, date, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, date) DO UPDATE SET
                   open=excluded.open, high=excluded.high, low=excluded.low,
                   close=excluded.close, volume=excluded.volume,
                   fetched_at=CURRENT_TIMESTAMP""",
            (symbol, date, open_p, high_p, low_p, close_p, volume)
        )


def get_prices(symbol: str, limit: int = 100, db_path: str = None) -> list:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT ?",
            (symbol, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_price_date(symbol: str, db_path: str = None):
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(date) as latest FROM prices WHERE symbol = ?", (symbol,)
        ).fetchone()
        return row["latest"] if row else None


# ─── News CRUD ────────────────────────────────────────────────────────────────

def insert_news(source: str, title: str, url: str = None, published_at: str = None,
                content: str = None, matched_symbol: str = None,
                raw_text: str = None, db_path: str = None):
    with get_conn(db_path) as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO news (source, title, url, published_at, content, matched_symbol, raw_text)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (source, title, url, published_at, content, matched_symbol, raw_text)
        )
        return cursor.lastrowid or None


def get_news(symbol: str = None, limit: int = 50, db_path: str = None) -> list:
    with get_conn(db_path) as conn:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM news WHERE matched_symbol = ? ORDER BY published_at DESC LIMIT ?",
                (symbol, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM news ORDER BY published_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_news_count(db_path: str = None) -> int:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM news").fetchone()
        return row["cnt"]


# ─── Sentiment CRUD ───────────────────────────────────────────────────────────

def insert_sentiment(news_id: int, symbol: str, sentiment: str,
                     confidence: float, score: float, db_path: str = None):
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO news_sentiment (news_id, symbol, sentiment, confidence, score)
               VALUES (?, ?, ?, ?, ?)""",
            (news_id, symbol, sentiment, confidence, score)
        )


# ─── ML Features CRUD ─────────────────────────────────────────────────────────

def upsert_ml_features(symbol: str, date: str, features: str,
                       target: float = None, db_path: str = None):
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO ml_features (symbol, date, features, target)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(symbol, date) DO UPDATE SET
                   features=excluded.features, target=excluded.target,
                   created_at=CURRENT_TIMESTAMP""",
            (symbol, date, features, target)
        )


# ─── Recommendations CRUD ─────────────────────────────────────────────────────

def insert_recommendation(symbol: str, date: str, action: str,
                          confidence: float, reason: str = None,
                          model_version: str = None, db_path: str = None):
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO recommendations (symbol, date, action, confidence, reason, model_version)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (symbol, date, action, confidence, reason, model_version)
        )


def get_latest_recommendations(db_path: str = None) -> list:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """SELECT r.* FROM recommendations r
               INNER JOIN (
                   SELECT symbol, MAX(date) as max_date FROM recommendations GROUP BY symbol
               ) latest ON r.symbol = latest.symbol AND r.date = latest.max_date
               ORDER BY r.confidence DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


# ─── Annual Reports CRUD ──────────────────────────────────────────────────────

def insert_annual_report(symbol: str, title: str, pdf_url: str,
                         announcement_date: str = None, local_path: str = None,
                         file_size: int = None, downloaded_at: str = None,
                         db_path: str = None):
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO annual_reports
               (symbol, title, announcement_date, pdf_url, local_path, file_size, downloaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (symbol, title, announcement_date, pdf_url, local_path, file_size, downloaded_at)
        )


def get_annual_reports(symbol: str, db_path: str = None) -> list:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM annual_reports WHERE symbol = ? ORDER BY announcement_date DESC",
            (symbol,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_annual_report_count(db_path: str = None) -> int:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM annual_reports").fetchone()
        return row["cnt"]


# ─── Company Profile CRUD ─────────────────────────────────────────────────────

def upsert_company_profile(symbol: str, fields: dict, db_path: str = None):
    """Upsert company profile. Fields dict can include: company_name, sector, industry,
    incorporation_date, employee_count, business_description, auditor, audit_opinion, website."""
    allowed = ["company_name", "sector", "industry", "incorporation_date",
               "employee_count", "business_description", "auditor", "audit_opinion", "website"]
    cols = [k for k in allowed if k in fields]
    if not cols:
        return
    vals = [fields[c] for c in cols]
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    updates = ", ".join([f"{c}=excluded.{c}" for c in cols])
    with get_conn(db_path) as conn:
        conn.execute(
            f"""INSERT INTO company_profile (symbol, {col_names}) VALUES (?, {placeholders})
                ON CONFLICT(symbol) DO UPDATE SET {updates}, updated_at=CURRENT_TIMESTAMP""",
            [symbol] + vals
        )


def get_company_profile(symbol: str, db_path: str = None):
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM company_profile WHERE symbol = ?", (symbol,)).fetchone()
        return dict(row) if row else None


# ─── Company Fundamentals CRUD ─────────────────────────────────────────────────

def upsert_fundamentals(symbol: str, financial_year: str, fields: dict,
                        db_path: str = None):
    """Upsert fundamentals for a stock/year. Fields dict keys match column names."""
    base_cols = ["symbol", "financial_year"]
    base_vals = [symbol, financial_year]

    allowed = [
        "report_date", "revenue", "cost_of_goods_sold", "gross_profit",
        "operating_expenses", "depreciation", "amortization", "operating_profit",
        "ebitda", "interest_expense", "profit_before_tax", "tax_expense", "net_profit",
        "eps", "total_assets", "total_liabilities", "total_equity",
        "cash_and_equivalents", "inventory", "receivables", "payables",
        "property_plant_equipment", "intangible_assets", "current_assets",
        "current_liabilities", "operating_cash_flow", "investing_cash_flow",
        "financing_cash_flow", "capex", "free_cash_flow", "roe", "roa",
        "debt_to_equity", "current_ratio", "quick_ratio", "interest_coverage",
        "gross_margin", "operating_margin", "net_margin", "raw_extraction",
    ]
    extra_cols = [k for k in allowed if k in fields]
    all_cols = base_cols + extra_cols
    all_vals = base_vals + [fields[c] for c in extra_cols]

    placeholders = ", ".join(["?"] * len(all_cols))
    col_names = ", ".join(all_cols)
    updates = ", ".join([f"{c}=excluded.{c}" for c in extra_cols])
    if updates:
        updates += ", created_at=CURRENT_TIMESTAMP"
    else:
        updates = "created_at=CURRENT_TIMESTAMP"

    with get_conn(db_path) as conn:
        conn.execute(
            f"""INSERT INTO company_fundamentals ({col_names}) VALUES ({placeholders})
                ON CONFLICT(symbol, financial_year) DO UPDATE SET {updates}""",
            all_vals
        )


def get_fundamentals(symbol: str, financial_year: str = None, db_path: str = None):
    with get_conn(db_path) as conn:
        if financial_year:
            row = conn.execute(
                "SELECT * FROM company_fundamentals WHERE symbol = ? AND financial_year = ?",
                (symbol, financial_year)
            ).fetchone()
            return dict(row) if row else None
        else:
            rows = conn.execute(
                "SELECT * FROM company_fundamentals WHERE symbol = ? ORDER BY financial_year DESC",
                (symbol,)
            ).fetchall()
            return [dict(r) for r in rows]


# ─── Segment Breakdown CRUD ────────────────────────────────────────────────────

def insert_segment(symbol: str, financial_year: str, segment_type: str,
                   segment_name: str, revenue: float = None, profit: float = None,
                   assets: float = None, revenue_pct: float = None, db_path: str = None):
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO segment_breakdown
               (symbol, financial_year, segment_type, segment_name, segment_revenue,
                segment_profit, segment_assets, revenue_percentage)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, financial_year, segment_type, segment_name, revenue, profit, assets, revenue_pct)
        )


def get_segments(symbol: str, financial_year: str = None, db_path: str = None) -> list:
    with get_conn(db_path) as conn:
        if financial_year:
            rows = conn.execute(
                "SELECT * FROM segment_breakdown WHERE symbol = ? AND financial_year = ?",
                (symbol, financial_year)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM segment_breakdown WHERE symbol = ? ORDER BY financial_year DESC",
                (symbol,)
            ).fetchall()
        return [dict(r) for r in rows]


# ─── Risk Factors CRUD ─────────────────────────────────────────────────────────

def insert_risk_factor(symbol: str, financial_year: str, risk_category: str,
                       risk_description: str, db_path: str = None):
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO risk_factors (symbol, financial_year, risk_category, risk_description)
               VALUES (?, ?, ?, ?)""",
            (symbol, financial_year, risk_category, risk_description)
        )


def get_risk_factors(symbol: str, financial_year: str = None, db_path: str = None) -> list:
    with get_conn(db_path) as conn:
        if financial_year:
            rows = conn.execute(
                "SELECT * FROM risk_factors WHERE symbol = ? AND financial_year = ?",
                (symbol, financial_year)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM risk_factors WHERE symbol = ? ORDER BY financial_year DESC",
                (symbol,)
            ).fetchall()
        return [dict(r) for r in rows]


# ─── Dividend History CRUD ─────────────────────────────────────────────────────

def insert_dividend(symbol: str, financial_year: str, dividend_type: str = None,
                    dps: float = None, dividend_yield: float = None,
                    payout_ratio: float = None, ex_date: str = None,
                    payment_date: str = None, db_path: str = None):
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO dividend_history
               (symbol, financial_year, dividend_type, dividend_per_share,
                dividend_yield, payout_ratio, ex_date, payment_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, financial_year, dividend_type, dps, dividend_yield,
             payout_ratio, ex_date, payment_date)
        )


def get_dividends(symbol: str, financial_year: str = None, db_path: str = None) -> list:
    with get_conn(db_path) as conn:
        if financial_year:
            rows = conn.execute(
                "SELECT * FROM dividend_history WHERE symbol = ? AND financial_year = ?",
                (symbol, financial_year)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM dividend_history WHERE symbol = ? ORDER BY financial_year DESC",
                (symbol,)
            ).fetchall()
        return [dict(r) for r in rows]


# ─── Annual Reports - Mark Processed ───────────────────────────────────────────

def mark_report_processed(report_id: int, db_path: str = None):
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE annual_reports SET processed = 1 WHERE id = ?",
            (report_id,)
        )


def get_unprocessed_reports(db_path: str = None) -> list:
    """Get annual reports that haven't been processed yet."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM annual_reports WHERE processed = 0 AND local_path IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]


# ─── Utility ──────────────────────────────────────────────────────────────────

def get_table_counts(db_path: str = None) -> dict:
    """Return row counts for all tables."""
    tables = ["stocks", "prices", "news", "news_sentiment",
              "ml_features", "recommendations", "annual_reports",
              "company_profile", "company_fundamentals", "segment_breakdown",
              "risk_factors", "dividend_history",
              "macro_indicators", "macro_calendar", "sector_sensitivity"]
    counts = {}
    with get_conn(db_path) as conn:
        for t in tables:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM {t}").fetchone()
            counts[t] = row["cnt"]
    return counts


# ─── Macro Helpers ──────────────────────────────────────────────────────────

def get_latest_macro(indicator_name: str, db_path: str = None) -> Optional[dict]:
    """Get the most recent value of a macro indicator."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM macro_indicators WHERE indicator_name = ? ORDER BY date DESC LIMIT 1",
            (indicator_name,)
        ).fetchone()
        return dict(row) if row else None


def get_macro_series(indicator_name: str, limit: int = 60, db_path: str = None) -> list:
    """Get time series of a macro indicator."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT date, value FROM macro_indicators WHERE indicator_name = ? ORDER BY date DESC LIMIT ?",
            (indicator_name, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_upcoming_macro_events(days_ahead: int = 30, db_path: str = None) -> list:
    """Get upcoming macro calendar events."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM macro_calendar WHERE event_date >= date('now') AND event_date <= date('now', ?) ORDER BY event_date",
            (f"+{days_ahead} days",)
        ).fetchall()
        return [dict(r) for r in rows]


def get_sector_sensitivity(sector: str, db_path: str = None) -> list:
    """Get macro sensitivities for a sector."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM sector_sensitivity WHERE sector = ? ORDER BY impact_magnitude DESC",
            (sector,)
        ).fetchall()
        return [dict(r) for r in rows]


def seed_sector_sensitivity(db_path: str = None):
    """Seed sector sensitivity table with known macro relationships."""
    data = [
        # Banks
        ("Banks", "OPR", "positive", 0.9, "Higher OPR = higher net interest margins for banks"),
        ("Banks", "GDP_growth", "positive", 0.7, "Strong GDP = more lending activity"),
        ("Banks", "CPI_inflation", "negative", 0.5, "High inflation may lead to rate hikes, but erodes loan quality"),
        ("Banks", "USD_MYR", "neutral", 0.2, "Limited direct FX exposure for local banks"),
        # Plantation
        ("Plantation", "palm_oil_price", "positive", 0.95, "Direct revenue driver"),
        ("Plantation", "USD_MYR", "positive", 0.6, "Weaker ringgit = higher MYR palm oil revenue"),
        ("Plantation", "GDP_growth", "positive", 0.3, "Domestic demand for food products"),
        ("Plantation", "crude_oil_price", "positive", 0.4, "Higher oil = biofuel demand supports palm oil"),
        # Technology
        ("Technology", "USD_MYR", "positive", 0.8, "Most tech companies earn USD (exporters)"),
        ("Technology", "global_semiconductor_demand", "positive", 0.9, "Direct revenue driver"),
        ("Technology", "OPR", "negative", 0.3, "Higher rates reduce growth stock valuations"),
        ("Technology", "CPI_inflation", "negative", 0.4, "Inflation erodes real earnings"),
        # Oil & Gas
        ("Oil & Gas", "crude_oil_price", "positive", 0.95, "Direct revenue driver"),
        ("Oil & Gas", "USD_MYR", "positive", 0.7, "Oil priced in USD, weaker ringgit = higher MYR revenue"),
        ("Oil & Gas", "GDP_growth", "positive", 0.4, "Energy demand correlates with economic activity"),
        # Property
        ("Property", "OPR", "negative", 0.8, "Higher rates = higher mortgage costs, lower demand"),
        ("Property", "GDP_growth", "positive", 0.7, "Strong economy = more property demand"),
        ("Property", "CPI_inflation", "negative", 0.5, "Construction cost inflation squeezes margins"),
        ("Property", "unemployment_rate", "negative", 0.6, "High unemployment = lower purchasing power"),
        # Healthcare
        ("Healthcare", "OPR", "negative", 0.3, "Moderate impact from financing costs"),
        ("Healthcare", "GDP_growth", "positive", 0.5, "More healthcare spending in good times"),
        ("Healthcare", "USD_MYR", "negative", 0.4, "Medical supplies often imported"),
        # Consumer/Retail
        ("Consumer", "CPI_inflation", "negative", 0.7, "Inflation erodes consumer purchasing power"),
        ("Consumer", "GDP_growth", "positive", 0.6, "Strong economy = more discretionary spending"),
        ("Consumer", "unemployment_rate", "negative", 0.7, "High unemployment = lower retail sales"),
        ("Consumer", "OPR", "negative", 0.5, "Higher rates = higher financing costs for consumers"),
        # Telecommunications
        ("Telecommunications", "OPR", "negative", 0.4, "Highly leveraged, sensitive to rate changes"),
        ("Telecommunications", "GDP_growth", "positive", 0.5, "More data/economic activity = more usage"),
        ("Telecommunications", "USD_MYR", "negative", 0.3, "Equipment imported in USD"),
        # Construction
        ("Construction", "OPR", "negative", 0.7, "Higher rates = higher project financing costs"),
        ("Construction", "GDP_growth", "positive", 0.8, "Infrastructure spending drives construction"),
        ("Construction", "CPI_inflation", "negative", 0.6, "Material cost inflation squeezes margins"),
        ("Construction", "unemployment_rate", "negative", 0.4, "Labor availability concerns"),
    ]
    with get_conn(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO sector_sensitivity (sector, indicator_name, correlation_direction, impact_magnitude, notes) VALUES (?, ?, ?, ?, ?)",
            data
        )
        conn.commit()


if __name__ == "__main__":
    init_db()
    seed_sector_sensitivity()
    print("Database initialized at", DB_PATH)
    print("Sector sensitivity seeded.")
    print("Table counts:", get_table_counts())
