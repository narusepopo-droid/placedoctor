import re
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from .database import engine, get_db
from .models import Base
from . import crud, schemas
from .core.scraper import diagnose_store

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="플레이스닥터 API",
    description="네이버 플레이스 순위 진단 서비스",
    version="0.3.0",
)


def _extract_place_id(url: str) -> str | None:
    m = re.search(r"\d{8,11}", url)
    return m.group(0) if m else None


@app.get("/health", tags=["시스템"])
def health():
    return {"status": "ok"}


@app.post("/diagnose", response_model=schemas.DiagnoseResponse, tags=["진단"])
async def diagnose(req: schemas.DiagnoseRequest, db: Session = Depends(get_db)):
    """
    매장명과 네이버 플레이스 URL을 받아 진단 결과를 반환합니다.
    24시간 이내 동일 매장 결과가 있으면 DB 캐시를 반환합니다.
    force_refresh=true 로 강제 재크롤링 가능합니다.
    """
    place_id = _extract_place_id(req.place_url)

    if place_id and not req.force_refresh:
        cached = crud.get_cached_result(db, place_id)
        if cached:
            cached["cached"] = True
            return cached

    result = await diagnose_store(req.store_name, req.place_url)
    crud.save_diagnosis(db, result, req.place_url)
    result["cached"] = False
    return result


@app.get("/store/{place_id}/history", tags=["진단"])
def get_history(place_id: str, db: Session = Depends(get_db)):
    """매장의 순위·점수 스냅샷 이력을 반환합니다."""
    history = crud.get_store_history(db, place_id)
    if not history:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다")
    return history


@app.post("/lead", response_model=schemas.LeadResponse, tags=["리드"])
def create_lead(req: schemas.LeadRequest, db: Session = Depends(get_db)):
    """연락처(리드)를 저장합니다."""
    lead = crud.create_lead(db, contact=req.contact, source=req.source, store_id=req.store_id)
    return lead
