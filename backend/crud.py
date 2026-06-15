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

    # P단계: competitor는 {status, cards:[...]} 구조. 첫 비교 카드를 스냅샷으로 저장(있으면).
    comp = result.get("competitor", {})
    comp_cards = comp.get("cards") or []
    if comp_cards and comp_cards[0].get("competitor_id"):
        c0 = comp_cards[0]
        db.add(models.Competitor(
            store_id=store.id,
            keyword=c0.get("keyword", ""),
            competitor_place_id=c0["competitor_id"],
            rank=c0.get("competitor_rank"),
            visitor_reviews=None,  # P단계: 경쟁사 리뷰 미수집(속도 개선 — get_store_details 제거)
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
    히스토리 추세 정보(analysis_count, prev_analysis, keyword_history)도 포함.
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

        # 히스토리 추세 정보 추가 (최근 매장에서 불러올 때도 추세 표시)
        analysis_count = get_analysis_count(db, place_id, analysis_type)
        result["analysis_count"] = analysis_count

        # 직전 분석 기록 (현재 레코드 제외)
        prev_record = (
            db.query(models.AnalysisHistory)
            .filter(
                models.AnalysisHistory.place_id == place_id,
                models.AnalysisHistory.analysis_type == analysis_type,
                models.AnalysisHistory.id != record.id,  # 현재 레코드 제외
            )
            .order_by(models.AnalysisHistory.analyzed_at.desc())
            .first()
        )

        if prev_record:
            result["prev_analysis"] = {
                "total_score": prev_record.total_score,
                "analyzed_at": prev_record.analyzed_at.isoformat() if prev_record.analyzed_at else None,
                "result_json": prev_record.result_json,
            }
            result["prev_analyzed_at"] = prev_record.analyzed_at.strftime("%m/%d") if prev_record.analyzed_at else None
        else:
            result["prev_analysis"] = None
            result["prev_analyzed_at"] = None

        # 키워드별 과거 순위 기록
        result["keyword_history"] = get_keyword_rank_history(db, place_id, analysis_type, limit=5)

        return result
    except:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# M단계: 내 매장 / 경쟁 매장 등록
# ─────────────────────────────────────────────────────────────────────────────

def register_store(
    db: Session,
    anon_id: str,
    place_id: str,
    store_name: str,
    store_type: str,  # 'my' | 'rival'
) -> models.RegisteredStore | None:
    """매장을 내 매장 또는 경쟁 매장으로 등록 (중복 방지)"""
    existing = (
        db.query(models.RegisteredStore)
        .filter(
            models.RegisteredStore.anon_id == anon_id,
            models.RegisteredStore.place_id == place_id,
            models.RegisteredStore.store_type == store_type,
        )
        .first()
    )
    if existing:
        return existing  # 이미 등록됨

    reg = models.RegisteredStore(
        anon_id=anon_id,
        place_id=place_id,
        store_name=store_name,
        store_type=store_type,
    )
    db.add(reg)
    db.commit()
    db.refresh(reg)
    return reg


def unregister_store(
    db: Session,
    anon_id: str,
    place_id: str,
    store_type: str,
) -> bool:
    """매장 등록 해제"""
    record = (
        db.query(models.RegisteredStore)
        .filter(
            models.RegisteredStore.anon_id == anon_id,
            models.RegisteredStore.place_id == place_id,
            models.RegisteredStore.store_type == store_type,
        )
        .first()
    )
    if record:
        db.delete(record)
        db.commit()
        return True
    return False


def get_registered_stores(
    db: Session,
    anon_id: str,
) -> dict:
    """
    내 매장 / 경쟁 매장 목록 조회.
    각 매장에 최근 분석 결과 정보도 포함.
    """
    records = (
        db.query(models.RegisteredStore)
        .filter(models.RegisteredStore.anon_id == anon_id)
        .order_by(models.RegisteredStore.registered_at.desc())
        .all()
    )

    my_stores = []
    rival_stores = []

    for r in records:
        # 최근 분석 결과 조회
        latest = get_latest_analysis_result(db, r.place_id, "place")

        info = {
            "id": r.id,
            "place_id": r.place_id,
            "store_name": r.store_name,
            "store_type": r.store_type,
            "registered_at": r.registered_at.isoformat() if r.registered_at else None,
            "total_score": None,
            "analyzed_at": None,
            "top_keyword": None,
            "top_rank": None,
        }

        if latest:
            info["total_score"] = latest.get("scores", {}).get("total")
            info["analyzed_at"] = latest.get("_analyzed_at")
            # 대표 키워드: 순위가 있는 키워드 중 가장 좋은 것
            place_results = latest.get("place_results", [])
            ranked = [p for p in place_results if p.get("rank")]
            if ranked:
                best = min(ranked, key=lambda x: x["rank"])
                info["top_keyword"] = best.get("keyword")
                info["top_rank"] = best.get("rank")

        if r.store_type == "my":
            my_stores.append(info)
        else:
            rival_stores.append(info)

    return {"my_stores": my_stores, "rival_stores": rival_stores}


def get_store_registration_status(
    db: Session,
    anon_id: str,
    place_id: str,
) -> dict:
    """특정 매장의 등록 상태 조회"""
    records = (
        db.query(models.RegisteredStore)
        .filter(
            models.RegisteredStore.anon_id == anon_id,
            models.RegisteredStore.place_id == place_id,
        )
        .all()
    )
    return {
        "is_my": any(r.store_type == "my" for r in records),
        "is_rival": any(r.store_type == "rival" for r in records),
    }
