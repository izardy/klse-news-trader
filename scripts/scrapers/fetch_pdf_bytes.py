"""Fetch a Bursa disclosure PDF via in-page fetch after loading viewHtml (correct cookies).
Usage: python fetch_pdf_bytes.py <viewHtml_ann_id> <download_url> <outfile>
"""
import asyncio, sys, base64
from playwright.async_api import async_playwright

ann_id = sys.argv[1]
url = sys.argv[2]
outfile = sys.argv[3]

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=False,
            args=["--no-sandbox","--disable-dev-shm-usage","--ignore-certificate-errors"])
        ctx=await b.new_context(viewport={"width":800,"height":600},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page=await ctx.new_page()
        # load the real disclosure viewHtml page (sets correct cookies for this origin/path)
        await page.goto(f"https://disclosure.bursamalaysia.com/FileAccess/viewHtml?e={ann_id}", timeout=30000, wait_until="domcontentloaded")
        for i in range(10):
            await asyncio.sleep(1.5)
            if "Just a moment" not in await page.title(): break
        await asyncio.sleep(3)
        escaped = url.replace("\\","\\\\").replace("'","\\'")
        fetch_js = f"async ()=>{{ const r=await fetch('{escaped}'); if(!r.ok) return 'STATUS:'+r.status; const buf=await r.arrayBuffer(); const bytes=new Uint8Array(buf); let bin=''; const CH=0x8000; for(let i=0;i<bytes.length;i+=CH){{ bin+=String.fromCharCode.apply(null, bytes.subarray(i,i+CH)); }} return btoa(bin); }}"
        b64 = await page.evaluate(fetch_js)
        if isinstance(b64,str) and b64.startswith('STATUS:'):
            print(b64, flush=True); sys.exit(1)
        data=base64.b64decode(b64)
        with open(outfile,'wb') as f: f.write(data)
        print(f"OK {len(data)} bytes -> {outfile}", flush=True)
        await b.close()

asyncio.run(main())