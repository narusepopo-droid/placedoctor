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
