"""
Annual report PDF downloader from Bursa Malaysia.
Fetches annual report announcements, downloads PDFs to data/pdfs/{SYMBOL}/,
and records metadata in the annual_reports table.
"""
import os
import sys
import re
import time
import requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import (
    insert_annual_report, get_annual_reports, upsert_stock,
    get_all_stocks, get_table_counts
)

# KLSE stock universe (must match prices.py)
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

# Bursa Malaysia company codes for URL lookups
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

PDF_BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pdfs")


def is_annual_report(title: str) -> bool:
    """Check if an announcement title indicates an annual report."""
    title_lower = title.lower()
    patterns = [
        r"annual\s*report",
        r"annual\s*statement",
        r"integrated\s*annual\s*report",
        r"annual\s*report\s*\d{4}",
    ]
    for pat in patterns:
        if re.search(pat, title_lower):
            return True
    return False


def fetch_announcement_page(symbol: str, bursa_code: str = None) -> str:
    """
    Fetch the Bursa Malaysia company announcement page HTML.
    Uses the Bursa announcements API endpoint.
    """
    if not bursa_code:
        bursa_code = BURSA_CODES.get(symbol, "")

    # Bursa Malaysia has a JSON API for announcements
    api_url = "https://www.bursamalaysia.com/api/v1/announcements"
    params = {
        "stock_code": bursa_code,
        "page": 1,
        "per_page": 50,
        "category": "all",
    }

    try:
        resp = requests.get(api_url, params=params, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass

    # Fallback: try the HTML page
    html_url = f"https://www.bursamalaysia.com/market_information/announcements/company_announcement?company={bursa_code}"
    try:
        resp = requests.get(html_url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        print(f"  Error fetching page for {symbol}: {e}")

    return ""


def parse_announcements(html: str) -> list:
    """
    Parse announcements from Bursa Malaysia HTML or JSON response.
    Returns list of dicts with keys: title, date, url, pdf_url
    """
    announcements = []

    # Try JSON first
    try:
        data = __import__("json").loads(html)
        if isinstance(data, dict) and "data" in data:
            for item in data["data"]:
                title = item.get("title", "") or item.get("subject", "")
                ann_date = item.get("date", "") or item.get("announced_date", "")
                url = item.get("url", "") or item.get("link", "")
                pdf_url = item.get("pdf_url", "") or item.get("attachment_url", "")
                if title:
                    announcements.append({
                        "title": title,
                        "date": ann_date,
                        "url": url if url.startswith("http") else f"https://www.bursamalaysia.com{url}",
                        "pdf_url": pdf_url if not pdf_url or pdf_url.startswith("http") else f"https://www.bursamalaysia.com{pdf_url}",
                    })
        if announcements:
            return announcements
    except Exception:
        pass

    # Parse HTML
    # Bursa announcement table rows
    rows = re.findall(
        r'<tr[^>]*>(.*?)</tr>',
        html, re.DOTALL | re.IGNORECASE
    )

    for row in rows:
        # Extract date
        date_match = re.search(r'<td[^>]*>\s*(\d{2}/\d{2}/\d{4}|\d{2}-\w{3}-\d{4})\s*</td>', row)
        ann_date = date_match.group(1) if date_match else ""

        # Extract title and link
        link_match = re.search(
            r'<a[^>]*href="([^"]*)"[^>]*>\s*(.*?)\s*</a>',
            row, re.DOTALL | re.IGNORECASE
        )
        if link_match:
            url = link_match.group(1)
            title = re.sub(r'<[^>]+>', '', link_match.group(2)).strip()
            if title and is_annual_report(title):
                full_url = url if url.startswith("http") else f"https://www.bursamalaysia.com{url}"
                announcements.append({
                    "title": title,
                    "date": ann_date,
                    "url": full_url,
                    "pdf_url": "",  # Will be extracted from detail page
                })

    return announcements


def extract_pdf_url(detail_url: str) -> str:
    """Extract PDF download URL from an announcement detail page."""
    try:
        resp = requests.get(detail_url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            html = resp.text
            # Look for PDF links
            pdf_match = re.search(r'href="([^"]*\.pdf[^"]*)"', html, re.IGNORECASE)
            if pdf_match:
                pdf_url = pdf_match.group(1)
                return pdf_url if pdf_url.startswith("http") else f"https://www.bursamalaysia.com{pdf_url}"
    except Exception:
        pass
    return ""


def download_pdf(pdf_url: str, symbol: str, title: str) -> tuple:
    """
    Download a PDF and save to data/pdfs/{SYMBOL}/
    Returns (local_path, file_size) or (None, 0) on failure.
    """
    if not pdf_url:
        return None, 0

    # Create directory
    pdf_dir = os.path.join(PDF_BASE_DIR, symbol)
    os.makedirs(pdf_dir, exist_ok=True)

    # Generate filename from title
    safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')[:80]
    filename = f"{safe_title}.pdf"
    local_path = os.path.join(pdf_dir, filename)

    # Skip if already downloaded
    if os.path.exists(local_path):
        return local_path, os.path.getsize(local_path)

    try:
        resp = requests.get(pdf_url, headers=HEADERS, timeout=30, stream=True)
        if resp.status_code == 200:
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            file_size = os.path.getsize(local_path)
            return local_path, file_size
    except Exception as e:
        print(f"  Error downloading PDF: {e}")

    return None, 0


def fetch_annual_reports_for_stock(symbol: str, db_path: str = None,
                                    download: bool = True) -> int:
    """
    Fetch annual reports for a single stock.
    Returns count of new reports found.
    """
    bursa_code = BURSA_CODES.get(symbol, "")
    if not bursa_code:
        print(f"  No Bursa code for {symbol}, skipping")
        return 0

    print(f"  Fetching announcements for {symbol} (code: {bursa_code})...", end=" ", flush=True)

    html = fetch_announcement_page(symbol, bursa_code)
    if not html:
        print("no data")
        return 0

    announcements = parse_announcements(html)
    annual_reports = [a for a in announcements if is_annual_report(a["title"])]

    if not annual_reports:
        print(f"no annual reports found ({len(announcements)} total announcements)")
        return 0

    print(f"found {len(annual_reports)} annual reports")

    count = 0
    for ann in annual_reports:
        pdf_url = ann.get("pdf_url", "")
        local_path = None
        file_size = None
        downloaded_at = None

        # If no direct PDF URL, try to get it from detail page
        if not pdf_url and ann.get("url"):
            pdf_url = extract_pdf_url(ann["url"])
            time.sleep(0.3)

        # Download PDF if requested
        if download and pdf_url:
            local_path, file_size = download_pdf(pdf_url, symbol, ann["title"])
            if local_path:
                downloaded_at = datetime.utcnow().isoformat()
            time.sleep(0.5)

        # Store metadata
        insert_annual_report(
            symbol=symbol,
            title=ann["title"],
            pdf_url=pdf_url or ann["url"],
            announcement_date=ann.get("date"),
            local_path=local_path,
            file_size=file_size,
            downloaded_at=downloaded_at,
            db_path=db_path,
        )
        count += 1

    return count


def fetch_all_annual_reports(db_path: str = None, download: bool = True,
                              delay: float = 1.0) -> int:
    """
    Fetch annual reports for all KLSE stocks.
    Returns total count of reports found.
    """
    total = 0
    symbols = list(KLSE_STOCKS.keys())

    print(f"=== Fetching annual reports for {len(symbols)} stocks ===")

    for i, symbol in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}]", end=" ")
        try:
            count = fetch_annual_reports_for_stock(symbol, db_path=db_path, download=download)
            total += count
        except Exception as e:
            print(f"  ERROR for {symbol}: {e}")
        time.sleep(delay)

    print(f"\n=== Annual reports complete: {total} reports found ===")
    return total


if __name__ == "__main__":
    from data.db import init_db
    init_db()
    # Ensure stocks are in DB
    for sym, name in KLSE_STOCKS.items():
        upsert_stock(sym, name, bursa_code=BURSA_CODES.get(sym, ""))
    fetch_all_annual_reports()
