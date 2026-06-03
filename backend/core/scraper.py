"""
PlaceDoctor 핵심 엔진 — 네이버 플레이스 순위 검색 및 상세정보 수집

참고: _reference/naver_tracker.py 에서 아래 함수들을 추출·정리했습니다.
  - get_place_details_failsafe  → get_store_details
  - _place_task (내부함수)       → check_place_rank
  - collect_blog_results        → 그대로
  - inspect_blog_post           → 그대로
  - check_blog_ranking_deep     → 그대로
  + diagnose_store              (새로 추가한 진단 래퍼)
"""

import asyncio
import logging
import random
import re
import urllib.parse

from playwright.async_api import async_playwright

from .keywords import generate_keywords

logger = logging.getLogger(__name__)


# ── 텍스트 정규화 헬퍼 ──────────────────────────────────────────────────────────

def normalize_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", "", str(text)).strip()

_AE_TABLE = str.maketrans('베레에게세제테케페헤네메체셰', '배래애개새재태캐패해내매채섀')

def _sn(text):
    """ㅔ→ㅐ 발음 정규화 (베럴짐=배럴짐 동일 처리)"""
    return normalize_text(text).translate(_AE_TABLE)


def extract_address_tokens(address):
    if not address:
        return []
    raw_tokens = re.split(r"[\s,()\[\]<>/]+", address)
    tokens = []
    for t in raw_tokens:
        t = t.strip()
        if len(t) < 2:
            continue
        if t in ["서울", "경기", "인천", "부산", "대구", "대전", "광주", "울산", "세종"]:
            continue
        if any(t.endswith(suffix) for suffix in ["구", "동", "로", "길", "시", "읍", "면"]):
            tokens.append(t)
        elif re.search(r"\d", t) and len(t) >= 3:
            tokens.append(t)
    return list(dict.fromkeys(tokens))[:8]


def clean_blog_url(url):
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        for key in ["url", "u", "target"]:
            if key in qs and qs[key]:
                url = qs[key][0]
                break
    except Exception:
        pass
    try:
        url = urllib.parse.unquote(url)
    except Exception:
        pass
    if "blog.naver.com" in url and "m.blog.naver.com" not in url:
        url = url.replace("https://blog.naver.com", "https://m.blog.naver.com")
        url = url.replace("http://blog.naver.com", "https://m.blog.naver.com")
    return url


# ── 브라우저 관리 ──────────────────────────────────────────────────────────────

async def create_browser():
    """헤드리스 Chromium 브라우저와 컨텍스트를 시작합니다."""
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=ko-KR"]
    )
    context = await browser.new_context(
        locale="ko-KR",
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    )
    return playwright, browser, context


async def close_browser(playwright, browser):
    await browser.close()
    await playwright.stop()


# ── 플레이스 상세정보 수집 ────────────────────────────────────────────────────

async def get_store_details(page, url):
    """
    네이버 플레이스 URL에서 매장 상세정보를 수집합니다.

    Returns:
        dict: {place_id, address, category, menu_items, official_keywords,
               nearby_station, keyword_list}
        실패 시 place_id=None 인 dict 반환
    """
    empty = dict(place_id=None, address="", category="",
                 menu_items=[], official_keywords=[], nearby_station="", keyword_list=[])
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1000)

        p_id = None
        match = re.search(r'\d{8,11}', page.url)
        if match:
            p_id = match.group(0)
        if not p_id:
            return empty

        map_url = f"https://map.naver.com/p/entry/place/{p_id}"
        await page.goto(map_url, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(3000)

        _kl_js = '''() => {
            const fullHtml = document.documentElement.innerHTML;
            const pats = [
                /"keywordList"\\s*:\\s*(\\[[^\\]]{2,2000}\\])/,
                /keywordList["']?\\s*:\\s*(\\[[^\\]]{2,2000}\\])/
            ];
            for (const pat of pats) {
                const m = fullHtml.match(pat);
                if (m) { try { const r = JSON.parse(m[1]); if (r && r.length) return r; } catch(e) {} }
            }
            return [];
        }'''
        keyword_list = await page.evaluate(_kl_js)
        if not keyword_list:
            await page.wait_for_timeout(2000)
            keyword_list = await page.evaluate(_kl_js)

        iframe_src = await page.evaluate('''() => {
            for (const f of document.querySelectorAll("iframe")) {
                const src = f.src || "";
                if (src.includes("pcmap.place.naver.com") || src.includes("place.map.naver.com")) return src;
            }
            return "";
        }''')

        if iframe_src:
            try:
                await page.goto(iframe_src, wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(3000)
                try:
                    await page.wait_for_selector(".pz7wy, .PkgBl, .LDgIH, .IH7VW, [class*='addr']", timeout=5000)
                except Exception:
                    pass
                if not keyword_list:
                    keyword_list = await page.evaluate('''() => {
                        const fullHtml = document.documentElement.innerHTML;
                        const m = fullHtml.match(/"keywordList"\\s*:\\s*(\\[[^\\]]{2,400}\\])/);
                        if (m) { try { const r = JSON.parse(m[1]); if (r && r.length) return r; } catch(e) {} }
                        return [];
                    }''')
            except Exception as iframe_err:
                logger.warning(f"iframe 로딩 지연: {type(iframe_err).__name__}")

        details = await page.evaluate('''() => {
            const REGIONS = ["서울","경기","인천","부산","대구","광주","대전","울산","세종","강원","충북","충남","전북","전남","경북","경남","제주"];
            const addrSels = [".pz7wy",".PkgBl",".LDgIH",".IH7VW","[class*='addr_area']","[class*='address']","address"];
            let addrEl = null;
            for (const sel of addrSels) {
                const el = document.querySelector(sel);
                if (el && (el.innerText||"").trim().length > 5) { addrEl = el; break; }
            }
            let addr = addrEl ? (addrEl.innerText || "").split("\\n")[0].trim() : "";

            if (!addr) {
                for (const s of document.querySelectorAll("script:not([src])")) {
                    const t = s.textContent || "";
                    const m = t.match(/"(?:roadAddress|address|jibunAddress)"\\s*:\\s*"([^"]{5,80})"/);
                    if (m && REGIONS.some(r => m[1].includes(r))) { addr = m[1]; break; }
                }
            }
            if (!addr) {
                for (const el of document.querySelectorAll("span, a, div, p")) {
                    const t = (el.innerText || "").trim().split("\\n")[0].trim();
                    if (t.length > 5 && t.length < 80 && REGIONS.some(r => t.includes(r)) && (t.includes("구") || t.includes("동") || t.includes("로") || t.includes("길"))) {
                        addr = t; break;
                    }
                }
            }
            const cat = document.querySelector(".DJJvD, .lnJFt")?.innerText?.trim() || "";

            let dongFound = "";
            if (addrEl) {
                const m = (addrEl.innerText || "").match(/([가-힣]{2,6}동)(?=[\\s\\d\\-·,]|$)/);
                if (m) dongFound = m[1];
            }
            if (!dongFound && addr) {
                const m = addr.match(/([가-힣]{2,6}동)/);
                if (m) dongFound = m[1];
            }
            if (!dongFound) {
                const bodyText = document.body ? (document.body.innerText || "") : "";
                const m = bodyText.match(/[시군구]\\s+([가-힣]{2,6}동)/);
                if (m && m[1] && m[1].length >= 3) dongFound = m[1];
            }
            if (!dongFound) {
                for (const s of document.querySelectorAll("script:not([src])")) {
                    const t = s.textContent || "";
                    const m = t.match(/"(?:legalDong|dong|eupMyeonDong|address)"\\s*:\\s*"([가-힣]{2,6}동)/);
                    if (m) { dongFound = m[1]; break; }
                }
            }

            let nearbyStation = "";
            const bodyText2 = document.body ? (document.body.innerText || "") : "";
            const stM = bodyText2.match(/([가-힣]{2,8}역)\s*(?:\d+번\s*출구|에서\s*(?:도보|차로|차량|\d))/);
            if (stM) nearbyStation = stM[1];
            if (!nearbyStation) {
                const stM2 = bodyText2.match(/([가-힣]{2,8}역)\s*(?:도보|방향|하차|인근|근처)/);
                if (stM2) nearbyStation = stM2[1];
            }

            return { address: addr, category: cat, nearbyDong: dongFound, nearbyStation };
        }''')

        menu_items = await page.evaluate('''() => {
            const menus = Array.from(document.querySelectorAll(".lPzOq.VXIyT, .Sqg65, .A_cdD, .y20fl"));
            return menus.slice(0, 5).map(el => el.innerText).filter(text => text && text.length > 1);
        }''')

        official_keywords = await page.evaluate('''() => {
            const tags = Array.from(document.querySelectorAll(".PR2aT .P1zUJ, .P1zUJ, .hB45E, .place_bluelink"));
            return tags.map(el => el.innerText.replace(/#/g, '')).filter(text => text && text.length > 1);
        }''')

        _ADDR_REGIONS = ["서울","경기","인천","부산","대구","광주","대전","울산","세종","강원","충북","충남","전북","전남","경북","경남","제주"]
        _ADDR_SUFFIXES = ["구","동","로","길","읍","면","리"]
        def _valid_addr(a):
            return bool(a) and any(r in a for r in _ADDR_REGIONS) and any(s in a for s in _ADDR_SUFFIXES)

        address_full = details["address"]
        if not _valid_addr(address_full):
            address_full = ""
        nearby_dong = details.get("nearbyDong", "")
        nearby_station = details.get("nearbyStation", "")

        if not nearby_dong:
            jibun = await page.evaluate('''() => {
                for (const s of document.querySelectorAll("script:not([src])")) {
                    const t = s.textContent || "";
                    const m1 = t.match(/"jibunAddress"\\s*:\\s*"([^"]+)"/);
                    if (m1) return m1[1];
                    const m2 = t.match(/"legalDong"\\s*:\\s*"([가-힣]{2,6}동)"/);
                    if (m2) return m2[1];
                    const m3 = t.match(/[구군]\\s+([가-힣]{2,6}동)/);
                    if (m3) return m3[1];
                }
                return "";
            }''')
            if jibun:
                m = re.search(r'([가-힣]{2,6}동)', jibun)
                if m:
                    nearby_dong = m.group(1)

        if not address_full and p_id:
            try:
                await page.goto(f"https://m.place.naver.com/place/{p_id}/home",
                                wait_until="domcontentloaded", timeout=12000)
                await page.wait_for_timeout(2000)
                mob = await page.evaluate('''() => {
                    const REGIONS = ["서울","경기","인천","부산","대구","광주","대전","울산","세종","강원","충북","충남","전북","전남","경북","경남","제주"];
                    for (const s of document.querySelectorAll("script:not([src])")) {
                        const t = s.textContent || "";
                        const m = t.match(/"(?:roadAddress|address|jibunAddress)"\\s*:\\s*"([^"]{5,80})"/);
                        if (m && REGIONS.some(r => m[1].includes(r))) return { addr: m[1], dong: "", station: "" };
                    }
                    for (const el of document.querySelectorAll("span, div, p, address")) {
                        const t = (el.innerText || "").trim().split("\\n")[0].trim();
                        if (t.length > 5 && t.length < 80 && REGIONS.some(r => t.includes(r)) && (t.includes("구") || t.includes("동") || t.includes("로") || t.includes("길"))) {
                            const dm = t.match(/([가-힣]{2,6}동)/);
                            const sm = (document.body.innerText||"").match(/([가-힣]{2,8}역)\\s*(?:\\d+번\\s*출구|에서\\s*(?:도보|차로|차량))/);
                            return { addr: t, dong: dm ? dm[1] : "", station: sm ? sm[1] : "" };
                        }
                    }
                    return { addr: "", dong: "", station: "" };
                }''')
                if mob.get("addr"):
                    address_full = mob["addr"]
                    if not nearby_dong: nearby_dong = mob.get("dong", "")
                    if not nearby_station: nearby_station = mob.get("station", "")
            except Exception:
                pass

        logger.info(f"주소: {address_full} | 동: {nearby_dong or '(없음)'} | 역: {nearby_station or '(없음)'}")
        if keyword_list:
            logger.info(f"대표 키워드: {', '.join(keyword_list)}")

        if nearby_dong and nearby_dong not in address_full:
            address_full = address_full + " " + nearby_dong

        return dict(
            place_id=p_id,
            address=address_full,
            category=details["category"],
            menu_items=menu_items,
            official_keywords=official_keywords,
            nearby_station=nearby_station,
            keyword_list=keyword_list,
        )
    except Exception as e:
        logger.warning(f"정보 수집 실패: {e}")
        return empty


# ── 플레이스 순위 검색 ────────────────────────────────────────────────────────

async def check_place_rank(page, keyword, place_id, safe_mode=True):
    """
    네이버 플레이스에서 keyword 검색 시 place_id 매장의 순위를 반환합니다.

    Returns:
        int | None: 순위 (1~30위), 30위 밖이거나 미발견이면 None
    """
    if not place_id:
        logger.warning(f"[{keyword}] place_id 없음")
        return None

    encoded_kw = urllib.parse.quote(keyword)
    logger.info(f"  검색: '{keyword}'")

    if safe_mode:
        await asyncio.sleep(random.uniform(0.05, 0.15))

    p_url = f"https://search.naver.com/search.naver?query={encoded_kw}&where=place&sm=tab_jum"
    await page.goto(p_url, wait_until="domcontentloaded", timeout=12000)
    await page.wait_for_timeout(900)

    try:
        await page.wait_for_function(
            """() => {
                let c = 0;
                for (const li of document.querySelectorAll('li')) {
                    for (const a of li.querySelectorAll('a[href]')) {
                        const h = a.getAttribute('href') || '';
                        if (h.includes('map.naver') && /\\d{8,11}/.test(h)) { c++; break; }
                    }
                    if (c >= 5) return true;
                }
                return false;
            }""",
            timeout=5000
        )
    except Exception:
        pass

    _scroll_js = '''() => {
        let scrolled = 0;
        for (const c of document.querySelectorAll('ul, div')) {
            if (c.scrollHeight > c.clientHeight + 50 && c.clientHeight > 200) {
                const r = c.getBoundingClientRect();
                if (r.left < 800) { c.scrollTop = c.scrollHeight; scrolled++; }
            }
        }
        window.scrollTo(0, document.body.scrollHeight);
        return scrolled;
    }'''

    for _ in range(12):
        await page.evaluate(_scroll_js)
        await page.wait_for_timeout(350)

    _raw = await page.evaluate('''() => {
        // 카드 단위 광고 필터 (광고+오가닉 동일 pid도 오가닉 정상 검출)
        const ranked_ids = [], all_ids = [];
        const seenR = new Set(), seenA = new Set();
        const pats = [
            /(?:place|restaurant|accommodation|hairshop|clinic|nail|gym|cafe|map|pcmap|store|outlink|entry\/place|local\/naver_place|pinId|beauty|spa|wellness)\\/(\\d{8,11})/i,
            /[?&](?:id|pid|placeId|bizId)=(\\d{8,11})/i
        ];
        const adRe = /\\/\\/(?:ader|ad)\\.(?:search\\.)?naver\\.com/i;
        const cardsInfo = new Map();
        for (const li of document.querySelectorAll('li')) {
            let pid = null, isAd = false;
            for (const a of li.querySelectorAll('a[href]')) {
                const h = a.getAttribute('href') || '';
                if (adRe.test(h)) isAd = true;
                if (!pid) {
                    let d=''; try{d=decodeURIComponent(h);}catch(e){d=h;}
                    for (const p of pats){const m=d.match(p);if(m){pid=m[1];break;}}
                }
            }
            if (!pid) {
                for (const el of li.querySelectorAll('[data-id],[data-place-id],[data-cid],[data-sid]')) {
                    const p=el.getAttribute('data-id')||el.getAttribute('data-place-id')||el.getAttribute('data-cid')||el.getAttribute('data-sid');
                    if(p&&/^\\d{8,11}$/.test(p)){pid=p;break;}
                }
            }
            if (!pid) continue;
            let parentReg=false; let anc=li.parentElement;
            while(anc){if(anc.tagName==='LI'&&cardsInfo.has(anc)){parentReg=true;break;}anc=anc.parentElement;}
            if (parentReg) continue;
            cardsInfo.set(li, {pid, isAd});
        }
        const sortedCards = Array.from(cardsInfo.entries()).sort(([a],[b])=>{
            const pos=a.compareDocumentPosition(b);
            if(pos&0x04)return -1; if(pos&0x02)return 1; return 0;
        });
        for (const [li, info] of sortedCards) {
            if (info.isAd) continue;
            if (seenR.has(info.pid)) continue;
            seenR.add(info.pid); ranked_ids.push(info.pid);
        }
        all_ids.push(...ranked_ids); seenR.forEach(x=>seenA.add(x));
        for (const [li, info] of sortedCards) {
            if (!seenA.has(info.pid)) { seenA.add(info.pid); all_ids.push(info.pid); }
        }
        try{const html=document.documentElement.innerHTML;const jp=/"(?:placeId|place_id|pid|sid|bizId)"\\s*:\\s*"?(\\d{8,11})"?/gi;let jm;
            while((jm=jp.exec(html))!==null){if(!seenA.has(jm[1])){seenA.add(jm[1]);all_ids.push(jm[1]);}}}catch(e){}
        return {ranked_ids, all_ids};
    }''')

    p_ids = _raw.get('ranked_ids', [])

    # 데스크탑 결과가 아예 없을 때만 모바일 재시도
    if not p_ids:
        p_url_m = f"https://m.search.naver.com/search.naver?query={encoded_kw}&where=m_place"
        await page.goto(p_url_m, wait_until="domcontentloaded", timeout=10000)
        await page.wait_for_timeout(600)
        for _ in range(8):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(250)
        m_ids = await page.evaluate('''() => {
            const ids = [], seen = new Set();
            const pats = [
                /(?:place|restaurant|accommodation|hairshop|clinic|nail|gym|cafe|map|entry\/place|local\/naver_place)\\/(\\d{8,11})/i,
                /[?&](?:id|pid|placeId)=(\\d{8,11})/i
            ];
            for (const a of document.querySelectorAll("a[href]")) {
                try {
                    const d = decodeURIComponent(a.getAttribute("href")||"");
                    for (const p of pats) { const m=d.match(p); if(m&&!seen.has(m[1])){seen.add(m[1]);ids.push(m[1]);break;} }
                } catch(e) {}
            }
            for (const el of document.querySelectorAll("[data-id],[data-place-id],[data-cid],[data-sid]")) {
                const pid=el.getAttribute("data-id")||el.getAttribute("data-place-id")||el.getAttribute("data-cid")||el.getAttribute("data-sid");
                if(pid&&/^\\d{8,11}$/.test(pid)&&!seen.has(pid)){seen.add(pid);ids.push(pid);}
            }
            return ids;
        }''')
        logger.debug(f"  [{keyword}] 모바일 p_ids={len(m_ids)}개, 감지={'O' if place_id in m_ids else 'X'}")
        if m_ids:
            p_ids = m_ids

    # 2페이지 폴백 (1페이지에 없을 때)
    if place_id and place_id not in p_ids:
        try:
            _p2_url = p_url + "&start=16"
            await page.goto(_p2_url, wait_until="domcontentloaded", timeout=12000)
            await page.wait_for_timeout(800)
            for _ in range(10):
                await page.evaluate(_scroll_js)
                await page.wait_for_timeout(350)
            _p2_ids = await page.evaluate('''() => {
                const ids = [], seen = new Set();
                const pats = [
                    /(?:place|restaurant|accommodation|hairshop|clinic|nail|gym|cafe|map|pcmap|store|outlink|entry\\/place|local\\/naver_place|pinId|beauty|spa|wellness)\\/(\\d{8,11})/i,
                    /[?&](?:id|pid|placeId|bizId)=(\\d{8,11})/i
                ];
                const adRe = /\\/\\/(?:ader|ad)\\.(?:search\\.)?naver\\.com/i;
                const cardsInfo = new Map();
                for (const li of document.querySelectorAll('li')) {
                    let pid=null, isAd=false;
                    for (const a of li.querySelectorAll('a[href]')) {
                        const h=a.getAttribute('href')||'';
                        if(adRe.test(h))isAd=true;
                        if(!pid){let d='';try{d=decodeURIComponent(h);}catch(e){d=h;}for(const p of pats){const m=d.match(p);if(m){pid=m[1];break;}}}
                    }
                    if(!pid){for(const el of li.querySelectorAll('[data-id],[data-place-id],[data-cid],[data-sid]')){const p=el.getAttribute('data-id')||el.getAttribute('data-place-id')||el.getAttribute('data-cid')||el.getAttribute('data-sid');if(p&&/^\\d{8,11}$/.test(p)){pid=p;break;}}}
                    if(!pid)continue;
                    let parentReg=false;let anc=li.parentElement;
                    while(anc){if(anc.tagName==='LI'&&cardsInfo.has(anc)){parentReg=true;break;}anc=anc.parentElement;}
                    if(parentReg)continue;
                    cardsInfo.set(li,{pid,isAd});
                }
                const sorted=Array.from(cardsInfo.entries()).sort(([a],[b])=>{const pos=a.compareDocumentPosition(b);if(pos&0x04)return -1;if(pos&0x02)return 1;return 0;});
                for(const [li, info] of sorted){if(info.isAd)continue;if(seen.has(info.pid))continue;seen.add(info.pid);ids.push(info.pid);}
                return ids;
            }''')
            logger.debug(f"  [{keyword}] 2페이지 p_ids={len(_p2_ids)}개, 감지={'O' if place_id in _p2_ids else 'X'}")
            if _p2_ids and place_id in _p2_ids:
                r2 = _p2_ids.index(place_id) + 16
                if r2 <= 30:
                    logger.info(f"  [{keyword}] 2페이지 {r2}위 검출")
                    return r2
        except Exception as fe:
            logger.warning(f"  [{keyword}] 2페이지 폴백 오류: {fe}")

    logger.debug(f"  [{keyword}] p_ids={len(p_ids)}개, 감지={'O' if place_id in p_ids else 'X'}")
    if place_id in p_ids:
        rank_num = p_ids.index(place_id) + 1
        if rank_num <= 30:
            logger.info(f"  [{keyword}] → {rank_num}위")
            return rank_num
    return None


# ── 블로그 순위 수집 (플마에서 검증된 로직 그대로) ────────────────────────────

async def collect_blog_results(page, keyword, limit=30, log_func=None):
    encoded_kw = urllib.parse.quote(keyword)
    blog_url = f"https://search.naver.com/search.naver?ssc=tab.blog.all&sm=tab_jum&query={encoded_kw}"
    await page.goto(blog_url, wait_until="domcontentloaded", timeout=15000)
    await page.wait_for_timeout(800)

    for _ in range(7):
        prev_height = await page.evaluate("document.body.scrollHeight")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(350)
        new_height = await page.evaluate("document.body.scrollHeight")
        if new_height == prev_height:
            break

    results = await page.evaluate('''(limit) => {
        function norm(s) {
            return (s || "").replace(/[\\s]+/g, " ").trim();
        }
        function cleanHref(href) {
            try { return decodeURIComponent(href || ""); } catch(e) { return href || ""; }
        }
        function isMainPostLink(href) {
            href = cleanHref(href);
            if (!href.includes("blog.naver.com")) return false;
            if (href.includes("PostList") || href.includes("profile") || href.includes("section")) return false;
            return /blog\\.naver\\.com\\/([^/?#]+)\\/(\\d{9,13})/.test(href) ||
                   /blog\\.naver\\.com\\/PostView/.test(href) ||
                   (href.includes("blogId=") && href.includes("logNo="));
        }
        function getPostKey(href) {
            href = cleanHref(href);
            let m = href.match(/blog\\.naver\\.com\\/([^/?#]+)\\/(\\d{9,13})/);
            if (m) return m[1] + "/" + m[2];
            try {
                const u = new URL(href);
                const blogId = u.searchParams.get("blogId") || "";
                const logNo = u.searchParams.get("logNo") || "";
                if (blogId && logNo) return blogId + "/" + logNo;
            } catch(e) {}
            return href.split("?")[0];
        }
        function isInsideAd(el) {
            let p = el;
            for (let i = 0; i < 8 && p; i++, p = p.parentElement) {
                const cls = ((p.className || "") + " " + (p.id || "")).toString().toLowerCase();
                if (/power_link|sp_nreview|ad_area|_ad|ugcad|adchoice/.test(cls)) return true;
            }
            return false;
        }
        function isTitleLike(txt) {
            if (!txt || txt.length < 8 || txt.length > 150) return false;
            if (txt.includes("blog.naver") || txt.includes("naver.com") || txt.includes("›")) return false;
            if (/^https?:/.test(txt)) return false;
            return true;
        }
        function extractTitle(card) {
            const selectors = [
                "strong.total_tit", "a.api_txt_lines.total_tit",
                ".total_tit", "a.title_link", "a.link_tit",
                ".title_area a", "strong a", "h2 a", ".article_tit"
            ];
            for (const sel of selectors) {
                for (const el of Array.from(card.querySelectorAll(sel))) {
                    const txt = norm(el.innerText || el.textContent || "");
                    if (isTitleLike(txt)) return txt;
                }
            }
            for (const a of Array.from(card.querySelectorAll("a"))) {
                const href = a.href || a.getAttribute("href") || "";
                if (!isMainPostLink(href)) continue;
                const txt = norm(a.innerText || a.textContent || "").replace(/blog[.]naver[.]com[^ ]*/g, "").trim();
                if (isTitleLike(txt)) return txt;
            }
            const lines = (card.innerText || "").split(/[\\n\\r]+/).map(l => l.trim()).filter(l => isTitleLike(l));
            lines.sort((a, b) => b.length - a.length);
            return lines[0] ? lines[0].slice(0, 100) : "";
        }

        const seenPosts = new Set();
        const list = [];

        function collectFromCard(card) {
            if (isInsideAd(card)) return;
            const anchors = Array.from(card.querySelectorAll("a[href]"));
            const postLinks = anchors
                .map(a => a.getAttribute("href") || "")
                .filter(isMainPostLink);
            if (postLinks.length === 0) return;
            const key = getPostKey(postLinks[0]);
            if (seenPosts.has(key)) return;
            seenPosts.add(key);
            const allLinks = anchors.map(a => a.href || a.getAttribute("href") || "");
            list.push({
                rank: list.length + 1,
                title: extractTitle(card),
                link: postLinks[0],
                text: norm(card.innerText || ""),
                cardLinks: allLinks,
                card_html: card.outerHTML.slice(0, 8000)
            });
        }

        // 1순위: ugcItem (개별 포스팅 카드, DOM 순서 = 실제 순위) — Naver SDS 신 디자인
        for (const card of document.querySelectorAll('div[data-template-id="ugcItem"]')) {
            if (list.length >= limit) break;
            collectFromCard(card);
        }

        // 2순위: fds-web-doc-root (상위 피처드 카드, ugcItem 없을 때 폴백)
        if (list.length < 5) {
            for (const card of document.querySelectorAll('div[class*="fds-web-doc-root"]')) {
                if (list.length >= limit) break;
                collectFromCard(card);
            }
        }

        // 3순위: data-template-id="layout" 카드 (폴백)
        if (list.length < 5) {
            for (const card of document.querySelectorAll('div[data-template-id="layout"]')) {
                if (list.length >= limit) break;
                collectFromCard(card);
            }
        }

        // 4순위: 구 디자인 li.bx / .view_wrap / .total_wrap 폴백
        if (list.length < 5) {
            for (const card of document.querySelectorAll("li.bx, .view_wrap, .total_wrap")) {
                if (list.length >= limit) break;
                collectFromCard(card);
            }
        }

        // 5순위: 전체 앵커 스캔 (최후 수단)
        if (list.length < 5) {
            const allAnchors = Array.from(document.querySelectorAll("a[href*='blog.naver.com']"));
            for (const a of allAnchors) {
                if (list.length >= limit) break;
                const href = a.getAttribute("href") || "";
                if (!isMainPostLink(href)) continue;
                if (isInsideAd(a)) continue;
                const key = getPostKey(href);
                if (seenPosts.has(key)) continue;
                seenPosts.add(key);
                let card = a.parentElement;
                for (let i = 0; i < 6 && card && card !== document.body; i++) {
                    const hasSibs = card.parentElement &&
                        Array.from(card.parentElement.children).filter(c => c !== card).length > 0;
                    if (hasSibs) break;
                    card = card.parentElement;
                }
                const titleTxt = card ? norm(card.innerText || "").split(/[\\n\\r]+/)[0].slice(0, 80) : "";
                list.push({
                    rank: list.length + 1,
                    title: (isTitleLike(titleTxt) ? titleTxt : null) ||
                           norm(a.innerText || a.textContent || "").slice(0, 80) || "제목 확인 필요",
                    link: href,
                    text: card ? norm(card.innerText || "").slice(0, 200) : "",
                    cardLinks: [href],
                    card_html: card ? card.outerHTML.slice(0, 4000) : ""
                });
            }
        }

        return list;
    }''', limit)

    return results


async def inspect_blog_post(page, blog_url, store_name, place_id, address, card_text="", card_links=None, card_html=""):
    card_links = card_links or []
    pid = str(place_id) if place_id else ""
    clean_store = normalize_text(store_name)
    address_tokens = extract_address_tokens(address)

    from .keywords import _INTENT_TOKENS as _IT
    brand_candidates = []
    for part in re.split(r"\s+", store_name.strip()):
        original = part
        part = re.sub(r"(본점|직영점|지점|점)$", "", part).strip()
        suffix_removed = (original != part)
        if len(part) < 3:
            continue
        if suffix_removed and len(part) <= 4 and re.match(r'^[가-힣]+$', part):
            continue
        brand_candidates.append(part)
    if store_name.strip():
        brand_candidates.append(store_name.strip())
    brand_candidates = list(dict.fromkeys(brand_candidates))

    score = 0
    reasons = []

    joined_card_links = " ".join(card_links) + " " + card_html
    if pid and pid in joined_card_links:
        score += 120
        reasons.append("검색카드 장소태그ID")

    if clean_store and (clean_store in normalize_text(card_text) or _sn(clean_store) in _sn(card_text)):
        score += 35
        reasons.append("검색카드 업체명")

    if score >= 100:
        return {"matched": True, "score": score, "reasons": reasons, "text_sample": "", "page_title": ""}

    try:
        url = clean_blog_url(blog_url)
        await page.goto(url, wait_until="domcontentloaded", timeout=12000)
        await page.wait_for_timeout(350)

        page_title = ""
        try:
            raw_title = await page.evaluate("() => document.title")
            for sep in [" : ", " | ", " - ", "::","｜"]:
                if sep in raw_title:
                    raw_title = raw_title.split(sep)[0].strip()
                    break
            page_title = raw_title[:100].strip()
        except Exception:
            pass

        for _ in range(2):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(200)

        outer_html = await page.evaluate("() => document.documentElement.outerHTML")
        html_decoded = urllib.parse.unquote(outer_html)

        iframe_html = ""
        try:
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                try:
                    fhtml = await frame.evaluate("() => document.documentElement.outerHTML")
                    iframe_html += fhtml
                except Exception:
                    pass
        except Exception:
            pass

        full_html = html_decoded + " " + iframe_html

        if pid:
            pid_patterns = [
                pid,
                f"placeId/{pid}",
                f'placeId":"{pid}',
                f"mapId={pid}",
                f"pid={pid}",
                f"place%2F{pid}",
                urllib.parse.quote(f'"placeId":"{pid}"'),
            ]
            for pat in pid_patterns:
                if pat in full_html:
                    score += 150
                    reasons.append("본문 플레이스ID")
                    break

        map_signals = [
            "map.naver.com", "place.naver.com", "pcmap.place.naver.com", "naver.me",
            "placeSection", "placeId", "businessId", "blog_map", "se-map"
        ]
        if any(sig in full_html for sig in map_signals):
            score += 25
            reasons.append("본문 지도/장소태그 흔적")

        body_text = await page.evaluate("() => (document.body ? document.body.innerText : '').replace(/[ \\t\\n\\r]+/g, ' ').trim()")
        normalized_body = normalize_text(body_text)

        _sn_body = _sn(body_text)
        if clean_store and (_sn(clean_store) in _sn_body):
            score += 55
            reasons.append("본문 업체명")
        else:
            matched_brands = [b for b in brand_candidates if normalize_text(b) and _sn(b) in _sn_body]
            if matched_brands:
                score += 30
                reasons.append(f"본문 브랜드명({matched_brands[0]})")

        matched_addr = [t for t in address_tokens if t and t in body_text]
        if len(matched_addr) >= 2:
            score += 35
            reasons.append("본문 주소일부")
        elif len(matched_addr) == 1:
            score += 15
            reasons.append("본문 주소1개")

        has_pid_in_body = "본문 플레이스ID" in reasons
        has_store_in_body = "본문 업체명" in reasons or any(r.startswith("본문 브랜드명") for r in reasons)
        _card_ok = "검색카드 장소태그ID" in reasons or "검색카드 업체명" in reasons
        _unique_brands = [
            b for b in brand_candidates
            if b != store_name.strip()
            and b not in _IT
            and len(normalize_text(b)) >= 3
            and not (re.match(r'^\d+', b) and len(normalize_text(b)) <= 3)
        ]
        _strong_confirm = _card_ok or has_pid_in_body

        _sn_title = _sn(page_title) if page_title else ""
        if not _strong_confirm:
            if _unique_brands and page_title:
                brand_in_title = any(
                    _sn(b) in _sn_title
                    for b in _unique_brands if len(normalize_text(b)) >= 2
                )
                if not brand_in_title:
                    reasons.append("제목업체명없음(부수적언급)")
                    return {"matched": False, "score": score, "reasons": reasons,
                            "text_sample": "", "page_title": page_title}
            elif not _unique_brands:
                if not has_pid_in_body and "검색카드 업체명" not in reasons and page_title:
                    brand_in_title = _sn(store_name.strip()) in _sn_title
                    if not brand_in_title:
                        reasons.append("제목업체명없음(부수적언급)")
                        return {"matched": False, "score": score, "reasons": reasons,
                                "text_sample": "", "page_title": page_title}

        if has_pid_in_body and not has_store_in_body and not _card_ok:
            brand_in_title = page_title and any(_sn(b) in _sn_title for b in brand_candidates)
            if not brand_in_title:
                reasons.append("부수적장소태그(제목업체명없음)")
                return {"matched": False, "score": score, "reasons": reasons, "text_sample": body_text[:120], "page_title": page_title}

        has_strong_signal = has_pid_in_body or "본문 업체명" in reasons or "검색카드 업체명" in reasons
        has_brand_signal = any(r.startswith("본문 브랜드명") for r in reasons)
        if has_strong_signal:
            threshold = 55
        elif has_brand_signal:
            threshold = 75
        else:
            threshold = 130
        matched = score >= threshold
        return {
            "matched": matched,
            "score": score,
            "reasons": reasons,
            "text_sample": body_text[:120],
            "page_title": page_title
        }

    except Exception as e:
        matched = score >= 100
        if matched:
            reasons.append("본문진입실패/카드근거충분")
        else:
            reasons.append(f"본문검사오류:{str(e)[:40]}")
        return {
            "matched": matched,
            "score": score,
            "reasons": reasons,
            "text_sample": "",
            "page_title": ""
        }


async def check_blog_ranking_deep(page, inspect_page, keyword, store_name, place_id, address, log_func=None, max_hits=5, url_cache=None):
    def _log(msg):
        if log_func:
            log_func(msg)
        else:
            logger.debug(msg)

    pid_str = str(place_id) if place_id else ""
    if url_cache is None:
        url_cache = {}
    try:
        results = await collect_blog_results(page, keyword, limit=10, log_func=log_func)
        if not results:
            return [{"status": "검색결과없음", "rank": None, "title": "", "blog_link": "", "score": 0, "reasons": []}]

        hits = []
        for item in results:
            if len(hits) >= max_hits:
                break

            card_combined = item.get("card_html", "") + " ".join(item.get("cardLinks", []))
            if pid_str and pid_str in card_combined:
                _log(f"      ↳ {item['rank']}위 카드즉시감지 → 합격")
                hits.append({
                    "status": f"{item['rank']}위",
                    "rank": item["rank"],
                    "title": item.get("title", ""),
                    "blog_link": clean_blog_url(item.get("link", "")),
                    "score": 150,
                    "reasons": ["카드장소태그ID"]
                })
                continue

            item_url = clean_blog_url(item.get("link", ""))
            if item_url and item_url in url_cache:
                checked = url_cache[item_url]
                _log(f"      ↳ {item['rank']}위 캐시({item.get('title','')[:15]}) 점수={checked['score']}")
            else:
                _log(f"      ↳ {item['rank']}위 딥스캔 중... ({item.get('title','')[:20]})")
                checked = await inspect_blog_post(
                    page=inspect_page,
                    blog_url=item.get("link", ""),
                    store_name=store_name,
                    place_id=place_id,
                    address=address,
                    card_text=item.get("text", ""),
                    card_links=item.get("cardLinks", []),
                    card_html=item.get("card_html", "")
                )
                if item_url:
                    url_cache[item_url] = checked
                _log(f"         점수={checked['score']} 근거={checked['reasons']}")

            if checked["matched"]:
                _log(f"      ↳ {item['rank']}위 → 합격")
                best_title = checked.get("page_title") or item.get("title", "")
                hits.append({
                    "status": f"{item['rank']}위",
                    "rank": item["rank"],
                    "title": best_title,
                    "blog_link": item_url,
                    "score": checked["score"],
                    "reasons": checked["reasons"]
                })

        if not hits:
            return [{"status": "순위권 밖", "rank": None, "title": "", "blog_link": "", "score": 0, "reasons": []}]

        hits.sort(key=lambda x: x["rank"] if x["rank"] else 999)
        return hits

    except Exception as e:
        return [{"status": "추적오류", "rank": None, "title": "", "blog_link": "", "score": 0, "reasons": [str(e)[:80]]}]


# ── 통합 진단 래퍼 ────────────────────────────────────────────────────────────

async def diagnose_store(store_name: str, place_url: str = None, keywords: list = None) -> dict:
    """
    매장 정보를 받아 플레이스 순위 진단 결과를 한 덩어리로 반환합니다.

    Args:
        store_name: 매장명
        place_url:  네이버 플레이스 URL (없으면 키워드만 생성)
        keywords:   직접 지정 키워드 목록 (없으면 자동 생성)

    Returns:
        {
          "store_name": str,
          "place_id": str | None,
          "address": str,
          "category": str,
          "keywords_used": list[str],
          "place_results": [{"keyword": str, "rank": int | None}],
        }
    """
    playwright, browser, context = await create_browser()
    try:
        page = await context.new_page()
        details = {}
        if place_url:
            details = await get_store_details(page, place_url)

        place_id = details.get("place_id")
        address = details.get("address", "")
        category = details.get("category", "")
        menu_items = details.get("menu_items", [])
        official_keywords = details.get("official_keywords", [])
        nearby_station = details.get("nearby_station", "")
        keyword_list = details.get("keyword_list", [])

        if keywords:
            target_keywords = keywords
        else:
            target_keywords = generate_keywords(
                store_name=store_name,
                category=category,
                address=address,
                menu_items=menu_items,
                official_keywords=official_keywords,
                nearby_station=nearby_station,
                keyword_list=keyword_list,
            )[:20]

        place_results = []
        for kw in target_keywords:
            rank = await check_place_rank(page, kw, place_id)
            place_results.append({"keyword": kw, "rank": rank})

        return {
            "store_name": store_name,
            "place_id": place_id,
            "address": address,
            "category": category,
            "keywords_used": target_keywords,
            "place_results": place_results,
        }
    finally:
        await close_browser(playwright, browser)
