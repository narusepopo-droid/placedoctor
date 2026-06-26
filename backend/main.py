import asyncio
import logging
import re
import sys
import threading

# uvicorn 구동 시 backend 패키지 로거(scraper의 진단·블로그 분석 로그)가
# 콘솔에 보이도록 핸들러를 1회 설정. uvicorn 자체 로깅과 충돌하지 않게
# backend 패키지 로거에만 핸들러를 붙이고 상위 전파는 끈다.
_pkg_logger = logging.getLogger("backend")
if not _pkg_logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    _pkg_logger.addHandler(_h)
    _pkg_logger.setLevel(logging.INFO)
    _pkg_logger.propagate = False

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .database import engine, get_db
from .models import Base
from . import crud, schemas
from .core.scraper import diagnose_store, diagnose_store_stream, analyze_blog_ranking
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
    title="플레이스랭킹 API",
    description="네이버 플레이스 순위 진단 서비스",
    version="0.4.0",
)

import os
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>네이버 플레이스 순위 무료 확인 | 플레이스랭킹</title>
<meta name="description" content="내 매장 키워드 순위를 무료로 확인하세요. 경쟁 매장과 비교해 네이버 플레이스 노출 현황을 진단합니다.">
<meta name="keywords" content="네이버 플레이스 순위, 플레이스 키워드 순위, 내 플레이스 순위 확인, 플레이스 진단, 네이버 플레이스 검색 순위">
<meta name="robots" content="index, follow">
<meta name="author" content="플레이스랭킹">
<meta property="og:type" content="website">
<meta property="og:title" content="네이버 플레이스 순위 무료 확인 | 플레이스랭킹">
<meta property="og:description" content="내 매장 키워드 순위를 무료로 확인하세요. 경쟁 매장과 비교해 네이버 플레이스 노출 현황을 진단합니다.">
<meta property="og:url" content="https://placeranking.com">
<meta property="og:site_name" content="플레이스랭킹">
<link rel="canonical" href="https://placeranking.com">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128'><rect width='128' height='128' rx='24' fill='%2300C896'/><circle cx='64' cy='50' r='28' fill='white'/><circle cx='64' cy='50' r='14' fill='%2300C896'/><path d='M64 78 L52 106 L64 101 L76 106 Z' fill='white'/></svg>">
<meta name="google-site-verification" content="OMcAcRnijHErEpfd4wIFa9jCXtXAQgVKZ2plesoCYvM" />
<meta name="naver-site-verification" content="df35aa6f9e46b7aa1e5678ee79a5a19ef5a868d6" />
<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<script src="https://unpkg.com/lucide@latest"></script>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "WebApplication",
  "name": "플레이스랭킹",
  "url": "https://placeranking.com",
  "description": "네이버 플레이스 키워드 순위 무료 진단 도구. 플레이스 상위노출, 블로그 노출 현황, 경쟁 매장 비교를 무료로 확인하세요.",
  "applicationCategory": "BusinessApplication",
  "operatingSystem": "Web",
  "offers": {
    "@type": "Offer",
    "price": "0",
    "priceCurrency": "KRW"
  },
  "provider": {
    "@type": "Organization",
    "name": "플레이스랭킹",
    "url": "https://placeranking.com"
  }
}
</script>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "네이버 플레이스 상위노출은 어떻게 결정되나요?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "키워드 일치도, 리뷰 수·점수, 저장수, 최근 활동, 블로그 포스팅 노출 등 복합 알고리즘으로 결정됩니다."
      }
    },
    {
      "@type": "Question",
      "name": "플레이스 광고를 집행 중인데 순위 확인이 필요한가요?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "네이버 플레이스 광고는 유료 노출이고, 자연 순위는 별개입니다. 광고 없이도 어떤 키워드에서 자연 노출되는지 확인하는 것이 진짜 마케팅 실력 파악의 시작입니다."
      }
    },
    {
      "@type": "Question",
      "name": "블로그 체험단 효과, 실제로 순위에 반영되고 있나요?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "블로그 포스팅이 어떤 키워드로 몇 위에 노출되는지 플레이스랭킹 블로그 분석 탭에서 직접 확인할 수 있습니다."
      }
    },
    {
      "@type": "Question",
      "name": "가게 오픈 전에도 경쟁 매장 분석이 가능한가요?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "오픈 예정이라면 경쟁 매장 분석으로 상권 내 키워드 경쟁 강도를 미리 파악할 수 있습니다."
      }
    },
    {
      "@type": "Question",
      "name": "지역소상공인광고와 일반 플레이스 광고 차이는 무엇인가요?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "지역소상공인광고는 네이버가 지원하는 소상공인 전용 광고 상품으로 노출 영역과 비용 구조가 다릅니다. 현재 키워드 순위를 먼저 파악한 후 결정하는 것이 효율적입니다."
      }
    }
  ]
}
</script>
<style>
:root{
  --brand-green:#00C896;--brand-green-dark:#00B085;--brand-green-light:#E8FAF4;
  --green:var(--brand-green);--green-d:var(--brand-green-dark);--green-l:#00d4a4;--green-bg:var(--brand-green-light);
  --primary-gradient:linear-gradient(135deg, var(--brand-green) 0%, #00d4a4 100%);
  --red:#ef4444;--orange:#f97316;--score-green:#22c55e;
  --gray-50:#f8fafc;--gray-100:#f1f5f9;--gray-200:#e2e8f0;--gray-300:#cbd5e1;
  --gray-400:#94a3b8;--gray-500:#64748b;--gray-600:#475569;--gray-700:#334155;--gray-800:#1e293b;--gray-900:#0f172a;
  --radius:16px;--radius-sm:12px;--radius-lg:20px;
  --shadow-sm:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
  --shadow:0 1px 3px rgba(0,0,0,.04),0 1px 2px rgba(0,0,0,.06);
  --shadow-lg:0 10px 40px rgba(0,0,0,.12);
  --shadow-glow:0 4px 24px rgba(0,184,148,.25);
  --card-border:1px solid #e2e8f0;
  --spacing-xs:8px;--spacing-sm:12px;--spacing-md:20px;--spacing-lg:32px;--spacing-xl:48px;
}
*{box-sizing:border-box;margin:0;padding:0;}
body,button,input,textarea,select{font-family:'Pretendard Variable',Pretendard,-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic','Segoe UI',sans-serif;letter-spacing:-.3px;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;}
body{background:linear-gradient(180deg,#F7FDFB 0%,#F4F6F8 320px,#F4F6F8 100%);color:var(--gray-900);min-height:100vh;font-feature-settings:'ss01' on;}

/* HEADER */
.header{background:rgba(255,255,255,.95);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);padding:12px 20px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;border-bottom:1px solid rgba(226,232,240,.6);}
.logo{display:flex;align-items:center;height:36px;}
.logo-full{height:100%;}
.header-badge{background:var(--primary-gradient);color:#fff;font-size:.7rem;font-weight:600;padding:5px 12px;border-radius:20px;box-shadow:var(--shadow-sm);}

/* MAIN */
.main{max-width:540px;margin:0 auto;padding:var(--spacing-md) 16px 100px;}
@media(min-width:768px){.main{max-width:1040px;padding-left:24px;padding-right:24px;}}
@media(min-width:768px){.hero h1,.hero-sub{max-width:600px;margin-left:auto;margin-right:auto;}}
@media(min-width:768px){.axis-grid{gap:20px;}}

/* INPUT CARD */
.input-card{background:#fff;border-radius:var(--radius-lg);border:var(--card-border);padding:28px 24px;box-shadow:var(--shadow);}
.input-card h2{font-size:1.2rem;font-weight:700;margin-bottom:6px;letter-spacing:-.3px;}
.input-card p{font-size:.875rem;color:var(--gray-500);margin-bottom:var(--spacing-md);}
.field{margin-bottom:var(--spacing-md);}
.field label{display:block;font-size:.82rem;font-weight:600;color:var(--gray-700);margin-bottom:8px;letter-spacing:-.2px;}
.field input{width:100%;padding:14px 16px;border:1.5px solid var(--gray-200);border-radius:var(--radius-sm);font-size:.95rem;outline:none;transition:all .2s ease;background:#fff;}
.field input:focus{border-color:var(--green);box-shadow:0 0 0 3px rgba(0,184,148,.1);}
.field input::placeholder{color:var(--gray-400);}
.btn-diagnose{width:100%;padding:16px;background:var(--primary-gradient);color:#fff;border:none;border-radius:var(--radius-sm);font-size:1.02rem;font-weight:700;cursor:pointer;transition:all .25s ease;margin-top:8px;box-shadow:var(--shadow-glow);letter-spacing:-.2px;}
.btn-diagnose:hover{transform:translateY(-2px);box-shadow:0 6px 28px rgba(0,184,148,.35);}
.btn-diagnose:active{transform:translateY(0);}
.btn-diagnose:disabled{background:var(--gray-300);cursor:not-allowed;box-shadow:none;transform:none;}
.status-msg{text-align:center;color:var(--gray-500);font-size:.85rem;margin-top:var(--spacing-sm);min-height:20px;}

/* LANDING */
.landing{margin-bottom:var(--spacing-sm);}
.hero{text-align:center;padding:var(--spacing-xl) var(--spacing-md) var(--spacing-lg);}
.hero-icon{font-size:3.2rem;display:block;margin-bottom:var(--spacing-md);filter:drop-shadow(0 4px 12px rgba(0,184,148,.3));}
.hero h1{font-size:1.75rem;font-weight:700;line-height:1.4;letter-spacing:-.8px;color:var(--gray-900);margin-bottom:var(--spacing-sm);}
.hero h1 .accent{background:var(--primary-gradient);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.hero-sub{font-size:1rem;color:var(--gray-500);line-height:1.7;margin:0 auto var(--spacing-lg);max-width:340px;letter-spacing:-.2px;}
.hero-cta{width:100%;max-width:320px;padding:17px 28px;background:var(--primary-gradient);color:#fff;border:none;border-radius:var(--radius);font-size:1.08rem;font-weight:700;cursor:pointer;transition:all .3s cubic-bezier(.4,0,.2,1);box-shadow:var(--shadow-glow);letter-spacing:-.2px;}
.hero-cta:hover{transform:translateY(-3px);box-shadow:0 8px 32px rgba(0,184,148,.4);}
.hero-cta:active{transform:translateY(-1px);}
.hero-note{font-size:.8rem;color:var(--gray-400);margin-top:var(--spacing-sm);display:flex;align-items:center;justify-content:center;gap:8px;}
.hero-note::before,.hero-note::after{content:'';width:24px;height:1px;background:var(--gray-200);}
.lp-section{margin-top:48px;}
@media(min-width:768px){.lp-section{margin-top:72px;}}
.lp-section-title{position:relative;font-size:1.4rem;font-weight:700;color:#2D3A4A;letter-spacing:-.5px;text-align:center;margin-bottom:20px;padding-top:18px;}
.lp-section-title::before{content:"";position:absolute;top:0;left:50%;transform:translateX(-50%);width:28px;height:3px;border-radius:2px;background:var(--green);}
.value-grid{display:grid;grid-template-columns:1fr 1fr;gap:var(--spacing-sm);}
@media(min-width:768px){.value-grid{gap:16px;}}
.value-card{background:#fff;border:1px solid var(--gray-200);border-radius:var(--radius);padding:28px 20px;text-align:center;transition:all .25s ease;box-shadow:var(--shadow-sm);display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:130px;}
@media(min-width:768px){.value-card{min-height:150px;padding:32px 24px;}}
.value-card:hover{transform:translateY(-4px);box-shadow:var(--shadow);border-color:var(--green);}
.value-card .v-icon{font-size:2rem;display:block;margin-bottom:var(--spacing-sm);}
.value-card .v-icon-luc{width:30px;height:30px;stroke-width:1.8;margin:0 auto 10px;}
.value-card .v-title{font-size:1rem;font-weight:700;color:var(--gray-800);margin-bottom:4px;letter-spacing:-.2px;}
.value-card .v-desc{font-size:.82rem;color:var(--gray-500);line-height:1.55;}
.steps{display:flex;flex-direction:column;gap:var(--spacing-sm);}
.step{display:flex;align-items:flex-start;gap:16px;background:#fff;border:1px solid var(--gray-200);border-radius:var(--radius);padding:20px;transition:all .25s ease;box-shadow:var(--shadow-sm);}
.step:hover{transform:translateY(-2px);box-shadow:var(--shadow);}
.step-num{flex-shrink:0;width:36px;height:36px;border-radius:50%;background:var(--primary-gradient);color:#fff;font-weight:700;font-size:1rem;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,184,148,.3);}
.step-body .s-title{font-size:.95rem;font-weight:700;color:var(--gray-800);margin-bottom:4px;letter-spacing:-.2px;}
.step-body .s-desc{font-size:.84rem;color:var(--gray-500);line-height:1.55;}
@media(min-width:768px){
  .steps{flex-direction:column;gap:14px;max-width:600px;margin-left:auto;margin-right:auto;}
  .step{min-height:88px;display:flex;flex-direction:row;align-items:center;width:100%;gap:12px;box-sizing:border-box;}
  .step .step-body{display:flex;flex-direction:column;}
}
.preview-card{background:#fff;border:1px solid var(--gray-200);border-radius:var(--radius-lg);padding:var(--spacing-lg) var(--spacing-md);text-align:center;box-shadow:var(--shadow);}
.preview-gauge{position:relative;width:150px;height:150px;margin:0 auto;}
.preview-score{position:absolute;top:50%;left:50%;transform:translate(-50%,-52%);font-size:2.4rem;font-weight:800;color:var(--green-d);line-height:1;}
.preview-score small{display:block;font-size:.7rem;color:var(--gray-400);font-weight:600;margin-top:4px;letter-spacing:-.2px;}
.preview-trend{margin-top:var(--spacing-md);display:inline-flex;align-items:center;gap:8px;background:linear-gradient(135deg,#dcfce7,#bbf7d0);border:1px solid #86efac;border-radius:var(--radius-sm);padding:10px 18px;font-size:.88rem;font-weight:700;color:#16a34a;}
.preview-kw{margin-top:var(--spacing-md);display:flex;flex-direction:column;gap:10px;}
.preview-kw-row{display:flex;justify-content:space-between;align-items:center;font-size:.86rem;padding:12px 16px;background:var(--gray-50);border-radius:var(--radius-sm);transition:all .2s;}
.preview-kw-row:hover{background:var(--green-bg);}
.preview-kw-row .pk-name{color:var(--gray-700);font-weight:600;}
.preview-kw-row .pk-rank{color:var(--green-d);font-weight:700;}
.preview-caption{font-size:.78rem;color:var(--gray-400);margin-top:var(--spacing-md);}
.search-divider{text-align:center;margin:var(--spacing-xl) 0 var(--spacing-md);}
.search-divider .sd-title{font-size:1.4rem;font-weight:700;color:#2D3A4A;letter-spacing:-.5px;}
.search-divider .sd-sub{font-size:.88rem;color:var(--gray-500);margin-top:6px;}

/* RESULT */
#result{display:none;}
.result-header{text-align:center;padding:var(--spacing-lg) 0 var(--spacing-sm);}
.store-badge{display:inline-flex;align-items:center;gap:6px;background:var(--primary-gradient);color:#fff;font-size:.78rem;font-weight:600;padding:5px 14px;border-radius:20px;margin-bottom:var(--spacing-sm);box-shadow:var(--shadow-sm);}
.store-name{font-size:1.5rem;font-weight:800;margin-bottom:6px;letter-spacing:-.4px;}
.store-meta{font-size:.85rem;color:var(--gray-500);}

/* GAUGE CARD */
.card{background:#fff;border-radius:var(--radius);border:var(--card-border);padding:24px;margin-top:var(--spacing-sm);box-shadow:var(--shadow-sm);transition:all .25s ease;}
.card:hover{box-shadow:var(--shadow);}
.card-title{font-size:.8rem;font-weight:700;color:var(--gray-500);text-transform:uppercase;letter-spacing:.8px;margin-bottom:var(--spacing-md);}
.gauge-wrap{display:flex;flex-direction:column;align-items:center;gap:12px;}
.gauge-svg{overflow:visible;}
.gauge-track{fill:none;stroke:var(--gray-100);stroke-width:12;}
.gauge-fill{fill:none;stroke-width:12;stroke-linecap:round;transition:stroke-dasharray 1.2s cubic-bezier(.4,0,.2,1),stroke .4s;transform:rotate(-90deg);transform-origin:50% 50%;}
.gauge-text{font-size:2.2rem;font-weight:800;text-anchor:middle;dominant-baseline:middle;}
.gauge-sub{font-size:.9rem;fill:var(--gray-600);text-anchor:middle;}
.grade-badge{font-size:1rem;font-weight:700;padding:6px 18px;border-radius:20px;color:#fff;}
.gauge-summary{font-size:.88rem;color:var(--gray-600);text-align:center;max-width:260px;}
/* 블로그 노출 요약 헤드라인 (게이지 대체) */
.blog-headline{display:flex;flex-direction:column;align-items:center;gap:6px;padding:10px 0 4px;}
.blog-headline .bh-num{font-size:3rem;font-weight:800;line-height:1;background:var(--primary-gradient);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.blog-headline .bh-num small{font-size:1.1rem;font-weight:700;-webkit-text-fill-color:var(--gray-500);color:var(--gray-500);margin-left:3px;}
.blog-headline .bh-sub{font-size:.92rem;font-weight:600;color:var(--gray-700);}
.blog-headline .bh-sub b{color:var(--brand-green);font-weight:800;}
.blog-headline .bh-empty{font-size:1.05rem;font-weight:700;color:var(--gray-500);}

/* J단계: 히스토리 추세 */
.analysis-history-info{margin-top:8px;font-size:.82rem;color:var(--gray-500);text-align:center;}
.score-trend{margin-top:16px;text-align:center;padding:14px 16px;border-radius:12px;}
.score-trend.trend-up{background:linear-gradient(135deg,#dcfce7,#bbf7d0);border:2px solid #22c55e;}
.score-trend.trend-down{background:linear-gradient(135deg,#fef2f2,#fecaca);border:2px solid #f87171;}
.score-trend.trend-same{background:var(--gray-50);border:1px solid var(--gray-200);}
.trend-main{display:flex;align-items:center;justify-content:center;gap:8px;}
.trend-arrow{font-size:1.5rem;font-weight:800;}
.trend-up .trend-arrow{color:#16a34a;}
.trend-down .trend-arrow{color:#dc2626;}
.trend-same .trend-arrow{color:var(--gray-400);}
.trend-diff{font-size:1.4rem;font-weight:800;}
.trend-up .trend-diff{color:#16a34a;}
.trend-down .trend-diff{color:#dc2626;}
.trend-same .trend-diff{color:var(--gray-500);}
.trend-vs{font-size:.85rem;color:var(--gray-500);font-weight:500;}
.trend-ment{margin-top:8px;font-size:.9rem;color:var(--gray-700);font-weight:500;}
.kw-trend{display:inline-block;margin-left:6px;font-size:.75rem;color:var(--gray-500);}
.kw-trend .up{color:#16a34a;}
.kw-trend .down{color:#dc2626;}
.kw-trend .same{color:var(--gray-400);}
.kw-trend .kw-date{font-size:.65rem;color:var(--gray-400);margin-right:2px;}
.kw-first{font-size:.72rem;color:var(--gray-400);margin-left:4px;}
/* S단계: 날짜별 순위 흐름 */
.kw-trend-flow{display:inline-flex;align-items:center;gap:4px;margin-left:6px;font-size:.72rem;}
.kw-trend-flow.up .trend-rank:last-child{color:#16a34a;font-weight:700;}
.kw-trend-flow.down .trend-rank:last-child{color:#dc2626;font-weight:700;}
.trend-item{display:inline-flex;flex-direction:column;align-items:center;gap:1px;}
.trend-date{font-size:.6rem;color:var(--gray-400);}
.trend-rank{font-size:.72rem;color:var(--gray-600);}
.trend-arrow{color:var(--gray-300);font-size:.65rem;margin:0 2px;}

/* SEO 콘텐츠 섹션 */
.seo-why-section{max-width:100%;margin:var(--spacing-xl) 0;padding:var(--spacing-lg) var(--spacing-md);background:#fff;border-radius:var(--radius-lg);text-align:center;box-shadow:var(--shadow);border:1px solid var(--gray-200);}
.seo-why-section h2{font-size:1.4rem;font-weight:700;color:#2D3A4A;margin-bottom:var(--spacing-md);letter-spacing:-.5px;line-height:1.45;}
.seo-why-section p{font-size:.9rem;color:var(--gray-600);line-height:1.8;margin-bottom:var(--spacing-sm);}
.seo-faq-section{max-width:100%;margin:var(--spacing-xl) 0;padding:0;}
.seo-faq-section h2{font-size:1.4rem;font-weight:700;color:#2D3A4A;margin-bottom:var(--spacing-md);text-align:center;letter-spacing:-.5px;}
.faq-item{border-bottom:1px solid var(--gray-200);background:#fff;margin-bottom:var(--spacing-xs);border-radius:var(--radius-sm);border:1px solid var(--gray-200);overflow:hidden;}
.faq-q{width:100%;text-align:left;background:#fff;border:none;padding:18px 20px;font-size:.92rem;font-weight:600;color:var(--gray-800);cursor:pointer;display:flex;justify-content:space-between;align-items:center;transition:background .2s;}
.faq-q:hover{background:var(--gray-50);}
.faq-q::after{content:'+';font-size:1.25rem;color:var(--green);flex-shrink:0;transition:transform .2s;}
.faq-q.open::after{content:'−';transform:rotate(180deg);}
.faq-a{display:none;padding:0 20px 18px;font-size:.88rem;color:var(--gray-600);line-height:1.8;}
.faq-a.open{display:block;}
.seo-cta-section{max-width:100%;margin:var(--spacing-xl) 0 64px;padding:var(--spacing-xl) var(--spacing-md);background:linear-gradient(135deg,#f0fdf8 0%,#dcfce7 100%);border-radius:var(--radius-lg);text-align:center;border:1px solid #bbf7d0;box-shadow:var(--shadow);}
@media(min-width:768px){.seo-why-section>*,.seo-cta-section>*{max-width:760px;margin-left:auto;margin-right:auto;}}
.cta-label{font-size:.72rem;font-weight:700;color:var(--green);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:var(--spacing-sm);}
.seo-cta-section h2{font-size:1.25rem;font-weight:800;color:var(--gray-900);margin-bottom:var(--spacing-sm);line-height:1.45;letter-spacing:-.3px;}
.cta-desc{font-size:.9rem;color:var(--gray-600);line-height:1.75;margin-bottom:var(--spacing-lg);}
.cta-btn{display:inline-block;background:#FEE500;color:#1A1A1A;font-weight:700;font-size:.95rem;padding:15px 36px;border-radius:var(--radius-sm);text-decoration:none;margin-bottom:var(--spacing-sm);border:none;cursor:pointer;transition:all .25s ease;box-shadow:0 4px 16px rgba(254,229,0,.4);}
.cta-btn:hover{transform:translateY(-2px);box-shadow:0 6px 24px rgba(254,229,0,.5);}


/* K단계: 최근 본 매장 */
.recent-stores-section{margin-top:var(--spacing-lg);padding:0 4px;}
.recent-stores-header{font-size:.92rem;font-weight:700;color:var(--gray-700);margin-bottom:var(--spacing-sm);display:flex;align-items:center;gap:8px;}
.recent-stores-header::before{content:'';width:4px;height:16px;background:var(--primary-gradient);border-radius:2px;}
.recent-stores-list{display:flex;flex-direction:column;gap:var(--spacing-xs);}
.recent-store-item{background:#fff;border-radius:var(--radius);border:var(--card-border);padding:16px 18px;cursor:pointer;transition:all .25s ease;box-shadow:var(--shadow-sm);}
.recent-store-item:hover{transform:translateY(-2px);border-color:var(--green);box-shadow:var(--shadow);}
.recent-store-name{font-size:.98rem;font-weight:700;color:var(--gray-800);letter-spacing:-.2px;}
.recent-store-meta{font-size:.82rem;color:var(--gray-500);margin-top:6px;display:flex;gap:10px;flex-wrap:wrap;}
.recent-store-score{font-size:.88rem;font-weight:700;color:var(--green);}
.recent-store-time{font-size:.78rem;color:var(--gray-400);}
.recent-stores-empty{font-size:.88rem;color:var(--gray-400);text-align:center;padding:var(--spacing-lg) 0;}

/* M단계: 내 매장 / 경쟁 매장 섹션 */
.registered-section{margin-top:24px;padding:0 4px;}
.registered-header{display:flex;align-items:center;gap:8px;margin-bottom:12px;}
.registered-title{font-size:.95rem;font-weight:700;color:var(--gray-700);}
.registered-count{font-size:.8rem;color:var(--gray-500);font-weight:500;}
.registered-desc{font-size:.8rem;color:var(--gray-500);margin-bottom:12px;}
.registered-list{display:flex;flex-direction:column;gap:10px;}
.registered-item{background:#fff;border-radius:14px;border:1px solid var(--gray-100);padding:14px 16px;cursor:pointer;transition:transform .15s,box-shadow .15s;position:relative;}
.registered-item:hover{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,.08);}
.registered-item.my-store{border-left:3px solid var(--green);}
.registered-item.rival-store{border-left:3px solid #f59e0b;}
.registered-name{font-size:.95rem;font-weight:700;color:var(--gray-800);display:flex;align-items:center;gap:6px;}
.registered-badge{font-size:.65rem;padding:2px 6px;border-radius:4px;font-weight:600;}
.registered-badge.my{background:#dcfce7;color:#16a34a;}
.registered-badge.rival{background:#fef3c7;color:#d97706;}
.registered-meta{font-size:.8rem;color:var(--gray-500);margin-top:6px;display:flex;gap:10px;flex-wrap:wrap;}
.registered-rank{font-weight:600;color:var(--green);}
.registered-score{font-weight:600;}
.registered-empty{font-size:.85rem;color:var(--gray-400);text-align:center;padding:16px 0;}
.registered-add-btn{display:inline-flex;align-items:center;gap:4px;padding:8px 14px;border:1px dashed var(--gray-300);border-radius:8px;background:#fff;font-size:.85rem;color:var(--gray-600);cursor:pointer;transition:all .15s;}
.registered-add-btn:hover{border-color:var(--green);color:var(--green);}

/* M단계: 등록 버튼 (결과 하단) */
.register-buttons{display:flex;gap:10px;margin-top:20px;padding:16px;background:var(--gray-50);border-radius:12px;}
.btn-register{flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;padding:14px 12px;border:1px solid var(--gray-200);border-radius:10px;background:#fff;cursor:pointer;transition:all .15s;}
.btn-register:hover{border-color:var(--green);background:#f0fdf4;}
.btn-register.registered{border-color:var(--green);background:#f0fdf4;}
.btn-register-icon{font-size:1.4rem;}
.btn-register-label{font-size:.85rem;font-weight:600;color:var(--gray-700);}
.btn-register-hint{font-size:.72rem;color:var(--gray-500);text-align:center;line-height:1.3;}
.btn-unregister{font-size:.75rem;color:var(--gray-400);text-decoration:underline;cursor:pointer;margin-top:4px;}
.btn-unregister:hover{color:#dc2626;}

/* O단계: 카드 삭제 버튼 */
.item-delete-btn{position:absolute;top:8px;right:8px;width:22px;height:22px;border:none;background:var(--gray-100);color:var(--gray-500);border-radius:50%;font-size:1rem;line-height:1;cursor:pointer;transition:all .15s;display:flex;align-items:center;justify-content:center;}
.item-delete-btn:hover{background:#fecaca;color:#dc2626;}
.registered-item,.recent-store-item{position:relative;}
.recent-stores-header{display:flex;align-items:center;justify-content:space-between;}
.btn-clear-all{font-size:.75rem;color:var(--gray-400);cursor:pointer;padding:4px 8px;}
.btn-clear-all:hover{color:#dc2626;}

/* K단계: 결과 상단 버튼 */
.result-top-actions{display:flex;gap:10px;margin-bottom:16px;}
.btn-action{flex:1;padding:10px 14px;border:1px solid var(--gray-200);border-radius:8px;background:#fff;font-size:.85rem;font-weight:600;color:var(--gray-700);cursor:pointer;transition:all .15s;}
.btn-action:hover{background:var(--gray-50);border-color:var(--gray-300);}
.btn-action.btn-refresh{background:var(--green);color:#fff;border-color:var(--green);}
.btn-action.btn-refresh:hover{background:#02b350;}
.btn-action.btn-reanalyze{background:var(--green);color:#fff;border-color:var(--green);}
.btn-action.btn-reanalyze:hover{background:#02b350;}

/* N단계: 맨 위로 플로팅 버튼 */
.btn-scroll-top{position:fixed;bottom:28px;right:20px;width:48px;height:48px;border-radius:50%;background:var(--primary-gradient);color:#fff;border:none;font-size:1.3rem;font-weight:700;cursor:pointer;box-shadow:var(--shadow-glow);opacity:0;visibility:hidden;transition:all .3s ease;z-index:100;}
.btn-scroll-top.visible{opacity:1;visibility:visible;}
.btn-scroll-top:hover{transform:scale(1.1) translateY(-2px);box-shadow:0 8px 24px rgba(0,184,148,.4);}

/* 4-AXIS CARDS */
.axis-grid{display:grid;grid-template-columns:1fr 1fr;gap:var(--spacing-sm);margin-top:var(--spacing-sm);}
@media(max-width:380px){.axis-grid{grid-template-columns:1fr;}}
@media(min-width:640px){.axis-grid{grid-template-columns:1fr 1fr;}}
.axis-card{background:#fff;border-radius:var(--radius);border:var(--card-border);padding:20px 18px;box-shadow:var(--shadow-sm);transition:all .25s ease;}
.axis-card:hover{box-shadow:var(--shadow);transform:translateY(-2px);}
.axis-head{display:flex;align-items:center;gap:10px;margin-bottom:var(--spacing-sm);}
.axis-icon{font-size:1.4rem;}
/* 디자인2차: 결과 화면 라인 아이콘(Lucide) 공통 스타일 */
.rpt-icon{width:18px;height:18px;stroke-width:2;vertical-align:-3px;color:#5A6B7B;}
.rpt-icon.is-good{color:var(--brand-green);}
.rpt-icon.is-warn{color:#E8833A;}
.rpt-icon.is-info{color:#5A6B7B;}
.axis-icon .rpt-icon{width:20px;height:20px;vertical-align:-4px;}
.card-title .rpt-icon{width:18px;height:18px;vertical-align:-3px;margin-right:3px;}
.tab-btn .rpt-icon{width:16px;height:16px;vertical-align:-3px;margin-right:3px;color:currentColor;}
.store-badge .rpt-icon{width:14px;height:14px;vertical-align:-2px;color:currentColor;}
.btn-action .rpt-icon,.btn-secondary .rpt-icon{width:15px;height:15px;vertical-align:-3px;margin-right:3px;color:currentColor;}
.chip .rpt-icon{width:12px;height:12px;stroke-width:2.5;vertical-align:-2px;color:currentColor;margin-right:1px;}
.sec-icon{width:16px;height:16px;vertical-align:-3px;margin-right:4px;}
.btn-reg-icon{width:22px;height:22px;stroke-width:1.8;}
.scroll-icon{width:20px;height:20px;stroke-width:2.5;color:#fff;}
.axis-name{font-size:.84rem;font-weight:700;color:var(--gray-600);letter-spacing:-.2px;}
.axis-score{font-size:1.9rem;font-weight:800;margin-bottom:8px;letter-spacing:-.5px;}
.progress-bar{height:7px;background:var(--gray-100);border-radius:6px;overflow:hidden;margin-bottom:var(--spacing-sm);}
.progress-fill{height:100%;border-radius:6px;transition:width 1s cubic-bezier(.4,0,.2,1);}
.detail-list{display:flex;flex-direction:column;gap:10px;}
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
.chip-none{background:#F1F3F5;color:#98A2B0;}

/* COMPETITOR */
.comp-rows{display:flex;flex-direction:column;gap:14px;}
.comp-label{font-size:.8rem;font-weight:600;color:var(--gray-600);margin-bottom:4px;}
.comp-bar-wrap{display:flex;align-items:center;gap:10px;}
.comp-tag{font-size:.75rem;font-weight:700;width:44px;flex-shrink:0;}
.comp-bar-bg{flex:1;height:22px;background:var(--gray-100);border-radius:6px;overflow:hidden;}
.comp-bar{height:100%;border-radius:6px;display:flex;align-items:center;padding-left:8px;transition:width 1s ease;font-size:.75rem;font-weight:700;color:#fff;white-space:nowrap;min-width:32px;}
.comp-gap{margin-top:8px;font-size:.82rem;color:var(--red);font-weight:600;}
/* P단계: 경쟁사 비교 반응형 카드 (PC 가로 최대3 / 모바일 세로) */
.comp-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:12px;}
@media(min-width:768px){.comp-grid{max-width:680px;margin-left:auto;margin-right:auto;}}
.comp-card2{border:1px solid var(--gray-200);border-radius:12px;padding:16px 14px;display:flex;flex-direction:column;gap:9px;}
.comp-grade{align-self:flex-start;font-size:.68rem;font-weight:700;color:#fff;padding:3px 9px;border-radius:6px;}
.comp-kw{font-size:.98rem;font-weight:700;color:var(--gray-900);}
.comp-vs{display:flex;align-items:flex-end;justify-content:center;gap:20px;}
.comp-vs-me,.comp-vs-rival{flex:0 0 auto;display:flex;flex-direction:column;gap:2px;min-width:0;}
.comp-vs-rival{text-align:left;}
.comp-vs-lbl{font-size:.72rem;color:var(--gray-500);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.comp-vs-rank{font-size:1.2rem;font-weight:800;line-height:1.1;}
.comp-vs-rival .comp-vs-rank{color:var(--gray-700);}
.comp-vs-sep{font-size:.72rem;color:var(--gray-400);font-weight:700;flex-shrink:0;padding-bottom:2px;}
.comp-gap2{font-size:.82rem;font-weight:700;}
.comp-ment{font-size:.78rem;color:var(--gray-600);line-height:1.5;background:var(--gray-50);border-radius:8px;padding:9px 11px;}
.comp-note{font-size:.88rem;color:var(--gray-700);line-height:1.6;}
.comp-praise{font-size:.92rem;font-weight:700;color:var(--green-d);line-height:1.6;}
.comp-fp-list{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;}
.comp-fp-kw{font-size:.78rem;font-weight:600;color:var(--green-d);background:var(--green-bg);border:1px solid #bbf7d0;border-radius:6px;padding:3px 9px;}

/* KEYWORDS */
.kw-list{display:grid;grid-template-columns:1fr;gap:12px;}
@media(min-width:768px){.kw-list{grid-template-columns:1fr 1fr;gap:14px;}}
.kw-item{background:#fff;border:1px solid var(--gray-200);border-left:3px solid var(--gray-200);border-radius:var(--radius);padding:14px 16px;box-shadow:var(--shadow-sm);transition:all .2s ease;}
.kw-item:hover{border-color:var(--green);box-shadow:var(--shadow);}
.kw-item.rank-top{border-left-color:var(--brand-green);}
.kw-item.rank-high{border-left-color:#7DD8B8;}
.kw-item.rank-mid{border-left-color:#E0E6EB;}
.kw-main{display:flex;align-items:center;gap:12px;}
.kw-rank-col{font-size:1.5rem;font-weight:800;min-width:50px;text-align:center;line-height:1.1;flex-shrink:0;letter-spacing:-.5px;}
.kw-rank-col .unit{font-size:.85rem;font-weight:600;}
@media(min-width:768px){.kw-rank-col{font-size:1.4rem;min-width:48px;}.kw-item{padding:16px 18px;}}
.kw-divider{width:1px;background:var(--gray-200);align-self:stretch;flex-shrink:0;}
.kw-info{flex:1;min-width:0;}
.kw-title-row{display:flex;align-items:center;gap:5px;flex-wrap:wrap;}
.kw-text{font-size:.95rem;font-weight:700;color:#1A2B3C;min-width:0;}
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
.comment-line{display:flex;align-items:flex-start;gap:9px;font-size:.88rem;color:var(--gray-800);line-height:1.6;margin-bottom:11px;}
.comment-line:last-child{margin-bottom:0;}
.comment-line .rpt-icon{margin-top:2px;flex-shrink:0;}

/* SUBSCRIBE */
.subscribe-form{display:flex;flex-direction:column;gap:12px;}
.subscribe-desc{font-size:.85rem;color:var(--gray-600);line-height:1.5;}
.subscribe-input{width:100%;padding:14px 16px;border:1.5px solid var(--gray-200);border-radius:10px;font-size:1rem;outline:none;transition:border .2s;}
.subscribe-input:focus{border-color:var(--green);}
.subscribe-agree{display:flex;align-items:flex-start;gap:8px;font-size:.82rem;color:var(--gray-600);cursor:pointer;line-height:1.45;}
.subscribe-agree input{margin-top:3px;accent-color:var(--green);}
.btn-subscribe{width:100%;padding:14px;background:var(--green);color:#fff;border:none;border-radius:10px;font-size:1rem;font-weight:700;cursor:pointer;transition:background .2s;}
.btn-subscribe:hover{background:var(--green-d);}
.btn-subscribe:disabled{background:var(--gray-300);cursor:not-allowed;}
.subscribe-done{text-align:center;padding:20px 0;}
.subscribe-done-icon{font-size:2.5rem;margin-bottom:10px;}
.subscribe-done-text{font-size:1rem;font-weight:700;color:var(--green-d);margin-bottom:4px;}
.subscribe-done-sub{font-size:.85rem;color:var(--gray-500);}

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
.l-card{background:#fff;border-radius:var(--radius-lg);border:var(--card-border);border-top:4px solid var(--green);padding:36px 24px;text-align:center;box-shadow:var(--shadow);}
.l-pulse{font-size:3.2rem;display:block;margin-bottom:16px;animation:lpulse 1.4s ease-in-out infinite;}
.l-pulse .rpt-icon{width:46px;height:46px;stroke-width:1.75;}
@keyframes lpulse{0%,100%{transform:scale(1);}50%{transform:scale(1.12);}}
.l-title{font-size:1.2rem;font-weight:700;margin-bottom:6px;letter-spacing:-.3px;}
.l-sub{font-size:.85rem;color:var(--gray-500);margin-bottom:24px;}
.l-bar-wrap{height:10px;background:var(--gray-100);border-radius:8px;overflow:hidden;margin-bottom:8px;}
.l-bar{height:100%;background:var(--primary-gradient);border-radius:8px;width:0%;transition:width .7s ease;}
.l-pct{font-size:.82rem;color:var(--green);text-align:right;margin-bottom:24px;font-weight:700;}
.l-steps{text-align:left;display:flex;flex-direction:column;gap:14px;margin-bottom:24px;}
.l-step{display:flex;align-items:flex-start;gap:12px;}
.l-ic{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.9rem;flex-shrink:0;transition:all .3s;}
.l-ic.done{background:var(--primary-gradient);box-shadow:0 2px 8px rgba(0,184,148,.3);}
.l-ic.active{background:var(--green-bg);border:2px solid var(--green);}
.l-ic.pending{background:var(--gray-100);filter:grayscale(1);opacity:.5;}
.l-body{padding-top:6px;}
.l-name{font-size:.9rem;font-weight:600;transition:color .3s;letter-spacing:-.2px;}
.l-name.done{color:var(--green);}
.l-name.active{color:var(--gray-900);}
.l-name.pending{color:var(--gray-400);}
.l-desc{font-size:.76rem;color:var(--gray-400);margin-top:3px;line-height:1.45;}
.dots{display:inline-flex;gap:3px;margin-left:4px;vertical-align:middle;}
.dots span{display:inline-block;width:5px;height:5px;border-radius:50%;background:var(--green);animation:db .65s ease-in-out infinite;}
.dots span:nth-child(2){animation-delay:.13s;}
.dots span:nth-child(3){animation-delay:.26s;}
@keyframes db{0%,100%{transform:translateY(0);}50%{transform:translateY(-6px);}}
/* 부팅 시퀀스 */
#boot-sequence{background:var(--gray-50);border-radius:var(--radius);padding:22px 26px;margin:var(--spacing-md) 0;font-family:'JetBrains Mono','Fira Code','Courier New',monospace;display:none;border:1px solid var(--gray-200);}
.boot-line{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--gray-700);padding:5px 0;opacity:0;animation:bootFadeIn 0.3s ease forwards;letter-spacing:-.3px;}
.boot-line .rpt-icon{width:15px;height:15px;flex-shrink:0;}
@keyframes bootFadeIn{from{opacity:0;transform:translateX(-10px);}to{opacity:1;transform:translateX(0);}}

/* 맞춤형 팁 */
#tip-section{display:none;background:linear-gradient(135deg,#f0fdf8 0%,#dcfce7 100%);border:1px solid #bbf7d0;border-radius:var(--radius);padding:20px 22px;margin:var(--spacing-md) 0;min-height:96px;flex-direction:column;gap:12px;box-shadow:var(--shadow-sm);}
.tip-header{display:flex;align-items:center;justify-content:center;gap:8px;font-size:.82rem;font-weight:700;color:var(--green);}
.tip-header .tip-icon{width:18px;height:18px;animation:tipBounce 2s ease-in-out infinite;}
.tip-item{display:flex;align-items:flex-start;justify-content:center;gap:8px;}
.tip-item .rpt-icon{margin-top:3px;flex-shrink:0;}
@keyframes tipBounce{0%,100%{transform:translateY(0);}50%{transform:translateY(-4px);}}
#tip-text{font-size:.9rem;color:#166534;line-height:1.75;transition:opacity 0.3s ease;}
.btn-stop{margin-top:var(--spacing-md);padding:12px 28px;background:#fff;border:1px solid var(--gray-300);border-radius:var(--radius-sm);font-size:.88rem;color:var(--gray-600);cursor:pointer;transition:all .2s ease;font-weight:500;}
.btn-stop:hover{background:var(--gray-50);border-color:var(--gray-400);transform:translateY(-1px);}

/* R단계: 게임형 UI */
.game-score-wrap{text-align:center;margin-bottom:var(--spacing-md);padding:20px;background:linear-gradient(135deg,#f0fdf8 0%,#dcfce7 100%);border-radius:var(--radius);box-shadow:var(--shadow-sm);border:1px solid #bbf7d0;}
.game-score-label{font-size:.78rem;color:var(--gray-500);margin-bottom:6px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;}
.game-score-num{font-size:3rem;font-weight:800;background:var(--primary-gradient);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;line-height:1;transition:transform .2s;}
.game-score-num.bump{transform:scale(1.12);}
.game-score-delta{font-size:1.05rem;font-weight:700;color:var(--green);margin-top:6px;min-height:26px;transition:opacity .3s;}
.game-score-delta.show{animation:deltaFade .8s ease-out forwards;}
@keyframes deltaFade{0%{opacity:1;transform:translateY(0);}100%{opacity:0;transform:translateY(-12px);}}

.kw-popup-area{min-height:100px;display:flex;flex-direction:column;align-items:center;justify-content:center;margin-bottom:var(--spacing-md);position:relative;}
.kw-popup{text-align:center;animation:kwPop .5s ease-out forwards;}
.kw-popup .kw-text{font-size:1.35rem;font-weight:700;color:var(--gray-800);margin-bottom:8px;letter-spacing:-.3px;}
.kw-popup .kw-rank{font-size:1.15rem;font-weight:600;color:var(--gray-600);}
.kw-popup .kw-rank.top{font-size:1.5rem;background:var(--primary-gradient);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;font-weight:800;}
.kw-popup .kw-reaction{font-size:1.3rem;margin-top:6px;animation:reactionBounce .4s ease-out;}
@keyframes kwPop{0%{opacity:0;transform:scale(1.4);}60%{transform:scale(0.95);}100%{opacity:1;transform:scale(1);}}
@keyframes reactionBounce{0%{transform:scale(0.5);}50%{transform:scale(1.2);}100%{transform:scale(1);}}

/* S단계: 실제 진행률 표시 */
.l-progress-text{font-size:.9rem;color:var(--gray-500);margin-bottom:4px;}
.l-progress-count{font-size:1.8rem;font-weight:800;color:var(--green);margin-bottom:16px;}
.l-progress-count span{transition:transform .15s;}

/* S단계: 상위 키워드 칩 누적 */
.top-kw-chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-bottom:16px;min-height:32px;}
.top-kw-chip{display:inline-flex;align-items:center;gap:4px;padding:6px 12px;border-radius:20px;font-size:.82rem;font-weight:600;animation:chipIn .4s ease-out;}
.top-kw-chip .chip-rank{font-weight:700;margin-right:2px;}
.top-kw-chip.rank-1{background:linear-gradient(135deg,#fef3c7,#fde68a);color:#92400e;border:1px solid #fcd34d;}
.top-kw-chip.rank-2-3{background:linear-gradient(135deg,#f3f4f6,#e5e7eb);color:#374151;border:1px solid #d1d5db;}
.top-kw-chip.rank-4-5{background:linear-gradient(135deg,#fed7aa,#fdba74);color:#9a3412;border:1px solid #fb923c;}
.top-kw-chip.rank-6-10{background:#f9fafb;color:#6b7280;border:1px solid #e5e7eb;}
@keyframes chipIn{0%{opacity:0;transform:scale(0.5) translateY(10px);}100%{opacity:1;transform:scale(1) translateY(0);}}
/* 블로그 키워드 행 (키워드명 + 순위칩들) */
.blog-kw-row{display:inline-flex;align-items:center;gap:4px;animation:chipIn .4s ease-out;}
.blog-kw-name{font-size:.85rem;font-weight:600;color:var(--gray-700);padding:4px 10px;background:var(--gray-50);border-radius:6px;}
.blog-rank-chip{font-size:.75rem;font-weight:700;padding:3px 8px;border-radius:12px;animation:chipSnap .25s ease-out backwards;}
.blog-rank-chip.rank-1{background:#fef3c7;color:#92400e;}
.blog-rank-chip.rank-2-3{background:#e5e7eb;color:#374151;}
.blog-rank-chip.rank-4-5{background:#fed7aa;color:#9a3412;}
.blog-rank-chip.rank-6-10{background:#f3f4f6;color:#6b7280;}
.blog-rank-chip.rank-11-plus{background:#f9fafb;color:#9ca3af;}
@keyframes rowSlide{0%{opacity:0;transform:translateY(-10px);}100%{opacity:1;transform:translateY(0);}}
@keyframes chipSnap{0%{opacity:0;transform:scale(0) translateX(-10px);}60%{transform:scale(1.1) translateX(0);}100%{opacity:1;transform:scale(1) translateX(0);}}

/* S단계: 분석 중 펄스 (키워드 사이 생동감) */
.kw-analyzing{text-align:center;padding:20px;}
.kw-analyzing-icon{font-size:2rem;animation:analyzePulse 1.2s ease-in-out infinite;}
.kw-analyzing-icon .rpt-icon{width:30px;height:30px;color:var(--green);}
.kw-analyzing-text{font-size:.9rem;color:var(--gray-500);margin-top:8px;}
.kw-analyzing-dots{display:inline-flex;gap:3px;margin-left:4px;}
.kw-analyzing-dots span{width:5px;height:5px;background:var(--green);border-radius:50%;animation:dotBounce .6s ease-in-out infinite;}
.kw-analyzing-dots span:nth-child(2){animation-delay:.1s;}
.kw-analyzing-dots span:nth-child(3){animation-delay:.2s;}
@keyframes analyzePulse{0%,100%{transform:scale(1);opacity:1;}50%{transform:scale(1.1);opacity:.7;}}
@keyframes dotBounce{0%,100%{transform:translateY(0);}50%{transform:translateY(-4px);}}

/* TABS */
.tabs{display:flex;gap:0;margin-top:14px;background:#fff;border-radius:var(--radius);border:var(--card-border);overflow:hidden;}
.tab-btn{flex:1;padding:14px 10px;background:#fff;border:none;font-size:.9rem;font-weight:600;color:var(--gray-600);cursor:pointer;transition:all .2s;border-bottom:3px solid transparent;}
.tab-btn.active{color:var(--green);border-bottom-color:var(--green);background:var(--green-bg);}
.tab-btn:hover:not(.active){background:var(--gray-50);}
.tab-content{display:none;}
.tab-content.active{display:block;}

/* BLOG TAB */
.blog-start-card{background:#fff;border-radius:var(--radius);border:var(--card-border);margin-top:14px;padding:32px 20px;text-align:center;}
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
.analysis-type-grid{display:grid;grid-template-columns:1fr 1fr;gap:var(--spacing-sm);}
.analysis-type-btn{display:flex;flex-direction:column;align-items:center;gap:6px;padding:16px 12px;background:#fff;border:2px solid var(--gray-200);border-radius:var(--radius);cursor:pointer;transition:all .25s ease;box-shadow:var(--shadow-sm);}
.analysis-type-btn:hover{border-color:var(--green);background:var(--green-bg);transform:translateY(-2px);box-shadow:var(--shadow);}
.analysis-type-btn.selected{border-color:var(--green);background:var(--green-bg);box-shadow:0 0 0 3px rgba(0,184,148,.15);}
.analysis-type-btn input{display:none;}
.type-icon{font-size:1.6rem;}
.type-icon-luc{width:24px;height:24px;stroke-width:1.8;}
.type-label{font-size:.92rem;font-weight:700;color:var(--gray-800);letter-spacing:-.2px;}
.type-desc{font-size:.75rem;color:var(--gray-500);}

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
  <div class="logo" onclick="goHome()" style="cursor:pointer;">
    <svg class="logo-full" viewBox="0 0 180 36" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="logoGrad" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#00C896"/><stop offset="100%" stop-color="#00d4a4"/></linearGradient></defs>
      <circle cx="18" cy="14" r="10" fill="url(#logoGrad)"/>
      <circle cx="18" cy="14" r="5" fill="white"/>
      <circle cx="18" cy="14" r="2.5" fill="#00C896"/>
      <path d="M18 24 L13 32 L18 29 L23 32 Z" fill="url(#logoGrad)"/>
      <text x="38" y="24" font-family="'Pretendard Variable',Pretendard,sans-serif" font-size="17" font-weight="800" fill="#1e293b" letter-spacing="-0.5">플레이스랭킹</text>
    </svg>
  </div>
  <span class="header-badge">무료</span>
</div>
<div class="main">

  <!-- INPUT (L단계: 랜딩 + 검색폼) -->
  <div id="input-section">

    <!-- 랜딩 -->
    <div class="landing" id="landing">
      <section class="hero">
        <span class="hero-icon">🏆</span>
        <h1>내 매장, 네이버에서<br><span class="accent">몇 위</span>인지 아세요?</h1>
        <p class="hero-sub">플레이스 순위 · 블로그 노출 · 경쟁사까지<br>1분 만에 무료로 확인하세요.</p>
        <button class="hero-cta" onclick="scrollToSearch()">내 순위 확인하기</button>
        <div class="hero-note">가입 없이 바로 · 네이버 URL만 있으면 OK</div>
      </section>

      <section class="lp-section">
        <div class="lp-section-title">무엇을 알 수 있나요</div>
        <div class="value-grid">
          <div class="value-card"><i data-lucide="map-pin" class="rpt-icon is-info v-icon-luc"></i><div class="v-title">플레이스 순위</div><div class="v-desc">키워드별 내 매장 순위</div></div>
          <div class="value-card"><i data-lucide="file-text" class="rpt-icon is-info v-icon-luc"></i><div class="v-title">블로그 노출</div><div class="v-desc">블로그 검색 노출 현황</div></div>
          <div class="value-card"><i data-lucide="trending-up" class="rpt-icon is-info v-icon-luc"></i><div class="v-title">변화 추적</div><div class="v-desc">지난 분석 대비 순위 변화</div></div>
          <div class="value-card"><i data-lucide="swords" class="rpt-icon is-info v-icon-luc"></i><div class="v-title">경쟁사 비교</div><div class="v-desc">1위 매장과의 격차</div></div>
        </div>
      </section>

      <section class="lp-section">
        <div class="lp-section-title">어떻게 작동하나요</div>
        <div class="steps">
          <div class="step"><div class="step-num">1</div><div class="step-body"><div class="s-title">URL 입력</div><div class="s-desc">매장명과 네이버 플레이스 URL만 넣으면 끝</div></div></div>
          <div class="step"><div class="step-num">2</div><div class="step-body"><div class="s-title">1분 분석</div><div class="s-desc">순위·리뷰·경쟁사를 자동으로 분석해요</div></div></div>
          <div class="step"><div class="step-num">3</div><div class="step-body"><div class="s-title">결과 확인 + 추적</div><div class="s-desc">점수와 순위를 받고, 다음 분석과 비교까지</div></div></div>
        </div>
      </section>

      <section class="lp-section">
        <div class="lp-section-title">이런 결과를 받아요</div>
        <div class="preview-card">
          <div class="preview-gauge">
            <svg width="140" height="140" viewBox="0 0 140 140" style="transform:rotate(-90deg);">
              <circle cx="70" cy="70" r="60" fill="none" stroke="#f3f4f6" stroke-width="12"/>
              <circle cx="70" cy="70" r="60" fill="none" stroke="#22c55e" stroke-width="12" stroke-linecap="round" stroke-dasharray="309 377"/>
            </svg>
            <div class="preview-score">82<small>종합점수</small></div>
          </div>
          <div class="preview-trend">▲ 지난번 78점 → 이번 82점 (+4)</div>
          <div class="preview-kw">
            <div class="preview-kw-row"><span class="pk-name">오산 피부관리</span><span class="pk-rank">13위 → 9위 → 2위</span></div>
            <div class="preview-kw-row"><span class="pk-name">오산 에스테틱</span><span class="pk-rank">6위 → 3위</span></div>
          </div>
          <div class="preview-caption">* 실제 분석 결과 예시 화면입니다</div>
        </div>
      </section>

      <!-- SEO 콘텐츠: Why 섹션 -->
      <section class="seo-why-section">
        <h2>네이버 플레이스 순위, 왜 확인해야 하나요?</h2>
        <p>
          네이버 플레이스, 상위 3개 매장이 손님의 80%를 가져갑니다.
        </p>
        <p>
          내 매장이 어떤 키워드에서 몇 위인지,<br>
          경쟁 매장과 얼마나 차이 나는지 — 지금 무료로 확인하세요.
        </p>
        <p>
          플레이스 광고 · 블로그 체험단 · 상위노출 작업 집행 중이라면<br>
          실제 키워드 순위 변화를 직접 확인해보세요.
        </p>
      </section>

      <!-- SEO 콘텐츠: FAQ 아코디언 -->
      <section class="seo-faq-section">
        <h2>플레이스 순위, 이런 게 궁금하셨죠?</h2>
        <div class="faq-item">
          <button class="faq-q">네이버 플레이스 상위노출은 어떻게 결정되나요?</button>
          <div class="faq-a">
            키워드 일치도, 리뷰 수·점수, 저장수, 최근 활동, 블로그 포스팅 노출 등
            복합 알고리즘으로 결정됩니다. 플레이스랭킹에서 내 매장의
            현재 점수와 부족한 항목을 무료로 확인할 수 있습니다.
          </div>
        </div>
        <div class="faq-item">
          <button class="faq-q">플레이스 광고를 집행 중인데 순위 확인이 필요한가요?</button>
          <div class="faq-a">
            네이버 플레이스 광고는 유료 노출이고, 자연 순위는 별개입니다.
            광고 없이도 어떤 키워드에서 자연 노출되는지 확인하는 것이
            진짜 마케팅 실력 파악의 시작입니다.
          </div>
        </div>
        <div class="faq-item">
          <button class="faq-q">블로그 체험단 효과, 실제로 순위에 반영되고 있나요?</button>
          <div class="faq-a">
            블로그 포스팅이 어떤 키워드로 몇 위에 노출되는지
            플레이스랭킹 블로그 분석 탭에서 직접 확인할 수 있습니다.
            체험단 효과를 데이터로 검증해보세요.
          </div>
        </div>
        <div class="faq-item">
          <button class="faq-q">경쟁 매장이 왜 나보다 순위가 높은지 알 수 있나요?</button>
          <div class="faq-a">
            경쟁사 비교 기능으로 1위 매장과의 키워드 격차를 확인할 수 있습니다.
            어떤 키워드에서 밀리는지 파악하면 개선 방향이 보입니다.
          </div>
        </div>
        <div class="faq-item">
          <button class="faq-q">가게 오픈 전에도 경쟁 매장 분석이 가능한가요?</button>
          <div class="faq-a">
            오픈 예정이라면 경쟁 매장 분석으로 상권 내 키워드 경쟁 강도를
            미리 파악할 수 있습니다. 플레이스 광고나 소상공인 마케팅 전략 수립에 활용하세요.
          </div>
        </div>
        <div class="faq-item">
          <button class="faq-q">지역소상공인광고와 일반 플레이스 광고 차이는 무엇인가요?</button>
          <div class="faq-a">
            지역소상공인광고는 네이버가 지원하는 소상공인 전용 광고 상품으로
            노출 영역과 비용 구조가 다릅니다.
            어떤 광고가 적합한지는 현재 키워드 순위를 먼저 파악한 후 결정하는 것이 효율적입니다.
          </div>
        </div>
      </section>

      <div class="search-divider" id="searchStart">
        <div class="sd-title">내 매장 순위, 지금 확인</div>
        <div class="sd-sub">아래에 매장 정보를 입력하세요</div>
      </div>
    </div>

    <div class="input-card" id="searchFormCard">
      <div class="field"><label>매장명</label><input type="text" id="storeName" placeholder=""></div>
      <div class="field"><label>네이버 플레이스 URL</label><input type="text" id="placeUrl" placeholder=""></div>
      <div class="field">
        <label>분석 유형</label>
        <div class="analysis-type-grid">
          <label class="analysis-type-btn selected" data-type="place">
            <input type="radio" name="analysisType" value="place" checked>
            <i data-lucide="map-pin" class="rpt-icon is-info type-icon-luc"></i>
            <span class="type-label">플레이스</span>
            <span class="type-desc">순위·리뷰·경쟁사</span>
          </label>
          <label class="analysis-type-btn" data-type="blog">
            <input type="radio" name="analysisType" value="blog">
            <i data-lucide="file-text" class="rpt-icon is-info type-icon-luc"></i>
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
      <button class="btn-diagnose" id="diagBtn" onclick="startAnalysis()">내 순위 확인하기</button>
      <div class="status-msg" id="statusMsg"></div>
    </div>
    <div id="errBox"></div>

    <!-- M단계: 내 매장 -->
    <div class="registered-section" id="myStoresSection" style="display:none;">
      <div class="registered-header">
        <span class="registered-title"><i data-lucide="star" class="rpt-icon is-info sec-icon"></i> 내 매장</span>
        <span class="registered-count" id="myStoresCount"></span>
      </div>
      <div class="registered-list" id="myStoresList"></div>
    </div>

    <!-- M단계: 경쟁 매장 -->
    <div class="registered-section" id="rivalStoresSection" style="display:none;">
      <div class="registered-header">
        <span class="registered-title"><i data-lucide="eye" class="rpt-icon is-info sec-icon"></i> 옆 매장 몰래보기</span>
        <span class="registered-count" id="rivalStoresCount"></span>
      </div>
      <div class="registered-desc">경쟁 매장 순위를 슬쩍 지켜보세요</div>
      <div class="registered-list" id="rivalStoresList"></div>
    </div>

    <!-- K단계: 최근 본 매장 -->
    <div class="recent-stores-section" id="recentStoresSection" style="display:none;">
      <div class="recent-stores-header">
        <span><i data-lucide="clock" class="rpt-icon is-info sec-icon"></i> 최근 본 매장</span>
        <span class="btn-clear-all" onclick="clearAllRecentStores()">전체 지우기</span>
      </div>
      <div class="recent-stores-list" id="recentStoresList"></div>
    </div>

    <!-- SEO 콘텐츠: CTA -->
    <section class="seo-cta-section">
      <p class="cta-label">순위 개선이 필요하다면?</p>
      <h2>진단 결과를 바탕으로<br>무엇을 개선해야 할지 물어보세요</h2>
      <p class="cta-desc">
        플레이스 광고, 블로그 체험단, 상위노출 작업 등<br>
        매장 상황에 맞는 방법을 안내해드립니다.
      </p>
      <a id="kakaoCtaBtn" href="https://pf.kakao.com/_qsxlXX/chat" target="_blank" class="cta-btn">
        💬 카카오톡으로 무료 문의하기
      </a>
      <script>
      (function(){
        var isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
        var btn = document.getElementById('kakaoCtaBtn');
        if(!isMobile) btn.href = 'https://pf.kakao.com/_qsxlXX';
      })();
      </script>
    </section>
  </div>

  <!-- LOADING (R단계: 게임형 UI) -->
  <div id="loading-section">
    <div class="l-card">
      <span class="l-pulse" id="lIcon"><i data-lucide="bar-chart-3" class="rpt-icon is-good"></i></span>
      <div class="l-title" id="lTitle">플레이스 진단 중이에요</div>
      <div class="l-progress-text" id="lProgressText">키워드 분석 중</div>

      <!-- 부팅 시퀀스 (초반 20초) -->
      <div id="boot-sequence"></div>

      <!-- 맞춤형 팁 (부팅 후 표시) -->
      <div id="tip-section">
        <div class="tip-header"><i data-lucide="lightbulb" class="rpt-icon is-warn tip-icon"></i><span id="tipHeaderText">플레이스 순위 높이는 꿀팁</span></div>
        <div class="tip-item"><i data-lucide="lightbulb" class="rpt-icon is-warn"></i><span id="tip-text"></span></div>
      </div>

      <!-- S단계: 상위 키워드 칩 누적 영역 -->
      <div class="top-kw-chips" id="topKwChips"></div>

      <!-- R단계: 키워드 등장 영역 -->
      <div class="kw-popup-area" id="kwPopupArea"></div>

      <div class="l-bar-wrap"><div class="l-bar" id="lBar"></div></div>
      <div class="l-pct" id="lPct">0%</div>
      <button class="btn-stop" onclick="goHome()">중지하기</button>
    </div>
  </div>

  <!-- RESULT -->
  <div id="result">
    <!-- K단계: 결과 화면 상단 재검색 버튼 -->
    <div class="result-top-actions">
      <button class="btn-action" onclick="goBackToSearch()">← 홈으로</button>
      <button class="btn-action btn-reanalyze" onclick="reAnalyze()"><i data-lucide="rotate-cw" class="rpt-icon"></i>다시 분석</button>
    </div>

    <!-- 공통 헤더: 매장명 + 종합점수 (탭 위에 항상 표시) -->
    <div class="result-header">
      <div class="store-badge"><i data-lucide="map-pin" class="rpt-icon"></i> <span id="rCategory"></span></div>
      <div class="store-name" id="rStoreName"></div>
      <div class="store-meta" id="rMeta"></div>
      <!-- J단계: 분석 횟수 표시 -->
      <div class="analysis-history-info" id="analysisHistoryInfo" style="display:none;"></div>
    </div>

    <!-- GAUGE (공통) -->
    <div class="card">
      <div class="card-title" id="gaugeCardTitle">종합 플레이스 점수</div>
      <div class="gauge-wrap">
        <span class="grade-badge" id="gradeBadge">-</span>
        <svg class="gauge-svg" id="gaugeSvg" width="160" height="160" viewBox="0 0 160 160">
          <circle class="gauge-track" cx="80" cy="80" r="66"/>
          <circle class="gauge-fill" id="gaugeFill" cx="80" cy="80" r="66" stroke-dasharray="0 415" stroke="#22c55e"/>
          <text class="gauge-text" id="gaugeNum" x="80" y="76" fill="#111827">0</text>
          <text class="gauge-sub" x="80" y="98">/100점</text>
        </svg>
        <!-- 블로그 노출 요약 (블로그 분석 시 게이지 대신 표시) -->
        <div class="blog-headline" id="blogHeadline" style="display:none;"></div>
        <p class="gauge-summary" id="gaugeSummary"></p>
        <!-- J단계: 종합점수 직전 비교 -->
        <div class="score-trend" id="scoreTrend" style="display:none;"></div>
      </div>
    </div>

    <!-- TABS -->
    <div class="tabs">
      <button class="tab-btn active" data-tab="place" onclick="switchTab('place')"><i data-lucide="map-pin" class="rpt-icon"></i>플레이스 분석</button>
      <button class="tab-btn" data-tab="blog" onclick="switchTab('blog')"><i data-lucide="file-text" class="rpt-icon"></i>블로그 분석</button>
    </div>

    <!-- TAB: 플레이스 분석 -->
    <div id="tab-place" class="tab-content active">
      <!-- 4-AXIS -->
      <div style="font-size:.82rem;font-weight:700;color:var(--gray-600);padding:12px 0 0;">진단 상세</div>
      <div class="axis-grid" id="axisGrid"></div>

      <!-- COMPETITOR -->
      <div class="card" id="compCard" style="display:none;">
        <div class="card-title"><i data-lucide="trophy" class="rpt-icon is-info"></i>경쟁사 비교</div>
        <div class="comp-rows" id="compRows"></div>
      </div>

      <!-- KEYWORDS -->
      <div class="card">
        <div class="card-title"><i data-lucide="key-round" class="rpt-icon is-info"></i>키워드 순위</div>
        <div class="kw-list" id="kwList"></div>
        <div class="kw-more" id="kwMore" onclick="toggleKw()"></div>
      </div>

      <!-- ANALYSIS COMMENT -->
      <div class="card">
        <div class="card-title"><i data-lucide="message-square-text" class="rpt-icon is-info"></i>분석 코멘트</div>
        <div class="comment-box" id="commentBox"></div>
      </div>

      <!-- 알림 신청 -->
      <div class="card" id="subscribeCard">
        <div class="card-title"><i data-lucide="bell" class="rpt-icon is-info"></i>매주 순위 알림 받기</div>
        <div class="subscribe-form" id="subscribeForm">
          <p class="subscribe-desc">매주 키워드 순위 변화를 카카오 알림톡으로 보내드려요.</p>
          <input type="tel" id="subscribePhone" class="subscribe-input" placeholder="휴대폰 번호 (예: 01012345678)" maxlength="13">
          <label class="subscribe-agree">
            <input type="checkbox" id="subscribeAgree">
            <span>플레이스랭킹 순위 리포트 알림톡 수신에 동의합니다 (정보성)</span>
          </label>
          <button class="btn-subscribe" id="btnSubscribe" onclick="submitSubscribe()">무료 알림 신청하기</button>
        </div>
        <div class="subscribe-done" id="subscribeDone" style="display:none;">
          <div class="subscribe-done-icon">✅</div>
          <div class="subscribe-done-text">알림 신청이 완료되었습니다!</div>
          <div class="subscribe-done-sub">매주 순위 변화를 알림톡으로 보내드릴게요.</div>
        </div>
      </div>

      <!-- BUTTONS -->
      <div class="card">
        <div class="btn-area">
          <div class="btn-row">
            <button class="btn-secondary" onclick="handlePwa()"><i data-lucide="smartphone" class="rpt-icon"></i>홈 화면 추가</button>
            <button class="btn-secondary" onclick="handleShare()">💬 카톡 공유</button>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB: 블로그 분석 -->
    <div id="tab-blog" class="tab-content">
      <!-- 분석 전: 시작 버튼 -->
      <div id="blogStartCard" class="blog-start-card">
        <div class="blog-start-icon">📝</div>
        <div class="blog-start-title">블로그 노출 분석</div>
        <div class="blog-start-desc">
          우리 매장를 태그한 블로그가 검색 몇 위에 노출되는지 분석해요.<br>
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

    <!-- M단계: 등록 버튼 -->
    <div class="register-buttons" id="registerButtons">
      <div class="btn-register" id="btnRegisterMy" onclick="registerStore('my')">
        <i data-lucide="star" class="rpt-icon is-info btn-reg-icon"></i>
        <span class="btn-register-label">내 매장으로 등록</span>
        <span class="btn-register-hint">매주 순위 변화를 알려드려요<br>(곧 출시)</span>
      </div>
      <div class="btn-register" id="btnRegisterRival" onclick="registerStore('rival')">
        <i data-lucide="eye" class="rpt-icon is-info btn-reg-icon"></i>
        <span class="btn-register-label">경쟁 매장으로 등록</span>
        <span class="btn-register-hint">옆 매장 순위를 슬쩍 지켜보세요</span>
      </div>
    </div>
  </div>

</div>

<!-- N단계: 맨 위로 플로팅 버튼 -->
<button class="btn-scroll-top" id="btnScrollTop" onclick="window.scrollTo({top:0,behavior:'smooth'})"><i data-lucide="arrow-up" class="rpt-icon scroll-icon"></i></button>

<script>
// ── 상태 ──────────────────────────────────────────────────────────────────────
const CIRC = 2 * Math.PI * 66; // ≈ 414.7
let _allKw = [], _kwExpanded = false;
let _blogAnalyzed = false;
let _analysisType = 'place';  // 'place' | 'blog'
let _prevAnalysis = null;     // 직전 분석 결과 (비교용)

// K단계: 익명 ID + 마지막 분석 정보
let _anonId = null;
let _lastStoreName = '';
let _lastPlaceUrl = '';
let _forceRefresh = false;
let _lastResultData = null;  // L단계: 강제 재크롤 체크박스 제거

// N단계: 분석 중단용 AbortController
let _analysisAbortController = null;

// 팁 슬라이더 상태
let _tipIdx = 0;
let _tipList = [];
let _tipInterval = null;

// 부팅 시퀀스 표시
function showBootSequence(storeName, category, address, mode) {
  mode = mode || 'place';
  // 마지막 2단계만 분석 유형별로 다름 (앞 4단계 = 연결·매장·카테고리·지역 공통)
  const tail = mode === 'blog'
    ? [{i:'file-text',  c:'is-info', t:'블로그 키워드 추출 중...'},
       {i:'play-circle',c:'is-good', t:'블로그 노출 분석 시작!'}]
    : [{i:'search',     c:'is-info', t:'키워드 목록 생성 중...'},
       {i:'play-circle',c:'is-good', t:'순위 분석 시작!'}];
  const steps = [
    {i:'link',          c:'is-info', t:'네이버 ' + (mode === 'blog' ? '블로그' : '플레이스') + ' 연결 중...'},
    {i:'check-circle-2',c:'is-good', t:(storeName || '매장') + ' 확인됨'},
    {i:'check-circle-2',c:'is-good', t:'카테고리: ' + (category || '매장 정보 확인됨')},
    {i:'check-circle-2',c:'is-good', t:'지역: ' + (address ? address.split(' ').slice(0,2).join(' ') : '위치 확인됨')},
    ...tail,
  ];

  const container = document.getElementById('boot-sequence');
  container.innerHTML = '';
  container.style.display = 'block';
  document.getElementById('tip-section').style.display = 'none';

  steps.forEach((step, i) => {
    setTimeout(() => {
      const line = document.createElement('div');
      line.className = 'boot-line';
      line.innerHTML = `<i data-lucide="${step.i}" class="rpt-icon ${step.c}"></i><span>${step.t}</span>`;
      container.appendChild(line);
      if(window.lucide) lucide.createIcons();
      if (i === steps.length - 1) {
        setTimeout(() => {
          container.style.display = 'none';
          document.getElementById('tip-section').style.display = 'flex';
          startTips(storeName, category, address, mode);
        }, 1000);
      }
    }, i * 600);
  });
}

// 업종 분류
function getMainCategory(category) {
  if (!category) return 'default';
  const cat = category;
  if (/육류|고기|갈비|삼겹|곱창|양고기|스테이크|한식|중식|일식|양식|분식|국밥|찌개|해산물|회|초밥|라멘|피자|버거|치킨|카레|태국|베트남|인도|음식|식당|맛집|백반|냉면|막국수|돼지|소고기|닭/.test(cat))
    return '음식점';
  if (/카페|커피|디저트|베이커리|케이크|브런치|빵|음료/.test(cat))
    return '카페';
  if (/헬스|피트니스|PT|필라테스|요가|크로스핏|수영|골프|테니스|운동|체육관|짐/.test(cat))
    return '헬스';
  if (/병원|의원|클리닉|치과|한의|성형|피부과|정형|내과|소아과|약국|의료/.test(cat))
    return '병원';
  if (/뷰티|미용|네일|에스테틱|왁싱|속눈썹|헤어|피부관리|미용실|샵/.test(cat))
    return '뷰티';
  if (/학원|교육|과외|어학|코딩|미술|음악|체육|입시|영어|수학/.test(cat))
    return '교육';
  if (/숙박|호텔|모텔|펜션|게스트하우스|캠핑|리조트/.test(cat))
    return '숙박';
  return 'default';
}

// 맞춤형 팁 생성
function getTips(storeName, category, address) {
  const region = address ? address.split(' ').slice(0,2).join(' ') : '이 지역';
  const mainCat = getMainCategory(category);
  const categoryTips = [];

  if (mainCat === '음식점') {
    categoryTips.push(
      storeName + " 음식점은 점심/저녁 키워드를 따로 공략하면 노출이 2배!",
      "음식 사진 클릭률이 테이블 사진보다 3배 높아요",
      "블로그 체험단 포스팅 후 2~4주 안에 순위 변화가 나타나요",
      region + " 음식점은 리뷰 50개 이상부터 상위 경쟁 가능해요"
    );
  }
  if (mainCat === '헬스') {
    categoryTips.push(
      region + " 헬스 업종은 지역명+서비스명 조합이 핵심 키워드예요",
      "시설 내부 사진이 많을수록 문의 전환율이 높아요",
      "체험가/무료체험 키워드는 경쟁이 낮아 공략하기 좋아요"
    );
  }
  if (mainCat === '카페') {
    categoryTips.push(
      region + " 카페는 '작업하기 좋은' 같은 목적 키워드가 효과적이에요",
      "시그니처 메뉴 이름이 키워드로 잡히는 경우도 있어요",
      "음료/디저트 비주얼 사진이 저장수를 높이는 핵심이에요"
    );
  }
  if (mainCat === '병원') {
    categoryTips.push(
      storeName + "은 전문의 이름이 키워드로 잡힐 수 있어요",
      "비급여 시술명 키워드는 경쟁이 낮아 공략 가치가 높아요",
      "병원/의원은 리뷰 신뢰도가 순위에 큰 영향을 줘요"
    );
  }
  if (mainCat === '뷰티') {
    categoryTips.push(
      storeName + "은 시술 전후 사진이 클릭률을 크게 높여요",
      region + " 뷰티 업종은 시술명 키워드가 이벤트보다 효과적이에요"
    );
  }
  if (mainCat === '교육') {
    categoryTips.push(
      region + " 학원은 지역+과목 조합 키워드가 전환율이 가장 높아요",
      "입학 시즌에 설명회/체험수업 키워드 효과가 커요",
      storeName + "은 학부모 리뷰가 순위에 큰 영향을 줘요"
    );
  }

  const commonTips = [
    storeName + ", 플레이스 사진 10장 이상이면 노출 점수 UP!",
    "리뷰 답글을 꾸준히 달면 사장님 활동 점수가 높아져요",
    "저장수가 많을수록 네이버가 인기 매장으로 인식해요",
    region + " 지역 키워드 + 업종 조합이 가장 효과적이에요",
    "플레이스 광고 없이도 자연 순위 1위가 가능해요",
    "경쟁 매장 분석으로 " + storeName + "이 놓친 키워드를 찾을 수 있어요",
    "영업시간/메뉴/가격 정보가 상세할수록 클릭 전환율이 높아요",
    "스마트플레이스 정보 최신화만으로도 순위가 오르는 경우가 있어요",
    "플레이스 지수는 검색노출/리뷰/활동/광고 4가지로 계산돼요",
    "분석 결과를 주 1회 확인하면 순위 변화 트렌드를 파악할 수 있어요",
    region + "에서 " + storeName + "의 경쟁 매장 현황도 곧 보여드려요"
  ];

  return [...categoryTips, ...commonTips].sort(() => Math.random() - 0.5);
}

// 블로그 분석용 맞춤 팁 (블로그 노출/마케팅 내용)
function getBlogTips(storeName, address) {
  const region = address ? address.split(' ').slice(0,2).join(' ') : '이 지역';
  const name = storeName || '우리 매장';
  return [
    "블로그 체험단 포스팅은 발행 후 2~4주에 순위에 반영돼요",
    name + "은 제목 앞쪽에 핵심 키워드를 넣은 블로그가 상위 노출에 유리해요",
    "사진 10장 이상 + 1,500자 이상 포스팅이 검색 노출에 강해요",
    "같은 키워드로 여러 블로거가 써주면 상위 노출 확률이 올라가요",
    "방문 후기형(영수증 인증) 블로그가 신뢰도·노출에 더 유리해요",
    "블로그 제목에 '" + region + " + 업종'을 함께 넣으면 검색에 잘 잡혀요",
    "협찬·체험단 표기는 정확히 — 네이버 저품질 회피에 중요해요",
    "발행 직후보다 2주 뒤에 순위가 더 안정적으로 잡혀요",
    "매달 꾸준히 몇 건씩 발행되는 매장이 상위에 오래 남아요",
    "본문에 매장 정보·지도 링크를 넣으면 키워드 연관도가 올라가요",
    name + "을 태그한 블로그가 많을수록 플레이스 노출에도 도움이 돼요",
  ].sort(() => Math.random() - 0.5);
}

// 팁 시작
function startTips(storeName, category, address, mode) {
  _tipList = (mode === 'blog') ? getBlogTips(storeName, address) : getTips(storeName, category, address);
  const _hdr = document.getElementById('tipHeaderText');
  if(_hdr) _hdr.textContent = (mode === 'blog') ? '블로그 노출 높이는 꿀팁' : '플레이스 순위 높이는 꿀팁';
  _tipIdx = 0;
  showTip(_tipIdx);
  _tipInterval = setInterval(() => {
    _tipIdx = (_tipIdx + 1) % _tipList.length;
    showTip(_tipIdx);
  }, 5000);
}

// 팁 표시
function showTip(idx) {
  const el = document.getElementById('tip-text');
  if (!el || !_tipList[idx]) return;
  el.style.opacity = 0;
  setTimeout(() => {
    el.innerHTML = _tipList[idx].replace(/\\\\n/g, '<br>');
    el.style.opacity = 1;
  }, 300);
}

// 팁 중지
function stopTips() {
  if (_tipInterval) {
    clearInterval(_tipInterval);
    _tipInterval = null;
  }
}

// K단계: 익명 ID 발급/조회
function getOrCreateAnonId(){
  let id = localStorage.getItem('placedoctor_anon_id');
  if(!id){
    id = crypto.randomUUID ? crypto.randomUUID() : 'anon-' + Date.now() + '-' + Math.random().toString(36).slice(2);
    localStorage.setItem('placedoctor_anon_id', id);
  }
  return id;
}

// K단계: 최근 본 매장 로드
async function loadRecentStores(){
  if(!_anonId) return;
  try{
    const res = await fetch('/recent-stores/' + _anonId);
    if(!res.ok) return;
    const data = await res.json();
    renderRecentStores(data.stores || []);
  }catch(e){
    console.log('최근 매장 로드 실패:', e);
  }
}

function renderRecentStores(stores){
  const section = document.getElementById('recentStoresSection');
  const list = document.getElementById('recentStoresList');

  // O단계: 숨긴 매장 필터링 + 최근 10개만
  const hidden = getHiddenRecentStores();
  const filtered = stores.filter(s => !hidden.includes(s.place_id)).slice(0, 10);

  if(!filtered || filtered.length === 0){
    section.style.display = 'none';
    return;
  }

  section.style.display = 'block';
  list.innerHTML = filtered.map(s => {
    const score = s.total_score != null ? `<span class="recent-store-score">${Math.round(s.total_score)}점</span>` : '';
    const time = formatRelativeTime(s.analyzed_at);
    const addr = s.address ? s.address.split(' ').slice(0,3).join(' ') : '';
    return `<div class="recent-store-item">
      <button class="item-delete-btn" onclick="event.stopPropagation(); hideRecentStore('${esc(s.place_id)}')" title="삭제">×</button>
      <div onclick="loadHistoryResult('${esc(s.place_id)}', '${esc(s.store_name)}')">
        <div class="recent-store-name">${esc(s.store_name)}</div>
        <div class="recent-store-meta">
          <span>${esc(addr)}</span>
          ${score}
          <span class="recent-store-time">${time}</span>
        </div>
      </div>
    </div>`;
  }).join('');
}

// O단계: 최근 본 매장 숨기기 (localStorage)
function getHiddenRecentStores(){
  try{
    return JSON.parse(localStorage.getItem('hidden_recent_stores') || '[]');
  }catch(e){
    return [];
  }
}

function hideRecentStore(placeId){
  const hidden = getHiddenRecentStores();
  if(!hidden.includes(placeId)){
    hidden.push(placeId);
    // 최대 50개까지만 저장 (오래된 것 자동 정리)
    if(hidden.length > 50) hidden.shift();
    localStorage.setItem('hidden_recent_stores', JSON.stringify(hidden));
  }
  loadRecentStores();  // 목록 새로고침
}

function clearAllRecentStores(){
  if(!confirm('최근 본 매장 목록을 전체 지울까요?')) return;
  // 현재 표시된 모든 매장을 숨김 처리
  const section = document.getElementById('recentStoresList');
  const items = section.querySelectorAll('.recent-store-item');
  const hidden = getHiddenRecentStores();
  items.forEach(item => {
    const btn = item.querySelector('.item-delete-btn');
    if(btn){
      const onclick = btn.getAttribute('onclick');
      const match = onclick.match(/hideRecentStore\\('([^']+)'\\)/);
      if(match) hidden.push(match[1]);
    }
  });
  localStorage.setItem('hidden_recent_stores', JSON.stringify([...new Set(hidden)]));
  loadRecentStores();
}

function formatRelativeTime(isoStr){
  if(!isoStr) return '';
  const d = new Date(isoStr);
  const now = new Date();
  const diff = Math.floor((now - d) / 1000);
  if(diff < 60) return '방금';
  if(diff < 3600) return Math.floor(diff/60) + '분 전';
  if(diff < 86400) return Math.floor(diff/3600) + '시간 전';
  if(diff < 172800) return '어제';
  return d.toLocaleDateString('ko-KR', {month:'numeric', day:'numeric'});
}

// ─────────────────────────────────────────────────────────────────────────────
// M단계: 내 매장 / 경쟁 매장 등록
// ─────────────────────────────────────────────────────────────────────────────

async function loadRegisteredStores(){
  if(!_anonId) return;
  try{
    const res = await fetch('/registered-stores/' + _anonId);
    if(!res.ok) return;
    const data = await res.json();
    renderRegisteredStores(data.my_stores || [], data.rival_stores || []);
  }catch(e){
    console.log('등록 매장 로드 실패:', e);
  }
}

function renderRegisteredStores(myStores, rivalStores){
  // 내 매장
  const mySection = document.getElementById('myStoresSection');
  const myList = document.getElementById('myStoresList');
  const myCount = document.getElementById('myStoresCount');

  if(myStores.length > 0){
    mySection.style.display = 'block';
    myCount.textContent = '관리 중 ' + myStores.length + '곳';
    myList.innerHTML = myStores.map(s => {
      const rankText = s.top_rank ? `${s.top_keyword} ${s.top_rank}위` : '';
      const scoreText = s.total_score != null ? `${Math.round(s.total_score)}점` : '';
      const time = formatRelativeTime(s.analyzed_at);
      return `<div class="registered-item my-store">
        <button class="item-delete-btn" onclick="event.stopPropagation(); deleteRegisteredStore('${esc(s.place_id)}', 'my', '${esc(s.store_name)}')" title="삭제">×</button>
        <div onclick="loadHistoryResult('${esc(s.place_id)}', '${esc(s.store_name)}')">
          <div class="registered-name">${esc(s.store_name)} <span class="registered-badge my">관리 중</span></div>
          <div class="registered-meta">
            ${rankText ? `<span class="registered-rank">${rankText}</span>` : ''}
            ${scoreText ? `<span class="registered-score">${scoreText}</span>` : ''}
            ${time ? `<span>${time}</span>` : ''}
          </div>
        </div>
      </div>`;
    }).join('');
  } else {
    mySection.style.display = 'none';
  }

  // 경쟁 매장
  const rivalSection = document.getElementById('rivalStoresSection');
  const rivalList = document.getElementById('rivalStoresList');
  const rivalCount = document.getElementById('rivalStoresCount');

  if(rivalStores.length > 0){
    rivalSection.style.display = 'block';
    rivalCount.textContent = rivalStores.length + '곳';
    rivalList.innerHTML = rivalStores.map(s => {
      const rankText = s.top_rank ? `${s.top_keyword} ${s.top_rank}위` : '';
      const scoreText = s.total_score != null ? `${Math.round(s.total_score)}점` : '';
      const time = formatRelativeTime(s.analyzed_at);
      return `<div class="registered-item rival-store">
        <button class="item-delete-btn" onclick="event.stopPropagation(); deleteRegisteredStore('${esc(s.place_id)}', 'rival', '${esc(s.store_name)}')" title="삭제">×</button>
        <div onclick="loadHistoryResult('${esc(s.place_id)}', '${esc(s.store_name)}')">
          <div class="registered-name">${esc(s.store_name)} <span class="registered-badge rival">경쟁</span></div>
          <div class="registered-meta">
            ${rankText ? `<span class="registered-rank">${rankText}</span>` : ''}
            ${scoreText ? `<span class="registered-score">${scoreText}</span>` : ''}
            ${time ? `<span>${time}</span>` : ''}
          </div>
        </div>
      </div>`;
    }).join('');
  } else {
    rivalSection.style.display = 'none';
  }
}

// O단계: 등록 매장 삭제
async function deleteRegisteredStore(placeId, storeType, storeName){
  const typeLabel = storeType === 'my' ? '내 매장' : '경쟁 매장';
  if(!confirm(`"${storeName}"을(를) ${typeLabel}에서 뺄까요?`)) return;

  try{
    const params = new URLSearchParams({
      anon_id: _anonId,
      place_id: placeId,
      store_type: storeType,
    });
    const res = await fetch('/unregister-store?' + params.toString(), {method: 'DELETE'});
    if(res.ok){
      loadRegisteredStores();  // 목록 새로고침
    }
  }catch(e){
    console.log('삭제 실패:', e);
  }
}

// 등록 상태 확인 및 버튼 업데이트
async function updateRegisterButtons(placeId){
  if(!_anonId || !placeId) return;
  try{
    const res = await fetch(`/store-registration-status/${_anonId}/${placeId}`);
    if(!res.ok) return;
    const status = await res.json();

    const btnMy = document.getElementById('btnRegisterMy');
    const btnRival = document.getElementById('btnRegisterRival');

    if(status.is_my){
      btnMy.classList.add('registered');
      btnMy.innerHTML = `
        <i data-lucide="check" class="rpt-icon is-good btn-reg-icon"></i>
        <span class="btn-register-label">내 매장 등록됨</span>
        <span class="btn-unregister" onclick="event.stopPropagation(); unregisterStore('my')">등록 해제</span>
      `;
    } else {
      btnMy.classList.remove('registered');
      btnMy.innerHTML = `
        <i data-lucide="star" class="rpt-icon is-info btn-reg-icon"></i>
        <span class="btn-register-label">내 매장으로 등록</span>
        <span class="btn-register-hint">매주 순위 변화를 알려드려요<br>(곧 출시)</span>
      `;
    }

    if(status.is_rival){
      btnRival.classList.add('registered');
      btnRival.innerHTML = `
        <i data-lucide="check" class="rpt-icon is-good btn-reg-icon"></i>
        <span class="btn-register-label">경쟁 매장 등록됨</span>
        <span class="btn-unregister" onclick="event.stopPropagation(); unregisterStore('rival')">등록 해제</span>
      `;
    } else {
      btnRival.classList.remove('registered');
      btnRival.innerHTML = `
        <i data-lucide="eye" class="rpt-icon is-info btn-reg-icon"></i>
        <span class="btn-register-label">경쟁 매장으로 등록</span>
        <span class="btn-register-hint">옆 매장 순위를 슬쩍 지켜보세요</span>
      `;
    }
    lucide.createIcons();
  }catch(e){
    console.log('등록 상태 확인 실패:', e);
  }
}

async function registerStore(storeType){
  const d = window._diagData;
  if(!d || !d.place_id){
    alert('매장 정보를 찾을 수 없습니다.');
    return;
  }
  if(!_anonId){
    alert('잠시 후 다시 시도해주세요.');
    return;
  }

  try{
    const params = new URLSearchParams({
      anon_id: _anonId,
      place_id: d.place_id,
      store_name: d.store_name || '',
      store_type: storeType,
    });
    const res = await fetch('/register-store?' + params.toString(), {method: 'POST'});
    if(!res.ok) throw new Error('등록 실패');

    // 버튼 상태 업데이트 (alert 대신 상태 변화로 피드백)
    await updateRegisterButtons(d.place_id);
    // 등록 매장 목록도 새로고침
    loadRegisteredStores();
  }catch(e){
    console.log('등록 실패:', e);
  }
}

async function unregisterStore(storeType){
  const d = window._diagData;
  if(!d || !d.place_id || !_anonId) return;

  const typeLabel = storeType === 'my' ? '내 매장' : '경쟁 매장';
  if(!confirm(`${typeLabel} 등록을 해제할까요?`)) return;

  try{
    const params = new URLSearchParams({
      anon_id: _anonId,
      place_id: d.place_id,
      store_type: storeType,
    });
    const res = await fetch('/unregister-store?' + params.toString(), {method: 'DELETE'});
    if(!res.ok) throw new Error('해제 실패');

    await updateRegisterButtons(d.place_id);
  }catch(e){
    alert('등록 해제에 실패했습니다.');
  }
}

// K단계: 저장된 결과 즉시 표시 (place + blog 둘 다)
let _historyPlaceData = null;
let _historyBlogData = null;
let _historyBlogRendered = false;  // 히스토리 블로그 데이터 렌더링 여부

async function loadHistoryResult(placeId, storeName){
  try{
    const res = await fetch('/history-result-all/' + placeId);
    if(!res.ok) throw new Error('저장된 결과 없음');
    const allData = await res.json();

    _historyPlaceData = allData.place;
    _historyBlogData = allData.blog;

    const placeData = _historyPlaceData;
    if(!placeData){
      alert('저장된 플레이스 분석 결과가 없습니다.');
      return;
    }

    _lastStoreName = storeName;
    _lastPlaceUrl = placeData.place_url || (placeData.place_id ? 'https://m.place.naver.com/place/' + placeData.place_id : '');
    _prevAnalysis = placeData.prev_analysis || null;

    document.getElementById('input-section').style.display = 'none';
    document.getElementById('loading-section').style.display = 'none';
    document.getElementById('result').style.display = 'block';
    document.querySelector('.tabs').style.display = 'flex';
    document.getElementById('tab-place').style.display = 'block';
    document.getElementById('tab-blog').style.display = 'none';
    document.querySelector('.tab-btn[data-tab="place"]').classList.add('active');
    document.querySelector('.tab-btn[data-tab="blog"]').classList.remove('active');

    // 블로그 탭 상태 설정 (저장된 결과 있으면 바로 볼 수 있게)
    _blogAnalyzed = false;  // 아직 블로그 탭에서 렌더링 안 함
    _historyBlogRendered = false;  // 히스토리 블로그 렌더링 플래그 초기화

    renderResult(placeData);
    // M단계: 등록 버튼 상태 업데이트
    if(placeData.place_id) updateRegisterButtons(placeData.place_id);
    window.scrollTo({top:0,behavior:'smooth'});
  }catch(e){
    alert('저장된 분석 결과를 불러올 수 없습니다. 새로 분석해주세요.');
  }
}

// K단계: 다른 매장 검색 (새로고침 없이)
// L단계: 랜딩 히어로 "내 순위 확인하기" → 검색폼으로 부드럽게 스크롤
function scrollToSearch(){
  const el = document.getElementById('searchStart') || document.getElementById('searchFormCard');
  if(el) el.scrollIntoView({behavior:'smooth', block:'start'});
  setTimeout(function(){ var s=document.getElementById('storeName'); if(s) s.focus({preventScroll:true}); }, 450);
}

// N단계: 홈으로 (로고 클릭, 중지하기에서도 사용)
function goHome(){
  // 분석 중이면 중단
  if(_analysisAbortController){
    _analysisAbortController.abort();
    _analysisAbortController = null;
  }
  stopLoading();
  stopTips();
  goBackToSearch();
}

function goBackToSearch(){
  document.getElementById('result').style.display = 'none';
  document.getElementById('loading-section').style.display = 'none';
  document.getElementById('input-section').style.display = 'block';

  // 입력 필드 초기화
  document.getElementById('storeName').value = '';
  document.getElementById('placeUrl').value = '';
  _forceRefresh = false;

  // 버튼 상태 리셋
  const btn = document.getElementById('diagBtn');
  btn.disabled = false;
  btn.textContent = '내 순위 확인하기';

  // M단계: 등록 매장 + 최근 매장 새로고침
  loadRegisteredStores();
  loadRecentStores();

  window.scrollTo({top:0,behavior:'smooth'});
}

// K단계: 같은 매장 다시 분석 (현재 탭 기준)
async function reAnalyze(){
  if(!_lastStoreName || !_lastPlaceUrl){
    const d = window._diagData;
    if(d){
      _lastStoreName = d.store_name || '';
      _lastPlaceUrl = d.place_url || (d.place_id ? 'https://m.place.naver.com/place/' + d.place_id : '');
    }
  }
  if(!_lastStoreName || !_lastPlaceUrl){
    alert('매장 정보를 찾을 수 없습니다. 다시 검색해주세요.');
    goBackToSearch();
    return;
  }

  document.getElementById('storeName').value = _lastStoreName;
  document.getElementById('placeUrl').value = _lastPlaceUrl;
  _forceRefresh = true;

  // 현재 보고 있는 탭 기준으로 분석 유형 결정
  const blogTabVisible = document.getElementById('tab-blog').style.display !== 'none'
                      && document.getElementById('tab-blog').classList.contains('active');
  const placeTabVisible = document.getElementById('tab-place').style.display !== 'none';

  // 블로그 탭만 보이면 블로그, 아니면 플레이스
  _analysisType = (blogTabVisible && !placeTabVisible) ? 'blog' : 'place';

  document.querySelectorAll('.analysis-type-btn').forEach(b => b.classList.remove('selected'));
  document.querySelector(`.analysis-type-btn[data-type="${_analysisType}"]`).classList.add('selected');

  startAnalysis();
}

// ── 분석 유형 선택 ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // 디자인2차: 정적 라인 아이콘(결과 카드 타이틀·탭·배지 등) 초기 렌더
  if(window.lucide) lucide.createIcons();
  // K단계: 익명 ID 발급 + 최근 매장 로드
  _anonId = getOrCreateAnonId();
  loadRegisteredStores();  // M단계: 내 매장 / 경쟁 매장 먼저
  loadRecentStores();

  document.querySelectorAll('.analysis-type-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.analysis-type-btn').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      _analysisType = btn.dataset.type;
      // 플레이스 분석 시에만 광고 체크박스 표시
      document.getElementById('adFieldsWrap').style.display = _analysisType === 'place' ? 'block' : 'none';
    });
  });

  // N단계: 스크롤 시 맨위로 버튼 표시
  const scrollBtn = document.getElementById('btnScrollTop');
  window.addEventListener('scroll', () => {
    if(window.scrollY > 300) scrollBtn.classList.add('visible');
    else scrollBtn.classList.remove('visible');
  });

  // SEO: FAQ 아코디언 토글
  document.querySelectorAll('.faq-q').forEach(btn => {
    btn.addEventListener('click', () => {
      const answer = btn.nextElementSibling;
      btn.classList.toggle('open');
      answer.classList.toggle('open');
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
  if(s == null) return `<span class="chip chip-none">정보 없음</span>`;
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
  { label:'매장 정보 수집 중',    icon:'search',        desc:'네이버 플레이스에서 매장 정보를 읽어오고 있어요',       ms:12000 },
  { label:'키워드 순위 분석 중',  icon:'bar-chart-3',   desc:'검색 키워드 30개를 하나씩 확인하고 있어요 (가장 오래 걸려요)', ms:80000 },
  { label:'리뷰·별점 수집 중',   icon:'star',          desc:'방문자 리뷰, 블로그 리뷰, 별점 데이터를 모으고 있어요', ms:15000 },
  { label:'경쟁사 비교 중',      icon:'trophy',        desc:'같은 키워드 1위 매장 정보를 분석하고 있어요',         ms:15000 },
  { label:'블로그 분석 중',      icon:'file-text',     desc:'우리 매장 태그한 블로그 순위를 확인하고 있어요',       ms:70000 },
  { label:'점수 계산 중',        icon:'check-circle-2',desc:'4축 진단 점수를 계산하고 있어요 — 거의 다 됐어요!',    ms:999999 },
];
const L_TIPS = [
  '방문자 리뷰 50개 이상이면 검색 노출에 유리해요',
  '플레이스 사진은 최소 10장 이상 등록하면 점수가 올라요',
  '키워드가 업종·지역과 잘 맞을수록 상위 노출 가능성이 높아요',
  '최근 30일 이내 리뷰가 있으면 활성도 점수가 높아져요',
  '매장 정보(주소·전화·영업시간)가 완전할수록 노출에 유리해요',
];

let _lStart=0, _lTimer=null, _lStepIdx=0, _lRafId=null, _lProg=0;

// 블로그 분석용 로딩 스텝
const L_STEPS_BLOG = [
  { label:'매장 정보 수집 중',    icon:'search',        desc:'네이버 플레이스에서 매장 정보를 읽어오고 있어요',       ms:15000 },
  { label:'키워드 추출 중',       icon:'file-text',     desc:'블로그 검색에 사용할 키워드를 생성하고 있어요',        ms:5000 },
  { label:'블로그 순위 분석 중',  icon:'bar-chart-3',   desc:'키워드별 블로그 검색 순위를 확인하고 있어요',         ms:60000 },
  { label:'결과 정리 중',         icon:'check-circle-2',desc:'분석 결과를 정리하고 있어요 — 거의 다 됐어요!',       ms:999999 },
];

function startLoading(type){
  _lStart=Date.now(); _lStepIdx=0; _lProg=0;
  document.getElementById('lBar').style.width='0%';
  document.getElementById('lPct').textContent='0%';

  // UI 초기화
  document.getElementById('topKwChips').innerHTML = '';
  document.getElementById('kwPopupArea').innerHTML = '';
  document.getElementById('boot-sequence').innerHTML = '';
  document.getElementById('boot-sequence').style.display = 'none';
  document.getElementById('tip-section').style.display = 'none';

  // 분석 유형에 따라 로딩 화면 텍스트 변경
  if(type === 'blog'){
    document.getElementById('lIcon').innerHTML = '<i data-lucide="file-text" class="rpt-icon is-good"></i>';
    document.getElementById('lTitle').textContent = '블로그 분석 중이에요';
    document.getElementById('lProgressText').textContent = '블로그 순위 분석 중';
  } else {
    document.getElementById('lIcon').innerHTML = '<i data-lucide="bar-chart-3" class="rpt-icon is-good"></i>';
    document.getElementById('lTitle').textContent = '플레이스 진단 중이에요';
    document.getElementById('lProgressText').textContent = '키워드 분석 중';
  }
  if(window.lucide) lucide.createIcons();

  // S단계: 가짜 단계 애니메이션 제거, 진행률 바만 유지
  _animateLBar();
}

function stopLoading(){
  clearInterval(_lTimer); cancelAnimationFrame(_lRafId);
  stopTips();  // 부팅/팁 슬라이더 정지 (블로그·플레이스 공통)
  document.getElementById('lBar').style.width='100%';
  document.getElementById('lPct').textContent='100%';
}

function _advanceLStep(steps){
  const elapsed=Date.now()-_lStart;
  let cum=0, idx=0;
  for(let i=0;i<steps.length;i++){cum+=steps[i].ms;if(elapsed<cum){idx=i;break;}idx=steps.length-1;}
  if(idx!==_lStepIdx){_lStepIdx=idx;}
}

function _renderLSteps(active,steps){
  const stepsArr = steps || L_STEPS;
  document.getElementById('lSteps').innerHTML=stepsArr.map((s,i)=>{
    const state=i<active?'done':i===active?'active':'pending';
    const ic=state==='done'?'check':s.icon;
    const dots=state==='active'?'<span class="dots"><span></span><span></span><span></span></span>':'';
    return `<div class="l-step">
      <div class="l-ic ${state}"><i data-lucide="${ic}" class="rpt-icon"></i></div>
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

// ── 플레이스 분석 (R단계: SSE 스트리밍) ──────────────────────────────────────
async function analyzePlaceOnly(){
  const name = document.getElementById('storeName').value.trim();
  const url  = document.getElementById('placeUrl').value.trim();
  const force= _forceRefresh; _forceRefresh = false;
  const adFlags = {
    ad_place:     document.getElementById('adPlace').checked,
    ad_powerlink: document.getElementById('adPowerlink').checked,
    ad_local:     document.getElementById('adLocal').checked,
    ad_blog:      document.getElementById('adBlog').checked,
  };
  if(!name||!url){alert('매장명과 URL을 입력해주세요.');return;}

  _lastStoreName = name;
  _lastPlaceUrl = url;
  _historyPlaceData = null;
  _historyBlogData = null;
  _historyBlogRendered = false;
  _blogAnalyzed = false;

  const btn = document.getElementById('diagBtn');
  btn.disabled=true; btn.textContent='분석 중...';
  document.getElementById('errBox').innerHTML='';

  document.getElementById('input-section').style.display='none';
  document.getElementById('loading-section').style.display='block';
  startLoading('place');
  window.scrollTo({top:0,behavior:'smooth'});

  // 부팅 시퀀스 즉시 시작 (SSE 연결 전에 입력 매장명으로)
  showBootSequence(name, '', '');

  // R단계: SSE로 실시간 스트리밍
  const params = new URLSearchParams({
    store_name: name,
    place_url: url,
    force_refresh: force,
    anon_id: _anonId || '',
    ad_place: adFlags.ad_place,
    ad_powerlink: adFlags.ad_powerlink,
    ad_local: adFlags.ad_local,
    ad_blog: adFlags.ad_blog,
  });

  // R단계: 게임 점수 상태
  let _gameScore = 0;
  let _maxScore = 100;  // complete 이벤트에서 실제 종합점수로 갱신

  let eventSource = null;
  try {
    eventSource = new EventSource('/diagnose-stream?' + params.toString());

    eventSource.addEventListener('started', (e) => {
      const d = JSON.parse(e.data);
      console.log('[SSE] started:', d);
      // 카테고리/주소 정보로 팁 리스트 업데이트 (부팅 시퀀스는 이미 시작됨)
      if(d.category || d.address) {
        _tipList = getTips(d.store_name || name, d.category || '', d.address || '');
      }
      // R단계: 게임 UI 초기화
      _gameScore = 0;
      document.getElementById('kwPopupArea').innerHTML = '';
      document.getElementById('topKwChips').innerHTML = '';
      // S단계: 분석 중 펄스 표시
      _showAnalyzingPulse();
    });

    // S단계: 분석 중 펄스 표시 함수
    function _showAnalyzingPulse() {
      const area = document.getElementById('kwPopupArea');
      area.innerHTML = `
        <div class="kw-analyzing">
          <div class="kw-analyzing-icon"><i data-lucide="search" class="rpt-icon"></i></div>
          <div class="kw-analyzing-text">다음 키워드 분석 중<span class="kw-analyzing-dots"><span></span><span></span><span></span></span></div>
        </div>
      `;
      if(window.lucide) lucide.createIcons();
    }

    eventSource.addEventListener('keyword', (e) => {
      const d = JSON.parse(e.data);
      console.log('[SSE] keyword:', d.keyword, 'rank:', d.rank, 'progress:', d.progress + '/' + d.total);

      // 진행률 바 업데이트
      if(d.total > 0) {
        const pct = Math.min(95, Math.round((d.progress / d.total) * 90));
        const bar = document.getElementById('lBar');
        const pctEl = document.getElementById('lPct');
        if(bar) bar.style.width = pct + '%';
        if(pctEl) pctEl.textContent = pct + '%';
      }

      // 키워드 팝업 + 리액션 (순위 있는 것만 표시)
      const area = document.getElementById('kwPopupArea');
      const rank = d.rank;

      if(rank !== null) {
        let reaction = '';
        let rankClass = '';
        if(rank === 1) { reaction = '오~!'; rankClass = 'top'; }
        else if(rank <= 3) { reaction = 'Nice!'; rankClass = 'top'; }
        else if(rank <= 5) { reaction = 'Good!'; rankClass = 'top'; }
        else if(rank <= 10) { rankClass = 'top'; }

        const popup = document.createElement('div');
        popup.className = 'kw-popup';
        popup.innerHTML = `
          <div class="kw-text">${esc(d.keyword)}</div>
          <div class="kw-rank ${rankClass}">${rank}위</div>
          ${reaction ? '<div class="kw-reaction">' + reaction + '</div>' : ''}
        `;
        area.innerHTML = '';
        area.appendChild(popup);

        // 상위 키워드 칩 누적
        const chipsArea = document.getElementById('topKwChips');
        let chipClass = 'rank-11-plus';
        if(rank === 1) chipClass = 'rank-1';
        else if(rank <= 3) chipClass = 'rank-2-3';
        else if(rank <= 5) chipClass = 'rank-4-5';
        else if(rank <= 10) chipClass = 'rank-6-10';

        const chip = document.createElement('span');
        chip.className = 'top-kw-chip ' + chipClass;
        chip.innerHTML = '<span class="chip-rank">' + rank + '위</span>' + esc(d.keyword);
        chipsArea.appendChild(chip);

        // 1초 후 분석 중 펄스로 전환
        if(d.progress < d.total) {
          setTimeout(() => {
            if(area.querySelector('.kw-popup')) {
              _showAnalyzingPulse();
            }
          }, 1200);
        }
      }

      // R단계: 점수 차오르기 (천장 규칙)
      const scoreDelta = d.score_delta || 0;
      if(scoreDelta > 0) {
        const newScore = Math.min(_gameScore + scoreDelta, _maxScore);
        const actualDelta = newScore - _gameScore;
        _gameScore = newScore;

        const numEl = document.getElementById('gameScoreNum');
        const deltaEl = document.getElementById('gameScoreDelta');

        numEl.textContent = _gameScore;
        numEl.classList.add('bump');
        setTimeout(() => numEl.classList.remove('bump'), 200);

        if(actualDelta > 0) {
          deltaEl.textContent = '+' + actualDelta;
          deltaEl.classList.remove('show');
          void deltaEl.offsetWidth;
          deltaEl.classList.add('show');
        }
      }
    });

    eventSource.addEventListener('complete', (e) => {
      eventSource.close();
      stopLoading();
      stopTips();
      const data = JSON.parse(e.data);
      _prevAnalysis = data.prev_analysis || null;

      // R단계: 최종 점수로 안착 (천장 = 실제 종합점수)
      const finalScore = data.scores?.total || 0;
      _maxScore = finalScore;
      _gameScore = finalScore;
      const numEl = document.getElementById('gameScoreNum');
      if(numEl) {
        numEl.textContent = finalScore;
        numEl.classList.add('bump');
        setTimeout(() => numEl.classList.remove('bump'), 200);
      }

      // 약간의 딜레이 후 결과 화면 전환 (완료 느낌)
      setTimeout(() => {
        document.getElementById('loading-section').style.display='none';
        renderResult(data);
        document.getElementById('result').style.display='block';
        switchTab('place');
        loadRecentStores();
        if(data.place_id) updateRegisterButtons(data.place_id);
        btn.disabled=false; btn.textContent='내 순위 확인하기';
        window.scrollTo({top:0,behavior:'smooth'});
      }, 800);
    });

    eventSource.addEventListener('error', (e) => {
      eventSource.close();
      stopLoading();
      stopTips();
      let msg = '연결 오류';
      try { const d = JSON.parse(e.data); msg = d.message || msg; } catch(x){}
      document.getElementById('loading-section').style.display='none';
      document.getElementById('input-section').style.display='block';
      document.getElementById('errBox').innerHTML=`<div class="err-box">분석 오류: ${esc(msg)}</div>`;
      btn.disabled=false; btn.textContent='내 순위 확인하기';
    });

    eventSource.onerror = (e) => {
      // SSE 연결 자체 실패
      if(eventSource.readyState === EventSource.CLOSED) return;
      eventSource.close();
      stopLoading();
      document.getElementById('loading-section').style.display='none';
      document.getElementById('input-section').style.display='block';
      document.getElementById('errBox').innerHTML=`<div class="err-box">서버 연결 실패. 잠시 후 다시 시도해주세요.</div>`;
      btn.disabled=false; btn.textContent='내 순위 확인하기';
    };

  } catch(e) {
    if(eventSource) eventSource.close();
    stopLoading();
    document.getElementById('loading-section').style.display='none';
    document.getElementById('input-section').style.display='block';
    document.getElementById('errBox').innerHTML=`<div class="err-box">요청 실패: ${esc(e.message)}</div>`;
    btn.disabled=false; btn.textContent='내 순위 확인하기';
  }
}

// ── 블로그 분석 (단독) ────────────────────────────────────────────────────────
async function analyzeBlogOnly(){
  const name = document.getElementById('storeName').value.trim();
  const url  = document.getElementById('placeUrl').value.trim();
  if(!name||!url){alert('매장명과 URL을 입력해주세요.');return;}

  // K단계: 마지막 분석 정보 저장 + 히스토리 초기화
  _lastStoreName = name;
  _lastPlaceUrl = url;
  _historyPlaceData = null;
  _historyBlogData = null;
  _historyBlogRendered = false;

  const btn = document.getElementById('diagBtn');
  btn.disabled=true; btn.textContent='분석 중...';
  document.getElementById('errBox').innerHTML='';

  document.getElementById('input-section').style.display='none';
  document.getElementById('loading-section').style.display='block';
  startLoading('blog');
  window.scrollTo({top:0,behavior:'smooth'});

  // 블로그 부팅 시퀀스 + 꿀팁 (플레이스와 동일 느낌, 내용은 블로그용)
  showBootSequence(name, '', '', 'blog');

  function _showBlogPulse(){
    const area = document.getElementById('kwPopupArea');
    area.innerHTML = `
      <div class="kw-analyzing">
        <div class="kw-analyzing-icon"><i data-lucide="search" class="rpt-icon"></i></div>
        <div class="kw-analyzing-text">블로그 노출 검색 중<span class="kw-analyzing-dots"><span></span><span></span><span></span></span></div>
      </div>`;
    if(window.lucide) lucide.createIcons();
  }

  let eventSource = null;
  try {
    const params = new URLSearchParams({ store_name: name, place_url: url, anon_id: _anonId || '' });
    eventSource = new EventSource('/analyze-blog-stream?' + params.toString());

    eventSource.addEventListener('started', (e) => {
      document.getElementById('kwPopupArea').innerHTML = '';
      document.getElementById('topKwChips').innerHTML = '';
      _showBlogPulse();
    });

    eventSource.addEventListener('blog_keyword', (e) => {
      const d = JSON.parse(e.data);
      // 진행률 바
      if(d.total > 0){
        const pct = Math.min(95, Math.round((d.progress / d.total) * 90));
        const bar = document.getElementById('lBar'); const pctEl = document.getElementById('lPct');
        if(bar) bar.style.width = pct + '%';
        if(pctEl) pctEl.textContent = pct + '%';
      }
      const area = document.getElementById('kwPopupArea');
      const matched = d.matched || 0;
      const best = d.best_rank;
      const hits = d.hits || [];
      // 검출된 키워드만 팝업 (없으면 담백하게 펄스 유지)
      if(matched > 0){
        let reaction = '', rankClass = '';
        if(best === 1){ reaction='오~!'; rankClass='top'; }
        else if(best <= 3){ reaction='Nice!'; rankClass='top'; }
        else if(best <= 5){ reaction='Good!'; rankClass='top'; }
        else if(best <= 10){ rankClass='top'; }

        const popup = document.createElement('div');
        popup.className = 'kw-popup';
        popup.innerHTML = `
          <div class="kw-text">${esc(d.keyword)}</div>
          <div class="kw-rank ${rankClass}">${matched}개 검출${best?' · 최고 '+best+'위':''}</div>
          ${reaction ? '<div class="kw-reaction">' + reaction + '</div>' : ''}
        `;
        area.innerHTML = '';
        area.appendChild(popup);

        // 칩 누적 (키워드 + 모든 순위 칩이 탁탁 붙음)
        const chipsArea = document.getElementById('topKwChips');
        const kwRow = document.createElement('div');
        kwRow.className = 'blog-kw-row';
        kwRow.innerHTML = `<span class="blog-kw-name">${esc(d.keyword)}</span>`;
        hits.sort((a,b) => a.rank - b.rank).forEach((h, i) => {
          const rankChip = document.createElement('span');
          let rc = 'rank-11-plus';
          if(h.rank === 1) rc = 'rank-1';
          else if(h.rank <= 3) rc = 'rank-2-3';
          else if(h.rank <= 5) rc = 'rank-4-5';
          else if(h.rank <= 10) rc = 'rank-6-10';
          rankChip.className = 'blog-rank-chip ' + rc;
          rankChip.textContent = h.rank + '위';
          rankChip.style.animationDelay = (i * 0.1) + 's';
          kwRow.appendChild(rankChip);
        });
        chipsArea.appendChild(kwRow);

        if(d.progress < d.total){
          setTimeout(() => { if(area.querySelector('.kw-popup')) _showBlogPulse(); }, 1200);
        }
      }
    });

    eventSource.addEventListener('complete', (e) => {
      eventSource.close();
      stopLoading();
      const data = JSON.parse(e.data);
      _prevAnalysis = data.prev_analysis || null;
      setTimeout(() => {
        document.getElementById('loading-section').style.display='none';
        renderBlogOnlyResult(data);
        document.getElementById('result').style.display='block';
        switchTab('blog');
        loadRecentStores();
        btn.disabled=false; btn.textContent='내 순위 확인하기';
        window.scrollTo({top:0,behavior:'smooth'});
      }, 800);
    });

    eventSource.addEventListener('error', (e) => {
      eventSource.close();
      stopLoading();
      let msg = '분석 중 문제가 발생했어요. 잠시 후 다시 시도해주세요.';
      try { const d = JSON.parse(e.data); if(d && d.message) msg = d.message; } catch(x){}
      document.getElementById('loading-section').style.display='none';
      document.getElementById('input-section').style.display='block';
      document.getElementById('errBox').innerHTML=`<div class="err-box">${esc(msg)}</div>`;
      btn.disabled=false; btn.textContent='내 순위 확인하기';
    });

    eventSource.onerror = () => {
      if(eventSource.readyState === EventSource.CLOSED) return;
      eventSource.close();
      stopLoading();
      document.getElementById('loading-section').style.display='none';
      document.getElementById('input-section').style.display='block';
      document.getElementById('errBox').innerHTML=`<div class="err-box">서버 연결이 끊겼어요. 잠시 후 다시 시도해주세요.</div>`;
      btn.disabled=false; btn.textContent='내 순위 확인하기';
    };
  } catch(e) {
    stopLoading();
    document.getElementById('loading-section').style.display='none';
    document.getElementById('input-section').style.display='block';
    document.getElementById('errBox').innerHTML=`<div class="err-box">요청 실패: ${esc(e.message)}</div>`;
    btn.disabled=false; btn.textContent='내 순위 확인하기';
  }
}

// ── 결과 렌더링 ───────────────────────────────────────────────────────────────
function renderResult(d){
  window._diagData = d;
  _lastResultData = d;
  const sc = d.scores||{};
  const prev = _prevAnalysis;

  // 게이지 복원 (직전에 블로그 결과를 봤을 수 있으므로 원위치)
  document.getElementById('gaugeCardTitle').textContent = '종합 플레이스 점수';
  document.getElementById('gaugeSvg').style.display = '';
  document.getElementById('blogHeadline').style.display = 'none';

  // 매장 정보
  document.getElementById('rStoreName').textContent = d.store_name||'-';
  document.getElementById('rCategory').textContent  = d.category||'매장';
  document.getElementById('rMeta').textContent      = d.address||'';

  // J단계: 분석 횟수 표시
  renderAnalysisHistoryInfo(d, 'place');

  // 종합 게이지 + 변동 표시
  const tot = sc.total??0;
  animateGauge(tot);
  const g = grade(tot);
  const badge = document.getElementById('gradeBadge');
  badge.textContent=g.text; badge.style.background=g.bg;

  let summaryHtml = buildSummary(d,sc);
  document.getElementById('gaugeSummary').innerHTML = summaryHtml;

  // J단계+K추가: 종합점수 직전 비교 (강조 + 단계별 멘트)
  const trendEl = document.getElementById('scoreTrend');
  if(prev && prev.total_score != null){
    const prevScore = Math.round(prev.total_score);
    const diff = Math.round(tot - prevScore);
    const absDiff = Math.abs(diff);
    let cls, arrow, ment;

    if(diff > 0){
      cls = 'trend-up';
      arrow = '▲';
      if(absDiff >= 10) ment = '크게 상승했어요!';
      else if(absDiff >= 4) ment = '순위가 오르고 있어요! 잘하고 계세요';
      else ment = '조금씩 좋아지고 있어요';
    } else if(diff < 0){
      cls = 'trend-down';
      arrow = '▼';
      if(absDiff >= 10) ment = '최근 노출이 많이 줄었어요. 원인을 살펴보는 게 좋아요';
      else if(absDiff >= 4) ment = '점수가 떨어지고 있어요. 점검해볼 시점이에요';
      else ment = '살짝 주춤했어요. 조금만 관리하면 금방 회복돼요';
    } else {
      cls = 'trend-same';
      arrow = '―';
      ment = '지난번과 같은 순위를 유지하고 있어요';
    }

    trendEl.className = 'score-trend ' + cls;
    if(diff === 0) {
      trendEl.innerHTML = `
        <div class="trend-main">
          <span class="trend-arrow">${arrow}</span>
          <span class="trend-vs">${Math.round(tot)}점 유지</span>
        </div>
        <div class="trend-ment">${ment}</div>
      `;
    } else {
      trendEl.innerHTML = `
        <div class="trend-main">
          <span class="trend-arrow">${arrow}</span>
          <span class="trend-diff">${diff > 0 ? '+' : ''}${diff}점</span>
          <span class="trend-vs">(${prevScore}점 → ${Math.round(tot)}점)</span>
        </div>
        <div class="trend-ment">${ment}</div>
      `;
    }
    trendEl.style.display = 'block';
  } else {
    trendEl.style.display = 'none';
  }

  // 4축 카드
  renderAxisCards(d, sc);

  // 경쟁사
  renderCompetitor(d);

  // 키워드 (J단계: 키워드별 히스토리 전달)
  _allKw = d.place_results||[];
  const prevRankMap = buildPrevRankMap(prev);
  const kwHistory = d.keyword_history || {};
  renderKeywords(false, prevRankMap, kwHistory);

  // 블로그 분석
  renderBlogResults(d.blog_results||[]);

  // 닥터 코멘트
  renderComment(d, sc);

  // 디자인2차: 동적 렌더링 완료 후 Lucide 라인 아이콘 그리기
  if(window.lucide) lucide.createIcons();
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

// J단계: 분석 횟수/시점 안내 표시
function renderAnalysisHistoryInfo(d, type){
  const el = document.getElementById('analysisHistoryInfo');
  const count = d.analysis_count || 0;
  const prevDate = d.prev_analyzed_at || null;

  if(count <= 1 && !prevDate){
    el.innerHTML = '첫 분석이에요. 다음에 또 분석하면 변화를 보여드려요.';
    el.style.display = 'block';
  } else if(count > 1){
    const typeLabel = type === 'place' ? '플레이스' : '블로그';
    let info = `이 매장 ${typeLabel} ${count}번째 분석`;
    if(prevDate){
      info += ` · 지난 분석 ${prevDate}`;
    }
    el.innerHTML = info;
    el.style.display = 'block';
  } else {
    el.style.display = 'none';
  }
}

// 블로그 단독 결과 렌더링
function renderBlogOnlyResult(d){
  window._diagData = d;
  const prev = _prevAnalysis;

  document.getElementById('rStoreName').textContent = d.store_name||'-';
  document.getElementById('rCategory').textContent  = d.category||'매장';
  document.getElementById('rMeta').textContent      = d.address||'';

  // J단계: 블로그 분석 횟수 표시
  renderAnalysisHistoryInfo(d, 'blog');

  // 블로그 노출 요약 헤드라인 (빈 100점 게이지 대신 검출 건수 표시)
  const _br = d.blog_results || [];
  let _totalMatched = 0, _kwWithHits = 0, _best = null;
  for(const r of _br){
    const ranks = (r.hits||[]).filter(h=>h.rank!=null).map(h=>h.rank);
    if(ranks.length){ _kwWithHits++; _totalMatched += ranks.length; const m = Math.min(...ranks); if(_best===null||m<_best) _best=m; }
  }
  document.getElementById('gaugeCardTitle').textContent = '블로그 노출 요약';
  document.getElementById('gradeBadge').textContent = '블로그';
  document.getElementById('gradeBadge').style.background = '#3b82f6';
  document.getElementById('gaugeSvg').style.display = 'none';
  const _bh = document.getElementById('blogHeadline');
  _bh.style.display = 'flex';
  if(_totalMatched > 0){
    _bh.innerHTML = `<div class="bh-num">${_totalMatched}<small>건 검출</small></div>`
      + `<div class="bh-sub">노출 키워드 <b>${_kwWithHits}</b>개 · 최고 <b>${_best}위</b></div>`;
  } else {
    _bh.innerHTML = `<div class="bh-empty">아직 노출된 블로그가 없어요</div>`
      + `<div class="bh-sub" style="color:var(--gray-500);font-weight:600">블로그 마케팅(체험단·협찬)을 시작하면 노출이 늘어나요</div>`;
  }
  document.getElementById('gaugeSummary').innerHTML = '';
  document.getElementById('scoreTrend').style.display = 'none';

  // 탭 숨기기 (블로그 결과만 표시)
  document.querySelector('.tabs').style.display = 'none';
  document.getElementById('tab-place').style.display = 'none';
  document.getElementById('tab-blog').classList.add('active');
  document.getElementById('tab-blog').style.display = 'block';

  // 블로그 시작 카드 숨기고 결과 표시 (_blogAnalyzed=true → switchTab이 시작카드로 덮어쓰지 않음)
  _blogAnalyzed = true;
  document.getElementById('blogStartCard').style.display = 'none';
  document.getElementById('blogResultCard').style.display = 'block';

  // 직전 블로그 순위 맵 + J단계: 키워드 히스토리
  const prevBlogMap = buildPrevBlogRankMap(prev);
  const kwHistory = d.keyword_history || {};
  renderBlogResultsWithComparison(d.blog_results||[], prevBlogMap, kwHistory);

  // 디자인2차: 결과 화면 라인 아이콘 렌더
  if(window.lucide) lucide.createIcons();
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
    buildActivityCard(d, sc.activity),
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
    <div class="axis-head"><span class="axis-icon"><i data-lucide="${icon}" class="rpt-icon is-info"></i></span><span class="axis-name">${name}</span></div>
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
  return axisCard('search','검색노출(SEO)',score,[
    detailRow('대표 키워드 순위', topRank?`${topRank}위`:'30위 밖', topRank?Math.max(0,100-topRank*3):5),
    detailRow('정보 완성도', infoScore+'%', infoScore),
    detailRow('사진 수', photoCount!=null?photoCount+'장':'-', photoCount!=null?Math.min(100,photoCount*8):null),
  ].join(''));
}

function buildContentCard(d, score){
  const vr = d.visitor_reviews, br = d.blog_reviews, ss = d.star_score;
  return axisCard('star','리뷰관리',score,[
    detailRow('방문자 리뷰', vr!=null?fmt(vr)+'개':'-', vr!=null?Math.min(100,vr/5):null),
    detailRow('블로그 리뷰', br!=null?fmt(br)+'개':'-', br!=null?Math.min(100,br/3):null),
    detailRow('별점', ss!=null?ss+'점':'별점 없음', ss!=null?(ss>=4.5?90:ss>=4.0?65:ss>=3.5?40:20):null),
  ].join(''));
}

function buildActivityCard(d, score){
  // B단계: 리뷰 활동 수집 실패(score=null, 보통 m.place 일시 차단) → 거짓 낮은 점수 대신
  // 중립 표시. 이 경우 종합점수에서도 최근활동 축은 제외됨(백엔드 재정규화).
  if(score == null){
    return `<div class="axis-card">
      <div class="axis-head"><span class="axis-icon"><i data-lucide="activity" class="rpt-icon is-info"></i></span><span class="axis-name">최근활동</span></div>
      <div class="axis-score" style="color:var(--gray-400);font-size:1.05rem;font-weight:700">리뷰 활동 정보 수집 중</div>
      <div class="detail-list">
        <div class="detail-row"><span class="detail-label">최근 리뷰</span><div class="detail-val"><span class="detail-num" style="color:var(--gray-400)">잠시 후 다시 확인</span></div></div>
        <div class="detail-row"><span class="detail-label">정보 최신성</span><div class="detail-val"><span class="detail-num">${d.address?'최신':'미확인'}</span></div></div>
      </div>
      <div style="font-size:.72rem;color:var(--gray-400);margin-top:10px;line-height:1.55">네이버 리뷰 페이지 접근이 일시 제한돼 최근활동을 종합점수에서 제외했어요. 잠시 후 다시 분석하면 반영돼요.</div>
    </div>`;
  }
  const lr = d.latest_review_date;
  let dayStr='정보 없음', dayScore=null, diff=null;
  if(lr){
    diff=Math.floor((Date.now()-new Date(lr.replace(/[.]/g,'-')))/86400000);
    dayStr=diff<=0?'오늘':`${diff}일 전`;
    dayScore=diff<=7?100:diff<=30?80:diff<=90?55:diff<=180?30:10;
  }
  // 리뷰 활동: 백엔드 라벨 있으면 사용, 없으면 최근 리뷰 날짜로 추론
  let act=d.review_activity;
  let actScore=null;
  if(act){
    actScore=act==='활발'?100:act==='보통'?70:act==='한산'?45:25;
  } else if(diff!==null){
    // 최근 리뷰 날짜 기반 추론: 7일내=활발, 30일내=보통, 90일내=한산, 그외=거의없음
    if(diff<=7){ act='활발'; actScore=100; }
    else if(diff<=30){ act='보통'; actScore=70; }
    else if(diff<=90){ act='한산'; actScore=45; }
    else { act='거의 없음'; actScore=25; }
  }
  return axisCard('activity','최근활동',score,[
    detailRow('최근 리뷰', dayStr, dayScore),
    detailRow('리뷰 활동', act??'정보 없음', actScore),
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
  const rows = adItems.map(a=>`<div class="detail-row"><span class="detail-label">${a.name}</span><div class="detail-val"><span class="chip ${a.on?'chip-good':'chip-bad'}"><i data-lucide="${a.on?'check':'x'}" class="rpt-icon"></i>${a.on?'집행':'미집행'}</span></div></div>`).join('');
  const label = sc.ad_label?`<p style="font-size:.78rem;font-weight:700;color:var(--gray-700);margin-top:8px;">${esc(sc.ad_label)}</p>`:'';
  const note = '<p style="font-size:.72rem;color:var(--gray-600);margin-top:6px;line-height:1.5;">광고가 켜져 있어도 키워드·소재 최적화로 효율을 더 올릴 수 있어요</p>';
  return axisCard('megaphone','키워드광고',score, rows + label + note);
}

// ── 경쟁사 비교 (P단계: S/A급 1위아닌 키워드 최대3 카드 + 통찰 멘트) ──────────────
function renderCompetitor(d){
  const comp=d.competitor||{};
  const cardEl=document.getElementById('compCard');
  const rowsEl=document.getElementById('compRows');
  const status=comp.status;

  // S/A급 키워드 자체가 없음 → 간접 자극 + 방안 안내
  if(status==='no_sa'){
    cardEl.style.display='block';
    rowsEl.innerHTML=`<p class="comp-note">아직 S·A급 상위 키워드가 없어 경쟁사 비교가 어려워요. 상위 노출되는 키워드를 늘리면 경쟁 위치를 파악할 수 있어요.</p>`;
    return;
  }
  // S/A급 키워드 전부 내가 1위 → 칭찬
  if(status==='all_first'){
    cardEl.style.display='block';
    const kws=(comp.first_place_keywords||[]).map(k=>`<span class="comp-fp-kw">${esc(k)} 1위</span>`).join('');
    rowsEl.innerHTML=`<p class="comp-praise">주요 키워드에서 모두 1위예요! 잘하고 계세요</p><div class="comp-fp-list">${kws}</div>`;
    return;
  }

  const cards=comp.cards||[];
  if(!cards.length){ cardEl.style.display='none'; return; }
  cardEl.style.display='block';

  const html=cards.map(c=>{
    const gradeLabel = c.grade==='S' ? 'S급 키워드' : 'A급 키워드';
    const gradeBg    = c.grade==='S' ? 'background:#22c55e' : 'background:#3b82f6';
    const myRankTxt  = c.my_rank ? `${c.my_rank}위` : '순위권 밖';
    const compName   = c.competitor_name || '1위 매장';
    const gap        = c.gap;

    // 색·멘트 (추정·여지 표현, 광고 티 X). 근소=주황(희망)/큰차이=빨강(주의)
    let tone, ment;
    if(gap!=null && gap<=2){
      tone='#f97316';
      ment=`${esc(compName)}와는 근소한 차이 — 약간의 최적화로 역전 가능해요`;
    } else if(gap!=null && gap<=5){
      tone='#f97316';
      ment=`${esc(compName)} 매장은 플레이스 광고나 상위노출 작업을 진행 중인 것으로 보여요`;
    } else {
      tone='#ef4444';
      ment=`${esc(compName)} 매장은 리뷰·키워드 관리에 꾸준히 투자하거나 광고를 병행하는 것으로 분석돼요`;
    }
    const gapTxt = gap!=null ? `${gap}계단 차이` : '아직 순위권 밖';

    return `<div class="comp-card2">
      <div class="comp-grade" style="${gradeBg}">${gradeLabel}</div>
      <div class="comp-kw">${esc(c.keyword)}</div>
      <div class="comp-vs">
        <div class="comp-vs-me"><span class="comp-vs-lbl">내 매장</span><span class="comp-vs-rank" style="color:${tone}">${myRankTxt}</span></div>
        <div class="comp-vs-sep">vs</div>
        <div class="comp-vs-rival"><span class="comp-vs-lbl">${esc(compName)}</span><span class="comp-vs-rank">1위</span></div>
      </div>
      <div class="comp-gap2" style="color:${tone}">${gapTxt}</div>
      <div class="comp-ment">${ment}</div>
    </div>`;
  }).join('');

  rowsEl.innerHTML=`<div class="comp-grid">${html}</div>`;
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
let _lastKwHistory = {};
function renderKeywords(expanded, prevRankMap, kwHistory){
  _kwExpanded=expanded;
  if(prevRankMap) _lastPrevRankMap = prevRankMap;
  if(kwHistory) _lastKwHistory = kwHistory;
  const list=document.getElementById('kwList');
  const more=document.getElementById('kwMore');

  // 등급 계산 (businesses_total 상대 백분율)
  const grades=calcGrades(_allKw);

  // 정렬: 내 순위 높은 순(1위→2위→...) → 같은 순위면 업체수 많은 순 → 놓침은 맨 뒤(업체수순)
  const sorted=[..._allKw].sort((a,b)=>{
    const aRank = a.rank ?? 9999;
    const bRank = b.rank ?? 9999;
    // 1순위: 순위 좋은 순 (놓침=9999로 뒤로)
    if(aRank !== bRank) return aRank - bRank;
    // 2순위: 등록업체수 많은 순 (경쟁 센 키워드가 더 가치 있음)
    const at = a.businesses_total ?? -1;
    const bt = b.businesses_total ?? -1;
    return bt - at;
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
      ?`${k.rank}<span class="unit">위</span>`
      :`<span style="font-size:.85rem;font-weight:700;color:#ef4444">놓침</span>`;
    const countHtml=k.businesses_total?`<span class="kw-count">등록업체 ${k.businesses_total.toLocaleString()}개</span>`:'';

    // J단계: 키워드 히스토리 추세 표시
    let trendHtml = buildKeywordTrend(k.keyword, k.rank, _lastKwHistory);

    // 순위별 클래스 결정
    let rankClass = '';
    if(k.rank === 1) rankClass = 'rank-top';
    else if(k.rank && k.rank <= 3) rankClass = 'rank-high';
    else if(k.rank) rankClass = 'rank-mid';

    return `<div class="kw-item ${rankClass}">
      <div class="kw-main">
        <div class="kw-rank-col" style="color:${rc}">${rankDisplay}${trendHtml}</div>
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

// J단계: 키워드 히스토리 추세 문자열 생성
// S단계: 날짜별 대표 1개만 표시 (같은 날 중복 제거) + 날짜 표기
function buildKeywordTrend(keyword, currentRank, kwHistory){
  const history = kwHistory[keyword];
  if(!history || history.length === 0){
    return '<span class="kw-first">(첫 분석)</span>';
  }

  // S단계: 같은 날짜 중복 제거 (날짜별 마지막 기록만 = 가장 최근)
  const byDate = {};
  for(const h of history){
    byDate[h.date] = h;  // 같은 날짜면 덮어씀 (마지막 = 가장 최근)
  }
  const dedupedHistory = Object.values(byDate);

  // 과거 기록이 1개면 직전 비교만 (날짜 포함)
  if(dedupedHistory.length === 1){
    const prev = dedupedHistory[0];
    const dateStr = prev.date ? `<span class="kw-date">${prev.date}</span>` : '';
    if(prev.rank == null && currentRank == null) return '';
    if(prev.rank == null) return '<span class="kw-trend"><span class="up">NEW</span></span>';
    if(currentRank == null) return `<span class="kw-trend">${dateStr}<span class="down">놓침</span> (전: ${prev.rank}위)</span>`;

    const diff = prev.rank - currentRank;
    if(diff > 0){
      return `<span class="kw-trend"><span class="up">▲${diff}</span> (전: ${dateStr}${prev.rank}위)</span>`;
    } else if(diff < 0){
      return `<span class="kw-trend"><span class="down">▼${Math.abs(diff)}</span> (전: ${dateStr}${prev.rank}위)</span>`;
    } else {
      return `<span class="kw-trend"><span class="same">-</span> (전: ${dateStr}${prev.rank}위)</span>`;
    }
  }

  // S단계: 과거 기록 2개 이상 → 날짜별 가로 나열 (날짜 위, 순위 아래)
  // 형태: "6/15  6/16  지금"
  //       " 1위 → 2위 → 1위"
  const dateLabels = dedupedHistory.map(h => h.date || '');
  dateLabels.push('지금');
  const rankLabels = dedupedHistory.map(h => h.rank != null ? h.rank + '위' : '놓침');
  rankLabels.push(currentRank != null ? currentRank + '위' : '놓침');

  // 상승/하락 판단
  const lastPrev = dedupedHistory[dedupedHistory.length - 1];
  let cls = 'same';
  if(lastPrev.rank != null && currentRank != null){
    if(lastPrev.rank > currentRank) cls = 'up';
    else if(lastPrev.rank < currentRank) cls = 'down';
  } else if(lastPrev.rank == null && currentRank != null){
    cls = 'up';
  } else if(lastPrev.rank != null && currentRank == null){
    cls = 'down';
  }

  // 가로 흐름 (날짜 + 순위)
  const flow = dateLabels.map((d, i) => `<span class="trend-item"><span class="trend-date">${d}</span><span class="trend-rank">${rankLabels[i]}</span></span>`).join('<span class="trend-arrow">→</span>');

  return `<span class="kw-trend-flow ${cls}">${flow}</span>`;
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
    summaryText = `✅ ${blogResults.length}개 키워드 중 총 ${totalMatched}개 블로그가 우리 매장를 태그했어요.`;
    if(bestRank !== null){
      summaryText += ` 최고 순위는 '${bestKw}'에서 ${bestRank}위예요.`;
    }
  } else {
    summaryText = `📋 ${blogResults.length}개 키워드 모두 우리 매장를 태그한 블로그가 10위 안에 없어요.<br>블로그 마케팅(체험단, 협찬)을 시작하면 노출이 늘어나요.`;
  }
  summary.innerHTML = `<p class="blog-summary-text">${summaryText}</p>`;
}

// 블로그 분석 결과 (직전 비교 포함)
function renderBlogResultsWithComparison(blogResults, prevBlogMap, kwHistory){
  const list = document.getElementById('blogList');
  const summary = document.getElementById('blogSummary');
  kwHistory = kwHistory || {};

  if(!blogResults || blogResults.length===0){
    list.innerHTML = `<div class="blog-empty">
      <div class="blog-empty-icon">📭</div>
      <div class="blog-empty-text">블로그 노출 결과가 없어요.</div>
    </div>`;
    summary.innerHTML = '';
    return;
  }

  // 정렬: 내 순위 높은 순 → 놓침은 뒤로
  const sortedResults = [...blogResults].sort((a,b)=>{
    const aHits = (a.hits||[]).filter(h=>h.rank!=null);
    const bHits = (b.hits||[]).filter(h=>h.rank!=null);
    const aTop = aHits.length > 0 ? Math.min(...aHits.map(h=>h.rank)) : 9999;
    const bTop = bHits.length > 0 ? Math.min(...bHits.map(h=>h.rank)) : 9999;
    return aTop - bTop;
  });

  let totalMatched = 0;
  let bestRank = null;
  let bestKw = '';

  let html = '';
  for(const br of sortedResults){
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

    // J단계: 블로그 키워드 추세 (최상위 순위 기준)
    const topRank = matchedHits.length > 0 ? Math.min(...matchedHits.map(h=>h.rank)) : null;
    const kwTrendHtml = buildKeywordTrend(kw, topRank, kwHistory);

    html += `<div class="blog-kw-group">
      <div class="blog-kw-title">${esc(kw)} ${badge} ${kwTrendHtml}</div>
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
    summaryText = `✅ ${blogResults.length}개 키워드 중 총 ${totalMatched}개 블로그가 우리 매장를 태그했어요.`;
    if(bestRank !== null){
      summaryText += ` 최고 순위는 '${bestKw}'에서 ${bestRank}위예요.`;
    }
  } else {
    summaryText = `📋 ${blogResults.length}개 키워드 모두 우리 매장를 태그한 블로그가 10위 안에 없어요.<br>블로그 마케팅(체험단, 협찬)을 시작하면 노출이 늘어나요.`;
  }
  summary.innerHTML = `<p class="blog-summary-text">${summaryText}</p>`;
}

// ── 닥터 코멘트 ───────────────────────────────────────────────────────────────
function renderComment(d, sc){
  const lines=[];
  const seo=sc.seo??0, con=sc.content??0;
  // B단계: activity가 null(리뷰활동 수집 실패)이면 강점/약점 분석에서 제외 (거짓 0점으로 약점 오판 방지)
  const axisPairs=[['seo',seo],['content',con]];
  if(sc.activity!=null) axisPairs.push(['activity',sc.activity]);
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
      lines.push({i:'bar-chart-3',c:'is-info',t:`검색한 키워드 ${allKws.length}개 중 ${firstPage.length}개가 첫 화면(1~10위)에 노출 중이에요.`});
    else
      lines.push({i:'bar-chart-3',c:'is-info',t:`검색한 키워드 ${allKws.length}개 중 아직 첫 화면에 든 키워드가 없어요.`});
  }

  if(oppKw){
    const gap=Math.max(1,oppKw.rank-5);
    lines.push({i:'lightbulb',c:'is-warn',t:`다만 '${esc(oppKw.keyword)}' 키워드가 ${oppKw.rank}위라, ${gap}계단만 올리면 첫 화면이에요.`});
  } else if(rankedKws.length===0&&allKws.length>0){
    lines.push({i:'lightbulb',c:'is-warn',t:`'${esc(allKws[0].keyword)}' 같은 핵심 키워드에서 노출이 안 돼, 검색 손님을 놓치고 있어요.`});
  }

  // 2) 리뷰/별점 강점
  let strength;
  if(ss!=null&&ss>=4.5)            strength=`별점 ${ss}점으로 고객 만족도가 높아요.`;
  else if(vr!=null&&vr>=100)       strength=`방문자 리뷰 ${fmt(vr)}개로 콘텐츠 기반이 탄탄해요.`;
  else if(rankedKws.length>=5)     strength=`${rankedKws.length}개 키워드에서 노출되고 있어 기본기는 갖춰져 있어요.`;
  else{
    const bestPair=axisPairs.reduce((a,b)=>b[1]>a[1]?b:a);
    const k=bestPair[0], best=bestPair[1];
    strength = best>=50 ? `${AX[k]} 쪽은 비교적 잘 관리되고 있어요.`
                        : `아직 시작 단계지만, 손볼 곳이 명확해 개선 여지가 큰 매장이에요.`;
  }
  lines.push({i:'check-circle-2',c:'is-good',t:strength});

  // 3) 핵심 약점 (가장 낮은 축)
  const weak=axisPairs.slice().sort((a,b)=>a[1]-b[1])[0];
  const weakKey=weak[0], weakVal=weak[1];
  const weakReason={
    seo:'주요 키워드 노출이 부족해요',
    content:'리뷰·별점 관리가 경쟁사 대비 약해요',
    activity:'최근 리뷰 활동이 뜸해 신선도가 떨어져요',
  }[weakKey];
  lines.push({i:'alert-circle',c:'is-warn',t:`${AX[weakKey]} 점수는 ${weakVal}점으로, ${weakReason}.`});

  // 4) 해결 방향
  const fix={
    seo:'매장 정보·사진을 채우고 키워드 일치도를 높이면 노출이 올라가요.',
    content:'리뷰와 블로그를 꾸준히 보완하면 충분히 상위권으로 올라갈 수 있어요.',
    activity:'최근 리뷰를 꾸준히 쌓으면 신선도 점수가 빠르게 회복돼요.',
  }[weakKey];
  lines.push({i:'trending-up',c:'is-good',t:fix});

  const box=document.getElementById('commentBox');
  box.innerHTML=lines.map(l=>`<div class="comment-line"><i data-lucide="${l.i}" class="rpt-icon ${l.c}"></i><span>${l.t}</span></div>`).join('');
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
  const storeName = _lastResultData?.store_name || '매장';
  const score = _lastResultData?.scores?.total || 0;
  const keywords = _lastResultData?.keyword_ranks || [];
  const topKw = keywords.find(k => k.rank && k.rank <= 10);
  const keyword = topKw?.keyword || (keywords[0]?.keyword || '');
  const rank = topKw?.rank || (keywords[0]?.rank || '');

  const title = storeName + ' 플레이스 지수 ' + score + '점';
  const text = keyword ? ("'" + keyword + "' " + rank + "위") : '네이버 플레이스 순위 무료 확인';
  const url = 'https://placeranking.com';
  const shareText = title + ' - ' + text + ' ' + url;

  const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
  if(isMobile && navigator.share){
    navigator.share({title: title, text: text, url: url}).catch(()=>{});
  } else {
    navigator.clipboard.writeText(shareText).then(()=>{
      alert('링크가 복사되었습니다!');
    }).catch(()=>{
      prompt('아래 링크를 복사하세요:', shareText);
    });
  }
}
window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();window._pwaPrompt=e;});


// ── 알림 구독 ────────────────────────────────────────────────────────────────
async function submitSubscribe(){
  const phone = document.getElementById('subscribePhone').value.replace(/[^0-9]/g,'');
  const agreed = document.getElementById('subscribeAgree').checked;
  const btn = document.getElementById('btnSubscribe');

  if(!agreed){
    alert('수신 동의에 체크해주세요.');
    return;
  }
  if(phone.length < 10 || phone.length > 11){
    alert('올바른 휴대폰 번호를 입력해주세요.');
    return;
  }

  btn.disabled = true;
  btn.textContent = '신청 중...';

  try{
    const res = await fetch('/subscribe', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        store_name: _lastResultData?.store_name || '',
        phone: phone,
        store_url: _lastResultData?.place_url || document.getElementById('placeUrl').value,
        place_id: _lastResultData?.place_id || null,
        anon_id: _anonId,
        agreed: true
      })
    });
    const data = await res.json();
    if(res.ok){
      document.getElementById('subscribeForm').style.display = 'none';
      document.getElementById('subscribeDone').style.display = 'block';
    } else {
      alert(data.detail || '신청에 실패했습니다.');
      btn.disabled = false;
      btn.textContent = '무료 알림 신청하기';
    }
  } catch(e){
    alert('네트워크 오류가 발생했습니다.');
    btn.disabled = false;
    btn.textContent = '무료 알림 신청하기';
  }
}

// ── 탭 전환 ──────────────────────────────────────────────────────────────────
function switchTab(tabId){
  document.querySelectorAll('.tab-btn').forEach(btn=>{
    btn.classList.toggle('active', btn.dataset.tab===tabId);
  });
  document.querySelectorAll('.tab-content').forEach(content=>{
    content.classList.toggle('active', content.id===`tab-${tabId}`);
    content.style.display = content.id===`tab-${tabId}` ? 'block' : 'none';
  });

  // K단계: 블로그 탭 클릭 시 히스토리 데이터가 있으면 표시
  if(tabId === 'blog'){
    if(_historyBlogData && !_historyBlogRendered){
      _historyBlogRendered = true;
      _blogAnalyzed = true;
      document.getElementById('blogStartCard').style.display = 'none';
      document.getElementById('blogLoading').style.display = 'none';
      document.getElementById('blogResultCard').style.display = 'block';

      const prevBlogMap = buildPrevBlogRankMap(_historyBlogData.prev_analysis);
      const kwHistory = _historyBlogData.keyword_history || {};
      renderBlogResultsWithComparison(_historyBlogData.blog_results || [], prevBlogMap, kwHistory);
    } else if(!_blogAnalyzed && !_historyBlogData){
      // 블로그 기록 없으면 분석하기 버튼 표시
      document.getElementById('blogStartCard').style.display = 'block';
      document.getElementById('blogLoading').style.display = 'none';
      document.getElementById('blogResultCard').style.display = 'none';
    }
  }
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
  btn.textContent = '내 순위 확인하기';
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


@app.get("/sitemap.xml", tags=["SEO"])
async def sitemap():
    content = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://placeranking.com/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>"""
    return Response(content=content, media_type="application/xml")


@app.get("/robots.txt", tags=["SEO"])
async def robots():
    content = """User-agent: *
Allow: /
Disallow: /admin
Disallow: /admin/
Sitemap: https://placeranking.com/sitemap.xml"""
    return Response(content=content, media_type="text/plain")


# ── R단계: SSE 스트리밍 진단 엔드포인트 ─────────────────────────────────────
@app.get("/diagnose-stream", tags=["진단"])
async def diagnose_stream_endpoint(
    store_name: str,
    place_url: str,
    ad_place: bool = False,
    ad_powerlink: bool = False,
    ad_local: bool = False,
    ad_blog: bool = False,
    anon_id: str = None,
    force_refresh: bool = False,
    db: Session = Depends(get_db),
):
    """
    SSE(Server-Sent Events)로 분석 결과를 실시간 스트리밍합니다.
    - started: 분석 시작 즉시 (504 방지)
    - keyword: 키워드 순위 하나씩
    - complete: 최종 결과
    """
    import json as json_module

    place_id = _extract_place_id(place_url)
    ad_flags = {
        "place": ad_place,
        "powerlink": ad_powerlink,
        "local": ad_local,
        "blog": ad_blog,
    }

    # 직전 분석 기록 조회
    prev_analysis = None
    analysis_count = 0
    prev_analyzed_at = None
    keyword_history = {}
    if place_id:
        prev_record = crud.get_previous_analysis(db, place_id, "place")
        if prev_record:
            prev_analysis = {
                "total_score": prev_record.total_score,
                "analyzed_at": prev_record.analyzed_at.isoformat() if prev_record.analyzed_at else None,
                "result_json": prev_record.result_json,
            }
            prev_analyzed_at = prev_record.analyzed_at.strftime("%m/%d") if prev_record.analyzed_at else None
        analysis_count = crud.get_analysis_count(db, place_id, "place")
        keyword_history = crud.get_keyword_rank_history(db, place_id, "place", limit=5)

    # 캐시 체크 (force_refresh가 아니고 캐시 있으면 complete만 바로 전송)
    if place_id and not force_refresh:
        cached = crud.get_cached_result(db, place_id)
        if cached:
            cached_place_id = cached.get("place_id") or place_id
            if cached_place_id:
                try:
                    crud.save_analysis_history(
                        db,
                        place_id=cached_place_id,
                        store_name=cached.get("store_name", store_name),
                        analysis_type="place",
                        total_score=cached.get("scores", {}).get("total"),
                        result_json=json_module.dumps(cached, ensure_ascii=False),
                        anon_id=anon_id,
                    )
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"히스토리 저장 실패(캐시): {e}")

            cached["cached"] = True
            cached["ad_flags"] = ad_flags
            cached["prev_analysis"] = prev_analysis
            cached["prev_analyzed_at"] = prev_analyzed_at
            cached["analysis_count"] = crud.get_analysis_count(db, cached_place_id, "place") if cached_place_id else analysis_count
            cached["keyword_history"] = crud.get_keyword_rank_history(db, cached_place_id, "place", limit=5) if cached_place_id else keyword_history
            apply_ad_flags(cached.get("scores", {}), ad_flags)

            async def cached_generator():
                # 캐시 히트 시에도 started → complete 흐름 유지
                yield f"event: started\ndata: {json_module.dumps({'total_keywords': len(cached.get('keywords_used', [])), 'store_name': store_name, 'place_id': cached_place_id, 'category': cached.get('category', ''), 'address': cached.get('address', ''), 'cached': True}, ensure_ascii=False)}\n\n"
                yield f"event: complete\ndata: {json_module.dumps(cached, ensure_ascii=False)}\n\n"

            return StreamingResponse(
                cached_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",  # nginx 버퍼링 끄기
                },
            )

    # 스레드 안전 큐로 실시간 이벤트 전달
    import queue
    event_queue = queue.Queue()

    async def run_stream_to_queue():
        """proactor 루프에서 실행, 이벤트를 큐에 넣음"""
        try:
            async for event in diagnose_store_stream(store_name, place_url, ad_flags=ad_flags):
                event_queue.put(event)
            event_queue.put(None)  # 종료 신호
        except Exception as e:
            import traceback
            event_queue.put({"type": "error", "message": str(e), "traceback": traceback.format_exc()})
            event_queue.put(None)

    # proactor 루프에서 스트리밍 시작 (비동기)
    asyncio.run_coroutine_threadsafe(run_stream_to_queue(), _proactor_loop)

    async def event_generator():
        try:
            while True:
                # 큐에서 이벤트 꺼내기 (blocking, 타임아웃 1초)
                try:
                    event = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: event_queue.get(timeout=1.0)
                    )
                except queue.Empty:
                    continue

                if event is None:  # 종료 신호
                    break

                event_type = event.get("type", "message")

                if event_type == "error":
                    yield f"event: error\ndata: {json_module.dumps(event, ensure_ascii=False)}\n\n"
                    break

                if event_type == "complete":
                    # 최종 결과에 히스토리 정보 추가
                    result = event.get("result", {})
                    result_place_id = result.get("place_id") or place_id

                    # 히스토리 저장
                    if result_place_id:
                        try:
                            crud.save_analysis_history(
                                db,
                                place_id=result_place_id,
                                store_name=result.get("store_name", store_name),
                                analysis_type="place",
                                total_score=result.get("scores", {}).get("total"),
                                result_json=json_module.dumps(result, ensure_ascii=False),
                                anon_id=anon_id,
                            )
                        except Exception as e:
                            import logging
                            logging.getLogger(__name__).warning(f"히스토리 저장 실패: {e}")

                        # 직전 기록 재조회
                        prev_record2 = crud.get_previous_analysis(db, result_place_id, "place")
                        if prev_record2:
                            result["prev_analysis"] = {
                                "total_score": prev_record2.total_score,
                                "analyzed_at": prev_record2.analyzed_at.isoformat() if prev_record2.analyzed_at else None,
                                "result_json": prev_record2.result_json,
                            }
                            result["prev_analyzed_at"] = prev_record2.analyzed_at.strftime("%m/%d") if prev_record2.analyzed_at else None
                        result["analysis_count"] = crud.get_analysis_count(db, result_place_id, "place")
                        result["keyword_history"] = crud.get_keyword_rank_history(db, result_place_id, "place", limit=5)

                    result["cached"] = False

                    # DB 저장
                    try:
                        crud.save_diagnosis(db, result, place_url)
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).warning(f"DB 저장 실패: {e}")

                    yield f"event: complete\ndata: {json_module.dumps(result, ensure_ascii=False)}\n\n"
                else:
                    yield f"event: {event_type}\ndata: {json_module.dumps(event, ensure_ascii=False)}\n\n"

        except Exception as e:
            import traceback
            error_data = {"type": "error", "message": str(e), "traceback": traceback.format_exc()}
            yield f"event: error\ndata: {json_module.dumps(error_data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx 버퍼링 끄기
        },
    )


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

    # 직전 분석 기록 조회 + J단계: 히스토리 추세
    prev_analysis = None
    analysis_count = 0
    prev_analyzed_at = None
    keyword_history = {}
    if place_id:
        prev_record = crud.get_previous_analysis(db, place_id, "place")
        if prev_record:
            prev_analysis = {
                "total_score": prev_record.total_score,
                "analyzed_at": prev_record.analyzed_at.isoformat() if prev_record.analyzed_at else None,
                "result_json": prev_record.result_json,
            }
            prev_analyzed_at = prev_record.analyzed_at.strftime("%m/%d") if prev_record.analyzed_at else None
        # 분석 횟수 (현재 분석 포함이므로 조회 시점에서는 +1 전)
        analysis_count = crud.get_analysis_count(db, place_id, "place")
        # 키워드별 과거 순위 기록
        keyword_history = crud.get_keyword_rank_history(db, place_id, "place", limit=5)

    if place_id and not req.force_refresh:
        cached = crud.get_cached_result(db, place_id)
        if cached:
            cached_place_id = cached.get("place_id") or place_id
            # 캐시 적중이어도 "분석 1회"로 히스토리에 누적한다.
            # (저장을 건너뛰면 24h 캐시 동안 재분석이 안 쌓여 항상 "첫 분석"으로 표시됨)
            if cached_place_id:
                try:
                    crud.save_analysis_history(
                        db,
                        place_id=cached_place_id,
                        store_name=cached.get("store_name", req.store_name),
                        analysis_type="place",
                        total_score=cached.get("scores", {}).get("total"),
                        result_json=json_module.dumps(cached, ensure_ascii=False),
                        anon_id=req.anon_id,
                    )
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"히스토리 저장 실패(캐시): {e}")
            cached["cached"] = True
            cached["ad_flags"] = ad_flags
            cached["prev_analysis"] = prev_analysis
            cached["prev_analyzed_at"] = prev_analyzed_at
            # 방금 저장분 포함해 재집계 (N번째 분석 / 키워드 추세)
            cached["analysis_count"] = crud.get_analysis_count(db, cached_place_id, "place") if cached_place_id else analysis_count
            cached["keyword_history"] = crud.get_keyword_rank_history(db, cached_place_id, "place", limit=5) if cached_place_id else keyword_history
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
        import traceback, logging, concurrent.futures
        logging.getLogger(__name__).error("분석 실패:\n" + traceback.format_exc())
        _is_to = isinstance(e, (TimeoutError, concurrent.futures.TimeoutError))
        raise HTTPException(status_code=500, detail=(
            "분석이 오래 걸려 시간이 초과됐어요. 잠시 후 다시 시도해주세요." if _is_to
            else "분석 중 오류가 발생했어요. 잠시 후 다시 시도해주세요."))

    try:
        crud.save_diagnosis(db, result, req.place_url)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"DB 저장 실패: {e}")

    # 히스토리 누적 + 직전기록 조회는 '해석된' place_id 기준으로 한다.
    # (_extract_place_id는 naver.me 단축URL에서 None이라, URL 기준으로만 조회하면
    #  단축URL 사용 시 직전기록/추세가 항상 비어 "첫 분석"으로만 보였음)
    result_place_id = result.get("place_id") or place_id

    # 저장 '전' 시점의 직전 기록(= 이번 분석 직전) — 해석된 place_id 기준으로 재조회
    if result_place_id:
        prev_record2 = crud.get_previous_analysis(db, result_place_id, "place")
        if prev_record2:
            prev_analysis = {
                "total_score": prev_record2.total_score,
                "analyzed_at": prev_record2.analyzed_at.isoformat() if prev_record2.analyzed_at else None,
                "result_json": prev_record2.result_json,
            }
            prev_analyzed_at = prev_record2.analyzed_at.strftime("%m/%d") if prev_record2.analyzed_at else None

    if result_place_id:
        try:
            crud.save_analysis_history(
                db,
                place_id=result_place_id,
                store_name=result.get("store_name", req.store_name),
                analysis_type="place",
                total_score=result.get("scores", {}).get("total"),
                result_json=json_module.dumps(result, ensure_ascii=False),
                anon_id=req.anon_id,  # K단계: 익명 ID 저장
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"히스토리 저장 실패: {e}")

    result["cached"] = False
    result["prev_analysis"] = prev_analysis
    # J단계: 저장 후 재집계 (방금 저장분 포함)
    if result_place_id:
        result["analysis_count"] = crud.get_analysis_count(db, result_place_id, "place")
        result["keyword_history"] = crud.get_keyword_rank_history(db, result_place_id, "place", limit=5)
    else:
        result["analysis_count"] = 1
        result["keyword_history"] = {}
    result["prev_analyzed_at"] = prev_analyzed_at
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
    #   - 브랜드+분리단어 제거 (플마 스타일)
    #   - 메뉴 키워드 우선 정렬
    import re as _re

    _brand_base = _re.sub(r"(본점|직영점|지점|점)$", "", req.store_name.strip()).strip()
    _brand_parts = [bp for bp in _re.split(r"\s+", _brand_base) if len(bp) >= 2]
    _brand_only = set([req.store_name.strip(), _brand_base] + _brand_parts)
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
        import traceback, logging, concurrent.futures
        logging.getLogger(__name__).error("분석 실패:\n" + traceback.format_exc())
        _is_to = isinstance(e, (TimeoutError, concurrent.futures.TimeoutError))
        raise HTTPException(status_code=500, detail=(
            "분석이 오래 걸려 시간이 초과됐어요. 잠시 후 다시 시도해주세요." if _is_to
            else "분석 중 오류가 발생했어요. 잠시 후 다시 시도해주세요."))

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
    from .core.keywords import generate_keywords

    place_id = _extract_place_id(req.place_url)
    keywords = []
    address = ""
    category = ""

    # 1. place_id가 regex로 잡히면 직전 place 분석 결과를 재사용해 크롤을 생략.
    #    (naver.me 단축링크는 regex로 안 잡혀 place_id=None → 아래 크롤에서 네비로 해석)
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
        # 2. place_id 미해석(naver.me 단축링크 등) 또는 주소 없음 → 매장 정보 크롤.
        #    get_store_details가 page.goto로 redirect를 따라가 place_id를 해석하므로
        #    store_info["place_id"]를 받아 채운다. (regex만으론 naver.me 해석 불가 = 라보떼 0건 원인)
        if not place_id or not address:
            from .core.scraper import fetch_store_info_only

            future = asyncio.run_coroutine_threadsafe(
                fetch_store_info_only(req.place_url),
                _proactor_loop,
            )
            store_info = await asyncio.get_running_loop().run_in_executor(
                None, future.result, 120
            )

            place_id = store_info.get("place_id") or place_id
            address = store_info.get("address", "") or address
            category = store_info.get("category", "") or category

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

        # 3. 네비게이션까지 했는데도 place_id 없음 → 정말 못 읽은 URL.
        #    (블로그 딥스캔은 place_id 포함 여부로 매칭 → place_id 필수)
        if not place_id:
            raise HTTPException(
                status_code=400,
                detail="URL에서 네이버 플레이스를 찾지 못했습니다. 네이버 지도에서 매장 상세 "
                       "페이지를 연 뒤 그 URL(또는 공유 링크)을 입력해 주세요.",
            )

        # 4. 블로그 키워드 정리 (플마 스타일): 브랜드+분리단어 제거
        _brand_base = re.sub(r"(본점|직영점|지점|점)$", "", req.store_name.strip()).strip()
        _brand_parts = [bp for bp in re.split(r"\s+", _brand_base) if len(bp) >= 2]
        _brand_only = set([req.store_name.strip(), _brand_base] + _brand_parts)
        keywords = [k for k in keywords if k and k not in _brand_only]

        # 5. 블로그 분석 (30개 키워드 — 폭 확보가 핵심)
        future2 = asyncio.run_coroutine_threadsafe(
            analyze_blog_ranking(
                store_name=req.store_name,
                place_id=place_id,
                address=address,
                keywords=keywords[:15],
                max_keywords=15,
            ),
            _proactor_loop,
        )
        blog_results = await asyncio.get_running_loop().run_in_executor(
            None, future2.result, 300
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback, logging, concurrent.futures
        logging.getLogger(__name__).error("분석 실패:\n" + traceback.format_exc())
        _is_to = isinstance(e, (TimeoutError, concurrent.futures.TimeoutError))
        raise HTTPException(status_code=500, detail=(
            "분석이 오래 걸려 시간이 초과됐어요. 잠시 후 다시 시도해주세요." if _is_to
            else "분석 중 오류가 발생했어요. 잠시 후 다시 시도해주세요."))

    # 직전 블로그 분석 기록 조회 (place_id 확정 후 — naver.me도 이 시점엔 해석됨)
    prev_analysis = None
    prev_analyzed_at = None
    prev_record = crud.get_previous_analysis(db, place_id, "blog")
    if prev_record:
        prev_analysis = {
            "total_score": prev_record.total_score,
            "analyzed_at": prev_record.analyzed_at.isoformat() if prev_record.analyzed_at else None,
            "result_json": prev_record.result_json,
        }
        prev_analyzed_at = prev_record.analyzed_at.strftime("%m/%d") if prev_record.analyzed_at else None

    # J단계: 분석 횟수 + 키워드별 과거 순위 (저장 전이므로 현재 분석 미포함)
    analysis_count_before = crud.get_analysis_count(db, place_id, "blog")
    keyword_history = crud.get_keyword_rank_history(db, place_id, "blog", limit=5)

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
        "prev_analyzed_at": prev_analyzed_at,
        "keywords_used": keywords[:15],
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
                anon_id=req.anon_id,  # K단계: 익명 ID 저장
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"블로그 히스토리 저장 실패: {e}")

    # J단계: 저장 후 최종 분석 횟수 (방금 저장한 것 포함)
    result["analysis_count"] = analysis_count_before + 1
    result["keyword_history"] = keyword_history

    return result


@app.get("/analyze-blog-stream", tags=["진단"])
async def analyze_blog_stream_endpoint(
    store_name: str,
    place_url: str,
    anon_id: str = None,
    db: Session = Depends(get_db),
):
    """
    블로그 단독 분석 SSE 스트리밍 버전.
    - started: 분석 시작 즉시 (504/타임아웃 방지)
    - blog_keyword: 키워드별 검출 결과 하나씩 (실시간 팝업용)
    - complete: 최종 결과
    """
    import json as json_module
    from .core.scraper import analyze_blog_stream

    place_id = _extract_place_id(place_url)
    keywords = []
    address = ""
    category = ""

    # 기존 place 분석이 있으면 키워드/주소 재사용 (크롤 생략 → 빠르고 차단↓)
    if place_id:
        prev_place = crud.get_previous_analysis(db, place_id, "place")
        if prev_place and prev_place.result_json:
            try:
                pd = json_module.loads(prev_place.result_json)
                keywords = pd.get("keywords_used", [])
                address = pd.get("address", "")
                category = pd.get("category", "")
            except Exception:
                pass

    import queue
    event_queue = queue.Queue()

    async def run_stream_to_queue():
        try:
            async for ev in analyze_blog_stream(
                store_name, place_url, place_id=place_id or "",
                keywords=keywords, address=address, category=category,
            ):
                event_queue.put(ev)
            event_queue.put(None)
        except Exception:
            import traceback, logging
            logging.getLogger(__name__).error("블로그 스트림 실패:\n" + traceback.format_exc())
            event_queue.put({"type": "error", "message": "분석 중 오류가 발생했어요. 잠시 후 다시 시도해주세요."})
            event_queue.put(None)

    asyncio.run_coroutine_threadsafe(run_stream_to_queue(), _proactor_loop)

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: event_queue.get(timeout=1.0)
                    )
                except queue.Empty:
                    continue
                if event is None:
                    break

                et = event.get("type", "message")
                if et == "error":
                    yield f"event: error\ndata: {json_module.dumps(event, ensure_ascii=False)}\n\n"
                    break

                if et == "complete":
                    result = event.get("result", {})
                    rpid = result.get("place_id") or place_id

                    if rpid:
                        # 직전 블로그 기록 + 분석 횟수 (저장 전)
                        prev_record = crud.get_previous_analysis(db, rpid, "blog")
                        if prev_record:
                            result["prev_analysis"] = {
                                "total_score": prev_record.total_score,
                                "analyzed_at": prev_record.analyzed_at.isoformat() if prev_record.analyzed_at else None,
                                "result_json": prev_record.result_json,
                            }
                            result["prev_analyzed_at"] = prev_record.analyzed_at.strftime("%m/%d") if prev_record.analyzed_at else None
                        analysis_count_before = crud.get_analysis_count(db, rpid, "blog")
                        result["keyword_history"] = crud.get_keyword_rank_history(db, rpid, "blog", limit=5)

                        try:
                            crud.save_analysis_history(
                                db, place_id=rpid, store_name=result.get("store_name", store_name),
                                analysis_type="blog", total_score=None,
                                result_json=json_module.dumps(result, ensure_ascii=False),
                                anon_id=anon_id,
                            )
                        except Exception as e:
                            import logging
                            logging.getLogger(__name__).warning(f"블로그 히스토리 저장 실패: {e}")

                        result["analysis_count"] = analysis_count_before + 1

                    yield f"event: complete\ndata: {json_module.dumps(result, ensure_ascii=False)}\n\n"
                else:
                    yield f"event: {et}\ndata: {json_module.dumps(event, ensure_ascii=False)}\n\n"
        except Exception:
            import traceback, logging
            logging.getLogger(__name__).error("블로그 스트림 전송 실패:\n" + traceback.format_exc())
            yield f"event: error\ndata: {json_module.dumps({'type':'error','message':'분석 중 오류가 발생했어요. 잠시 후 다시 시도해주세요.'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/lead", response_model=schemas.LeadResponse, tags=["리드"])
def create_lead(req: schemas.LeadRequest, db: Session = Depends(get_db)):
    """연락처(리드)를 저장합니다."""
    lead = crud.create_lead(db, contact=req.contact, source=req.source, store_id=req.store_id)
    return lead


# ── K단계: 최근 본 매장 API ───────────────────────────────────────────────────
@app.get("/recent-stores/{anon_id}", tags=["K단계"])
def get_recent_stores(anon_id: str, db: Session = Depends(get_db)):
    """익명 사용자의 최근 본 매장 목록을 반환합니다."""
    stores = crud.get_recent_stores_by_anon_id(db, anon_id, limit=10)
    return {"stores": stores}


@app.get("/history-result/{place_id}", tags=["K단계"])
def get_history_result(place_id: str, analysis_type: str = "place", db: Session = Depends(get_db)):
    """저장된 최신 분석 결과를 반환합니다 (재크롤링 없이 즉시 표시용)."""
    result = crud.get_latest_analysis_result(db, place_id, analysis_type)
    if not result:
        raise HTTPException(status_code=404, detail="저장된 분석 결과가 없습니다")
    return result


@app.get("/history-result-all/{place_id}", tags=["K단계"])
def get_history_result_all(place_id: str, db: Session = Depends(get_db)):
    """place와 blog 저장된 결과를 모두 반환합니다."""
    place_result = crud.get_latest_analysis_result(db, place_id, "place")
    blog_result = crud.get_latest_analysis_result(db, place_id, "blog")

    if not place_result and not blog_result:
        raise HTTPException(status_code=404, detail="저장된 분석 결과가 없습니다")

    return {
        "place": place_result,
        "blog": blog_result,
    }


# ─────────────────────────────────────────────────────────────────────────────
# M단계: 내 매장 / 경쟁 매장 등록
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/register-store", tags=["M단계"])
def register_store_endpoint(
    anon_id: str,
    place_id: str,
    store_name: str,
    store_type: str,
    db: Session = Depends(get_db),
):
    """매장을 내 매장(my) 또는 경쟁 매장(rival)으로 등록"""
    if store_type not in ("my", "rival"):
        raise HTTPException(status_code=400, detail="store_type은 'my' 또는 'rival'이어야 합니다")
    result = crud.register_store(db, anon_id, place_id, store_name, store_type)
    return {"success": True, "id": result.id if result else None}


@app.delete("/unregister-store", tags=["M단계"])
def unregister_store_endpoint(
    anon_id: str,
    place_id: str,
    store_type: str,
    db: Session = Depends(get_db),
):
    """매장 등록 해제"""
    success = crud.unregister_store(db, anon_id, place_id, store_type)
    return {"success": success}


@app.get("/registered-stores/{anon_id}", tags=["M단계"])
def get_registered_stores_endpoint(anon_id: str, db: Session = Depends(get_db)):
    """내 매장 / 경쟁 매장 목록 조회"""
    return crud.get_registered_stores(db, anon_id)


@app.get("/store-registration-status/{anon_id}/{place_id}", tags=["M단계"])
def get_store_registration_status_endpoint(
    anon_id: str,
    place_id: str,
    db: Session = Depends(get_db),
):
    """특정 매장의 등록 상태 조회 (내 매장/경쟁 매장 여부)"""
    return crud.get_store_registration_status(db, anon_id, place_id)


# ─────────────────────────────────────────────────────────────────────────────
# 알림톡 구독 API
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/subscribe", tags=["구독"])
async def subscribe_alarm_endpoint(
    req: schemas.SubscribeRequest,
    db: Session = Depends(get_db),
):
    """알림톡 구독 신청"""
    if not req.agreed:
        raise HTTPException(status_code=400, detail="수신 동의가 필요합니다")
    if not req.phone or len(req.phone) < 10:
        raise HTTPException(status_code=400, detail="올바른 전화번호를 입력해주세요")

    sub = crud.subscribe_alarm(
        db,
        store_name=req.store_name,
        phone=req.phone,
        store_url=req.store_url,
        place_id=req.place_id,
        anon_id=req.anon_id,
    )

    # 신청 완료 알림톡 발송 (실패해도 구독 저장은 성공 처리)
    try:
        from .services.alimtalk import send_signup_alimtalk
        await send_signup_alimtalk(
            phone=req.phone,
            store_name=req.store_name,
            day_of_week="월요일",
        )
    except Exception as e:
        logging.getLogger(__name__).warning(f"[알림톡] 신청완료 발송 실패: {e}")

    return {
        "id": sub.id,
        "store_name": sub.store_name,
        "phone": sub.phone[:3] + "****" + sub.phone[-4:] if len(sub.phone) >= 7 else "****",
        "alarm_on": sub.alarm_on,
        "message": "알림 신청이 완료되었습니다",
    }


@app.post("/unsubscribe/{subscriber_id}", tags=["구독"])
def unsubscribe_alarm_endpoint(subscriber_id: int, db: Session = Depends(get_db)):
    """알림톡 해지"""
    if crud.unsubscribe_alarm(db, subscriber_id):
        return {"message": "해지되었습니다"}
    raise HTTPException(status_code=404, detail="구독 정보를 찾을 수 없습니다")


# ─────────────────────────────────────────────────────────────────────────────
# 관리자 인증 (환경변수 기반 단순 인증)
# ─────────────────────────────────────────────────────────────────────────────
import os
import secrets
from fastapi import Response, Cookie
from typing import Optional as Opt

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "placeranking2026")
_admin_sessions: dict[str, bool] = {}


def _check_admin(session_id: Opt[str]) -> bool:
    return session_id is not None and _admin_sessions.get(session_id, False)


@app.post("/admin/login", tags=["관리자"])
def admin_login(
    req: schemas.AdminLoginRequest,
    response: Response,
):
    """관리자 로그인"""
    if req.username == ADMIN_USER and req.password == ADMIN_PASS:
        session_id = secrets.token_hex(16)
        _admin_sessions[session_id] = True
        response.set_cookie(
            key="admin_session",
            value=session_id,
            httponly=True,
            max_age=86400,
            samesite="lax",
        )
        return {"success": True}
    raise HTTPException(status_code=401, detail="인증 실패")


@app.post("/admin/logout", tags=["관리자"])
def admin_logout(
    response: Response,
    admin_session: Opt[str] = Cookie(None),
):
    """관리자 로그아웃"""
    if admin_session and admin_session in _admin_sessions:
        del _admin_sessions[admin_session]
    response.delete_cookie("admin_session")
    return {"success": True}


@app.get("/admin/check", tags=["관리자"])
def admin_check(admin_session: Opt[str] = Cookie(None)):
    """로그인 상태 확인"""
    return {"logged_in": _check_admin(admin_session)}


# ─────────────────────────────────────────────────────────────────────────────
# 관리자 API (인증 필요)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/admin/api/stats", tags=["관리자"])
def admin_stats(
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """대시보드 통계"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    return crud.get_admin_stats(db)


@app.get("/admin/api/recent-analyses", tags=["관리자"])
def admin_recent_analyses(
    limit: int = 10,
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """최근 분석 목록"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    return crud.get_recent_analyses(db, limit)


@app.get("/admin/api/subscribers", tags=["관리자"])
def admin_subscribers(
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """구독자 목록 (전화번호 포함 - 관리자 전용)"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    subs = crud.get_all_subscribers(db)
    return [
        {
            "id": s.id,
            "store_name": s.store_name,
            "phone": s.phone,
            "place_id": s.place_id,
            "alarm_on": s.alarm_on,
            "created_at": s.created_at.strftime("%m-%d") if s.created_at else None,
            "last_analyzed_at": s.last_analyzed_at.strftime("%m-%d") if s.last_analyzed_at else None,
        }
        for s in subs
    ]


@app.get("/admin/api/subscribers/csv", tags=["관리자"])
def admin_subscribers_csv(
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """구독자 CSV 다운로드"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    subs = crud.get_all_subscribers(db)

    import io
    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["매장명", "연락처", "신청일", "최근진단", "알림상태"])
    for s in subs:
        writer.writerow([
            s.store_name,
            s.phone,
            s.created_at.strftime("%Y-%m-%d") if s.created_at else "",
            s.last_analyzed_at.strftime("%Y-%m-%d") if s.last_analyzed_at else "",
            "수신중" if s.alarm_on else "해지",
        ])

    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=subscribers.csv"},
    )


@app.get("/admin/api/monitored-stores", tags=["관리자"])
def admin_monitored_stores(
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """모니터링 매장 목록"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    return crud.get_monitored_stores(db)


@app.get("/admin/api/alim-templates", tags=["관리자"])
def admin_alim_templates(
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """알림톡 템플릿 목록"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    templates = crud.get_all_alim_templates(db)
    return [
        {
            "template_key": t.template_key,
            "extra_text": t.extra_text,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in templates
    ]


@app.post("/admin/api/alim-templates", tags=["관리자"])
def admin_update_alim_template(
    req: schemas.AlimTemplateUpdate,
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """알림톡 추가문구 저장"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    tpl = crud.upsert_alim_template(db, req.template_key, req.extra_text)
    return {"success": True, "template_key": tpl.template_key}


@app.post("/admin/api/subscriber/{subscriber_id}/toggle", tags=["관리자"])
def admin_toggle_subscriber(
    subscriber_id: int,
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """구독자 알림 상태 토글"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    sub = db.query(crud.models.Subscriber).filter(crud.models.Subscriber.id == subscriber_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="구독자를 찾을 수 없습니다")
    if sub.alarm_on:
        crud.unsubscribe_alarm(db, subscriber_id)
    else:
        crud.resubscribe_alarm(db, subscriber_id)
    return {"success": True, "alarm_on": not sub.alarm_on}


# ─────────────────────────────────────────────────────────────────────────────
# 관리자 페이지 HTML
# ─────────────────────────────────────────────────────────────────────────────

_ADMIN_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>플레이스랭킹 관리자</title>
<script src="https://cdn.jsdelivr.net/npm/lucide@0.294.0/dist/umd/lucide.min.js"></script>
<style>
  .adm-icon{width:16px;height:16px;stroke-width:2;vertical-align:-3px;margin-right:6px;}
  :root{
    --green:#00C896; --green-d:#00B085; --green-soft:#E6F7F2;
    --ink:#1A2B3C; --sub:#6B7C8F; --line:#E8EDF1; --bg:#F6F8FA; --white:#fff;
    --amber:#F0A500; --red:#E06A6A;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;
    background:var(--bg);color:var(--ink);display:flex;min-height:100vh;font-size:14px;line-height:1.5}

  /* login */
  .login-wrap{display:flex;align-items:center;justify-content:center;width:100%;min-height:100vh;background:var(--bg)}
  .login-box{background:#fff;border:1px solid var(--line);border-radius:16px;padding:36px 32px;width:100%;max-width:360px;text-align:center}
  .login-box h1{font-size:20px;font-weight:800;margin-bottom:8px}
  .login-box p{color:var(--sub);font-size:13px;margin-bottom:24px}
  .login-box input{width:100%;padding:12px 14px;border:1px solid var(--line);border-radius:8px;font-size:14px;margin-bottom:12px}
  .login-box input:focus{outline:none;border-color:var(--green)}
  .login-box .btn{width:100%;padding:12px;background:var(--green);color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:700;cursor:pointer}
  .login-box .btn:hover{background:var(--green-d)}
  .login-box .err{color:var(--red);font-size:12px;margin-top:12px;display:none}
  .remember-me{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--sub);margin-bottom:14px;cursor:pointer}
  .remember-me input{accent-color:var(--green)}

  /* sidebar */
  .side{width:220px;background:var(--white);border-right:1px solid var(--line);
    display:flex;flex-direction:column;padding:22px 0;flex-shrink:0}
  .brand{padding:0 24px 22px;font-weight:800;font-size:18px;letter-spacing:-.5px}
  .brand span{color:var(--green)}
  .nav button{display:flex;align-items:center;gap:11px;width:100%;border:0;background:none;
    padding:12px 24px;font-size:14.5px;color:var(--sub);cursor:pointer;text-align:left;font-weight:500;
    border-left:3px solid transparent;transition:.15s}
  .nav button:hover{background:#FAFCFD;color:var(--ink)}
  .nav button.on{color:var(--green-d);background:var(--green-soft);border-left-color:var(--green);font-weight:700}
  .nav .ico{font-size:16px;width:18px;text-align:center}
  .side-foot{margin-top:auto;padding:18px 24px 0;border-top:1px solid var(--line);color:var(--sub);font-size:12.5px}
  .side-foot a{color:var(--sub);cursor:pointer;text-decoration:underline}

  /* main */
  .app{display:none;flex:1}
  .main{flex:1;padding:30px 38px;overflow:auto}
  .head{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:24px}
  .head h1{font-size:22px;font-weight:800;letter-spacing:-.6px}
  .head p{color:var(--sub);font-size:13px;margin-top:4px}
  .btn{border:0;background:var(--green);color:#fff;padding:9px 16px;border-radius:8px;
    font-size:13px;font-weight:700;cursor:pointer}
  .btn.ghost{background:#fff;color:var(--ink);border:1px solid var(--line)}

  .page{display:none}
  .page.on{display:block}

  /* cards */
  .cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:26px}
  .card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 20px}
  .card .lbl{color:var(--sub);font-size:12.5px;font-weight:600}
  .card .num{font-size:28px;font-weight:800;margin-top:8px;letter-spacing:-1px}
  .card .num small{font-size:13px;color:var(--green);font-weight:700;margin-left:6px}
  .card.hl{background:var(--green-soft);border-color:#BfeBe0}

  .panel{background:#fff;border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin-bottom:20px}
  .panel h2{font-size:15px;font-weight:800;margin-bottom:4px}
  .panel .desc{color:var(--sub);font-size:12.5px;margin-bottom:16px}

  table{width:100%;border-collapse:collapse}
  th{text-align:left;font-size:12px;color:var(--sub);font-weight:700;padding:10px 12px;border-bottom:1px solid var(--line)}
  td{padding:13px 12px;border-bottom:1px solid #F1F4F7;font-size:13.5px}
  tr:last-child td{border-bottom:0}
  .tag{display:inline-block;padding:3px 9px;border-radius:20px;font-size:11.5px;font-weight:700}
  .tag.on{background:var(--green-soft);color:var(--green-d)}
  .tag.off{background:#F1F4F7;color:var(--sub)}
  .rank{font-weight:800}
  .up{color:var(--green)} .down{color:var(--red)} .same{color:var(--sub)}

  /* 알림톡 */
  .tpl{border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:14px}
  .tpl .top{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
  .tpl .name{font-weight:800;font-size:13.5px}
  .locked{background:#F6F8FA;border:1px dashed #D5DEE5;border-radius:9px;padding:13px 15px;
    font-size:13px;color:#42566A;white-space:pre-line;line-height:1.65}
  .locked b{color:var(--green-d)}
  .lockhint{font-size:11.5px;color:var(--sub);margin:7px 2px 12px}
  .addlbl{font-size:12.5px;font-weight:700;margin:6px 2px}
  textarea{width:100%;border:1px solid var(--line);border-radius:9px;padding:11px 13px;
    font-family:inherit;font-size:13px;resize:vertical;min-height:74px;color:var(--ink)}
  .savebar{display:flex;justify-content:flex-end;gap:8px;margin-top:10px}
  .saved-msg{color:var(--green-d);font-size:12px;margin-right:auto;display:none}

  @media(max-width:760px){
    body{flex-direction:column}
    .side{width:100%;flex-direction:row;flex-wrap:wrap;padding:12px;align-items:center}
    .brand{padding:0 12px 0 8px}
    .nav{display:flex;flex:1;overflow:auto}
    .nav button{padding:9px 12px;border-left:0;border-bottom:3px solid transparent;white-space:nowrap}
    .nav button.on{border-left:0;border-bottom-color:var(--green)}
    .side-foot{display:none}
    .main{padding:20px}
    .cards{grid-template-columns:1fr 1fr}
  }
</style>
</head>
<body>

<!-- 로그인 -->
<div class="login-wrap" id="loginWrap">
  <div class="login-box">
    <h1>플레이스랭킹 관리자</h1>
    <p>관리자 계정으로 로그인하세요</p>
    <input type="text" id="loginUser" placeholder="아이디">
    <input type="password" id="loginPass" placeholder="비밀번호">
    <label class="remember-me"><input type="checkbox" id="rememberMe" checked> 로그인 유지</label>
    <button class="btn" onclick="doLogin()">로그인</button>
    <div class="err" id="loginErr">아이디 또는 비밀번호가 올바르지 않습니다</div>
  </div>
</div>

<!-- 앱 -->
<div class="app" id="appWrap">
  <aside class="side">
    <div class="brand">플레이스<span>랭킹</span> <span style="color:var(--sub);font-weight:600;font-size:12px">admin</span></div>
    <nav class="nav">
      <button class="on" data-p="dash"><i data-lucide="layout-dashboard" class="adm-icon"></i> 대시보드</button>
      <button data-p="lead"><i data-lucide="users" class="adm-icon"></i> 회원·리드</button>
      <button data-p="store"><i data-lucide="store" class="adm-icon"></i> 매장 모니터링</button>
      <button data-p="alim"><i data-lucide="message-square" class="adm-icon"></i> 알림톡 관리</button>
    </nav>
    <div class="side-foot"><a onclick="doLogout()">로그아웃</a></div>
  </aside>

  <main class="main">

    <!-- 대시보드 -->
    <section class="page on" id="dash">
      <div class="head"><div><h1>대시보드</h1><p>오늘 기준 한눈에 보기</p></div></div>
      <div class="cards">
        <div class="card"><div class="lbl">총 진단 횟수</div><div class="num" id="statTotal">-</div></div>
        <div class="card"><div class="lbl">등록 매장</div><div class="num" id="statStores">-</div></div>
        <div class="card hl"><div class="lbl">알림 신청자 (리드)</div><div class="num" id="statSubs">-</div></div>
        <div class="card"><div class="lbl">이번주 신규 진단</div><div class="num" id="statWeek">-</div></div>
      </div>
      <div class="panel">
        <h2>최근 진단</h2><p class="desc">사장님들이 방금 진단한 매장들</p>
        <table>
          <thead><tr><th>매장명</th><th>대표 키워드</th><th>플레이스 지수</th><th>시각</th></tr></thead>
          <tbody id="recentTable"></tbody>
        </table>
      </div>
    </section>

    <!-- 회원·리드 -->
    <section class="page" id="lead">
      <div class="head">
        <div><h1>회원·리드</h1><p>알림 신청 시 입력한 연락처</p></div>
        <button class="btn" onclick="downloadCsv()">엑셀 내려받기</button>
      </div>
      <div class="panel">
        <h2>알림 신청자 <span id="subCount">0</span>명</h2><p class="desc">전화번호를 남긴 사장님 목록</p>
        <table>
          <thead><tr><th>매장명</th><th>연락처</th><th>신청일</th><th>최근 진단</th><th>알림</th></tr></thead>
          <tbody id="subTable"></tbody>
        </table>
      </div>
    </section>

    <!-- 매장 모니터링 -->
    <section class="page" id="store">
      <div class="head"><div><h1>매장 모니터링</h1><p>"내 매장"으로 등록돼 추적 중인 매장들</p></div></div>
      <div class="panel">
        <h2>모니터링 매장 <span id="monitorCount">0</span>곳</h2><p class="desc">대표 키워드 기준 최신 순위</p>
        <table>
          <thead><tr><th>매장명</th><th>대표 키워드</th><th>지난주</th><th>이번주</th><th>변화</th></tr></thead>
          <tbody id="monitorTable"></tbody>
        </table>
      </div>
    </section>

    <!-- 알림톡 관리 -->
    <section class="page" id="alim">
      <div class="head"><div><h1>알림톡 관리</h1><p>승인 골격은 고정 · 아래 추가문구만 자유롭게 수정</p></div></div>

      <div class="panel">
        <h2>① 알림 신청 완료</h2><p class="desc">사장님이 알림을 신청하면 자동 발송</p>
        <div class="locked">[플레이스랭킹] 순위 알림 신청 완료

<b>#{매장명}</b>님, 순위 모니터링을 시작했어요.
이제 매주 <b>#{요일}</b>에 플레이스 키워드 순위 변화를 정리해 보내드릴게요.</div>
        <div class="lockhint"><i data-lucide="lock" class="adm-icon"></i> 위 골격은 카카오 승인 영역 (수정 불가). #{ } 자리는 발송 때 자동으로 채워짐.</div>
        <div class="addlbl">추가문구 (자유 수정)</div>
        <textarea id="tplSignup"></textarea>
        <div class="savebar"><span class="saved-msg" id="savedSignup">저장됨</span><button class="btn" onclick="saveTemplate('signup')">저장</button></div>
      </div>

      <div class="panel">
        <h2>② 주간 순위 리포트</h2><p class="desc">매주 정해진 요일 자동 발송 (메인)</p>
        <div class="locked">[플레이스랭킹] <b>#{매장명}</b> 이번주 순위 리포트

대표 키워드 '<b>#{키워드}</b>'
지난주 <b>#{지난순위}</b>위 → 이번주 <b>#{이번순위}</b>위

전체 키워드와 경쟁 매장 변화는 아래에서 확인하세요.</div>
        <div class="lockhint"><i data-lucide="lock" class="adm-icon"></i> 위 골격은 카카오 승인 영역 (수정 불가).</div>
        <div class="addlbl">추가문구 (자유 수정)</div>
        <textarea id="tplWeekly"></textarea>
        <div class="savebar"><span class="saved-msg" id="savedWeekly">저장됨</span><button class="btn" onclick="saveTemplate('weekly')">저장</button></div>
      </div>

      <div class="panel">
        <h2>발송 이력</h2><p class="desc">최근 보낸 알림톡 (연동 후 표시됨)</p>
        <table>
          <thead><tr><th>일시</th><th>템플릿</th><th>대상</th><th>상태</th></tr></thead>
          <tbody id="sendHistory"><tr><td colspan="4" style="color:var(--sub);text-align:center;padding:24px">알림톡 발송 연동 후 이력이 표시됩니다</td></tr></tbody>
        </table>
      </div>
    </section>

  </main>
</div>

<script>
lucide.createIcons();
const btns=document.querySelectorAll('.nav button');
const pages=document.querySelectorAll('.page');
btns.forEach(b=>b.addEventListener('click',()=>{
  btns.forEach(x=>x.classList.remove('on'));
  pages.forEach(p=>p.classList.remove('on'));
  b.classList.add('on');
  document.getElementById(b.dataset.p).classList.add('on');
}));

async function checkAuth(){
  const r=await fetch('/admin/check');
  const d=await r.json();
  if(d.logged_in){
    document.getElementById('loginWrap').style.display='none';
    document.getElementById('appWrap').style.display='flex';
    loadAll();
  }else{
    const saved=localStorage.getItem('admin_cred');
    if(saved){
      try{
        const cred=JSON.parse(saved);
        const ar=await fetch('/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cred)});
        if(ar.ok){
          document.getElementById('loginWrap').style.display='none';
          document.getElementById('appWrap').style.display='flex';
          loadAll();
          return;
        }
      }catch(e){}
    }
    document.getElementById('loginWrap').style.display='flex';
    document.getElementById('appWrap').style.display='none';
  }
}

async function doLogin(){
  const u=document.getElementById('loginUser').value;
  const p=document.getElementById('loginPass').value;
  const remember=document.getElementById('rememberMe').checked;
  const r=await fetch('/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})});
  if(r.ok){
    if(remember){localStorage.setItem('admin_cred',JSON.stringify({username:u,password:p}));}
    checkAuth();
  }else{
    document.getElementById('loginErr').style.display='block';
  }
}

async function doLogout(){
  localStorage.removeItem('admin_cred');
  await fetch('/admin/logout',{method:'POST'});
  checkAuth();
}

async function loadAll(){
  loadStats();
  loadRecent();
  loadSubs();
  loadMonitor();
  loadTemplates();
}

async function loadStats(){
  const r=await fetch('/admin/api/stats');
  const d=await r.json();
  document.getElementById('statTotal').textContent=d.total_analyses.toLocaleString();
  document.getElementById('statStores').textContent=d.registered_stores.toLocaleString();
  document.getElementById('statSubs').innerHTML=d.subscriber_count+'<small>+'+d.new_subscribers_week+' 이번주</small>';
  document.getElementById('statWeek').textContent=d.new_analyses_week.toLocaleString();
}

async function loadRecent(){
  const r=await fetch('/admin/api/recent-analyses?limit=10');
  const d=await r.json();
  let html='';
  d.forEach(x=>{
    const t=x.analyzed_at?new Date(x.analyzed_at).toLocaleTimeString('ko',{hour:'2-digit',minute:'2-digit'}):'';
    html+=`<tr><td>${x.store_name}</td><td>${x.top_keyword||'-'}</td><td><b>${x.total_score?Math.round(x.total_score):'-'}</b></td><td>${t}</td></tr>`;
  });
  document.getElementById('recentTable').innerHTML=html||'<tr><td colspan="4" style="color:var(--sub);text-align:center">진단 기록이 없습니다</td></tr>';
}

async function loadSubs(){
  const r=await fetch('/admin/api/subscribers');
  const d=await r.json();
  document.getElementById('subCount').textContent=d.length;
  let html='';
  d.forEach(x=>{
    const tag=x.alarm_on?'<span class="tag on">수신중</span>':'<span class="tag off">해지</span>';
    html+=`<tr><td>${x.store_name}</td><td>${x.phone}</td><td>${x.created_at||'-'}</td><td>${x.last_analyzed_at||'-'}</td><td>${tag}</td></tr>`;
  });
  document.getElementById('subTable').innerHTML=html||'<tr><td colspan="5" style="color:var(--sub);text-align:center">알림 신청자가 없습니다</td></tr>';
}

function downloadCsv(){
  window.location.href='/admin/api/subscribers/csv';
}

async function loadMonitor(){
  const r=await fetch('/admin/api/monitored-stores');
  const d=await r.json();
  document.getElementById('monitorCount').textContent=d.length;
  let html='';
  d.forEach(x=>{
    let change='<span class="same">- 유지</span>';
    if(x.last_rank&&x.this_rank){
      const diff=x.last_rank-x.this_rank;
      if(diff>0)change=`<span class="up">▲ ${diff}</span>`;
      else if(diff<0)change=`<span class="down">▼ ${Math.abs(diff)}</span>`;
    }
    html+=`<tr><td>${x.store_name}</td><td>${x.top_keyword||'-'}</td><td>${x.last_rank?x.last_rank+'위':'-'}</td><td class="rank">${x.this_rank?x.this_rank+'위':'-'}</td><td>${change}</td></tr>`;
  });
  document.getElementById('monitorTable').innerHTML=html||'<tr><td colspan="5" style="color:var(--sub);text-align:center">모니터링 매장이 없습니다</td></tr>';
}

let templates={};
async function loadTemplates(){
  const r=await fetch('/admin/api/alim-templates');
  const d=await r.json();
  d.forEach(t=>{templates[t.template_key]=t.extra_text||'';});
  document.getElementById('tplSignup').value=templates['signup']||'지난 진단 결과가 궁금하면 언제든 다시 확인하실 수 있어요.';
  document.getElementById('tplWeekly').value=templates['weekly']||'이번주 순위, 한 번 확인해보세요 👀  새로 뜬 키워드가 있을 수 있어요.';
}

async function saveTemplate(key){
  const txt=document.getElementById(key==='signup'?'tplSignup':'tplWeekly').value;
  await fetch('/admin/api/alim-templates',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({template_key:key,extra_text:txt})});
  const msg=document.getElementById(key==='signup'?'savedSignup':'savedWeekly');
  msg.style.display='inline';
  setTimeout(()=>msg.style.display='none',2000);
}

document.getElementById('loginPass').addEventListener('keypress',e=>{if(e.key==='Enter')doLogin();});
checkAuth();
</script>
</body>
</html>"""


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page():
    """관리자 페이지"""
    return _ADMIN_HTML
