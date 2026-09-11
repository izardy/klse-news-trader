#!/usr/bin/env python3
"""
Full KLSE Stock Universe Scraper.

Scrapes ALL ~900+ KLSE-listed stocks from iTick API and populates
the stocks table with symbol, name, sector, market cap category, and bursa_code.

This is the master list that price_updater and PDF monitor iterate over.
"""
import os
import sys
import time
import logging
import requests
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import get_conn, init_pipeline_state, set_pipeline_state

# iTick config
ITICK_TOKEN = os.environ.get("ITICK_TOKEN", "c01d09b814fb4f06bc75f0fea5aedff29d48e8f8c5324918b8d89e9150492375")
ITICK_BASE_URL = os.environ.get("ITICK_BASE_URL", "https://api-free.itick.org")
REGION = "my"
HEADERS = {"token": ITICK_TOKEN, "accept": "application/json"}





def fetch_itick_stock_list():
    """
    Fetch full stock list from iTick API.
    Returns list of dicts with stock details.
    """
    url = f"{ITICK_BASE_URL}/stock/list?region={REGION}"
    logger.info("Fetching full KLSE stock list from iTick...")

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") == 0 and data.get("data"):
            stocks = data["data"]
            logger.info(f"Retrieved {len(stocks)} stocks from iTick")
            return stocks
        else:
            logger.error(f"iTick error: code={data.get('code')}, msg={data.get('msg')}")
            return []
    except Exception as e:
        logger.error(f"Error fetching stock list: {e}")
        return []


def store_universe(stocks):
    """
    Store the full stock universe in the database.
    Uses batch operations for speed.
    """
    stored = 0
    skipped = 0
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    with get_conn() as conn:
        for stock in stocks:
            try:
                symbol = stock.get("c", "").strip()
                name = stock.get("n", "").strip()
                sector = stock.get("s", "").strip()
                industry = stock.get("i", "")
                market_cap = stock.get("mcb")

                if not symbol or not name:
                    skipped += 1
                    continue

                conn.execute(
                    """INSERT INTO stocks (symbol, name, sector, is_active, added_at)
                       VALUES (?, ?, ?, 1, ?)
                       ON CONFLICT(symbol) DO UPDATE SET
                           name=excluded.name,
                           sector=COALESCE(excluded.sector, stocks.sector)""",
                    (symbol, name, sector, now)
                )
                stored += 1

            except Exception as e:
                logger.error(f"Error storing {stock.get('c', '?')}: {e}")
                skipped += 1

        conn.commit()

    logger.info(f"Stored {stored} stocks, skipped {skipped}")
    return stored





def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("KLSE FULL UNIVERSE SCRAPER")
    logger.info("=" * 60)

    # Ensure DB is ready
    init_pipeline_state()

    # Fetch from iTick
    stocks = fetch_itick_stock_list()

    if not stocks:
        logger.error("Could not fetch any stocks!")
        sys.exit(1)

    logger.info(f"Total stocks fetched: {len(stocks)}")

    # Store in DB
    stored = store_universe(stocks)

    # Update pipeline state
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    set_pipeline_state("klse_universe_last_update", now_str)
    set_pipeline_state("klse_universe_count", str(stored))

    # Verify
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
        sectors = conn.execute(
            "SELECT sector, COUNT(*) as cnt FROM stocks GROUP BY sector ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        logger.info(f"Total stocks in DB: {count}")
        logger.info("Top sectors:")
        for s in sectors:
            logger.info(f"  {s[0] or '(none)'}: {s[1]}")

    logger.info(f"\n=== COMPLETE: {stored} stocks stored ===")


if __name__ == "__main__":
    main()
