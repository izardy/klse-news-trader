"""
Price data fetcher for KLSE stocks using iTick API.
Fetches real-time quotes and historical daily klines.
"""
import os
import sys
import time
import requests
from datetime import datetime, timedelta

# Add parent to path so we can import db
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import upsert_price, upsert_stock, get_all_stocks, get_latest_price_date

# iTick config
ITICK_TOKEN = os.environ.get(
    "ITICK_TOKEN",
    "c01d09b814fb4f06bc75f0fea5aedff29d48e8f8c5324918b8d89e9150492375"
)
ITICK_BASE_URL = os.environ.get("ITICK_BASE_URL", "https://api-free.itick.org")
REGION = "my"  # Malaysia

# KLSE stock universe with display names
KLSE_STOCKS = {
    "MAYBANK": "Malayan Banking Bhd",
    "PBBANK": "Public Bank Bhd",
    "CIMB": "CIMB Group Holdings Bhd",
    "TENAGA": "Tenaga Nasional Bhd",
    "PETGAS": "Petronas Gas Bhd",
    "MAXIS": "Maxis Bhd",
    "AXIATA": "Axiata Group Bhd",
    "GENTING": "Genting Bhd",
    "IHH": "IHH Healthcare Bhd",
    "NESTLE": "Nestle (Malaysia) Bhd",
    "SIME": "Sime Darby Bhd",
    "TM": "Telekom Malaysia Bhd",
    "DIALOG": "Dialog Group Bhd",
    "YTL": "YTL Corporation Bhd",
    "PCHEM": "Petronas Chemicals Group Bhd",
    "IOICORP": "IOI Corporation Bhd",
    "KLK": "Kuala Lumpur Kepong Bhd",
    "BAT": "British American Tobacco (Malaysia) Bhd",
    "MRDIY": "MR D.I.Y. Group Bhd",
    "GAMUDA": "Gamuda Bhd",
    "UWC": "UWC Bhd",
    "GENM": "Genting Malaysia Bhd",
    "SD Guthrie": "SD Guthrie Berhad",
    "VITROX": "Vitrox Corporation Bhd",
    "INARI": "Inari Amertron Bhd",
    "UNISEM": "Unisem (M) Bhd",
    "FRONTKEN": "Frontken Corporation Bhd",
    "D&O": "D & O Green Technologies Bhd",
    "GHL": "GHL Systems Bhd",
    "KENANGA": "Kenanga Investment Bank Bhd",
    "MBSB": "MBSB Bank Bhd",
    "BIMB": "BIMB Holdings Bhd",
    "MRCB": "Malaysian Resources Corporation Bhd",
    "SPSETIA": "SP Setia Bhd",
    "IOIPG": "IOI Properties Group Bhd",
    "QL": "QL Resources Bhd",
    "PPB": "PPB Group Bhd",
    "HARTALEGA": "Hartalega Holdings Bhd",
    "TOPGLOV": "Top Glove Corporation Bhd",
    "SUPERMX": "Supermax Corporation Bhd",
}

# Bursa Malaysia company codes for annual report lookups
# (stock code used in Bursa announcement URLs)
BURSA_CODES = {
    "MAYBANK": "1155",
    "PBBANK": "1295",
    "CIMB": "1023",
    "TENAGA": "5347",
    "PETGAS": "6033",
    "MAXIS": "6012",
    "AXIATA": "6888",
    "GENTING": "3182",
    "IHH": "5225",
    "NESTLE": "4707",
    "SIME": "4197",
    "TM": "4863",
    "DIALOG": "7277",
    "YTL": "4677",
    "PCHEM": "5183",
    "IOICORP": "1961",
    "KLK": "2445",
    "BAT": "4162",
    "MRDIY": "5296",
    "GAMUDA": "5398",
    "UWC": "5292",
    "GENM": "4715",
    "SD Guthrie": "5285",
    "VITROX": "0090",
    "INARI": "0166",
    "UNISEM": "5005",
    "FRONTKEN": "0128",
    "D&O": "7203",
    "GHL": "0300",
    "KENANGA": "4836",
    "MBSB": "1171",
    "BIMB": "5258",
    "MRCB": "1651",
    "SPSETIA": "8664",
    "IOIPG": "5249",
    "QL": "7084",
    "PPB": "4065",
    "HARTALEGA": "5168",
    "TOPGLOV": "7113",
    "SUPERMX": "7106",
}

HEADERS = {"token": ITICK_TOKEN, "accept": "application/json"}


def fetch_realtime_quote(symbol: str) -> dict:
    """Fetch real-time tick quote for a single stock."""
    url = f"{ITICK_BASE_URL}/stock/ticks?region={REGION}&codes={symbol}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") == 0 and data.get("data"):
        return data["data"].get(symbol, {})
    return {}


def fetch_kline(symbol: str, ktype: int = 5, limit: int = 500) -> list:
    """
    Fetch historical kline (OHLCV) data.
    ktype: 1=1min, 2=5min, 15=15min, 30=30min, 60=60min, 5=daily
    Returns list of dicts with keys: date, open, high, low, close, volume
    """
    url = f"{ITICK_BASE_URL}/stock/kline?region={REGION}&code={symbol}&kType={ktype}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0 or not data.get("data"):
        return []

    bars = []
    for bar in data["data"]:
        # Convert millisecond timestamp to date string
        ts_ms = int(bar["t"])
        dt = datetime.utcfromtimestamp(ts_ms / 1000)
        bars.append({
            "date": dt.strftime("%Y-%m-%d"),
            "open": float(bar["o"]),
            "high": float(bar["h"]),
            "low": float(bar["l"]),
            "close": float(bar["c"]),
            "volume": int(bar["v"]),
        })
    return bars


def store_stock_universe(db_path: str = None):
    """Populate stocks table with KLSE universe."""
    for symbol, name in KLSE_STOCKS.items():
        bursa_code = BURSA_CODES.get(symbol, "")
        upsert_stock(symbol, name, bursa_code=bursa_code, db_path=db_path)
    print(f"Stored {len(KLSE_STOCKS)} stocks in database.")


def fetch_and_store_prices(symbol: str, db_path: str = None) -> int:
    """Fetch daily klines for a stock and store in prices table. Returns count stored."""
    bars = fetch_kline(symbol, ktype=5)
    if not bars:
        print(f"  WARNING: No price data for {symbol}")
        return 0
    for bar in bars:
        upsert_price(
            symbol=symbol,
            date=bar["date"],
            open_p=bar["open"],
            high_p=bar["high"],
            low_p=bar["low"],
            close_p=bar["close"],
            volume=bar["volume"],
            db_path=db_path,
        )
    return len(bars)


def fetch_all_prices(db_path: str = None, delay: float = 0.3):
    """Fetch and store daily prices for all KLSE stocks."""
    total = 0
    success = 0
    failed = []
    symbols = list(KLSE_STOCKS.keys())

    for i, symbol in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] Fetching {symbol}...", end=" ", flush=True)
        try:
            count = fetch_and_store_prices(symbol, db_path=db_path)
            print(f"✓ {count} bars")
            total += count
            success += 1
        except Exception as e:
            print(f"✗ {e}")
            failed.append(symbol)
        time.sleep(delay)

    print(f"\n=== Price fetch complete: {success}/{len(symbols)} stocks, {total} total bars ===")
    if failed:
        print(f"Failed: {', '.join(failed)}")
    return total


def fetch_all_quotes(db_path: str = None) -> dict:
    """Fetch real-time quotes for all stocks (batch request)."""
    codes = ",".join(KLSE_STOCKS.keys())
    url = f"{ITICK_BASE_URL}/stock/ticks?region={REGION}&codes={codes}"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") == 0 and data.get("data"):
        return data["data"]
    return {}


if __name__ == "__main__":
    from data.db import init_db
    init_db()
    store_stock_universe()
    fetch_all_prices()
