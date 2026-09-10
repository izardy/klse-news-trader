import sys, os, time, json, requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/home/sandbox/klse-news-trader")
from data.db import init_db, upsert_price, upsert_stock, get_conn

ITICK_TOKEN = "c01d09b814fb4f06bc75f0fea5aedff29d48e8f8c5324918b8d89e9150492375"
HEADERS = {"token": ITICK_TOKEN, "accept": "application/json"}

KLSE_STOCKS = {
    "MAYBANK": "Malayan Banking Bhd", "PBBANK": "Public Bank Bhd", "CIMB": "CIMB Group Holdings Bhd",
    "TENAGA": "Tenaga Nasional Bhd", "PETGAS": "Petronas Gas Bhd", "MAXIS": "Maxis Bhd",
    "AXIATA": "Axiata Group Bhd", "GENTING": "Genting Bhd", "IHH": "IHH Healthcare Bhd",
    "NESTLE": "Nestle (Malaysia) Bhd", "SIME": "Sime Darby Bhd", "TM": "Telekom Malaysia Bhd",
    "DIALOG": "Dialog Group Bhd", "YTL": "YTL Corporation Bhd", "PCHEM": "Petronas Chemicals Group Bhd",
    "IOICORP": "IOI Corporation Bhd", "KLK": "Kuala Lumpur Kepong Bhd", "BAT": "British American Tobacco (Malaysia) Bhd",
    "MRDIY": "MR D.I.Y. Group Bhd", "GAMUDA": "Gamuda Bhd", "UWC": "UWC Bhd",
    "GENM": "Genting Malaysia Bhd", "SD Guthrie": "SD Guthrie Berhad", "VITROX": "Vitrox Corporation Bhd",
    "INARI": "Inari Amertron Bhd", "UNISEM": "Unisem (M) Bhd", "FRONTKEN": "Frontken Corporation Bhd",
    "D&O": "D & O Green Technologies Bhd", "GHL": "GHL Systems Bhd", "KENANGA": "Kenanga Investment Bank Bhd",
    "MBSB": "MBSB Bank Bhd", "BIMB": "BIMB Holdings Bhd", "MRCB": "Malaysian Resources Corporation Bhd",
    "SPSETIA": "SP Setia Bhd", "IOIPG": "IOI Properties Group Bhd", "QL": "QL Resources Bhd",
    "PPB": "PPB Group Bhd", "HARTALEGA": "Hartalega Holdings Bhd", "TOPGLOV": "Top Glove Corporation Bhd",
    "SUPERMX": "Supermax Corporation Bhd",
}

def fetch_one(symbol):
    """Fetch prices for one stock. Returns (symbol, count)."""
    try:
        url = f"https://api-free.itick.org/stock/kline?region=my&code={symbol}&kType=5"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        data = resp.json()
        if data.get("code") == 0 and data.get("data"):
            bars = data["data"]
            for bar in bars:
                dt = datetime.utcfromtimestamp(int(bar["t"]) / 1000)
                d = dt.strftime("%Y-%m-%d")
                upsert_price(symbol, d, float(bar["o"]), float(bar["h"]), float(bar["l"]), float(bar["c"]), int(bar["v"]))
            return (symbol, len(bars), None)
        else:
            return (symbol, 0, data.get("msg", "no data"))
    except Exception as e:
        return (symbol, 0, str(e))

init_db()

# Store stocks
for symbol, name in KLSE_STOCKS.items():
    upsert_stock(symbol, name)
print(f"Stored {len(KLSE_STOCKS)} stocks")

# Fetch prices concurrently (5 workers)
total = 0
success = 0
failed = []
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {executor.submit(fetch_one, sym): sym for sym in KLSE_STOCKS.keys()}
    for i, future in enumerate(as_completed(futures), 1):
        symbol, count, err = future.result()
        if count > 0:
            print(f"[{i}/40] {symbol}: {count} bars")
            total += count
            success += 1
        else:
            print(f"[{i}/40] {symbol}: {err}")
            failed.append(symbol)

print(f"\n=== Prices: {success}/40 stocks, {total} total bars ===")
if failed:
    print(f"Failed: {', '.join(failed)}")

# Final counts
from data.db import get_table_counts
print(get_table_counts())
