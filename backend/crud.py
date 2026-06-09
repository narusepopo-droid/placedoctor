import json
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from . import models

CACHE_TTL_HOURS = 24


def get_store_by_place_id(db: Session, place_id: str) -> models.Store | None:
    return db.query(models.Store).filter(models.Store.place_id == place_id).first()


def get_cached_result(db: Session, place_id: str) -> dict | None:
    store = get_store_by_place_id(db, place_id)
    if not store or not store.detail or not store.detail.cached_json:
        return None
    updated = store.detail.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=CACHE_TTL_HOURS)
    if updated < cutoff:
        return None
    return json.loads(store.detail.cached_json)


def save_diagnosis(db: Session, result: dict, place_url: str) -> models.Store:
    place_id = result.get("place_id")
    now = datetime.now(timezone.utc)

    store = get_store_by_place_id(db, place_id) if place_id else None
    if not store:
        store = models.Store(
            name=result["store_name"],
            place_id=place_id,
            place_url=place_url,
            category=result.get("category", ""),
            address=result.get("address", ""),
        )
        db.add(store)
        db.flush()
    else:
        store.name = result["store_name"]
        store.category = result.get("category") or store.category
        store.address = result.get("address") or store.address

    if store.detail:
        d = store.detail
        d.visitor_reviews    = result.get("visitor_reviews")
        d.blog_reviews       = result.get("blog_reviews")
        d.star_score         = result.get("star_score")
        d.photo_count        = result.get("photo_count")
        d.latest_review_date = result.get("latest_review_date")
        d.updated_at         = now
        d.cached_json        = json.dumps(result, ensure_ascii=False)
    else:
        db.add(models.StoreDetail(
            store_id=store.id,
            visitor_reviews=result.get("visitor_reviews"),
            blog_reviews=result.get("blog_reviews"),
            star_score=result.get("star_score"),
            photo_count=result.get("photo_count"),
            latest_review_date=result.get("latest_review_date"),
            updated_at=now,
            cached_json=json.dumps(result, ensure_ascii=False),
        ))

    for pr in result.get("place_results", []):
        db.add(models.RankSnapshot(
            store_id=store.id,
            keyword=pr["keyword"],
            mode="place",
            rank=pr.get("rank"),
        ))

    existing_kws = {k.keyword for k in store.keywords}
    for kw in result.get("keywords_used", []):
        if kw not in existing_kws:
            db.add(models.Keyword(store_id=store.id, keyword=kw, auto_generated=True))

    comp = result.get("competitor", {})
    if comp.get("competitor_id"):
        best_kw = next(
            (p["keyword"] for p in result.get("place_results", []) if p.get("rank")), ""
        )
        comp_d = comp.get("details", {})
        db.add(models.Competitor(
            store_id=store.id,
            keyword=best_kw,
            competitor_place_id=comp["competitor_id"],
            rank=comp.get("competitor_rank"),
            visitor_reviews=comp_d.get("visitor_reviews"),
        ))

    scores = result.get("scores", {})
    db.add(models.ScoreSnapshot(
        store_id=store.id,
        seo=scores.get("seo"),
        content=scores.get("content"),
        activity=scores.get("activity"),
        ad=scores.get("ad"),
        total=scores.get("total"),
    ))

    db.commit()
    db.refresh(store)
    return store


def get_store_history(db: Session, place_id: str) -> dict | None:
    store = get_store_by_place_id(db, place_id)
    if not store:
        return None
    return {
        "place_id": place_id,
        "store_name": store.name,
        "rank_history": [
            {
                "keyword": r.keyword,
                "mode": r.mode,
                "rank": r.rank,
                "captured_at": r.captured_at.isoformat(),
            }
            for r in store.rank_snapshots
        ],
        "score_history": [
            {
                "seo": s.seo,
                "content": s.content,
                "activity": s.activity,
                "ad": s.ad,
                "total": s.total,
                "captured_at": s.captured_at.isoformat(),
            }
            for s in store.score_snapshots
        ],
    }


def create_lead(
    db: Session, contact: str, source: str, store_id: int | None = None
) -> models.Lead:
    lead = models.Lead(contact=contact, source=source, store_id=store_id, status="new")
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def save_analysis_history(
    db: Session,
    place_id: str,
    store_name: str,
    analysis_type: str,
    total_score: float | None,
    result_json: str,
    anon_id: str | None = None,
) -> models.AnalysisHistory:
    """분석 결과를 히스토리에 누적 저장 (덮어쓰기 X)"""
    history = models.AnalysisHistory(
        place_id=place_id,
        store_name=store_name,
        analysis_type=analysis_type,
        total_score=total_score,
        result_json=result_json,
        anon_id=anon_id,
    )
    db.add(history)
    db.commit()
    db.refresh(history)
    return history


def get_previous_analysis(
    db: Session, place_id: str, analysis_type: str
) -> models.AnalysisHistory | None:
    """해당 매장의 직전 분석 기록 조회 (현재 분석 제외)"""
    return (
        db.query(models.AnalysisHistory)
        .filter(
            models.AnalysisHistory.place_id == place_id,
            models.AnalysisHistory.analysis_type == analysis_type,
        )
        .order_by(models.AnalysisHistory.analyzed_at.desc())
        .first()
    )


def get_analysis_history_list(
    db: Session, place_id: str, analysis_type: str, limit: int = 10
) -> list[models.AnalysisHistory]:
    """해당 매장의 분석 히스토리 목록 (최근순)"""
    return (
        db.query(models.AnalysisHistory)
        .filter(
            models.AnalysisHistory.place_id == place_id,
            models.AnalysisHistory.analysis_type == analysis_type,
        )
        .order_by(models.AnalysisHistory.analyzed_at.desc())
        .limit(limit)
        .all()
    )


def get_keyword_rank_history(
    db: Session, place_id: str, analysis_type: str, limit: int = 5
) -> dict[str, list[dict]]:
    """
    키워드별 과거 순위 기록을 조회합니다.

    Returns:
        {"키워드명": [{"rank": 5, "date": "06/08"}, ...], ...}
        리스트는 오래된 순 -> 최신순 정렬
    """
    history_list = get_analysis_history_list(db, place_id, analysis_type, limit)
    if not history_list:
        return {}

    keyword_history: dict[str, list[dict]] = {}

    # 오래된 순으로 처리 (reverse)
    for record in reversed(history_list):
        if not record.result_json:
            continue
        try:
            data = json.loads(record.result_json)
            results_key = "place_results" if analysis_type == "place" else "blog_results"
            results = data.get(results_key, [])

            date_str = record.analyzed_at.strftime("%m/%d") if record.analyzed_at else ""

            for item in results:
                kw = item.get("keyword", "")
                if not kw:
                    continue

                rank = item.get("rank")
                if rank is None:
                    rank_str = item.get("status", "")
                    if "위" in str(rank_str):
                        try:
                            rank = int(str(rank_str).replace("위", ""))
                        except:
                            rank = None

                if kw not in keyword_history:
                    keyword_history[kw] = []
                keyword_history[kw].append({
                    "rank": rank,
                    "date": date_str,
                })
        except Exception:
            continue

    return keyword_history


def get_analysis_count(db: Session, place_id: str, analysis_type: str) -> int:
    """해당 매장의 분석 횟수"""
    return (
        db.query(models.AnalysisHistory)
        .filter(
            models.AnalysisHistory.place_id == place_id,
            models.AnalysisHistory.analysis_type == analysis_type,
        )
        .count()
    )


def get_recent_stores_by_anon_id(
    db: Session, anon_id: str, limit: int = 10
) -> list[dict]:
    """
    K단계: 익명 사용자의 최근 본 매장 목록을 반환합니다.
    place_id별로 가장 최근 분석 기록만 (중복 제거).
    """
    from sqlalchemy import func, desc

    # place_id별 최신 분석 기록의 id를 서브쿼리로
    subq = (
        db.query(
            models.AnalysisHistory.place_id,
            func.max(models.AnalysisHistory.id).label("max_id"),
        )
        .filter(models.AnalysisHistory.anon_id == anon_id)
        .group_by(models.AnalysisHistory.place_id)
        .subquery()
    )

    # 최신 기록만 조회
    records = (
        db.query(models.AnalysisHistory)
        .join(subq, models.AnalysisHistory.id == subq.c.max_id)
        .order_by(desc(models.AnalysisHistory.analyzed_at))
        .limit(limit)
        .all()
    )

    result = []
    for r in records:
        # 주소 추출 (result_json에서)
        address = ""
        category = ""
        if r.result_json:
            try:
                data = json.loads(r.result_json)
                address = data.get("address", "")
                category = data.get("category", "")
            except:
                pass

        result.append({
            "place_id": r.place_id,
            "store_name": r.store_name,
            "address": address,
            "category": category,
            "analysis_type": r.analysis_type,
            "total_score": r.total_score,
            "analyzed_at": r.analyzed_at.isoformat() if r.analyzed_at else None,
        })

    return result


def get_latest_analysis_result(
    db: Session, place_id: str, analysis_type: str = "place"
) -> dict | None:
    """
    K단계: 특정 매장의 최신 분석 결과를 반환합니다 (재크롤링 없이 즉시 표시용).
    """
    record = (
        db.query(models.AnalysisHistory)
        .filter(
            models.AnalysisHistory.place_id == place_id,
            models.AnalysisHistory.analysis_type == analysis_type,
        )
        .order_by(models.AnalysisHistory.analyzed_at.desc())
        .first()
    )

    if not record or not record.result_json:
        return None

    try:
        result = json.loads(record.result_json)
        result["_from_history"] = True
        result["_analyzed_at"] = record.analyzed_at.isoformat() if record.analyzed_at else None
        return result
    except:
        return None
