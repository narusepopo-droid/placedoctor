"""
PlaceDoctor 4축 점수 계산 모듈

축별 가중치 및 임계값은 상수로 관리하므로 나중에 조정 가능합니다.
"""

from datetime import datetime, date

# ── 가중치 ────────────────────────────────────────────────────────────────────
# 광고 실제 감지 미구현이므로 플레이스홀더 20점(미집행) 고정.
# 낮은 점수가 고정되므로 비중을 15%로 제한해 종합 점수 변별력 유지.
SEO_WEIGHT      = 0.34
CONTENT_WEIGHT  = 0.30
ACTIVITY_WEIGHT = 0.21
AD_WEIGHT       = 0.15  # 광고 축 비중

AD_SCORE_PLACEHOLDER = 20  # 미집행 기본값 (실제 감지 구현 시 교체)

# ── SEO 축 상수 ───────────────────────────────────────────────────────────────
# 키워드 순위 → 점수 (키워드 하나당)
RANK_SCORE_TABLE = [
    (1,  3,  20),   # 1~3위: 20점
    (4,  7,  12),   # 4~7위: 12점
    (8,  15,  6),   # 8~15위: 6점
    (16, 30,  2),   # 16~30위: 2점
]
SEO_MAX_KW_RAW    = 60   # 키워드 점수 만점 기준 (이 이상이면 SEO 100점)
SEO_INFO_BONUS    = 15   # 주소+업종+메뉴 완성도 보너스 (최대)

# ── 콘텐츠 축 상수 ────────────────────────────────────────────────────────────
# (리뷰 수 이상 → 점수) 형태의 계단 함수
VISITOR_REVIEW_STEPS = [(500, 70), (200, 55), (100, 42), (50, 30), (20, 18), (5,  8), (0, 0)]
BLOG_REVIEW_STEPS    = [(300, 30), (100, 24), (50, 18),  (20, 12), (5,  6),  (0, 0)]
# 두 합산이 100점 만점 (visitor 70 + blog 30)

# ── 활성도 축 상수 ────────────────────────────────────────────────────────────
# 최근 리뷰 날짜 기준 (경과 일수 이하 → 점수)
RECENCY_STEPS = [(7, 100), (30, 80), (90, 55), (180, 30), (365, 10), (9999, 0)]


# ── 내부 유틸 ─────────────────────────────────────────────────────────────────

def _step(value, steps):
    """steps = [(임계값, 점수), ...] 내림차순. value가 임계값 이상이면 해당 점수."""
    if value is None:
        return 0
    for threshold, score in steps:
        if value >= threshold:
            return score
    return 0


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


# ── 공개 API ──────────────────────────────────────────────────────────────────

def calculate_scores(store_data: dict, competitor_data: dict = None, benchmark: dict = None) -> dict:
    """
    4축 진단 점수를 계산합니다.

    Args:
        store_data:      diagnose_store() 반환값 (place_results, visitor_reviews 등 포함)
        competitor_data: find_competitor() 반환값. None이면 경쟁사 비교 없이 점수만 계산.
        benchmark:       업종 평균값 dict (예: {"visitor_reviews": 150, "seo": 60}).
                         현재는 자리만 유지 — None이면 미사용.

    Returns:
        {
          "seo":      int,   # 0~100
          "content":  int,   # 0~100
          "activity": int,   # 0~100
          "ad":       None,  # 미구현
          "total":    int,   # 0~100 (3축 가중 평균)
          "detail":   dict,  # 계산 근거
        }
    """
    # ── SEO 점수 ─────────────────────────────────────────────────────────────
    place_results = store_data.get("place_results", [])
    raw_kw_score = sum(_rank_score(r["rank"]) for r in place_results)
    kw_score = min(100, round(raw_kw_score / SEO_MAX_KW_RAW * 85))  # 키워드 최대 85점

    # 정보 완성도 보너스
    info_score = 0
    if store_data.get("address"):   info_score += 5
    if store_data.get("category"):  info_score += 5
    if store_data.get("menu_items"): info_score += 3
    photo_count = store_data.get("photo_count") or 0
    if photo_count >= 10: info_score += 5
    elif photo_count >= 3: info_score += 2
    info_score = min(SEO_INFO_BONUS, info_score)

    seo = min(100, kw_score + info_score)

    # ── 콘텐츠 점수 ───────────────────────────────────────────────────────────
    visitor = store_data.get("visitor_reviews")
    blog    = store_data.get("blog_reviews")
    content = min(100, _step(visitor, VISITOR_REVIEW_STEPS) + _step(blog, BLOG_REVIEW_STEPS))

    # ── 활성도 점수 ───────────────────────────────────────────────────────────
    days = _days_since(store_data.get("latest_review_date"))
    activity = _step(
        (9999 - days) if days is not None else None,   # 경과 일수를 역수로 변환
        [(9999 - thr, score) for thr, score in RECENCY_STEPS]
    ) if days is not None else 0

    # days 그대로 step 함수에 맞게 다시 계산 (역수 변환 방식보다 직접 계산이 명확)
    if days is not None:
        activity = 0
        for thr, score in RECENCY_STEPS:
            if days <= thr:
                activity = score
                break

    # ── 광고 축 (감지 미구현 — 플레이스홀더) ────────────────────────────────
    ad = AD_SCORE_PLACEHOLDER

    # ── 종합 점수 (4축 가중 평균) ────────────────────────────────────────────
    total = round(seo * SEO_WEIGHT + content * CONTENT_WEIGHT + activity * ACTIVITY_WEIGHT + ad * AD_WEIGHT)

    detail = {
        "kw_score_raw": raw_kw_score,
        "kw_hits": sum(1 for r in place_results if r["rank"]),
        "info_bonus": info_score,
        "visitor_reviews": visitor,
        "blog_reviews": blog,
        "days_since_review": days,
        "benchmark_used": benchmark is not None,
    }

    return {
        "seo":      seo,
        "content":  content,
        "activity": activity,
        "ad":       ad,
        "total":    total,
        "detail":   detail,
    }
