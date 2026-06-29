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
    """알림 구독 신청 (동일 anon_id+place_id면 업데이트)"""
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

    return {
        "total_analyses": total_analyses,
        "registered_stores": registered_stores,
        "subscriber_count": subscriber_count,
        "new_subscribers_week": new_subscribers_week,
        "new_analyses_week": new_analyses_week,
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
                # 지역: 주소에서 구/동 추출
                import re
                match = re.search(r'([\w]+구|[\w]+시|[\w]+군)', address)
                region = match.group(1) if match else address[:10] if address else ""
                category = data.get("category", "")
                place_url = data.get("place_url", "")
                if not place_url and r.place_id:
                    place_url = f"https://m.place.naver.com/place/{r.place_id}"
            except:
                pass
        items.append({
            "id": r.id,
            "store_name": r.store_name,
            "analysis_type": r.analysis_type,
            "region": region,
            "category": category,
            "place_url": place_url,
            "total_score": r.total_score,
            "source": r.source,
            "analyzed_at": r.analyzed_at.isoformat() if r.analyzed_at else None,
        })

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
        place_url = s.store_url or ""

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
            if record and record.result_json:
                try:
                    data = json.loads(record.result_json)
                    address = data.get("address", "")
                    # 지역: 주소에서 구/동 추출
                    import re
                    match = re.search(r'([\w]+구|[\w]+시|[\w]+군)', address)
                    region = match.group(1) if match else address[:10] if address else ""
                    category = data.get("category", "")
                    # 키워드 목록 (순위 있는 것 우선)
                    place_results = data.get("place_results", [])
                    ranked = [p["keyword"] for p in place_results if p.get("rank")]
                    keywords = ranked[:15] if ranked else [p["keyword"] for p in place_results][:15]
                except:
                    pass

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
                category = data.get("category", "")
            except:
                pass

        items.append({
            "rank": i + 1,
            "store_name": r.store_name,
            "region": region,
            "category": category,
            "count": r.count,
            "last_analyzed": r.last_analyzed.strftime("%m-%d") if r.last_analyzed else "",
        })

    return items


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
