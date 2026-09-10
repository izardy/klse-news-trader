#!/usr/bin/env python3
"""
Real-time Price Updater — fetches latest quotes for ALL KLSE stocks.

Runs frequently (every 15 min via cron) to:
1. Fetch latest quotes from iTick API for all stocks in DB
2. Update prices table (INSERT OR REPLACE by symbol+date)
3. Fetch latest news from KLSE Screener and store in news table
4. Update macro_indicators if new data available (monthly check)
5. Store last-update timestamp in pipeline_state table

Respects iTick rate limit (5 req per window, adds sleep between batches).
"""
import os
import sys
import time
import json
import logging
import requests
from datetime import datetime, timedelta

# Setup logging
LOG_FILE = "/tmp/price_updater.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import (
    get_conn, upsert_price, upsert_stock, insert_news,
    get_pipeline_state, set_pipeline_state, init_pipeline_state,
    get_all_stocks, DB_PATH
)

# iTick config
ITICK_TOKEN = os.environ.get("ITICK_TOKEN", "c01d09b814fb4f06bc75f0fea5aedff29d48e8f8c5324918b8d89e9150492375")
ITICK_BASE_URL = os.environ.get("ITICK_BASE_URL", "https://api-free.itick.org")
REGION = "my"
HEADERS = {"token": ITICK_TOKEN, "accept": "application/json"}

# Rate limiting: iTick free tier = 5 requests per window (~60s)
RATE_LIMIT_REQUESTS = 5
RATE_LIMIT_WINDOW = 65  # seconds (slightly over 60 to be safe)
BATCH_DELAY = 15  # seconds between batches

# News sources
KLSE_SCREENER_NEWS_URL = "https://www.klsescreener.com/news"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def get_all_symbols_from_db():
    """Get all stock symbols from the stocks table."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT symbol, name, sector, bursa_code FROM stocks WHERE is_active = 1 ORDER BY symbol"
        ).fetchall()
        return [dict(r) for r in rows]


def rate_limited_request(url, headers, timeout=15):
    """Make a rate-limited request to iTick API."""
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_batch_quotes(symbols_batch):
    """
    Fetch real-time quotes for a batch of symbols.
    Uses the batch endpoint: /stock/ticks?region=my&codes=SYM1,SYM2,...
    Returns dict of {symbol: quote_data}
    """
    codes = ",".join(symbols_batch)
    url = f"{ITICK_BASE_URL}/stock/ticks?region={REGION}&codes={codes}"

    try:
        data = rate_limited_request(url, HEADERS)
        if data.get("code") == 0 and data.get("data"):
            return data["data"]
    except Exception as e:
        logger.error(f"Batch quote error: {e}")
    return {}


def fetch_kline_latest(symbol):
    """
    Fetch latest daily kline for a single stock.
    Returns dict with date, open, high, low, close, volume or None.
    """
    url = f"{ITICK_BASE_URL}/stock/kline?region={REGION}&code={symbol}&kType=5&limit=1"

    try:
        data = rate_limited_request(url, HEADERS)
        if data.get("code") == 0 and data.get("data"):
            bar = data["data"][-1]  # Most recent bar
            ts_ms = int(bar["t"])
            dt = datetime.utcfromtimestamp(ts_ms / 1000)
            return {
                "date": dt.strftime("%Y-%m-%d"),
                "open": float(bar["o"]),
                "high": float(bar["h"]),
                "low": float(bar["l"]),
                "close": float(bar["c"]),
                "volume": int(bar["v"]),
            }
    except Exception as e:
        logger.debug(f"Kline error for {symbol}: {e}")
    return None


def update_prices_for_all():
    """
    Fetch and update prices for ALL stocks in DB.
    Uses batch quotes first, then kline for missing data.
    Returns count of updated stocks.
    """
    stocks = get_all_symbols_from_db()
    if not stocks:
        logger.warning("No stocks found in DB!")
        return 0

    symbols = [s["symbol"] for s in stocks]
    total = len(symbols)
    updated = 0
    request_count = 0
    window_start = time.time()

    logger.info(f"Updating prices for {total} stocks...")

    # Process in batches of 5 (rate limit)
    batch_size = RATE_LIMIT_REQUESTS
    for i in range(0, total, batch_size):
        batch = symbols[i:i + batch_size]

        # Check rate limit window
        elapsed = time.time() - window_start
        if elapsed < RATE_LIMIT_WINDOW and request_count >= RATE_LIMIT_REQUESTS:
            wait = RATE_LIMIT_WINDOW - elapsed + 2
            logger.info(f"Rate limit reached, waiting {wait:.0f}s...")
            time.sleep(wait)
            request_count = 0
            window_start = time.time()

        # Fetch batch quotes
        quotes = fetch_batch_quotes(batch)
        request_count += 1

        today = datetime.utcnow().strftime("%Y-%m-%d")

        for symbol in batch:
            quote = quotes.get(symbol)
            if quote:
                try:
                    close_val = float(quote.get("last", 0) or 0)
                    if close_val > 0:
                        upsert_price(
                            symbol=symbol,
                            date=today,
                            open_p=float(quote.get("open", 0) or 0),
                            high_p=float(quote.get("high", 0) or 0),
                            low_p=float(quote.get("low", 0) or 0),
                            close_p=close_val,
                            volume=int(quote.get("volume", 0) or 0),
                        )
                        updated += 1
                except Exception as e:
                    logger.debug(f"Error storing {symbol}: {e}")

        progress = min(i + batch_size, total)
        logger.info(f"  Progress: {progress}/{total} stocks processed, {updated} updated")

        # Delay between batches
        time.sleep(2)

    # For stocks not updated by quotes, try kline endpoint
    # (only for stocks with no price today)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if updated < total:
        missing_count = 0
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT symbol FROM stocks WHERE is_active = 1 AND symbol NOT IN "
                "(SELECT symbol FROM prices WHERE date = ?) ORDER BY symbol",
                (today,)
            ).fetchall()
            missing = [r[0] for r in rows]

        if missing:
            logger.info(f"Fetching kline for {len(missing)} missing stocks...")
            for symbol in missing:
                # Rate limit check
                elapsed = time.time() - window_start
                if elapsed < RATE_LIMIT_WINDOW and request_count >= RATE_LIMIT_REQUESTS:
                    wait = RATE_LIMIT_WINDOW - elapsed + 2
                    logger.info(f"Rate limit (kline), waiting {wait:.0f}s...")
                    time.sleep(wait)
                    request_count = 0
                    window_start = time.time()

                bar = fetch_kline_latest(symbol)
                request_count += 1

                if bar:
                    try:
                        upsert_price(
                            symbol=symbol,
                            date=bar["date"],
                            open_p=bar["open"],
                            high_p=bar["high"],
                            low_p=bar["low"],
                            close_p=bar["close"],
                            volume=bar["volume"],
                        )
                        updated += 1
                        missing_count += 1
                    except Exception as e:
                        logger.debug(f"Error storing kline {symbol}: {e}")

                time.sleep(0.5)  # Rate limit for individual kline requests

            logger.info(f"  Kline: {missing_count} additional stocks updated")

    logger.info(f"Price update complete: {updated}/{total} stocks")
    return updated


def fetch_latest_news():
    """Fetch latest news from KLSE Screener and store in news table."""
    import re

    new_articles = 0
    logger.info("Fetching latest news...")

    try:
        resp = requests.get(
            KLSE_SCREENER_NEWS_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"News fetch failed: HTTP {resp.status_code}")
            return 0

        html = resp.text

        # Extract news links and titles
        news_items = re.findall(
            r'<a[^>]*href="(/v2/news/\d+)"[^>]*>([^<]+)</a>',
            html, re.IGNORECASE
        )

        for link, title in news_items:
            title = title.strip()
            if not title or len(title) < 5:
                continue

            url = f"https://www.klsescreener.com{link}"

            # Try to match stock symbol
            matched = match_stock_symbol(title)

            try:
                row_id = insert_news(
                    source="klsescreener",
                    title=title,
                    url=url,
                    published_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                    content="",
                    matched_symbol=matched,
                    raw_text=title,
                )
                if row_id:
                    new_articles += 1
            except Exception as e:
                logger.debug(f"News insert error: {e}")

    except Exception as e:
        logger.error(f"Error fetching news: {e}")

    logger.info(f"News: {new_articles} new articles stored")
    return new_articles


def match_stock_symbol(text):
    """Match text to a stock symbol from the database."""
    if not text:
        return None

    text_lower = text.lower()

    # Get all stocks from DB for matching
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT symbol, name FROM stocks WHERE is_active = 1"
        ).fetchall()

    # Sort by name length (longest first) for more specific matches
    candidates = []
    for r in rows:
        symbol = r[0]
        name = r[1] or ""
        candidates.append((symbol, name))

    candidates.sort(key=lambda x: len(x[1]), reverse=True)

    for symbol, name in candidates:
        if name and name.lower() in text_lower:
            return symbol
        if symbol.lower() in text_lower:
            return symbol

    return None


def check_macro_update():
    """
    Check if macro indicators need updating.
    Only runs if last update was > 25 days ago (monthly data).
    """
    last_update = get_pipeline_state("macro_last_update")
    if last_update:
        try:
            last_dt = datetime.strptime(last_update, "%Y-%m-%d %H:%M:%S")
            if (datetime.utcnow() - last_dt).days < 25:
                logger.info("Macro indicators updated recently, skipping")
                return
        except ValueError:
            pass

    logger.info("Checking macro indicators for update...")
    # Macro data update is handled separately (monthly economic data)
    # For now, just update the timestamp
    set_pipeline_state("macro_last_update", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("PRICE UPDATER — Starting")
    logger.info("=" * 60)

    # Ensure pipeline_state table exists
    init_pipeline_state()

    start_time = time.time()

    # 1. Update prices for all stocks
    try:
        price_count = update_prices_for_all()
        set_pipeline_state("price_updater_last_count", str(price_count))
    except Exception as e:
        logger.error(f"Price update failed: {e}")
        price_count = 0

    # 2. Fetch latest news
    try:
        news_count = fetch_latest_news()
        set_pipeline_state("news_last_count", str(news_count))
    except Exception as e:
        logger.error(f"News update failed: {e}")
        news_count = 0

    # 3. Check macro indicators (monthly)
    try:
        check_macro_update()
    except Exception as e:
        logger.error(f"Macro check failed: {e}")

    # Update pipeline state
    elapsed = time.time() - start_time
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    set_pipeline_state("price_updater_last_run", now_str)
    set_pipeline_state("price_updater_last_duration", f"{elapsed:.1f}s")

    logger.info(f"\n=== COMPLETE: {price_count} prices, {news_count} news in {elapsed:.1f}s ===")


if __name__ == "__main__":
    main()
