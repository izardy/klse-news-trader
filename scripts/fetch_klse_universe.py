#!/usr/bin/env python3
"""
Full KLSE Stock Universe Scraper.

Scrapes ALL ~900+ KLSE-listed stocks from KLSE Screener and populates
the stocks table with symbol, name, sector, and market cap category.

This is the master list that price_updater and PDF monitor iterate over.
"""
import os
import sys
import re
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
from data.db import get_conn, upsert_stock, init_pipeline_state, set_pipeline_state

# KLSE Screener has a stock list page with all listed companies
KLSE_SCREENER_LIST_URL = "https://www.klsescreener.com/v2/stocks"
BURSA_LIST_URL = "https://www.bursamalaysia.com/market_information/announcements/company_announcement"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def scrape_klsescreener_universe():
    """
    Scrape the full stock list from KLSE Screener.
    Returns list of dicts: {symbol, name, sector}
    """
    stocks = []
    page = 1
    max_pages = 50  # Safety limit

    while page <= max_pages:
        url = f"{KLSE_SCREENER_LIST_URL}?page={page}"
        logger.info(f"Fetching KLSE Screener page {page}...")

        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            if resp.status_code != 200:
                logger.warning(f"HTTP {resp.status_code} on page {page}")
                break

            html = resp.text

            # KLSE Screener stock table pattern
            # Look for stock links in the table: <a href="/v2/stocks/view/1155">MAYBANK</a>
            rows = re.findall(
                r'<a[^>]*href="/v2/stocks/view/(\d+)"[^>]*>([^<]+)</a>',
                html
            )

            if not rows:
                logger.info(f"No stocks found on page {page}, stopping")
                break

            page_stocks = []
            for code, symbol in rows:
                symbol = symbol.strip()
                if symbol and re.match(r'^[A-Za-z0-9\&\.\-]+$', symbol):
                    page_stocks.append({
                        "symbol": symbol,
                        "bursa_code": code,
                        "name": symbol,  # Will be enriched later
                        "sector": "",
                    })

            if not page_stocks:
                break

            stocks.extend(page_stocks)
            logger.info(f"  Found {len(page_stocks)} stocks on page {page} (total: {len(stocks)})")

            # Check if there's a next page
            if 'rel="next"' not in html and 'Next &raquo;' not in html and '&rsaquo;' not in html:
                logger.info("No next page link found")
                break

            page += 1
            time.sleep(0.5)  # Rate limit

        except Exception as e:
            logger.error(f"Error on page {page}: {e}")
            break

    return stocks


def scrape_klsescreener_sectors(stocks):
    """
    Enrich stocks with sector info from KLSE Screener.
    Fetches individual stock pages to get sector data.
    """
    logger.info(f"Enriching {len(stocks)} stocks with sector info...")

    for i, stock in enumerate(stocks):
        code = stock.get("bursa_code", "")
        if not code:
            continue

        try:
            url = f"https://www.klsescreener.com/v2/stocks/view/{code}"
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
            if resp.status_code == 200:
                html = resp.text

                # Extract company name
                name_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
                if name_match:
                    stock["name"] = name_match.group(1).strip()

                # Extract sector
                sector_match = re.search(
                    r'(?:Sector|Industry)[^<]*</th>\s*<td[^>]*>([^<]+)</td>',
                    html, re.IGNORECASE
                )
                if sector_match:
                    stock["sector"] = sector_match.group(1).strip()

        except Exception as e:
            logger.debug(f"Error enriching {stock['symbol']}: {e}")

        if (i + 1) % 50 == 0:
            logger.info(f"  Enriched {i+1}/{len(stocks)} stocks...")
            time.sleep(0.5)

        time.sleep(0.2)  # Rate limit

    return stocks


def scrape_bursa_universe():
    """
    Alternative: Scrape from Bursa Malaysia's company listing.
    Returns list of dicts: {symbol, name, sector, bursa_code}
    """
    stocks = []
    try:
        # Bursa has a company list page
        url = "https://www.bursamalaysia.com/trade/trading_resources/listing_directory"
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        if resp.status_code == 200:
            html = resp.text
            # Extract company listings
            rows = re.findall(
                r'<tr>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>',
                html, re.DOTALL
            )
            for row in rows:
                symbol = re.sub(r'<[^>]+>', '', row[0]).strip()
                name = re.sub(r'<[^>]+>', '', row[1]).strip()
                sector = re.sub(r'<[^>]+>', '', row[2]).strip()
                if symbol and re.match(r'^[A-Za-z0-9\&\.\-]+$', symbol):
                    stocks.append({
                        "symbol": symbol,
                        "name": name,
                        "sector": sector,
                        "bursa_code": "",
                    })
    except Exception as e:
        logger.error(f"Error scraping Bursa universe: {e}")

    return stocks


def store_universe(stocks):
    """Store the full stock universe in the database."""
    stored = 0
    for stock in stocks:
        try:
            upsert_stock(
                symbol=stock["symbol"],
                name=stock.get("name", stock["symbol"]),
                sector=stock.get("sector", ""),
                bursa_code=stock.get("bursa_code", ""),
            )
            stored += 1
        except Exception as e:
            logger.error(f"Error storing {stock['symbol']}: {e}")

    logger.info(f"Stored {stored} stocks in database")
    return stored


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("KLSE FULL UNIVERSE SCRAPER")
    logger.info("=" * 60)

    # Ensure DB is ready
    init_pipeline_state()

    # Try KLSE Screener first
    stocks = scrape_klsescreener_universe()

    if not stocks:
        logger.warning("KLSE Screener returned no results, trying Bursa...")
        stocks = scrape_bursa_universe()

    if not stocks:
        logger.error("Could not scrape any stocks!")
        sys.exit(1)

    logger.info(f"Scraped {len(stocks)} stocks from KLSE Screener")

    # Enrich with sector info (optional, can be slow for 900+ stocks)
    # Uncomment if needed:
    # stocks = scrape_klsescreener_sectors(stocks)

    # Store in DB
    stored = store_universe(stocks)

    # Update pipeline state
    set_pipeline_state("klse_universe_last_update", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
    set_pipeline_state("klse_universe_count", str(stored))

    # Verify
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
        logger.info(f"Total stocks in DB: {count}")

    logger.info(f"\n=== COMPLETE: {stored} stocks stored ===")


if __name__ == "__main__":
    main()
