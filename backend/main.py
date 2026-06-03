import asyncio
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .database import engine, get_db
from .models import Base
from . import crud, schemas
from .core.scraper import diagnose_store

# uvicorn --reload 모드에서는 모듈 import 전에 SelectorEventLoop가 이미 생성됨.
# 따라서 Playwright(subprocess)는 별도 스레드의 ProactorEventLoop에서 실행.
_playwright_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="playwright")


def _diagnose_sync(store_name: str, place_url: str) -> dict:
    """Playwright 진단을 전용 스레드에서 실행. 전체 트레이스백 포함."""
    import traceback as _tb
    try:
        if sys.platform == "win32":
            # loop_factory=ProactorEventLoop: Python 3.12+ 공식 방법
            return asyncio.run(
                diagnose_store(store_name, place_url),
                loop_factory=asyncio.ProactorEventLoop,
            )
        return asyncio.run(diagnose_store(store_name, place_url))
    except Exception as exc:
        # 전체 스택 트레이스를 RuntimeError 메시지에 포함
        raise RuntimeError(_tb.format_exc()) from exc

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="플레이스닥터 API",
    description="네이버 플레이스 순위 진단 서비스",
    version="0.4.0",
)

_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>플레이스닥터</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 860px; margin: 40px auto; padding: 0 20px; color: #222; }
  h1 { font-size: 1.5rem; margin-bottom: 4px; }
  .sub { color: #666; font-size: 0.9rem; margin-bottom: 24px; }
  label { display: block; margin-top: 14px; font-weight: 600; font-size: 0.9rem; }
  input[type=text] {
    width: 100%; padding: 9px 12px; margin-top: 5px; box-sizing: border-box;
    border: 1px solid #ccc; border-radius: 6px; font-size: 0.95rem;
  }
  input[type=text]:focus { outline: none; border-color: #03c75a; }
  .row { display: flex; align-items: center; gap: 16px; margin-top: 18px; }
  button {
    padding: 10px 28px; background: #03c75a; color: white;
    border: none; border-radius: 6px; cursor: pointer; font-size: 1rem; font-weight: 600;
  }
  button:disabled { background: #aaa; cursor: not-allowed; }
  .chk-lbl { font-size: 0.85rem; color: #555; display: flex; align-items: center; gap: 6px; }
  #status { margin-top: 12px; color: #555; font-size: 0.9rem; min-height: 20px; }
  #result { margin-top: 20px; }
  .card { background: #f7f7f7; border-radius: 10px; padding: 18px 20px; margin-top: 14px; }
  .card h3 { margin: 0 0 12px 0; font-size: 1rem; color: #333; }
  .scores { display: flex; gap: 10px; flex-wrap: wrap; }
  .sc { background: white; border-radius: 8px; padding: 14px 18px; text-align: center; min-width: 80px; }
  .sc .val { font-size: 2rem; font-weight: 700; }
  .sc .lbl { font-size: 0.75rem; color: #888; margin-top: 2px; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: 6px 4px; border-bottom: 1px solid #eee; font-size: 0.88rem; }
  th { color: #666; font-weight: 600; width: 130px; }
  .hit { color: #03c75a; font-weight: 600; }
  .miss { color: #bbb; }
  .err { color: #cc3300; background: #fff0ed; border-radius: 6px; padding: 12px 16px; }
  .badge-cached { background: #e0e0e0; color: #555; border-radius: 4px; font-size: 0.75rem; padding: 2px 6px; }
</style>
</head>
<body>
<h1>플레이스닥터</h1>
<p class="sub">네이버 플레이스 순위 진단 도구 (테스트용)</p>

<label>매장명</label>
<input type="text" id="storeName" placeholder="예: 감동식당" />
<label>네이버 플레이스 URL</label>
<input type="text" id="placeUrl" placeholder="https://naver.me/... 또는 https://map.naver.com/p/entry/place/..." />

<div class="row">
  <button id="btn" onclick="diagnose()">진단하기</button>
  <label class="chk-lbl">
    <input type="checkbox" id="force"> 강제 재크롤링
  </label>
</div>
<div id="status"></div>
<div id="result"></div>

<script>
async function diagnose() {
  const storeName = document.getElementById('storeName').value.trim();
  const placeUrl  = document.getElementById('placeUrl').value.trim();
  const force     = document.getElementById('force').checked;
  if (!storeName || !placeUrl) { alert('매장명과 URL을 모두 입력해주세요.'); return; }

  const btn = document.getElementById('btn');
  const status = document.getElementById('status');
  const out = document.getElementById('result');
  btn.disabled = true;
  btn.textContent = '진단 중...';
  status.textContent = '진단 중입니다. 키워드 수에 따라 1~3분 소요됩니다.';
  out.innerHTML = '';

  try {
    const res = await fetch('/diagnose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ store_name: storeName, place_url: placeUrl, force_refresh: force })
    });
    const text = await res.text();
    if (!res.ok) {
      out.innerHTML = `<div class="err"><b>오류 (${res.status})</b><br><pre style="white-space:pre-wrap;margin:8px 0 0">${escHtml(text)}</pre></div>`;
      return;
    }
    renderResult(JSON.parse(text));
  } catch(e) {
    out.innerHTML = `<div class="err">요청 실패: ${escHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '진단하기';
    status.textContent = '';
  }
}

function renderResult(d) {
  const sc = d.scores || {};
  const comp = d.competitor || {};
  const compD = comp.details || {};
  const gap = comp.gap || {};
  const results = d.place_results || [];
  const hits = results.filter(r => r.rank).length;

  const scoreHtml = ['SEO','콘텐츠','활성도','종합'].map((lbl, i) => {
    const val = [sc.seo, sc.content, sc.activity, sc.total][i];
    const color = val == null ? '#999' : val >= 70 ? '#03c75a' : val >= 40 ? '#e09000' : '#cc3300';
    return `<div class="sc"><div class="val" style="color:${color}">${val ?? '-'}</div><div class="lbl">${lbl}</div></div>`;
  }).join('');

  const badge = d.cached ? ' <span class="badge-cached">캐시</span>' : '';
  const infoRows = [
    ['매장명', d.store_name],
    ['place_id', (d.place_id || '-') + badge],
    ['카테고리', d.category || '-'],
    ['주소', d.address || '-'],
    ['방문자 리뷰', fmt(d.visitor_reviews)],
    ['블로그 리뷰', fmt(d.blog_reviews)],
    ['별점', d.star_score != null ? d.star_score : '-'],
    ['사진 수', fmt(d.photo_count)],
    ['최근 리뷰', d.latest_review_date || '-'],
  ].map(([k,v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join('');

  const kwRows = results.map(r =>
    `<tr class="${r.rank ? 'hit' : 'miss'}"><td>${escHtml(r.keyword)}</td><td>${r.rank ? r.rank + '위' : '-'}</td></tr>`
  ).join('');

  const compHtml = comp.competitor_id ? `<table>
    <tr><th>경쟁사 ID</th><td>${comp.competitor_id}</td></tr>
    <tr><th>경쟁사 순위</th><td>${comp.competitor_rank != null ? comp.competitor_rank + '위' : '-'}</td></tr>
    <tr><th>우리 순위</th><td>${comp.my_rank != null ? comp.my_rank + '위' : '-'}</td></tr>
    <tr><th>경쟁사 방문자</th><td>${fmt(compD.visitor_reviews)}</td></tr>
    <tr><th>방문자 격차</th><td>${gap.visitor_reviews != null ? (gap.visitor_reviews > 0 ? '+' : '') + gap.visitor_reviews : '-'}</td></tr>
    <tr><th>경쟁사 블로그</th><td>${fmt(compD.blog_reviews)}</td></tr>
    <tr><th>블로그 격차</th><td>${gap.blog_reviews != null ? (gap.blog_reviews > 0 ? '+' : '') + gap.blog_reviews : '-'}</td></tr>
  </table>` : '<p style="color:#999;margin:0">경쟁사 정보 없음</p>';

  document.getElementById('result').innerHTML = `
    <div class="card"><h3>진단 점수</h3><div class="scores">${scoreHtml}</div></div>
    <div class="card"><h3>매장 정보</h3><table>${infoRows}</table></div>
    <div class="card"><h3>키워드 순위 (${hits}/${results.length} 노출)</h3>
      <table><tr><th>키워드</th><th>순위</th></tr>${kwRows}</table></div>
    <div class="card"><h3>경쟁사 비교</h3>${compHtml}</div>
  `;
}

function fmt(n) { return n != null ? n.toLocaleString() : '-'; }
function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

document.getElementById('placeUrl').addEventListener('keydown', e => {
  if (e.key === 'Enter') diagnose();
});
</script>
</body>
</html>"""


def _extract_place_id(url: str) -> str | None:
    m = re.search(r"\d{8,11}", url)
    return m.group(0) if m else None


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return HTMLResponse(_HTML)


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

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _playwright_executor,
            partial(_diagnose_sync, req.store_name, req.place_url),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        crud.save_diagnosis(db, result, req.place_url)
    except Exception as e:
        # DB 저장 실패해도 결과는 반환
        import logging
        logging.getLogger(__name__).warning(f"DB 저장 실패: {e}")

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
