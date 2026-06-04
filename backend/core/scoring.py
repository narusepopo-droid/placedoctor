"""
PlaceDoctor 4축 점수 계산 모듈

축 (화면 라벨):
  seo      → 검색노출(SEO)
  content  → 리뷰관리
  activity → 최근활동
  ad       → 키워드광고

내부 점수 키(seo/content/activity/ad)는 DB 컬럼·캐시와 묶여 있어 그대로 유지합니다.
가중치 및 임계값은 상수로 관리하므로 나중에 조정 가능합니다.
"""

from datetime import datetime, date

# ── 종합 가중치 ───────────────────────────────────────────────────────────────
# 광고 축은 체크박스 입력 기반(최대 60점)이라 비중을 15%로 제한.
SEO_WEIGHT      = 0.34
CONTENT_WEIGHT  = 0.30
ACTIVITY_WEIGHT = 0.21
AD_WEIGHT       = 0.15

# ── SEO 축 상수 ───────────────────────────────────────────────────────────────
# 키워드 순위 → 점수 (키워드 하나당)
RANK_SCORE_TABLE = [
    (1,  3,  20),   # 1~3위: 20점
    (4,  7,  12),   # 4~7위: 12점
    (8,  15,  6),   # 8~15위: 6점
    (16, 30,  2),   # 16~30위: 2점
]
SEO_MAX_KW_RAW    = 60   # 키워드 점수 만점 기준 (이 이상이면 키워드 점수 85점)
SEO_INFO_BONUS    = 15   # 주소+업종+메뉴 완성도 보너스 (최대)

# ── 리뷰관리(content) 축 상수 ────────────────────────────────────────────────
# 합산 방식: 방문자 리뷰 + 블로그 리뷰 + 별점을 각각 점수화 후 더함.
# 별점이 None인 매장은 별점 항목을 빼고 나머지(방문자+블로그)만으로 100점 환산.
CONTENT_VISITOR_MAX = 50   # 방문자 리뷰 만점 기여
CONTENT_BLOG_MAX    = 30   # 블로그 리뷰 만점 기여
CONTENT_STAR_MAX    = 20   # 별점 만점 기여
# (임계값 이상 → 만점 대비 비율)
VISITOR_REVIEW_FRACS = [(500, 1.0), (200, 0.85), (100, 0.65), (50, 0.45), (20, 0.28), (5, 0.12), (0, 0.0)]
BLOG_REVIEW_FRACS    = [(300, 1.0), (100, 0.80), (50, 0.60),  (20, 0.40), (5, 0.20),  (0, 0.0)]
STAR_FRACS           = [(4.7, 1.0), (4.5, 0.85), (4.0, 0.60), (3.5, 0.40), (3.0, 0.20), (0.0, 0.10)]

# ── 최근활동(activity) 축 상수 ───────────────────────────────────────────────
# 합산 방식: 최근 리뷰 날짜 + 최근 30일 방문자 리뷰수 + 정보 최신성.
# 최근 30일 리뷰수를 수집하지 못하면(None) 그 항목을 빼고 나머지로 100점 환산.
ACTIVITY_RECENCY_MAX  = 50   # 최근 리뷰 날짜 만점 기여
ACTIVITY_RECENT30_MAX = 35   # 최근 30일 리뷰수 만점 기여
ACTIVITY_INFO_MAX     = 15   # 정보 최신성 만점 기여
# 최근 리뷰 경과 일수 (이하 → 비율)
RECENCY_FRACS = [(7, 1.0), (30, 0.8), (90, 0.55), (180, 0.30), (365, 0.10), (9999, 0.0)]
# 최근 30일 방문자 리뷰 개수 (이상 → 비율)
RECENT30_FRACS = [(30, 1.0), (15, 0.8), (8, 0.6), (4, 0.4), (1, 0.2), (0, 0.0)]

# ── 키워드광고(ad) 축 상수 ───────────────────────────────────────────────────
# 자동 감지 제거 → 업주 체크박스 입력 기반. 어떤 경우도 "잘함"은 안 나옴(최적화 여지 항상 있음).
AD_NONE_SCORE = 20   # 다 미체크
AD_SOME_SCORE = 40   # 일부 체크
AD_ALL_SCORE  = 60   # 다 체크 (최대)
AD_FLAG_KEYS  = ["place", "powerlink", "local", "blog"]  # 플레이스/파워링크/지역소상공인/블로그체험단


# ── 내부 유틸 ─────────────────────────────────────────────────────────────────

def _frac_ge(value, fracs):
    """fracs = [(임계값, 비율), ...] 내림차순. value가 임계값 이상이면 해당 비율(0~1)."""
    if value is None:
        return 0.0
    for threshold, frac in fracs:
        if value >= threshold:
            return frac
    return 0.0


def _frac_le(value, fracs):
    """fracs = [(임계값, 비율), ...] 오름차순. value가 임계값 이하이면 해당 비율(0~1)."""
    if value is None:
        return 0.0
    for threshold, frac in fracs:
        if value <= threshold:
            return frac
    return 0.0


def _rank_score(rank):
    if rank is None:
        return 0
    for lo, hi, score in RANK_SCORE_TABLE:
        if lo <= rank <= hi:
            return score
    return 0


def _days_since(date_str):
    """'YYYY.MM.DD' 또는 'YYYY-MM-DD' 형식 문자열 → 오늘까지 경과 일수. 파싱 실패 시 None."""
    if not date_str:
        return None
    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            d = datetime.strptime(date_str[:10], fmt).date()
            return (date.today() - d).days
        except ValueError:
            continue
    return None


# ── 키워드광고 점수 (체크박스 입력) ─────────────────────────────────────────

def calculate_ad_score(ad_flags: dict | None) -> tuple[int, str]:
    """
    체크박스 입력값을 광고축 점수·라벨로 변환합니다.

    Args:
        ad_flags: {"place": bool, "powerlink": bool, "local": bool, "blog": bool}

    Returns:
        (score, label)
    """
    flags = ad_flags or {}
    checked = sum(1 for k in AD_FLAG_KEYS if flags.get(k))
    if checked == 0:
        return AD_NONE_SCORE, "미집행 · 기회"
    if checked >= len(AD_FLAG_KEYS):
        return AD_ALL_SCORE, "집행 중 · 최적화 부족"
    return AD_SOME_SCORE, "일부 집행 · 부족"


def calculate_total(seo: int, content: int, activity: int, ad: int) -> int:
    """4축 가중 평균 종합 점수."""
    return round(
        seo * SEO_WEIGHT + content * CONTENT_WEIGHT
        + activity * ACTIVITY_WEIGHT + ad * AD_WEIGHT
    )


def apply_ad_flags(scores: dict, ad_flags: dict | None) -> dict:
    """
    이미 계산된 scores dict에 체크박스 광고 점수를 반영하고 total을 재계산합니다.
    캐시된 결과(체크박스 입력 전 계산값)에도 적용 가능 — ad 입력은 캐시에 굳지 않습니다.
    """
    ad_score, ad_label = calculate_ad_score(ad_flags)
    scores["ad"] = ad_score
    scores["ad_label"] = ad_label
    scores["total"] = calculate_total(
        scores.get("seo", 0), scores.get("content", 0),
        scores.get("activity", 0), ad_score,
    )
    return scores


# ── 공개 API ──────────────────────────────────────────────────────────────────

def calculate_scores(store_data: dict, competitor_data: dict = None,
                     benchmark: dict = None, ad_flags: dict = None) -> dict:
    """
    4축 진단 점수를 계산합니다.

    Args:
        store_data:      diagnose_store() 반환값 (place_results, visitor_reviews 등 포함)
        competitor_data: find_competitor() 반환값. None이면 경쟁사 비교 없이 점수만 계산.
        benchmark:       업종 평균값 dict. 현재는 자리만 유지 — None이면 미사용.
        ad_flags:        광고 체크박스 입력값 dict. None이면 미집행(20점) 처리.

    Returns:
        {
          "seo":      int,    # 0~100  검색노출(SEO)
          "content":  int,    # 0~100  리뷰관리
          "activity": int,    # 0~100  최근활동
          "ad":       int,    # 0~100  키워드광고 (체크박스 기반)
          "ad_label": str,
          "total":    int,    # 0~100  4축 가중 평균
          "detail":   dict,   # 계산 근거
        }
    """
    # ── 검색노출(SEO) ────────────────────────────────────────────────────────
    place_results = store_data.get("place_results", [])
    raw_kw_score = sum(_rank_score(r["rank"]) for r in place_results)
    kw_score = min(100, round(raw_kw_score / SEO_MAX_KW_RAW * 85))  # 키워드 최대 85점

    info_score = 0
    if store_data.get("address"):    info_score += 5
    if store_data.get("category"):   info_score += 5
    if store_data.get("menu_items"): info_score += 3
    photo_count = store_data.get("photo_count") or 0
    if photo_count >= 10:  info_score += 5
    elif photo_count >= 3: info_score += 2
    info_score = min(SEO_INFO_BONUS, info_score)

    seo = min(100, kw_score + info_score)

    # ── 리뷰관리(content) = 방문자 + 블로그 + 별점 합산 ──────────────────────
    visitor = store_data.get("visitor_reviews")
    blog    = store_data.get("blog_reviews")
    star    = store_data.get("star_score")

    visitor_pt = _frac_ge(visitor, VISITOR_REVIEW_FRACS) * CONTENT_VISITOR_MAX
    blog_pt    = _frac_ge(blog,    BLOG_REVIEW_FRACS)    * CONTENT_BLOG_MAX
    if star is not None:
        star_pt = _frac_ge(star, STAR_FRACS) * CONTENT_STAR_MAX
        content = round(visitor_pt + blog_pt + star_pt)
    else:
        # 별점 없는 매장: 방문자+블로그만으로 100점 환산 (곱셈 아님 → 0점 방지)
        possible = CONTENT_VISITOR_MAX + CONTENT_BLOG_MAX
        content = round((visitor_pt + blog_pt) / possible * 100)
    content = min(100, content)

    # ── 최근활동(activity) = 최근 리뷰 날짜 + 최근 30일 리뷰수 + 정보 최신성 ──
    days = _days_since(store_data.get("latest_review_date"))
    recent30 = store_data.get("recent_30d_reviews")  # 신규 수집값 (None 가능)

    recency_pt = _frac_le(days, RECENCY_FRACS) * ACTIVITY_RECENCY_MAX
    info_fresh_frac = 1.0 if store_data.get("address") else 0.4
    info_fresh_pt = info_fresh_frac * ACTIVITY_INFO_MAX

    if recent30 is not None:
        recent30_pt = _frac_ge(recent30, RECENT30_FRACS) * ACTIVITY_RECENT30_MAX
        activity = round(recency_pt + recent30_pt + info_fresh_pt)
    else:
        # 30일 리뷰수 미수집: 나머지(최근 리뷰 날짜 + 정보 최신성)로 100점 환산
        possible = ACTIVITY_RECENCY_MAX + ACTIVITY_INFO_MAX
        activity = round((recency_pt + info_fresh_pt) / possible * 100)
    activity = min(100, activity)

    # ── 키워드광고(ad) = 체크박스 입력 ──────────────────────────────────────
    ad_score, ad_label = calculate_ad_score(ad_flags)

    # ── 종합 점수 ────────────────────────────────────────────────────────────
    total = calculate_total(seo, content, activity, ad_score)

    detail = {
        "kw_score_raw": raw_kw_score,
        "kw_hits": sum(1 for r in place_results if r["rank"]),
        "info_bonus": info_score,
        "visitor_reviews": visitor,
        "blog_reviews": blog,
        "star_score": star,
        "days_since_review": days,
        "recent_30d_reviews": recent30,
        "benchmark_used": benchmark is not None,
    }

    return {
        "seo":      seo,
        "content":  content,
        "activity": activity,
        "ad":       ad_score,
        "ad_label": ad_label,
        "total":    total,
        "detail":   detail,
    }
