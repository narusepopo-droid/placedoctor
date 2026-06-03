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
from .core.scraper import diagnose_store

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
.kw-list{display:flex;flex-wrap:wrap;gap:8px;}
.kw-item{background:var(--gray-50);border:1px solid var(--gray-200);border-radius:10px;padding:8px 12px;}
.kw-item.kw-opp{background:#fff;border-color:transparent;box-shadow:0 1px 8px rgba(0,0,0,.09);}
.kw-row{display:flex;align-items:center;gap:6px;}
.kw-text{font-size:.82rem;font-weight:600;flex:1;}
.kw-rank{font-size:.72rem;font-weight:700;padding:2px 8px;border-radius:6px;color:#fff;white-space:nowrap;}
.kw-comment{font-size:.75rem;color:var(--gray-600);margin-top:5px;line-height:1.5;}
.kw-more{margin-top:10px;font-size:.82rem;color:var(--green-d);font-weight:600;cursor:pointer;}

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
      <label style="display:flex;align-items:center;gap:6px;font-size:.82rem;color:var(--gray-600);margin-bottom:14px;cursor:pointer;">
        <input type="checkbox" id="forceRefresh"> 강제 재크롤링
      </label>
      <button class="btn-diagnose" id="diagBtn" onclick="diagnose()">🔍 진단하기</button>
      <div class="status-msg" id="statusMsg"></div>
    </div>
    <div id="errBox"></div>
  </div>

  <!-- LOADING -->
  <div id="loading-section">
    <div class="l-card">
      <span class="l-pulse">🩺</span>
      <div class="l-title">플레이스 진단 중이에요</div>
      <div class="l-sub">키워드를 하나씩 검색하고 있어요 · 1~3분 소요</div>
      <div class="l-bar-wrap"><div class="l-bar" id="lBar"></div></div>
      <div class="l-pct" id="lPct">0%</div>
      <div class="l-steps" id="lSteps"></div>
      <div class="l-tip" id="lTip"></div>
    </div>
  </div>

  <!-- RESULT -->
  <div id="result">
    <div class="result-header">
      <div class="store-badge">📍 <span id="rCategory"></span></div>
      <div class="store-name" id="rStoreName"></div>
      <div class="store-meta" id="rMeta"></div>
    </div>

    <!-- GAUGE -->
    <div class="card">
      <div class="card-title">종합 플레이스 점수</div>
      <div class="gauge-wrap">
        <svg class="gauge-svg" width="160" height="160" viewBox="0 0 160 160">
          <circle class="gauge-track" cx="80" cy="80" r="66"/>
          <circle class="gauge-fill" id="gaugeFill" cx="80" cy="80" r="66" stroke-dasharray="0 415" stroke="#22c55e"/>
          <text class="gauge-text" id="gaugeNum" x="80" y="76" fill="#111827">0</text>
          <text class="gauge-sub" x="80" y="98">/100점</text>
        </svg>
        <span class="grade-badge" id="gradeBadge">-</span>
        <p class="gauge-summary" id="gaugeSummary"></p>
      </div>
    </div>

    <!-- 4-AXIS -->
    <div style="margin-top:6px;font-size:.82rem;font-weight:700;color:var(--gray-600);padding:12px 0 0;">진단 상세</div>
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

    <div class="btn-redo"><button onclick="resetForm()">← 다시 진단하기</button></div>
  </div>

</div>
<script>
// ── 상태 ──────────────────────────────────────────────────────────────────────
const CIRC = 2 * Math.PI * 66; // ≈ 414.7
let _allKw = [], _kwExpanded = false;

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
  if(s>=90)return{text:'A등급 · 최우수',bg:'#22c55e'};
  if(s>=70)return{text:'B등급 · 우수',bg:'#22c55e'};
  if(s>=50)return{text:'C등급 · 보통',bg:'#f97316'};
  if(s>=30)return{text:'D등급 · 미흡',bg:'#f97316'};
  return{text:'F등급 · 위험',bg:'#ef4444'};
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
  { label:'키워드 순위 분석 중',  icon:'📊', desc:'검색 키워드 20개를 하나씩 확인하고 있어요 (가장 오래 걸려요)', ms:80000 },
  { label:'리뷰·별점 수집 중',   icon:'⭐', desc:'방문자 리뷰, 블로그 리뷰, 별점 데이터를 모으고 있어요', ms:20000 },
  { label:'경쟁사 비교 중',      icon:'🏆', desc:'같은 키워드 1위 매장 정보를 분석하고 있어요',         ms:20000 },
  { label:'점수 계산 중',        icon:'📝', desc:'4축 진단 점수를 계산하고 있어요 — 거의 다 됐어요!',    ms:999999 },
];
const L_TIPS = [
  '💡 방문자 리뷰 50개 이상이면 검색 노출에 유리해요',
  '💡 플레이스 사진은 최소 10장 이상 등록하면 점수가 올라요',
  '💡 키워드가 업종·지역과 잘 맞을수록 상위 노출 가능성이 높아요',
  '💡 최근 30일 이내 리뷰가 있으면 활성도 점수가 높아져요',
  '💡 매장 정보(주소·전화·영업시간)가 완전할수록 노출에 유리해요',
];

let _lStart=0, _lTimer=null, _lStepIdx=0, _lRafId=null, _lProg=0;

function startLoading(){
  _lStart=Date.now(); _lStepIdx=0; _lProg=0;
  document.getElementById('lBar').style.width='0%';
  document.getElementById('lPct').textContent='0%';
  document.getElementById('lTip').textContent=L_TIPS[Math.floor(Math.random()*L_TIPS.length)];
  _renderLSteps(0);
  _animateLBar();
  _lTimer=setInterval(_advanceLStep,1000);
}

function stopLoading(){
  clearInterval(_lTimer); cancelAnimationFrame(_lRafId);
  document.getElementById('lBar').style.width='100%';
  document.getElementById('lPct').textContent='100%';
}

function _advanceLStep(){
  const elapsed=Date.now()-_lStart;
  let cum=0, idx=0;
  for(let i=0;i<L_STEPS.length;i++){cum+=L_STEPS[i].ms;if(elapsed<cum){idx=i;break;}idx=L_STEPS.length-1;}
  if(idx!==_lStepIdx){_lStepIdx=idx;_renderLSteps(idx);}
  // rotate tip every 20s
  const tipIdx=Math.floor(elapsed/20000)%L_TIPS.length;
  document.getElementById('lTip').textContent=L_TIPS[tipIdx];
}

function _renderLSteps(active){
  document.getElementById('lSteps').innerHTML=L_STEPS.map((s,i)=>{
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

// ── 메인 진단 요청 ────────────────────────────────────────────────────────────
async function diagnose(){
  const name = document.getElementById('storeName').value.trim();
  const url  = document.getElementById('placeUrl').value.trim();
  const force= document.getElementById('forceRefresh').checked;
  if(!name||!url){alert('매장명과 URL을 입력해주세요.');return;}

  const btn = document.getElementById('diagBtn');
  btn.disabled=true; btn.textContent='분석 중...';
  document.getElementById('errBox').innerHTML='';

  // 즉시 로딩 화면으로 전환 (버튼 클릭 즉시 발생)
  document.getElementById('input-section').style.display='none';
  document.getElementById('loading-section').style.display='block';
  startLoading();
  window.scrollTo({top:0,behavior:'smooth'});

  const _loadStart = Date.now();
  const MIN_SHOW_MS = 1500; // 캐시 응답이 와도 최소 1.5초는 로딩 화면 유지

  try{
    const [res] = await Promise.all([
      fetch('/diagnose',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({store_name:name,place_url:url,force_refresh:force})
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
    // 결과 화면으로 자연스럽게 전환
    document.getElementById('loading-section').style.display='none';
    renderResult(JSON.parse(text));
    document.getElementById('result').style.display='block';
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
  window._diagData = d;  // 키워드 태그에서 competitor 접근용
  const sc = d.scores||{};
  // 매장 정보
  document.getElementById('rStoreName').textContent = d.store_name||'-';
  document.getElementById('rCategory').textContent  = d.category||'매장';
  document.getElementById('rMeta').textContent      = d.address||'';

  // 종합 게이지
  const tot = sc.total??0;
  animateGauge(tot);
  const g = grade(tot);
  const badge = document.getElementById('gradeBadge');
  badge.textContent=g.text; badge.style.background=g.bg;
  document.getElementById('gaugeSummary').textContent = buildSummary(d,sc);

  // 4축 카드
  renderAxisCards(d, sc);

  // 경쟁사
  renderCompetitor(d);

  // 키워드
  _allKw = d.place_results||[];
  renderKeywords(false);

  // 닥터 코멘트
  renderComment(d, sc);
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
    buildAdCard(sc.ad??20),
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
  return axisCard('📍','SEO·노출',score,[
    detailRow('대표 키워드 순위', topRank?`${topRank}위`:'30위 밖', topRank?Math.max(0,100-topRank*3):5),
    detailRow('정보 완성도', infoScore+'%', infoScore),
    detailRow('사진 수', photoCount!=null?photoCount+'장':'-', photoCount!=null?Math.min(100,photoCount*8):null),
  ].join(''));
}

function buildContentCard(d, score){
  const vr = d.visitor_reviews, br = d.blog_reviews, ss = d.star_score;
  return axisCard('⭐','콘텐츠',score,[
    detailRow('방문자 리뷰', vr!=null?fmt(vr)+'개':'-', vr!=null?Math.min(100,vr/5):null),
    detailRow('블로그 리뷰', br!=null?fmt(br)+'개':'-', br!=null?Math.min(100,br/3):null),
    detailRow('별점', ss!=null?ss+'점':'-', ss!=null?(ss>=4.5?90:ss>=4.0?65:ss>=3.5?40:20):null),
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
  const vr=d.visitor_reviews;
  return axisCard('🔥','활성도',score,[
    detailRow('최근 리뷰', dayStr, dayScore),
    detailRow('방문자 리뷰 수', vr!=null?fmt(vr)+'개':'-', vr!=null?Math.min(100,vr/5):null),
    detailRow('정보 최신성', d.address?'최신':'미확인', d.address?80:30),
  ].join(''));
}

function buildAdCard(score){
  const adItems = [
    {name:'플레이스광고', on:false},
    {name:'파워링크', on:false},
    {name:'지역소상공인광고', on:false},
  ];
  const rows = adItems.map(a=>`<div class="detail-row"><span class="detail-label">${a.name}</span><div class="detail-val"><span class="chip chip-bad">${a.on?'✓집행':'✗미집행'}</span></div></div>`).join('');
  const note = '<p style="font-size:.72rem;color:var(--gray-600);margin-top:10px;line-height:1.5;">광고가 켜져 있어도 키워드·소재 최적화로 효율을 더 올릴 수 있어요</p>';
  return axisCard('📣','광고',score, rows + note);
}

// ── 경쟁사 비교 ───────────────────────────────────────────────────────────────
function renderCompetitor(d){
  const comp=d.competitor||{}, compD=comp.details||{}, gap=comp.gap||{};
  if(!comp.competitor_id){document.getElementById('compCard').style.display='none';return;}
  document.getElementById('compCard').style.display='block';

  const myVr=d.visitor_reviews??0, cVr=compD.visitor_reviews??0;
  const maxVr=Math.max(myVr,cVr,1);
  const myBr=d.blog_reviews??0, cBr=compD.blog_reviews??0;
  const maxBr=Math.max(myBr,cBr,1);

  const vrGap=gap.visitor_reviews, brGap=gap.blog_reviews;
  const rankGap=comp.my_rank?comp.my_rank-1:null;

  let rows='';

  // 방문자 리뷰 막대
  rows+=`<div>
    <div class="comp-label">방문자 리뷰</div>
    <div class="comp-bar-wrap">
      <div class="comp-tag" style="color:var(--green);font-size:.7rem;">우리</div>
      <div class="comp-bar-bg"><div class="comp-bar" style="width:${(myVr/maxVr*100).toFixed(0)}%;background:var(--green);">${fmt(myVr)}</div></div>
    </div>
    <div class="comp-bar-wrap" style="margin-top:6px;">
      <div class="comp-tag" style="color:var(--gray-600);font-size:.7rem;">1위</div>
      <div class="comp-bar-bg"><div class="comp-bar" style="width:${(cVr/maxVr*100).toFixed(0)}%;background:var(--gray-400);">${fmt(cVr)}</div></div>
    </div>
    ${vrGap!=null&&vrGap>0?`<p class="comp-gap">▼ ${fmt(vrGap)}개 뒤처져 있어요</p>`:''}
  </div>`;

  // 순위 격차
  if(rankGap!=null&&rankGap>0){
    rows+=`<div class="comp-gap" style="border-top:1px solid var(--gray-100);padding-top:10px;">
      현재 ${comp.my_rank}위 · 1위와 ${rankGap}계단 차이
    </div>`;
  }

  document.getElementById('compRows').innerHTML=rows;
}

// ── 키워드 기회 태그 ──────────────────────────────────────────────────────────
function kwTag(rank, hasComp){
  if(rank!=null && rank<=5)  return {label:'노출 중', color:'#22c55e', priority:4};
  if(rank!=null && rank<=10) return {label:`${rank}위`, color:'#86efac', priority:3};
  if(rank!=null && rank<=15) return {label:'아깝다!', color:'#f97316', priority:1};
  if(rank!=null && rank<=30) return {label:`${rank}위`, color:'#9ca3af', priority:3};
  return hasComp
    ? {label:'경쟁사 우위', color:'#ef4444', priority:0}
    : {label:'놓침', color:'#ef4444', priority:2};
}

// ── 키워드별 규칙 기반 멘트 ──────────────────────────────────────────────────
const KW_COMMENTS = {
  '아깝다!': (r) => `첫 화면(1~5위)까지 ${r-5}계단 남았어요. 지금 이 키워드로 검색하는 손님은 대부분 경쟁사로 가고 있어요.`,
  '놓침':    ()  => `노출이 안 되고 있어요. 이 키워드를 검색하는 신규 고객이 매장을 못 찾습니다.`,
  '경쟁사 우위': () => `같은 동네 경쟁사는 이 키워드를 잡고 있어요. 그만큼 손님을 뺏기는 중이에요.`,
};

// ── 키워드 렌더링 ─────────────────────────────────────────────────────────────
function renderKeywords(expanded){
  _kwExpanded=expanded;
  const list=document.getElementById('kwList');
  const more=document.getElementById('kwMore');
  const comp = (window._diagData||{}).competitor||{};
  const hasComp = !!(comp.competitor_id);

  // 기회 우선 정렬
  const sorted = [..._allKw].sort((a,b)=>{
    const ta=kwTag(a.rank, hasComp), tb=kwTag(b.rank, hasComp);
    return ta.priority - tb.priority;
  });

  const SHOW_COMMENTS_FOR = ['아깝다!','놓침','경쟁사 우위'];
  const show = expanded ? sorted : sorted.slice(0,8);

  list.innerHTML=show.map(k=>{
    const tag=kwTag(k.rank, hasComp);
    const isOpportunity=SHOW_COMMENTS_FOR.includes(tag.label);
    const comment=KW_COMMENTS[tag.label]?.(k.rank);
    return `<div class="kw-item${isOpportunity?' kw-opp':''}">
      <div class="kw-row">
        <span class="kw-text">${esc(k.keyword)}</span>
        <span class="kw-rank" style="background:${tag.color}">${tag.label}</span>
      </div>
      ${comment?`<div class="kw-comment">${esc(comment)}</div>`:''}
    </div>`;
  }).join('');

  if(sorted.length>8){
    more.textContent=expanded?'▲ 접기':`전체 ${sorted.length}개 키워드 보기 →`;
  } else {
    more.textContent='';
  }
}
function toggleKw(){ renderKeywords(!_kwExpanded); }

// ── 닥터 코멘트 ───────────────────────────────────────────────────────────────
function renderComment(d, sc){
  const lines=[];
  const tot=sc.total??0, seo=sc.seo??0, con=sc.content??0, act=sc.activity??0;
  const vr=d.visitor_reviews, ss=d.star_score;
  const kws=d.place_results||[];
  const hitKws=kws.filter(k=>k.rank);

  // 잘하는 점
  if(ss!=null&&ss>=4.5) lines.push(`✅ 별점 ${ss}점으로 고객 신뢰도가 높아요. 이건 큰 강점입니다.`);
  if(hitKws.length>=5) lines.push(`✅ ${hitKws.length}개 키워드에서 30위 이내에 노출되고 있어요.`);
  if(vr!=null&&vr>=100) lines.push(`✅ 방문자 리뷰 ${fmt(vr)}개로 콘텐츠 기반이 탄탄해요.`);

  // 개선 필요
  if(seo<50) lines.push(`🔸 주요 키워드에서 노출이 부족해요. 정보 완성도와 키워드 일치도를 높여보세요.`);
  if(con<50){
    if(vr!=null&&vr<20) lines.push(`🔸 방문자 리뷰가 ${vr}개로 적어요. 리뷰 유도 캠페인이 효과적이에요.`);
    else lines.push(`🔸 콘텐츠(리뷰·별점) 관리를 강화하면 순위 상승에 도움이 돼요.`);
  }
  if(act<50) lines.push(`🔸 최근 리뷰 활성도가 낮아요. 꾸준한 리뷰 관리가 필요해요.`);

  // 희망 메시지
  if(tot<70) lines.push(`💡 현재 점수에서 꾸준히 관리하면 충분히 상위권 진입이 가능한 구간이에요.`);

  const box=document.getElementById('commentBox');
  box.innerHTML=lines.map(l=>`<p class="comment-line">${esc(l)}</p>`).join('');
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

// ── 폼 리셋 ──────────────────────────────────────────────────────────────────
function resetForm(){
  document.getElementById('result').style.display='none';
  document.getElementById('input-section').style.display='block';
  window.scrollTo({top:0,behavior:'smooth'});
}

document.getElementById('placeUrl').addEventListener('keydown',e=>{if(e.key==='Enter')diagnose();});
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
        # ProactorEventLoop 전용 스레드에서 실행 (--reload 모드의 SelectorEventLoop 우회)
        future = asyncio.run_coroutine_threadsafe(
            diagnose_store(req.store_name, req.place_url),
            _proactor_loop,
        )
        result = await asyncio.get_running_loop().run_in_executor(
            None, future.result, 600  # 최대 10분
        )
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())

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
