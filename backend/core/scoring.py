"""
PlaceDoctor 3축 점수 계산 모듈

축 (화면 라벨):
  seo      → 검색노출(SEO)
  content  → 리뷰관리
  activity → 최근활동

AS단계에서 광고 축 제거 — 사용자 입력 의존(체크박스)이라 실질 가치 낮음.
3축 비율 재배분: 기존 34/30/21 비율 유지하며 100%로 스케일.
"""

from datetime import datetime, date

# ── 종합 가중치 ───────────────────────────────────────────────────────────────
# 광고 축 제거 후 3축 재배분 (기존 비율 유지)
SEO_WEIGHT      = 0.40   # 검색노출 (기존 34% → 40%)
CONTENT_WEIGHT  = 0.35   # 리뷰관리 (기존 30% → 35%)
ACTIVITY_WEIGHT = 0.25   # 최근활동 (기존 21% → 25%)

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
# 합산 방식: 최근 리뷰 날짜 + 최근 30일 리뷰수 + 정보 최신성.
# 30일 리뷰수는 리뷰탭 처음 ~10개 중 30일 이내 개수(더보기 없음). 미수집(None)이면
# 그 항목을 빼고 나머지(최근 리뷰 날짜 + 정보 최신성)로 100점 환산.
ACTIVITY_RECENCY_MAX  = 50   # 최근 리뷰 날짜 만점 기여
ACTIVITY_RECENT30_MAX = 35   # 최근 30일 리뷰수 만점 기여
ACTIVITY_INFO_MAX     = 15   # 정보 최신성 만점 기여
# 최근 리뷰 경과 일수 (이하 → 비율)
RECENCY_FRACS = [(7, 1.0), (30, 0.8), (90, 0.55), (180, 0.30), (365, 0.10), (9999, 0.0)]
# 처음 ~10개 중 30일 이내 리뷰 개수 (이상 → 비율) — 6+ 활발 / 3~5 보통 / 1~2 한산 / 0 거의없음
RECENT30_FRACS = [(6, 1.0), (3, 0.7), (1, 0.45), (0, 0.25)]

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


def calculate_total(seo: int, content: int, activity) -> int:
    """3축 가중 평균 종합 점수.

    activity가 None이면(= 리뷰 활동 수집 실패) 최근활동 축을 빼고
    나머지 2축(seo/content)을 재정규화한다. 거짓 낮은 활동 점수가 총점을 깎지 않게.
    """
    if activity is None:
        w = SEO_WEIGHT + CONTENT_WEIGHT
        return round((seo * SEO_WEIGHT + content * CONTENT_WEIGHT) / w)
    return round(
        seo * SEO_WEIGHT + content * CONTENT_WEIGHT + activity * ACTIVITY_WEIGHT
    )


# ── 공개 API ──────────────────────────────────────────────────────────────────

def calculate_scores(store_data: dict, competitor_data: dict = None,
                     benchmark: dict = None) -> dict:
    """
    3축 진단 점수를 계산합니다.

    Args:
        store_data:      diagnose_store() 반환값 (place_results, visitor_reviews 등 포함)
        competitor_data: find_competitor() 반환값. None이면 경쟁사 비교 없이 점수만 계산.
        benchmark:       업종 평균값 dict. 현재는 자리만 유지 — None이면 미사용.

    Returns:
        {
          "seo":      int,    # 0~100  검색노출(SEO)
          "content":  int,    # 0~100  리뷰관리
          "activity": int,    # 0~100  최근활동
          "total":    int,    # 0~100  3축 가중 평균
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
    recent30 = store_data.get("recent_30d_reviews")  # 처음 ~10개 중 30일 이내 개수. None 가능

    recency_pt = _frac_le(days, RECENCY_FRACS) * ACTIVITY_RECENCY_MAX
    info_fresh_frac = 1.0 if store_data.get("address") else 0.4
    info_fresh_pt = info_fresh_frac * ACTIVITY_INFO_MAX

    # B단계: 리뷰 활동을 전혀 수집 못 한 경우(최근 리뷰 날짜 None + 30일 리뷰수 None,
    # 보통 m.place 차단) 거짓 낮은 점수 대신 activity=None → 총점에서 제외(재정규화)하고
    # 화면에 "수집 중" 중립 표시. 리뷰 신호 일부라도 있으면 정상 계산.
    if days is None and recent30 is None:
        activity = None
    elif recent30 is not None:
        recent30_pt = _frac_ge(recent30, RECENT30_FRACS) * ACTIVITY_RECENT30_MAX
        activity = min(100, round(recency_pt + recent30_pt + info_fresh_pt))
    else:
        # 30일 리뷰수만 미수집: 나머지(최근 리뷰 날짜 + 정보 최신성)로 100점 환산
        possible = ACTIVITY_RECENCY_MAX + ACTIVITY_INFO_MAX
        activity = min(100, round((recency_pt + info_fresh_pt) / possible * 100))

    # ── 종합 점수 ────────────────────────────────────────────────────────────
    total = calculate_total(seo, content, activity)

    detail = {
        "kw_score_raw": raw_kw_score,
        "kw_hits": sum(1 for r in place_results if r["rank"]),
        "info_bonus": info_score,
        "visitor_reviews": visitor,
        "blog_reviews": blog,
        "star_score": star,
        "days_since_review": days,
        "review_activity": store_data.get("review_activity"),
        "recent_30d_reviews": recent30,
        "benchmark_used": benchmark is not None,
    }

    return {
        "seo":      seo,
        "content":  content,
        "activity": activity,
        "total":    total,
        "detail":   detail,
    }
