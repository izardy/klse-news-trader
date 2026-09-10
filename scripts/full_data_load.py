import sys, os, time, json, requests
sys.path.insert(0, "/home/sandbox/klse-news-trader")

from data.db import init_db, upsert_price, upsert_stock, insert_news, get_table_counts
from data.news import match_stock

# Config
ITICK_TOKEN = "c01d09b814fb4f06bc75f0fea5aedff29d48e8f8c5324918b8d89e9150492375"
HEADERS = {"token": ITICK_TOKEN, "accept": "application/json"}

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

init_db()
print("DB initialized")

# Step 1: Store stock universe
for symbol, name in KLSE_STOCKS.items():
    upsert_stock(symbol, name)
print(f"Stored {len(KLSE_STOCKS)} stocks")

# Step 2: Fetch prices
from datetime import datetime
total_prices = 0
success_stocks = 0
for i, symbol in enumerate(KLSE_STOCKS.keys()):
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
            print(f"[{i+1}/40] {symbol}: {len(bars)} bars")
            total_prices += len(bars)
            success_stocks += 1
        else:
            print(f"[{i+1}/40] {symbol}: no data - {data.get('msg', '')}")
    except Exception as e:
        print(f"[{i+1}/40] {symbol}: ERROR {e}")
    time.sleep(0.15)

print(f"\n=== Prices: {success_stocks}/40 stocks, {total_prices} total bars ===")

# Step 3: Store Iceberg berita news
try:
    with open("/home/sandbox/klse-news-trader/data/berita_raw.json", "r") as f:
        all_rows = json.load(f)
    
    header = [v.get("VarCharValue", "") for v in all_rows[0]["Data"]]
    news_count = 0
    for row in all_rows[1:]:
        vals = [v.get("VarCharValue", "") for v in row["Data"]]
        record = dict(zip(header, vals))
        title = record.get("Title", "")
        url = record.get("URL", "")
        date_created = record.get("Date_created", "")
        content = record.get("Content", "")
        if not title:
            continue
        matched = match_stock(title + " " + content[:200])
        row_id = insert_news(
            source="iceberg",
            title=title,
            url=url if url else None,
            published_at=date_created[:19] if date_created else None,
            content=content[:2000] if content else None,
            matched_symbol=matched,
            raw_text=title + " " + content[:500] if content else title,
        )
        if row_id:
            news_count += 1
    print(f"=== News: {news_count} articles stored from Iceberg ===")
except Exception as e:
    print(f"News error: {e}")

# Final counts
print("\n=== FINAL COUNTS ===")
print(get_table_counts())
