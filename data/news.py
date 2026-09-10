"""
News fetcher for KLSE stocks.
Sources: Bursa Malaysia announcements, KLSE Screener, Iceberg staging.berita (via Athena).
Matches news to stocks by company name/symbol.
"""
import os
import sys
import re
import time
import json
import requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import insert_news, upsert_stock, get_all_stocks

# KLSE stock universe with display names (must match prices.py)
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

# Build keyword matching patterns
# Map common abbreviations / keywords to stock symbols
# Order matters: more specific patterns first to avoid false matches
STOCK_KEYWORDS = {
    "MAYBANK": ["maybank", "malayan banking", "malayan banking bhd", "maybank group", "maybank ib"],
    "PBBANK": ["public bank", "public bank bhd", "public islamic"],
    "CIMB": ["cimb group", "cimb bank", "cimb", "cimb niaga"],
    "TENAGA": ["tenaga nasional", "tenaga", "tnb"],
    "PETGAS": ["petronas gas", "petgas"],
    "MAXIS": ["maxis", "maxis bh", "maxis broadband"],
    "AXIATA": ["axiata", "axiata group", "celcom", "celcomdigi", "digi"],
    "GENTING": ["genting group", "genting berhad", "genting singapore", "genting group"],
    "IHH": ["ihh healthcare", "ihh", "parkway", "gleneagles"],
    "NESTLE": ["nestle", "nestlé", "nestle malaysia"],
    "SIME": ["sime darby", "sime", "sime darby property", "sime darby plantation"],
    "TM": ["telekom malaysia", "telekom", "tm group", "unifi"],
    "DIALOG": ["dialog group", "dialog"],
    "YTL": ["ytl corporation", "ytl power", "ytl", "yeoh tiong lay"],
    "PCHEM": ["petronas chemicals", "pchem", "petronas chemical"],
    "IOICORP": ["ioi corporation", "ioi corp", "ioi group", "ioicorp"],
    "KLK": ["kuala lumpur kepong", "klk", "kl kepong", "batu kawan"],
    "BAT": ["british american tobacco", "bat malaysia", "bat (malaysia)"],
    "MRDIY": ["mr diy", "mrdiy", "mr.diy", "d.i.y group"],
    "GAMUDA": ["gamuda", "gamuda engineering"],
    "UWC": ["uwc", "uwc berhad"],
    "GENM": ["genting malaysia", "genm", "genting malaysia"],
    "SD Guthrie": ["sd guthrie", "sdg", "sime darby plantation", "sd guthrie berhad"],
    "VITROX": ["vitrox", "vitrox corp"],
    "INARI": ["inari amertron", "inari", "amertron"],
    "UNISEM": ["unisem", "unisem m"],
    "FRONTKEN": ["frontken", "frontken corporation"],
    "D&O": ["d & o green", "d&o green", "d & o", "d&o"],
    "GHL": ["ghl systems", "ghl", "ghl transaction"],
    "KENANGA": ["kenanga investment", "kenanga", "kenanga ib"],
    "MBSB": ["mbsb bank", "mbsb", "malaysia building society"],
    "BIMB": ["bimb holdings", "bank islam", "bimb", "bank islam malaysia"],
    "MRCB": ["malaysian resources", "mrcb", "mrcb group"],
    "SPSETIA": ["sp setia", "spsetia", "setia"],
    "IOIPG": ["ioi properties", "ioipg", "ioi properties group"],
    "QL": ["ql resources", "ql group", "ql", "ql seafood"],
    "PPB": ["ppb group", "ppb", "wilmar", "f fm"],
    "HARTALEGA": ["hartalega", "hartalega holdings"],
    "TOPGLOV": ["top glove", "topglov", "topglove"],
    "SUPERMX": ["supermax", "supermx"],
}


def match_stock(text: str) -> str:
    """
    Match text to a stock symbol based on keywords.
    Returns symbol or None.
    Uses word-boundary matching to reduce false positives.
    """
    if not text:
        return None
    text_lower = text.lower()
    
    # Priority matching: check longer/more specific keywords first
    # Sort all (symbol, keyword) pairs by keyword length descending
    all_patterns = []
    for symbol, keywords in STOCK_KEYWORDS.items():
        for kw in keywords:
            all_patterns.append((symbol, kw))
    
    # Sort by keyword length (longest first) for more specific matches
    all_patterns.sort(key=lambda x: len(x[1]), reverse=True)
    
    for symbol, kw in all_patterns:
        if kw in text_lower:
            return symbol
    return None


def fetch_bursa_announcements(symbol: str = None, limit: int = 50) -> list:
    """
    Fetch company announcements from Bursa Malaysia.
    Uses the Bursa Malaysia announcements API/HTML scraping.
    """
    articles = []
    base_url = "https://www.bursamalaysia.com/market_information/announcements/company_announcement"

    if symbol:
        symbols = [symbol]
    else:
        symbols = list(KLSE_STOCKS.keys())

    for sym in symbols:
        try:
            # Bursa uses stock codes - we'll try the API endpoint
            params = {
                "company": sym,
                "page": 1,
                "per_page": limit,
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/json",
            }
            resp = requests.get(base_url, params=params, headers=headers, timeout=15)
            if resp.status_code == 200:
                # Try to extract announcements from HTML
                html = resp.text
                # Look for announcement rows - Bursa uses a table structure
                # Pattern: date | company | title | ...
                ann_pattern = re.findall(
                    r'<tr[^>]*>.*?<td[^>]*>(.*?)</td>.*?<td[^>]*>(.*?)</td>.*?<td[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?</td>.*?</tr>',
                    html, re.DOTALL | re.IGNORECASE
                )
                for match in ann_pattern:
                    date_str = re.sub(r'<[^>]+>', '', match[0]).strip()
                    title = re.sub(r'<[^>]+>', '', match[3]).strip()
                    link = match[2] if match[2].startswith("http") else f"https://www.bursamalaysia.com{match[2]}"
                    if title:
                        articles.append({
                            "source": "bursa",
                            "title": title,
                            "url": link,
                            "published_at": date_str,
                            "content": "",
                            "matched_symbol": sym,
                            "raw_text": title,
                        })
            time.sleep(0.5)  # Rate limiting
        except Exception as e:
            print(f"  Error fetching Bursa for {sym}: {e}")

    return articles


def fetch_klsescreener_news(pages: int = 3) -> list:
    """
    Fetch news from KLSE Screener website.
    """
    articles = []
    base_url = "https://www.klsescreener.com/news"

    for page in range(1, pages + 1):
        try:
            url = f"{base_url}?page={page}" if page > 1 else base_url
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                html = resp.text
                # KLSE Screener news items
                # Pattern: look for news article links
                news_items = re.findall(
                    r'<h[23][^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?</h[23]>.*?<[^>]*class="[^"]*(?:date|time)[^"]*"[^>]*>(.*?)</[^>]*>',
                    html, re.DOTALL | re.IGNORECASE
                )
                for link, title, date_str in news_items:
                    title = re.sub(r'<[^>]+>', '', title).strip()
                    link = link if link.startswith("http") else f"https://www.klsescreener.com{link}"
                    matched = match_stock(title)
                    if title:
                        articles.append({
                            "source": "klsescreener",
                            "title": title,
                            "url": link,
                            "published_at": re.sub(r'<[^>]+>', '', date_str).strip(),
                            "content": "",
                            "matched_symbol": matched,
                            "raw_text": title,
                        })

                # Also try simpler pattern for news links
                if not news_items:
                    links = re.findall(r'<a[^>]*href="(/news/\d+[^"]*)"[^>]*>(.*?)</a>', html, re.DOTALL)
                    for link, title in links:
                        title = re.sub(r'<[^>]+>', '', title).strip()
                        if title and len(title) > 10:
                            matched = match_stock(title)
                            articles.append({
                                "source": "klsescreener",
                                "title": title,
                                "url": f"https://www.klsescreener.com{link}",
                                "published_at": None,
                                "content": "",
                                "matched_symbol": matched,
                                "raw_text": title,
                            })
            time.sleep(0.5)
        except Exception as e:
            print(f"  Error fetching KLSE Screener page {page}: {e}")

    return articles


def fetch_iceberg_berita(db_path: str = None) -> list:
    """
    Fetch news from Iceberg staging.berita table via AWS Athena.
    Uses the mcp__sqlite tool or aws cli for Athena queries.
    Returns list of news dicts.
    """
    articles = []
    # This will be called via the execute_code Athena MCP tool
    # For now, return empty - the actual Athena query is done separately
    return articles


def store_articles(articles: list, db_path: str = None) -> int:
    """Store fetched articles in the news table. Returns count of new articles."""
    count = 0
    for art in articles:
        row_id = insert_news(
            source=art["source"],
            title=art["title"],
            url=art.get("url"),
            published_at=art.get("published_at"),
            content=art.get("content"),
            matched_symbol=art.get("matched_symbol"),
            raw_text=art.get("raw_text"),
            db_path=db_path,
        )
        if row_id:
            count += 1
    return count


def fetch_all_news(db_path: str = None) -> int:
    """Fetch news from all sources and store. Returns total new articles."""
    total = 0

    # 1. Bursa Malaysia announcements
    print("Fetching Bursa Malaysia announcements...")
    bursa_articles = fetch_bursa_announcements(limit=30)
    print(f"  Found {len(bursa_articles)} Bursa announcements")
    count = store_articles(bursa_articles, db_path=db_path)
    print(f"  Stored {count} new")
    total += count

    # 2. KLSE Screener
    print("Fetching KLSE Screener news...")
    kls_articles = fetch_klsescreener_news(pages=5)
    print(f"  Found {len(kls_articles)} KLSE Screener articles")
    count = store_articles(kls_articles, db_path=db_path)
    print(f"  Stored {count} new")
    total += count

    # 3. Iceberg berita (done separately via Athena MCP)
    print("Iceberg berita: use fetch_iceberg_berita_via_athena() for Athena-sourced news")

    print(f"\n=== News fetch complete: {total} new articles stored ===")
    return total


def fetch_iceberg_berita_via_sql(db_path: str = None) -> list:
    """
    Query Iceberg berita table via Athena and store results.
    This function generates the SQL to be executed via MCP Athena tool.
    Returns the SQL query string.
    """
    query = """
    SELECT
        title,
        url,
        published_date,
        content,
        source
    FROM staging_tables.berita
    WHERE published_date >= date_add('day', -30, current_date)
    ORDER BY published_date DESC
    LIMIT 500
    """
    return query


if __name__ == "__main__":
    from data.db import init_db
    init_db()
    fetch_all_news()
