import asyncio
import json
import re
from playwright.async_api import async_playwright
from urllib.parse import quote

async def test():
    query = "백세돼지국밥"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        
        url = f"https://search.naver.com/search.naver?where=nexearch&query={quote(query)}"
        await page.goto(url, timeout=30000)
        await page.wait_for_timeout(2000)

        script_content = await page.evaluate('''() => {
            const scripts = document.querySelectorAll("script");
            for (const s of scripts) {
                const t = s.textContent || "";
                if (t.includes("__APOLLO_STATE__") && t.includes("PlaceListBusinesses")) {
                    return t;
                }
            }
            return null;
        }''')
        
        if script_content:
            match = re.search(r'__APOLLO_STATE__\s*=\s*(\{.+?\});', script_content, re.DOTALL)
            if match:
                state = json.loads(match.group(1))
                for k, v in state.items():
                    if isinstance(v, dict) and k.startswith("PlaceListBusinessesItem:"):
                        print(f"Keys: {list(v.keys())}")
                        print(f"address: {v.get('address')}")
                        print(f"roadAddress: {v.get('roadAddress')}")
                        print(f"fullAddress: {v.get('fullAddress')}")
                        print(f"fullRoadAddress: {v.get('fullRoadAddress')}")
                        print(f"abbrAddress: {v.get('abbrAddress')}")
                        break

        await browser.close()

asyncio.run(test())
