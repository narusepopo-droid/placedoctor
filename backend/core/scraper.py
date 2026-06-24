"""
PlaceDoctor 핵심 엔진 — 네이버 플레이스 순위 검색 및 상세정보 수집

참고: _reference/naver_tracker.py 에서 아래 함수들을 추출·정리했습니다.
  - get_place_details_failsafe  → get_store_details  (2단계: 리뷰·별점·사진수 추가)
  - _place_task (내부함수)       → _fetch_place_ranking + check_place_rank
  - collect_blog_results        → 그대로
  - inspect_blog_post           → 그대로
  - check_blog_ranking_deep     → 그대로
  + _build_competitor_compare   (P단계: S/A급 1위아닌 키워드 최대3 비교, 검색결과 재사용)
  + _fetch_place_name           (P단계: 경쟁사 1위 매장 이름만 가볍게 — map 타이틀)
  + diagnose_store              (진단 래퍼, 2단계: 경쟁사·점수 포함)
"""

import asyncio
import logging
import random
import re
import time
import urllib.parse

from playwright.async_api import async_playwright

from .keywords import generate_keywords
from .scoring import calculate_scores

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
    """
    헤드리스 Chromium 브라우저와 컨텍스트를 시작합니다.

    ⚠️ 차단 우회 설정은 플마(_reference/naver_tracker.py)와 **바이트 단위로 동일**하게 유지한다.
       플마는 같은 PC/IP에서 한 번도 IP 차단이 없었으므로, 플닥을 플마와 똑같이 맞추면 안 막힌다.
       한 군데라도 다르면 그게 차단 원인일 수 있으니 임의로 args/옵션을 추가/변경하지 말 것.

    플마 원본(naver_tracker.py L2320~2334)과 1:1:
      - launch args 3개: AutomationControlled 비활성화 / no-sandbox / 이미지 로딩 끔
      - context: viewport + 진짜 크롬 user_agent (locale/timezone/headers 미설정 — 플마도 안 함)
      - 모든 페이지: delete navigator.__proto__.webdriver (create_stealth_page)
    """
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",  # 자동화 탐지 끄기 (핵심)
            "--no-sandbox",
            "--blink-settings=imagesEnabled=false",  # 이미지 로딩 끔 (속도↑·부하↓)
        ],
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    return playwright, browser, context


async def create_stealth_page(context):
    """
    새 페이지를 만들고 navigator.webdriver를 삭제합니다.
    네이버는 navigator.webdriver=true이면 봇으로 판단 → 차단.
    모든 새 페이지에 반드시 이 함수를 사용할 것.
    """
    page = await context.new_page()
    await page.add_init_script("delete navigator.__proto__.webdriver;")
    return page


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
    empty = dict(
        place_id=None, address="", category="",
        menu_items=[], official_keywords=[], nearby_station="", keyword_list=[],
        visitor_reviews=None, blog_reviews=None, star_score=None,
        photo_count=None, latest_review_date=None,
        review_activity=None, recent_30d_reviews=None,
    )
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
            const stM = bodyText2.match(/([가-힣]{2,8}역)[\\s]*(?:\\d+번[\\s]*출구|에서[\\s]*(?:도보|차로|차량|\\d))/);
            if (stM) nearbyStation = stM[1];
            if (!nearbyStation) {
                const stM2 = bodyText2.match(/([가-힣]{2,8}역)\\s*(?:도보|방향|하차|인근|근처)/);
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

        # ── 리뷰·별점·사진수 추출 (JSON + DOM 텍스트 이중 탐색) ──────────────────
        review_data = await page.evaluate('''() => {
            const html = document.documentElement.innerHTML;
            const bodyText = (document.body && document.body.innerText) ? document.body.innerText : "";

            function mNum(pats) {
                for (const p of pats) {
                    const m = html.match(p);
                    if (m) { const n = parseInt((m[1] || "").replace(/[,\\s]/g, "")); if (!isNaN(n) && n >= 0) return n; }
                }
                return null;
            }
            function mFloat(pats) {
                for (const p of pats) {
                    const m = html.match(p);
                    if (m) { const f = parseFloat(m[1]); if (!isNaN(f)) return f; }
                }
                return null;
            }
            function mStr(pats) {
                for (const p of pats) {
                    const m = html.match(p);
                    if (m) return m[1];
                }
                return null;
            }
            function domNum(patterns) {
                for (const p of patterns) {
                    const m = bodyText.match(p);
                    if (m) { const n = parseInt((m[1] || "").replace(/,/g, "")); if (!isNaN(n) && n >= 0) return n; }
                }
                return null;
            }
            function domFloat(patterns) {
                for (const p of patterns) {
                    const m = bodyText.match(p);
                    if (m) { const f = parseFloat(m[1]); if (!isNaN(f) && f >= 1 && f <= 5) return f; }
                }
                return null;
            }

            // 방문자 리뷰: JSON 우선, DOM 텍스트·Apollo state 폴백
            let visitorReviews = mNum([
                /"visitorReviewCount"\\s*:\\s*(\\d+)/,
                /"visitorReviewsTotal"\\s*:\\s*(\\d+)/,
                /"reviewCount"\\s*:\\s*(\\d+)/,
                /"visitor_review_count"\\s*:\\s*(\\d+)/,
                /"totalReviewCount"\\s*:\\s*(\\d+)/,
                /"placeReviewCount"\\s*:\\s*(\\d+)/
            ]) ?? domNum([/방문자\\s*리뷰?\\s*([\\d,]+)/, /방문자\\s*([\\d,]+)\\s*개/]);
            if (visitorReviews === null || visitorReviews === undefined) {
                try {
                    const _st = window.__APOLLO_STATE__;
                    if (_st) {
                        for (const _v of Object.values(_st)) {
                            if (_v && typeof _v === 'object' && typeof _v.visitorReviewCount === 'number' && _v.visitorReviewCount > 0) {
                                visitorReviews = _v.visitorReviewCount; break;
                            }
                        }
                    }
                } catch(e) {}
            }

            // 블로그 리뷰: JSON + DOM
            const blogReviews =
                mNum([/"cafeBlogReviewsTotal"\\s*:\\s*(\\d+)/, /"blogCafeReviewCount"\\s*:\\s*(\\d+)/, /"blogReviewCount"\\s*:\\s*(\\d+)/]) ||
                domNum([/블로그\\s*리뷰?\\s*([\\d,]+)/, /블로그([\\d,]+)/, /blog[\\s:]*([\\d,]+)/i]);

            // 별점: DOM 텍스트 우선 (1.0~5.0 범위 숫자)
            const starScore =
                domFloat([/([1-5]\\.[0-9]{1,2})\\s*(?:\\/|점|★)/, /평점\\s*([1-5]\\.[0-9]{1,2})/, /별점\\s*([1-5]\\.[0-9]{1,2})/]) ||
                mFloat([/"starScoreAvg"\\s*:\\s*"?([\\d.]+)"?/, /"starScore"\\s*:\\s*"?([\\d.]+)"?/, /"ratingScore"\\s*:\\s*"?([\\d.]+)"?/]);

            // 사진 수: DOM 텍스트 우선
            const photoCount =
                domNum([/사진\\s*([\\d,]+)/, /포토\\s*([\\d,]+)/]) ||
                mNum([/"representativePhotoCount"\\s*:\\s*(\\d+)/, /"photoCount"\\s*:\\s*(\\d+)/, /"imageCount"\\s*:\\s*(\\d+)/]);

            // 최근 리뷰 날짜: JSON 패턴 (리뷰 탭 차단 시에도 메인에서 추출 시도)
            let latestReview = mStr([
                /"latestVisitorReviewDate"\\s*:\\s*"(\\d{4}[.\\-\\/]\\d{2}[.\\-\\/]\\d{2})"/,
                /"latestReviewDate"\\s*:\\s*"(\\d{4}[.\\-\\/]\\d{2}[.\\-\\/]\\d{2})"/,
                /"recentReviewDate"\\s*:\\s*"(\\d{4}[.\\-\\/]\\d{2}[.\\-\\/]\\d{2})"/,
                /"created"\\s*:\\s*"(20[12]\\d[.\\-\\/]\\d{2}[.\\-\\/]\\d{2})"/
            ]);
            // B단계: 메인페이지 "date" 전역 스캔 제거 — 추천/고정 리뷰의 옛 날짜를
            // 최신 리뷰로 잘못 잡아 최근활동 점수를 왜곡시켰음(예: 7개월 전 날짜).
            // 최신 리뷰 날짜는 아래 리뷰 탭(최신순 맨 위)에서만 신뢰해 가져온다.
            return { visitorReviews, blogReviews, starScore, photoCount, latestReview };
        }''')
        visitor_reviews  = review_data.get("visitorReviews")
        blog_reviews     = review_data.get("blogReviews")
        star_score       = review_data.get("starScore")
        photo_count      = review_data.get("photoCount")
        # B단계: 메인페이지 날짜는 신뢰도 낮아 미사용. 최신 리뷰 날짜는 리뷰 탭(최신순 맨 위)에서만
        # 설정한다. 리뷰 탭이 차단/실패하면 None으로 둔다(거짓 옛 날짜보다 "수집 실패"가 정확).
        latest_review_date = None

        # 블로그리뷰·사진수·별점이 없으면 모바일 페이지에서 보완
        if (blog_reviews is None or photo_count is None or star_score is None) and p_id:
            try:
                mob_url = f"https://m.place.naver.com/place/{p_id}/home"
                await page.goto(mob_url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2000)
                mob_data = await page.evaluate(r'''() => {
                    const t = document.body ? document.body.innerText : "";
                    function domNum(pats) {
                        for (const p of pats) {
                            const m = t.match(p);
                            if (m) { const n = parseInt((m[1]||"").replace(/,/g,"")); if(!isNaN(n) && n>=0) return n; }
                        }
                        return null;
                    }
                    function domFloat(pats) {
                        for (const p of pats) {
                            const m = t.match(p);
                            if (m) { const f = parseFloat(m[1]); if(!isNaN(f) && f>=1.0 && f<=5.0) return f; }
                        }
                        return null;
                    }
                    return {
                        blog:  domNum([/블로그\s*리뷰[^\d]*([\d,]+)/, /블로그[^\d]+([\d,]+)/]),
                        photo: domNum([/사진[^\d]*([\d,]+)/, /포토[^\d]*([\d,]+)/]),
                        star:  domFloat([/([1-5]\.[0-9]{1,2})\s*(?:\/|점|★)/, /별점[^\d]*([1-5]\.[0-9]{1,2})/, /평점[^\d]*([1-5]\.[0-9]{1,2})/]),
                    };
                }''')
                if blog_reviews  is None: blog_reviews  = mob_data.get("blog")
                if photo_count   is None: photo_count   = mob_data.get("photo")
                if star_score    is None: star_score    = mob_data.get("star")
            except Exception:
                pass

        # 방문자 리뷰 탭(최근순) → 처음 로드되는 약 10개 리뷰만 사용. 더보기 클릭 없음
        # (더보기는 3~2월 오래된 리뷰까지 끌어와 활발도를 왜곡 + 차단 위험 → 제거).
        # 리뷰 페이지 요청은 진단당 정확히 1회.
        #   처음 ~10개 중 최근 30일 이내 개수: 6+ 활발 / 3~5 보통 / 1~2 한산 / 0 거의 없음
        review_activity    = None   # "활발" | "보통" | "한산" | "거의 없음"
        recent_30d_reviews = None   # 처음 ~10개 중 30일 이내 개수
        if p_id:
            from datetime import date as _date, timedelta as _td
            _today = _date.today()
            try:
                mob_rev_url = f"https://m.place.naver.com/place/{p_id}/review/visitor?reviewSort=recent"
                await page.goto(mob_rev_url, wait_until="domcontentloaded", timeout=12000)
                await page.wait_for_timeout(2500)

                _JS_DAYS = r'''() => {
                    function parseTxt(txt) {
                        if (!txt) return null;
                        if (/방금|오늘/.test(txt)) return 0;
                        if (/어제/.test(txt)) return 1;
                        let m;
                        if (m = txt.match(/(\d{1,2})\s*분\s*전/))          return 0;
                        if (m = txt.match(/(\d{1,2})\s*시간\s*전/))         return 0;
                        if (m = txt.match(/(\d{1,3})\s*일\s*전/))          return parseInt(m[1]);
                        if (m = txt.match(/(\d{1,2})\s*주\s*전/))          return parseInt(m[1]) * 7;
                        if (m = txt.match(/(\d{1,2})\s*(?:개월|달)\s*전/)) return parseInt(m[1]) * 30;
                        if (m = txt.match(/(20\d{2})[.\-\/](\d{1,2})[.\-\/](\d{1,2})/)) {
                            const d = new Date(parseInt(m[1]), parseInt(m[2])-1, parseInt(m[3]));
                            return Math.floor((Date.now()-d)/86400000);
                        }
                        if (m = txt.match(/^(\d{1,2})\.(\d{1,2})$/)) {
                            const mo = parseInt(m[1]), dy = parseInt(m[2]);
                            if (mo >= 1 && mo <= 12 && dy >= 1 && dy <= 31) {
                                const now = new Date();
                                let d = new Date(now.getFullYear(), mo-1, dy);
                                let diff = Math.floor((Date.now()-d)/86400000);
                                if (diff < 0) { d = new Date(now.getFullYear()-1, mo-1, dy); diff = Math.floor((Date.now()-d)/86400000); }
                                if (diff >= 0) return diff;
                            }
                        }
                        return null;
                    }
                    // 리뷰 카드(li)를 직접 순회해 각 카드의 날짜 하나씩 추출 — innerText의
                    // "화면에 보이는 것만" 제약을 피해 로드된 리뷰를 빠짐없이 센다.
                    const out = [];
                    for (const li of document.querySelectorAll('li')) {
                        const t = (li.textContent || '').trim();
                        if (!t) continue;
                        const d = parseTxt(t);
                        if (d !== null && d >= 0 && d <= 4000) out.push(d);
                    }
                    // 폴백: li에서 못 찾으면 페이지 전체 텍스트 줄 단위 파싱
                    if (out.length < 3) {
                        const lines = (document.body ? document.body.innerText : '').split(/\n+/).map(l => l.trim());
                        for (const line of lines) {
                            const d = parseTxt(line);
                            if (d !== null && d >= 0 && d <= 4000) out.push(d);
                        }
                    }
                    return out;
                }'''

                review_days = await page.evaluate(_JS_DAYS)

                if review_days:
                    recent10 = sorted(review_days)[:10]          # 최근(작은 일수) 10개
                    recent_30d_reviews = sum(1 for d in recent10 if d <= 30)
                    c = recent_30d_reviews
                    review_activity = ("활발" if c >= 6 else "보통" if c >= 3
                                       else "한산" if c >= 1 else "거의 없음")
                    logger.info(
                        f"  리뷰활동: 처음 {len(recent10)}개 중 30일이내 {c}개 → {review_activity} "
                        f"(경과일 샘플 {recent10})"
                    )
                    # 리뷰 탭 최신순 맨 위(가장 최근) = min(경과일). latest_review_date의 유일 신뢰 소스.
                    d = _today - _td(days=min(review_days))
                    latest_review_date = d.strftime("%Y.%m.%d")
            except Exception:
                pass

        logger.info(
            f"리뷰: 방문자 {visitor_reviews} / 블로그 {blog_reviews} | "
            f"별점: {star_score} | 사진: {photo_count} | 최근리뷰: {latest_review_date} | "
            f"리뷰활동: {review_activity}(30일 {recent_30d_reviews}개)"
        )

        return dict(
            place_id=p_id,
            address=address_full,
            category=details["category"],
            menu_items=menu_items,
            official_keywords=official_keywords,
            nearby_station=nearby_station,
            keyword_list=keyword_list,
            visitor_reviews=visitor_reviews,
            blog_reviews=blog_reviews,
            star_score=star_score,
            photo_count=photo_count,
            latest_review_date=latest_review_date,
            review_activity=review_activity,
            recent_30d_reviews=recent_30d_reviews,
        )
    except Exception as e:
        logger.warning(f"정보 수집 실패: {e}")
        return empty


# ── 플레이스 순위 검색 (내부 헬퍼) ──────────────────────────────────────────

_PLACE_RANK_JS = '''() => {
    const ranked_ids = [], all_ids = [];
    const seenR = new Set(), seenA = new Set();
    const pats = [
        /(?:place|restaurant|accommodation|hairshop|clinic|nail|gym|cafe|map|pcmap|store|outlink|entry\\/place|local\\/naver_place|pinId|beauty|spa|wellness)\\/(\\d{8,11})/i,
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
}'''

_BUSINESSES_TOTAL_JS = '''() => {
    function scanState(st) {
        if (!st || typeof st !== 'object') return null;
        // ROOT_QUERY 기반 탐색 (placeList / nluPlace / searchPlace 등 다양한 키)
        const rq = st['ROOT_QUERY'] || st;
        try {
            for (const k of Object.keys(rq)) {
                let pl = rq[k];
                if (pl && pl.__ref) pl = st[pl.__ref];
                if (!pl || typeof pl !== 'object') continue;
                let biz = pl.businesses;
                if (biz && biz.__ref) biz = st[biz.__ref];
                if (biz && typeof biz.total === 'number') return biz.total;
            }
        } catch(e) {}
        // 전체 값 순회 폴백
        try {
            for (const v of Object.values(st)) {
                if (!v || typeof v !== 'object') continue;
                if (typeof v.total === 'number' && v.total > 5 &&
                    String(v.__typename||'').toLowerCase().includes('list')) return v.total;
                let biz = v.businesses;
                if (!biz) continue;
                if (biz.__ref) biz = st[biz.__ref];
                if (biz && typeof biz.total === 'number') return biz.total;
            }
        } catch(e) {}
        return null;
    }
    // 주요 전역 상태 컨테이너 시도
    for (const key of ['__APOLLO_STATE__','__PLACE_STATE__','__INITIAL_STATE__']) {
        try { const r = scanState(window[key]); if (r) return r; } catch(e) {}
    }
    // 인라인 스크립트 태그에서 직접 패턴 추출
    try {
        for (const s of document.querySelectorAll('script:not([src])')) {
            const t = s.textContent || '';
            if (!t.includes('total') || !t.match(/[Bb]usiness/)) continue;
            let m;
            m = t.match(/"total"\\s*:\\s*(\\d{3,6})\\s*,\\s*"__typename"\\s*:\\s*"[A-Z][A-Za-z]*List"/);
            if (m) return parseInt(m[1]);
            m = t.match(/"__typename"\\s*:\\s*"[A-Z][A-Za-z]*List"[^}]{0,60}"total"\\s*:\\s*(\\d{3,6})/);
            if (m) return parseInt(m[1]);
        }
    } catch(e) {}
    return null;
}'''

_SCROLL_JS = '''() => {
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


async def _fetch_place_ranking(page, keyword, safe_mode=True):
    """
    네이버 플레이스 검색 결과에서 ranked_ids 리스트를 반환합니다 (1페이지 기준).
    check_place_rank / find_competitor 양쪽에서 공유하는 핵심 로직.
    """
    encoded_kw = urllib.parse.quote(keyword)
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

    # 플마 동일: 12회 스크롤 (순위 6+ 안정 로드, 모바일 폴백 최소화)
    for _ in range(12):
        await page.evaluate(_SCROLL_JS)
        await page.wait_for_timeout(350)

    raw = await page.evaluate(_PLACE_RANK_JS)
    ranked_ids = raw.get('ranked_ids', [])
    names = raw.get('names', {})  # 보통 {} (검색카드 이름수집은 불안정해 미사용 — 경쟁사 이름은 _fetch_place_name 폴백)

    # 등록업체 총수 수집 — 메인 페이지 → iframe 프레임 순으로 시도
    businesses_total = None
    try:
        businesses_total = await page.evaluate(_BUSINESSES_TOTAL_JS)
        if businesses_total:
            logger.debug(f"  [{keyword}] businesses.total={businesses_total}")
    except Exception:
        pass
    if not businesses_total:
        try:
            for frame in page.frames:
                if businesses_total:
                    break
                try:
                    bt = await asyncio.wait_for(frame.evaluate(_BUSINESSES_TOTAL_JS), timeout=1.5)
                    if bt:
                        businesses_total = bt
                        logger.debug(f"  [{keyword}] frame businesses.total={bt}")
                except Exception:
                    pass
        except Exception:
            pass

    # Q단계: map.naver businesses_total 폴백 제거 (키워드당 goto+2.5초 ≈ 6초, 거의 매번 발동했지만
    # 결과도 자주 None이고 점수에는 전혀 안 씀 = 등급 뱃지 전용). search.naver 1차 시도만 유지.
    # businesses_total None이면 그 키워드는 등급 뱃지 없음(graceful) — 점수·순위·경쟁사 선정 영향 없음.

    # 데스크탑 결과 없을 때만 모바일 재시도
    if not ranked_ids:
        p_url_m = f"https://m.search.naver.com/search.naver?query={encoded_kw}&where=m_place"
        await page.goto(p_url_m, wait_until="domcontentloaded", timeout=10000)
        await page.wait_for_timeout(600)
        for _ in range(8):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(250)
        ranked_ids = await page.evaluate('''() => {
            const ids = [], seen = new Set();
            const pats = [
                /(?:place|restaurant|accommodation|hairshop|clinic|nail|gym|cafe|map|entry\\/place|local\\/naver_place)\\/(\\d{8,11})/i,
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

    return ranked_ids, p_url, businesses_total, names


async def check_place_rank(page, keyword, place_id, safe_mode=True):
    """
    네이버 플레이스에서 keyword 검색 시 place_id 매장의 순위를 반환합니다.

    Returns:
        (rank, businesses_total, first_id, first_name)
        - rank: 내 순위 1~30위, 30위 밖/미발견이면 None
        - businesses_total: 경쟁업체 총수 (등급 산출용)
        - first_id/first_name: 이 키워드 검색 1위 매장의 place_id·이름 (P단계 경쟁사 비교용,
          검색결과에서 같이 수집 → 추가 요청 0). 이름 미수집 시 first_name=None.
    """
    if not place_id:
        logger.warning(f"[{keyword}] place_id 없음")
        return None, None, None, None

    logger.info(f"  검색: '{keyword}'")
    p_ids, p_url, bt, names = await _fetch_place_ranking(page, keyword, safe_mode)
    # 등록업체수(businesses_total) 실제 수집값을 키워드별로 명시 — None이면 수집 실패
    logger.info(f"  [등록업체수] '{keyword}' → businesses_total: {bt}")
    # P단계: 이 키워드의 1위 매장 (검색결과 맨 위) — 경쟁사 비교용
    first_id   = p_ids[0] if p_ids else None
    first_name = names.get(first_id) if first_id else None

    # 2페이지 폴백 (1페이지에 없을 때)
    if place_id and place_id not in p_ids:
        try:
            _p2_url = p_url + "&start=16"
            await page.goto(_p2_url, wait_until="domcontentloaded", timeout=12000)
            await page.wait_for_timeout(800)
            for _ in range(10):
                await page.evaluate(_SCROLL_JS)
                await page.wait_for_timeout(350)
            _p2_ids = await page.evaluate(_PLACE_RANK_JS)
            _p2_ids = _p2_ids.get('ranked_ids', [])
            logger.debug(f"  [{keyword}] 2페이지 {len(_p2_ids)}개, 감지={'O' if place_id in _p2_ids else 'X'}")
            if _p2_ids and place_id in _p2_ids:
                r2 = _p2_ids.index(place_id) + 16
                if r2 <= 30:
                    logger.info(f"  [{keyword}] 2페이지 {r2}위 검출")
                    return r2, bt, first_id, first_name
        except Exception as fe:
            logger.warning(f"  [{keyword}] 2페이지 폴백 오류: {fe}")

    logger.debug(f"  [{keyword}] p_ids={len(p_ids)}개, 감지={'O' if place_id in p_ids else 'X'}")
    if place_id in p_ids:
        rank_num = p_ids.index(place_id) + 1
        if rank_num <= 30:
            logger.info(f"  [{keyword}] → {rank_num}위")
            return rank_num, bt, first_id, first_name
    return None, bt, first_id, first_name


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
    # 레이트리밋 방어: 워커별 딥스캔(inspect_blog_post) 사이 지터 딜레이.
    # 카드 즉시감지·url_cache 히트(네트워크 없음)에는 걸지 않고,
    # 실제 블로그 본문 요청(딥스캔)에만 첫 건 제외 후 적용한다.
    # N_BLOG=3 워커 × 키워드 폭 확대(최대 15) 환경에서 동시 요청 폭주를 완화.
    INSPECT_DELAY = (0.5, 1.2)  # (min, max) 초 — random.uniform
    did_inspect = False
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
                if did_inspect:
                    await asyncio.sleep(random.uniform(*INSPECT_DELAY))
                did_inspect = True
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


# ── 경쟁사 비교 (P단계) ───────────────────────────────────────────────────────
# 기존 find_competitor(경쟁사 get_store_details 전체 크롤 → 504 유발)는 제거.
# 대신 내 매장 키워드 검색 결과를 재사용해 가볍게 비교한다.

def _calc_grades(place_results: list) -> dict:
    """프론트 calcGrades와 동일 규칙: businesses_total 상대 백분율로 키워드 등급(S/A/B/C).

    businesses_total이 클수록(경쟁업체 많을수록) 가치 높은 키워드 → 상위 등급.
    Returns {keyword: 'S'|'A'|'B'|'C'}. businesses_total None인 키워드는 등급 없음.
    """
    valid = [r for r in place_results if r.get("businesses_total") is not None]
    if not valid:
        return {}
    valid = sorted(valid, key=lambda r: r["businesses_total"], reverse=True)
    n = len(valid)
    grades = {}
    for i, r in enumerate(valid):
        pct = i / (n - 1) if n > 1 else 0
        if i == 0 or pct < 0.10:
            g = "S"
        elif pct < 0.35:
            g = "A"
        elif pct < 0.70:
            g = "B"
        else:
            g = "C"
        grades[r["keyword"]] = g
    return grades


def _build_competitor_compare(place_results: list, my_place_id: str) -> dict:
    """P단계 경쟁사 비교 데이터.

    - S급 우선 → A급, 그 키워드에서 내가 1위가 아닌 것 중 상위(S먼저, businesses_total 큰 순) 최대 3개.
    - 각 카드: 키워드/등급/내 순위/1위 매장 이름·id/격차(계단). 1위 매장 이름은 검색결과에서 수집.

    status:
      'ok'        → cards 있음
      'no_sa'     → S/A급 키워드 자체가 없음 (상위 노출 키워드 부재)
      'all_first' → S/A급 키워드가 있으나 전부 내가 1위 (칭찬)
    """
    grades = _calc_grades(place_results)
    rmap = {r["keyword"]: r for r in place_results}
    sa = [kw for kw in grades if grades[kw] in ("S", "A")]
    if not sa:
        return {"status": "no_sa", "cards": [], "first_place_keywords": []}

    first_place = [kw for kw in sa if rmap.get(kw, {}).get("rank") == 1]
    # 비교 후보: S/A이면서 내가 1위가 아닌 키워드 (rank != 1; rank None=순위권 밖도 포함)
    cand = [kw for kw in sa if rmap.get(kw, {}).get("rank") != 1]
    if not cand:
        return {"status": "all_first", "cards": [], "first_place_keywords": first_place}

    grade_order = {"S": 0, "A": 1}
    cand.sort(key=lambda kw: (grade_order.get(grades[kw], 9),
                              -(rmap[kw].get("businesses_total") or 0)))
    cards = []
    for kw in cand[:3]:
        r = rmap[kw]
        my_rank = r.get("rank")
        cards.append({
            "keyword":         kw,
            "grade":           grades[kw],
            "my_rank":         my_rank,
            "competitor_id":   r.get("first_id"),
            "competitor_name": r.get("first_name") or None,
            "competitor_rank": 1,
            "gap":             (my_rank - 1) if my_rank else None,
        })
    logger.info(f"  [경쟁사 비교] S/A {len(sa)}개 중 비교 {len(cards)}개: "
                f"{[(c['keyword'], c['grade'], c['my_rank']) for c in cards]}")
    return {"status": "ok", "cards": cards, "first_place_keywords": first_place}


async def _fetch_place_name(page, place_id: str) -> str | None:
    """place_id의 매장명만 가볍게 가져온다 (map entry 타이틀). 리뷰/키워드 등 무거운 수집 없음.

    경쟁사 비교 카드의 1위 매장 이름용. 선정된 경쟁사(≤3개)에만 호출.
    map.naver만 사용(m.place 안 건드림 → 리뷰 차단과 무관).
    ※ map.naver 타이틀은 처음 '장소' 플레이스홀더 → JS 로드 후 "{매장명} - 네이버지도"로 갱신되므로
       갱신될 때까지 대기한 뒤 읽는다.
    """
    try:
        await page.goto(f"https://map.naver.com/p/entry/place/{place_id}",
                        wait_until="domcontentloaded", timeout=10000)
        try:
            await page.wait_for_function(
                "() => { const t = document.title || ''; "
                "const n = t.split(' - 네이버')[0].trim(); "
                "return t.includes('네이버지도') && n && n !== '장소' && n.length >= 2; }",
                timeout=6000,
            )
        except Exception:
            await page.wait_for_timeout(1500)
        title = (await page.title()) or ""
        # "백세돼지국밥 - 네이버지도" / "백세돼지국밥 : 네이버" 형태 → 앞부분만
        name = title.split(" - 네이버")[0].split(" : ")[0].split(" - ")[0].strip()
        if name and name not in ("장소", "네이버지도", "네이버 지도") and "네이버" not in name and len(name) <= 40:
            return name
    except Exception:
        pass
    return None


# ── 매장 정보만 간단히 가져오기 (블로그 단독 분석용) ─────────────────────────────

async def fetch_store_info_only(place_url: str) -> dict:
    """
    매장 기본 정보만 크롤링하여 반환합니다 (블로그 단독 분석용).
    """
    playwright, browser, context = await create_browser()
    try:
        page = await create_stealth_page(context)
        details = await get_store_details(page, place_url)
        return details
    finally:
        await browser.close()
        await playwright.stop()


# ── 통합 진단 래퍼 ────────────────────────────────────────────────────────────

async def diagnose_store(store_name: str, place_url: str = None, keywords: list = None,
                         ad_flags: dict = None) -> dict:
    """
    매장 정보를 받아 플레이스 순위 + 경쟁사 비교 + 4축 점수를 한 덩어리로 반환합니다.

    Args:
        store_name: 매장명
        place_url:  네이버 플레이스 URL (없으면 키워드만 생성)
        keywords:   직접 지정 키워드 목록 (없으면 자동 생성)

    Returns:
        {
          "store_name", "place_id", "address", "category",
          "visitor_reviews", "blog_reviews", "star_score", "photo_count", "latest_review_date",
          "keywords_used", "place_results",
          "competitor": { status, cards:[{keyword,grade,my_rank,competitor_name,competitor_rank,gap}], first_place_keywords },
          "scores":     { seo, content, activity, ad, total, detail },
        }
    """
    N_WORKERS  = 5   # 플마 동일 (N_PLACE=5). 6 테스트=2 vCPU 포화로 동일 시간→5 유지
    MAX_KW     = 30  # 상위 우선순위 키워드 (역·동 두 지역 키워드 모두 포함)

    _t0 = time.perf_counter()  # ⏱ Q단계 타이밍: 전체 시작
    playwright, browser, context = await create_browser()
    try:
        # 모든 페이지에 navigator.webdriver 삭제 적용 (차단 우회 핵심)
        detail_page = await create_stealth_page(context)
        _t_browser = time.perf_counter()  # ⏱ 브라우저 기동 완료

        # ── 우리 매장 상세정보 ──────────────────────────────────────────────
        details = {}
        if place_url:
            details = await get_store_details(detail_page, place_url)
        _t_detail = time.perf_counter()  # ⏱ get_store_details 완료
        logger.info(f"⏱ [타이밍] 브라우저 기동: {_t_browser - _t0:.1f}s | "
                    f"get_store_details(우리매장): {_t_detail - _t_browser:.1f}s")

        place_id          = details.get("place_id")
        address           = details.get("address", "")
        category          = details.get("category", "")
        menu_items        = details.get("menu_items", [])
        official_keywords = details.get("official_keywords", [])
        nearby_station    = details.get("nearby_station", "")
        keyword_list      = details.get("keyword_list", [])

        # ── 키워드 생성 입력값 로그 (디버깅용) ──────────────────────────────
        logger.info("=" * 60)
        logger.info("[키워드 생성 입력값]")
        logger.info(f"  store_name: {store_name}")
        logger.info(f"  category: {category}")
        logger.info(f"  address: {address}")
        logger.info(f"  official_keywords: {official_keywords}")
        logger.info(f"  menu_items: {menu_items}")
        logger.info(f"  nearby_station: {nearby_station}")
        logger.info(f"  keyword_list: {keyword_list}")
        logger.info("=" * 60)

        if keywords:
            target_keywords = keywords[:MAX_KW]
        else:
            # generate_keywords는 이미 우선순위순(keywordList > 역 > 동 > 구) 정렬
            target_keywords = generate_keywords(
                store_name=store_name,
                category=category,
                address=address,
                menu_items=menu_items,
                official_keywords=official_keywords,
                nearby_station=nearby_station,
                keyword_list=keyword_list,
            )[:MAX_KW]

        # ── 생성된 키워드 목록 로그 ──────────────────────────────────────────
        logger.info(f"[생성된 키워드 목록] ({len(target_keywords)}개)")
        for i, kw in enumerate(target_keywords, 1):
            logger.info(f"  {i:2d}. {kw}")
        logger.info("=" * 60)

        # ── 병렬 키워드 순위 검색 ─────────────────────────────────────────
        # P단계: 경쟁사 비교는 이 키워드 검색 결과를 재사용한다(추가 요청 0 = 속도 개선).
        # 기존엔 경쟁사에 get_store_details(주소·키워드·리뷰·m.place 리뷰탭) 전체 크롤 →
        # 504 유발. 이제 검색결과에서 1위 매장 id·이름만 같이 받아 가볍게 비교.
        # 모든 페이지에 navigator.webdriver 삭제 적용 (차단 우회 핵심)
        search_pages = [await create_stealth_page(context) for _ in range(N_WORKERS)]
        page_pool: asyncio.Queue = asyncio.Queue()
        for p in search_pages:
            await page_pool.put(p)

        async def _rank_task(kw: str) -> dict:
            pg = await page_pool.get()
            try:
                r, bt, first_id, first_name = await check_place_rank(pg, kw, place_id)
                return {"keyword": kw, "rank": r, "businesses_total": bt,
                        "first_id": first_id, "first_name": first_name}
            finally:
                await page_pool.put(pg)

        # 키워드 순위 동시 검색 (경쟁사 비교는 이 결과 재사용 → 별도 탐색 없음)
        _t_rank_start = time.perf_counter()  # ⏱ 랭킹 시작
        place_results = list(await asyncio.gather(*[_rank_task(kw) for kw in target_keywords]))
        _t_rank = time.perf_counter()  # ⏱ 랭킹 완료
        logger.info(f"⏱ [타이밍] 랭킹 {len(target_keywords)}개(병렬 {N_WORKERS}): "
                    f"{_t_rank - _t_rank_start:.1f}s "
                    f"(키워드당 평균 {(_t_rank - _t_rank_start) / max(1, len(target_keywords)):.1f}s)")

        # ── 경쟁사 비교 (P단계): S/A급 + 내가 1위 아닌 키워드 상위 최대 3개 ──────
        competitor = _build_competitor_compare(place_results, place_id)

        # 1위 매장 이름이 검색결과에서 안 잡힌 카드만, 가벼운 title fetch 폴백(≤3, 리뷰/키워드 안 건드림)
        missing = [c for c in competitor.get("cards", [])
                   if not c.get("competitor_name") and c.get("competitor_id")]
        if missing:
            async def _name_task(card: dict):
                pg = await page_pool.get()
                try:
                    card["competitor_name"] = await _fetch_place_name(pg, card["competitor_id"])
                except Exception:
                    pass
                finally:
                    await page_pool.put(pg)
            await asyncio.gather(*[_name_task(c) for c in missing])
        _t_names = time.perf_counter()  # ⏱ 경쟁사 이름 완료
        logger.info(f"⏱ [타이밍] 경쟁사 이름 {len(missing)}개: {_t_names - _t_rank:.1f}s | "
                    f"★ 총 소요: {_t_names - _t0:.1f}s "
                    f"(브라우저 {_t_browser - _t0:.1f} + 상세 {_t_detail - _t_browser:.1f} "
                    f"+ 랭킹 {_t_rank - _t_rank_start:.1f} + 이름 {_t_names - _t_rank:.1f})")

        # ── 블로그 분석은 별도 API로 분리 (diagnose_store에서 제거) ─────────
        blog_results = []

        # ── 점수 계산 (경쟁사는 점수에 영향 없음 — competitor_data 미사용) ──────
        store_data_for_score = {
            **details,
            "place_results": place_results,
        }
        scores = calculate_scores(store_data_for_score, ad_flags=ad_flags)

        return {
            "store_name":         store_name,
            "place_id":           place_id,
            "address":            address,
            "category":           category,
            "visitor_reviews":    details.get("visitor_reviews"),
            "blog_reviews":       details.get("blog_reviews"),
            "star_score":         details.get("star_score"),
            "photo_count":        details.get("photo_count"),
            "latest_review_date": details.get("latest_review_date"),
            "review_activity":    details.get("review_activity"),
            "recent_30d_reviews": details.get("recent_30d_reviews"),
            "keywords_used":      target_keywords,
            "place_results":      place_results,
            "blog_results":       blog_results,  # 블로그 분석 결과 (키워드별 우리 블로그 순위)
            "competitor":         competitor,
            "scores":             scores,
            "ad_flags":           ad_flags or {},
        }
    finally:
        await close_browser(playwright, browser)


# ── R단계: SSE 스트리밍용 진단 함수 ─────────────────────────────────────────
async def diagnose_store_stream(
    store_name: str,
    place_url: str = "",
    ad_flags: dict | None = None,
    keywords: list[str] | None = None,
):
    """
    diagnose_store의 SSE 스트리밍 버전.
    키워드 순위가 나올 때마다 yield로 즉시 전송.

    Yields:
        dict: {"type": "started"|"keyword"|"complete", ...}
    """
    N_WORKERS = 5   # 플마 동일. 라이브 SSE 경로 (6 테스트=2 vCPU 포화로 동일 시간→5 유지)
    MAX_KW = 30

    _t0 = time.perf_counter()
    playwright, browser, context = await create_browser()
    try:
        detail_page = await create_stealth_page(context)

        # ── 우리 매장 상세정보 ──
        details = {}
        if place_url:
            details = await get_store_details(detail_page, place_url)

        place_id = details.get("place_id")
        address = details.get("address", "")
        category = details.get("category", "")
        menu_items = details.get("menu_items", [])
        official_keywords = details.get("official_keywords", [])
        nearby_station = details.get("nearby_station", "")
        keyword_list = details.get("keyword_list", [])

        if keywords:
            target_keywords = keywords[:MAX_KW]
        else:
            target_keywords = generate_keywords(
                store_name=store_name,
                category=category,
                address=address,
                menu_items=menu_items,
                official_keywords=official_keywords,
                nearby_station=nearby_station,
                keyword_list=keyword_list,
            )[:MAX_KW]

        # ⭐ started 이벤트 즉시 전송 (504 방지 핵심)
        yield {
            "type": "started",
            "total_keywords": len(target_keywords),
            "store_name": store_name,
            "place_id": place_id,
            "category": category,
            "address": address,
        }

        # ── 병렬 키워드 순위 검색 (하나씩 yield) ──
        search_pages = [await create_stealth_page(context) for _ in range(N_WORKERS)]
        page_pool: asyncio.Queue = asyncio.Queue()
        for p in search_pages:
            await page_pool.put(p)

        place_results = []
        results_queue: asyncio.Queue = asyncio.Queue()

        async def _rank_task(kw: str, idx: int):
            pg = await page_pool.get()
            try:
                r, bt, first_id, first_name = await check_place_rank(pg, kw, place_id)
                result = {"keyword": kw, "rank": r, "businesses_total": bt,
                          "first_id": first_id, "first_name": first_name}
                await results_queue.put((idx, result))
            except Exception as e:
                logger.warning(f"[{kw}] 순위 검색 실패: {e}")
                await results_queue.put((idx, {"keyword": kw, "rank": None, "businesses_total": None,
                                                "first_id": None, "first_name": None}))
            finally:
                await page_pool.put(pg)

        # 모든 태스크 시작
        tasks = [asyncio.create_task(_rank_task(kw, i)) for i, kw in enumerate(target_keywords)]

        # 결과가 나올 때마다 yield
        completed = 0
        total = len(target_keywords)
        results_map = {}

        while completed < total:
            idx, result = await results_queue.get()
            results_map[idx] = result
            completed += 1

            # 순위별 가상 점수 (게임 연출용)
            rank = result.get("rank")
            if rank is not None:
                if rank <= 3:
                    score_delta = 2
                elif rank <= 10:
                    score_delta = 1
                else:
                    score_delta = 0
            else:
                score_delta = 0

            yield {
                "type": "keyword",
                "keyword": result["keyword"],
                "rank": rank,
                "businesses_total": result.get("businesses_total"),
                "score_delta": score_delta,
                "progress": completed,
                "total": total,
            }

        # 태스크 완료 대기
        await asyncio.gather(*tasks)

        # 순서대로 정렬
        place_results = [results_map[i] for i in range(total)]

        # ── 경쟁사 비교 ──
        competitor = _build_competitor_compare(place_results, place_id)
        missing = [c for c in competitor.get("cards", [])
                   if not c.get("competitor_name") and c.get("competitor_id")]
        if missing:
            async def _name_task(card: dict):
                pg = await page_pool.get()
                try:
                    card["competitor_name"] = await _fetch_place_name(pg, card["competitor_id"])
                except Exception:
                    pass
                finally:
                    await page_pool.put(pg)
            await asyncio.gather(*[_name_task(c) for c in missing])

        blog_results = []

        # ── 점수 계산 ──
        store_data_for_score = {
            **details,
            "place_results": place_results,
        }
        scores = calculate_scores(store_data_for_score, ad_flags=ad_flags)

        _t_total = time.perf_counter() - _t0
        logger.info(f"⏱ [SSE 스트리밍] 총 소요: {_t_total:.1f}s")

        # ⭐ complete 이벤트 (최종 결과)
        yield {
            "type": "complete",
            "result": {
                "store_name": store_name,
                "place_id": place_id,
                "address": address,
                "category": category,
                "visitor_reviews": details.get("visitor_reviews"),
                "blog_reviews": details.get("blog_reviews"),
                "star_score": details.get("star_score"),
                "photo_count": details.get("photo_count"),
                "latest_review_date": details.get("latest_review_date"),
                "review_activity": details.get("review_activity"),
                "recent_30d_reviews": details.get("recent_30d_reviews"),
                "keywords_used": target_keywords,
                "place_results": place_results,
                "blog_results": blog_results,
                "competitor": competitor,
                "scores": scores,
                "ad_flags": ad_flags or {},
            }
        }
    finally:
        await close_browser(playwright, browser)


# ── 블로그 분석 전용 함수 (별도 호출) ────────────────────────────────────────

async def analyze_blog_ranking(
    store_name: str,
    place_id: str,
    address: str,
    keywords: list[str],
    max_keywords: int = 15,
) -> list[dict]:
    """
    블로그 순위 분석을 별도로 실행합니다.
    플레이스 진단 완료 후 사용자가 요청할 때만 호출.

    플마(naver_tracker.py) blog 모드와 동일하게 **병렬**로 처리한다:
      - N_BLOG=3 워커 풀(검색 페이지 3 + 딥스캔 페이지 3)
      - 키워드 사이 순차 딜레이 제거(플마도 없음) → 폭(키워드 수) 확보
      - url_cache 공유로 동일 블로그 중복 딥스캔 방지
      - check_blog_ranking_deep 내부에서 collect_blog_results를 호출하므로
        여기서 사전 collect는 하지 않음(중복 요청 제거 = 차단 위험↓)

    Args:
        store_name: 매장명
        place_id:   네이버 플레이스 ID
        address:    매장 주소
        keywords:   분석할 키워드 목록(대표키워드 우선)
        max_keywords: 최대 분석 키워드 수

    Returns:
        [{"keyword": str, "hits": [{"rank", "title", "blog_link", ...}]}]  (입력 순서 유지)
    """
    N_BLOG = 3  # 동시 블로그 처리 워커 수 (플마와 동일)

    if not place_id or not keywords:
        logger.warning("[블로그 분석] place_id 또는 keywords 없음")
        return []

    blog_keywords = keywords[:max_keywords]

    playwright, browser, context = await create_browser()
    try:
        url_cache: dict = {}
        search_pool: asyncio.Queue = asyncio.Queue()
        insp_pool: asyncio.Queue = asyncio.Queue()
        for _ in range(N_BLOG):
            search_pool.put_nowait(await create_stealth_page(context))
            insp_pool.put_nowait(await create_stealth_page(context))

        logger.info("=" * 60)
        logger.info(f"[블로그 분석 시작] {len(blog_keywords)}개 키워드 (병렬 {N_BLOG})")
        logger.info(f"  매장: {store_name} (place_id={place_id})")
        logger.info(f"  주소: {address}")
        logger.info(f"  키워드: {blog_keywords}")
        logger.info("=" * 60)

        async def _blog_task(idx: int, kw: str) -> tuple:
            pg = await search_pool.get()
            ip = await insp_pool.get()
            try:
                logger.info(f"  [{idx+1}/{len(blog_keywords)}] 블로그 검색: '{kw}'")
                hits = await check_blog_ranking_deep(
                    page=pg,
                    inspect_page=ip,
                    keyword=kw,
                    store_name=store_name,
                    place_id=place_id,
                    address=address,
                    url_cache=url_cache,
                    max_hits=5,   # 플마 blog 모드와 동일
                )
                matched = len([h for h in hits if h.get("rank")])
                logger.info(f"    → '{kw}' 매칭 {matched}개")
                for h in hits:
                    if h.get("rank"):
                        logger.info(f"       {h['rank']}위: {h.get('title','')[:30]} | {h.get('blog_link','')[:50]}")
                return (idx, kw, hits)
            except Exception as e:
                logger.error(f"  블로그 분석 오류({kw}): {e}")
                return (idx, kw, [{"status": f"오류: {str(e)[:40]}", "rank": None,
                                   "title": "", "blog_link": "", "score": 0, "reasons": [str(e)[:80]]}])
            finally:
                search_pool.put_nowait(pg)
                insp_pool.put_nowait(ip)

        raw = await asyncio.gather(*[_blog_task(i, kw) for i, kw in enumerate(blog_keywords)])
        raw_sorted = sorted(raw, key=lambda x: x[0])   # 입력 순서 유지
        blog_results = [{"keyword": kw, "hits": hits} for _, kw, hits in raw_sorted]

        total_matched = sum(len([h for h in r["hits"] if h.get("rank")]) for r in blog_results)
        logger.info("=" * 60)
        logger.info(f"[블로그 분석 완료] {len(blog_results)}개 키워드 / 총 매칭 {total_matched}개")
        logger.info("=" * 60)

        return blog_results

    finally:
        await close_browser(playwright, browser)
