import asyncio
import re
import sys
import threading

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .database import engine, get_db
from .models import Base
from . import crud, schemas
from .core.scraper import diagnose_store, analyze_blog_ranking
from .core.scoring import apply_ad_flags

# ── Windows ProactorEventLoop 전용 스레드 ────────────────────────────────────
# uvicorn --reload 모드에서는 SelectorEventLoop를 강제하므로
# Playwright subprocess 호출이 실패한다.
# test_scraper.py는 asyncio.run()을 직접 쓰기 때문에 ProactorEventLoop가 생성됨.
# 동일한 방식으로: 영구 데몬 스레드에서 ProactorEventLoop를 실행하고
# asyncio.run_coroutine_threadsafe()로 코루틴을 위임한다.

_proactor_loop: asyncio.AbstractEventLoop | None = None
_proactor_ready = threading.Event()

def _proactor_thread_main():
    global _proactor_loop
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _proactor_loop = loop
    _proactor_ready.set()
    loop.run_forever()

_t = threading.Thread(target=_proactor_thread_main, daemon=True, name="proactor-playwright")
_t.start()
_proactor_ready.wait()  # 루프가 준비될 때까지 대기

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
<title>플레이스닥터 — 네이버 플레이스 무료 진단</title>
<style>
:root{
  --green:#03c75a;--green-d:#02a84d;--green-bg:#f0fdf6;
  --red:#ef4444;--orange:#f97316;--score-green:#22c55e;
  --gray-50:#f9fafb;--gray-100:#f3f4f6;--gray-200:#e5e7eb;
  --gray-400:#9ca3af;--gray-600:#4b5563;--gray-800:#1f2937;--gray-900:#111827;
  --radius:16px;--shadow:0 2px 16px rgba(0,0,0,.08);
}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f9fafb;color:var(--gray-900);min-height:100vh;}

/* HEADER */
.header{background:var(--green);padding:14px 20px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;box-shadow:0 2px 8px rgba(3,199,90,.3);}
.logo{display:flex;align-items:center;gap:8px;color:#fff;font-size:1.2rem;font-weight:800;letter-spacing:-.3px;}
.logo-icon{font-size:1.4rem;}
.header-badge{background:rgba(255,255,255,.25);color:#fff;font-size:.72rem;font-weight:700;padding:3px 10px;border-radius:20px;}

/* MAIN */
.main{max-width:520px;margin:0 auto;padding:20px 16px 100px;}

/* INPUT CARD */
.input-card{background:#fff;border-radius:var(--radius);box-shadow:var(--shadow);padding:24px 20px;}
.input-card h2{font-size:1.15rem;font-weight:700;margin-bottom:4px;}
.input-card p{font-size:.85rem;color:var(--gray-600);margin-bottom:20px;}
.field{margin-bottom:14px;}
.field label{display:block;font-size:.82rem;font-weight:600;color:var(--gray-800);margin-bottom:6px;}
.field input{width:100%;padding:12px 14px;border:1.5px solid var(--gray-200);border-radius:10px;font-size:.95rem;outline:none;transition:border .2s;}
.field input:focus{border-color:var(--green);}
.btn-diagnose{width:100%;padding:14px;background:var(--green);color:#fff;border:none;border-radius:10px;font-size:1rem;font-weight:700;cursor:pointer;transition:background .2s;margin-top:4px;}
.btn-diagnose:hover{background:var(--green-d);}
.btn-diagnose:disabled{background:var(--gray-400);cursor:not-allowed;}
.status-msg{text-align:center;color:var(--gray-600);font-size:.85rem;margin-top:12px;min-height:20px;}

/* RESULT */
#result{display:none;}
.result-header{text-align:center;padding:24px 0 8px;}
.store-badge{display:inline-flex;align-items:center;gap:6px;background:var(--green-bg);color:var(--green-d);font-size:.8rem;font-weight:600;padding:4px 12px;border-radius:20px;margin-bottom:10px;}
.store-name{font-size:1.4rem;font-weight:800;margin-bottom:4px;}
.store-meta{font-size:.82rem;color:var(--gray-600);}

/* GAUGE CARD */
.card{background:#fff;border-radius:var(--radius);box-shadow:var(--shadow);padding:22px 20px;margin-top:14px;}
.card-title{font-size:.82rem;font-weight:700;color:var(--gray-600);text-transform:uppercase;letter-spacing:.5px;margin-bottom:16px;}
.gauge-wrap{display:flex;flex-direction:column;align-items:center;gap:12px;}
.gauge-svg{overflow:visible;}
.gauge-track{fill:none;stroke:var(--gray-100);stroke-width:12;}
.gauge-fill{fill:none;stroke-width:12;stroke-linecap:round;transition:stroke-dasharray 1.2s cubic-bezier(.4,0,.2,1),stroke .4s;transform:rotate(-90deg);transform-origin:50% 50%;}
.gauge-text{font-size:2.2rem;font-weight:800;text-anchor:middle;dominant-baseline:middle;}
.gauge-sub{font-size:.9rem;fill:var(--gray-600);text-anchor:middle;}
.grade-badge{font-size:1rem;font-weight:700;padding:6px 18px;border-radius:20px;color:#fff;}
.gauge-summary{font-size:.88rem;color:var(--gray-600);text-align:center;max-width:260px;}

/* 4-AXIS CARDS */
.axis-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px;}
@media(max-width:380px){.axis-grid{grid-template-columns:1fr;}}
@media(min-width:640px){.axis-grid{grid-template-columns:1fr 1fr;}}
.axis-card{background:#fff;border-radius:var(--radius);box-shadow:var(--shadow);padding:18px 16px;}
.axis-head{display:flex;align-items:center;gap:8px;margin-bottom:12px;}
.axis-icon{font-size:1.3rem;}
.axis-name{font-size:.82rem;font-weight:700;color:var(--gray-600);}
.axis-score{font-size:1.8rem;font-weight:800;margin-bottom:6px;}
.progress-bar{height:6px;background:var(--gray-100);border-radius:4px;overflow:hidden;margin-bottom:14px;}
.progress-fill{height:100%;border-radius:4px;transition:width 1s ease;}
.detail-list{display:flex;flex-direction:column;gap:8px;}
.detail-row{display:flex;justify-content:space-between;align-items:center;}
.ad-check-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;}
.ad-check{display:flex;align-items:center;gap:6px;font-size:.82rem;color:var(--gray-800);background:var(--gray-50,#f9fafb);border:1px solid var(--gray-200);border-radius:8px;padding:9px 10px;cursor:pointer;}
.ad-check input{accent-color:var(--green);}
.detail-label{font-size:.78rem;color:var(--gray-600);}
.detail-val{display:flex;align-items:center;gap:4px;}
.detail-num{font-size:.8rem;font-weight:600;}
.chip{font-size:.68rem;font-weight:700;padding:2px 7px;border-radius:8px;color:#fff;}
.chip-good{background:var(--score-green);}
.chip-ok{background:var(--orange);}
.chip-bad{background:var(--red);}

/* COMPETITOR */
.comp-rows{display:flex;flex-direction:column;gap:14px;}
.comp-label{font-size:.8rem;font-weight:600;color:var(--gray-600);margin-bottom:4px;}
.comp-bar-wrap{display:flex;align-items:center;gap:10px;}
.comp-tag{font-size:.75rem;font-weight:700;width:44px;flex-shrink:0;}
.comp-bar-bg{flex:1;height:22px;background:var(--gray-100);border-radius:6px;overflow:hidden;}
.comp-bar{height:100%;border-radius:6px;display:flex;align-items:center;padding-left:8px;transition:width 1s ease;font-size:.75rem;font-weight:700;color:#fff;white-space:nowrap;min-width:32px;}
.comp-gap{margin-top:8px;font-size:.82rem;color:var(--red);font-weight:600;}

/* KEYWORDS */
.kw-list{display:flex;flex-direction:column;gap:8px;}
.kw-item{background:var(--gray-50);border:1px solid var(--gray-200);border-radius:10px;padding:10px 12px;}
.kw-main{display:flex;align-items:center;gap:10px;}
.kw-rank-col{font-size:1.5rem;font-weight:800;min-width:46px;text-align:center;line-height:1.1;flex-shrink:0;}
.kw-divider{width:1px;background:var(--gray-200);align-self:stretch;flex-shrink:0;}
.kw-info{flex:1;min-width:0;}
.kw-title-row{display:flex;align-items:center;gap:5px;flex-wrap:wrap;}
.kw-text{font-size:.82rem;font-weight:600;min-width:0;}
.kw-count{font-size:.7rem;color:var(--gray-400);white-space:nowrap;flex-shrink:0;}
.kw-grade-badge{font-size:.68rem;font-weight:700;padding:2px 7px;border-radius:5px;white-space:nowrap;flex-shrink:0;margin-left:auto;}
.kw-sub{font-size:.73rem;color:var(--gray-500);margin-top:3px;line-height:1.5;}
.kw-more{margin-top:10px;font-size:.82rem;color:var(--green-d);font-weight:600;cursor:pointer;}

/* BLOG ANALYSIS */
.blog-list{display:flex;flex-direction:column;gap:10px;}
.blog-kw-group{background:var(--gray-50);border:1px solid var(--gray-200);border-radius:10px;padding:12px;}
.blog-kw-title{font-size:.85rem;font-weight:700;color:var(--gray-800);margin-bottom:8px;display:flex;align-items:center;gap:6px;}
.blog-kw-title .blog-kw-badge{font-size:.68rem;font-weight:600;padding:2px 8px;border-radius:5px;background:var(--green);color:#fff;}
.blog-hits{display:flex;flex-direction:column;gap:6px;}
.blog-hit{display:flex;align-items:center;gap:8px;padding:6px 8px;background:#fff;border-radius:6px;border:1px solid var(--gray-200);}
.blog-rank{font-size:1.1rem;font-weight:800;min-width:36px;text-align:center;flex-shrink:0;}
.blog-info{flex:1;min-width:0;}
.blog-title{font-size:.78rem;font-weight:600;color:var(--gray-800);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.blog-link{font-size:.68rem;color:var(--gray-400);text-decoration:none;}
.blog-link:hover{color:var(--green);text-decoration:underline;}
.blog-none{font-size:.78rem;color:var(--gray-400);padding:6px 0;text-align:center;}
.blog-summary{margin-top:12px;padding:10px;background:var(--green-bg);border-radius:8px;}
.blog-summary-text{font-size:.82rem;color:var(--gray-700);line-height:1.5;}

/* DOCTOR COMMENT */
.comment-box{background:var(--green-bg);border-left:4px solid var(--green);border-radius:0 var(--radius) var(--radius) 0;padding:16px;margin-top:6px;}
.comment-line{font-size:.88rem;color:var(--gray-800);line-height:1.65;margin-bottom:6px;}
.comment-line:last-child{margin-bottom:0;}

/* BUTTONS */
.btn-area{display:flex;flex-direction:column;gap:10px;margin-top:14px;}
.btn-main{padding:15px;background:var(--green);color:#fff;border:none;border-radius:12px;font-size:1rem;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;}
.btn-secondary{padding:13px;background:#fff;color:var(--gray-800);border:1.5px solid var(--gray-200);border-radius:12px;font-size:.9rem;font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;}
.btn-row{display:flex;gap:8px;}
.btn-row .btn-secondary{flex:1;}
.bottom-note{text-align:center;font-size:.75rem;color:var(--gray-400);margin-top:10px;}

/* RE-DIAGNOSE */
.btn-redo{margin-top:16px;text-align:center;}
.btn-redo button{background:none;border:none;color:var(--gray-400);font-size:.82rem;cursor:pointer;text-decoration:underline;}

/* ERR */
.err-box{background:#fff5f5;border:1px solid #fecaca;border-radius:12px;padding:16px;margin-top:12px;font-size:.85rem;color:#b91c1c;}

/* LOADING */
#loading-section{display:none;}
.l-card{background:#fff;border-radius:var(--radius);box-shadow:0 8px 32px rgba(3,199,90,.15),var(--shadow);padding:32px 20px;text-align:center;border-top:4px solid var(--green);}
.l-pulse{font-size:3rem;display:block;margin-bottom:14px;animation:lpulse 1.4s ease-in-out infinite;}
@keyframes lpulse{0%,100%{transform:scale(1);}50%{transform:scale(1.14);}}
.l-title{font-size:1.15rem;font-weight:700;margin-bottom:4px;}
.l-sub{font-size:.82rem;color:var(--gray-400);margin-bottom:22px;}
.l-bar-wrap{height:8px;background:var(--gray-100);border-radius:4px;overflow:hidden;margin-bottom:6px;}
.l-bar{height:100%;background:var(--green);border-radius:4px;width:0%;transition:width .7s ease;}
.l-pct{font-size:.78rem;color:var(--green-d);text-align:right;margin-bottom:22px;font-weight:700;}
.l-steps{text-align:left;display:flex;flex-direction:column;gap:11px;margin-bottom:20px;}
.l-step{display:flex;align-items:flex-start;gap:10px;}
.l-ic{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.85rem;flex-shrink:0;transition:all .3s;}
.l-ic.done{background:var(--green);}
.l-ic.active{background:var(--green-bg);border:2px solid var(--green);}
.l-ic.pending{background:var(--gray-100);filter:grayscale(1);opacity:.5;}
.l-body{padding-top:5px;}
.l-name{font-size:.88rem;font-weight:600;transition:color .3s;}
.l-name.done{color:var(--green);}
.l-name.active{color:var(--gray-900);}
.l-name.pending{color:var(--gray-400);}
.l-desc{font-size:.74rem;color:var(--gray-400);margin-top:2px;line-height:1.4;}
.dots{display:inline-flex;gap:2px;margin-left:3px;vertical-align:middle;}
.dots span{display:inline-block;width:4px;height:4px;border-radius:50%;background:var(--green);animation:db .65s ease-in-out infinite;}
.dots span:nth-child(2){animation-delay:.13s;}
.dots span:nth-child(3){animation-delay:.26s;}
@keyframes db{0%,100%{transform:translateY(0);}50%{transform:translateY(-5px);}}
.l-tip{background:var(--green-bg);border-radius:10px;padding:12px 14px;font-size:.8rem;color:#374151;line-height:1.6;text-align:left;}

/* TABS */
.tabs{display:flex;gap:0;margin-top:14px;background:#fff;border-radius:var(--radius) var(--radius) 0 0;box-shadow:var(--shadow);overflow:hidden;}
.tab-btn{flex:1;padding:14px 10px;background:#fff;border:none;font-size:.9rem;font-weight:600;color:var(--gray-600);cursor:pointer;transition:all .2s;border-bottom:3px solid transparent;}
.tab-btn.active{color:var(--green);border-bottom-color:var(--green);background:var(--green-bg);}
.tab-btn:hover:not(.active){background:var(--gray-50);}
.tab-content{display:none;}
.tab-content.active{display:block;}

/* BLOG TAB */
.blog-start-card{background:#fff;border-radius:0 0 var(--radius) var(--radius);box-shadow:var(--shadow);padding:32px 20px;text-align:center;}
.blog-start-icon{font-size:3rem;margin-bottom:12px;}
.blog-start-title{font-size:1.1rem;font-weight:700;margin-bottom:8px;}
.blog-start-desc{font-size:.85rem;color:var(--gray-600);margin-bottom:20px;line-height:1.5;}
.btn-blog-analyze{padding:14px 28px;background:var(--green);color:#fff;border:none;border-radius:10px;font-size:1rem;font-weight:700;cursor:pointer;transition:background .2s;}
.btn-blog-analyze:hover{background:var(--green-d);}
.btn-blog-analyze:disabled{background:var(--gray-400);cursor:not-allowed;}
.blog-loading{padding:24px;text-align:center;}
.blog-loading-text{font-size:.9rem;color:var(--gray-600);margin-top:12px;}
.blog-empty{background:var(--gray-50);border:1px dashed var(--gray-200);border-radius:10px;padding:24px;text-align:center;margin-top:14px;}
.blog-empty-icon{font-size:2rem;margin-bottom:8px;}
.blog-empty-text{font-size:.88rem;color:var(--gray-600);}

/* ANALYSIS TYPE SELECTOR */
.analysis-type-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;}
.analysis-type-btn{display:flex;flex-direction:column;align-items:center;gap:4px;padding:14px 10px;background:#fff;border:2px solid var(--gray-200);border-radius:12px;cursor:pointer;transition:all .2s;}
.analysis-type-btn:hover{border-color:var(--green);background:var(--green-bg);}
.analysis-type-btn.selected{border-color:var(--green);background:var(--green-bg);}
.analysis-type-btn input{display:none;}
.type-icon{font-size:1.5rem;}
.type-label{font-size:.9rem;font-weight:700;color:var(--gray-800);}
.type-desc{font-size:.72rem;color:var(--gray-500);}

/* RANK CHANGE INDICATOR */
.rank-change{font-size:.68rem;font-weight:600;margin-left:4px;}
.rank-change.up{color:var(--green);}
.rank-change.down{color:var(--red);}
.rank-change.same{color:var(--gray-400);}
.prev-rank{font-size:.68rem;color:var(--gray-400);margin-left:4px;}

/* SCORE CHANGE INDICATOR */
.score-change{font-size:.72rem;font-weight:600;margin-left:6px;}
.score-change.up{color:var(--green);}
.score-change.down{color:var(--red);}

/* DESKTOP */
@media(min-width:640px){
  .main{padding:28px 24px 80px;}
  .axis-grid{grid-template-columns:1fr 1fr;}
}
</style>
</head>
<body>
<div class="header">
  <div class="logo"><span class="logo-icon">🩺</span>플레이스닥터</div>
  <span class="header-badge">무료 진단</span>
</div>
<div class="main">

  <!-- INPUT -->
  <div id="input-section">
    <div class="input-card">
      <h2>내 매장, 지금 몇 위인지 아세요?</h2>
      <p>네이버 플레이스 URL만 있으면 순위·리뷰·경쟁사까지 무료로 분석해드려요</p>
      <div class="field"><label>매장명</label><input type="text" id="storeName" placeholder="예: 감동식당"></div>
      <div class="field"><label>네이버 플레이스 URL</label><input type="text" id="placeUrl" placeholder="https://naver.me/... 또는 map.naver.com/..."></div>
      <div class="field">
        <label>분석 유형</label>
        <div class="analysis-type-grid">
          <label class="analysis-type-btn selected" data-type="place">
            <input type="radio" name="analysisType" value="place" checked>
            <span class="type-icon">📍</span>
            <span class="type-label">플레이스</span>
            <span class="type-desc">순위·리뷰·경쟁사</span>
          </label>
          <label class="analysis-type-btn" data-type="blog">
            <input type="radio" name="analysisType" value="blog">
            <span class="type-icon">📝</span>
            <span class="type-label">블로그</span>
            <span class="type-desc">블로그 노출 순위</span>
          </label>
        </div>
      </div>
      <div id="adFieldsWrap" class="field">
        <label>현재 집행 중인 광고 (해당 항목 체크)</label>
        <div class="ad-check-grid">
          <label class="ad-check"><input type="checkbox" id="adPlace"> 플레이스 광고</label>
          <label class="ad-check"><input type="checkbox" id="adPowerlink"> 파워링크</label>
          <label class="ad-check"><input type="checkbox" id="adLocal"> 지역소상공인광고</label>
          <label class="ad-check"><input type="checkbox" id="adBlog"> 블로그 체험단</label>
        </div>
      </div>
      <label style="display:flex;align-items:center;gap:6px;font-size:.82rem;color:var(--gray-600);margin-bottom:14px;cursor:pointer;">
        <input type="checkbox" id="forceRefresh"> 강제 재크롤링
      </label>
      <button class="btn-diagnose" id="diagBtn" onclick="startAnalysis()">🔍 진단하기</button>
      <div class="status-msg" id="statusMsg"></div>
    </div>
    <div id="errBox"></div>
  </div>

  <!-- LOADING -->
  <div id="loading-section">
    <div class="l-card">
      <span class="l-pulse" id="lIcon">🩺</span>
      <div class="l-title" id="lTitle">플레이스 진단 중이에요</div>
      <div class="l-sub" id="lSub">키워드를 하나씩 검색하고 있어요 · 1~3분 소요</div>
      <div class="l-bar-wrap"><div class="l-bar" id="lBar"></div></div>
      <div class="l-pct" id="lPct">0%</div>
      <div class="l-steps" id="lSteps"></div>
      <div class="l-tip" id="lTip"></div>
    </div>
  </div>

  <!-- RESULT -->
  <div id="result">
    <!-- 공통 헤더: 매장명 + 종합점수 (탭 위에 항상 표시) -->
    <div class="result-header">
      <div class="store-badge">📍 <span id="rCategory"></span></div>
      <div class="store-name" id="rStoreName"></div>
      <div class="store-meta" id="rMeta"></div>
    </div>

    <!-- GAUGE (공통) -->
    <div class="card">
      <div class="card-title">종합 플레이스 점수</div>
      <div class="gauge-wrap">
        <span class="grade-badge" id="gradeBadge">-</span>
        <svg class="gauge-svg" width="160" height="160" viewBox="0 0 160 160">
          <circle class="gauge-track" cx="80" cy="80" r="66"/>
          <circle class="gauge-fill" id="gaugeFill" cx="80" cy="80" r="66" stroke-dasharray="0 415" stroke="#22c55e"/>
          <text class="gauge-text" id="gaugeNum" x="80" y="76" fill="#111827">0</text>
          <text class="gauge-sub" x="80" y="98">/100점</text>
        </svg>
        <p class="gauge-summary" id="gaugeSummary"></p>
      </div>
    </div>

    <!-- TABS -->
    <div class="tabs">
      <button class="tab-btn active" data-tab="place" onclick="switchTab('place')">📍 플레이스 분석</button>
      <button class="tab-btn" data-tab="blog" onclick="switchTab('blog')">📝 블로그 분석</button>
    </div>

    <!-- TAB: 플레이스 분석 -->
    <div id="tab-place" class="tab-content active">
      <!-- 4-AXIS -->
      <div style="font-size:.82rem;font-weight:700;color:var(--gray-600);padding:12px 0 0;">진단 상세</div>
      <div class="axis-grid" id="axisGrid"></div>

      <!-- COMPETITOR -->
      <div class="card" id="compCard" style="display:none;">
        <div class="card-title">🏆 경쟁사 비교</div>
        <div class="comp-rows" id="compRows"></div>
      </div>

      <!-- KEYWORDS -->
      <div class="card">
        <div class="card-title">🔑 키워드 순위</div>
        <div class="kw-list" id="kwList"></div>
        <div class="kw-more" id="kwMore" onclick="toggleKw()"></div>
      </div>

      <!-- DOCTOR COMMENT -->
      <div class="card">
        <div class="card-title">💬 닥터 코멘트</div>
        <div class="comment-box" id="commentBox"></div>
      </div>

      <!-- BUTTONS -->
      <div class="card">
        <div class="btn-area">
          <button class="btn-main" onclick="handleLead()">📋 상세 리포트 카톡으로 받기</button>
          <div class="btn-row">
            <button class="btn-secondary" onclick="handlePwa()">📱 홈 화면 추가</button>
            <button class="btn-secondary" onclick="handleShare()">💬 카톡 공유</button>
          </div>
        </div>
        <p class="bottom-note">개선 로드맵 + 키워드별 분석 무료 발송 · 운영 <strong>광고토대왕</strong></p>
      </div>
    </div>

    <!-- TAB: 블로그 분석 -->
    <div id="tab-blog" class="tab-content">
      <!-- 분석 전: 시작 버튼 -->
      <div id="blogStartCard" class="blog-start-card">
        <div class="blog-start-icon">📝</div>
        <div class="blog-start-title">블로그 노출 분석</div>
        <div class="blog-start-desc">
          우리 가게를 태그한 블로그가 검색 몇 위에 노출되는지 분석해요.<br>
          상위 5개 키워드 기준 · 약 60초 소요
        </div>
        <button class="btn-blog-analyze" id="btnBlogAnalyze" onclick="startBlogAnalysis()">🔍 블로그 노출 분석하기</button>
      </div>

      <!-- 분석 중: 로딩 -->
      <div id="blogLoading" class="card" style="display:none;">
        <div class="blog-loading">
          <span class="l-pulse" style="font-size:2.5rem;">📝</span>
          <div class="blog-loading-text">블로그 분석 중... <span id="blogProgress">0/5</span></div>
          <div class="l-bar-wrap" style="margin-top:12px;"><div class="l-bar" id="blogBar" style="width:0%"></div></div>
        </div>
      </div>

      <!-- 분석 완료: 결과 -->
      <div id="blogResultCard" class="card" style="display:none;">
        <div class="card-title">📝 블로그 노출 분석 결과</div>
        <div class="blog-list" id="blogList"></div>
        <div class="blog-summary" id="blogSummary"></div>
      </div>
    </div>

    <div class="btn-redo"><button onclick="resetForm()">← 다시 진단하기</button></div>
  </div>

</div>
<script>
// ── 상태 ──────────────────────────────────────────────────────────────────────
const CIRC = 2 * Math.PI * 66; // ≈ 414.7
let _allKw = [], _kwExpanded = false;
let _blogAnalyzed = false;
let _analysisType = 'place';  // 'place' | 'blog'
let _prevAnalysis = null;     // 직전 분석 결과 (비교용)

// ── 분석 유형 선택 ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.analysis-type-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.analysis-type-btn').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      _analysisType = btn.dataset.type;
      // 플레이스 분석 시에만 광고 체크박스 표시
      document.getElementById('adFieldsWrap').style.display = _analysisType === 'place' ? 'block' : 'none';
    });
  });
});

// ── 유틸 ──────────────────────────────────────────────────────────────────────
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const fmt = n => (n == null ? '-' : Number(n).toLocaleString());

function scoreColor(s){
  if(s == null) return '#9ca3af';
  if(s >= 70)   return '#22c55e';
  if(s >= 40)   return '#f97316';
  return '#ef4444';
}
function scoreChip(s, low='부족', mid='보통', high='좋음'){
  if(s == null) return `<span class="chip chip-ok">-</span>`;
  const c = s>=70?'chip-good':s>=40?'chip-ok':'chip-bad';
  const l = s>=70?high:s>=40?mid:low;
  return `<span class="chip ${c}">${l}</span>`;
}
function grade(s){
  if(s>=90)return{text:'A등급 · 최우수',bg:'#16a34a'};
  if(s>=70)return{text:'B등급 · 우수',  bg:'#22c55e'};
  if(s>=50)return{text:'C등급 · 보통',  bg:'#f97316'};
  if(s>=30)return{text:'D등급 · 미흡',  bg:'#ef4444'};
  return          {text:'F등급 · 위험',  bg:'#dc2626'};
}

// ── 게이지 애니메이션 ──────────────────────────────────────────────────────────
function animateGauge(target){
  const fill = document.getElementById('gaugeFill');
  const num  = document.getElementById('gaugeNum');
  const color = scoreColor(target);
  fill.setAttribute('stroke', color);
  let cur = 0;
  const step = () => {
    cur = Math.min(cur + 2, target);
    const dash = (cur / 100 * CIRC).toFixed(1);
    fill.setAttribute('stroke-dasharray', `${dash} ${CIRC}`);
    num.textContent = cur;
    num.setAttribute('fill', color);
    if(cur < target) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ── 로딩 애니메이션 ───────────────────────────────────────────────────────────
const L_STEPS = [
  { label:'매장 정보 수집 중',    icon:'🔍', desc:'네이버 플레이스에서 매장 정보를 읽어오고 있어요',       ms:12000 },
  { label:'키워드 순위 분석 중',  icon:'📊', desc:'검색 키워드 30개를 하나씩 확인하고 있어요 (가장 오래 걸려요)', ms:80000 },
  { label:'리뷰·별점 수집 중',   icon:'⭐', desc:'방문자 리뷰, 블로그 리뷰, 별점 데이터를 모으고 있어요', ms:15000 },
  { label:'경쟁사 비교 중',      icon:'🏆', desc:'같은 키워드 1위 매장 정보를 분석하고 있어요',         ms:15000 },
  { label:'블로그 분석 중',      icon:'📝', desc:'우리 가게 태그한 블로그 순위를 확인하고 있어요',       ms:70000 },
  { label:'점수 계산 중',        icon:'✅', desc:'4축 진단 점수를 계산하고 있어요 — 거의 다 됐어요!',    ms:999999 },
];
const L_TIPS = [
  '💡 방문자 리뷰 50개 이상이면 검색 노출에 유리해요',
  '💡 플레이스 사진은 최소 10장 이상 등록하면 점수가 올라요',
  '💡 키워드가 업종·지역과 잘 맞을수록 상위 노출 가능성이 높아요',
  '💡 최근 30일 이내 리뷰가 있으면 활성도 점수가 높아져요',
  '💡 매장 정보(주소·전화·영업시간)가 완전할수록 노출에 유리해요',
];

let _lStart=0, _lTimer=null, _lStepIdx=0, _lRafId=null, _lProg=0;

// 블로그 분석용 로딩 스텝
const L_STEPS_BLOG = [
  { label:'매장 정보 수집 중',    icon:'🔍', desc:'네이버 플레이스에서 매장 정보를 읽어오고 있어요',       ms:15000 },
  { label:'키워드 추출 중',       icon:'📝', desc:'블로그 검색에 사용할 키워드를 생성하고 있어요',        ms:5000 },
  { label:'블로그 순위 분석 중',  icon:'📊', desc:'키워드별 블로그 검색 순위를 확인하고 있어요',         ms:60000 },
  { label:'결과 정리 중',         icon:'✅', desc:'분석 결과를 정리하고 있어요 — 거의 다 됐어요!',       ms:999999 },
];

function startLoading(type){
  _lStart=Date.now(); _lStepIdx=0; _lProg=0;
  document.getElementById('lBar').style.width='0%';
  document.getElementById('lPct').textContent='0%';
  document.getElementById('lTip').textContent=L_TIPS[Math.floor(Math.random()*L_TIPS.length)];

  // 분석 유형에 따라 로딩 화면 텍스트 변경
  if(type === 'blog'){
    document.getElementById('lIcon').textContent = '📝';
    document.getElementById('lTitle').textContent = '블로그 분석 중이에요';
    document.getElementById('lSub').textContent = '블로그 노출 순위를 확인하고 있어요 · 약 1분 소요';
    _renderLSteps(0, L_STEPS_BLOG);
  } else {
    document.getElementById('lIcon').textContent = '🩺';
    document.getElementById('lTitle').textContent = '플레이스 진단 중이에요';
    document.getElementById('lSub').textContent = '키워드를 하나씩 검색하고 있어요 · 1~3분 소요';
    _renderLSteps(0, L_STEPS);
  }

  _animateLBar();
  _lTimer=setInterval(()=>_advanceLStep(type==='blog'?L_STEPS_BLOG:L_STEPS),1000);
}

function stopLoading(){
  clearInterval(_lTimer); cancelAnimationFrame(_lRafId);
  document.getElementById('lBar').style.width='100%';
  document.getElementById('lPct').textContent='100%';
}

function _advanceLStep(steps){
  const elapsed=Date.now()-_lStart;
  let cum=0, idx=0;
  for(let i=0;i<steps.length;i++){cum+=steps[i].ms;if(elapsed<cum){idx=i;break;}idx=steps.length-1;}
  if(idx!==_lStepIdx){_lStepIdx=idx;_renderLSteps(idx,steps);}
  // rotate tip every 20s
  const tipIdx=Math.floor(elapsed/20000)%L_TIPS.length;
  document.getElementById('lTip').textContent=L_TIPS[tipIdx];
}

function _renderLSteps(active,steps){
  const stepsArr = steps || L_STEPS;
  document.getElementById('lSteps').innerHTML=stepsArr.map((s,i)=>{
    const state=i<active?'done':i===active?'active':'pending';
    const ic=state==='done'?'✓':s.icon;
    const dots=state==='active'?'<span class="dots"><span></span><span></span><span></span></span>':'';
    return `<div class="l-step">
      <div class="l-ic ${state}">${ic}</div>
      <div class="l-body">
        <div class="l-name ${state}">${s.label}${dots}</div>
        ${state!=='pending'?`<div class="l-desc">${s.desc}</div>`:''}
      </div>
    </div>`;
  }).join('');
}

function _animateLBar(){
  // 총 진행 시간의 95% 까지만 자동으로 채우고, 나머지는 API 응답 후 100%
  const totalMs=L_STEPS.slice(0,-1).reduce((a,s)=>a+s.ms,0); // 마지막 step 제외
  const target=95;
  const update=()=>{
    const elapsed=Date.now()-_lStart;
    // easing: fast at start, slow near end
    const raw=Math.min(elapsed/totalMs,1);
    const eased=1-Math.pow(1-raw,2.5);
    _lProg=Math.min(eased*target,target);
    document.getElementById('lBar').style.width=_lProg.toFixed(1)+'%';
    document.getElementById('lPct').textContent=Math.floor(_lProg)+'%';
    _lRafId=requestAnimationFrame(update);
  };
  _lRafId=requestAnimationFrame(update);
}

// ── 메인 분석 시작 (유형에 따라 분기) ──────────────────────────────────────────
async function startAnalysis(){
  if(_analysisType === 'place'){
    await analyzePlaceOnly();
  } else {
    await analyzeBlogOnly();
  }
}

// ── 플레이스 분석 ─────────────────────────────────────────────────────────────
async function analyzePlaceOnly(){
  const name = document.getElementById('storeName').value.trim();
  const url  = document.getElementById('placeUrl').value.trim();
  const force= document.getElementById('forceRefresh').checked;
  const adFlags = {
    ad_place:     document.getElementById('adPlace').checked,
    ad_powerlink: document.getElementById('adPowerlink').checked,
    ad_local:     document.getElementById('adLocal').checked,
    ad_blog:      document.getElementById('adBlog').checked,
  };
  if(!name||!url){alert('매장명과 URL을 입력해주세요.');return;}

  const btn = document.getElementById('diagBtn');
  btn.disabled=true; btn.textContent='분석 중...';
  document.getElementById('errBox').innerHTML='';

  document.getElementById('input-section').style.display='none';
  document.getElementById('loading-section').style.display='block';
  startLoading('place');
  window.scrollTo({top:0,behavior:'smooth'});

  const MIN_SHOW_MS = 1500;

  try{
    const [res] = await Promise.all([
      fetch('/diagnose',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({store_name:name,place_url:url,force_refresh:force,...adFlags})
      }),
      new Promise(r=>setTimeout(r, MIN_SHOW_MS))
    ]);
    const text = await res.text();
    stopLoading();
    if(!res.ok){
      document.getElementById('loading-section').style.display='none';
      document.getElementById('input-section').style.display='block';
      document.getElementById('errBox').innerHTML=`<div class="err-box">오류 (${res.status})<br><small>${esc(text.slice(0,400))}</small></div>`;
      btn.disabled=false; btn.textContent='🔍 진단하기';
      return;
    }
    document.getElementById('loading-section').style.display='none';
    const data = JSON.parse(text);
    _prevAnalysis = data.prev_analysis || null;  // 직전 분석 결과
    renderResult(data);
    document.getElementById('result').style.display='block';
    switchTab('place');
    window.scrollTo({top:0,behavior:'smooth'});
  }catch(e){
    stopLoading();
    document.getElementById('loading-section').style.display='none';
    document.getElementById('input-section').style.display='block';
    document.getElementById('errBox').innerHTML=`<div class="err-box">요청 실패: ${esc(e.message)}</div>`;
    btn.disabled=false; btn.textContent='🔍 진단하기';
  }
}

// ── 블로그 분석 (단독) ────────────────────────────────────────────────────────
async function analyzeBlogOnly(){
  const name = document.getElementById('storeName').value.trim();
  const url  = document.getElementById('placeUrl').value.trim();
  if(!name||!url){alert('매장명과 URL을 입력해주세요.');return;}

  const btn = document.getElementById('diagBtn');
  btn.disabled=true; btn.textContent='분석 중...';
  document.getElementById('errBox').innerHTML='';

  document.getElementById('input-section').style.display='none';
  document.getElementById('loading-section').style.display='block';
  startLoading('blog');
  window.scrollTo({top:0,behavior:'smooth'});

  const MIN_SHOW_MS = 1500;

  try{
    const [res] = await Promise.all([
      fetch('/analyze-blog-standalone',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({store_name:name,place_url:url})
      }),
      new Promise(r=>setTimeout(r, MIN_SHOW_MS))
    ]);
    const text = await res.text();
    stopLoading();
    if(!res.ok){
      document.getElementById('loading-section').style.display='none';
      document.getElementById('input-section').style.display='block';
      document.getElementById('errBox').innerHTML=`<div class="err-box">오류 (${res.status})<br><small>${esc(text.slice(0,400))}</small></div>`;
      btn.disabled=false; btn.textContent='🔍 진단하기';
      return;
    }
    document.getElementById('loading-section').style.display='none';
    const data = JSON.parse(text);
    _prevAnalysis = data.prev_analysis || null;
    renderBlogOnlyResult(data);
    document.getElementById('result').style.display='block';
    switchTab('blog');
    window.scrollTo({top:0,behavior:'smooth'});
  }catch(e){
    stopLoading();
    document.getElementById('loading-section').style.display='none';
    document.getElementById('input-section').style.display='block';
    document.getElementById('errBox').innerHTML=`<div class="err-box">요청 실패: ${esc(e.message)}</div>`;
    btn.disabled=false; btn.textContent='🔍 진단하기';
  }
}

// ── 결과 렌더링 ───────────────────────────────────────────────────────────────
function renderResult(d){
  window._diagData = d;
  const sc = d.scores||{};
  const prev = _prevAnalysis;

  // 매장 정보
  document.getElementById('rStoreName').textContent = d.store_name||'-';
  document.getElementById('rCategory').textContent  = d.category||'매장';
  document.getElementById('rMeta').textContent      = d.address||'';

  // 종합 게이지 + 변동 표시
  const tot = sc.total??0;
  animateGauge(tot);
  const g = grade(tot);
  const badge = document.getElementById('gradeBadge');
  badge.textContent=g.text; badge.style.background=g.bg;

  let summaryHtml = buildSummary(d,sc);
  if(prev && prev.total_score != null){
    const diff = Math.round(tot - prev.total_score);
    if(diff !== 0){
      const cls = diff > 0 ? 'up' : 'down';
      const arrow = diff > 0 ? '▲' : '▼';
      summaryHtml += ` <span class="score-change ${cls}">(지난번 ${Math.round(prev.total_score)}점 → ${arrow}${Math.abs(diff)})</span>`;
    } else {
      summaryHtml += ` <span class="score-change same">(지난번과 동일)</span>`;
    }
  }
  document.getElementById('gaugeSummary').innerHTML = summaryHtml;

  // 4축 카드
  renderAxisCards(d, sc);

  // 경쟁사
  renderCompetitor(d);

  // 키워드 (직전 순위 맵 전달)
  _allKw = d.place_results||[];
  const prevRankMap = buildPrevRankMap(prev);
  renderKeywords(false, prevRankMap);

  // 블로그 분석
  renderBlogResults(d.blog_results||[]);

  // 닥터 코멘트
  renderComment(d, sc);
}

// 직전 분석에서 키워드별 순위 맵 생성
function buildPrevRankMap(prev){
  const map = {};
  if(!prev || !prev.result_json) return map;
  try{
    const data = JSON.parse(prev.result_json);
    for(const pr of (data.place_results||[])){
      if(pr.keyword && pr.rank != null) map[pr.keyword] = pr.rank;
    }
  }catch(e){}
  return map;
}

// 블로그 단독 결과 렌더링
function renderBlogOnlyResult(d){
  window._diagData = d;
  const prev = _prevAnalysis;

  document.getElementById('rStoreName').textContent = d.store_name||'-';
  document.getElementById('rCategory').textContent  = d.category||'매장';
  document.getElementById('rMeta').textContent      = d.address||'';

  // 게이지 숨기기 (플레이스 분석 결과가 아님)
  document.getElementById('gradeBadge').textContent = '블로그';
  document.getElementById('gradeBadge').style.background = '#3b82f6';
  document.getElementById('gaugeFill').setAttribute('stroke-dasharray', '0 415');
  document.getElementById('gaugeNum').textContent = '-';
  document.getElementById('gaugeSummary').innerHTML = '블로그 노출 분석 결과입니다.';

  // 탭 숨기기 (블로그 결과만 표시)
  document.querySelector('.tabs').style.display = 'none';
  document.getElementById('tab-place').style.display = 'none';
  document.getElementById('tab-blog').classList.add('active');
  document.getElementById('tab-blog').style.display = 'block';

  // 블로그 시작 카드 숨기고 결과 표시
  document.getElementById('blogStartCard').style.display = 'none';
  document.getElementById('blogResultCard').style.display = 'block';

  // 직전 블로그 순위 맵
  const prevBlogMap = buildPrevBlogRankMap(prev);
  renderBlogResultsWithComparison(d.blog_results||[], prevBlogMap);
}

function buildPrevBlogRankMap(prev){
  const map = {};
  if(!prev || !prev.result_json) return map;
  try{
    const data = JSON.parse(prev.result_json);
    for(const br of (data.blog_results||[])){
      const kw = br.keyword;
      for(const h of (br.hits||[])){
        if(h.rank != null){
          const key = `${kw}|${h.blog_link||h.title}`;
          map[key] = h.rank;
        }
      }
    }
  }catch(e){}
  return map;
}

function buildSummary(d, sc){
  const tot=sc.total??0;
  if(tot>=80) return '전반적으로 잘 관리되고 있는 매장이에요. 경쟁사와의 격차를 더 벌려볼 수 있어요.';
  if(tot>=60) return '일부 항목이 개선되면 상위 노출 가능성이 높아져요. 충분히 올릴 수 있는 구간이에요.';
  if(tot>=40) return '아직 개선 여지가 많아요. 리뷰·키워드 관리부터 시작하면 빠르게 효과를 볼 수 있어요.';
  return '지금 당장 개선이 필요해요. 기본기부터 차근차근 채워나가면 달라집니다.';
}

// ── 4축 카드 ─────────────────────────────────────────────────────────────────
function renderAxisCards(d, sc){
  const grid = document.getElementById('axisGrid');
  const axes = [
    buildSeoCard(d, sc.seo??0),
    buildContentCard(d, sc.content??0),
    buildActivityCard(d, sc.activity??0),
    buildAdCard(d, sc),
  ];
  grid.innerHTML = axes.join('');
  // 진행바 애니메이션
  setTimeout(()=>{
    document.querySelectorAll('.progress-fill').forEach(el=>{
      el.style.width = el.dataset.w;
    });
  },100);
}

function axisCard(icon, name, score, details){
  const color = scoreColor(score);
  return `<div class="axis-card">
    <div class="axis-head"><span class="axis-icon">${icon}</span><span class="axis-name">${name}</span></div>
    <div class="axis-score" style="color:${color}">${score}<small style="font-size:.9rem;font-weight:600;color:var(--gray-400)">/100</small></div>
    <div class="progress-bar"><div class="progress-fill" data-w="${score}%" style="width:0%;background:${color}"></div></div>
    <div class="detail-list">${details}</div>
  </div>`;
}

function detailRow(label, val, chipScore){
  return `<div class="detail-row">
    <span class="detail-label">${label}</span>
    <div class="detail-val"><span class="detail-num">${val}</span>${scoreChip(chipScore)}</div>
  </div>`;
}

function buildSeoCard(d, score){
  const kws = d.place_results||[];
  const topRank = kws.reduce((best,k)=>k.rank&&k.rank<(best||999)?k.rank:best, null);
  const infoScore = (d.address?30:0)+(d.category?30:0)+((d.photo_count||0)>=10?40:(d.photo_count||0)>=3?20:0);
  const photoCount = d.photo_count??null;
  return axisCard('📍','검색노출(SEO)',score,[
    detailRow('대표 키워드 순위', topRank?`${topRank}위`:'30위 밖', topRank?Math.max(0,100-topRank*3):5),
    detailRow('정보 완성도', infoScore+'%', infoScore),
    detailRow('사진 수', photoCount!=null?photoCount+'장':'-', photoCount!=null?Math.min(100,photoCount*8):null),
  ].join(''));
}

function buildContentCard(d, score){
  const vr = d.visitor_reviews, br = d.blog_reviews, ss = d.star_score;
  return axisCard('⭐','리뷰관리',score,[
    detailRow('방문자 리뷰', vr!=null?fmt(vr)+'개':'-', vr!=null?Math.min(100,vr/5):null),
    detailRow('블로그 리뷰', br!=null?fmt(br)+'개':'-', br!=null?Math.min(100,br/3):null),
    detailRow('별점', ss!=null?ss+'점':'별점 없음', ss!=null?(ss>=4.5?90:ss>=4.0?65:ss>=3.5?40:20):null),
  ].join(''));
}

function buildActivityCard(d, score){
  const lr = d.latest_review_date;
  let dayStr='-', dayScore=null;
  if(lr){
    const diff=Math.floor((Date.now()-new Date(lr.replace(/[.]/g,'-')))/86400000);
    dayStr=diff<=0?'오늘':`${diff}일 전`;
    dayScore=diff<=7?100:diff<=30?80:diff<=90?55:diff<=180?30:10;
  }
  // 리뷰 활동: 처음 ~10개 중 30일 이내 개수로 백엔드가 산출한 라벨 (활발/보통/한산/거의 없음)
  const act=d.review_activity;
  const actScore=act==null?null:act==='활발'?100:act==='보통'?70:act==='한산'?45:25;
  return axisCard('🔥','최근활동',score,[
    detailRow('최근 리뷰', dayStr, dayScore),
    detailRow('리뷰 활동', act??'-', actScore),
    detailRow('정보 최신성', d.address?'최신':'미확인', d.address?80:30),
  ].join(''));
}

function buildAdCard(d, sc){
  const score = sc.ad??20;
  const f = d.ad_flags||{};
  const adItems = [
    {name:'플레이스 광고',     on:!!f.place},
    {name:'파워링크',          on:!!f.powerlink},
    {name:'지역소상공인광고',  on:!!f.local},
    {name:'블로그 체험단',     on:!!f.blog},
  ];
  const rows = adItems.map(a=>`<div class="detail-row"><span class="detail-label">${a.name}</span><div class="detail-val"><span class="chip ${a.on?'chip-good':'chip-bad'}">${a.on?'✓ 집행':'✗ 미집행'}</span></div></div>`).join('');
  const label = sc.ad_label?`<p style="font-size:.78rem;font-weight:700;color:var(--gray-700);margin-top:8px;">${esc(sc.ad_label)}</p>`:'';
  const note = '<p style="font-size:.72rem;color:var(--gray-600);margin-top:6px;line-height:1.5;">광고가 켜져 있어도 키워드·소재 최적화로 효율을 더 올릴 수 있어요</p>';
  return axisCard('📣','키워드광고',score, rows + label + note);
}

// ── 경쟁사 비교 ───────────────────────────────────────────────────────────────
function renderCompetitor(d){
  const comp=d.competitor||{}, compD=comp.details||{};
  if(!comp.competitor_id){document.getElementById('compCard').style.display='none';return;}
  document.getElementById('compCard').style.display='block';

  const baseKw=(d.keywords_used||[])[0]||'';
  const myBestRank=(d.place_results||[]).reduce((b,k)=>k.rank&&k.rank<(b||999)?k.rank:b,null);
  const compRank=comp.competitor_rank||1;

  const myVr=d.visitor_reviews??0, cVr=compD.visitor_reviews??0;
  const myBr=d.blog_reviews??0,    cBr=compD.blog_reviews??0;

  // 순위 점수: 1위=100, 계단당 -10 (10위 초과=0)
  const rankScore=r=>r?Math.max(0,110-r*10):0;
  const myRS=rankScore(myBestRank), cRS=rankScore(compRank);

  // 간이 리뷰파워 점수 (visitor 60% + blog 40%)
  function revPow(vr,br){
    return Math.round(Math.min(vr,500)/500*60+Math.min(br,300)/300*40);
  }
  const myRP=revPow(myVr,myBr), cRP=revPow(cVr,cBr);

  const isUs1st = comp.my_rank === 1;
  const compLabel = compRank + '위';

  function compRow(label,myVal,cVal,maxVal,myTxt,cTxt){
    const mp=(myVal/Math.max(maxVal,1)*100).toFixed(0);
    const cp=(cVal/Math.max(maxVal,1)*100).toFixed(0);
    return `<div>
      <div class="comp-label">${label}</div>
      <div class="comp-bar-wrap">
        <div class="comp-tag" style="color:var(--green)">우리</div>
        <div class="comp-bar-bg"><div class="comp-bar" style="width:${mp}%;background:var(--green)">${myTxt}</div></div>
      </div>
      <div class="comp-bar-wrap" style="margin-top:4px">
        <div class="comp-tag" style="color:var(--gray-600)">${compLabel}</div>
        <div class="comp-bar-bg"><div class="comp-bar" style="width:${cp}%;background:var(--gray-400)">${cTxt}</div></div>
      </div>
    </div>`;
  }

  let rows='';
  if(baseKw){
    const headerTxt = isUs1st
      ? `🥇 당신은 1위! '${esc(baseKw)}' 검색 2위 매장과 비교`
      : `'${esc(baseKw)}' 검색 1위 매장과 비교`;
    rows+=`<p style="font-size:.8rem;color:var(--gray-600);margin:0 0 12px;">${headerTxt}</p>`;
  }

  rows+=compRow('플레이스 순위', myRS, cRS, 100,
    myBestRank?myBestRank+'위':'미노출', compRank+'위');

  const brGap=cBr-myBr;
  rows+=compRow('블로그 리뷰', myBr, cBr, Math.max(myBr,cBr,1),
    fmt(myBr)+'개', fmt(cBr)+'개');
  if(brGap>0) rows+=`<p class="comp-gap">▼ 블로그 리뷰 ${fmt(brGap)}개 뒤처져 있어요</p>`;

  const vrGap=cVr-myVr;
  rows+=compRow('방문자 리뷰', myVr, cVr, Math.max(myVr,cVr,1),
    fmt(myVr)+'개', fmt(cVr)+'개');
  if(vrGap>0) rows+=`<p class="comp-gap">▼ 방문자 리뷰 ${fmt(vrGap)}개 뒤처져 있어요</p>`;

  rows+=compRow('리뷰 파워', myRP, cRP, Math.max(myRP,cRP,1),
    myRP+'점', cRP+'점');

  document.getElementById('compRows').innerHTML=rows;
}

// ── 순위 숫자 색 ──────────────────────────────────────────────────────────────
function rankColor(rank){
  if(rank===1)             return '#16a34a';
  if(rank!=null&&rank<=5)  return '#22c55e';
  if(rank!=null&&rank<=10) return '#3b82f6';
  if(rank!=null&&rank<=15) return '#f97316';
  if(rank!=null)           return '#9ca3af';
  return '#ef4444';
}

// ── 등급(S/A/B/C) 계산 — businesses_total 상대 백분율 ────────────────────────
const GRADE_STYLE={
  S:'background:#22c55e;color:#fff',
  A:'background:#3b82f6;color:#fff',
  B:'background:#9ca3af;color:#fff',
  C:'background:#e5e7eb;color:#6b7280'
};
function calcGrades(kwList){
  const valid=kwList.filter(k=>k.businesses_total!=null);
  if(!valid.length) return {};
  const sorted=[...valid].sort((a,b)=>b.businesses_total-a.businesses_total);
  const n=sorted.length;
  const grades={};
  sorted.forEach((k,i)=>{
    const pct=i/(n>1?n-1:1);
    let g;
    if(i===0||pct<0.10) g='S';
    else if(pct<0.35)   g='A';
    else if(pct<0.70)   g='B';
    else                g='C';
    grades[k.keyword]=g;
  });
  return grades;
}

// ── 순위 구간별 규칙 기반 멘트 — 구간×4개 풀, {rank} 치환, 인덱스 순환 ─────────
function rankBand(rank){
  if(rank==null)  return 'none';
  if(rank===1)    return 'top1';
  if(rank<=5)     return 'top5';
  if(rank<=10)    return 'top10';
  if(rank<=15)    return 'top15';
  if(rank<=20)    return 'top20';
  return 'top30';
}
const RANK_MENTS_POOL = {
  top1:  ["1위! 이 키워드는 완벽해요. 지금처럼 유지하세요","최상단 고정! 손님이 가장 먼저 보는 자리예요","1위 — 이 키워드로는 더 바랄 게 없어요","검색하면 맨 위. 이 키워드는 효자 키워드예요"],
  top5:  ["{rank}위 — 첫 화면 안에 잘 들어와 있어요","{rank}위, 상위권이에요. 1~2위까지 노려볼 만해요","{rank}위 — 손님 눈에 잘 띄는 좋은 자리예요","{rank}위로 안정적. 조금만 더 올리면 최상단이에요"],
  top10: ["{rank}위 — 첫 화면이 코앞! 조금만 더 올리면 돼요","{rank}위, 1페이지 끝자락이에요. 한 끗만 더!","{rank}위 — 상위권까지 멀지 않아요. 밀어줄 타이밍","{rank}위. 첫 화면 상단으로 올릴 여지가 충분해요"],
  top15: ["{rank}위 — 3~4계단만 올리면 첫 화면! 가장 아까워요","{rank}위, 1페이지가 손에 잡힐 듯! 마지막 한 끗이에요","{rank}위 — 여기서 조금만 올리면 노출이 확 늘어요","{rank}위. 첫 화면 문턱이에요. 제일 효율 좋은 구간"],
  top20: ["{rank}위 — 2페이지예요. 대부분 여기까진 안 봐요","{rank}위, 첫 화면 밖이에요. 끌어올릴 필요가 있어요","{rank}위 — 노출은 되지만 손님이 닿기 어려운 자리예요","{rank}위. 1페이지로 올리면 방문이 크게 늘어요"],
  top30: ["{rank}위 — 많이 뒤예요. 본격적인 관리가 필요해요","{rank}위, 검색 손님 대부분을 놓치고 있어요","{rank}위 — 노출은 되지만 사실상 안 보이는 위치예요","{rank}위. 상위로 올릴 여지가 큰 키워드예요"],
  none:  ["아직 안 보여요 — 노려볼 만한 기회 키워드예요","이 키워드론 검색에 안 잡혀요. 새로 공략할 자리예요","미노출 상태 — 경쟁이 덜할 수 있는 기회예요","아직 순위권 밖. 잡으면 신규 손님이 늘어요"],
};
function getRankMent(rank, idx){
  const pool=RANK_MENTS_POOL[rankBand(rank)];
  return pool[idx%pool.length].replace(/\\{rank\\}/g, rank??'');
}

// ── 키워드 렌더링 ─────────────────────────────────────────────────────────────
let _lastPrevRankMap = {};
function renderKeywords(expanded, prevRankMap){
  _kwExpanded=expanded;
  if(prevRankMap) _lastPrevRankMap = prevRankMap;
  const list=document.getElementById('kwList');
  const more=document.getElementById('kwMore');

  // 등급 계산 (businesses_total 상대 백분율)
  const grades=calcGrades(_allKw);

  // 업체수 많은 순 정렬 (없으면 뒤로, 업체수 같으면 순위 좋은 순)
  const sorted=[..._allKw].sort((a,b)=>{
    const at=a.businesses_total??-1, bt=b.businesses_total??-1;
    if(at!==bt) return bt-at;
    if(a.rank&&b.rank) return a.rank-b.rank;
    if(a.rank) return -1;
    if(b.rank) return 1;
    return 0;
  });
  const show=expanded?sorted:sorted.slice(0,8);

  // 같은 구간 내 멘트 순환 카운터
  const bandIdx={};
  list.innerHTML=show.map(k=>{
    const band=rankBand(k.rank);
    if(bandIdx[band]==null) bandIdx[band]=0;
    const comment=getRankMent(k.rank, bandIdx[band]++);
    const grade=grades[k.keyword];
    const gradeBadge=grade?`<span class="kw-grade-badge" style="${GRADE_STYLE[grade]}">${grade}급</span>`:'';
    const rc=rankColor(k.rank);
    const rankDisplay=k.rank
      ?`${k.rank}<span style="font-size:.6em;font-weight:600">위</span>`
      :`<span style="font-size:.85rem;font-weight:700;color:#ef4444">놓침</span>`;
    const countHtml=k.businesses_total?`<span class="kw-count">등록업체 ${k.businesses_total.toLocaleString()}개</span>`:'';

    // 직전 순위 비교
    let changeHtml = '';
    const prevRank = _lastPrevRankMap[k.keyword];
    if(prevRank != null && k.rank != null){
      const diff = prevRank - k.rank;  // 양수면 상승
      if(diff > 0){
        changeHtml = `<span class="rank-change up">▲${diff}</span><span class="prev-rank">(전: ${prevRank}위)</span>`;
      } else if(diff < 0){
        changeHtml = `<span class="rank-change down">▼${Math.abs(diff)}</span><span class="prev-rank">(전: ${prevRank}위)</span>`;
      } else {
        changeHtml = `<span class="rank-change same">-</span><span class="prev-rank">(전: ${prevRank}위)</span>`;
      }
    } else if(prevRank != null && k.rank == null){
      changeHtml = `<span class="rank-change down">▼</span><span class="prev-rank">(전: ${prevRank}위)</span>`;
    } else if(prevRank == null && k.rank != null && Object.keys(_lastPrevRankMap).length > 0){
      changeHtml = `<span class="rank-change up">NEW</span>`;
    }

    return `<div class="kw-item">
      <div class="kw-main">
        <div class="kw-rank-col" style="color:${rc}">${rankDisplay}${changeHtml}</div>
        <div class="kw-divider"></div>
        <div class="kw-info">
          <div class="kw-title-row">
            <span class="kw-text">${esc(k.keyword)}</span>
            ${countHtml}
            ${gradeBadge}
          </div>
          <div class="kw-sub">${comment}</div>
        </div>
      </div>
    </div>`;
  }).join('');

  if(sorted.length>8){
    more.textContent=expanded?'▲ 접기':`전체 ${sorted.length}개 키워드 보기 →`;
  } else {
    more.textContent='';
  }
}
function toggleKw(){ renderKeywords(!_kwExpanded); }

// ── 블로그 분석 렌더링 ───────────────────────────────────────────────────────
function renderBlogResults(blogResults){
  const list = document.getElementById('blogList');
  const summary = document.getElementById('blogSummary');

  // 빈 결과도 명확히 표시 (카드가 안 보이는 문제 해결)
  if(!blogResults || blogResults.length===0){
    list.innerHTML = `<div class="blog-empty">
      <div class="blog-empty-icon">📭</div>
      <div class="blog-empty-text">분석할 키워드가 없어요. 먼저 플레이스 진단을 완료해주세요.</div>
    </div>`;
    summary.innerHTML = '';
    return;
  }

  // 총 매칭 블로그 수 계산
  let totalMatched = 0;
  let bestRank = null;
  let bestKw = '';

  let html = '';
  for(const br of blogResults){
    const kw = br.keyword;
    const hits = br.hits || [];
    const matchedHits = hits.filter(h => h.rank != null);
    totalMatched += matchedHits.length;

    // 최고 순위 추적
    for(const h of matchedHits){
      if(bestRank===null || h.rank < bestRank){
        bestRank = h.rank;
        bestKw = kw;
      }
    }

    const badge = matchedHits.length > 0
      ? `<span class="blog-kw-badge">${matchedHits.length}개 매칭</span>`
      : '<span class="blog-kw-badge" style="background:var(--gray-400)">0개</span>';

    html += `<div class="blog-kw-group">
      <div class="blog-kw-title">${esc(kw)} ${badge}</div>
      <div class="blog-hits">`;

    if(matchedHits.length > 0){
      for(const h of matchedHits){
        const rc = h.rank<=5 ? '#22c55e' : h.rank<=10 ? '#84cc16' : '#f97316';
        const linkUrl = h.blog_link || '#';
        html += `<div class="blog-hit">
          <div class="blog-rank" style="color:${rc}">${h.rank}<span style="font-size:.6em">위</span></div>
          <div class="blog-info">
            <div class="blog-title">${esc(h.title || '(제목 없음)')}</div>
            <a class="blog-link" href="${esc(linkUrl)}" target="_blank" rel="noopener">${esc(linkUrl.replace(/^https?:\\/\\/m?\\.?/,'').slice(0,40))}...</a>
          </div>
        </div>`;
      }
    } else {
      // 매칭 없는 경우도 명확히 표시
      const status = hits[0]?.status || '순위권 밖 (10위 이내 없음)';
      html += `<div class="blog-none">😶 ${esc(status)}</div>`;
    }

    html += `</div></div>`;
  }

  list.innerHTML = html;

  // 요약
  let summaryText = '';
  if(totalMatched > 0){
    summaryText = `✅ ${blogResults.length}개 키워드 중 총 ${totalMatched}개 블로그가 우리 가게를 태그했어요.`;
    if(bestRank !== null){
      summaryText += ` 최고 순위는 '${bestKw}'에서 ${bestRank}위예요.`;
    }
  } else {
    summaryText = `📋 ${blogResults.length}개 키워드 모두 우리 가게를 태그한 블로그가 10위 안에 없어요.<br>블로그 마케팅(체험단, 협찬)을 시작하면 노출이 늘어나요.`;
  }
  summary.innerHTML = `<p class="blog-summary-text">${summaryText}</p>`;
}

// 블로그 분석 결과 (직전 비교 포함)
function renderBlogResultsWithComparison(blogResults, prevBlogMap){
  const list = document.getElementById('blogList');
  const summary = document.getElementById('blogSummary');

  if(!blogResults || blogResults.length===0){
    list.innerHTML = `<div class="blog-empty">
      <div class="blog-empty-icon">📭</div>
      <div class="blog-empty-text">블로그 노출 결과가 없어요.</div>
    </div>`;
    summary.innerHTML = '';
    return;
  }

  let totalMatched = 0;
  let bestRank = null;
  let bestKw = '';

  let html = '';
  for(const br of blogResults){
    const kw = br.keyword;
    const hits = br.hits || [];
    const matchedHits = hits.filter(h => h.rank != null);
    totalMatched += matchedHits.length;

    for(const h of matchedHits){
      if(bestRank===null || h.rank < bestRank){
        bestRank = h.rank;
        bestKw = kw;
      }
    }

    const badge = matchedHits.length > 0
      ? `<span class="blog-kw-badge">${matchedHits.length}개 매칭</span>`
      : '<span class="blog-kw-badge" style="background:var(--gray-400)">0개</span>';

    html += `<div class="blog-kw-group">
      <div class="blog-kw-title">${esc(kw)} ${badge}</div>
      <div class="blog-hits">`;

    if(matchedHits.length > 0){
      for(const h of matchedHits){
        const rc = h.rank<=5 ? '#22c55e' : h.rank<=10 ? '#84cc16' : '#f97316';
        const linkUrl = h.blog_link || '#';
        const key = `${kw}|${h.blog_link||h.title}`;
        const prevRank = prevBlogMap[key];

        let changeHtml = '';
        if(prevRank != null){
          const diff = prevRank - h.rank;
          if(diff > 0){
            changeHtml = `<span class="rank-change up">▲${diff}</span><span class="prev-rank">(전: ${prevRank}위)</span>`;
          } else if(diff < 0){
            changeHtml = `<span class="rank-change down">▼${Math.abs(diff)}</span><span class="prev-rank">(전: ${prevRank}위)</span>`;
          } else {
            changeHtml = `<span class="rank-change same">-</span>`;
          }
        } else if(Object.keys(prevBlogMap).length > 0){
          changeHtml = `<span class="rank-change up">NEW</span>`;
        }

        html += `<div class="blog-hit">
          <div class="blog-rank" style="color:${rc}">${h.rank}<span style="font-size:.6em">위</span>${changeHtml}</div>
          <div class="blog-info">
            <div class="blog-title">${esc(h.title || '(제목 없음)')}</div>
            <a class="blog-link" href="${esc(linkUrl)}" target="_blank" rel="noopener">${esc(linkUrl.replace(/^https?:\\/\\/m?\\.?/,'').slice(0,40))}...</a>
          </div>
        </div>`;
      }
    } else {
      const status = hits[0]?.status || '순위권 밖 (10위 이내 없음)';
      html += `<div class="blog-none">😶 ${esc(status)}</div>`;
    }

    html += `</div></div>`;
  }

  list.innerHTML = html;

  let summaryText = '';
  if(totalMatched > 0){
    summaryText = `✅ ${blogResults.length}개 키워드 중 총 ${totalMatched}개 블로그가 우리 가게를 태그했어요.`;
    if(bestRank !== null){
      summaryText += ` 최고 순위는 '${bestKw}'에서 ${bestRank}위예요.`;
    }
  } else {
    summaryText = `📋 ${blogResults.length}개 키워드 모두 우리 가게를 태그한 블로그가 10위 안에 없어요.<br>블로그 마케팅(체험단, 협찬)을 시작하면 노출이 늘어나요.`;
  }
  summary.innerHTML = `<p class="blog-summary-text">${summaryText}</p>`;
}

// ── 닥터 코멘트 ───────────────────────────────────────────────────────────────
function renderComment(d, sc){
  const lines=[];
  const seo=sc.seo??0, con=sc.content??0, act=sc.activity??0;
  const vr=d.visitor_reviews, ss=d.star_score;
  const allKws=d.place_results||[];
  const rankedKws=allKws.filter(k=>k.rank);
  const AX={seo:'검색노출',content:'리뷰관리',activity:'최근활동'};

  // 1) 키워드 성과 — 첫화면 칭찬 + 아쉬운 키워드 (규칙 기반, 멘트 한 곳에 모음)
  const firstPage=rankedKws.filter(k=>k.rank<=10);
  const oppKw=rankedKws.filter(k=>k.rank>=11&&k.rank<=15).sort((a,b)=>a.rank-b.rank)[0]
            ||rankedKws.filter(k=>k.rank>5).sort((a,b)=>a.rank-b.rank)[0];

  if(allKws.length>0){
    if(firstPage.length>0)
      lines.push(`📊 검색한 키워드 ${allKws.length}개 중 ${firstPage.length}개가 첫 화면(1~10위)에 노출 중이에요.`);
    else
      lines.push(`📊 검색한 키워드 ${allKws.length}개 중 아직 첫 화면에 든 키워드가 없어요.`);
  }

  if(oppKw){
    const gap=Math.max(1,oppKw.rank-5);
    lines.push(`💡 다만 '${esc(oppKw.keyword)}'이(가) ${oppKw.rank}위라, ${gap}계단만 올리면 첫 화면이에요.`);
  } else if(rankedKws.length===0&&allKws.length>0){
    lines.push(`💡 '${esc(allKws[0].keyword)}' 같은 핵심 키워드에서 노출이 안 돼, 검색 손님을 놓치고 있어요.`);
  }

  // 2) 리뷰/별점 강점
  let strength;
  if(ss!=null&&ss>=4.5)            strength=`별점 ${ss}점으로 고객 만족도가 높아요.`;
  else if(vr!=null&&vr>=100)       strength=`방문자 리뷰 ${fmt(vr)}개로 콘텐츠 기반이 탄탄해요.`;
  else if(rankedKws.length>=5)     strength=`${rankedKws.length}개 키워드에서 노출되고 있어 기본기는 갖춰져 있어요.`;
  else{
    const best=Math.max(seo,con,act);
    const k=seo===best?'seo':con===best?'content':'activity';
    strength = best>=50 ? `${AX[k]} 쪽은 비교적 잘 관리되고 있어요.`
                        : `아직 시작 단계지만, 손볼 곳이 명확해 개선 여지가 큰 매장이에요.`;
  }
  lines.push('✅ '+strength);

  // 3) 핵심 약점 (가장 낮은 축)
  const weak=[['seo',seo],['content',con],['activity',act]].sort((a,b)=>a[1]-b[1])[0];
  const weakKey=weak[0], weakVal=weak[1];
  const weakReason={
    seo:'주요 키워드 노출이 부족해요',
    content:'리뷰·별점 관리가 경쟁사 대비 약해요',
    activity:'최근 리뷰 활동이 뜸해 신선도가 떨어져요',
  }[weakKey];
  lines.push(`🔸 ${AX[weakKey]}이(가) ${weakVal}점으로, ${weakReason}.`);

  // 4) 해결 방향
  const fix={
    seo:'매장 정보·사진을 채우고 키워드 일치도를 높이면 노출이 올라가요.',
    content:'리뷰와 블로그를 꾸준히 보완하면 충분히 상위권으로 올라갈 수 있어요.',
    activity:'최근 리뷰를 꾸준히 쌓으면 신선도 점수가 빠르게 회복돼요.',
  }[weakKey];
  lines.push('🚀 '+fix);

  const box=document.getElementById('commentBox');
  box.innerHTML=lines.map(l=>`<p class="comment-line">${l}</p>`).join('');
}

// ── 버튼 액션 (자리 확보, 동작은 추후) ──────────────────────────────────────
function handleLead(){
  alert('상세 리포트 발송 기능은 준비 중입니다.');
}
function handlePwa(){
  const isIos=/iphone|ipad|ipod/i.test(navigator.userAgent);
  if(isIos) alert('사파리에서 하단 공유 버튼 → "홈 화면에 추가"를 탭하세요.');
  else if(window._pwaPrompt){window._pwaPrompt.prompt();}
  else alert('브라우저 주소창 옆 설치 아이콘을 눌러주세요.');
}
function handleShare(){
  alert('카톡 공유 기능은 준비 중입니다.');
}
window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();window._pwaPrompt=e;});

// ── 탭 전환 ──────────────────────────────────────────────────────────────────
function switchTab(tabId){
  document.querySelectorAll('.tab-btn').forEach(btn=>{
    btn.classList.toggle('active', btn.dataset.tab===tabId);
  });
  document.querySelectorAll('.tab-content').forEach(content=>{
    content.classList.toggle('active', content.id===`tab-${tabId}`);
  });
}

// ── 블로그 분석 ──────────────────────────────────────────────────────────────
async function startBlogAnalysis(){
  const d = window._diagData;
  if(!d || !d.place_id){
    alert('먼저 플레이스 진단을 완료해주세요.');
    return;
  }

  const btn = document.getElementById('btnBlogAnalyze');
  btn.disabled = true;
  btn.textContent = '분석 중...';

  document.getElementById('blogStartCard').style.display = 'none';
  document.getElementById('blogLoading').style.display = 'block';
  document.getElementById('blogResultCard').style.display = 'none';

  // 백엔드가 대표키워드 그대로 + 폭 확대(최대 15개)로 분석하므로 후보 전체를 넘김
  const keywords = (d.keywords_used || []);
  const total = Math.min(keywords.length, 15) || 15;

  try {
    const res = await fetch('/analyze-blog', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        store_name: d.store_name,
        place_id: d.place_id,
        address: d.address || '',
        category: d.category || '',
        keywords: keywords
      })
    });

    document.getElementById('blogBar').style.width = '100%';
    document.getElementById('blogProgress').textContent = `${total}/${total}`;

    if(!res.ok){
      const errText = await res.text();
      throw new Error(errText.slice(0, 200));
    }

    const result = await res.json();
    _blogAnalyzed = true;

    document.getElementById('blogLoading').style.display = 'none';
    document.getElementById('blogResultCard').style.display = 'block';

    renderBlogResults(result.blog_results || []);

  } catch(e) {
    document.getElementById('blogLoading').style.display = 'none';
    document.getElementById('blogStartCard').style.display = 'block';
    btn.disabled = false;
    btn.textContent = '🔍 블로그 노출 분석하기';
    alert('블로그 분석 실패: ' + e.message);
  }
}

// ── 폼 리셋 ──────────────────────────────────────────────────────────────────
function resetForm(){
  document.getElementById('result').style.display='none';
  document.getElementById('input-section').style.display='block';
  document.getElementById('storeName').value='';
  document.getElementById('placeUrl').value='';
  ['adPlace','adPowerlink','adLocal','adBlog'].forEach(id=>{
    const el=document.getElementById(id);
    if(el) el.checked=false;
  });
  // 진단 버튼 초기화
  const btn = document.getElementById('diagBtn');
  btn.disabled = false;
  btn.textContent = '🔍 진단하기';
  // 블로그 분석 상태 초기화
  _blogAnalyzed = false;
  _prevAnalysis = null;
  _lastPrevRankMap = {};
  document.getElementById('blogStartCard').style.display = 'block';
  document.getElementById('blogLoading').style.display = 'none';
  document.getElementById('blogResultCard').style.display = 'none';
  document.getElementById('btnBlogAnalyze').disabled = false;
  document.getElementById('btnBlogAnalyze').textContent = '🔍 블로그 노출 분석하기';
  // 탭 표시 복구 및 초기화
  document.querySelector('.tabs').style.display = 'flex';
  document.getElementById('tab-place').style.display = '';
  switchTab('place');
  window.scrollTo({top:0,behavior:'smooth'});
}

document.getElementById('placeUrl').addEventListener('keydown',e=>{if(e.key==='Enter')startAnalysis();});
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


@app.post("/diagnose", tags=["진단"])
async def diagnose_endpoint(req: schemas.DiagnoseRequest, db: Session = Depends(get_db)):
    """
    매장명과 네이버 플레이스 URL을 받아 진단 결과를 반환합니다.
    24시간 이내 동일 매장 결과가 있으면 DB 캐시를 반환합니다.
    force_refresh=true 로 강제 재크롤링 가능합니다.
    직전 분석 기록(prev_analysis)도 함께 반환합니다.
    """
    import json as json_module
    place_id = _extract_place_id(req.place_url)

    # 키워드광고 체크박스 입력
    ad_flags = {
        "place":     req.ad_place,
        "powerlink": req.ad_powerlink,
        "local":     req.ad_local,
        "blog":      req.ad_blog,
    }

    # 직전 분석 기록 조회
    prev_analysis = None
    if place_id:
        prev_record = crud.get_previous_analysis(db, place_id, "place")
        if prev_record:
            prev_analysis = {
                "total_score": prev_record.total_score,
                "analyzed_at": prev_record.analyzed_at.isoformat() if prev_record.analyzed_at else None,
                "result_json": prev_record.result_json,
            }

    if place_id and not req.force_refresh:
        cached = crud.get_cached_result(db, place_id)
        if cached:
            cached["cached"] = True
            cached["ad_flags"] = ad_flags
            cached["prev_analysis"] = prev_analysis
            apply_ad_flags(cached.get("scores", {}), ad_flags)
            return cached

    try:
        future = asyncio.run_coroutine_threadsafe(
            diagnose_store(req.store_name, req.place_url, ad_flags=ad_flags),
            _proactor_loop,
        )
        result = await asyncio.get_running_loop().run_in_executor(
            None, future.result, 600
        )
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())

    try:
        crud.save_diagnosis(db, result, req.place_url)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"DB 저장 실패: {e}")

    # 히스토리에 누적 저장
    result_place_id = result.get("place_id") or place_id
    if result_place_id:
        try:
            crud.save_analysis_history(
                db,
                place_id=result_place_id,
                store_name=result.get("store_name", req.store_name),
                analysis_type="place",
                total_score=result.get("scores", {}).get("total"),
                result_json=json_module.dumps(result, ensure_ascii=False),
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"히스토리 저장 실패: {e}")

    result["cached"] = False
    result["prev_analysis"] = prev_analysis
    return result


@app.get("/store/{place_id}/history", tags=["진단"])
def get_history(place_id: str, db: Session = Depends(get_db)):
    """매장의 순위·점수 스냅샷 이력을 반환합니다."""
    history = crud.get_store_history(db, place_id)
    if not history:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다")
    return history


@app.post("/analyze-blog", response_model=schemas.BlogAnalyzeResponse, tags=["진단"])
async def analyze_blog(req: schemas.BlogAnalyzeRequest):
    """
    블로그 순위 분석을 별도로 실행합니다.
    플레이스 진단 완료 후 사용자가 요청할 때만 호출됩니다.
    """
    if not req.place_id:
        raise HTTPException(status_code=400, detail="place_id가 필요합니다")

    # 플마 블로그 모드와 동일하게 키워드 구성:
    #   - place 대표키워드(keywordList 기반, keywords_used)를 "그대로" 사용
    #   - 브랜드 단독 키워드만 제거 (브랜드명만으론 블로그 매칭 의미 없음)
    # ※ 메뉴 재조합(generate_blog_keywords)은 플마에 없는 로직 → 사용 안 함.
    #   플마는 대표키워드(예: '오산피부관리')를 그대로 검색해 우리 블로그를 잡아냄.
    #   핵심은 키워드 "폭" — 히트가 여러 키워드에 흩어져 있어 충분히 많이 돌려야 함.
    import re as _re

    _brand_base = _re.sub(r"(본점|직영점|지점|점)$", "", req.store_name.strip()).strip()
    _brand_only = {req.store_name.strip(), _brand_base}
    keywords = [k for k in (req.keywords or []) if k and k not in _brand_only]

    if not keywords:
        raise HTTPException(status_code=400, detail="분석할 키워드가 없습니다")

    try:
        future = asyncio.run_coroutine_threadsafe(
            analyze_blog_ranking(
                store_name=req.store_name,
                place_id=req.place_id,
                address=req.address,
                keywords=keywords,
                max_keywords=15,
            ),
            _proactor_loop,
        )
        blog_results = await asyncio.get_running_loop().run_in_executor(
            None, future.result, 300  # 최대 5분
        )
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())

    total_matched = sum(
        len([h for h in r.get("hits", []) if h.get("rank")])
        for r in blog_results
    )

    return {
        "blog_results": blog_results,
        "total_matched": total_matched,
        "analyzed_keywords": len(blog_results),
    }


@app.post("/analyze-blog-standalone", tags=["진단"])
async def analyze_blog_standalone(req: schemas.BlogStandaloneRequest, db: Session = Depends(get_db)):
    """
    블로그 순위 분석만 단독으로 실행합니다.
    1. 기존 플레이스 분석이 있으면 → 그 때 추출한 키워드 그대로 사용
    2. 없으면 → 매장 정보 크롤링 후 키워드 추출
    """
    import json as json_module
    from .core.keywords import generate_keywords, generate_blog_keywords

    place_id = _extract_place_id(req.place_url)
    keywords = []
    address = ""
    category = ""

    # 직전 블로그 분석 기록 조회
    prev_analysis = None
    if place_id:
        prev_record = crud.get_previous_analysis(db, place_id, "blog")
        if prev_record:
            prev_analysis = {
                "total_score": prev_record.total_score,
                "analyzed_at": prev_record.analyzed_at.isoformat() if prev_record.analyzed_at else None,
                "result_json": prev_record.result_json,
            }

    # 1. 기존 플레이스 분석 결과에서 정보 가져오기
    if place_id:
        prev_place_record = crud.get_previous_analysis(db, place_id, "place")
        if prev_place_record and prev_place_record.result_json:
            try:
                prev_data = json_module.loads(prev_place_record.result_json)
                keywords = prev_data.get("keywords_used", [])
                address = prev_data.get("address", "")
                category = prev_data.get("category", "")
            except Exception:
                pass

    try:
        # 2. 주소가 없으면 매장 정보 크롤링
        if not address:
            from .core.scraper import fetch_store_info_only

            future = asyncio.run_coroutine_threadsafe(
                fetch_store_info_only(req.place_url),
                _proactor_loop,
            )
            store_info = await asyncio.get_running_loop().run_in_executor(
                None, future.result, 120
            )

            address = store_info.get("address", "")
            category = store_info.get("category", "")

            if not keywords:
                keywords = generate_keywords(
                    store_name=req.store_name,
                    category=category,
                    address=address,
                    menu_items=store_info.get("menu_items", []),
                    official_keywords=store_info.get("official_keywords", []),
                    nearby_station=store_info.get("nearby_station", ""),
                    keyword_list=store_info.get("keyword_list", []),
                )

        # 3. 블로그 분석용 키워드 생성 (지역+업종 조합)
        # 기존 키워드가 일반적인 것만 있으면 블로그용 키워드 추가
        blog_keywords = generate_blog_keywords(req.store_name, address, category)
        if blog_keywords:
            # 블로그용 키워드를 앞에 배치
            combined = blog_keywords + [k for k in keywords if k not in blog_keywords]
            keywords = combined

        # 블로그 분석 (상위 10개 키워드)
        future2 = asyncio.run_coroutine_threadsafe(
            analyze_blog_ranking(
                store_name=req.store_name,
                place_id=place_id or "",
                address=address,
                keywords=keywords[:10],
                max_keywords=10,
            ),
            _proactor_loop,
        )
        blog_results = await asyncio.get_running_loop().run_in_executor(
            None, future2.result, 300
        )
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())

    total_matched = sum(
        len([h for h in r.get("hits", []) if h.get("rank")])
        for r in blog_results
    )

    result = {
        "store_name": req.store_name,
        "place_id": place_id,
        "address": address,
        "category": category,
        "blog_results": blog_results,
        "total_matched": total_matched,
        "analyzed_keywords": len(blog_results),
        "prev_analysis": prev_analysis,
        "keywords_used": keywords[:10],
    }

    # 히스토리에 누적 저장
    if place_id:
        try:
            crud.save_analysis_history(
                db,
                place_id=place_id,
                store_name=req.store_name,
                analysis_type="blog",
                total_score=None,
                result_json=json_module.dumps(result, ensure_ascii=False),
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"블로그 히스토리 저장 실패: {e}")

    return result


@app.post("/lead", response_model=schemas.LeadResponse, tags=["리드"])
def create_lead(req: schemas.LeadRequest, db: Session = Depends(get_db)):
    """연락처(리드)를 저장합니다."""
    lead = crud.create_lead(db, contact=req.contact, source=req.source, store_id=req.store_id)
    return lead
