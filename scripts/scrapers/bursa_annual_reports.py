"""Optimized annual-report PDF extraction — navigates directly to disclosure viewHtml.
Instead of opening the Bursa detail page (outer page + iframe), we go straight to:
  https://disclosure.bursamalaysia.com/FileAccess/viewHtml?e={ann_id}
which is where the actual content + Attachments live. Much faster.

Phase 1: Use Playwright to enumerate ann_id links from the results pages (with pagination).
Phase 2: For each ann_id, navigate directly to viewHtml, extract attachments, fetch PDFs.
"""
import asyncio, json, sys, os, urllib.parse, re, base64
from playwright.async_api import async_playwright

CODE = sys.argv[1] if len(sys.argv) > 1 else ""
SYMBOL = sys.argv[2] if len(sys.argv) > 2 else "ALL"
OUT = f"/home/sandbox/klse-news-trader/data/pdfs/{SYMBOL}"
os.makedirs(OUT, exist_ok=True)
MAX_PAGES = int(os.environ.get("MAX_PAGES", "0"))
MAX_ROWS = int(os.environ.get("MAX_ROWS", "0"))
DISC = "https://disclosure.bursamalaysia.com"
context = None


def safe_name(text, ann_id):
    n = re.sub(r"[^\w.\-]+", "_", (text or "")[:80]).strip("_")
    if not n:
        n = f"ann_{ann_id}"
    if not n.endswith(".pdf"):
        n += f"_{ann_id}.pdf"
    return n[:150]


async def pass_turnstile(page):
    for i in range(30):
        await asyncio.sleep(2)
        if "Just a moment" not in await page.title():
            return True
        for f in page.frames:
            if "turnstile" in f.url or "challenges" in f.url:
                try:
                    cb = f.locator(".label,input[type=checkbox]")
                    if await cb.count() and await cb.first.is_visible():
                        await cb.first.click(timeout=1500)
                except Exception:
                    pass
    return False


async def extract_ann_ids(page, max_pages):
    """Enumerate all ann_id links from the results pages (with DataTables pagination)."""
    all_ids = []
    page_no = 1
    while True:
        if max_pages and page_no > max_pages:
            print(f"[enum] MAX_PAGES={max_pages} reached", flush=True)
            break
        # wait for rows
        rj = None
        for i in range(15):
            await asyncio.sleep(2)
            rj = json.loads(await page.evaluate("""()=>{
                const tbl=document.querySelector('#table-announcements');
                return JSON.stringify({
                    rows:tbl?tbl.querySelectorAll('tbody tr').length:0,
                    noRes:document.body.innerText.includes('No results')||document.body.innerText.includes('0 results'),
                    titleLinks:Array.from(tbl?tbl.querySelectorAll('a[href*="ann_id="]')||[]:[]).map(a=>a.href),
                    pageInfo:(document.body.innerText.match(/Showing[\\s\\S]{0,40}/)||[''])[0]
                });
            }"""))
            if rj["rows"] > 0 or rj["noRes"]:
                break
        if not rj or (rj["noRes"] and rj["rows"] == 0):
            print(f"[enum] no results p{page_no}", flush=True)
            break
        links = rj["titleLinks"]
        ids = []
        for u in links:
            aid = urllib.parse.parse_qs(urllib.parse.urlparse(u).query).get("ann_id", [""])[0]
            if aid:
                ids.append(aid)
        all_ids.extend(ids)
        print(f"[enum] PAGE {page_no}: {len(ids)} ids | {rj['pageInfo']} | total={len(all_ids)}", flush=True)
        if not ids:
            break
        # next page
        has_next = await page.evaluate("()=>{const n=document.querySelector('#table-announcements_next');return !!(n&&!n.className.includes('disabled'));}")
        if not has_next:
            break
        await page.evaluate("()=>{const a=document.querySelector('#table-announcements_next a');if(a)a.click();}")
        page_no += 1
        await asyncio.sleep(2)
    return all_ids


async def download_pdfs_for_ann(ann_ids):
    """For each ann_id, navigate directly to viewHtml, extract attachments, fetch PDFs."""
    global context
    pg = await context.new_page()
    count = 0
    for idx, ann_id in enumerate(ann_ids):
        if MAX_ROWS and idx >= MAX_ROWS:
            print(f"[dl] MAX_ROWS={MAX_ROWS} reached", flush=True)
            break
        # Navigate directly to the disclosure content page
        url = f"{DISC}/FileAccess/viewHtml?e={ann_id}"
        try:
            await pg.goto(url, timeout=30000, wait_until="domcontentloaded")
            for i in range(8):
                await asyncio.sleep(1.5)
                if "Just a moment" not in await pg.title():
                    break
            await asyncio.sleep(2)
            # Extract attachment links + metadata
            info = await pg.evaluate("""()=>{
                const title=(document.querySelector('h1,h2,[class*=title],td')?.innerText||'').trim().slice(0,150);
                const comp=(document.body.innerText.match(/[A-Z][A-Z ]+(?:BERHAD|GROUP|HOLDINGS)/)?.[0]||'').trim();
                const links=Array.from(document.querySelectorAll('a')).map(a=>({
                    text:(a.innerText||a.getAttribute('title')||'').trim(),
                    href:a.getAttribute('href')||''
                })).filter(x=>x.href&&(/download|FileAccess|EA_DS_ATTACH/.test(x.href)||/\\.pdf($|\\?)/i.test(x.href)));
                return JSON.stringify({title,comp,links});
            }""")
            r = json.loads(info)
            pdfs = []
            for l in r["links"]:
                href = l["href"]
                if href.startswith("/"):
                    href = DISC + href
                elif not href.startswith("http"):
                    href = DISC + "/" + href
                pdfs.append({"name": l["text"], "href": href})
            if pdfs:
                print(f"[dl] {idx+1}/{len(ann_ids)} ann={ann_id} comp='{r['comp'][:25]}' pdfs={len(pdfs)}", flush=True)
            for pdoc in pdfs:
                fname = safe_name((r["comp"].replace(" ", "") + "_" if r["comp"] else "") + pdoc["name"], ann_id)
                path = os.path.join(OUT, fname)
                if os.path.exists(path):
                    continue
                escaped = pdoc["href"].replace("\\", "\\\\").replace("'", "\\'")
                fetch_js = ("async ()=>{"
                            f" const r=await fetch('{escaped}');"
                            " if(!r.ok) return 'STATUS:'+r.status;"
                            " const buf=await r.arrayBuffer(); const bytes=new Uint8Array(buf);"
                            " let bin=''; const CH=0x8000;"
                            " for(let i=0;i<bytes.length;i+=CH){ bin+=String.fromCharCode.apply(null,bytes.subarray(i,i+CH)); }"
                            " return btoa(bin); }")
                try:
                    b64 = await pg.evaluate(fetch_js)
                    if isinstance(b64, str) and b64.startswith("STATUS:"):
                        continue
                    data = base64.b64decode(b64)
                    with open(path, "wb") as f:
                        f.write(data)
                    count += 1
                    print(f"   DL {fname} ({len(data)} bytes)", flush=True)
                except Exception as e:
                    print(f"   DL err {str(e)[:80]}", flush=True)
        except Exception as e:
            print(f"[dl] err ann={ann_id}: {str(e)[:80]}", flush=True)
    await pg.close()
    return count


async def main():
    global context
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False,
            args=["--no-sandbox","--disable-dev-shm-usage","--ignore-certificate-errors",
                  "--window-size=1400,950","--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(viewport={"width":1400,"height":950},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        await context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")

        # Phase 1: enumerate ann_ids from results pages
        page = await context.new_page()
        await page.goto("https://www.bursamalaysia.com/market_information/announcements/company_announcement",
                        timeout=45000, wait_until="domcontentloaded")
        if not await pass_turnstile(page):
            print(f"[{SYMBOL}] BLOCKED", flush=True); await browser.close(); return
        await asyncio.sleep(3)
        if CODE:
            await page.evaluate("""(code)=>{const s=document.querySelector('#inCompany');s.value=code;s.dispatchEvent(new Event('change',{bubbles:true}));if(window.jQuery)jQuery(s).trigger('change');}""", CODE)
            await asyncio.sleep(2)
        await page.evaluate("""()=>{const s=document.querySelector('#inMarket');s.value='AR,ARCO';s.dispatchEvent(new Event('change',{bubbles:true}));if(window.jQuery)jQuery(s).trigger('change');}""")
        await asyncio.sleep(2)
        await page.evaluate("""()=>{
            const form=document.querySelector('#inCompany')?.closest('form')||document;
            const sb=Array.from(form.querySelectorAll('button')).find(x=>x.innerText&&x.innerText.trim().toLowerCase()==='search');
            if(sb)sb.click();
        }""")
        print(f"[{SYMBOL}] Search clicked — enumerating ann_ids...", flush=True)
        ann_ids = await extract_ann_ids(page, MAX_PAGES)
        await page.close()
        print(f"[{SYMBOL}] Total ann_ids: {len(ann_ids)}", flush=True)

        if not ann_ids:
            print(f"[{SYMBOL}] no ann_ids found", flush=True); await browser.close(); return

        # Phase 2: download PDFs for each ann_id
        n = await download_pdfs_for_ann(ann_ids)
        print(f"\n[{SYMBOL}] COMPLETE: {n} PDFs downloaded from {len(ann_ids)} announcements", flush=True)
        await browser.close()


asyncio.run(main())