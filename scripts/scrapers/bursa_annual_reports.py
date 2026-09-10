"""Full paginated annual-report extraction from Bursa Malaysia.
Workflow:
 1. Category=Annual Report (no company filter -> ALL companies), click Search
 2. For each page: each Title link opens a detail page
 3. Detail content (incl. Attachments) lives in disclosure.bursamalaysia.com iframe
 4. Attachment download links (apbursaweb/download) fetch 200 ONLY via in-page fetch
    (external request=403). So we navigate the page to viewHtml (sets cookies) then fetch.
 5. Next page, repeat; stop when no more pages.
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


async def extract_from_frame(pg, ann_id):
    """Find the disclosure iframe, pull title + attachment download links."""
    iframe = None
    for i in range(10):
        for f in pg.frames:
            if "disclosure.bursamalaysia.com" in f.url and "FileAccess" in f.url:
                iframe = f
                break
        if iframe:
            break
        await asyncio.sleep(1.5)
    if not iframe:
        return {"title": "", "company": "", "ann_id": ann_id, "pdfs": []}
    await asyncio.sleep(2)
    info = await iframe.evaluate("""()=>{
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
    seen = set(); dedup = []
    for p in pdfs:
        if p["href"] not in seen:
            seen.add(p["href"]); dedup.append(p)
    print(f"   iframe: title='{r['title'][:40]}' comp='{r['comp'][:25]}' pdfs={len(dedup)}", flush=True)
    return {"title": r["title"], "company": r["comp"], "ann_id": ann_id, "pdfs": dedup}


async def scrape_detail(page, url):
    """Open a detail link in a new tab, read iframe content."""
    ann_id = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("ann_id", [""])[0]
    try:
        async with context.expect_page(timeout=8000) as np_info:
            try:
                await page.evaluate(f"window.open('{url}','_blank')")
            except Exception:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        pg = await np_info.value
        await pg.wait_for_load_state("domcontentloaded")
        for i in range(10):
            await asyncio.sleep(1.5)
            if "Just a moment" not in await pg.title():
                break
        await asyncio.sleep(4)
        return await extract_from_frame(pg, ann_id), pg
    except Exception:
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            for i in range(10):
                await asyncio.sleep(1.5)
                if "Just a moment" not in await page.title():
                    break
            await asyncio.sleep(4)
            return await extract_from_frame(page, ann_id), page
        except Exception as e2:
            return {"title": "", "company": "", "ann_id": ann_id, "pdfs": [], "error": str(e2)[:100]}, page


async def download_pdfs(pg, ann_id, pdfs):
    """Load disclosure viewHtml (sets cookies) then fetch() each PDF in-page."""
    if "disclosure.bursamalaysia.com" not in pg.url:
        try:
            await pg.goto(f"{DISC}/FileAccess/viewHtml?e={ann_id}", timeout=30000, wait_until="domcontentloaded")
            for i in range(8):
                await asyncio.sleep(1.5)
                if "Just a moment" not in await pg.title():
                    break
        except Exception:
            pass
    await asyncio.sleep(2)
    comp = ""
    for pdoc in pdfs:
        fname = safe_name((comp.replace(" ", "") + "_" if comp else "") + pdoc["name"], ann_id)
        path = os.path.join(OUT, fname)
        if os.path.exists(path):
            print(f"   exists {fname}", flush=True)
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
                print(f"   DL fail {b64[7:]} {pdoc['href'][:60]}", flush=True)
                continue
            data = base64.b64decode(b64)
            with open(path, "wb") as f:
                f.write(data)
            print(f"   DOWNLOADED {fname} ({len(data)} bytes)", flush=True)
        except Exception as e:
            print(f"   DL err {str(e)[:100]}", flush=True)


async def main():
    global context
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False,
            args=["--no-sandbox","--disable-dev-shm-usage","--ignore-certificate-errors",
                  "--window-size=1400,950","--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(viewport={"width":1400,"height":950},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        await context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = await context.new_page()
        await page.goto("https://www.bursamalaysia.com/market_information/announcements/company_announcement",
                        timeout=45000, wait_until="domcontentloaded")
        if not await pass_turnstile(page):
            print(f"[{SYMBOL}] BLOCKED by Turnstile", flush=True)
            await browser.close(); return
        print(f"[{SYMBOL}] passed", flush=True)
        await asyncio.sleep(3)
        if CODE:
            await page.evaluate("""(code)=>{const s=document.querySelector('#inCompany');s.value=code;s.dispatchEvent(new Event('change',{bubbles:true}));if(window.jQuery)jQuery(s).trigger('change');}""", CODE)
            await asyncio.sleep(2)
        await page.evaluate("""()=>{const s=document.querySelector('#inMarket');s.value='AR,ARCO';s.dispatchEvent(new Event('change',{bubbles:true}));if(window.jQuery)jQuery(s).trigger('change');}""")
        await asyncio.sleep(2)
        await page.evaluate("""()=>{
            const inC=document.querySelector('#inCompany');
            const form=inC?.closest('form')||document;
            const btns=Array.from(form.querySelectorAll('button,input[type=submit],[class*=btn]'));
            const sb=btns.find(x=>x.innerText&&x.innerText.trim().toLowerCase()==='search');
            if(sb)sb.click();
        }""")
        print(f"[{SYMBOL}] Search clicked", flush=True)

        all_results = []
        page_no = 1
        while True:
            if MAX_PAGES and page_no > MAX_PAGES:
                print(f"[{SYMBOL}] MAX_PAGES reached", flush=True); break
            rj = None
            for i in range(15):
                await asyncio.sleep(2)
                rj = json.loads(await page.evaluate("""()=>{
                    const tbl=document.querySelectorAll('table')[1];
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
                print(f"[{SYMBOL}] no results p{page_no}", flush=True); break
            tl = rj["titleLinks"]
            print(f"[{SYMBOL}] PAGE {page_no}: {len(tl)} links | {rj['pageInfo']}", flush=True)
            if not tl:
                break
            for j, u in enumerate(tl):
                if MAX_ROWS and len(all_results) >= MAX_ROWS:
                    print(f"[{SYMBOL}] MAX_ROWS reached", flush=True); break
                print(f"[{SYMBOL}] p{page_no} r{j+1}/{len(tl)}", flush=True)
                d, pg = await scrape_detail(page, u)
                if d.get("pdfs"):
                    await download_pdfs(pg, d["ann_id"], d["pdfs"])
                all_results.append(d)
                try:
                    await pg.close()
                except Exception:
                    pass
            if MAX_ROWS and len(all_results) >= MAX_ROWS:
                break
            has_next = await page.evaluate("""()=>{
                const pg=document.querySelector('.pagination,.dataTables_paginate,[class*=pagination]');
                if(!pg) return false;
                return [...pg.querySelectorAll('a,button')].some(x=>/next|›|»/.test((x.innerText||'')+(x.className||''))&&!x.className.includes('disabled'));
            }""")
            if not has_next:
                print(f"[{SYMBOL}] no next page p{page_no}", flush=True); break
            await page.evaluate("""()=>{
                const pg=document.querySelector('.pagination,.dataTables_paginate,[class*=pagination]');
                const next=[...pg.querySelectorAll('a,button')].find(x=>/next|›|»/.test((x.innerText||'')+(x.className||''))&&!x.className.includes('disabled'));
                if(next)next.click();
            }""")
            page_no += 1
            await asyncio.sleep(3)

        with open(f"{OUT}/all_extracted.json", "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"\n[{SYMBOL}] COMPLETE: {len(all_results)} announcements, {page_no} pages", flush=True)
        await browser.close()


asyncio.run(main())