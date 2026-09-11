#!/usr/bin/env python3
"""
Daily PDF Monitor — checks Bursa Malaysia for NEW annual report PDFs.

Uses Playwright (headless=False to bypass Turnstile) to:
1. Load the Bursa announcements page filtered for Annual Reports
2. Find announcements posted since last run
3. Download new PDFs to data/pdfs/ALL/
4. Record in annual_reports table

Designed to run once daily via cron at 06:00 MYT.
"""
import os
import sys
import re
import json
import time
import asyncio
import logging
import urllib.parse
from datetime import datetime, timedelta

# Setup logging
LOG_FILE = "/tmp/daily_pdf_monitor.log"
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
    get_pipeline_state, set_pipeline_state,
    init_pipeline_state, get_conn, insert_annual_report,
    DB_PATH
)

# Config
PDF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pdfs", "NEW")
PDF_ALL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pdfs", "ALL")
DISCLOSURE_URL = "https://disclosure.bursamalaysia.com"
BURSA_ANNOUNCE_URL = "https://www.bursamalaysia.com/market_information/announcements/company_announcement"
HEADLESS = False  # Must be False to bypass Turnstile
MAX_ANNOUNCEMENTS = 200  # Max announcements to process per run


def get_existing_pdfs():
    """Get set of filenames already downloaded (NEW + ALL)."""
    dirs_to_check = [PDF_DIR, PDF_ALL_DIR]
    existing = set()
    for d in dirs_to_check:
        if os.path.exists(d):
            existing.update(os.listdir(d))
        else:
            os.makedirs(d, exist_ok=True)
    return existing


def get_existing_pdf_urls():
    """Get set of already-downloaded PDF URLs from DB."""
    with get_conn() as conn:
        rows = conn.execute("SELECT pdf_url FROM annual_reports").fetchall()
        return set(r[0] for r in rows)


async def pass_turnstile(page):
    """Wait for Turnstile challenge to resolve."""
    for i in range(30):
        await asyncio.sleep(2)
        title = await page.title()
        if "Just a moment" not in title:
            logger.info(f"Turnstile passed after {i*2}s")
            return True
        # Try clicking checkbox in iframe
        for f in page.frames:
            if "turnstile" in f.url or "challenges" in f.url:
                try:
                    cb = f.locator(".label,input[type=checkbox]")
                    if await cb.count() and await cb.first.is_visible():
                        await cb.first.click(timeout=1500)
                        logger.info("Clicked Turnstile checkbox")
                except Exception:
                    pass
    logger.error("Turnstile NOT passed after 60s")
    return False


async def extract_announcements_since(page, since_date):
    """
    Extract Annual Report announcements from Bursa posted since `since_date`.
    Returns list of dicts: {ann_id, title, date, company_code}
    """
    announcements = []
    page_no = 1
    max_pages = 10  # Limit to avoid extremely long runs

    while page_no <= max_pages:
        logger.info(f"Scanning announcements page {page_no}...")

        # Wait for table to load
        data = None
        for attempt in range(15):
            await asyncio.sleep(2)
            data = await page.evaluate("""() => {
                const tbl = document.querySelector('#table-announcements');
                if (!tbl) return {rows: 0, noRes: false, titleLinks: [], pageInfo: ''};
                return {
                    rows: tbl.querySelectorAll('tbody tr').length,
                    noRes: document.body.innerText.includes('No results') || document.body.innerText.includes('0 results'),
                    titleLinks: Array.from(tbl.querySelectorAll('a[href*="ann_id="]') || []).map(a => ({
                        href: a.href,
                        text: a.innerText.trim()
                    })),
                    pageInfo: (document.body.innerText.match(/Showing[\s\S]{0,40}/) || [''])[0]
                };
            }""")
            if data["rows"] > 0 or data["noRes"]:
                break

        if not data or (data["noRes"] and data["rows"] == 0):
            logger.info(f"No results on page {page_no}")
            break

        # Extract date from each row
        dates_info = await page.evaluate("""() => {
            const tbl = document.querySelector('#table-announcements');
            if (!tbl) return [];
            return Array.from(tbl.querySelectorAll('tbody tr')).map(tr => {
                const tds = tr.querySelectorAll('td');
                return {
                    date: tds[0] ? tds[0].innerText.trim() : '',
                    company: tds[1] ? tds[1].innerText.trim() : '',
                };
            });
        }""")

        # Process each announcement
        stopped = False
        for idx, link_info in enumerate(data["titleLinks"]):
            href = link_info["href"]
            title = link_info["text"]

            # Extract ann_id
            ann_id = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("ann_id", [""])[0]
            if not ann_id:
                continue

            # Get date from corresponding row
            date_str = dates_info[idx]["date"] if idx < len(dates_info) else ""
            company = dates_info[idx]["company"] if idx < len(dates_info) else ""

            # Parse announcement date
            ann_date = parse_bursa_date(date_str)
            if not ann_date:
                continue

            # Check if this is within our window
            if ann_date < since_date:
                logger.info(f"Reached announcements older than {since_date.strftime('%Y-%m-%d')}, stopping")
                stopped = True
                break

            # Check if it's an Annual Report
            if not is_annual_report(title):
                continue

            announcements.append({
                "ann_id": ann_id,
                "title": title,
                "date": ann_date.strftime("%Y-%m-%d"),
                "date_raw": date_str,
                "company": company,
            })

            if len(announcements) >= MAX_ANNOUNCEMENTS:
                logger.info(f"Reached MAX_ANNOUNCEMENTS ({MAX_ANNOUNCEMENTS})")
                stopped = True
                break

        if stopped:
            break

        # Next page
        has_next = await page.evaluate(
            "()=>{const n=document.querySelector('#table-announcements_next');return !!(n&&!n.className.includes('disabled'));"
        )
        if not has_next:
            break

        await page.evaluate(
            "()=>{const a=document.querySelector('#table-announcements_next a');if(a)a.click();}"
        )
        page_no += 1
        await asyncio.sleep(2)

    return announcements


def parse_bursa_date(date_str):
    """Parse Bursa Malaysia date format (DD/MM/YYYY or DD MMM YYYY)."""
    if not date_str:
        return None

    # Try DD/MM/YYYY
    try:
        return datetime.strptime(date_str.strip(), "%d/%m/%Y")
    except ValueError:
        pass

    # Try DD MMM YYYY (e.g., "31 Dec 2024")
    try:
        return datetime.strptime(date_str.strip(), "%d %b %Y")
    except ValueError:
        pass

    # Try DD-MM-YYYY
    try:
        return datetime.strptime(date_str.strip(), "%d-%m-%Y")
    except ValueError:
        pass

    return None


def is_annual_report(title):
    """Check if announcement title indicates an Annual Report."""
    title_upper = title.upper()
    ar_keywords = [
        "ANNUAL REPORT",
        "ANNUAL RETURN",
        "AR FOR",
        "AR20", "AR21", "AR22", "AR23", "AR24", "AR25", "AR26",
        "AR 20", "AR 21",
    ]
    for kw in ar_keywords:
        if kw in title_upper:
            return True
    return False


async def download_pdfs_for_announcements(context, announcements, existing_urls):
    """Navigate to each announcement's viewHtml page and download PDFs."""
    page = await context.new_page()
    downloaded = 0
    new_reports = []

    for idx, ann in enumerate(announcements):
        ann_id = ann["ann_id"]
        url = f"{DISCLOSURE_URL}/FileAccess/viewHtml?e={ann_id}"

        try:
            logger.info(f"[{idx+1}/{len(announcements)}] Processing ann_id={ann_id}: {ann['title'][:60]}")
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")

            # Wait for Turnstile
            for i in range(8):
                await asyncio.sleep(1.5)
                title = await page.title()
                if "Just a moment" not in title:
                    break

            await asyncio.sleep(2)

            # Extract attachment links
            info = await page.evaluate("""() => {
                const title = (document.querySelector('h1,h2,[class*=title],td')?.innerText || '').trim().slice(0, 150);
                const comp = (document.body.innerText.match(/[A-Z][A-Z ]+(?:BERHAD|GROUP|HOLDINGS)/)?.[0] || '').trim();
                const links = Array.from(document.querySelectorAll('a')).map(a => ({
                    text: (a.innerText || a.getAttribute('title') || '').trim(),
                    href: a.getAttribute('href') || ''
                })).filter(x => x.href && (/download|FileAccess|EA_DS_ATTACH/.test(x.href) || /\.pdf($|\?)/i.test(x.href)));
                return JSON.stringify({title, comp, links});
            }""")

            r = json.loads(info)
            pdfs = []
            for link in r["links"]:
                href = link["href"]
                if href.startswith("/"):
                    href = DISCLOSURE_URL + href
                elif not href.startswith("http"):
                    href = DISCLOSURE_URL + "/" + href
                pdfs.append({"name": link["text"], "href": href})

            if not pdfs:
                logger.info(f"  No PDFs found for ann_id={ann_id}")
                continue

            for pdoc in pdfs:
                pdf_url = pdoc["href"]

                # Skip if already downloaded
                if pdf_url in existing_urls:
                    logger.info(f"  Skipping (already in DB): {pdoc['name'][:50]}")
                    continue

                # Generate safe filename
                comp_prefix = r["comp"].replace(" ", "") + "_" if r["comp"] else ""
                safe_name = re.sub(r"[^\w.\-]+", "_", (comp_prefix + pdoc["name"])[:80]).strip("_")
                if not safe_name.endswith(".pdf"):
                    safe_name += f"_{ann_id}.pdf"
                safe_name = safe_name[:150]

                filepath = os.path.join(PDF_DIR, safe_name)
                if os.path.exists(filepath):
                    logger.info(f"  Skipping (file exists): {safe_name}")
                    existing_urls.add(pdf_url)
                    continue

                # Download via browser fetch
                escaped = pdf_url.replace("\\", "\\\\").replace("'", "\\'")
                fetch_js = (
                    "async ()=>{"
                    f" const r=await fetch('{escaped}');"
                    " if(!r.ok) return 'STATUS:'+r.status;"
                    " const buf=await r.arrayBuffer(); const bytes=new Uint8Array(buf);"
                    " let bin=''; const CH=0x8000;"
                    " for(let i=0;i<bytes.length;i+=CH){ bin+=String.fromCharCode.apply(null,bytes.subarray(i,i+CH)); }"
                    " return btoa(bin); }"
                )

                try:
                    import base64
                    b64 = await page.evaluate(fetch_js)
                    if isinstance(b64, str) and b64.startswith("STATUS:"):
                        logger.warning(f"  Download failed ({b64}): {safe_name}")
                        continue

                    data = base64.b64decode(b64)
                    with open(filepath, "wb") as f:
                        f.write(data)

                    downloaded += 1
                    existing_urls.add(pdf_url)
                    file_size = len(data)

                    # Record in DB
                    new_reports.append({
                        "symbol": r["comp"][:20].upper() if r["comp"] else "UNKNOWN",
                        "title": ann["title"],
                        "pdf_url": pdf_url,
                        "announcement_date": ann["date"],
                        "local_path": filepath,
                        "file_size": file_size,
                    })

                    logger.info(f"  Downloaded: {safe_name} ({file_size} bytes)")

                except Exception as e:
                    logger.error(f"  Download error for {safe_name}: {str(e)[:80]}")

        except Exception as e:
            logger.error(f"  Error processing ann_id={ann_id}: {str(e)[:80]}")

        # Small delay between requests
        await asyncio.sleep(1)

    await page.close()
    return downloaded, new_reports


async def run_monitor():
    """Main monitoring routine."""
    logger.info("=" * 60)
    logger.info("DAILY PDF MONITOR — Starting")
    logger.info("=" * 60)

    # Ensure pipeline_state table exists
    init_pipeline_state()

    # Determine the lookback window
    last_check = get_pipeline_state("daily_pdf_monitor_last_check")
    if last_check:
        try:
            since_date = datetime.strptime(last_check, "%Y-%m-%d %H:%M:%S")
            # Don't look back more than 7 days to avoid reprocessing old data
            min_date = datetime.utcnow() - timedelta(days=7)
            if since_date < min_date:
                since_date = min_date
        except ValueError:
            since_date = datetime.utcnow() - timedelta(days=7)
    else:
        since_date = datetime.utcnow() - timedelta(days=7)

    logger.info(f"Looking for Annual Reports since: {since_date.strftime('%Y-%m-%d')}")

    # Get existing PDFs
    existing_pdfs = get_existing_pdfs()
    existing_urls = get_existing_pdf_urls()
    logger.info(f"Existing: {len(existing_pdfs)} files on disk, {len(existing_urls)} URLs in DB")

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        logger.info(f"Launching browser (headless={HEADLESS})...")
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--ignore-certificate-errors",
                "--window-size=1400,950",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1400, "height": 950},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )

        # Navigate to Bursa announcements page
        page = await context.new_page()
        logger.info("Navigating to Bursa announcements page...")

        try:
            await page.goto(BURSA_ANNOUNCE_URL, timeout=45000, wait_until="domcontentloaded")
        except Exception as e:
            logger.error(f"Failed to load Bursa page: {e}")
            await browser.close()
            return

        # Pass Turnstile
        if not await pass_turnstile(page):
            logger.error("Blocked by Turnstile — aborting")
            await browser.close()
            return

        await asyncio.sleep(3)

        # Set filter to Annual Reports category
        logger.info("Setting Annual Report filter...")
        try:
            # Select Annual Report category in the market dropdown
            await page.evaluate("""() => {
                const sel = document.querySelector('#inMarket');
                if (sel) {
                    sel.value = 'AR,ARCO';
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                    if (window.jQuery) jQuery(sel).trigger('change');
                }
            }""")
            await asyncio.sleep(2)

            # Click search button
            await page.evaluate("""() => {
                const form = document.querySelector('#inCompany')?.closest('form') || document;
                const btn = Array.from(form.querySelectorAll('button')).find(
                    x => x.innerText && x.innerText.trim().toLowerCase() === 'search'
                );
                if (btn) btn.click();
            }""")
            await asyncio.sleep(3)
        except Exception as e:
            logger.warning(f"Could not set AR filter (may still work): {e}")

        # Extract announcements
        announcements = await extract_announcements_since(page, since_date)
        await page.close()

        logger.info(f"Found {len(announcements)} new Annual Report announcements")

        if not announcements:
            logger.info("No new Annual Reports found. Done.")
            set_pipeline_state("daily_pdf_monitor_last_check", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
            await browser.close()
            return

        # Download PDFs
        downloaded, new_reports = await download_pdfs_for_announcements(context, announcements, existing_urls)

        # Store in DB with status='pending'
        for report in new_reports:
            try:
                insert_annual_report(
                    symbol=report["symbol"],
                    title=report["title"],
                    pdf_url=report["pdf_url"],
                    announcement_date=report["announcement_date"],
                    local_path=report["local_path"],
                    file_size=report["file_size"],
                    downloaded_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                )
                # Set status='pending' for pipeline processing
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE annual_reports SET status = 'pending' WHERE pdf_url = ?",
                        (report["pdf_url"],)
                    )
            except Exception as e:
                logger.error(f"DB insert error: {e}")

        # Update pipeline state
        set_pipeline_state("daily_pdf_monitor_last_check", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
        set_pipeline_state("daily_pdf_monitor_last_count", str(downloaded))

        logger.info(f"\n=== COMPLETE: Downloaded {downloaded} new PDFs from {len(announcements)} announcements ===")
        await browser.close()


def main():
    """Entry point."""
    try:
        asyncio.run(run_monitor())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
