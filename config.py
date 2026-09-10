# KLSE News Trader Configuration
import os

# iTick API
ITICK_TOKEN = os.environ.get("ITICK_TOKEN", "c01d09b814fb4f06bc75f0fea5aedff29d48e8f8c5324918b8d89e9150492375")
ITICK_BASE_URL = os.environ.get("ITICK_BASE_URL", "https://api-free.itick.org")

# Database
DB_PATH = os.environ.get("DB_PATH", "data/klse.db")

# Flask
PORT = int(os.environ.get("PORT", 5200))
DEBUG = os.environ.get("DEBUG", "true").lower() == "true"

# KLSE stock universe — loaded dynamically from DB (see get_all_symbols())
# This list is a fallback seed; the full universe (~900+) is populated by
# scripts/fetch_klse_universe.py and stored in the stocks table.
KLSE_STOCKS = [
    "MAYBANK", "PBBANK", "CIMB", "TENAGA", "PETGAS",
    "MAXIS", "AXIATA", "GENTING", "IHH", "NESTLE",
    "SIME", "TM", "DIALOG", "YTL", "PCHEM",
    "IOICORP", "KLK", "BAT", "MRDIY", "GAMUDA",
    "UWC", "GENM", "SD Guthrie", "VITROX", "INARI",
    "UNISEM", "FRONTKEN", "D&O", "GHL", "KENANGA",
    "MBSB", "BIMB", "MRCB", "SPSETIA", "IOIPG",
    "QL", "PPB", "HARTALEGA", "TOPGLOV", "SUPERMX"
]


def get_all_symbols(db_path=None):
    """Return all stock symbols from the stocks table (full KLSE universe)."""
    import sqlite3
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    symbols = [r[0] for r in conn.execute(
        "SELECT symbol FROM stocks ORDER BY symbol"
    ).fetchall()]
    conn.close()
    return symbols if symbols else KLSE_STOCKS

# News sources: Bursa announcements + KLSE Screener + Iceberg berita table
USE_ICEBERG_BERITA = True
ATHENA_DATABASE = "staging_tables"
ATHENA_TABLE = "berita"
ATHENA_OUTPUT = "s3://aws-athena-query-results-855346386998-ap-southeast-1/klse-news-trader/"

NEWS_SOURCES = [
    "https://www.bursamalaysia.com/market_information/announcements/company_announcement",
    "https://www.klsescreener.com/news",
]
