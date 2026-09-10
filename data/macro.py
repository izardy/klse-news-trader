"""
Macroeconomic data pipeline for Malaysia.
Scrapes and stores: OPR, CPI, GDP, trade data, forex, commodities, bond yields, macro calendar.
Sources: BNM, DOSM, TradingEconomics, iTick, web_search/web_extract.
"""
import os
import sys
import re
import time
import json
import requests
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import upsert_macro_indicator, upsert_macro_calendar, get_latest_macro

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

ITICK_TOKEN = os.environ.get(
    "ITICK_TOKEN",
    "c01d09b814fb4f06bc75f0fea5aedff29d48e8f8c5324918b8d89e9150492375"
)


# ─── BNM OPR ──────────────────────────────────────────────────────────────────

def scrape_bnm_opr(db_path: str = None) -> list:
    """
    Scrape BNM Overnight Policy Rate from tradingeconomics.com or BNM website.
    Returns list of dicts with date, value.
    """
    results = []
    url = "https://tradingeconomics.com/malaysia/investor-interest-rate"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            # Look for the current OPR value
            # TradingEconomics has a pattern like: <div ...>3.00</div> near "Malaysia Interest Rate"
            rate_match = re.search(
                r'Interest Rate.*?([\d.]+)\s*%',
                html, re.DOTALL | re.IGNORECASE
            )
            if not rate_match:
                rate_match = re.search(
                    r'Overnight Policy Rate.*?([\d.]+)',
                    html, re.DOTALL | re.IGNORECASE
                )
            if rate_match:
                value = float(rate_match.group(1))
                today = datetime.now().strftime("%Y-%m-%d")
                upsert_macro_indicator(today, "OPR", value, "%", "tradingeconomics.com", db_path=db_path)
                results.append({"date": today, "indicator": "OPR", "value": value, "unit": "%"})
                print(f"  OPR: {value}%")
    except Exception as e:
        print(f"  OPR scrape error: {e}")

    # Also try to get historical OPR from BNM
    try:
        bnm_url = "https://www.bnm.gov.my/monetary-stability/opr"
        resp = requests.get(bnm_url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            # BNM page has historical OPR decisions
            html = resp.text
            # Pattern: date and rate pairs
            decisions = re.findall(
                r'(\d{1,2}\s+\w+\s+\d{4}).*?([\d.]+)\s*%',
                html, re.DOTALL | re.IGNORECASE
            )
            for date_str, rate in decisions[:12]:
                try:
                    dt = datetime.strptime(date_str.strip(), "%d %B %Y")
                    date_iso = dt.strftime("%Y-%m-%d")
                    upsert_macro_indicator(date_iso, "OPR", float(rate), "%", "bnm.gov.my", db_path=db_path)
                    results.append({"date": date_iso, "indicator": "OPR", "value": float(rate), "unit": "%"})
                except ValueError:
                    pass
    except Exception as e:
        print(f"  BNM OPR history error: {e}")

    return results


# ─── DOSM CPI (Inflation) ────────────────────────────────────────────────────

def scrape_dosm_cpi(db_path: str = None) -> list:
    """
    Scrape CPI/inflation data from DOSM or tradingeconomics.
    """
    results = []
    url = "https://tradingeconomics.com/malaysia/inflation-cpi"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            # Current inflation rate
            rate_match = re.search(
                r'Inflation Rate.*?([\d.]+)\s*%',
                html, re.DOTALL | re.IGNORECASE
            )
            if not rate_match:
                rate_match = re.search(
                    r'CPI.*?([\d.]+)\s*%',
                    html, re.DOTALL | re.IGNORECASE
                )
            if rate_match:
                value = float(rate_match.group(1))
                today = datetime.now().strftime("%Y-%m-%d")
                upsert_macro_indicator(today, "CPI_inflation", value, "%", "tradingeconomics.com", db_path=db_path)
                results.append({"date": today, "indicator": "CPI_inflation", "value": value, "unit": "%"})
                print(f"  CPI inflation: {value}%")
    except Exception as e:
        print(f"  CPI scrape error: {e}")

    return results


# ─── DOSM GDP ─────────────────────────────────────────────────────────────────

def scrape_dosm_gdp(db_path: str = None) -> list:
    """
    Scrape GDP growth data from tradingeconomics or DOSM.
    """
    results = []
    url = "https://tradingeconomics.com/malaysia/gdp-growth-annual"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            rate_match = re.search(
                r'GDP Growth Rate.*?([\d.]+)\s*%',
                html, re.DOTALL | re.IGNORECASE
            )
            if not rate_match:
                rate_match = re.search(
                    r'GDP.*?Growth.*?([\d.]+)\s*%',
                    html, re.DOTALL | re.IGNORECASE
                )
            if rate_match:
                value = float(rate_match.group(1))
                today = datetime.now().strftime("%Y-%m-%d")
                upsert_macro_indicator(today, "GDP_growth", value, "%", "tradingeconomics.com", db_path=db_path)
                results.append({"date": today, "indicator": "GDP_growth", "value": value, "unit": "%"})
                print(f"  GDP growth: {value}%")
    except Exception as e:
        print(f"  GDP scrape error: {e}")

    return results


# ─── Trade Data ───────────────────────────────────────────────────────────────

def scrape_trade_data(db_path: str = None) -> list:
    """
    Scrape export/import/trade balance data from tradingeconomics.
    """
    results = []

    # Exports
    try:
        url = "https://tradingeconomics.com/malaysia/exports"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            val_match = re.search(r'Exports.*?([\d.]+)\s*(Billion|USD)', html, re.DOTALL | re.IGNORECASE)
            if val_match:
                value = float(val_match.group(1))
                today = datetime.now().strftime("%Y-%m-%d")
                upsert_macro_indicator(today, "export_value", value, "USD Billion", "tradingeconomics.com", db_path=db_path)
                results.append({"indicator": "export_value", "value": value})
                print(f"  Exports: {value} USD Billion")
    except Exception as e:
        print(f"  Exports error: {e}")

    # Imports
    try:
        url = "https://tradingeconomics.com/malaysia/imports"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            val_match = re.search(r'Imports.*?([\d.]+)\s*(Billion|USD)', html, re.DOTALL | re.IGNORECASE)
            if val_match:
                value = float(val_match.group(1))
                today = datetime.now().strftime("%Y-%m-%d")
                upsert_macro_indicator(today, "import_value", value, "USD Billion", "tradingeconomics.com", db_path=db_path)
                results.append({"indicator": "import_value", "value": value})
                print(f"  Imports: {value} USD Billion")
    except Exception as e:
        print(f"  Imports error: {e}")

    # Trade balance
    try:
        url = "https://tradingeconomics.com/malaysia/balance-of-trade"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            val_match = re.search(r'Trade Balance.*?([-\d.]+)\s*(Billion|USD)', html, re.DOTALL | re.IGNORECASE)
            if val_match:
                value = float(val_match.group(1))
                today = datetime.now().strftime("%Y-%m-%d")
                upsert_macro_indicator(today, "trade_balance", value, "USD Billion", "tradingeconomics.com", db_path=db_path)
                results.append({"indicator": "trade_balance", "value": value})
                print(f"  Trade Balance: {value} USD Billion")
    except Exception as e:
        print(f"  Trade balance error: {e}")

    return results


# ─── Forex Rates ──────────────────────────────────────────────────────────────

def scrape_forex(db_path: str = None) -> list:
    """
    Fetch USD/MYR and SGD/MYR rates from iTick API.
    """
    results = []
    headers = {"token": ITICK_TOKEN, "accept": "application/json"}

    # USD/MYR
    try:
        url = "https://api-free.itick.org/forex/ticks?region=my&codes=USDMYR"
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        if data.get("code") == 0 and data.get("data"):
            quote = data["data"].get("USDMYR", {})
            if quote.get("ld"):
                value = quote["ld"]
                today = datetime.now().strftime("%Y-%m-%d")
                upsert_macro_indicator(today, "USD_MYR", value, "MYR", "itick.org", db_path=db_path)
                results.append({"indicator": "USD_MYR", "value": value})
                print(f"  USD/MYR: {value}")
    except Exception as e:
        print(f"  USD/MYR error: {e}")

    # SGD/MYR
    try:
        url = "https://api-free.itick.org/forex/ticks?region=my&codes=SGDMYR"
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        if data.get("code") == 0 and data.get("data"):
            quote = data["data"].get("SGDMYR", {})
            if quote.get("ld"):
                value = quote["ld"]
                today = datetime.now().strftime("%Y-%m-%d")
                upsert_macro_indicator(today, "SGD_MYR", value, "MYR", "itick.org", db_path=db_path)
                results.append({"indicator": "SGD_MYR", "value": value})
                print(f"  SGD/MYR: {value}")
    except Exception as e:
        print(f"  SGD/MYR error: {e}")

    return results


# ─── Commodities ──────────────────────────────────────────────────────────────

def scrape_commodities(db_path: str = None) -> list:
    """
    Scrape crude oil (WTI/Brent) and palm oil prices from tradingeconomics.
    """
    results = []

    # WTI Crude Oil
    try:
        url = "https://tradingeconomics.com/commodity/crude-oil"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            val_match = re.search(r'Crude Oil.*?([\d.]+)\s*(USD|per barrel)', html, re.DOTALL | re.IGNORECASE)
            if val_match:
                value = float(val_match.group(1))
                today = datetime.now().strftime("%Y-%m-%d")
                upsert_macro_indicator(today, "crude_oil_wti", value, "USD/barrel", "tradingeconomics.com", db_path=db_path)
                results.append({"indicator": "crude_oil_wti", "value": value})
                print(f"  WTI Crude: ${value}")
    except Exception as e:
        print(f"  WTI error: {e}")

    # Brent Crude Oil
    try:
        url = "https://tradingeconomics.com/commodity/brent-crude-oil"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            val_match = re.search(r'Brent.*?([\d.]+)\s*(USD|per barrel)', html, re.DOTALL | re.IGNORECASE)
            if val_match:
                value = float(val_match.group(1))
                today = datetime.now().strftime("%Y-%m-%d")
                upsert_macro_indicator(today, "crude_oil_brent", value, "USD/barrel", "tradingeconomics.com", db_path=db_path)
                results.append({"indicator": "crude_oil_brent", "value": value})
                print(f"  Brent Crude: ${value}")
    except Exception as e:
        print(f"  Brent error: {e}")

    # Palm Oil
    try:
        url = "https://tradingeconomics.com/commodity/palm-oil"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            val_match = re.search(r'Palm Oil.*?([\d.]+)', html, re.DOTALL | re.IGNORECASE)
            if val_match:
                value = float(val_match.group(1))
                today = datetime.now().strftime("%Y-%m-%d")
                upsert_macro_indicator(today, "palm_oil_price", value, "MYR/tonne", "tradingeconomics.com", db_path=db_path)
                results.append({"indicator": "palm_oil_price", "value": value})
                print(f"  Palm Oil: {value} MYR/tonne")
    except Exception as e:
        print(f"  Palm oil error: {e}")

    return results


# ─── Bond Yields ──────────────────────────────────────────────────────────────

def scrape_bond_yields(db_path: str = None) -> list:
    """
    Scrape Malaysian government bond yields from tradingeconomics.
    """
    results = []

    try:
        url = "https://tradingeconomics.com/malaysia/government-bond-yield"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            val_match = re.search(
                r'Government Bond Yield.*?([\d.]+)\s*%',
                html, re.DOTALL | re.IGNORECASE
            )
            if not val_match:
                val_match = re.search(
                    r'10Y.*?([\d.]+)\s*%',
                    html, re.DOTALL | re.IGNORECASE
                )
            if val_match:
                value = float(val_match.group(1))
                today = datetime.now().strftime("%Y-%m-%d")
                upsert_macro_indicator(today, "government_bond_10y", value, "%", "tradingeconomics.com", db_path=db_path)
                results.append({"indicator": "government_bond_10y", "value": value})
                print(f"  MGS 10Y: {value}%")
    except Exception as e:
        print(f"  Bond yield error: {e}")

    return results


# ─── Unemployment Rate ────────────────────────────────────────────────────────

def scrape_unemployment(db_path: str = None) -> list:
    """
    Scrape unemployment rate from tradingeconomics.
    """
    results = []

    try:
        url = "https://tradingeconomics.com/malaysia/unemployment-rate"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            val_match = re.search(
                r'Unemployment Rate.*?([\d.]+)\s*%',
                html, re.DOTALL | re.IGNORECASE
            )
            if val_match:
                value = float(val_match.group(1))
                today = datetime.now().strftime("%Y-%m-%d")
                upsert_macro_indicator(today, "unemployment_rate", value, "%", "tradingeconomics.com", db_path=db_path)
                results.append({"indicator": "unemployment_rate", "value": value})
                print(f"  Unemployment: {value}%")
    except Exception as e:
        print(f"  Unemployment error: {e}")

    return results


# ─── Macro Calendar ───────────────────────────────────────────────────────────

def scrape_macro_calendar(db_path: str = None) -> list:
    """
    Scrape upcoming macro events from tradingeconomics.com/calendar.
    """
    results = []

    try:
        url = "https://tradingeconomics.com/calendar"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            # Look for Malaysia events in the calendar
            # Pattern: event rows with date, name, previous, forecast
            events = re.findall(
                r'<tr[^>]*>.*?<td[^>]*>(.*?)</td>.*?<td[^>]*>(.*?)</td>.*?<td[^>]*>(.*?)</td>.*?<td[^>]*>(.*?)</td>.*?<td[^>]*>(.*?)</td>.*?</tr>',
                html, re.DOTALL | re.IGNORECASE
            )
            for event in events:
                date_str = re.sub(r'<[^>]+>', '', event[0]).strip()
                name = re.sub(r'<[^>]+>', '', event[1]).strip()
                country = re.sub(r'<[^>]+>', '', event[2]).strip()
                prev = re.sub(r'<[^>]+>', '', event[3]).strip()
                forecast = re.sub(r'<[^>]+>', '', event[4]).strip()

                if "Malaysia" in country or "MY" in country:
                    try:
                        # Parse date
                        dt = datetime.strptime(date_str, "%b %d, %Y")
                        event_date = dt.strftime("%Y-%m-%d")
                        upsert_macro_calendar(
                            event_date=event_date,
                            event_name=name,
                            country="MY",
                            importance="medium",
                            previous=float(prev) if prev and prev != "-" else None,
                            forecast=float(forecast) if forecast and forecast != "-" else None,
                            db_path=db_path,
                        )
                        results.append({"date": event_date, "event": name})
                    except (ValueError, TypeError):
                        pass

            print(f"  Found {len(results)} Malaysia macro events")
    except Exception as e:
        print(f"  Calendar error: {e}")

    # Also add known recurring BNM events
    _seed_bnm_meetings(db_path=db_path)

    return results


def _seed_bnm_meetings(db_path: str = None):
    """Seed known BNM Monetary Policy Committee meeting dates for 2026."""
    # BNM MPC typically meets 6 times per year (Jan, Mar, May, Jul, Sep, Nov)
    mpc_dates_2026 = [
        ("2026-01-22", "BNM MPC Meeting - OPR Decision"),
        ("2026-03-05", "BNM MPC Meeting - OPR Decision"),
        ("2026-05-07", "BNM MPC Meeting - OPR Decision"),
        ("2026-07-09", "BNM MPC Meeting - OPR Decision"),
        ("2026-09-03", "BNM MPC Meeting - OPR Decision"),
        ("2026-11-05", "BNM MPC Meeting - OPR Decision"),
    ]

    for date_str, event_name in mpc_dates_2026:
        upsert_macro_calendar(
            event_date=date_str,
            event_name=event_name,
            country="MY",
            importance="high",
            db_path=db_path,
        )

    # Add key US events that affect MY markets
    us_events_2026 = [
        ("2026-01-09", "US Non-Farm Payrolls (Dec)", "high"),
        ("2026-02-06", "US Non-Farm Payrolls (Jan)", "high"),
        ("2026-03-06", "US Non-Farm Payrolls (Feb)", "high"),
        ("2026-01-15", "US CPI Release (Dec)", "high"),
        ("2026-02-12", "US CPI Release (Jan)", "high"),
        ("2026-03-18", "FOMC Meeting", "high"),
        ("2026-04-29", "FOMC Meeting", "high"),
        ("2026-06-17", "FOMC Meeting", "high"),
        ("2026-07-29", "FOMC Meeting", "high"),
        ("2026-09-16", "FOMC Meeting", "high"),
        ("2026-11-04", "FOMC Meeting", "high"),
        ("2026-12-16", "FOMC Meeting", "high"),
    ]

    for date_str, event_name, importance in us_events_2026:
        upsert_macro_calendar(
            event_date=date_str,
            event_name=event_name,
            country="US",
            importance=importance,
            db_path=db_path,
        )


# ─── Full Pipeline ────────────────────────────────────────────────────────────

def run_full_macro_backfill(db_path: str = None, delay: float = 0.5) -> dict:
    """
    Run all macro scraping functions and store results.
    Returns dict with counts per indicator.
    """
    counts = {}

    print("=== Macroeconomic Data Backfill ===\n")

    print("1. BNM OPR (Overnight Policy Rate)")
    results = scrape_bnm_opr(db_path=db_path)
    counts["OPR"] = len(results)
    time.sleep(delay)

    print("\n2. CPI Inflation")
    results = scrape_dosm_cpi(db_path=db_path)
    counts["CPI_inflation"] = len(results)
    time.sleep(delay)

    print("\n3. GDP Growth")
    results = scrape_dosm_gdp(db_path=db_path)
    counts["GDP_growth"] = len(results)
    time.sleep(delay)

    print("\n4. Trade Data (Exports/Imports/Balance)")
    results = scrape_trade_data(db_path=db_path)
    counts["trade_data"] = len(results)
    time.sleep(delay)

    print("\n5. Forex Rates (USD/MYR, SGD/MYR)")
    results = scrape_forex(db_path=db_path)
    counts["forex"] = len(results)
    time.sleep(delay)

    print("\n6. Commodities (Oil, Palm Oil)")
    results = scrape_commodities(db_path=db_path)
    counts["commodities"] = len(results)
    time.sleep(delay)

    print("\n7. Bond Yields")
    results = scrape_bond_yields(db_path=db_path)
    counts["bond_yields"] = len(results)
    time.sleep(delay)

    print("\n8. Unemployment Rate")
    results = scrape_unemployment(db_path=db_path)
    counts["unemployment"] = len(results)
    time.sleep(delay)

    print("\n9. Macro Calendar")
    results = scrape_macro_calendar(db_path=db_path)
    counts["calendar"] = len(results)

    print(f"\n=== Macro backfill complete ===")
    for k, v in counts.items():
        print(f"  {k}: {v} data points")

    return counts


if __name__ == "__main__":
    from data.db import init_db
    init_db()
    run_full_macro_backfill()
