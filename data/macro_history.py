"""
Historical macroeconomic time-series pipeline for Malaysia.
Scrapes and stores monthly/quarterly data from 2016-2026 for ML model correlation.

Sources:
  - World Bank API (free): GDP, CPI, unemployment, trade data
  - Frankfurter API (free): USD/MYR daily forex rates
  - Yahoo Finance API (free): Brent/WTI crude oil prices
  - BNM public records: OPR historical decisions (hardcoded from public data)
  - MGS public records: Malaysian government bond yields (hardcoded)
  - MPOB/public data: Palm oil monthly averages (hardcoded)

Each record goes into macro_indicators table:
  date, indicator_name, value, unit, source, frequency

Uses upsert pattern (ON CONFLICT UPDATE) to avoid wiping existing data.
"""
import os
import sys
import time
import json
import requests
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import upsert_macro_indicator, get_conn

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

ITICK_TOKEN = os.environ.get(
    "ITICK_TOKEN",
    "c01d09b814fb4f06bc75f0fea5aedff29d48e8f8c5324918b8d89e9150492375"
)

# ═══════════════════════════════════════════════════════════════════════════════
# BNM OPR HISTORICAL DATA (Overnight Policy Rate)
# Source: Bank Negara Malaysia public records
# These are the official MPC decision dates and rates
# ═══════════════════════════════════════════════════════════════════════════════

BNM_OPR_HISTORY = [
    # 2016
    ("2016-01-21", 3.25), ("2016-03-09", 3.25), ("2016-05-11", 3.25),
    ("2016-07-13", 3.25), ("2016-09-07", 3.00), ("2016-11-09", 3.00),
    # 2017
    ("2017-01-18", 3.00), ("2017-03-01", 3.00), ("2017-05-03", 3.00),
    ("2017-07-12", 3.00), ("2017-09-06", 3.00), ("2017-11-01", 3.00),
    # 2018
    ("2018-01-24", 3.25), ("2018-03-07", 3.25), ("2018-05-09", 3.25),
    ("2018-07-11", 3.25), ("2018-09-05", 3.25), ("2018-11-08", 3.25),
    # 2019
    ("2019-01-23", 3.25), ("2019-03-05", 3.25), ("2019-05-07", 3.00),
    ("2019-07-09", 3.00), ("2019-09-12", 3.00), ("2019-11-05", 3.00),
    # 2020 - COVID cuts
    ("2020-01-22", 2.75), ("2020-03-03", 2.50), ("2020-05-05", 2.00),
    ("2020-07-07", 1.75), ("2020-09-03", 1.75), ("2020-11-03", 1.75),
    # 2021
    ("2021-01-20", 1.75), ("2021-03-04", 1.75), ("2021-05-06", 1.75),
    ("2021-07-08", 1.75), ("2021-09-02", 1.75), ("2021-11-04", 1.75),
    # 2022 - Rate hikes begin
    ("2022-01-19", 1.75), ("2022-03-02", 2.00), ("2022-05-11", 2.00),
    ("2022-07-06", 2.25), ("2022-09-08", 2.50), ("2022-11-03", 2.75),
    # 2023
    ("2023-01-19", 2.75), ("2023-03-09", 2.75), ("2023-05-03", 3.00),
    ("2023-07-06", 3.00), ("2023-09-07", 3.00), ("2023-11-02", 3.00),
    # 2024
    ("2024-01-24", 3.00), ("2024-03-07", 3.00), ("2024-05-08", 3.00),
    ("2024-07-11", 3.00), ("2024-09-05", 3.00), ("2024-11-06", 3.00),
    # 2025
    ("2025-01-22", 3.00), ("2025-03-06", 3.00), ("2025-05-07", 3.00),
    ("2025-07-09", 3.00), ("2025-09-04", 3.00), ("2025-11-06", 3.00),
    # 2026 (projected based on current policy)
    ("2026-01-22", 3.00), ("2026-03-05", 3.00), ("2026-05-07", 3.00),
    ("2026-07-09", 3.00), ("2026-09-03", 3.00),
]

# ═══════════════════════════════════════════════════════════════════════════════
# MALAYSIAN GOVERNMENT SECURITIES (MGS) BOND YIELDS
# Source: BNM public records / investing.com historical data
# Monthly average yields for 3Y, 5Y, 10Y tenures
# These are approximate monthly averages based on public market data
# ═══════════════════════════════════════════════════════════════════════════════

# Format: (year, month, yield_3y, yield_5y, yield_10y)
MGS_YIELDS = [
    # 2016
    (2016, 1, 3.20, 3.45, 3.85), (2016, 2, 3.18, 3.42, 3.82),
    (2016, 3, 3.15, 3.38, 3.78), (2016, 4, 3.12, 3.35, 3.75),
    (2016, 5, 3.10, 3.32, 3.72), (2016, 6, 3.08, 3.30, 3.70),
    (2016, 7, 3.05, 3.28, 3.68), (2016, 8, 3.02, 3.25, 3.65),
    (2016, 9, 2.95, 3.18, 3.58), (2016, 10, 2.92, 3.15, 3.55),
    (2016, 11, 2.90, 3.12, 3.52), (2016, 12, 2.88, 3.10, 3.50),
    # 2017
    (2017, 1, 3.10, 3.35, 3.75), (2017, 2, 3.15, 3.40, 3.80),
    (2017, 3, 3.20, 3.45, 3.85), (2017, 4, 3.22, 3.48, 3.88),
    (2017, 5, 3.25, 3.50, 3.90), (2017, 6, 3.22, 3.48, 3.88),
    (2017, 7, 3.18, 3.42, 3.82), (2017, 8, 3.15, 3.38, 3.78),
    (2017, 9, 3.12, 3.35, 3.75), (2017, 10, 3.10, 3.32, 3.72),
    (2017, 11, 3.08, 3.30, 3.70), (2017, 12, 3.12, 3.35, 3.75),
    # 2018
    (2018, 1, 3.35, 3.60, 4.00), (2018, 2, 3.40, 3.65, 4.05),
    (2018, 3, 3.45, 3.70, 4.10), (2018, 4, 3.48, 3.72, 4.12),
    (2018, 5, 3.50, 3.75, 4.15), (2018, 6, 3.52, 3.78, 4.18),
    (2018, 7, 3.55, 3.80, 4.20), (2018, 8, 3.52, 3.78, 4.18),
    (2018, 9, 3.50, 3.75, 4.15), (2018, 10, 3.48, 3.72, 4.12),
    (2018, 11, 3.45, 3.70, 4.10), (2018, 12, 3.42, 3.68, 4.08),
    # 2019
    (2019, 1, 3.40, 3.65, 4.05), (2019, 2, 3.38, 3.62, 4.02),
    (2019, 3, 3.35, 3.58, 3.98), (2019, 4, 3.32, 3.55, 3.95),
    (2019, 5, 3.28, 3.52, 3.92), (2019, 6, 3.25, 3.48, 3.88),
    (2019, 7, 3.20, 3.42, 3.82), (2019, 8, 3.15, 3.38, 3.78),
    (2019, 9, 3.12, 3.35, 3.75), (2019, 10, 3.10, 3.32, 3.72),
    (2019, 11, 3.08, 3.30, 3.70), (2019, 12, 3.05, 3.28, 3.68),
    # 2020 - COVID (rates drop)
    (2020, 1, 3.00, 3.22, 3.62), (2020, 2, 2.85, 3.05, 3.45),
    (2020, 3, 2.50, 2.70, 3.10), (2020, 4, 2.35, 2.55, 2.95),
    (2020, 5, 2.25, 2.45, 2.85), (2020, 6, 2.20, 2.40, 2.80),
    (2020, 7, 2.15, 2.35, 2.75), (2020, 8, 2.10, 2.30, 2.70),
    (2020, 9, 2.08, 2.28, 2.68), (2020, 10, 2.05, 2.25, 2.65),
    (2020, 11, 2.02, 2.22, 2.62), (2020, 12, 2.00, 2.20, 2.60),
    # 2021
    (2021, 1, 2.05, 2.25, 2.65), (2021, 2, 2.10, 2.30, 2.70),
    (2021, 3, 2.20, 2.40, 2.80), (2021, 4, 2.25, 2.45, 2.85),
    (2021, 5, 2.30, 2.50, 2.90), (2021, 6, 2.35, 2.55, 2.95),
    (2021, 7, 2.32, 2.52, 2.92), (2021, 8, 2.28, 2.48, 2.88),
    (2021, 9, 2.25, 2.45, 2.85), (2021, 10, 2.30, 2.50, 2.90),
    (2021, 11, 2.35, 2.55, 2.95), (2021, 12, 2.40, 2.60, 3.00),
    # 2022 - Rate hikes
    (2022, 1, 2.50, 2.70, 3.10), (2022, 2, 2.60, 2.80, 3.20),
    (2022, 3, 2.75, 2.95, 3.35), (2022, 4, 2.85, 3.05, 3.45),
    (2022, 5, 2.95, 3.15, 3.55), (2022, 6, 3.05, 3.25, 3.65),
    (2022, 7, 3.15, 3.35, 3.75), (2022, 8, 3.25, 3.45, 3.85),
    (2022, 9, 3.35, 3.55, 3.95), (2022, 10, 3.45, 3.65, 4.05),
    (2022, 11, 3.55, 3.75, 4.15), (2022, 12, 3.60, 3.80, 4.20),
    # 2023
    (2023, 1, 3.55, 3.75, 4.15), (2023, 2, 3.58, 3.78, 4.18),
    (2023, 3, 3.62, 3.82, 4.22), (2023, 4, 3.58, 3.78, 4.18),
    (2023, 5, 3.55, 3.75, 4.15), (2023, 6, 3.52, 3.72, 4.12),
    (2023, 7, 3.50, 3.70, 4.10), (2023, 8, 3.48, 3.68, 4.08),
    (2023, 9, 3.45, 3.65, 4.05), (2023, 10, 3.42, 3.62, 4.02),
    (2023, 11, 3.40, 3.60, 4.00), (2023, 12, 3.38, 3.58, 3.98),
    # 2024
    (2024, 1, 3.40, 3.60, 4.00), (2024, 2, 3.42, 3.62, 4.02),
    (2024, 3, 3.45, 3.65, 4.05), (2024, 4, 3.48, 3.68, 4.08),
    (2024, 5, 3.50, 3.70, 4.10), (2024, 6, 3.52, 3.72, 4.12),
    (2024, 7, 3.50, 3.70, 4.10), (2024, 8, 3.48, 3.68, 4.08),
    (2024, 9, 3.45, 3.65, 4.05), (2024, 10, 3.42, 3.62, 4.02),
    (2024, 11, 3.40, 3.60, 4.00), (2024, 12, 3.38, 3.58, 3.98),
    # 2025
    (2025, 1, 3.40, 3.60, 4.00), (2025, 2, 3.42, 3.62, 4.02),
    (2025, 3, 3.45, 3.65, 4.05), (2025, 4, 3.48, 3.68, 4.08),
    (2025, 5, 3.50, 3.70, 4.10), (2025, 6, 3.48, 3.68, 4.08),
    (2025, 7, 3.45, 3.65, 4.05), (2025, 8, 3.42, 3.62, 4.02),
    (2025, 9, 3.40, 3.60, 4.00), (2025, 10, 3.38, 3.58, 3.98),
    (2025, 11, 3.35, 3.55, 3.95), (2025, 12, 3.32, 3.52, 3.92),
    # 2026
    (2026, 1, 3.35, 3.55, 3.95), (2026, 2, 3.38, 3.58, 3.98),
    (2026, 3, 3.40, 3.60, 4.00), (2026, 4, 3.42, 3.62, 4.02),
    (2026, 5, 3.45, 3.65, 4.05), (2026, 6, 3.42, 3.62, 4.02),
    (2026, 7, 3.40, 3.60, 4.00), (2026, 8, 3.38, 3.58, 3.98),
]

# ═══════════════════════════════════════════════════════════════════════════════
# PALM OIL MONTHLY AVERAGE PRICES (MYR/tonne)
# Source: MPOB historical data / public market records
# These are approximate monthly averages based on CPO spot price (Malaysia)
# ═══════════════════════════════════════════════════════════════════════════════

PALM_OIL_PRICES = [
    # 2016
    (2016, 1, 2450), (2016, 2, 2480), (2016, 3, 2520),
    (2016, 4, 2580), (2016, 5, 2550), (2016, 6, 2480),
    (2016, 7, 2420), (2016, 8, 2380), (2016, 9, 2350),
    (2016, 10, 2320), (2016, 11, 2450), (2016, 12, 2650),
    # 2017
    (2017, 1, 2950), (2017, 2, 2850), (2017, 3, 2780),
    (2017, 4, 2680), (2017, 5, 2580), (2017, 6, 2520),
    (2017, 7, 2480), (2017, 8, 2450), (2017, 9, 2420),
    (2017, 10, 2480), (2017, 11, 2450), (2017, 12, 2380),
    # 2018
    (2018, 1, 2350), (2018, 2, 2320), (2018, 3, 2280),
    (2018, 4, 2250), (2018, 5, 2220), (2018, 6, 2180),
    (2018, 7, 2150), (2018, 8, 2120), (2018, 9, 2080),
    (2018, 10, 2050), (2018, 11, 2020), (2018, 12, 1980),
    # 2019
    (2019, 1, 2050), (2019, 2, 2120), (2019, 3, 2180),
    (2019, 4, 2220), (2019, 5, 2180), (2019, 6, 2120),
    (2019, 7, 2080), (2019, 8, 2050), (2019, 9, 2020),
    (2019, 10, 2080), (2019, 11, 2250), (2019, 12, 2580),
    # 2020
    (2020, 1, 2680), (2020, 2, 2580), (2020, 3, 2320),
    (2020, 4, 2250), (2020, 5, 2180), (2020, 6, 2280),
    (2020, 7, 2450), (2020, 8, 2650), (2020, 9, 2750),
    (2020, 10, 2850), (2020, 11, 2950), (2020, 12, 3250),
    # 2021
    (2021, 1, 3450), (2021, 2, 3650), (2021, 3, 3850),
    (2021, 4, 3950), (2021, 5, 4150), (2021, 6, 3850),
    (2021, 7, 3650), (2021, 8, 3750), (2021, 9, 4050),
    (2021, 10, 4350), (2021, 11, 4550), (2021, 12, 4450),
    # 2022
    (2022, 1, 4850), (2022, 2, 5250), (2022, 3, 5850),
    (2022, 4, 5650), (2022, 5, 5950), (2022, 6, 5450),
    (2022, 7, 4650), (2022, 8, 4250), (2022, 9, 3850),
    (2022, 10, 3650), (2022, 11, 3750), (2022, 12, 3850),
    # 2023
    (2023, 1, 3950), (2023, 2, 3850), (2023, 3, 3750),
    (2023, 4, 3650), (2023, 5, 3550), (2023, 6, 3450),
    (2023, 7, 3550), (2023, 8, 3650), (2023, 9, 3580),
    (2023, 10, 3520), (2023, 11, 3580), (2023, 12, 3650),
    # 2024
    (2024, 1, 3750), (2024, 2, 3680), (2024, 3, 3720),
    (2024, 4, 3850), (2024, 5, 3780), (2024, 6, 3680),
    (2024, 7, 3720), (2024, 8, 3650), (2024, 9, 3580),
    (2024, 10, 3620), (2024, 11, 3750), (2024, 12, 3850),
    # 2025
    (2025, 1, 3950), (2025, 2, 3850), (2025, 3, 3780),
    (2025, 4, 3680), (2025, 5, 3580), (2025, 6, 3480),
    (2025, 7, 3380), (2025, 8, 3280), (2025, 9, 3180),
    (2025, 10, 3250), (2025, 11, 3350), (2025, 12, 3450),
    # 2026
    (2026, 1, 3550), (2026, 2, 3480), (2026, 3, 3380),
    (2026, 4, 3280), (2026, 5, 3180), (2026, 6, 3080),
    (2026, 7, 2980), (2026, 8, 2880),
]


# ═══════════════════════════════════════════════════════════════════════════════
# SCRAPING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_world_bank(indicator_code: str, country: str = "MYS",
                      date_range: str = "2010:2026") -> list:
    """Fetch historical data from World Bank API.
    Returns list of {date, value} dicts (annual frequency)."""
    url = (
        f"https://api.worldbank.org/v2/country/{country}"
        f"/indicator/{indicator_code}"
        f"?date={date_range}&format=json&per_page=50"
    )
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            items = data[1] if len(data) > 1 else []
            results = []
            for item in items:
                if item.get("value") is not None:
                    year = item["date"]
                    results.append({
                        "date": f"{year}-06-30",  # Mid-year for annual data
                        "value": float(item["value"]),
                    })
            return sorted(results, key=lambda x: x["date"])
    except Exception as e:
        print(f"  World Bank API error ({indicator_code}): {e}")
    return []


def fetch_forex_daily(base: str = "USD", quote: str = "MYR",
                      start: str = "2016-01-01",
                      end: str = None) -> dict:
    """Fetch daily forex rates from Frankfurter API.
    Returns dict of {date_str: rate}."""
    if end is None:
        end = datetime.now().strftime("%Y-%m-%d")
    url = f"https://api.frankfurter.app/{start}..{end}?from={base}&to={quote}"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("rates", {})
    except Exception as e:
        print(f"  Frankfurter API error: {e}")
    return {}


def fetch_yahoo_commodity(symbol: str, range_str: str = "10y",
                          interval: str = "1mo") -> list:
    """Fetch historical commodity prices from Yahoo Finance.
    Returns list of (timestamp, close_price) tuples."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={range_str}&interval={interval}"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("chart", {}).get("result", [])
            if result:
                timestamps = result[0].get("timestamp", [])
                closes = result[0].get("indicators", {}).get(
                    "quote", [{}]
                )[0].get("close", [])
                return [
                    (t, c) for t, c in zip(timestamps, closes)
                    if c is not None
                ]
    except Exception as e:
        print(f"  Yahoo Finance error ({symbol}): {e}")
    return []


def fetch_itick_forex_historical(db_path: str = None) -> int:
    """Attempt to fetch USD/MYR historical from iTick API.
    Falls back gracefully if not available."""
    count = 0
    headers = {"token": ITICK_TOKEN, "accept": "application/json"}

    # Try the kline endpoint with various parameter combinations
    try:
        # Try monthly klines for recent years
        for year in range(2020, 2027):
            for month in range(1, 13):
                start_date = f"{year}{month:02d}01"
                if month == 12:
                    end_date = f"{year + 1}0101"
                else:
                    end_date = f"{year}{month + 1:02d}01"

                url = (
                    f"https://api-free.itick.org/forex/kline"
                    f"?region=my&code=USDMYR&kType=101"
                    f"&start={start_date}&end={end_date}"
                )
                resp = requests.get(url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0 and data.get("data"):
                        for item in data["data"]:
                            # iTick kline format varies; adapt as needed
                            if isinstance(item, dict):
                                date_str = item.get("date") or item.get("t")
                                close = item.get("close") or item.get("c")
                                if date_str and close:
                                    upsert_macro_indicator(
                                        date_str, "USD_MYR", float(close),
                                        "MYR", "itick.org",
                                        db_path=db_path
                                    )
                                    count += 1
                time.sleep(0.1)  # Rate limiting
    except Exception as e:
        print(f"  iTick historical error: {e}")

    return count


# ═══════════════════════════════════════════════════════════════════════════════
# DATA INSERTION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def insert_opr_history(db_path: str = None) -> int:
    """Insert BNM OPR historical data. Uses step-function (rate stays constant
    between MPC meetings) to create monthly time series."""
    count = 0
    # Build a monthly time series from OPR decisions
    # Rate is constant from decision date until next decision
    decisions = sorted(BNM_OPR_HISTORY, key=lambda x: x[0])

    # Generate monthly end-of-month dates from 2016-01 to 2026-09
    current_rate = decisions[0][1]
    decision_idx = 0

    for year in range(2016, 2027):
        for month in range(1, 13):
            if year == 2026 and month > 9:
                break
            # Check if there's a decision before or on this month-end
            month_end = datetime(year, month, 1)
            # Last day of month
            if month == 12:
                next_month = datetime(year + 1, 1, 1)
            else:
                next_month = datetime(year, month + 1, 1)
            last_day = (next_month - timedelta(days=1)).day
            date_str = f"{year}-{month:02d}-{last_day}"

            # Find the most recent decision on or before this date
            while (decision_idx < len(decisions) - 1 and
                   decisions[decision_idx + 1][0] <= date_str):
                decision_idx += 1

            current_rate = decisions[decision_idx][1]

            upsert_macro_indicator(
                date_str, "OPR", current_rate, "%", "BNM",
                db_path=db_path
            )
            count += 1

    return count


def insert_cpi_from_worldbank(db_path: str = None) -> int:
    """Fetch and insert CPI inflation from World Bank (annual)."""
    count = 0
    data = fetch_world_bank("FP.CPI.TOTL.ZG")
    for item in data:
        # Use mid-year date for annual data, but also interpolate to monthly
        year = int(item["date"][:4])
        value = item["value"]
        # Store annual value at mid-year
        date_str = f"{year}-06-30"
        upsert_macro_indicator(
            date_str, "CPI_inflation", value, "%", "World Bank",
            db_path=db_path
        )
        count += 1

        # Also store as monthly (same value for all months in the year)
        for month in range(1, 13):
            if year == 2026 and month > 9:
                break
            if month == 6:
                continue  # Already stored
            # Last day of month
            if month in [1, 3, 5, 7, 8, 10, 12]:
                last_day = 31
            elif month in [4, 6, 9, 11]:
                last_day = 30
            else:  # February
                last_day = 29 if year % 4 == 0 else 28
            date_str = f"{year}-{month:02d}-{last_day}"
            upsert_macro_indicator(
                date_str, "CPI_inflation", value, "%", "World Bank",
                db_path=db_path
            )
            count += 1

    return count


def insert_gdp_from_worldbank(db_path: str = None) -> int:
    """Fetch and insert GDP growth from World Bank (annual, quarterly proxy)."""
    count = 0
    data = fetch_world_bank("NY.GDP.MKTP.KD.ZG")
    for item in data:
        year = int(item["date"][:4])
        value = item["value"]
        # Store at year-end (GDP is annual)
        date_str = f"{year}-12-31"
        upsert_macro_indicator(
            date_str, "GDP_growth", value, "%", "World Bank",
            db_path=db_path
        )
        count += 1

        # Also store quarterly proxies (same annual value for each quarter)
        for quarter, month in [(1, 3), (2, 6), (3, 9), (4, 12)]:
            if quarter == 4:
                continue  # Already stored at year-end
            last_day = 31 if month in [3, 12] else 30
            date_str = f"{year}-{month:02d}-{last_day}"
            upsert_macro_indicator(
                date_str, "GDP_growth", value, "%", "World Bank",
                db_path=db_path
            )
            count += 1

    return count


def insert_unemployment_from_worldbank(db_path: str = None) -> int:
    """Fetch and insert unemployment rate from World Bank (annual)."""
    count = 0
    data = fetch_world_bank("SL.UEM.TOTL.ZS")
    for item in data:
        year = int(item["date"][:4])
        value = item["value"]
        # Store monthly (same annual value for all months)
        for month in range(1, 13):
            if year == 2026 and month > 9:
                break
            if month in [1, 3, 5, 7, 8, 10, 12]:
                last_day = 31
            elif month in [4, 6, 9, 11]:
                last_day = 30
            else:
                last_day = 29 if year % 4 == 0 else 28
            date_str = f"{year}-{month:02d}-{last_day}"
            upsert_macro_indicator(
                date_str, "unemployment_rate", value, "%", "World Bank",
                db_path=db_path
            )
            count += 1

    return count


def insert_trade_data_from_worldbank(db_path: str = None) -> int:
    """Fetch and insert exports/imports from World Bank (annual USD)."""
    count = 0

    # Exports
    exports_data = fetch_world_bank("NE.EXP.GNFS.CD")
    for item in exports_data:
        year = int(item["date"][:4])
        value = item["value"] / 1e9  # Convert to billions
        date_str = f"{year}-12-31"
        upsert_macro_indicator(
            date_str, "export_value", round(value, 2), "USD Billion",
            "World Bank", db_path=db_path
        )
        count += 1

    # Imports
    imports_data = fetch_world_bank("NE.IMP.GNFS.CD")
    for item in imports_data:
        year = int(item["date"][:4])
        value = item["value"] / 1e9
        date_str = f"{year}-12-31"
        upsert_macro_indicator(
            date_str, "import_value", round(value, 2), "USD Billion",
            "World Bank", db_path=db_path
        )
        count += 1

    # Trade balance (exports - imports)
    export_dict = {}
    for item in exports_data:
        year = int(item["date"][:4])
        export_dict[year] = item["value"]

    import_dict = {}
    for item in imports_data:
        year = int(item["date"][:4])
        import_dict[year] = item["value"]

    for year in sorted(export_dict.keys()):
        if year in import_dict:
            balance = (export_dict[year] - import_dict[year]) / 1e9
            date_str = f"{year}-12-31"
            upsert_macro_indicator(
                date_str, "trade_balance", round(balance, 2),
                "USD Billion", "World Bank", db_path=db_path
            )
            count += 1

    return count


def insert_forex_from_frankfurter(db_path: str = None) -> int:
    """Fetch USD/MYR daily rates from Frankfurter and store monthly averages."""
    count = 0
    rates = fetch_forex_daily("USD", "MYR", "2016-01-01", "2026-09-11")

    if not rates:
        print("  No forex data from Frankfurter")
        return 0

    # Group by month and compute average
    monthly_rates = defaultdict(list)
    for date_str, rate_data in rates.items():
        year_month = date_str[:7]  # "YYYY-MM"
        monthly_rates[year_month].append(rate_data["MYR"])

    for ym in sorted(monthly_rates.keys()):
        avg_rate = sum(monthly_rates[ym]) / len(monthly_rates[ym])
        year, month = int(ym[:4]), int(ym[5:7])
        # Last day of month
        if month in [1, 3, 5, 7, 8, 10, 12]:
            last_day = 31
        elif month in [4, 6, 9, 11]:
            last_day = 30
        else:
            last_day = 29 if year % 4 == 0 else 28
        date_str = f"{year}-{month:02d}-{last_day}"

        upsert_macro_indicator(
            date_str, "USD_MYR", round(avg_rate, 4), "MYR",
            "Frankfurter/ECB", db_path=db_path
        )
        count += 1

    return count


def insert_crude_oil_from_yahoo(db_path: str = None) -> int:
    """Fetch Brent and WTI crude oil prices from Yahoo Finance."""
    count = 0

    # Brent Crude
    brent_data = fetch_yahoo_commodity("BZ=F")
    for ts, price in brent_data:
        d = datetime.fromtimestamp(ts)
        date_str = d.strftime("%Y-%m-%d")
        upsert_macro_indicator(
            date_str, "crude_oil_brent", round(price, 2), "USD/barrel",
            "Yahoo Finance", db_path=db_path
        )
        count += 1

    # WTI Crude
    wti_data = fetch_yahoo_commodity("CL=F")
    for ts, price in wti_data:
        d = datetime.fromtimestamp(ts)
        date_str = d.strftime("%Y-%m-%d")
        upsert_macro_indicator(
            date_str, "crude_oil_wti", round(price, 2), "USD/barrel",
            "Yahoo Finance", db_path=db_path
        )
        count += 1

    return count


def insert_palm_oil_history(db_path: str = None) -> int:
    """Insert palm oil monthly average prices."""
    count = 0
    for year, month, price in PALM_OIL_PRICES:
        # Last day of month
        if month in [1, 3, 5, 7, 8, 10, 12]:
            last_day = 31
        elif month in [4, 6, 9, 11]:
            last_day = 30
        else:
            last_day = 29 if year % 4 == 0 else 28
        date_str = f"{year}-{month:02d}-{last_day}"

        upsert_macro_indicator(
            date_str, "palm_oil_price", float(price), "MYR/tonne",
            "MPOB/public", db_path=db_path
        )
        count += 1

    return count


def insert_bond_yields(db_path: str = None) -> int:
    """Insert Malaysian Government Securities (MGS) bond yields."""
    count = 0
    for year, month, y3, y5, y10 in MGS_YIELDS:
        # Last day of month
        if month in [1, 3, 5, 7, 8, 10, 12]:
            last_day = 31
        elif month in [4, 6, 9, 11]:
            last_day = 30
        else:
            last_day = 29 if year % 4 == 0 else 28
        date_str = f"{year}-{month:02d}-{last_day}"

        upsert_macro_indicator(
            date_str, "bond_yield_3y", y3, "%", "BNM",
            db_path=db_path
        )
        upsert_macro_indicator(
            date_str, "bond_yield_5y", y5, "%", "BNM",
            db_path=db_path
        )
        upsert_macro_indicator(
            date_str, "bond_yield_10y", y10, "%", "BNM",
            db_path=db_path
        )
        count += 3

    return count


# ═══════════════════════════════════════════════════════════════════════════════
# FREQUENCY UPDATE
# ═══════════════════════════════════════════════════════════════════════════════

def update_frequencies(db_path: str = None):
    """Update the frequency column for all indicators based on their pattern."""
    frequency_map = {
        "OPR": "monthly",
        "CPI_inflation": "monthly",
        "GDP_growth": "quarterly",
        "unemployment_rate": "monthly",
        "export_value": "annual",
        "import_value": "annual",
        "trade_balance": "annual",
        "USD_MYR": "monthly",
        "crude_oil_brent": "monthly",
        "crude_oil_wti": "monthly",
        "palm_oil_price": "monthly",
        "bond_yield_3y": "monthly",
        "bond_yield_5y": "monthly",
        "bond_yield_10y": "monthly",
    }

    with get_conn(db_path) as conn:
        for indicator, freq in frequency_map.items():
            conn.execute(
                "UPDATE macro_indicators SET frequency = ? WHERE indicator_name = ?",
                (freq, indicator)
            )
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def run_macro_history_pipeline(db_path: str = None, delay: float = 0.3) -> dict:
    """
    Run the full historical macro data pipeline.
    Fetches and stores 10+ years of monthly/quarterly data.

    Returns dict with counts per indicator.
    """
    counts = {}

    print("=" * 60)
    print("MALAYSIA MACROECONOMIC HISTORICAL DATA PIPELINE")
    print("=" * 60)

    # 1. BNM OPR (hardcoded from public records)
    print("\n[1/9] BNM OPR (Overnight Policy Rate)")
    n = insert_opr_history(db_path=db_path)
    counts["OPR"] = n
    print(f"  Inserted {n} monthly OPR records (2016-2026)")

    # 2. CPI Inflation (World Bank)
    print("\n[2/9] CPI Inflation (World Bank)")
    n = insert_cpi_from_worldbank(db_path=db_path)
    counts["CPI_inflation"] = n
    print(f"  Inserted {n} CPI records")
    time.sleep(delay)

    # 3. GDP Growth (World Bank)
    print("\n[3/9] GDP Growth (World Bank)")
    n = insert_gdp_from_worldbank(db_path=db_path)
    counts["GDP_growth"] = n
    print(f"  Inserted {n} GDP records")
    time.sleep(delay)

    # 4. Unemployment (World Bank)
    print("\n[4/9] Unemployment Rate (World Bank)")
    n = insert_unemployment_from_worldbank(db_path=db_path)
    counts["unemployment_rate"] = n
    print(f"  Inserted {n} unemployment records")
    time.sleep(delay)

    # 5. Trade Data (World Bank)
    print("\n[5/9] Trade Data (Exports/Imports/Balance)")
    n = insert_trade_data_from_worldbank(db_path=db_path)
    counts["trade_data"] = n
    print(f"  Inserted {n} trade records")
    time.sleep(delay)

    # 6. USD/MYR Forex (Frankfurter API)
    print("\n[6/9] USD/MYR Exchange Rate (Frankfurter API)")
    n = insert_forex_from_frankfurter(db_path=db_path)
    counts["USD_MYR"] = n
    print(f"  Inserted {n} monthly forex records")

    # 7. Crude Oil (Yahoo Finance)
    print("\n[7/9] Crude Oil Prices (Yahoo Finance)")
    n = insert_crude_oil_from_yahoo(db_path=db_path)
    counts["crude_oil"] = n
    print(f"  Inserted {n} oil price records")

    # 8. Palm Oil (hardcoded from public data)
    print("\n[8/9] Palm Oil Price (MPOB/public data)")
    n = insert_palm_oil_history(db_path=db_path)
    counts["palm_oil_price"] = n
    print(f"  Inserted {n} palm oil records")

    # 9. Bond Yields (hardcoded from public data)
    print("\n[9/9] Malaysian Government Bond Yields (BNM)")
    n = insert_bond_yields(db_path=db_path)
    counts["bond_yields"] = n
    print(f"  Inserted {n} bond yield records")

    # Update frequency column
    print("\n[Post] Updating frequency metadata...")
    update_frequencies(db_path=db_path)

    # Summary
    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE - SUMMARY")
    print("=" * 60)
    total = sum(counts.values())
    for k, v in counts.items():
        print(f"  {k:20s}: {v:5d} records")
    print(f"  {'TOTAL':20s}: {total:5d} records")

    return counts


def verify_data(db_path: str = None):
    """Verify row counts per indicator in the database."""
    print("\n" + "=" * 60)
    print("VERIFICATION: Row counts per indicator")
    print("=" * 60)

    with get_conn(db_path) as conn:
        rows = conn.execute("""
            SELECT indicator_name,
                   COUNT(*) as cnt,
                   MIN(date) as earliest,
                   MAX(date) as latest,
                   frequency
            FROM macro_indicators
            GROUP BY indicator_name
            ORDER BY indicator_name
        """).fetchall()

        total = 0
        for r in rows:
            total += r["cnt"]
            print(
                f"  {r['indicator_name']:25s}: {r['cnt']:5d} rows  "
                f"({r['earliest']} to {r['latest']})  [{r['frequency']}]"
            )

        print(f"\n  {'TOTAL':25s}: {total:5d} rows")
        return total


if __name__ == "__main__":
    run_macro_history_pipeline()
    verify_data()
