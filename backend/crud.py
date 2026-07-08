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
    source: str | None = None,
    search_query: str | None = None,
) -> models.AnalysisHistory:
    """분석 결과를 히스토리에 누적 저장 (덮어쓰기 X)"""
    history = models.AnalysisHistory(
        place_id=place_id,
        store_name=store_name,
        analysis_type=analysis_type,
        total_score=total_score,
        result_json=result_json,
        anon_id=anon_id,
        source=source,
        search_query=search_query,
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


# ─────────────────────────────────────────────────────────────────────────────
# 알림톡 구독자 관리
# ─────────────────────────────────────────────────────────────────────────────

def subscribe_alarm(
    db: Session,
    store_name: str,
    phone: str,
    store_url: str | None = None,
    place_id: str | None = None,
    anon_id: str | None = None,
) -> models.Subscriber:
    """알림 구독 신청 (중복이면 새 행 추가 없이 기존 행 업데이트).
    중복 판정 우선순위: ① anon_id+place_id → ② phone+place_id(다른 기기여도 동일 리드)
    → ③ place_id 없으면 phone+store_name. 한 사람이 다른 매장을 신청하면 store_name/place_id가
    달라 별도 리드로 유지된다."""
    existing = None
    if anon_id and place_id:
        existing = (
            db.query(models.Subscriber)
            .filter(
                models.Subscriber.anon_id == anon_id,
                models.Subscriber.place_id == place_id,
            )
            .first()
        )
    # 다른 기기/세션에서 같은 번호+같은 매장으로 재신청 → 중복 생성 방지
    if not existing and phone and place_id:
        existing = (
            db.query(models.Subscriber)
            .filter(
                models.Subscriber.phone == phone,
                models.Subscriber.place_id == place_id,
            )
            .first()
        )
    # place_id가 없는 경우 번호+매장명으로 중복 판정
    if not existing and phone and store_name and not place_id:
        existing = (
            db.query(models.Subscriber)
            .filter(
                models.Subscriber.phone == phone,
                models.Subscriber.store_name == store_name,
            )
            .first()
        )

    now = datetime.now(timezone.utc)
    if existing:
        existing.phone = phone
        existing.store_name = store_name
        existing.store_url = store_url
        existing.alarm_on = True
        existing.agreed_at = now
        db.commit()
        db.refresh(existing)
        return existing

    sub = models.Subscriber(
        anon_id=anon_id,
        store_name=store_name,
        store_url=store_url,
        place_id=place_id,
        phone=phone,
        alarm_on=True,
        agreed_at=now,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def unsubscribe_alarm(db: Session, subscriber_id: int) -> bool:
    """알림 해지 (삭제 아닌 alarm_on=False)"""
    sub = db.query(models.Subscriber).filter(models.Subscriber.id == subscriber_id).first()
    if sub:
        sub.alarm_on = False
        db.commit()
        return True
    return False


def resubscribe_alarm(db: Session, subscriber_id: int) -> bool:
    """알림 재구독"""
    sub = db.query(models.Subscriber).filter(models.Subscriber.id == subscriber_id).first()
    if sub:
        sub.alarm_on = True
        sub.agreed_at = datetime.now(timezone.utc)
        db.commit()
        return True
    return False


def delete_subscriber(db: Session, subscriber_id: int) -> bool:
    """구독자 영구 삭제 (테스트/중복 데이터 정리용 — 관리자 수동 호출만)"""
    sub = db.query(models.Subscriber).filter(models.Subscriber.id == subscriber_id).first()
    if sub:
        db.delete(sub)
        db.commit()
        return True
    return False


def get_all_subscribers(db: Session, alarm_on_only: bool = False) -> list[models.Subscriber]:
    """전체 구독자 목록"""
    q = db.query(models.Subscriber)
    if alarm_on_only:
        q = q.filter(models.Subscriber.alarm_on == True)
    return q.order_by(models.Subscriber.created_at.desc()).all()


def get_subscriber_count(db: Session, alarm_on_only: bool = False) -> int:
    """구독자 수"""
    q = db.query(models.Subscriber)
    if alarm_on_only:
        q = q.filter(models.Subscriber.alarm_on == True)
    return q.count()


def get_new_subscribers_this_week(db: Session) -> int:
    """이번 주 신규 구독자 수"""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    return (
        db.query(models.Subscriber)
        .filter(models.Subscriber.created_at >= week_ago)
        .count()
    )


def update_subscriber_last_analyzed(db: Session, place_id: str):
    """분석 시 구독자의 last_analyzed_at 업데이트"""
    subs = db.query(models.Subscriber).filter(models.Subscriber.place_id == place_id).all()
    now = datetime.now(timezone.utc)
    for sub in subs:
        sub.last_analyzed_at = now
    if subs:
        db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 알림톡 템플릿 관리
# ─────────────────────────────────────────────────────────────────────────────

def get_alim_template(db: Session, template_key: str) -> models.AlimTemplate | None:
    return db.query(models.AlimTemplate).filter(models.AlimTemplate.template_key == template_key).first()


def get_all_alim_templates(db: Session) -> list[models.AlimTemplate]:
    return db.query(models.AlimTemplate).all()


def upsert_alim_template(db: Session, template_key: str, extra_text: str) -> models.AlimTemplate:
    """추가문구 저장 (없으면 생성)"""
    tpl = get_alim_template(db, template_key)
    now = datetime.now(timezone.utc)
    if tpl:
        tpl.extra_text = extra_text
        tpl.updated_at = now
    else:
        tpl = models.AlimTemplate(
            template_key=template_key,
            extra_text=extra_text,
            updated_at=now,
        )
        db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


# ─────────────────────────────────────────────────────────────────────────────
# 알림톡 발송 이력
# ─────────────────────────────────────────────────────────────────────────────

def create_alimtalk_log(db: Session, *, template_key: str, template_code: str,
                        phone: str, store_name: str, success: bool,
                        result_code: str = "", message: str = "") -> models.AlimtalkLog:
    """발송 1건 기록 (실패해도 기록)"""
    log = models.AlimtalkLog(
        template_key=template_key,
        template_code=template_code,
        phone=phone,
        store_name=store_name,
        success=success,
        result_code=str(result_code) if result_code is not None else "",
        message=(message or "")[:500],
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_recent_alimtalk_logs(db: Session, limit: int = 50) -> list[models.AlimtalkLog]:
    return (
        db.query(models.AlimtalkLog)
        .order_by(models.AlimtalkLog.sent_at.desc())
        .limit(limit)
        .all()
    )


# ─────────────────────────────────────────────────────────────────────────────
# 관리자 대시보드 통계
# ─────────────────────────────────────────────────────────────────────────────

def get_admin_stats(db: Session) -> dict:
    """관리자 대시보드용 통계"""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    total_analyses = db.query(models.AnalysisHistory).count()
    registered_stores = db.query(models.RegisteredStore).filter(
        models.RegisteredStore.store_type == "my"
    ).count()
    subscriber_count = get_subscriber_count(db)
    new_subscribers_week = get_new_subscribers_this_week(db)
    new_analyses_week = (
        db.query(models.AnalysisHistory)
        .filter(models.AnalysisHistory.analyzed_at >= week_ago)
        .count()
    )

    # 방문 통계
    total_visits = db.query(models.SiteVisit).count()
    visits_this_week = db.query(models.SiteVisit).filter(
        models.SiteVisit.visited_at >= week_ago
    ).count()

    # 재방문 통계
    revisit = get_revisit_stats(db)

    return {
        "total_analyses": total_analyses,
        "registered_stores": registered_stores,
        "subscriber_count": subscriber_count,
        "new_subscribers_week": new_subscribers_week,
        "new_analyses_week": new_analyses_week,
        "total_visits": total_visits,
        "visits_this_week": visits_this_week,
        "unique_visitors": revisit["unique_visitors"],
        "returning_visitors": revisit["returning_visitors"],
        "revisit_rate": revisit["revisit_rate"],
        "returning_visits": revisit["returning_visits"],
    }


def get_recent_analyses(db: Session, limit: int = 10) -> list[dict]:
    """최근 분석 목록"""
    records = (
        db.query(models.AnalysisHistory)
        .order_by(models.AnalysisHistory.analyzed_at.desc())
        .limit(limit)
        .all()
    )
    result = []
    for r in records:
        top_keyword = ""
        if r.result_json:
            try:
                data = json.loads(r.result_json)
                place_results = data.get("place_results", [])
                ranked = [p for p in place_results if p.get("rank")]
                if ranked:
                    best = min(ranked, key=lambda x: x["rank"])
                    top_keyword = best.get("keyword", "")
            except:
                pass
        result.append({
            "store_name": r.store_name,
            "top_keyword": top_keyword,
            "total_score": r.total_score,
            "analyzed_at": r.analyzed_at.isoformat() if r.analyzed_at else None,
        })
    return result


def get_monitored_stores(db: Session, limit: int = 50) -> list[dict]:
    """모니터링 중인 매장 목록 (내 매장으로 등록된 것들)"""
    from sqlalchemy import func, distinct

    # place_id별 최근 2개 분석 기록 가져오기
    stores = (
        db.query(models.RegisteredStore)
        .filter(models.RegisteredStore.store_type == "my")
        .limit(limit)
        .all()
    )

    result = []
    for s in stores:
        histories = (
            db.query(models.AnalysisHistory)
            .filter(
                models.AnalysisHistory.place_id == s.place_id,
                models.AnalysisHistory.analysis_type == "place",
            )
            .order_by(models.AnalysisHistory.analyzed_at.desc())
            .limit(2)
            .all()
        )

        top_keyword = ""
        this_rank = None
        last_rank = None

        if histories:
            # 이번 주 (최신)
            latest = histories[0]
            if latest.result_json:
                try:
                    data = json.loads(latest.result_json)
                    place_results = data.get("place_results", [])
                    ranked = [p for p in place_results if p.get("rank")]
                    if ranked:
                        best = min(ranked, key=lambda x: x["rank"])
                        top_keyword = best.get("keyword", "")
                        this_rank = best.get("rank")
                except:
                    pass

            # 지난 주 (이전)
            if len(histories) > 1:
                prev = histories[1]
                if prev.result_json and top_keyword:
                    try:
                        data = json.loads(prev.result_json)
                        place_results = data.get("place_results", [])
                        for p in place_results:
                            if p.get("keyword") == top_keyword:
                                last_rank = p.get("rank")
                                break
                    except:
                        pass

        result.append({
            "store_name": s.store_name,
            "place_id": s.place_id,
            "top_keyword": top_keyword,
            "this_rank": this_rank,
            "last_rank": last_rank,
        })

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 관리자 2차: 검색/필터 + 리드 상태 + 일별 추이 + 유입경로
# ─────────────────────────────────────────────────────────────────────────────

def get_analyses_filtered(
    db: Session,
    search: str = "",
    date_range: str = "all",
    has_score: str = "all",
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """검색/필터가 적용된 분석 목록 + 페이지네이션"""
    from datetime import timedelta
    now = datetime.now(timezone.utc)

    query = db.query(models.AnalysisHistory)

    # 검색: 매장명
    if search:
        query = query.filter(models.AnalysisHistory.store_name.ilike(f"%{search}%"))

    # 날짜 범위 필터
    if date_range == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(models.AnalysisHistory.analyzed_at >= start)
    elif date_range == "week":
        start = now - timedelta(days=7)
        query = query.filter(models.AnalysisHistory.analyzed_at >= start)
    elif date_range == "month":
        start = now - timedelta(days=30)
        query = query.filter(models.AnalysisHistory.analyzed_at >= start)

    # 플레이스 지수 유무 필터
    if has_score == "yes":
        query = query.filter(models.AnalysisHistory.total_score.isnot(None))
    elif has_score == "no":
        query = query.filter(models.AnalysisHistory.total_score.is_(None))

    total = query.count()
    records = (
        query.order_by(models.AnalysisHistory.analyzed_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = []
    for r in records:
        region = ""
        category = ""
        place_url = ""
        if r.result_json:
            try:
                data = json.loads(r.result_json)
                address = data.get("address", "")
                # 지역: 주소에서 구/시/군 추출
                import re
                match = re.search(r'([\w]+구|[\w]+시|[\w]+군)', address)
                region = match.group(1) if match else ""
                if not region and address:
                    # 시/도 + 구 형태로 재시도
                    parts = address.split()
                    if len(parts) >= 2:
                        region = parts[1] if len(parts[1]) >= 2 else parts[0]
                # 업종: category에서 첫번째 항목
                cat_raw = data.get("category", "")
                if cat_raw:
                    category = cat_raw.split(",")[0].strip()
                # category 비어있으면 store_name에서 추론
                if not category:
                    category = _infer_category(r.store_name)
                place_url = data.get("place_url", "")
                if not place_url and r.place_id:
                    place_url = f"https://m.place.naver.com/place/{r.place_id}"
            except:
                pass
        # result_json 없어도 store_name에서 업종 추론
        if not category:
            category = _infer_category(r.store_name)
        items.append({
            "id": r.id,
            "store_name": r.store_name,
            "place_id": r.place_id,
            "analysis_type": r.analysis_type,
            "region": region,
            "category": category,
            "place_url": place_url,
            "total_score": r.total_score,
            "source": r.source,
            "search_query": r.search_query,
            "analyzed_at": r.analyzed_at.isoformat() if r.analyzed_at else None,
        })

    # 업체별 누적 분석 횟수 + 분석유형(place/blog) — 이 페이지 place_id들만 한 번에 집계
    from sqlalchemy import func
    place_ids = list({it["place_id"] for it in items if it["place_id"]})
    count_map = {}
    type_map: dict[str, set] = {}
    if place_ids:
        rows = (
            db.query(
                models.AnalysisHistory.place_id,
                models.AnalysisHistory.analysis_type,
                func.count().label("c"),
            )
            .filter(models.AnalysisHistory.place_id.in_(place_ids))
            .group_by(models.AnalysisHistory.place_id, models.AnalysisHistory.analysis_type)
            .all()
        )
        for pid, atype, c in rows:
            count_map[pid] = count_map.get(pid, 0) + c
            type_map.setdefault(pid, set()).add(atype)
    for it in items:
        pid = it["place_id"]
        it["store_count"] = count_map.get(pid, 1)
        types = type_map.get(pid) or {it["analysis_type"]}
        it["has_place"] = "place" in types
        it["has_blog"] = "blog" in types

    return {"total": total, "items": items}


def get_daily_analysis_counts(db: Session, days: int = 30) -> list[dict]:
    """일별 진단 수 집계"""
    from sqlalchemy import func, cast, Date
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    results = (
        db.query(
            cast(models.AnalysisHistory.analyzed_at, Date).label("date"),
            func.count().label("count"),
        )
        .filter(models.AnalysisHistory.analyzed_at >= start)
        .group_by(cast(models.AnalysisHistory.analyzed_at, Date))
        .order_by(cast(models.AnalysisHistory.analyzed_at, Date))
        .all()
    )

    return [{"date": r.date.isoformat(), "count": r.count} for r in results]


def get_source_stats(db: Session, days: int = 30) -> list[dict]:
    """유입경로별 통계"""
    from sqlalchemy import func
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    results = (
        db.query(
            models.AnalysisHistory.source,
            func.count().label("count"),
        )
        .filter(models.AnalysisHistory.analyzed_at >= start)
        .group_by(models.AnalysisHistory.source)
        .all()
    )

    return [
        {"source": r.source or "unknown", "count": r.count}
        for r in results
    ]


def update_subscriber_status(
    db: Session, subscriber_id: int, status: str
) -> models.Subscriber | None:
    """리드 상태 업데이트"""
    sub = db.query(models.Subscriber).filter(models.Subscriber.id == subscriber_id).first()
    if sub:
        sub.status = status
        db.commit()
        db.refresh(sub)
    return sub


def update_subscriber_memo(
    db: Session, subscriber_id: int, memo: str
) -> models.Subscriber | None:
    """리드 메모 업데이트"""
    sub = db.query(models.Subscriber).filter(models.Subscriber.id == subscriber_id).first()
    if sub:
        sub.memo = memo
        db.commit()
        db.refresh(sub)
    return sub


def update_subscriber_keyword(
    db: Session, subscriber_id: int, keyword: str
) -> models.Subscriber | None:
    """리드 대표 키워드 업데이트"""
    sub = db.query(models.Subscriber).filter(models.Subscriber.id == subscriber_id).first()
    if sub:
        sub.selected_keyword = keyword
        db.commit()
        db.refresh(sub)
    return sub


def get_subscriber_keywords(db: Session, place_id: str) -> list[str]:
    """해당 매장의 분석 결과에서 키워드 목록 추출"""
    record = (
        db.query(models.AnalysisHistory)
        .filter(
            models.AnalysisHistory.place_id == place_id,
            models.AnalysisHistory.analysis_type == "place",
        )
        .order_by(models.AnalysisHistory.analyzed_at.desc())
        .first()
    )
    if not record or not record.result_json:
        return []
    try:
        data = json.loads(record.result_json)
        place_results = data.get("place_results", [])
        # 순위 있는 키워드 우선, 없으면 전체
        ranked = [p["keyword"] for p in place_results if p.get("rank")]
        if ranked:
            return ranked[:20]
        return [p["keyword"] for p in place_results][:20]
    except:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 사이트 방문 추적
# ─────────────────────────────────────────────────────────────────────────────

def record_site_visit(
    db: Session,
    anon_id: str | None = None,
    source: str | None = None,
    path: str | None = None
) -> models.SiteVisit:
    """사이트 방문 기록"""
    visit = models.SiteVisit(
        anon_id=anon_id,
        source=source,
        path=path
    )
    db.add(visit)
    db.commit()
    return visit


def get_total_visits(db: Session) -> int:
    """총 방문 횟수"""
    return db.query(models.SiteVisit).count()


def get_visits_this_week(db: Session) -> int:
    """이번주 방문 횟수"""
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    return db.query(models.SiteVisit).filter(
        models.SiteVisit.visited_at >= week_ago
    ).count()


def get_today_visits(db: Session) -> int:
    """오늘 방문 횟수"""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(models.SiteVisit).filter(
        models.SiteVisit.visited_at >= today
    ).count()


def get_visit_source_stats(db: Session) -> dict:
    """방문 유입경로별 통계 (레거시 영문/빈값 → 한글 라벨로 정규화해 병합)"""
    from sqlalchemy import func
    results = (
        db.query(models.SiteVisit.source, func.count(models.SiteVisit.id))
        .group_by(models.SiteVisit.source)
        .all()
    )
    LEGACY = {
        None: "직접유입", "": "직접유입", "direct": "직접유입",
        "blog": "블로그", "search": "네이버검색", "referrer": "기타",
        "chatgpt.com": "ChatGPT", "unknown": "기타",
    }
    stats = {}
    for source, count in results:
        key = LEGACY.get(source, source) or "직접유입"
        stats[key] = stats.get(key, 0) + count
    return stats


def get_daily_visits(db: Session, days: int = 30) -> list[dict]:
    """일별 방문 통계"""
    from sqlalchemy import func, cast, Date
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    results = (
        db.query(
            cast(models.SiteVisit.visited_at, Date).label("date"),
            func.count(models.SiteVisit.id).label("count")
        )
        .filter(models.SiteVisit.visited_at >= cutoff)
        .group_by(cast(models.SiteVisit.visited_at, Date))
        .order_by(cast(models.SiteVisit.visited_at, Date))
        .all()
    )
    return [{"date": str(r.date), "count": r.count} for r in results]


def get_revisit_stats(db: Session) -> dict:
    """재방문 통계 — anon_id 기준.
    - unique_visitors : 순 방문자(고유 anon_id) 수
    - returning_visitors : 서로 다른 날 2회 이상 방문한 재방문자 수
    - revisit_rate : 재방문자 / 순 방문자 (%)
    - returning_visits : 재방문(첫 방문일 이후) 총 건수
    """
    from sqlalchemy import func, cast, Date
    # anon_id별 (방문한 서로 다른 날 수, 총 방문 수)
    rows = (
        db.query(
            models.SiteVisit.anon_id,
            func.count(func.distinct(cast(models.SiteVisit.visited_at, Date))).label("days"),
            func.count(models.SiteVisit.id).label("visits"),
        )
        .filter(models.SiteVisit.anon_id.isnot(None))
        .group_by(models.SiteVisit.anon_id)
        .all()
    )
    unique = len(rows)
    returning = sum(1 for r in rows if r.days >= 2)
    returning_visits = sum((r.visits - 1) for r in rows if r.visits >= 2)
    rate = round(returning / unique * 100, 1) if unique else 0
    return {
        "unique_visitors": unique,
        "returning_visitors": returning,
        "revisit_rate": rate,
        "returning_visits": returning_visits,
    }


def get_daily_revisits(db: Session, days: int = 30) -> list[dict]:
    """일별 재방문 건수 — 각 방문의 anon_id가 그 날 '이전 날'에 이미 방문한 적 있으면 재방문으로 집계."""
    from sqlalchemy import func
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    # anon_id별 최초 방문 시각
    first_seen = dict(
        db.query(models.SiteVisit.anon_id, func.min(models.SiteVisit.visited_at))
        .filter(models.SiteVisit.anon_id.isnot(None))
        .group_by(models.SiteVisit.anon_id)
        .all()
    )
    visits = (
        db.query(models.SiteVisit.anon_id, models.SiteVisit.visited_at)
        .filter(
            models.SiteVisit.visited_at >= cutoff,
            models.SiteVisit.anon_id.isnot(None),
        )
        .all()
    )
    daily: dict[str, int] = {}
    for anon, vat in visits:
        fs = first_seen.get(anon)
        if fs and vat.date() > fs.date():  # 첫 방문일보다 뒤 → 재방문
            key = vat.date().isoformat()
            daily[key] = daily.get(key, 0) + 1
    return [{"date": k, "count": daily[k]} for k in sorted(daily)]


def get_subscribers_filtered(
    db: Session,
    search: str = "",
    status: str = "all",
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """필터가 적용된 구독자 목록 + 페이지네이션"""
    query = db.query(models.Subscriber)

    # 검색: 매장명 또는 전화번호
    if search:
        query = query.filter(
            (models.Subscriber.store_name.ilike(f"%{search}%")) |
            (models.Subscriber.phone.ilike(f"%{search}%"))
        )

    # 상태 필터
    if status != "all":
        query = query.filter(models.Subscriber.status == status)

    total = query.count()
    records = (
        query.order_by(models.Subscriber.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = []
    for s in records:
        # 분석 결과에서 지역/업종/키워드 추출
        region = ""
        category = ""
        keywords = []

        # store_url에서 URL만 추출 (매장명+주소+URL 혼합 입력 대응)
        import re
        raw_url = s.store_url or ""
        url_match = re.search(r'(https?://[^\s]+)', raw_url)
        place_url = url_match.group(1) if url_match else ""

        # place_id가 있으면 URL 생성
        if s.place_id and not place_url:
            place_url = f"https://m.place.naver.com/place/{s.place_id}"

        # place_id로 분석 기록 찾기, 없으면 store_name으로 폴백
        record = None
        if s.place_id:
            record = (
                db.query(models.AnalysisHistory)
                .filter(
                    models.AnalysisHistory.place_id == s.place_id,
                    models.AnalysisHistory.analysis_type == "place",
                )
                .order_by(models.AnalysisHistory.analyzed_at.desc())
                .first()
            )
        # place_id 없거나 분석 기록 없으면 store_name으로 찾기
        if not record and s.store_name:
            # 정확히 일치
            record = (
                db.query(models.AnalysisHistory)
                .filter(
                    models.AnalysisHistory.store_name == s.store_name,
                    models.AnalysisHistory.analysis_type == "place",
                )
                .order_by(models.AnalysisHistory.analyzed_at.desc())
                .first()
            )
            # 공백 제거 후 일치 (예: "배럴짐 대치점" vs "배럴짐대치점")
            if not record:
                store_name_no_space = s.store_name.replace(" ", "")
                all_records = (
                    db.query(models.AnalysisHistory)
                    .filter(models.AnalysisHistory.analysis_type == "place")
                    .order_by(models.AnalysisHistory.analyzed_at.desc())
                    .limit(500)
                    .all()
                )
                for r in all_records:
                    if r.store_name and r.store_name.replace(" ", "") == store_name_no_space:
                        record = r
                        break
        if record and record.result_json:
            try:
                data = json.loads(record.result_json)
                # 분석 기록에서 place_id 발견 시 place_url 생성
                if record.place_id and not place_url:
                    place_url = f"https://m.place.naver.com/place/{record.place_id}"
                address = data.get("address", "")
                # 지역: 주소에서 구/시/군 추출
                import re
                match = re.search(r'([\w]+구|[\w]+시|[\w]+군)', address)
                region = match.group(1) if match else ""
                if not region and address:
                    parts = address.split()
                    if len(parts) >= 2:
                        region = parts[1] if len(parts[1]) >= 2 else parts[0]
                # 업종: category에서 첫번째 항목 (콤마 또는 > 구분)
                cat_raw = data.get("category", "")
                if cat_raw:
                    # "카페,디저트" 또는 "음식점 > 한식" 형태 처리
                    if "," in cat_raw:
                        category = cat_raw.split(",")[0].strip()
                    elif ">" in cat_raw:
                        category = cat_raw.split(">")[0].strip()
                    else:
                        category = cat_raw.strip()
                # category 비어있으면 store_name에서 추론
                if not category:
                    category = _infer_category(s.store_name)
                # place_url이 result에 있으면 그걸 사용
                if data.get("place_url"):
                    place_url = data.get("place_url")
                # 키워드 목록 (순위 있는 것 우선)
                place_results = data.get("place_results", [])
                ranked = [p["keyword"] for p in place_results if p.get("rank")]
                keywords = ranked[:15] if ranked else [p["keyword"] for p in place_results][:15]
            except:
                pass

        # place_id 없어도 store_name에서 업종 추론
        if not category:
            category = _infer_category(s.store_name)

        # 지역 없으면 store_name에서 추론 (예: "배럴짐 대치점" → "대치")
        if not region:
            region = _infer_region(s.store_name)

        items.append({
            "id": s.id,
            "store_name": s.store_name,
            "phone": s.phone,
            "place_id": s.place_id,
            "place_url": place_url,
            "region": region,
            "category": category,
            "status": s.status or "new",
            "memo": s.memo or "",
            "selected_keyword": s.selected_keyword or "",
            "keywords": keywords,
            "alarm_on": s.alarm_on,
            "created_at": s.created_at.strftime("%m-%d") if s.created_at else None,
            "last_analyzed_at": s.last_analyzed_at.strftime("%m-%d") if s.last_analyzed_at else None,
        })

    return {"total": total, "items": items}


def get_subscriber_stores_status(db: Session) -> list[dict]:
    """구독자 매장들의 순위 변화 현황"""
    subs = db.query(models.Subscriber).filter(models.Subscriber.alarm_on == True).all()
    result = []

    for s in subs:
        if not s.place_id:
            continue

        place_url = s.store_url or f"https://m.place.naver.com/place/{s.place_id}"

        histories = (
            db.query(models.AnalysisHistory)
            .filter(
                models.AnalysisHistory.place_id == s.place_id,
                models.AnalysisHistory.analysis_type == "place",
            )
            .order_by(models.AnalysisHistory.analyzed_at.desc())
            .limit(2)
            .all()
        )

        keyword = s.selected_keyword or ""
        this_rank = None
        last_rank = None

        if histories:
            latest = histories[0]
            if latest.result_json:
                try:
                    data = json.loads(latest.result_json)
                    place_results = data.get("place_results", [])
                    if data.get("place_url"):
                        place_url = data.get("place_url")
                    if keyword:
                        for p in place_results:
                            if p.get("keyword") == keyword:
                                this_rank = p.get("rank")
                                break
                    if not this_rank:
                        ranked = [p for p in place_results if p.get("rank")]
                        if ranked:
                            best = min(ranked, key=lambda x: x["rank"])
                            keyword = best.get("keyword", "")
                            this_rank = best.get("rank")
                except:
                    pass

            if len(histories) > 1 and keyword:
                prev = histories[1]
                if prev.result_json:
                    try:
                        data = json.loads(prev.result_json)
                        for p in data.get("place_results", []):
                            if p.get("keyword") == keyword:
                                last_rank = p.get("rank")
                                break
                    except:
                        pass

        result.append({
            "store_name": s.store_name,
            "place_url": place_url,
            "keyword": keyword,
            "this_rank": this_rank,
            "last_rank": last_rank,
        })

    return result


def get_popular_stores(db: Session, limit: int = 10) -> list[dict]:
    """가장 많이 분석된 매장 TOP N"""
    from sqlalchemy import func

    results = (
        db.query(
            models.AnalysisHistory.place_id,
            models.AnalysisHistory.store_name,
            func.count().label("count"),
            func.max(models.AnalysisHistory.analyzed_at).label("last_analyzed"),
        )
        .filter(models.AnalysisHistory.place_id.isnot(None))
        .group_by(models.AnalysisHistory.place_id, models.AnalysisHistory.store_name)
        .order_by(func.count().desc())
        .limit(limit)
        .all()
    )

    items = []
    for i, r in enumerate(results):
        region = ""
        category = ""
        place_url = f"https://m.place.naver.com/place/{r.place_id}" if r.place_id else ""
        record = (
            db.query(models.AnalysisHistory)
            .filter(models.AnalysisHistory.place_id == r.place_id)
            .order_by(models.AnalysisHistory.analyzed_at.desc())
            .first()
        )
        if record and record.result_json:
            try:
                data = json.loads(record.result_json)
                address = data.get("address", "")
                import re
                match = re.search(r'([\w]+구|[\w]+시|[\w]+군)', address)
                region = match.group(1) if match else ""
                if not region and address:
                    parts = address.split()
                    if len(parts) >= 2:
                        region = parts[1] if len(parts[1]) >= 2 else parts[0]
                cat_raw = data.get("category", "")
                if cat_raw:
                    if "," in cat_raw:
                        category = cat_raw.split(",")[0].strip()
                    elif ">" in cat_raw:
                        category = cat_raw.split(">")[0].strip()
                    else:
                        category = cat_raw.strip()
                # category 비어있으면 store_name에서 추론
                if not category:
                    category = _infer_category(r.store_name)
                if data.get("place_url"):
                    place_url = data.get("place_url")
            except:
                pass

        # record 없거나 category 비어있으면 store_name에서 추론
        if not category:
            category = _infer_category(r.store_name)

        items.append({
            "rank": i + 1,
            "store_name": r.store_name,
            "place_url": place_url,
            "region": region,
            "category": category,
            "count": r.count,
            "last_analyzed": r.last_analyzed.strftime("%m-%d") if r.last_analyzed else "",
        })

    return items


def _infer_region(store_name: str) -> str:
    """store_name에서 지역 추론 (공통 헬퍼)"""
    import re
    sn = store_name or ""
    # "OO점" 패턴에서 지역 추출 (예: 대치점 → 대치, 강남역점 → 강남역)
    match = re.search(r'([가-힣]{2,4})점$', sn)
    if match:
        return match.group(1)
    # "OO지점" 패턴
    match = re.search(r'([가-힣]{2,4})지점$', sn)
    if match:
        return match.group(1)
    # "OO구" 또는 "OO동" 패턴
    match = re.search(r'([가-힣]+(?:구|동|시|군))', sn)
    if match:
        return match.group(1)
    return ""


def _infer_category(store_name: str) -> str:
    """store_name에서 업종 추론 (공통 헬퍼)"""
    sn = store_name or ""
    # 순서 중요: 더 구체적인 것 먼저 체크
    if "카페" in sn or "커피" in sn:
        return "카페"
    elif any(x in sn for x in ["음악", "피아노", "기타", "드럼", "보컬"]):
        return "음악학원"
    elif any(x in sn for x in ["식당", "국밥", "고기", "육식", "삼겹", "갈비", "맛집", "식육", "정육", "푸고"]):
        return "음식점"
    elif any(x in sn for x in ["피부", "메디컬", "스킨", "에스테틱"]):
        return "피부관리"
    elif any(x in sn for x in ["성형", "의원", "병원", "클리닉", "치과", "한의원"]):
        return "병원"
    elif any(x in sn for x in ["헬스", "피트니스", "짐", "PT", "필라테스", "요가", "휘트니스", "배럴", "크로스핏", "트레이닝"]):
        return "피트니스"
    elif any(x in sn for x in ["학원", "교육", "영재", "수학", "영어", "사고력", "논술", "독서", "미술", "태권도", "합기도", "검도", "주짓수"]):
        return "학원"
    elif any(x in sn for x in ["호텔", "펫", "애견", "고양이", "캣", "동물"]):
        return "펫서비스"
    elif any(x in sn for x in ["미용", "헤어", "네일", "뷰티", "왁싱", "속눈썹", "반영구", "타투"]):
        return "뷰티"
    elif any(x in sn for x in ["골프", "스크린", "당구", "볼링", "탁구", "테니스", "배드민턴", "클라이밍", "스쿼시"]):
        return "스포츠시설"
    elif any(x in sn for x in ["공방", "클래스", "원데이", "도예", "가죽", "캔들", "플라워", "꽃", "베이킹", "쿠킹"]):
        return "공방·클래스"
    elif any(x in sn for x in ["청소", "이사", "세차", "인테리어", "시공", "수리", "설비", "도배", "철거", "방역"]):
        return "생활서비스"
    elif any(x in sn for x in ["스튜디오", "사진", "포토", "촬영", "영상"]):
        return "사진·스튜디오"
    elif any(x in sn for x in ["부동산", "공인중개", "중개"]):
        return "부동산"
    elif any(x in sn for x in ["향수", "공예", "소품", "편집샵", "쇼룸"]):
        return "소매·쇼룸"
    return "기타"


def get_category_stats(db: Session, limit: int = 10) -> list[dict]:
    """업종별 분석 통계"""
    records = (
        db.query(models.AnalysisHistory)
        .filter(models.AnalysisHistory.result_json.isnot(None))
        .order_by(models.AnalysisHistory.analyzed_at.desc())
        .limit(500)
        .all()
    )

    category_count = {}
    for r in records:
        try:
            data = json.loads(r.result_json)
            cat = data.get("category", "")
            if cat:
                main_cat = cat.split(",")[0].strip()
            else:
                # store_name에서 추론
                main_cat = _infer_category(r.store_name)
            if main_cat:
                category_count[main_cat] = category_count.get(main_cat, 0) + 1
        except:
            pass

    sorted_cats = sorted(category_count.items(), key=lambda x: x[1], reverse=True)[:limit]
    total = sum(c for _, c in sorted_cats)

    return [{"category": cat, "count": cnt, "percent": round(cnt / total * 100) if total else 0} for cat, cnt in sorted_cats]


def get_region_stats(db: Session, limit: int = 10) -> list[dict]:
    """지역별 분석 통계"""
    import re

    records = (
        db.query(models.AnalysisHistory)
        .filter(models.AnalysisHistory.result_json.isnot(None))
        .order_by(models.AnalysisHistory.analyzed_at.desc())
        .limit(500)
        .all()
    )

    region_count = {}
    for r in records:
        try:
            data = json.loads(r.result_json)
            address = data.get("address", "")
            match = re.search(r'([\w]+구|[\w]+시|[\w]+군)', address)
            if match:
                region = match.group(1)
                region_count[region] = region_count.get(region, 0) + 1
        except:
            pass

    sorted_regions = sorted(region_count.items(), key=lambda x: x[1], reverse=True)[:limit]
    total = sum(c for _, c in sorted_regions)

    return [{"region": reg, "count": cnt, "percent": round(cnt / total * 100) if total else 0} for reg, cnt in sorted_regions]


# ─────────────────────────────────────────────────────────────────────────────
# 전환율 퍼널 + 기간 비교
# ─────────────────────────────────────────────────────────────────────────────

def get_funnel_stats(db: Session) -> dict:
    """전환율 퍼널 통계 (전체 기간)"""
    total_visits = db.query(models.SiteVisit).count()
    total_analyses = db.query(models.AnalysisHistory).count()
    total_leads = db.query(models.Subscriber).count()

    visit_to_analysis = round(total_analyses / total_visits * 100, 1) if total_visits else 0
    analysis_to_lead = round(total_leads / total_analyses * 100, 1) if total_analyses else 0

    return {
        "visits": total_visits,
        "analyses": total_analyses,
        "leads": total_leads,
        "visit_to_analysis_rate": visit_to_analysis,
        "analysis_to_lead_rate": analysis_to_lead,
    }


def get_week_comparison(db: Session) -> dict:
    """이번주 vs 지난주 비교 (월~일 기준)"""
    now = datetime.now(timezone.utc)
    # 이번주 월요일 00:00
    days_since_monday = now.weekday()
    this_week_start = (now - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    last_week_start = this_week_start - timedelta(days=7)
    last_week_end = this_week_start

    # 이번주
    this_visits = db.query(models.SiteVisit).filter(
        models.SiteVisit.visited_at >= this_week_start
    ).count()
    this_analyses = db.query(models.AnalysisHistory).filter(
        models.AnalysisHistory.analyzed_at >= this_week_start
    ).count()
    this_leads = db.query(models.Subscriber).filter(
        models.Subscriber.created_at >= this_week_start
    ).count()

    # 지난주
    last_visits = db.query(models.SiteVisit).filter(
        models.SiteVisit.visited_at >= last_week_start,
        models.SiteVisit.visited_at < last_week_end
    ).count()
    last_analyses = db.query(models.AnalysisHistory).filter(
        models.AnalysisHistory.analyzed_at >= last_week_start,
        models.AnalysisHistory.analyzed_at < last_week_end
    ).count()
    last_leads = db.query(models.Subscriber).filter(
        models.Subscriber.created_at >= last_week_start,
        models.Subscriber.created_at < last_week_end
    ).count()

    def calc_change(this_val, last_val):
        if last_val == 0:
            return {"value": this_val - last_val, "percent": None}
        pct = round((this_val - last_val) / last_val * 100)
        return {"value": this_val - last_val, "percent": pct}

    return {
        "this_week": {"visits": this_visits, "analyses": this_analyses, "leads": this_leads},
        "last_week": {"visits": last_visits, "analyses": last_analyses, "leads": last_leads},
        "change": {
            "visits": calc_change(this_visits, last_visits),
            "analyses": calc_change(this_analyses, last_analyses),
            "leads": calc_change(this_leads, last_leads),
        }
    }
