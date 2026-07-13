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
from .core.scraper import diagnose_store, diagnose_store_stream, analyze_blog_ranking, fetch_top_places
from .core.scoring import apply_ad_flags

# 공유용 OG 카드 생성 (Pillow). 미설치·폰트 문제로 사이트 전체가 죽지 않도록 방어 임포트.
try:
    from .services import og_image as _og
except Exception as _e:  # noqa: BLE001
    _og = None

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
<script src="https://t1.kakaocdn.net/kakao_js_sdk/2.7.2/kakao.min.js"></script>
<script>try{ if(window.Kakao && !Kakao.isInitialized()) Kakao.init('feef26a73850e916e03403ad1b9398e9'); }catch(e){}</script>
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
/* 검색 입력 + 버튼 */
.search-input-wrap{display:flex;gap:8px;}
.search-input-wrap input{flex:1;}
.search-btn{flex-shrink:0;width:48px;height:48px;border:none;border-radius:var(--radius-sm);background:var(--green);color:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .2s ease;}
.search-btn:hover{background:#00a347;transform:scale(1.02);}
.search-btn:active{transform:scale(0.98);}
.search-btn i,.search-btn svg{width:22px;height:22px;stroke-width:2.5;}
.url-toggle-wrap{text-align:right;margin-top:-8px;margin-bottom:8px;}
.url-toggle-btn{background:none;border:none;color:var(--gray-400);font-size:.8rem;cursor:pointer;padding:4px 0;text-decoration:underline;}
.url-toggle-btn:hover{color:var(--gray-600);}
.url-fallback-btn{margin-top:10px;padding:8px 16px;background:var(--green);color:#fff;border:none;border-radius:var(--radius-sm);font-size:.85rem;cursor:pointer;transition:all .2s ease;}
.url-fallback-btn:hover{background:#00a347;}
/* 자동완성 드롭다운 */
.autocomplete-wrap{position:relative;}
.autocomplete-dropdown{position:absolute;top:100%;left:0;right:0;background:#fff;border:1px solid var(--gray-200);border-radius:var(--radius-sm);box-shadow:0 8px 32px rgba(0,0,0,.12);z-index:100;max-height:440px;overflow-y:auto;display:none;margin-top:4px;}
.autocomplete-url-footer{padding:14px 16px;text-align:center;font-size:.85rem;color:var(--gray-500);cursor:pointer;border-top:1px solid var(--gray-100);position:sticky;bottom:0;background:#fff;}
.autocomplete-url-footer b{color:var(--green);text-decoration:underline;}
.autocomplete-url-footer:hover{background:var(--gray-50);}
.autocomplete-dropdown.show{display:block;}
.autocomplete-item{display:flex;align-items:center;gap:14px;padding:12px 16px;cursor:pointer;transition:background .15s ease;border-bottom:1px solid var(--gray-100);}
.autocomplete-item:last-child{border-bottom:none;}
.autocomplete-item:hover,.autocomplete-item.active{background:var(--gray-50);}
.autocomplete-thumb{width:56px;height:56px;border-radius:var(--radius-sm);object-fit:cover;background:var(--gray-100);flex-shrink:0;display:flex;align-items:center;justify-content:center;color:var(--gray-400);font-size:1.5rem;}
.autocomplete-thumb.no-img{background:linear-gradient(135deg,var(--gray-100),var(--gray-200));}
.autocomplete-info{flex:1;min-width:0;}
.autocomplete-name{font-size:.95rem;font-weight:600;color:var(--gray-900);margin-bottom:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.autocomplete-meta{font-size:.8rem;color:var(--gray-500);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.autocomplete-loading{padding:20px;text-align:center;color:var(--gray-400);font-size:.9rem;}
.autocomplete-empty{padding:20px;text-align:center;color:var(--gray-500);font-size:.9rem;}
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
.hero-note{font-size:.95rem;color:var(--gray-600);font-weight:600;margin-top:var(--spacing-sm);display:flex;align-items:center;justify-content:center;gap:8px;}
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
  .steps{flex-direction:column;gap:14px;max-width:none;margin-left:0;margin-right:0;}
  .step{min-height:84px;display:flex;flex-direction:row;align-items:center;justify-content:center;width:100%;gap:18px;padding:20px 32px 20px 56px;box-sizing:border-box;}
  .preview-kw{margin-left:0;margin-right:0;}
  .preview-kw-row{padding:12px 16px;}
  .preview-kw{margin-left:0;margin-right:0;}
  .preview-kw-row{display:inline-flex;justify-content:space-between;padding:12px 24px;margin-left:50%;transform:translateX(-50%);min-width:360px;}
  .step .step-num{flex:0 0 auto;}
  .step .step-body{flex:0 0 450px;display:flex;flex-direction:row;align-items:center;gap:24px;text-align:left;}
  .step .step-body .s-title{flex:0 0 130px;text-align:left;white-space:nowrap;margin-bottom:0;}
  .step .step-body .s-desc{flex:1;text-align:left;position:relative;padding-left:16px;}
  .step .step-body .s-desc::before{content:"";position:absolute;left:0;top:50%;transform:translateY(-50%);width:3px;height:20px;border-radius:2px;background:var(--green);}
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
/* 진단 리포트 카드 (결과 상단 핵심 요약·액션) */
.report-card{background:linear-gradient(160deg,#f0fdf4,#ffffff 55%);border:1px solid #bbf7d0;}
.report-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;}
.report-badge{display:inline-flex;align-items:center;gap:6px;font-size:.74rem;font-weight:800;color:#15803d;background:#dcfce7;padding:5px 12px;border-radius:20px;}
.report-badge .rpt-icon{width:15px;height:15px;}
.report-headline{font-size:1.05rem;font-weight:700;color:var(--gray-800);line-height:1.5;letter-spacing:-.3px;margin-bottom:14px;}
.report-headline b{color:#16a34a;}
.report-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px;}
.report-stat{background:#fff;border:1px solid var(--gray-200);border-radius:12px;padding:12px 6px;text-align:center;}
.report-stat-num{font-size:1.3rem;font-weight:800;color:var(--gray-900);line-height:1.1;}
.report-stat-num.good{color:#16a34a;}
.report-stat-lbl{font-size:.68rem;color:var(--gray-500);margin-top:4px;font-weight:600;}
.report-action{background:#fff;border:1px solid #fed7aa;border-left:4px solid #f97316;border-radius:0 12px 12px 0;padding:13px 15px;margin-bottom:10px;}
.report-action-t{font-size:.78rem;font-weight:800;color:#c2410c;margin-bottom:5px;display:flex;align-items:center;gap:5px;}
.report-action-t .rpt-icon{width:15px;height:15px;}
.report-action-b{font-size:.9rem;color:var(--gray-800);line-height:1.55;}
.report-action-b b{color:#ea580c;}
.report-threat{font-size:.85rem;color:var(--gray-600);line-height:1.5;padding:4px 2px 2px;}
.report-threat b{color:#dc2626;}
.report-cta{display:block;text-align:center;margin-top:14px;padding:13px 8px 3px;border-top:1px solid var(--gray-100);text-decoration:none;}
.report-cta-main{display:block;font-size:.82rem;color:var(--gray-500);font-weight:500;line-height:1.5;}
.report-cta-sub{display:inline-block;margin-top:2px;font-size:.84rem;color:var(--green);font-weight:600;}
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
.ad-check{display:flex;align-items:center;gap:6px;font-size:.82rem;color:var(--gray-800);background:var(--gray-50,#f9fafb);border:1px solid var(--gray-200);border-radius:8px;padding:9px 10px;cursor:pointer;-webkit-tap-highlight-color:transparent;outline:none;}
.ad-check:focus,.ad-check:active{outline:none;box-shadow:none;}
.ad-check input{accent-color:var(--green);outline:none;}
.ad-check input:focus{outline:none;box-shadow:none;}
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
/* 내 순위 추적(구독) 개편 */
.sub-head{display:flex;align-items:center;gap:11px;margin-bottom:14px;}
.sub-head-ico{display:inline-flex;width:40px;height:40px;align-items:center;justify-content:center;background:var(--green-bg);border-radius:11px;flex-shrink:0;}
.sub-head-ico .rpt-icon{width:22px;height:22px;color:var(--green);}
.sub-head-title{font-size:1.02rem;font-weight:800;color:var(--gray-800);letter-spacing:-.3px;}
.sub-head-sub{font-size:.8rem;color:var(--gray-500);margin-top:2px;}
.sub-track{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:12px;padding:14px 16px;margin-bottom:10px;text-align:center;}
.track-label{font-size:.75rem;font-weight:700;color:#15803d;margin-bottom:8px;}
.track-seq{display:flex;flex-wrap:wrap;align-items:center;justify-content:center;gap:7px;margin-bottom:8px;}
.track-seq .ts{font-size:.9rem;font-weight:700;color:var(--gray-500);}
.track-seq .ts.now{color:#16a34a;font-weight:800;font-size:1rem;}
.track-seq .ts-arw{color:#9ca3af;font-style:normal;font-size:.8rem;}
.track-dots{display:flex;justify-content:center;gap:10px;margin-bottom:8px;}
.track-dots .td{width:11px;height:11px;border-radius:50%;background:#d1fae5;border:1.5px solid #a7f3d0;}
.track-dots .td.on{background:#16a34a;border-color:#16a34a;box-shadow:0 0 0 4px rgba(22,163,74,.14);}
.track-hint{font-size:.8rem;color:var(--gray-600);}
.track-hint b{color:#15803d;}
.sub-hooks{display:flex;flex-direction:column;gap:8px;margin-bottom:14px;}
.sub-hook{display:flex;align-items:flex-start;gap:9px;font-size:.88rem;color:var(--gray-700);line-height:1.5;background:#fff;border:1px solid var(--gray-100);border-radius:10px;padding:11px 13px;}
.sub-hook .rpt-icon{width:17px;height:17px;flex-shrink:0;margin-top:2px;}
.sub-hook.trig .rpt-icon{color:#f97316;}
.sub-hook.guard .rpt-icon{color:#ef4444;}
.sub-hook b{color:var(--gray-900);}

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
.kw-popup .kw-rank.out{font-size:1.05rem;color:#9ca3af;font-weight:600;}
.kw-popup .kw-reaction{font-size:1.3rem;margin-top:6px;animation:reactionBounce .4s ease-out;}
@keyframes kwPop{0%{opacity:0;transform:scale(1.4);}60%{transform:scale(0.95);}100%{opacity:1;transform:scale(1);}}
@keyframes reactionBounce{0%{transform:scale(0.5);}50%{transform:scale(1.2);}100%{transform:scale(1);}}

/* S단계: 실제 진행률 표시 */
.l-progress-text{font-size:.9rem;color:var(--gray-500);margin-bottom:4px;}
.l-progress-count{font-size:1.8rem;font-weight:800;color:var(--green);margin-bottom:16px;}
.l-progress-count span{transition:transform .15s;}

/* S단계: 상위 키워드 칩 누적 */
.top-kw-chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-bottom:16px;min-height:32px;max-height:34vh;overflow-y:auto;padding:2px;}
.top-kw-chip{display:inline-flex;align-items:center;gap:4px;padding:6px 12px;border-radius:20px;font-size:.82rem;font-weight:600;animation:chipIn .4s ease-out;}
.top-kw-chip .chip-rank{font-weight:800;margin-right:3px;}
/* 검출된 순위 = 기분 좋은 색 + 조금 더 크게 (좋은 순위가 확 눈에 띄게) */
.top-kw-chip.rank-1{background:linear-gradient(135deg,#fef3c7,#fcd34d);color:#92400e;border:1px solid #f59e0b;font-size:.92rem;padding:8px 16px;font-weight:800;box-shadow:0 2px 9px rgba(245,158,11,.28);}
.top-kw-chip.rank-2-3{background:linear-gradient(135deg,#dcfce7,#bbf7d0);color:#15803d;border:1px solid #86efac;font-size:.9rem;padding:8px 15px;font-weight:800;}
.top-kw-chip.rank-4-5{background:linear-gradient(135deg,#dbeafe,#bfdbfe);color:#1d4ed8;border:1px solid #93c5fd;font-size:.87rem;padding:7px 14px;font-weight:700;}
.top-kw-chip.rank-6-10{background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0;font-size:.85rem;padding:7px 14px;font-weight:700;}
/* 검출됐지만 뒤(11위+) = 옅게 */
.top-kw-chip.rank-11-plus{background:#f3f4f6;color:#6b7280;border:1px solid #e5e7eb;}
/* 순위 밖(미검출) = 가장 옅게·작게 (좋은 순위 돋보이게) */
.top-kw-chip.rank-out{background:#fafafa;color:#9ca3af;border:1px solid #f0f0f0;font-weight:500;font-size:.76rem;padding:5px 11px;}
.top-kw-chip.rank-out .chip-rank{color:#c7c7c7;font-weight:600;}
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
.blog-rank-chip.rank-out{background:#fafafa;color:#bcbcbc;}
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
/* 실시간 진단 피드 */
.scan-hero{display:flex;flex-direction:column;align-items:center;gap:8px;margin:10px 0 10px;}
/* 엔진 가동 라벨 (회전 레이더 아이콘) */
.scan-engine{display:inline-flex;align-items:center;gap:7px;font-size:.9rem;font-weight:800;color:#0f766e;}
.scan-engine-ico{width:19px !important;height:19px !important;color:#14b8a6;animation:engineSpin 1.4s linear infinite;}
@keyframes engineSpin{to{transform:rotate(360deg);}}
/* 병렬 진행 중 키워드 칩 (워커 수만큼 동시 표시, 빛이 스윕하며 처리 중 느낌) */
.scan-live{display:inline-flex;flex-wrap:wrap;gap:7px;justify-content:center;align-items:center;}
.live-chip{position:relative;overflow:hidden;display:inline-flex;align-items:center;font-size:.92rem;font-weight:700;color:#0f766e;background:#ccfbf1;border:1px solid #5eead4;padding:8px 14px;border-radius:16px;animation:livePop .2s ease-out;}
.live-chip::before{content:'';width:7px;height:7px;border-radius:50%;background:#14b8a6;margin-right:7px;flex-shrink:0;box-shadow:0 0 0 0 rgba(20,184,166,.6);animation:liveDot .9s ease-in-out infinite;}
.live-chip::after{content:'';position:absolute;inset:0;background:linear-gradient(100deg,transparent 25%,rgba(255,255,255,.65) 50%,transparent 75%);transform:translateX(-100%);animation:shimmer 1.1s linear infinite;}
.scan-live-wait{font-size:.9rem;color:var(--gray-400);font-weight:500;}
@keyframes livePop{from{opacity:0;transform:scale(.8);}to{opacity:1;transform:scale(1);}}
@keyframes liveDot{0%,100%{box-shadow:0 0 0 0 rgba(20,184,166,.55);}50%{box-shadow:0 0 0 5px rgba(20,184,166,0);}}
@keyframes shimmer{to{transform:translateX(100%);}}
.scan-found{text-align:center;font-size:.85rem;color:#15803d;font-weight:700;margin:2px 0 8px;}
.scan-found .rpt-icon{width:15px;height:15px;vertical-align:-2px;color:#16a34a;}
.scan-found b{font-size:1rem;margin:0 1px;}
/* 상위 노출(≤10위) = 위에 자석처럼 탁 붙는 칩 */
.scan-top{display:flex;flex-wrap:wrap;gap:7px;justify-content:center;margin:0 0 12px;}
.scan-top:empty{display:none;}
.scan-chip{display:inline-flex;align-items:center;gap:5px;padding:6px 13px;border-radius:20px;font-size:.85rem;font-weight:700;animation:chipSnap .32s cubic-bezier(.2,1.4,.4,1) backwards;}
.scan-chip .sc-rank{font-weight:800;}
.scan-chip.r-1{background:linear-gradient(135deg,#fef3c7,#fcd34d);color:#92400e;border:1px solid #f59e0b;box-shadow:0 2px 9px rgba(245,158,11,.25);}
.scan-chip.r-2-3{background:linear-gradient(135deg,#dcfce7,#bbf7d0);color:#15803d;border:1px solid #86efac;}
.scan-chip.r-4-5{background:#dbeafe;color:#1d4ed8;border:1px solid #93c5fd;}
.scan-chip.r-6-10{background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0;}
/* 검출 시도 키워드 = 전부 표기 (그리드, 잘림 없음) */
.scan-feed{display:grid;grid-template-columns:repeat(auto-fill,minmax(158px,1fr));gap:7px;margin-bottom:16px;}
.scan-row{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:9px 12px;background:#fff;border:1px solid var(--gray-100);border-radius:11px;animation:feedIn .3s ease-out;min-width:0;}
.scan-row .sr-kw{font-size:.85rem;color:var(--gray-700);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.scan-row .sr-badge{flex-shrink:0;font-size:.8rem;font-weight:800;padding:3px 10px;border-radius:16px;}
.scan-row.r-1{border-color:#fcd34d;box-shadow:0 2px 11px rgba(245,158,11,.18);}
.scan-row.r-1 .sr-badge{background:linear-gradient(135deg,#fef3c7,#fcd34d);color:#92400e;}
.scan-row.r-2-3{border-color:#bbf7d0;}
.scan-row.r-2-3 .sr-badge{background:linear-gradient(135deg,#dcfce7,#bbf7d0);color:#15803d;}
.scan-row.r-4-5 .sr-badge{background:#dbeafe;color:#1d4ed8;}
.scan-row.r-6-10 .sr-badge{background:#f0fdf4;color:#16a34a;}
.scan-row.r-11 .sr-badge{background:#f3f4f6;color:#6b7280;}
.scan-row.r-out{background:#fafafa;border-color:#f3f4f6;}
.scan-row.r-out .sr-kw{color:#9ca3af;font-weight:500;}
.scan-row.r-out .sr-badge{background:transparent;color:#c0c0c0;font-weight:600;padding:3px 2px;}
@keyframes feedIn{from{opacity:0;transform:translateY(-10px) scale(.96);}to{opacity:1;transform:translateY(0) scale(1);}}
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
.analysis-type-btn{display:flex;flex-direction:column;align-items:center;gap:6px;padding:16px 12px;background:#fff;border:2px solid var(--gray-200);border-radius:var(--radius);cursor:pointer;transition:border-color .2s, background .2s;}
.analysis-type-btn:hover{border-color:var(--green);background:var(--green-bg);}
.analysis-type-btn.selected{border-color:var(--green);background:var(--green-bg);}
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
        <div class="hero-note">가입 없이 · 매장명으로 3초만에 분석 시작</div>
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
          <div class="step"><div class="step-num">1</div><div class="step-body"><div class="s-title">매장명 검색</div><div class="s-desc">매장명만 입력하면 3초만에 분석 시작</div></div></div>
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

      <div style="text-align:center;margin:22px 0 4px">
        <a href="/rank" style="color:var(--green);font-weight:700;text-decoration:none;font-size:.95rem">📊 지역별 · 업종별 네이버 플레이스 순위 한눈에 보기 →</a>
      </div>

      <div class="search-divider" id="searchStart">
        <div class="sd-title">내 매장 순위, 지금 확인</div>
        <div class="sd-sub">아래에 매장 정보를 입력하세요</div>
      </div>
    </div>

    <div class="input-card" id="searchFormCard">
      <div class="field autocomplete-wrap">
        <label>매장명</label>
        <div class="search-input-wrap">
          <input type="text" id="storeName" placeholder="매장명 입력 후 검색 버튼 클릭" autocomplete="off">
          <button type="button" class="search-btn" id="searchPlaceBtn" title="매장 검색">
            <i data-lucide="search"></i>
          </button>
        </div>
        <div class="autocomplete-dropdown" id="autocompleteDropdown"></div>
      </div>
      <div class="field url-field" id="urlFieldWrap" style="display:none;">
        <label>네이버 플레이스 URL</label>
        <input type="text" id="placeUrl" placeholder="https://m.place.naver.com/...">
      </div>
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

      <!-- 실시간 진단 피드 -->
      <div class="scan-hero" id="scanHero" style="display:none;">
        <div class="scan-engine"><i data-lucide="radar" class="rpt-icon scan-engine-ico"></i><span id="scanEngineLabel">키워드 검색 엔진 가동 중</span></div>
        <div class="scan-live" id="scanLive"></div>
      </div>
      <div class="scan-found" id="scanFound" style="display:none;"><i data-lucide="sparkles" class="rpt-icon"></i> 상위 노출 <b id="scanFoundN">0</b>개 발견</div>
      <div class="scan-top" id="scanTopChips"></div>
      <div class="scan-feed" id="scanFeed"></div>

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

      <!-- 진단 리포트 (핵심 요약·액션) — 키워드 순위 바로 위 -->
      <div class="card report-card" id="reportCard" style="display:none;">
        <div class="report-head">
          <span class="report-badge"><i data-lucide="clipboard-list" class="rpt-icon"></i> 순위 진단 리포트</span>
        </div>
        <div class="report-headline" id="reportHeadline"></div>
        <div class="report-stats" id="reportStats"></div>
        <div class="report-action" id="reportAction" style="display:none;"></div>
        <div class="report-threat" id="reportThreat" style="display:none;"></div>
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

      <!-- 내 순위 추적 (구독) -->
      <div class="card" id="subscribeCard">
        <div class="sub-head">
          <span class="sub-head-ico"><i data-lucide="radar" class="rpt-icon"></i></span>
          <div>
            <div class="sub-head-title">내 순위, 계속 지켜봐 드릴게요</div>
            <div class="sub-head-sub">순위가 바뀌면 카카오톡으로 알림 · 무료</div>
          </div>
        </div>
        <!-- ② 추세(첫 기록이면 빈 그래프=채우고 싶게) -->
        <div class="sub-track" id="subTrack"></div>
        <!-- ③ 개인화 미래 트리거 + ④ 손실회피 -->
        <div class="sub-hooks" id="subHooks"></div>
        <div class="subscribe-form" id="subscribeForm">
          <input type="tel" id="subscribePhone" class="subscribe-input" placeholder="휴대폰 번호 (예: 01012345678)" maxlength="13">
          <label class="subscribe-agree">
            <input type="checkbox" id="subscribeAgree">
            <span>플레이스랭킹 순위 리포트 알림톡 수신에 동의합니다 (정보성)</span>
          </label>
          <button class="btn-subscribe" id="btnSubscribe" onclick="submitSubscribe()">무료로 내 순위 추적 시작</button>
        </div>
        <div class="subscribe-done" id="subscribeDone" style="display:none;">
          <div class="subscribe-done-icon">✅</div>
          <div class="subscribe-done-text">추적을 시작했어요!</div>
          <div class="subscribe-done-sub">순위가 바뀌면 카카오톡으로 가장 먼저 알려드릴게요.</div>
        </div>
      </div>

      <!-- BUTTONS -->
      <div class="card">
        <div class="btn-area">
          <div class="btn-row">
            <button class="btn-secondary" onclick="handlePwa()"><i data-lucide="smartphone" class="rpt-icon"></i>홈 화면 추가</button>
            <button class="btn-secondary" onclick="handleShare()">💬 카카오톡으로 공유</button>
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

      <!-- 블로그 분석 후 알림 받기 -->
      <div class="card" id="blogSubscribeCard" style="display:none;">
        <div class="sub-head">
          <span class="sub-head-ico"><i data-lucide="radar" class="rpt-icon"></i></span>
          <div>
            <div class="sub-head-title">블로그 노출, 계속 지켜봐 드릴게요</div>
            <div class="sub-head-sub">블로그 노출 순위가 바뀌면 카카오톡으로 알림 · 무료</div>
          </div>
        </div>
        <div class="sub-track" id="blogSubTrack"></div>
        <div class="sub-hooks" id="blogSubHooks"></div>
        <div class="subscribe-form" id="blogSubscribeForm">
          <input type="tel" id="blogSubscribePhone" class="subscribe-input" placeholder="휴대폰 번호 (예: 01012345678)" maxlength="13">
          <label class="subscribe-agree">
            <input type="checkbox" id="blogSubscribeAgree">
            <span>플레이스랭킹 순위 리포트 알림톡 수신에 동의합니다 (정보성)</span>
          </label>
          <button class="btn-subscribe" id="btnBlogSubscribe" onclick="submitBlogSubscribe()">무료로 블로그 노출 추적 시작</button>
        </div>
        <div class="subscribe-done" id="blogSubscribeDone" style="display:none;">
          <div class="subscribe-done-icon">✅</div>
          <div class="subscribe-done-text">추적을 시작했어요!</div>
          <div class="subscribe-done-sub">블로그 노출이 바뀌면 카카오톡으로 가장 먼저 알려드릴게요.</div>
        </div>
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
let _searchQuery = '';  // 유입 키워드: 사용자가 검색창에 입력한 원본 검색어

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

// ── 유입경로 판별 (방문추적·분석 공용) ─────────────────────────────────────────
// utm_source 우선 → 없으면 document.referrer로 세분화 → 미상은 실제 호스트명 노출
function detectVisitSource() {
  try {
    const urlParams = new URLSearchParams(window.location.search);
    let src = urlParams.get('utm_source');
    if (src) {
      const low = src.toLowerCase();
      if (low === 'blog') {
        const m = urlParams.get('utm_medium');
        if (m === 'case') return '블로그(사례)';
        if (m === 'qna') return '블로그(Q&A)';
        return '블로그';
      }
      // 흔한 utm_source 영문값 → 한글 라벨 정규화 (문자/이메일 등은 태그로만 구분 가능)
      const UTM = {sms:'문자', text:'문자', email:'이메일', mail:'이메일', kakao:'카카오톡',
        kakaotalk:'카카오톡', talk:'카카오톡', instagram:'인스타그램', insta:'인스타그램',
        facebook:'페이스북', fb:'페이스북', naver:'네이버', blog:'블로그', youtube:'유튜브',
        yt:'유튜브', band:'밴드', threads:'스레드', tiktok:'틱톡', qr:'QR코드',
        flyer:'전단지', offline:'오프라인', dm:'DM', ad:'광고', google:'구글', daum:'다음'};
      return UTM[low] || src;
    }
    const ref = (document.referrer || '').toLowerCase();
    if (!ref) return '직접유입';
    let host = '';
    try { host = new URL(document.referrer).hostname.replace(/^www\\./,''); } catch(e) {}
    // 사이트 내 이동(같은 도메인)은 직접유입으로 취급
    if (host && host === location.hostname.replace(/^www\\./,'')) return '직접유입';
    if (ref.includes('blog.naver.com') || ref.includes('m.blog.naver.com') || ref.includes('tistory.com')) return '블로그';
    if (ref.includes('cafe.naver.com')) return '네이버카페';
    if (ref.includes('search.naver.com') || ref.includes('naver.com')) return '네이버검색';
    if (ref.includes('google.')) return '구글검색';
    if (ref.includes('daum.net') || ref.includes('zum.com')) return '포털검색';
    if (ref.includes('chatgpt.com') || ref.includes('openai.com')) return 'ChatGPT';
    if (ref.includes('perplexity.ai')) return 'Perplexity';
    if (ref.includes('claude.ai') || ref.includes('anthropic.com')) return 'Claude';
    if (ref.includes('gemini.google.com') || ref.includes('bard.google.com')) return 'Gemini';
    if (ref.includes('copilot.microsoft.com') || ref.includes('bing.com/chat')) return 'Copilot';
    if (ref.includes('bing.com')) return 'Bing검색';
    if (ref.includes('instagram.com')) return '인스타그램';
    if (ref.includes('facebook.com') || ref.includes('fb.com') || ref.includes('l.facebook')) return '페이스북';
    if (ref.includes('youtube.com') || ref.includes('youtu.be')) return '유튜브';
    if (ref.includes('twitter.com') || ref.includes('x.com') || ref.includes('t.co')) return 'X(트위터)';
    if (ref.includes('kakao') || ref.includes('kko') || ref.includes('daum.net')) return '카카오톡';
    return host ? ('기타:' + host) : '기타';
  } catch(e) { return '직접유입'; }
}

// ── 분석 유형 선택 ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // 디자인2차: 정적 라인 아이콘(결과 카드 타이틀·탭·배지 등) 초기 렌더
  if(window.lucide) lucide.createIcons();
  // K단계: 익명 ID 발급 + 최근 매장 로드
  _anonId = getOrCreateAnonId();
  loadRegisteredStores();  // M단계: 내 매장 / 경쟁 매장 먼저
  loadRecentStores();

  // 방문 기록 추적 (유입경로 세분화 적용)
  const utmSource = detectVisitSource();
  fetch(`/track-visit?anon_id=${_anonId || ''}&source=${encodeURIComponent(utmSource)}&path=/`, {method:'POST'}).catch(()=>{});

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

  // 자동완성 드롭다운 초기화
  initAutocomplete();
});

// ── 자동완성 (매장 검색) ──────────────────────────────────────────────────────
let _acTimeout = null;
let _acController = null;
let _acSelectedIdx = -1;

function initAutocomplete() {
  const input = document.getElementById('storeName');
  const dropdown = document.getElementById('autocompleteDropdown');
  if (!input || !dropdown) return;

  // 입력 중에는 검색하지 않음 (엔터 시에만 검색)
  input.addEventListener('input', () => {
    dropdown.classList.remove('show');
  });

  input.addEventListener('keydown', (e) => {
    const items = dropdown.querySelectorAll('.autocomplete-item');

    if (e.key === 'Enter') {
      e.preventDefault();
      // 드롭다운 열려있고 선택된 항목 있으면 그것 클릭
      if (dropdown.classList.contains('show') && _acSelectedIdx >= 0) {
        items[_acSelectedIdx]?.click();
      }
      // 드롭다운 열려있고 항목 있으면 (키보드 선택 안 했으면) 검색 목록 유지
      else if (dropdown.classList.contains('show') && items.length > 0) {
        // 아무것도 안함 - 사용자가 클릭 또는 방향키로 선택하도록
      }
      // 드롭다운 안 열려있으면 검색해서 목록 표시
      else {
        const q = input.value.trim();
        if (q.length >= 2) {
          _searchQuery = q;  // 유입 키워드 저장
          searchPlaces(q);
        }
      }
      return;
    }

    if (!dropdown.classList.contains('show')) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _acSelectedIdx = Math.min(_acSelectedIdx + 1, items.length - 1);
      updateAcSelection(items);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      _acSelectedIdx = Math.max(_acSelectedIdx - 1, 0);
      updateAcSelection(items);
    } else if (e.key === 'Escape') {
      dropdown.classList.remove('show');
    }
  });

  // 외부 클릭 시 닫기
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.autocomplete-wrap')) {
      dropdown.classList.remove('show');
    }
  });

  // 검색 버튼 클릭
  const searchBtn = document.getElementById('searchPlaceBtn');
  if (searchBtn) {
    searchBtn.addEventListener('click', () => {
      const q = input.value.trim();
      if (q.length >= 2) {
        _searchQuery = q;  // 유입 키워드 저장
        searchPlaces(q);
      }
    });
  }

  // URL 직접 입력 토글
  const urlToggleBtn = document.getElementById('urlToggleBtn');
  const urlFieldWrap = document.getElementById('urlFieldWrap');
  if (urlToggleBtn && urlFieldWrap) {
    urlToggleBtn.addEventListener('click', () => {
      const isHidden = urlFieldWrap.style.display === 'none';
      urlFieldWrap.style.display = isHidden ? 'block' : 'none';
      urlToggleBtn.textContent = isHidden ? 'URL 입력란 닫기' : 'URL로 직접 입력하기';
    });
  }
}

function updateAcSelection(items) {
  items.forEach((it, i) => {
    it.classList.toggle('active', i === _acSelectedIdx);
  });
}

function openUrlField() {
  const urlFieldWrap = document.getElementById('urlFieldWrap');
  const urlToggleBtn = document.getElementById('urlToggleBtn');
  const dropdown = document.getElementById('autocompleteDropdown');

  if (urlFieldWrap) {
    urlFieldWrap.style.display = 'block';
    if (urlToggleBtn) urlToggleBtn.textContent = 'URL 입력란 닫기';
  }
  if (dropdown) dropdown.classList.remove('show');

  // URL 입력란에 포커스
  setTimeout(() => {
    const urlInput = document.getElementById('placeUrl');
    if (urlInput) urlInput.focus();
  }, 100);
}

async function searchPlaces(query) {
  const dropdown = document.getElementById('autocompleteDropdown');
  dropdown.innerHTML = '<div class="autocomplete-loading">검색 중...</div>';
  dropdown.classList.add('show');
  _acSelectedIdx = -1;

  // 기존 요청 취소
  if (_acController) _acController.abort();
  _acController = new AbortController();

  try {
    const res = await fetch(`/search-place?query=${encodeURIComponent(query)}`, {
      signal: _acController.signal
    });
    const results = await res.json();

    if (!results || results.length === 0) {
      dropdown.innerHTML = '<div class="autocomplete-empty">검색 결과가 없습니다<br><button type="button" class="url-fallback-btn" onclick="openUrlField()">URL로 직접 입력하기</button></div>';
      return;
    }

    dropdown.innerHTML = results.map((r, i) => {
      const thumbHtml = r.thumbnail
        ? `<img class="autocomplete-thumb" src="${r.thumbnail}" onerror="this.outerHTML='<div class=\\'autocomplete-thumb no-img\\'>📍</div>'" alt="">`
        : `<div class="autocomplete-thumb no-img">📍</div>`;
      return `
      <div class="autocomplete-item" data-url="${esc(r.url)}" data-name="${esc(r.name)}">
        ${thumbHtml}
        <div class="autocomplete-info">
          <div class="autocomplete-name">${esc(r.name)}</div>
          <div class="autocomplete-meta">${esc(r.category)}${r.category && r.address ? ' · ' : ''}${esc(r.address)}</div>
        </div>
      </div>
    `;
    }).join('') + `<div class="autocomplete-url-footer" onclick="openUrlField()">찾는 매장이 없나요? <b>플레이스 URL로 검색하기</b></div>`;

    dropdown.querySelectorAll('.autocomplete-item').forEach(item => {
      item.addEventListener('click', () => {
        const storeInput = document.getElementById('storeName');
        const urlInput = document.getElementById('placeUrl');

        // 값 설정
        storeInput.value = item.dataset.name;
        urlInput.value = item.dataset.url;
        dropdown.classList.remove('show');

        // 선택 완료 피드백: URL 입력란 하이라이트
        urlInput.style.transition = 'all 0.3s ease';
        urlInput.style.borderColor = 'var(--green)';
        urlInput.style.backgroundColor = 'rgba(0,184,148,0.05)';
        setTimeout(() => {
          urlInput.style.borderColor = '';
          urlInput.style.backgroundColor = '';
        }, 1500);

        // 포커스를 분석 버튼으로 이동
        document.querySelector('.btn-diagnose')?.focus();
      });
    });
  } catch (e) {
    if (e.name !== 'AbortError') {
      dropdown.innerHTML = '<div class="autocomplete-empty">검색 중 오류가 발생했습니다</div>';
    }
  }
}

// 엔터키로 검색 후 첫번째 결과 자동 선택
async function searchPlacesAndSelect(query) {
  const dropdown = document.getElementById('autocompleteDropdown');
  dropdown.innerHTML = '<div class="autocomplete-loading">검색 중...</div>';
  dropdown.classList.add('show');

  try {
    const res = await fetch(`/search-place?query=${encodeURIComponent(query)}`);
    const results = await res.json();

    if (results && results.length > 0) {
      const r = results[0];
      const storeInput = document.getElementById('storeName');
      const urlInput = document.getElementById('placeUrl');

      storeInput.value = r.name;
      urlInput.value = r.url;
      dropdown.classList.remove('show');

      // 선택 완료 피드백
      urlInput.style.transition = 'all 0.3s ease';
      urlInput.style.borderColor = 'var(--green)';
      urlInput.style.backgroundColor = 'rgba(0,184,148,0.05)';
      setTimeout(() => {
        urlInput.style.borderColor = '';
        urlInput.style.backgroundColor = '';
      }, 1500);

      document.querySelector('.btn-diagnose')?.focus();
    } else {
      dropdown.innerHTML = '<div class="autocomplete-empty">검색 결과가 없습니다<br><button type="button" class="url-fallback-btn" onclick="openUrlField()">URL로 직접 입력하기</button></div>';
    }
  } catch (e) {
    dropdown.innerHTML = '<div class="autocomplete-empty">검색 중 오류가 발생했습니다</div>';
  }
}

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

// ── 실시간 진단 피드 헬퍼 (전역) ──────────────────────────────────────────────
let _scanLive = new Set();  // 지금 병렬로 분석 중인 키워드들(워커 수만큼)
function _resetScan(){
  _scanLive = new Set();
  ['scanFeed','scanTopChips','scanLive'].forEach(id=>{const el=document.getElementById(id); if(el) el.innerHTML='';});
  const fb=document.getElementById('scanFound'); if(fb) fb.style.display='none';
  const fn=document.getElementById('scanFoundN'); if(fn) fn.textContent='0';
  const hero=document.getElementById('scanHero'); if(hero) hero.style.display='none';
}
// 병렬 진행 중 키워드 표시 (시작=추가 / 완료=제거) — 실제 워커 병렬을 그대로 노출
function _renderScanLive(){
  const hero=document.getElementById('scanHero'), box=document.getElementById('scanLive');
  if(!hero||!box) return;
  hero.style.display='flex';
  const all=[..._scanLive], arr=all.slice(-5);  // 최대 5개(=워커수) 동시 표시
  const lbl=document.getElementById('scanEngineLabel');
  if(lbl) lbl.textContent = all.length ? `키워드 검색 엔진 · ${all.length}개 동시 검색 중` : '키워드 검색 엔진 가동 중';
  box.innerHTML = arr.length
    ? arr.map(k=>`<span class="live-chip">${esc(k)}</span>`).join('')
    : '<span class="scan-live-wait">다음 키워드 준비 중…</span>';
}
function _scanLiveAdd(kw){ _scanLive.add(kw); _renderScanLive(); }
function _scanLiveDone(kw){ _scanLive.delete(kw); _renderScanLive(); }
function _bumpFound(){
  const fb=document.getElementById('scanFound'), fn=document.getElementById('scanFoundN');
  if(fb) fb.style.display='block';
  if(fn) fn.textContent = (parseInt(fn.textContent||'0',10)+1);
}
function _rankCls(rank){
  if(rank===1) return 'r-1'; if(rank<=3) return 'r-2-3';
  if(rank<=5) return 'r-4-5'; if(rank<=10) return 'r-6-10'; return 'r-11';
}
// 검출 시도 키워드를 그리드에 전부 표기 (최신이 앞) — rank=null이면 30위 밖
function _addScanRow(kw, rank){
  const feed=document.getElementById('scanFeed'); if(!feed) return;
  const found=(rank!==null&&rank!==undefined);
  const cls=found?_rankCls(rank):'r-out', badge=found?(rank+'위'):'30위 밖';
  const row=document.createElement('div');
  row.className='scan-row '+cls;
  row.innerHTML=`<span class="sr-kw">${esc(kw)}</span><span class="sr-badge">${badge}</span>`;
  feed.insertBefore(row, feed.firstChild);
}
// 블로그용 그리드 행 (검출 개수·최고순위)
function _addBlogScanRow(kw, matched, best){
  const feed=document.getElementById('scanFeed'); if(!feed) return;
  let cls='r-out', badge='미검출';
  if(matched>0){ badge = best ? (best+'위') : (matched+'개'); cls = best ? _rankCls(best) : 'r-11'; }
  const row=document.createElement('div');
  row.className='scan-row '+cls;
  row.innerHTML=`<span class="sr-kw">${esc(kw)}</span><span class="sr-badge">${badge}</span>`;
  feed.insertBefore(row, feed.firstChild);
}
// 상위 노출(≤10위) = 위에 자석처럼 탁 붙는 칩
function _addScanTopChip(kw, rank){
  const box=document.getElementById('scanTopChips'); if(!box) return;
  const chip=document.createElement('span');
  chip.className='scan-chip '+_rankCls(rank);
  chip.innerHTML=`<span class="sc-rank">${rank}위</span>${esc(kw)}`;
  box.appendChild(chip);
}

function startLoading(type){
  _lStart=Date.now(); _lStepIdx=0; _lProg=0;
  document.getElementById('lBar').style.width='0%';
  document.getElementById('lPct').textContent='0%';

  // UI 초기화
  _resetScan();
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
  if(!name||!url){alert('매장명을 검색해서 매장을 선택해주세요.');return;}

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
  const utmSource = detectVisitSource();
  const params = new URLSearchParams({
    store_name: name,
    place_url: url,
    force_refresh: force,
    anon_id: _anonId || '',
    ad_place: adFlags.ad_place,
    ad_powerlink: adFlags.ad_powerlink,
    ad_local: adFlags.ad_local,
    ad_blog: adFlags.ad_blog,
    source: utmSource,
    search_query: _searchQuery || name,  // 유입 키워드 (검색어 없으면 매장명)
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
      _resetScan();
    });

    // 현재 병렬로 분석 중인 키워드 추가 (시작 이벤트)
    eventSource.addEventListener('keyword_scanning', (e) => {
      try { _scanLiveAdd(JSON.parse(e.data).keyword); } catch(_){}
    });

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

      // 완료 → 병렬셋에서 제거 + 그리드에 전부 표기 + 상위노출(≤10)은 위 칩 자석
      const rank = d.rank;
      const found = (rank !== null && rank !== undefined);
      _scanLiveDone(d.keyword);
      _addScanRow(d.keyword, rank);
      if(found && rank <= 10){ _bumpFound(); _addScanTopChip(d.keyword, rank); }
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
  if(!name||!url){alert('매장명을 검색해서 매장을 선택해주세요.');return;}

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

  let eventSource = null;
  try {
    const utmSource = detectVisitSource();
    const params = new URLSearchParams({ store_name: name, place_url: url, anon_id: _anonId || '', source: utmSource, search_query: _searchQuery || name });
    eventSource = new EventSource('/analyze-blog-stream?' + params.toString());

    eventSource.addEventListener('started', (e) => {
      _resetScan();
    });

    // 현재 병렬로 분석 중인 블로그 키워드 추가
    eventSource.addEventListener('blog_scanning', (e) => {
      try { _scanLiveAdd(JSON.parse(e.data).keyword); } catch(_){}
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
      const matched = d.matched || 0;
      const best = d.best_rank;
      // 완료 → 병렬셋 제거 + 그리드 전부 표기 + 상위노출은 위 칩
      _scanLiveDone(d.keyword);
      _addBlogScanRow(d.keyword, matched, best);
      if(matched > 0 && best && best <= 10){ _bumpFound(); _addScanTopChip(d.keyword, best); }
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

  // 진단 리포트 (핵심 요약·액션 — 점수 바로 아래 최상단)
  renderActionReport(d, sc);

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

  // 내 순위 추적(구독) — 개인화 훅
  renderSubscribe(d);

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
  document.getElementById('blogSubscribeCard').style.display = 'block';

  // 직전 블로그 순위 맵 + J단계: 키워드 히스토리
  const prevBlogMap = buildPrevBlogRankMap(prev);
  const kwHistory = d.keyword_history || {};
  renderBlogResultsWithComparison(d.blog_results||[], prevBlogMap, kwHistory);

  // 블로그 노출 추적(구독) 개인화 훅
  renderBlogSubscribe(d.blog_results||[]);

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
  if(status==='no_sa' || status==='no_rank'){
    cardEl.style.display='block';
    rowsEl.innerHTML=`<p class="comp-note">아직 순위권(1~30위)에 든 키워드가 없어 비교할 대상이 없어요. 노출되는 키워드를 늘리면 경쟁 위치를 볼 수 있어요.</p>`;
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
    const gradeLabel = c.grade==='S' ? 'S급 키워드' : c.grade==='A' ? 'A급 키워드' : '경쟁 키워드';
    const gradeBg    = c.grade==='S' ? 'background:#22c55e' : c.grade==='A' ? 'background:#3b82f6' : 'background:#0ea5a4';
    const myRankTxt  = c.my_rank ? `${c.my_rank}위` : '30위 밖';
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
// ── 진단 리포트 (핵심 요약·액션) ──────────────────────────────────────────────
function weakestAxis(sc){
  const pairs=[['seo',sc.seo??0],['content',sc.content??0]];
  if(sc.activity!=null) pairs.push(['activity',sc.activity]);
  return pairs.sort((a,b)=>a[1]-b[1])[0][0];
}
function renderActionReport(d, sc){
  const card=document.getElementById('reportCard');
  const allKws=(d.place_results||[]);
  if(!allKws.length){ card.style.display='none'; return; }
  card.style.display='block';

  const ranked=allKws.filter(k=>k.rank);
  const firstPage=ranked.filter(k=>k.rank<=10);
  const best=ranked.slice().sort((a,b)=>a.rank-b.rank)[0];
  // 가장 아까운 키워드: 11~15위 우선, 없으면 6~10위 최상위
  const oppKw=ranked.filter(k=>k.rank>=11&&k.rank<=15).sort((a,b)=>a.rank-b.rank)[0]
           || ranked.filter(k=>k.rank>5&&k.rank<=10).sort((a,b)=>a.rank-b.rank)[0];

  // 1) 헤드라인
  let hl;
  if(!ranked.length){
    hl=`아직 <b>주요 키워드에서 노출이 안 되고</b> 있어요. 지금이 시작하기 딱 좋은 시점이에요.`;
  } else if(best.rank===1){
    hl=`<b>'${esc(best.keyword)}' 1위</b> — 상위 노출이 아주 잘 되고 있어요!`;
  } else if(firstPage.length>0){
    hl=`검색 키워드 중 <b>${firstPage.length}개가 첫 화면(1~10위)</b>에 노출 중이에요.`;
    if(oppKw && oppKw.rank>10) hl+=` '${esc(oppKw.keyword)}' ${oppKw.rank}위만 조금 올리면 하나 더 늘어요.`;
  } else {
    const gap=Math.max(1,best.rank-10);
    hl=`<b>'${esc(best.keyword)}' ${best.rank}위</b> — 첫 화면(10위)까지 <b>${gap}계단</b> 남았어요.`;
  }
  document.getElementById('reportHeadline').innerHTML=hl;

  // 2) 핵심 지표 3개
  document.getElementById('reportStats').innerHTML=`
    <div class="report-stat"><div class="report-stat-num">${allKws.length}</div><div class="report-stat-lbl">분석 키워드</div></div>
    <div class="report-stat"><div class="report-stat-num ${firstPage.length?'good':''}">${firstPage.length}</div><div class="report-stat-lbl">첫 화면 노출</div></div>
    <div class="report-stat"><div class="report-stat-num ${best&&best.rank<=5?'good':''}">${best?best.rank+'위':'-'}</div><div class="report-stat-lbl">최고 순위</div></div>`;

  // 3) 액션 — 가장 아까운 기회 + 약점축 기반 구체 조치
  const actEl=document.getElementById('reportAction');
  const weakKey=weakestAxis(sc);
  const actFix={
    seo:'매장 정보·사진·대표키워드를 꼼꼼히 채우면 노출이 올라가요.',
    content:'리뷰와 별점을 꾸준히 관리하면 격차가 빠르게 좁혀져요.',
    activity:'최근 리뷰를 주기적으로 쌓아 신선도를 높이세요.',
  }[weakKey]||'기본 정보와 리뷰부터 탄탄히 채워보세요.';
  if(oppKw){
    const gap=Math.max(1,oppKw.rank-10);
    actEl.innerHTML=`
      <div class="report-action-t"><i data-lucide="target" class="rpt-icon"></i>지금 가장 아까운 키워드</div>
      <div class="report-action-b"><b>'${esc(oppKw.keyword)}' ${oppKw.rank}위</b> — ${oppKw.rank>10?`${gap}계단만 올리면 첫 화면이에요. `:''}${actFix}</div>`;
    actEl.style.display='block';
  } else if(!ranked.length){
    actEl.innerHTML=`
      <div class="report-action-t"><i data-lucide="target" class="rpt-icon"></i>먼저 할 일</div>
      <div class="report-action-b">핵심 키워드부터 노출시키는 게 우선이에요. ${actFix}</div>`;
    actEl.style.display='block';
  } else {
    actEl.style.display='none';
  }

  // 4) 위기감 — 경쟁사
  const threatEl=document.getElementById('reportThreat');
  const comp=d.competitor||{};
  const cards=comp.cards||[];
  if(cards.length){
    const closest=cards.slice().filter(c=>c.gap!=null).sort((a,b)=>a.gap-b.gap)[0];
    let t=`상위 키워드 <b>${cards.length}곳</b>에서 경쟁사가 앞서 있어요.`;
    if(closest) t+=` 가장 근소한 건 '${esc(closest.keyword)}' ${closest.gap}계단 차이 — 곧 뒤집을 수 있어요.`;
    threatEl.innerHTML=t; threatEl.style.display='block';
  } else if(comp.status==='all_first'){
    threatEl.innerHTML=`주요 키워드에서 <b>모두 1위</b>예요. 이제는 <b>방어</b>가 중요해요.`;
    threatEl.style.display='block';
  } else {
    threatEl.style.display='none';
  }
}

// ── 내 순위 추적(구독) 개인화 훅 ──────────────────────────────────────────────
function renderSubscribe(d){
  const trackEl=document.getElementById('subTrack');
  const hooksEl=document.getElementById('subHooks');
  if(!trackEl||!hooksEl) return;

  const allKws=d.place_results||[];
  const ranked=allKws.filter(k=>k.rank);
  const best=ranked.slice().sort((a,b)=>a.rank-b.rank)[0];
  const oppKw=ranked.filter(k=>k.rank>=11&&k.rank<=15).sort((a,b)=>a.rank-b.rank)[0]
           || ranked.filter(k=>k.rank>5&&k.rank<=10).sort((a,b)=>a.rank-b.rank)[0];
  const kwHist=d.keyword_history||{};

  // ② 추세 / 첫 기록(빈 그래프 = 채우고 싶게)
  let past=[];
  if(best && kwHist[best.keyword]){
    const byDate={}; for(const h of kwHist[best.keyword]) byDate[h.date]=h;
    past=Object.values(byDate).filter(h=>h.rank!=null);
  }
  if(best && past.length>=1){
    const seq=past.slice(-3).map(h=>h.rank+'위').concat('지금 '+best.rank+'위');
    trackEl.innerHTML=`<div class="track-label">‘${esc(best.keyword)}’ 순위 흐름</div>`
      + `<div class="track-seq">${seq.map((s,i)=>`<span class="ts${i===seq.length-1?' now':''}">${s}</span>`).join('<i class="ts-arw">→</i>')}</div>`
      + `<div class="track-hint">이 흐름, 추적을 켜두면 <b>매주 자동으로</b> 이어 그려드려요</div>`;
  } else {
    trackEl.innerHTML=`<div class="track-label">순위 추세</div>`
      + `<div class="track-dots"><span class="td on"></span><span class="td"></span><span class="td"></span><span class="td"></span></div>`
      + `<div class="track-hint">오늘이 <b>1번째 기록</b>이에요 · 매주 자동으로 이어 그려드려요</div>`;
  }

  // ③ 개인화 미래 트리거 + ④ 손실회피
  const hooks=[];
  if(oppKw){
    const gap=Math.max(1,oppKw.rank-10);
    hooks.push(`<div class="sub-hook trig"><i data-lucide="target" class="rpt-icon"></i><span>‘<b>${esc(oppKw.keyword)}</b>’ ${oppKw.rank}위 — 첫 화면까지 <b>${gap}계단</b>. <b>진입하는 순간</b> 알려드릴게요.</span></div>`);
  }
  if(best && best.rank<=10){
    hooks.push(`<div class="sub-hook guard"><i data-lucide="shield" class="rpt-icon"></i><span>지금 ‘<b>${esc(best.keyword)}</b>’ ${best.rank}위 — 경쟁사가 추월하면 <b>바로</b> 알려드려요.</span></div>`);
  } else if(!ranked.length){
    hooks.push(`<div class="sub-hook trig"><i data-lucide="target" class="rpt-icon"></i><span>핵심 키워드가 <b>첫 화면에 드는 순간</b> 알려드릴게요.</span></div>`);
  }
  hooksEl.innerHTML=hooks.join('');
  if(window.lucide) lucide.createIcons();
}

// 블로그 노출 추적(구독) 개인화 훅
function renderBlogSubscribe(blogResults){
  const trackEl=document.getElementById('blogSubTrack');
  const hooksEl=document.getElementById('blogSubHooks');
  if(!trackEl||!hooksEl) return;
  const br=blogResults||[];
  const kwBest=[];
  for(const r of br){
    const ranks=(r.hits||[]).filter(h=>h.rank!=null).map(h=>h.rank);
    if(ranks.length) kwBest.push({keyword:r.keyword, rank:Math.min(...ranks)});
  }
  kwBest.sort((a,b)=>a.rank-b.rank);
  const best=kwBest[0];
  const oppKw=kwBest.filter(k=>k.rank>=11&&k.rank<=15)[0] || kwBest.filter(k=>k.rank>5&&k.rank<=10)[0];

  // ② 빈 그래프(첫 기록 = 채우고 싶게)
  trackEl.innerHTML=`<div class="track-label">블로그 노출 추세</div>`
    + `<div class="track-dots"><span class="td on"></span><span class="td"></span><span class="td"></span><span class="td"></span></div>`
    + `<div class="track-hint">오늘이 <b>1번째 기록</b>이에요 · 블로그 노출 변화를 매주 이어 그려드려요</div>`;

  // ③ 미래 트리거 + ④ 손실회피
  const hooks=[];
  if(oppKw){
    const gap=Math.max(1,oppKw.rank-10);
    hooks.push(`<div class="sub-hook trig"><i data-lucide="target" class="rpt-icon"></i><span>‘<b>${esc(oppKw.keyword)}</b>’ 블로그 ${oppKw.rank}위 — 첫 화면까지 <b>${gap}계단</b>. <b>진입하는 순간</b> 알려드릴게요.</span></div>`);
  }
  if(best && best.rank<=10){
    hooks.push(`<div class="sub-hook guard"><i data-lucide="shield" class="rpt-icon"></i><span>지금 ‘<b>${esc(best.keyword)}</b>’ 블로그 ${best.rank}위 노출 중 — 밀려나면 <b>바로</b> 알려드려요.</span></div>`);
  } else if(!kwBest.length){
    hooks.push(`<div class="sub-hook trig"><i data-lucide="target" class="rpt-icon"></i><span>우리 블로그가 <b>검색 첫 화면에 노출되는 순간</b> 알려드릴게요.</span></div>`);
  }
  hooksEl.innerHTML=hooks.join('');
  if(window.lucide) lucide.createIcons();
}

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
  const d = _lastResultData || {};
  const pid = d.place_id;
  const url = pid ? ('https://placeranking.com/report/' + encodeURIComponent(pid)) : 'https://placeranking.com';
  // 받는 사람이 '내 가게는 몇 위지?' 궁금해서 눌러보게 하는 훅 (매장·점수는 카드 이미지가 보여줌)
  const title = '우리 가게 네이버 플레이스 순위는 몇 위일까?';
  const desc = '1분이면 무료로 내 가게 순위·경쟁사 비교까지 확인할 수 있어요';

  // 1순위: 카카오톡 직접 공유 (나에게 보내기 / 친구 선택 + OG 카드)
  if(pid && window.Kakao && Kakao.isInitialized && Kakao.isInitialized()){
    try{
      Kakao.Share.sendDefault({
        objectType: 'feed',
        content: {
          title: title,
          description: desc,
          imageUrl: 'https://placeranking.com/og/' + encodeURIComponent(pid) + '.png?v=2',
          link: { mobileWebUrl: url, webUrl: url }
        },
        buttons: [{ title: '내 가게 순위 확인하기', link: { mobileWebUrl: url, webUrl: url } }]
      });
      return;
    }catch(e){ /* SDK 실패 시 아래 폴백 */ }
  }

  // 2순위: OS 공유 시트 (모바일, 카톡도 선택 가능)
  const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
  if(isMobile && navigator.share){
    navigator.share({title: title, text: desc, url: url}).catch(()=>{});
    return;
  }
  // 3순위: 링크 복사
  const shareText = title + ' ' + url;
  navigator.clipboard.writeText(shareText).then(()=>{
    alert('공유 링크가 복사되었습니다!');
  }).catch(()=>{
    prompt('아래 링크를 복사하세요:', shareText);
  });
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
      const tk=document.getElementById('subTrack'); if(tk) tk.style.display='none';
      const hk=document.getElementById('subHooks'); if(hk) hk.style.display='none';
      document.getElementById('subscribeDone').style.display = 'block';
    } else {
      alert(data.detail || '신청에 실패했습니다.');
      btn.disabled = false;
      btn.textContent = '무료로 내 순위 추적 시작';
    }
  } catch(e){
    alert('네트워크 오류가 발생했습니다.');
    btn.disabled = false;
    btn.textContent = '무료로 내 순위 추적 시작';
  }
}

// 블로그 분석용 알림 구독
async function submitBlogSubscribe(){
  const phone = document.getElementById('blogSubscribePhone').value.replace(/[^0-9]/g,'');
  const agreed = document.getElementById('blogSubscribeAgree').checked;
  const btn = document.getElementById('btnBlogSubscribe');

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
        store_name: _lastStoreName || '',
        phone: phone,
        store_url: _lastPlaceUrl || document.getElementById('placeUrl').value,
        place_id: null,
        anon_id: _anonId,
        agreed: true
      })
    });
    const data = await res.json();
    if(res.ok){
      document.getElementById('blogSubscribeForm').style.display = 'none';
      const tk=document.getElementById('blogSubTrack'); if(tk) tk.style.display='none';
      const hk=document.getElementById('blogSubHooks'); if(hk) hk.style.display='none';
      document.getElementById('blogSubscribeDone').style.display = 'block';
    } else {
      alert(data.detail || '신청에 실패했습니다.');
      btn.disabled = false;
      btn.textContent = '무료로 블로그 노출 추적 시작';
    }
  } catch(e){
    alert('네트워크 오류가 발생했습니다.');
    btn.disabled = false;
    btn.textContent = '무료로 블로그 노출 추적 시작';
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
  document.getElementById('blogSubscribeCard').style.display = 'block';

      const prevBlogMap = buildPrevBlogRankMap(_historyBlogData.prev_analysis);
      const kwHistory = _historyBlogData.keyword_history || {};
      renderBlogResultsWithComparison(_historyBlogData.blog_results || [], prevBlogMap, kwHistory);
      renderBlogSubscribe(_historyBlogData.blog_results || []);
    } else if(!_blogAnalyzed && !_historyBlogData){
      // 블로그 기록 없으면 분석하기 버튼 표시
      document.getElementById('blogStartCard').style.display = 'block';
      document.getElementById('blogLoading').style.display = 'none';
      document.getElementById('blogResultCard').style.display = 'none';
  document.getElementById('blogSubscribeCard').style.display = 'none';
    }
  }
}

// ── 블로그 분석 (SSE 스트리밍) ─────────────────────────────────────────────────
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
  document.getElementById('blogSubscribeCard').style.display = 'none';

  const keywords = (d.keywords_used || []);
  const total = Math.min(keywords.length, 15) || 15;
  let progress = 0;

  // SSE 스트리밍으로 타임아웃 방지
  const params = new URLSearchParams({
    store_name: d.store_name,
    place_id: d.place_id,
    address: d.address || '',
    category: d.category || '',
    keywords: keywords.join(','),
    anon_id: getAnonId(),
  });

  const eventSource = new EventSource('/analyze-blog-stream?' + params.toString());

  eventSource.addEventListener('started', (e) => {
    const data = JSON.parse(e.data);
    document.getElementById('blogProgress').textContent = `0/${data.total || total}`;
  });

  eventSource.addEventListener('blog_keyword', (e) => {
    progress++;
    const data = JSON.parse(e.data);
    const t = data.total || total;
    document.getElementById('blogProgress').textContent = `${progress}/${t}`;
    document.getElementById('blogBar').style.width = `${Math.min(95, (progress/t)*100)}%`;
  });

  eventSource.addEventListener('complete', (e) => {
    eventSource.close();
    const result = JSON.parse(e.data);
    _blogAnalyzed = true;

    document.getElementById('blogBar').style.width = '100%';
    document.getElementById('blogProgress').textContent = `${total}/${total}`;
    document.getElementById('blogLoading').style.display = 'none';
    document.getElementById('blogResultCard').style.display = 'block';
    document.getElementById('blogSubscribeCard').style.display = 'block';

    const prevBlogMap = buildPrevBlogRankMap(result.prev_analysis);
    const kwHistory = result.keyword_history || {};
    renderBlogResultsWithComparison(result.blog_results || [], prevBlogMap, kwHistory);
    renderBlogSubscribe(result.blog_results || []);

    btn.disabled = false;
    btn.textContent = '🔍 블로그 노출 분석하기';
  });

  eventSource.addEventListener('error', (e) => {
    eventSource.close();
    let msg = '분석 중 오류가 발생했어요.';
    try { const data = JSON.parse(e.data); msg = data.message || msg; } catch(ex) {}
    document.getElementById('blogLoading').style.display = 'none';
    document.getElementById('blogStartCard').style.display = 'block';
    btn.disabled = false;
    btn.textContent = '🔍 블로그 노출 분석하기';
    alert('블로그 분석 실패: ' + msg);
  });

  eventSource.onerror = () => {
    if(eventSource.readyState === EventSource.CLOSED) return;
    eventSource.close();
    document.getElementById('blogLoading').style.display = 'none';
    document.getElementById('blogStartCard').style.display = 'block';
    btn.disabled = false;
    btn.textContent = '🔍 블로그 노출 분석하기';
  };
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
  document.getElementById('blogSubscribeCard').style.display = 'none';
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
    # 메인 HTML은 인라인(자산 버전관리 없음)이라 브라우저가 옛 화면을 캐시하면
    # "배포했는데 안 바뀜" 혼란이 생긴다 → 항상 재검증(no-cache)해서 최신 화면 보장.
    return HTMLResponse(_HTML, headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/health", tags=["시스템"])
def health():
    return {"status": "ok"}


# ── 공유 리포트 페이지 + OG 카드 이미지 (카카오톡 큰 미리보기) ──────────────────

def _grade_of(score: int):
    if score >= 80:
        return "S", "#16a34a", "#dcfce7"
    if score >= 60:
        return "A", "#3b82f6", "#dbeafe"
    if score >= 40:
        return "B", "#f97316", "#ffedd5"
    return "C", "#ef4444", "#fee2e2"


def _report_data(place_id: str, data: dict) -> dict:
    import html as _h
    store = _h.escape((data.get("store_name") or "내 가게")[:40])
    category = _h.escape((data.get("category") or "매장")[:30])
    scores = data.get("scores") or {}
    try:
        score = int(round(float(scores.get("total") or 0)))
    except (TypeError, ValueError):
        score = 0
    results = sorted([r for r in (data.get("place_results") or []) if r.get("rank")],
                     key=lambda r: r["rank"])
    best = results[0] if results else None
    first_page = [r for r in results if r["rank"] <= 10]
    g, gc, gbg = _grade_of(score)

    if best:
        headline = f"‘{_h.escape(str(best['keyword']))}’ {best['rank']}위" + (
            f" · 첫 화면 노출 {len(first_page)}개" if first_page else "")
    else:
        headline = "아직 상위 노출 키워드가 없어요"
    # 카톡 공유 시 받는 사람이 '내 가게는?' 궁금해서 눌러보게 하는 훅 (제목·설명)
    desc = "1분이면 무료로 내 가게 순위·경쟁사 비교까지 확인할 수 있어요"

    return {
        "store": store, "category": category, "score": score,
        "grade": g, "gc": gc, "gbg": gbg, "results": results,
        "first_page": first_page, "best": best, "headline": headline, "desc": desc,
        "title": "우리 가게 네이버 플레이스 순위는 몇 위일까?",
        "og_img": f"https://placeranking.com/og/{place_id}.png?v=2",
        "url": f"https://placeranking.com/report/{place_id}",
    }


def _build_report_html(place_id: str, data: dict) -> str:
    import html as _h
    r = _report_data(place_id, data)

    kw_rows = ""
    for row in r["results"][:6]:
        rank = row["rank"]
        color = ("#16a34a" if rank <= 5 else "#3b82f6" if rank <= 10
                 else "#f97316" if rank <= 15 else "#9ca3af")
        kw_rows += (f'<li><span class="kw">{_h.escape(str(row["keyword"]))}</span>'
                    f'<span class="rk" style="color:{color}">{rank}위</span></li>')
    if not kw_rows:
        kw_rows = '<li class="empty">아직 상위 노출 키워드가 없어요</li>'

    return f"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{r['title']}</title>
<meta name="description" content="{r['desc']}">
<link rel="canonical" href="{r['url']}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="플레이스랭킹">
<meta property="og:title" content="{r['title']}">
<meta property="og:description" content="{r['desc']}">
<meta property="og:image" content="{r['og_img']}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:url" content="{r['url']}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{r['title']}">
<meta name="twitter:description" content="{r['desc']}">
<meta name="twitter:image" content="{r['og_img']}">
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:-apple-system,'Malgun Gothic','Apple SD Gothic Neo',sans-serif;background:#f6f8f7;color:#111827;padding:18px;line-height:1.5;}}
.wrap{{max-width:460px;margin:0 auto;}}
.brand{{font-weight:800;color:#16a34a;font-size:1.05rem;padding:6px 0 14px;}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:24px 20px;}}
.cat{{display:inline-block;font-size:.78rem;color:#6b7280;background:#f3f4f6;padding:4px 11px;border-radius:20px;margin-bottom:10px;}}
h1{{font-size:1.5rem;font-weight:800;letter-spacing:-.5px;margin-bottom:18px;}}
.score-wrap{{display:flex;align-items:center;gap:14px;margin-bottom:18px;}}
.score-num{{font-size:3rem;font-weight:800;line-height:1;}}
.score-num span{{font-size:1.1rem;color:#9ca3af;margin-left:2px;}}
.grade{{font-size:.85rem;font-weight:800;color:#fff;padding:6px 14px;border-radius:20px;}}
.headline{{font-size:1rem;font-weight:600;color:#374151;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:12px;padding:13px 15px;margin-bottom:18px;}}
.kwlist{{list-style:none;}}
.kwlist li{{display:flex;justify-content:space-between;align-items:center;padding:11px 2px;border-bottom:1px solid #f3f4f6;font-size:.95rem;}}
.kwlist li:last-child{{border-bottom:none;}}
.kwlist .kw{{color:#374151;font-weight:500;}}
.kwlist .rk{{font-weight:800;}}
.kwlist .empty{{justify-content:center;color:#9ca3af;}}
.cta{{display:block;text-align:center;margin-top:18px;padding:17px;background:linear-gradient(135deg,#16a34a,#15803d);color:#fff;font-size:1.05rem;font-weight:800;border-radius:14px;text-decoration:none;}}
.foot{{text-align:center;color:#9ca3af;font-size:.8rem;margin-top:16px;}}
</style></head><body>
<div class="wrap">
  <div class="brand">📊 플레이스랭킹</div>
  <div class="card">
    <span class="cat">{r['category']}</span>
    <h1>{r['store']}</h1>
    <div class="score-wrap">
      <div class="score-num" style="color:{r['gc']}">{r['score']}<span>점</span></div>
      <div class="grade" style="background:{r['gc']}">{r['grade']}등급</div>
    </div>
    <div class="headline">{r['headline']}</div>
    <ul class="kwlist">{kw_rows}</ul>
  </div>
  <a class="cta" href="/">내 가게 순위도 무료로 확인하기 →</a>
  <div class="foot">네이버 플레이스 순위 무료 진단 · placeranking.com</div>
</div>
</body></html>"""


def _report_not_found_html() -> str:
    return """<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>플레이스랭킹 · 네이버 플레이스 순위 무료 진단</title>
<meta property="og:title" content="네이버 플레이스 순위 무료 확인 | 플레이스랭킹">
<meta property="og:description" content="내 매장 키워드 순위를 무료로 확인하세요.">
<meta http-equiv="refresh" content="2; url=/">
<style>body{font-family:-apple-system,'Malgun Gothic',sans-serif;background:#f6f8f7;text-align:center;padding:60px 20px;color:#374151;}
a{color:#16a34a;font-weight:800;text-decoration:none;}</style></head><body>
<h2 style="color:#16a34a;">📊 플레이스랭킹</h2>
<p style="margin:16px 0;">아직 이 매장의 분석 결과가 없어요.</p>
<a href="/">내 가게 순위 무료로 확인하기 →</a>
</body></html>"""


@app.get("/og/{place_id}.png", include_in_schema=False)
def og_card(place_id: str, db: Session = Depends(get_db)):
    if _og is None:
        return Response(status_code=404)
    data = crud.get_latest_analysis_result(db, place_id, "place")
    if not data:
        return Response(status_code=404)
    try:
        png = _og.generate_card(data)
    except Exception as e:  # noqa: BLE001
        _pkg_logger.warning(f"OG 카드 생성 실패({place_id}): {e}")
        return Response(status_code=404)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/report/{place_id}", response_class=HTMLResponse, include_in_schema=False)
def report_page(place_id: str, db: Session = Depends(get_db)):
    data = crud.get_latest_analysis_result(db, place_id, "place")
    if not data:
        return HTMLResponse(_report_not_found_html())
    return HTMLResponse(_build_report_html(place_id, data))


def _source_from_ua(ua: str, referer: str = "") -> str | None:
    """User-Agent(+서버 referer)로 유입경로 판별. 인앱 브라우저는 referrer가 없어
    JS가 '직접유입'으로 넘기지만, UA 문자열엔 앱 식별자가 남는다(한국은 인앱 비중이 큼).
    분류 못 하면 None(=직접유입 유지)."""
    u = (ua or "").lower()
    # 인앱 브라우저 / 메신저 (UA 기반)
    if "kakaotalk" in u:                              return "카카오톡"
    if "naver(inapp" in u or "naver(inapp;" in u or " naver/" in u or "navercafe" in u: return "네이버앱"
    if "instagram" in u:                              return "인스타그램"
    if "fban" in u or "fbav" in u or "fb_iab" in u or "fbios" in u: return "페이스북"
    if "line/" in u or "line " in u:                  return "라인"
    if "band/" in u or "bandapp" in u:                return "밴드"
    if "daumapps" in u or "daum/" in u:               return "다음앱"
    if "everytimeapp" in u:                           return "에브리타임"
    if "threads" in u:                                return "스레드"
    if "trill" in u or "musical_ly" in u or "tiktok" in u: return "틱톡"
    if "twitter" in u:                                return "X(트위터)"
    if "telegram" in u:                               return "텔레그램"
    if "micromessenger" in u:                         return "위챗"
    if "snapchat" in u:                               return "스냅챗"
    if "pinterest" in u:                              return "핀터레스트"
    if "linkedinapp" in u or "linkedin" in u:         return "링크드인"
    if "discord" in u:                                return "디스코드"
    if "reddit" in u:                                 return "레딧"
    # 서버 referer 폴백 (JS referrer가 비어도 헤더엔 있을 수 있음)
    r = (referer or "").lower()
    if r:
        if "blog.naver" in r or "m.blog.naver" in r or "tistory" in r: return "블로그"
        if "cafe.naver" in r:            return "네이버카페"
        if "search.naver" in r or "naver.com" in r: return "네이버검색"
        if "google." in r:               return "구글검색"
        if "daum.net" in r or "zum.com" in r: return "포털검색"
        if "instagram" in r:             return "인스타그램"
        if "facebook" in r or "l.facebook" in r or "fb.com" in r: return "페이스북"
        if "youtube" in r or "youtu.be" in r: return "유튜브"
        if "twitter" in r or "x.com" in r or "t.co" in r: return "X(트위터)"
        if "instagram" in r:             return "인스타그램"
        if "t.me" in r or "telegram" in r: return "텔레그램"
        if "pinterest" in r:             return "핀터레스트"
        if "linkedin" in r:              return "링크드인"
        if "band.us" in r:               return "밴드"
        if "bing.com" in r:              return "Bing검색"
        if "chatgpt" in r or "openai" in r: return "ChatGPT"
        if "perplexity" in r:            return "Perplexity"
    return None


@app.post("/track-visit", tags=["추적"])
def track_visit(
    request: Request,
    anon_id: str | None = None,
    source: str | None = None,
    path: str | None = "/",
    db: Session = Depends(get_db)
):
    """사이트 방문 기록. JS가 '직접유입'/빈값으로 넘긴 경우에만 UA로 재분류(이미 분류된 건 유지)."""
    src = source or ""
    if src in ("", "직접유입", "기타"):
        detected = _source_from_ua(
            request.headers.get("user-agent", ""),
            request.headers.get("referer", ""),
        )
        if detected:
            src = detected
        elif not src:
            src = "직접유입"
    crud.record_site_visit(db, anon_id=anon_id, source=src, path=path)
    return {"ok": True}


@app.get("/sitemap.xml", tags=["SEO"])
async def sitemap(db: Session = Depends(get_db)):
    from urllib.parse import quote
    urls = ['<url><loc>https://placeranking.com/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>',
            '<url><loc>https://placeranking.com/rank</loc><changefreq>daily</changefreq><priority>0.9</priority></url>']
    try:
        for item in crud.get_keyword_rankings(db):
            slug = quote(item["keyword"].replace(" ", "-"))
            urls.append(f'<url><loc>https://placeranking.com/rank/{slug}</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>')
    except Exception:
        pass
    content = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               + "\n".join(urls) + "\n</urlset>")
    return Response(content=content, media_type="application/xml")


# ── 자동 SEO 콘텐츠: 지역+업종 순위 페이지 (분석 데이터 → 검색 유입 자가증식) ──────
_SEO_CSS = """
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,'Malgun Gothic',sans-serif;color:#1a1f24;background:#f6f8f7;line-height:1.6}
.wrap{max-width:720px;margin:0 auto;padding:20px 16px 60px}
.top{display:flex;align-items:center;gap:8px;padding:14px 0}.top a{color:#03c75a;font-weight:800;text-decoration:none;font-size:18px}
h1{font-size:22px;margin:18px 0 6px;line-height:1.35}.sub{color:#5b6770;font-size:14px;margin:0 0 18px}
.card{background:#fff;border:1px solid #e5eae7;border-radius:14px;padding:18px;margin:14px 0}
table{width:100%;border-collapse:collapse}td{padding:11px 8px;border-bottom:1px solid #eef2f0;font-size:15px}
.rk{font-weight:800;color:#03c75a;width:64px}.rk.top{color:#e8a400}
.cta{display:block;text-align:center;background:#03c75a;color:#fff;font-weight:800;font-size:17px;padding:16px;border-radius:12px;text-decoration:none;margin:20px 0}
.note{color:#5b6770;font-size:13.5px}.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
.chip{background:#fff;border:1px solid #e5eae7;border-radius:20px;padding:7px 13px;font-size:13.5px;text-decoration:none;color:#1a1f24}
.chip:hover{border-color:#03c75a;color:#03c75a}.foot{color:#9aa5ad;font-size:12.5px;text-align:center;margin-top:30px}
h2{font-size:16px;margin:22px 0 8px}
"""

def _seo_head(title, desc, canonical, jsonld=""):
    ld = f'<script type="application/ld+json">{jsonld}</script>' if jsonld else ""
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title><meta name="description" content="{desc}">'
            f'<link rel="canonical" href="{canonical}">'
            f'<meta property="og:title" content="{title}"><meta property="og:description" content="{desc}">'
            f'<meta property="og:type" content="website"><meta name="robots" content="index,follow">'
            f'<style>{_SEO_CSS}</style>{ld}</head><body><div class="wrap">'
            f'<div class="top"><a href="/">📊 플레이스랭킹</a></div>')

_SEO_FOOT = ('<a class="cta" href="/">👉 내 가게 순위 무료로 확인하기</a>'
             '<p class="foot">플레이스랭킹 · 네이버 플레이스 순위 무료 진단 · '
             '<a href="/rank" style="color:#9aa5ad">전체 순위</a></p></div></body></html>')


@app.get("/rank", response_class=HTMLResponse, include_in_schema=False)
async def rank_index(db: Session = Depends(get_db)):
    from urllib.parse import quote
    import html as _h
    data = crud.get_keyword_rankings(db)
    # 지역별 그룹
    by_region = {}
    for it in data:
        by_region.setdefault(it["region"], []).append(it)
    title = "지역별·업종별 네이버 플레이스 순위 | 플레이스랭킹"
    desc = "지역·업종별 네이버 플레이스 실제 순위를 한눈에. 내 가게 순위도 무료로 바로 확인하세요."
    body = ['<h1>지역별 · 업종별 플레이스 순위</h1>',
            '<p class="sub">네이버 플레이스에서 어떤 가게가 몇 위인지, 실제 분석 데이터로 확인하세요.</p>']
    for region in sorted(by_region.keys()):
        items = by_region[region]
        body.append(f'<h2>{_h.escape(region)}</h2><div class="chips">')
        for it in sorted(items, key=lambda x: len(x["stores"]), reverse=True)[:40]:
            slug = quote(it["keyword"].replace(" ", "-"))
            body.append(f'<a class="chip" href="/rank/{slug}">{_h.escape(it["service"])} <b>({len(it["stores"])})</b></a>')
        body.append('</div>')
    if not data:
        body.append('<div class="card note">아직 분석 데이터가 쌓이는 중입니다. 매장을 분석할수록 이 페이지가 풍성해집니다.</div>')
    return HTMLResponse(_seo_head(title, desc, "https://placeranking.com/rank") + "".join(body) + _SEO_FOOT)


@app.get("/rank/{slug}", response_class=HTMLResponse, include_in_schema=False)
async def rank_detail(slug: str, db: Session = Depends(get_db)):
    from urllib.parse import quote, unquote
    import html as _h
    keyword = unquote(slug).replace("-", " ").strip()
    data = crud.get_keyword_rankings(db)
    item = next((x for x in data if x["keyword"] == keyword), None)
    if not item:
        # 없으면 인덱스로
        return HTMLResponse(status_code=404, content=_seo_head(
            "순위 정보 없음 | 플레이스랭킹", "요청하신 순위 페이지가 아직 없습니다.",
            "https://placeranking.com/rank") + '<h1>아직 데이터가 없어요</h1>'
            '<p class="sub">이 키워드는 아직 분석 기록이 없습니다.</p>' + _SEO_FOOT)
    kw = item["keyword"]; region = item["region"]; service = item["service"]; stores = item["stores"]
    n = len(stores)
    title = f"{kw} 순위 TOP {n} | 네이버 플레이스 순위 - 플레이스랭킹"
    desc = f"{kw} 네이버 플레이스 순위 데이터 {n}건. {region}에서 {service} 검색 시 상위 노출 매장을 확인하고, 내 가게 순위도 무료로 진단하세요."
    items_ld = ",".join(
        f'{{"@type":"ListItem","position":{i},"name":"{_h.escape(nm)}"}}'
        for i, (nm, pid, rk) in enumerate(stores, 1))
    jsonld = ('{"@context":"https://schema.org","@type":"ItemList",'
              f'"name":"{_h.escape(kw)} 순위","numberOfItems":{n},"itemListElement":[{items_ld}]}}')
    rows = ""
    for nm, pid, rk in stores:
        cls = "rk top" if rk <= 3 else "rk"
        rows += f'<tr><td class="{cls}">{rk}위</td><td>{_h.escape(nm)}</td></tr>'
    # 인근 관련 페이지(같은 지역 다른 업종) 내부링크
    related = [x for x in data if x["region"] == region and x["keyword"] != kw][:8]
    rel_html = ""
    if related:
        rel_html = '<h2>' + _h.escape(region) + ' 다른 업종 순위</h2><div class="chips">'
        for x in related:
            rel_html += f'<a class="chip" href="/rank/{quote(x["keyword"].replace(" ","-"))}">{_h.escape(x["service"])}</a>'
        rel_html += '</div>'
    body = (f'<h1>{_h.escape(kw)} 네이버 플레이스 순위</h1>'
            f'<p class="sub">{_h.escape(region)}에서 <b>{_h.escape(service)}</b> 검색 시 상위 노출된 매장입니다. '
            f'(플레이스랭킹 분석 데이터 · {n}건)</p>'
            f'<div class="card"><table>{rows}</table></div>'
            f'<p class="note">순위는 분석 시점 기준이며 실시간과 다를 수 있습니다. '
            f'내 가게가 여기 없거나 순위를 올리고 싶다면 아래에서 무료로 진단해 보세요.</p>'
            + rel_html)
    return HTMLResponse(_seo_head(title, desc, f"https://placeranking.com/rank/{quote(kw.replace(' ','-'))}", jsonld) + body + _SEO_FOOT)


# ── 키워드 도구 API ──────────────────────────────────────────────────────────

@app.get("/keyword-tool/top-places", tags=["키워드도구"])
async def keyword_tool_top_places(keyword: str, limit: int = 10):
    """
    키워드 검색 시 상위 1~10위 매장 정보를 반환합니다.

    - **keyword**: 검색할 키워드 (예: "강남역 맛집", "홍대 카페")
    - **limit**: 반환할 매장 수 (기본 10, 최대 10)

    Returns:
        - keyword: 검색한 키워드
        - businesses_total: 해당 키워드로 등록된 총 업체 수 (등급 산출용)
        - places: 상위 매장 리스트
            - rank: 순위 (1~10)
            - place_id: 네이버 플레이스 ID
            - name: 매장명
            - image: 대표 이미지 URL
            - place_url: 플레이스 URL
            - category: 업종
            - address: 주소
    """
    if not keyword or len(keyword.strip()) < 2:
        raise HTTPException(status_code=400, detail="키워드를 2자 이상 입력하세요")

    keyword = keyword.strip()
    limit = min(max(1, limit), 10)

    try:
        import concurrent.futures
        future = asyncio.run_coroutine_threadsafe(
            fetch_top_places(keyword, limit),
            _proactor_loop
        )
        result = future.result(timeout=30)
        return result
    except concurrent.futures.TimeoutError:
        raise HTTPException(status_code=504, detail="검색 시간 초과")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")


@app.get("/search-place", tags=["검색"])
async def search_place(query: str):
    """
    네이버 플레이스 검색 - 매장명으로 검색하여 후보 목록 반환
    search.naver.com HTML에서 Apollo State 파싱 (httpx로 빠르게)
    """
    import re
    import json as json_module
    from urllib.parse import quote
    import httpx

    if not query or len(query.strip()) < 2:
        return []

    query = query.strip()

    def _norm(s: str) -> str:
        return re.sub(r'\s+', '', s or '').lower()

    def _name_match(name: str, q: str) -> bool:
        n, qq = _norm(name), _norm(q)
        if not n or not qq:
            return False
        # 앞글자 매칭: 매장명이 검색어로 시작(체인 지점) 또는 검색어가 매장명으로 시작
        # (중간글자 substring 오탐 제거 — "이안"이 매장명 중간에 든 무관 업체 걸러짐)
        return n.startswith(qq) or qq.startswith(n)

    async def do_search_pcmap(q: str) -> list:
        """pcmap.place.naver.com 목록 페이지 — 지점 최대 50개(동일 매장명 다수) 반환."""
        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
        url = f"https://pcmap.place.naver.com/place/list?query={quote(q)}"
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            html = resp.text
        m = re.search(r'window\.__APOLLO_STATE__\s*=\s*(\{.*?\});\s*</script>', html, re.DOTALL)
        if not m:
            m = re.search(r'__APOLLO_STATE__\s*=\s*(\{.*?\});', html, re.DOTALL)
        if not m:
            return results
        try:
            state = json_module.loads(m.group(1))
        except json_module.JSONDecodeError:
            return results
        for key, val in state.items():
            if not key.startswith("PlaceListBusinessesItem:") or not isinstance(val, dict):
                continue
            pid = val.get("id") or key.split(":")[-1]
            name = re.sub(r'<[^>]+>', '', val.get("name") or "").strip()
            if not pid or not name:
                continue
            thumb = val.get("imageUrl") or val.get("thumUrl") or ""
            if thumb and thumb.startswith("//"):
                thumb = "https:" + thumb
            results.append({
                "place_id": str(pid),
                "name": name,
                "category": (val.get("category") or "").split(",")[0].strip(),
                "address": val.get("fullAddress") or val.get("roadAddress") or val.get("commonAddress") or val.get("address") or "",
                "thumbnail": thumb,
                "url": f"https://m.place.naver.com/place/{pid}",
            })
        return results

    async def do_search(q: str) -> list:
        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
        url = f"https://search.naver.com/search.naver?where=nexearch&query={quote(q)}"

        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            html = resp.text

            match = re.search(r'__APOLLO_STATE__\s*=\s*(\{.*\});\s*window\.__APOLLO', html, re.DOTALL)
            if not match:
                match = re.search(r'__APOLLO_STATE__\s*=\s*(\{[^<]+\});', html, re.DOTALL)
            if match:
                try:
                    state = json_module.loads(match.group(1))
                except json_module.JSONDecodeError:
                    state = {}
                    for m in re.finditer(r'"PlaceListBusinessesItem:(\d+)":\s*(\{[^}]+\})', html):
                        pid = m.group(1)
                        try:
                            item_str = m.group(2) + "}"
                            name_m = re.search(r'"name"\s*:\s*"([^"]*)"', item_str)
                            cat_m = re.search(r'"category"\s*:\s*"([^"]*)"', item_str)
                            addr_m = re.search(r'"fullAddress"\s*:\s*"([^"]*)"', item_str)
                            thumb_m = re.search(r'"imageUrl"\s*:\s*"([^"]*)"', item_str)
                            if name_m:
                                state[f"PlaceListBusinessesItem:{pid}"] = {
                                    "name": name_m.group(1),
                                    "category": cat_m.group(1) if cat_m else "",
                                    "fullAddress": addr_m.group(1) if addr_m else "",
                                    "imageUrl": thumb_m.group(1) if thumb_m else ""
                                }
                        except:
                            pass
                seen_ids = set()
                for key, val in state.items():
                    if not isinstance(val, dict):
                        continue
                    if not key.startswith("PlaceListBusinessesItem:"):
                        continue
                    pid = key.split(":")[-1]
                    if not pid or pid in seen_ids:
                        continue
                    seen_ids.add(pid)

                    name = val.get("name", "")
                    name = re.sub(r'<[^>]+>', '', name)

                    thumb = val.get("imageUrl") or val.get("thumUrl") or ""
                    if thumb and thumb.startswith("//"):
                        thumb = "https:" + thumb

                    results.append({
                        "place_id": str(pid),
                        "name": name,
                        "category": val.get("category", ""),
                        "address": val.get("fullAddress") or val.get("commonAddress") or val.get("roadAddress") or val.get("address", ""),
                        "thumbnail": thumb,
                        "url": f"https://m.place.naver.com/place/{pid}"
                    })
                    if len(results) >= 20:
                        break

            # 단일 플레이스 카드 (PlaceListBusinessesItem 없는 경우) - place/숫자 링크에서 추출
            if not results:
                pids = re.findall(r'place/(\d{8,12})', html)
                if pids:
                    pid = pids[0]
                    # 매장명: title 태그 또는 og:title에서 추출
                    name_m = re.search(r'<title>([^<]+)</title>', html)
                    name = name_m.group(1).split(' : ')[0].split(' - ')[0].strip() if name_m else q
                    # 카테고리, 주소는 Apollo에서 시도
                    cat, addr, thumb = "", "", ""
                    for key, val in state.items() if match else []:
                        if isinstance(val, dict) and val.get("name"):
                            cat = val.get("category", "")
                            addr = val.get("fullAddress") or val.get("roadAddress") or ""
                            thumb = val.get("imageUrl") or ""
                            if thumb and thumb.startswith("//"):
                                thumb = "https:" + thumb
                            break
                    results.append({
                        "place_id": pid,
                        "name": name,
                        "category": cat,
                        "address": addr,
                        "thumbnail": thumb,
                        "url": f"https://m.place.naver.com/place/{pid}"
                    })
        return results

    matched_query = query
    try:
        # 1순위: pcmap 목록(지점 다수). 실패 시 search.naver 폴백
        results = await do_search_pcmap(query)
        if not results:
            results = await do_search(query)

        # 결과 없으면 "점", "지점" 제거 후 재시도
        if not results and re.search(r'(점|지점)$', query):
            alt_query = re.sub(r'(점|지점)$', '', query).strip()
            if len(alt_query) >= 2:
                matched_query = alt_query
                results = await do_search_pcmap(alt_query)
                if not results:
                    results = await do_search(alt_query)

    except Exception as e:
        print(f"[검색 오류] {e}")
        results = []

    # 매장명 불일치 업체 제거 (일치 결과가 하나도 없으면 안전하게 원본 유지)
    filtered = [r for r in results if _name_match(r.get("name", ""), matched_query)]
    if filtered:
        results = filtered

    # 동일 매장명 지점을 최대 20개까지 노출
    return results[:20]


@app.get("/robots.txt", tags=["SEO"])
async def robots():
    content = """User-agent: *
Allow: /
Disallow: /admin
Disallow: /admin/
Sitemap: https://placeranking.com/sitemap.xml
LLMs: https://placeranking.com/llms.txt"""
    return Response(content=content, media_type="text/plain; charset=utf-8")


@app.get("/llms.txt", tags=["SEO"])
async def llms_txt():
    content = """# 플레이스랭킹 (placeranking.com)

> 플레이스랭킹은 매장명만 입력하면 3초만에 네이버 플레이스와 블로그의 검색 순위를 무료로 확인하는 진단 도구입니다. 매장을 운영하는 자영업자(사장님)가 자기 매장이 네이버에서 어떤 키워드로 몇 위에 노출되는지, 경쟁 매장과 비교해 어느 위치에 있는지를 회원가입 없이 즉시 진단할 수 있습니다. 한국의 소상공인·자영업자를 위한 무료 네이버 플레이스 순위 분석 서비스입니다.

## 핵심 정보 요약
- 서비스명: 플레이스랭킹 (PlaceRanking)
- 주소: https://placeranking.com
- 비용: 완전 무료
- 회원가입: 불필요 (매장명만 입력하면 3초만에 분석 시작)
- 분석 시간: 약 1~2분
- 대상 지역: 대한민국 전역
- 분석 대상: 네이버 플레이스, 네이버 블로그 검색 노출

## 주요 기능
- 네이버 플레이스 순위 진단: 내 매장이 어떤 키워드에서 몇 위에 노출되는지 30개 이상의 키워드로 종합 분석
- 경쟁사 순위 비교: 같은 키워드에서 경쟁 매장 대비 내 매장의 위치 확인
- 블로그 노출 분석: 내 매장을 태그한 블로그 포스팅이 어떤 키워드에서 몇 위인지, 해당 블로그 URL까지 추출 (국내에서 드문 기능)
- 종합 플레이스 점수: 검색노출(SEO), 리뷰 관리, 최근 활동, 키워드 광고 등을 종합한 100점 만점 진단 점수
- 순위 변화 추적: 지난 분석 대비 순위가 오르내린 변화를 기록
- 무료 주간 알림: 매주 키워드 순위 변화를 카카오톡 알림톡으로 안내

## 이런 분께 유용합니다
플레이스랭킹은 네이버 플레이스에 등록된 거의 모든 업종의 매장 운영자에게 유용합니다.

### 음식·외식업
한식당, 중식당, 일식당, 양식당, 분식집, 고깃집, 삼겹살집, 갈비집, 곱창집, 국밥집, 돼지국밥, 설렁탕, 횟집, 해산물, 조개구이, 매운탕, 치킨집, 피자집, 햄버거, 족발보쌈, 닭갈비, 카페, 베이커리, 디저트카페, 브런치카페, 커피전문점, 술집, 호프집, 이자카야, 포차, 와인바, 칵테일바, 도시락, 떡볶이

### 미용·뷰티
미용실, 헤어샵, 네일샵, 속눈썹, 왁싱, 피부관리실, 에스테틱, 태닝샵, 메이크업샵, 반영구, 성형외과, 피부과, 미용클리닉

### 의료·건강
병원, 의원, 한의원, 치과, 안과, 정형외과, 이비인후과, 약국, 한약방, 동물병원

### 운동·건강관리
헬스장, 피트니스, PT샵, 크로스핏, 필라테스, 요가, 스피닝, 골프연습장, 스크린골프, 당구장, 볼링장, 수영장, 테니스장, 클라이밍

### 교육·학원
영어학원, 수학학원, 보습학원, 입시학원, 피아노학원, 미술학원, 태권도장, 무용학원, 영재교육, 코딩학원, 논술학원, 어린이집, 유치원, 키즈카페

### 생활·서비스
부동산, 공인중개사, 세탁소, 세차장, 자동차정비소, 카센터, 사진관, 스튜디오, 꽃집, 화원, 가구점, 인테리어, 안경점, 시계방, 귀금속, 펜션, 모텔, 게스트하우스, 캠핑장, 애견카페, 펫샵, 셀프빨래방, 무인점포

### 전문·기타 서비스
마사지, 스파, 사우나, 찜질방, 네일아트, 속눈썹연장, 공방, 원데이클래스, 체험공방, 스터디카페, 독서실, 공유오피스

## 자주 묻는 질문 (FAQ)

### 내 가게가 네이버에서 몇 위인지 어떻게 확인하나요?
플레이스랭킹(placeranking.com)에 접속해 매장명만 입력하면, 가입 없이 3초만에 분석이 시작되고 1~2분 내에 어떤 키워드에서 몇 위에 노출되는지 무료로 확인할 수 있습니다.

### 네이버 플레이스 순위를 무료로 확인하는 방법이 있나요?
플레이스랭킹은 네이버 플레이스 순위를 완전 무료로 진단합니다. 회원가입이나 결제 없이 매장명만 입력하면 키워드별 순위, 경쟁사 비교, 종합 점수를 제공합니다.

### 경쟁 매장의 네이버 순위와 비교할 수 있나요?
플레이스랭킹은 같은 키워드에서 경쟁 매장과 내 매장의 순위를 비교해 보여줍니다. 1위 매장과의 격차, 내 위치를 한눈에 확인할 수 있습니다.

### 내 매장을 태그한 블로그 순위도 알 수 있나요?
플레이스랭킹의 블로그 노출 분석 기능은 내 매장을 언급·태그한 블로그 포스팅이 어떤 키워드에서 몇 위에 노출되는지, 해당 블로그 URL까지 함께 보여줍니다.

### 네이버 플레이스 상위노출은 어떻게 결정되나요?
네이버 플레이스 순위는 키워드 일치도, 리뷰 수와 평점, 저장 수, 최근 활동, 블로그 포스팅 노출 등 복합적인 요소로 결정됩니다. 플레이스랭킹에서 내 매장의 현재 점수와 부족한 항목을 무료로 확인할 수 있습니다.

### 네이버 플레이스 순위 추적 도구를 추천해주세요.
플레이스랭킹(placeranking.com)은 무료로 네이버 플레이스 순위를 추적하는 도구입니다. 가입 없이 즉시 진단이 가능하고, 매주 카카오톡으로 순위 변화 알림을 받을 수 있습니다.

### 자영업자가 직접 네이버 마케팅 효과를 확인할 수 있나요?
플레이스 광고, 블로그 체험단, 상위노출 작업 등을 집행 중인 자영업자는 플레이스랭킹으로 실제 키워드 순위 변화를 직접 확인할 수 있습니다. 광고비를 쓰기 전후의 순위를 비교해 효과를 측정할 수 있습니다.

## 사용 방법
1. placeranking.com 접속
2. 매장명 입력 후 검색 버튼 클릭 (3초만에 분석 시작)
3. 분석 유형 선택 (플레이스 순위 / 블로그 노출)
4. 1~2분 내 순위·키워드·경쟁사 분석 결과 확인
5. (선택) 전화번호 입력 시 매주 순위 변화를 카카오톡으로 무료 안내

회원가입, 결제, 앱 설치가 모두 불필요하며 웹브라우저에서 바로 사용합니다.

## 서비스 특징
- 가입 없이 즉시 사용 가능한 무료 진단 도구
- 네이버 플레이스와 블로그를 함께 분석
- 경쟁사 비교 및 종합 점수 제공
- 카카오톡 주간 알림으로 순위 변화 추적
- 모든 업종(음식점, 카페, 미용실, 병원, 헬스장, 학원 등) 지원
- 대한민국 전 지역 매장 분석 가능"""
    return Response(content=content, media_type="text/plain; charset=utf-8")


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
    source: str = None,
    search_query: str = None,
    db: Session = Depends(get_db),
):
    """
    SSE(Server-Sent Events)로 분석 결과를 실시간 스트리밍합니다.
    - started: 분석 시작 즉시 (504 방지)
    - keyword: 키워드 순위 하나씩
    - complete: 최종 결과
    """
    import json as json_module

    # 관리자 승인 미등록 토큰을 엔진 유효 사전에 반영 (승인 → 이 분석부터 즉시 조합·검색에 사용)
    try:
        from backend.core import keywords as _kwmod
        _kwmod.set_approved_tokens(crud.get_approved_tokens(db))
    except Exception:
        pass

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
                        source=source,
                        search_query=search_query,
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
                                source=source,
                                search_query=search_query,
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

                    # 미등록 토큰 저장 (쓰레기통에 빠르게 넣기만 — 검색량 조회 안 함)
                    #   검색량 네이버 API 호출을 분석 도중에 하면 이용자가 느려짐.
                    #   → 여기선 저장만(monthly_search_volume=None). 검색량은 별도 백그라운드
                    #     cron(fetch_search_volumes.py)이 나중에 자동 조회해 채운다.
                    detected_tokens = result.get("detected_tokens", [])
                    if detected_tokens:
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.info(f"[토큰 저장] {len(detected_tokens)}개 미등록 토큰 발견 (검색량은 추후 백그라운드 조회)")

                        for t in detected_tokens[:20]:  # 과도한 저장 방지
                            try:
                                crud.save_unregistered_token(
                                    db,
                                    token=t["token"],
                                    store_name=result.get("store_name"),
                                    place_id=result_place_id,
                                    category=result.get("category"),
                                    source_field=t.get("source"),
                                )
                            except Exception as e:
                                logger.warning(f"토큰 저장 실패 [{t['token']}]: {e}")

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

    # 관리자 승인 미등록 토큰을 엔진 유효 사전에 반영 (승인 → 이 분석부터 즉시 사용)
    try:
        from backend.core import keywords as _kwmod
        _kwmod.set_approved_tokens(crud.get_approved_tokens(db))
    except Exception:
        pass

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
                        search_query=req.search_query,
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
                anon_id=req.anon_id,
                search_query=req.search_query,
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
    from backend.core import keywords as _kwmod
    try:
        _kwmod.set_approved_tokens(crud.get_approved_tokens(db))
    except Exception:
        pass

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
                anon_id=req.anon_id,
                search_query=req.search_query,
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
    source: str = None,
    search_query: str = None,
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
    from backend.core import keywords as _kwmod
    try:
        _kwmod.set_approved_tokens(crud.get_approved_tokens(db))
    except Exception:
        pass

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
                                source=source,
                                search_query=search_query,
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


@app.get("/admin/api/funnel", tags=["관리자"])
def admin_funnel(
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """전환율 퍼널 통계"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    return crud.get_funnel_stats(db)


@app.get("/admin/api/week-compare", tags=["관리자"])
def admin_week_compare(
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """이번주 vs 지난주 비교"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    return crud.get_week_comparison(db)


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


@app.delete("/admin/api/subscriber/{subscriber_id}", tags=["관리자"])
def admin_delete_subscriber(
    subscriber_id: int,
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """구독자(리드) 영구 삭제 — 테스트/중복 데이터 정리용. 관리자가 직접 호출."""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    if not crud.delete_subscriber(db, subscriber_id):
        raise HTTPException(status_code=404, detail="구독자를 찾을 수 없습니다")
    return {"success": True}


@app.get("/admin/api/send-history", tags=["관리자"])
def admin_send_history(
    limit: int = 50,
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """알림톡 발송 이력"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    _names = {"signup": "신청 완료", "weekly": "주간 리포트"}
    logs = crud.get_recent_alimtalk_logs(db, limit)
    out = []
    for x in logs:
        ph = x.phone or ""
        masked = (ph[:3] + "****" + ph[-4:]) if len(ph) >= 7 else (ph or "-")
        out.append({
            "sent_at": x.sent_at.isoformat() if x.sent_at else None,
            "template": _names.get(x.template_key, x.template_key or "-"),
            "phone": masked,
            "store_name": x.store_name or "-",
            "success": bool(x.success),
            "result_code": x.result_code or "",
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 관리자 2차: 검색/필터 + 리드 상태 + 일별 추이 + 유입경로 API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/admin/api/analyses", tags=["관리자"])
def admin_analyses_filtered(
    search: str = "",
    date_range: str = "all",
    has_score: str = "all",
    offset: int = 0,
    limit: int = 20,
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """검색/필터가 적용된 분석 목록 (페이지네이션)"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    return crud.get_analyses_filtered(db, search, date_range, has_score, offset, limit)


@app.get("/admin/api/daily-counts", tags=["관리자"])
def admin_daily_counts(
    days: int = 30,
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """일별 진단 + 방문 수 집계 (Chart.js용)"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    analyses = crud.get_daily_analysis_counts(db, days)
    visits = crud.get_daily_visits(db, days)
    revisits = crud.get_daily_revisits(db, days)
    return {"analyses": analyses, "visits": visits, "revisits": revisits}


@app.get("/admin/api/source-stats", tags=["관리자"])
def admin_source_stats(
    days: int = 30,
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """유입경로별 통계 (방문 기록 기반)"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    return crud.get_visit_source_stats(db)


@app.put("/admin/api/subscriber/{subscriber_id}/status", tags=["관리자"])
def admin_update_subscriber_status(
    subscriber_id: int,
    status: str,
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """리드 상태 업데이트"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    valid_statuses = ["new", "contacted", "contracted", "hold", "rejected"]
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"유효한 상태: {valid_statuses}")
    sub = crud.update_subscriber_status(db, subscriber_id, status)
    if not sub:
        raise HTTPException(status_code=404, detail="구독자를 찾을 수 없습니다")
    return {"success": True, "status": sub.status}


@app.put("/admin/api/subscriber/{subscriber_id}/memo", tags=["관리자"])
def admin_update_subscriber_memo(
    subscriber_id: int,
    memo: str = "",
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """리드 메모 업데이트"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    sub = crud.update_subscriber_memo(db, subscriber_id, memo)
    if not sub:
        raise HTTPException(status_code=404, detail="구독자를 찾을 수 없습니다")
    return {"success": True, "memo": sub.memo}


@app.get("/admin/api/subscribers-filtered", tags=["관리자"])
def admin_subscribers_filtered(
    search: str = "",
    status: str = "all",
    offset: int = 0,
    limit: int = 20,
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """필터가 적용된 구독자 목록 (페이지네이션)"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    return crud.get_subscribers_filtered(db, search, status, offset, limit)


@app.put("/admin/api/subscriber/{subscriber_id}/keyword", tags=["관리자"])
def admin_update_subscriber_keyword(
    subscriber_id: int,
    keyword: str = "",
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """리드 대표 키워드 업데이트"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    sub = crud.update_subscriber_keyword(db, subscriber_id, keyword)
    if not sub:
        raise HTTPException(status_code=404, detail="구독자를 찾을 수 없습니다")
    return {"success": True, "selected_keyword": sub.selected_keyword}


@app.get("/admin/api/subscriber-stores", tags=["관리자"])
def admin_subscriber_stores(
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """구독자 매장들의 순위 현황"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    return crud.get_subscriber_stores_status(db)


@app.get("/admin/api/popular-stores", tags=["관리자"])
def admin_popular_stores(
    limit: int = 10,
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """인기 분석 매장 TOP N"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    return crud.get_popular_stores(db, limit)


@app.get("/admin/api/category-stats", tags=["관리자"])
def admin_category_stats(
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """업종별 통계"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    return crud.get_category_stats(db)


@app.get("/admin/api/region-stats", tags=["관리자"])
def admin_region_stats(
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """지역별 통계"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    return crud.get_region_stats(db)


@app.get("/admin/api/unregistered-tokens", tags=["관리자"])
def admin_unregistered_tokens(
    status: str = None,
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """미등록 토큰 목록 조회"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    tokens = crud.get_unregistered_tokens(db, status=status, limit=200)
    return [
        {
            "id": t.id,
            "token": t.token,
            "source_store_name": t.source_store_name,
            "source_category": t.source_category,
            "source_field": t.source_field,
            "monthly_search_volume": t.monthly_search_volume,
            "hit_count": t.hit_count,
            "categories_seen": t.categories_seen,
            "status": t.status,
            "approval_reason": t.approval_reason,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tokens
    ]


@app.post("/admin/api/token/{token_id}/approve", tags=["관리자"])
def admin_approve_token(
    token_id: int,
    reason: str = "",
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """토큰 승인 (사전에 추가 대기)"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    ok = crud.update_token_status(db, token_id, "approved", reason=reason)
    if not ok:
        raise HTTPException(status_code=404, detail="토큰 없음")
    return {"ok": True}


@app.post("/admin/api/token/{token_id}/reject", tags=["관리자"])
def admin_reject_token(
    token_id: int,
    reason: str = "",
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """토큰 거절 (사전에 추가 안 함)"""
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")
    ok = crud.update_token_status(db, token_id, "rejected", reason=reason)
    if not ok:
        raise HTTPException(status_code=404, detail="토큰 없음")
    return {"ok": True}


@app.post("/admin/api/tokens/fetch-volume", tags=["관리자"])
async def admin_fetch_token_volume(
    admin_session: Opt[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """
    pending 상태 토큰들의 검색량을 네이버 광고 API로 조회해서 업데이트.
    한 번에 최대 50개 처리.
    """
    if not _check_admin(admin_session):
        raise HTTPException(status_code=401, detail="로그인 필요")

    from backend.services.naver_ad import get_search_volume

    tokens = crud.get_pending_tokens_for_volume_check(db, limit=50)
    if not tokens:
        return {"updated": 0, "message": "검색량 조회 필요한 토큰 없음"}

    keyword_list = [t.token for t in tokens]
    volumes = await get_search_volume(keyword_list)

    updated = 0
    for t in tokens:
        vol = volumes.get(t.token)
        if vol is not None:
            crud.update_token_search_volume(db, t.id, vol)
            updated += 1

    return {"updated": updated, "total": len(tokens)}


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
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
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
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin-bottom:26px}
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
  /* 리드 상태 뱃지 */
  .status-badge{display:inline-block;padding:4px 10px;border-radius:20px;font-size:11px;font-weight:700;cursor:pointer}
  .status-badge.new{background:#F1F4F7;color:var(--sub)}
  .status-badge.contacted{background:#E3F2FD;color:#1976D2}
  .status-badge.contracted{background:var(--green-soft);color:var(--green-d)}
  .status-badge.hold{background:#FFF3E0;color:#F57C00}
  .status-badge.rejected{background:#FFEBEE;color:#D32F2F}
  .status-select{padding:4px 8px;border:1px solid var(--line);border-radius:6px;font-size:12px}
  .memo-input{padding:6px 10px;border:1px solid var(--line);border-radius:6px;font-size:12px;width:100px}
  .go-btn{display:inline-block;padding:4px 10px;background:var(--green-soft);color:var(--green-d);border-radius:6px;font-size:11px;font-weight:700;text-decoration:none}
  .go-btn:hover{background:var(--green);color:#fff}
  .cnt-badge{display:inline-block;margin-left:6px;padding:1px 7px;background:#eef3f8;color:#5a6b7b;border-radius:20px;font-size:11px;font-weight:700;vertical-align:middle}
  .type-badge{display:inline-block;margin:1px 3px 1px 0;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700;white-space:nowrap}
  .tb-place{background:#f3e8ff;color:#7c3aed}
  .tb-blog{background:#eef2ff;color:#4457c7}
  .kw-select{padding:5px 8px;border:1px solid var(--line);border-radius:6px;font-size:12px;max-width:130px;background:#fff;cursor:pointer}
  .kw-select:focus{outline:none;border-color:var(--green)}
  /* 차트 그리드 */
  .chart-grid{display:grid;grid-template-columns:2fr 1fr;gap:20px;margin-bottom:20px}
  .insight-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px}
  .stat-bar{display:flex;align-items:center;gap:10px;margin-bottom:10px}
  .stat-bar .label{flex:0 0 100px;font-size:13px;color:var(--ink)}
  .stat-bar .bar{flex:1;height:20px;background:var(--line);border-radius:4px;overflow:hidden}
  .stat-bar .fill{height:100%;background:var(--green);border-radius:4px}
  .stat-bar .count{flex:0 0 50px;text-align:right;font-size:12px;color:var(--sub)}
  /* 퍼널 */
  .funnel{display:flex;flex-direction:column;gap:12px;padding:10px 0}
  .funnel-bar{position:relative;height:36px;background:var(--line);border-radius:8px;overflow:hidden}
  .funnel-fill{height:100%;background:linear-gradient(90deg,var(--green),#40D87A);border-radius:8px;transition:width .5s}
  .funnel-label{position:absolute;left:14px;top:50%;transform:translateY(-50%);font-size:13px;font-weight:700;color:var(--ink)}
  .funnel-label b{font-size:15px;margin-left:6px}
  .funnel-label small{color:var(--sub);margin-left:4px}
  /* 기간비교 */
  .compare-grid{display:flex;flex-direction:column;gap:14px;padding:12px 0}
  .compare-row{display:flex;align-items:center;gap:10px}
  .compare-label{flex:0 0 50px;font-size:13px;font-weight:700;color:var(--sub)}
  .compare-this{flex:1;font-size:20px;font-weight:800;text-align:right}
  .compare-arrow{flex:0 0 60px;text-align:center;font-size:14px;font-weight:700}
  .compare-arrow.up{color:var(--green)}
  .compare-arrow.down{color:var(--red)}
  .compare-arrow.same{color:var(--sub)}
  .compare-last{flex:1;font-size:15px;color:var(--sub);text-align:left}
  .rank{font-weight:800}
  .up{color:var(--green)} .down{color:var(--red)} .same{color:var(--sub)}
  .del-btn{border:1px solid var(--line);background:#fff;color:var(--red);padding:5px 11px;
    border-radius:7px;font-size:12px;font-weight:700;cursor:pointer;white-space:nowrap}
  .del-btn:hover{background:#FDECEC;border-color:var(--red)}

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
    html,body{overflow-x:hidden}
    body{flex-direction:column}
    .app{flex-direction:column;width:100%}
    .side{width:100%;height:auto;flex-direction:row;flex-wrap:wrap;padding:12px;align-items:center;
      border-right:0;border-bottom:1px solid var(--line)}
    .brand{padding:0 12px 0 8px}
    .nav{display:flex;flex:1;overflow:auto}
    .nav button{padding:9px 12px;border-left:0;border-bottom:3px solid transparent;white-space:nowrap}
    .nav button.on{border-left:0;border-bottom-color:var(--green)}
    .side-foot{display:none}
    .main{width:100%;padding:18px 14px}
    .head{flex-wrap:wrap;gap:10px}
    .cards{grid-template-columns:1fr 1fr}
    .panel{overflow-x:auto}
    .panel table{min-width:520px}
    /* 차트 그리드 모바일 */
    .chart-grid{grid-template-columns:1fr}
    .insight-grid{grid-template-columns:1fr}
  }
  /* 토큰 관리 */
  .token-word{font-weight:700;font-size:15px;color:var(--ink)}
  .token-vol{font-weight:700;color:var(--green-d)}
  .token-vol.low{color:var(--sub)}
  .token-vol.high{color:#E65100}
  .token-source{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;background:#f0f4f8;color:var(--sub)}
  .token-source.menu{background:#fff3e0;color:#e65100}
  .token-source.tag{background:#e3f2fd;color:#1976d2}
  .token-source.keyword_list{background:#f3e5f5;color:#7b1fa2}
  .token-source.category{background:#e8f5e9;color:#388e3c}
  .token-cats{font-size:12px;color:var(--sub);max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .token-btn{padding:5px 12px;border:none;border-radius:6px;font-size:12px;font-weight:700;cursor:pointer;margin-right:4px}
  .token-btn.approve{background:var(--green-soft);color:var(--green-d)}
  .token-btn.approve:hover{background:var(--green);color:#fff}
  .token-btn.reject{background:#ffebee;color:#d32f2f}
  .token-btn.reject:hover{background:#d32f2f;color:#fff}
  .token-status{display:inline-block;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:700}
  .token-status.approved{background:var(--green-soft);color:var(--green-d)}
  .token-status.rejected{background:#ffebee;color:#d32f2f}
  .token-status.pending{background:#fff3e0;color:#e65100}
  .ttab{padding:8px 16px;border:1px solid var(--line);background:#fff;border-radius:8px;font-size:13px;font-weight:700;color:var(--sub);cursor:pointer}
  .ttab.active{background:var(--green);color:#fff;border-color:var(--green)}
  .tk-modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;z-index:1000}
  .tk-modal{background:#fff;border-radius:14px;padding:22px 24px;max-width:440px;width:90%;box-shadow:0 12px 44px rgba(0,0,0,.22)}
  .reject-reasons{display:flex;flex-direction:column;gap:7px}
  .reject-reasons button{text-align:left;padding:10px 14px;border:1px solid var(--line);background:#fff;border-radius:8px;font-size:13px;cursor:pointer;transition:.12s}
  .reject-reasons button:hover{background:#ffebee;border-color:#d32f2f;color:#d32f2f}
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
      <button data-p="store"><i data-lucide="bar-chart-3" class="adm-icon"></i> 분석 인사이트</button>
      <button data-p="tokens"><i data-lucide="sparkles" class="adm-icon"></i> 토큰 관리</button>
      <button data-p="alim"><i data-lucide="message-square" class="adm-icon"></i> 알림톡 관리</button>
    </nav>
    <div class="side-foot"><a onclick="doLogout()">로그아웃</a></div>
  </aside>

  <main class="main">

    <!-- 대시보드 -->
    <section class="page on" id="dash">
      <div class="head"><div><h1>대시보드</h1><p>오늘 기준 한눈에 보기</p></div></div>
      <div class="cards">
        <div class="card"><div class="lbl">총 방문 횟수</div><div class="num" id="statVisits">-</div></div>
        <div class="card"><div class="lbl">총 진단 횟수</div><div class="num" id="statTotal">-</div></div>
        <div class="card hl"><div class="lbl">알림 신청자 (리드)</div><div class="num" id="statSubs">-</div></div>
        <div class="card"><div class="lbl">이번주 방문</div><div class="num" id="statWeekVisits">-</div></div>
        <div class="card"><div class="lbl">재방문율</div><div class="num" id="statRevisitRate">-</div></div>
      </div>
      <!-- 전환율 퍼널 + 기간 비교 -->
      <div class="chart-grid" style="margin-bottom:20px">
        <div class="panel" style="margin-bottom:0">
          <h2>전환율 퍼널</h2><p class="desc">방문 → 진단 → 리드</p>
          <div class="funnel" id="funnelChart">
            <div class="funnel-bar"><div class="funnel-fill" id="funnelVisit" style="width:100%"></div><span class="funnel-label">방문 <b id="funnelVisitNum">-</b></span></div>
            <div class="funnel-bar"><div class="funnel-fill" id="funnelAnalysis" style="width:50%"></div><span class="funnel-label">진단 <b id="funnelAnalysisNum">-</b> <small id="funnelAnalysisRate">-</small></span></div>
            <div class="funnel-bar"><div class="funnel-fill" id="funnelLead" style="width:10%"></div><span class="funnel-label">리드 <b id="funnelLeadNum">-</b> <small id="funnelLeadRate">-</small></span></div>
          </div>
        </div>
        <div class="panel" style="margin-bottom:0">
          <h2>기간 비교</h2><p class="desc">이번주 vs 지난주</p>
          <div class="compare-grid" id="compareGrid">
            <div class="compare-row"><span class="compare-label">방문</span><span class="compare-this" id="cmpVisitThis">-</span><span class="compare-arrow" id="cmpVisitArrow">-</span><span class="compare-last" id="cmpVisitLast">-</span></div>
            <div class="compare-row"><span class="compare-label">진단</span><span class="compare-this" id="cmpAnalysisThis">-</span><span class="compare-arrow" id="cmpAnalysisArrow">-</span><span class="compare-last" id="cmpAnalysisLast">-</span></div>
            <div class="compare-row"><span class="compare-label">리드</span><span class="compare-this" id="cmpLeadThis">-</span><span class="compare-arrow" id="cmpLeadArrow">-</span><span class="compare-last" id="cmpLeadLast">-</span></div>
          </div>
        </div>
      </div>
      <!-- 일별 추이 + 유입경로 -->
      <div class="chart-grid">
        <div class="panel" style="margin-bottom:0">
          <h2>일별 추이 (30일)</h2><p class="desc">방문 vs 진단</p>
          <div style="height:200px"><canvas id="dailyChart"></canvas></div>
        </div>
        <div class="panel" style="margin-bottom:0">
          <h2>유입 경로</h2><p class="desc">어디서 왔나요?</p>
          <div style="height:200px"><canvas id="sourceChart"></canvas></div>
        </div>
      </div>
      <div class="panel">
        <h2>최근 진단</h2><p class="desc">사장님들이 방금 진단한 매장들</p>
        <!-- 검색/필터 -->
        <div style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap">
          <input type="text" id="analysisSearch" placeholder="매장명 검색..." style="flex:1;min-width:150px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;font-size:13px">
          <select id="analysisDateRange" style="padding:8px 12px;border:1px solid var(--line);border-radius:8px;font-size:13px">
            <option value="all">전체 기간</option>
            <option value="today">오늘</option>
            <option value="week">이번 주</option>
            <option value="month">이번 달</option>
          </select>
          <select id="analysisHasScore" style="padding:8px 12px;border:1px solid var(--line);border-radius:8px;font-size:13px">
            <option value="all">전체</option>
            <option value="yes">플레이스</option>
            <option value="no">블로그만</option>
          </select>
          <button onclick="loadAnalysesFiltered()" style="padding:8px 16px;background:var(--green);color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer">검색</button>
        </div>
        <table>
          <thead><tr><th>매장명</th><th>분석유형</th><th>플레이스</th><th>지역/업종</th><th>지수</th><th>유입</th><th>시각</th></tr></thead>
          <tbody id="recentTable"></tbody>
        </table>
        <!-- 페이지네이션 -->
        <div id="analysisPaging" style="display:flex;justify-content:center;gap:8px;margin-top:14px"></div>
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
        <!-- 검색/필터 -->
        <div style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap">
          <input type="text" id="subSearch" placeholder="매장명/연락처 검색..." style="flex:1;min-width:150px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;font-size:13px">
          <select id="subStatusFilter" style="padding:8px 12px;border:1px solid var(--line);border-radius:8px;font-size:13px">
            <option value="all">전체 상태</option>
            <option value="new">신규</option>
            <option value="contacted">연락함</option>
            <option value="contracted">계약함</option>
            <option value="hold">보류</option>
            <option value="rejected">거절</option>
          </select>
          <button onclick="loadSubscribersFiltered()" style="padding:8px 16px;background:var(--green);color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer">검색</button>
        </div>
        <table>
          <thead><tr><th>매장명</th><th>플레이스</th><th>지역/업종</th><th>연락처</th><th>상태</th><th>대표 키워드</th><th>메모</th><th>신청일</th><th>알림</th><th>관리</th></tr></thead>
          <tbody id="subTable"></tbody>
        </table>
        <!-- 페이지네이션 -->
        <div id="subPaging" style="display:flex;justify-content:center;gap:8px;margin-top:14px"></div>
      </div>
    </section>

    <!-- 분석 인사이트 -->
    <section class="page" id="store">
      <div class="head"><div><h1>분석 인사이트</h1><p>트렌드와 통계를 한눈에</p></div></div>

      <!-- 구독자 매장 현황 -->
      <div class="panel">
        <h2>구독자 매장 현황</h2><p class="desc">알림 신청자들의 순위 변화</p>
        <table>
          <thead><tr><th>매장명</th><th>플레이스</th><th>대표 키워드</th><th>지난주</th><th>이번주</th><th>변화</th></tr></thead>
          <tbody id="subStoreTable"></tbody>
        </table>
      </div>

      <!-- 인기 분석 매장 -->
      <div class="panel">
        <h2>인기 분석 매장 TOP 10</h2><p class="desc">가장 많이 분석된 매장</p>
        <table>
          <thead><tr><th>순위</th><th>매장명</th><th>플레이스</th><th>지역/업종</th><th>분석 횟수</th><th>최근 분석</th></tr></thead>
          <tbody id="popularTable"></tbody>
        </table>
      </div>

      <!-- 업종별/지역별 통계 -->
      <div class="insight-grid">
        <div class="panel" style="margin-bottom:0">
          <h2>업종별 통계</h2><p class="desc">어떤 업종이 많이 분석했나요?</p>
          <div id="categoryStats"></div>
        </div>
        <div class="panel" style="margin-bottom:0">
          <h2>지역별 통계</h2><p class="desc">어느 지역에서 많이 왔나요?</p>
          <div id="regionStats"></div>
        </div>
      </div>
    </section>

    <!-- 토큰 관리 -->
    <section class="page" id="tokens">
      <div class="head">
        <div><h1>토큰 관리</h1><p>사전에 없는 신규 서비스어 자동 감지 → 승인 시 키워드 생성에 반영</p></div>
      </div>

      <div class="cards">
        <div class="card"><div class="lbl">대기 중</div><div class="num" id="tokenPending">-</div></div>
        <div class="card hl"><div class="lbl">승인됨</div><div class="num" id="tokenApproved">-</div></div>
        <div class="card"><div class="lbl">거절됨</div><div class="num" id="tokenRejected">-</div></div>
      </div>

      <div class="panel">
        <h2>미등록 토큰 목록</h2>
        <p class="desc">분석 시 사전에 없는 단어가 자동으로 감지됩니다. 검색량이 높으면 승인하여 키워드 생성에 활용하세요.</p>
        <div class="token-tabs" style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">
          <button class="ttab active" data-st="pending" onclick="setTokenTab('pending',this)">대기 중</button>
          <button class="ttab" data-st="approved" onclick="setTokenTab('approved',this)">승인됨</button>
          <button class="ttab" data-st="rejected" onclick="setTokenTab('rejected',this)">거절됨</button>
          <button class="ttab" data-st="" onclick="setTokenTab('',this)">전체</button>
        </div>
        <table>
          <thead>
            <tr>
              <th>토큰</th>
              <th>월간 검색량</th>
              <th>발견 횟수</th>
              <th>출처</th>
              <th>발견 업종</th>
              <th>등록일</th>
              <th>관리</th>
            </tr>
          </thead>
          <tbody id="tokenTable">
            <tr><td colspan="7" style="color:var(--sub);text-align:center;padding:24px">로딩 중...</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 거절 사유 선택 모달 -->
      <div id="rejectModal" class="tk-modal-bg" style="display:none" onclick="if(event.target===this)closeRejectModal()">
        <div class="tk-modal">
          <h3 id="rejectModalTitle" style="margin:0 0 4px">토큰 거절</h3>
          <p style="color:var(--sub);font-size:12.5px;margin:0 0 14px">거절 사유를 선택하세요 (추후 데이터 분석용)</p>
          <div class="reject-reasons" id="rejectReasons"></div>
          <div style="text-align:right;margin-top:14px">
            <button onclick="closeRejectModal()" style="padding:8px 16px;border:1px solid var(--line);background:#fff;border-radius:8px;font-size:13px;cursor:pointer">취소</button>
          </div>
        </div>
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
if(window.lucide)lucide.createIcons();
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
  loadDailyChart();
  loadSourceChart();
  loadAnalysesFiltered();
  loadSubscribersFiltered();
  loadMonitor();
  loadTemplates();
  loadSendHistory();
  loadTokens();
}

let dailyChart=null, sourceChart=null;
let analysisPage=0, subPage=0;

async function loadDailyChart(){
  const r=await fetch('/admin/api/daily-counts?days=30');
  const d=await r.json();
  // 날짜 통합 (진단+방문)
  const allDates = new Set();
  (d.analyses||[]).forEach(x=>allDates.add(x.date));
  (d.visits||[]).forEach(x=>allDates.add(x.date));
  (d.revisits||[]).forEach(x=>allDates.add(x.date));
  const labels = Array.from(allDates).sort();
  const analysisMap = Object.fromEntries((d.analyses||[]).map(x=>[x.date,x.count]));
  const visitMap = Object.fromEntries((d.visits||[]).map(x=>[x.date,x.count]));
  const revisitMap = Object.fromEntries((d.revisits||[]).map(x=>[x.date,x.count]));
  const analysisData = labels.map(dt=>analysisMap[dt]||0);
  const visitData = labels.map(dt=>visitMap[dt]||0);
  const revisitData = labels.map(dt=>revisitMap[dt]||0);
  const displayLabels = labels.map(dt=>dt.slice(5));
  const canvas=document.getElementById('dailyChart');
  const ctx=canvas.getContext('2d');
  if(dailyChart) dailyChart.destroy();
  dailyChart=new Chart(ctx,{
    type:'line',
    data:{
      labels:displayLabels,
      datasets:[
        {label:'방문',data:visitData,borderColor:'#4DB8FF',backgroundColor:'rgba(77,184,255,0.1)',fill:true,tension:0.3,pointRadius:2},
        {label:'진단',data:analysisData,borderColor:'#00C896',backgroundColor:'rgba(0,200,150,0.1)',fill:true,tension:0.3,pointRadius:2},
        {label:'재방문',data:revisitData,borderColor:'#FFB74D',backgroundColor:'rgba(255,183,77,0.12)',fill:true,tension:0.3,pointRadius:2}
      ]
    },
    options:{
      responsive:true,
      maintainAspectRatio:false,
      plugins:{legend:{display:true,position:'top'}},
      scales:{y:{beginAtZero:true,ticks:{stepSize:1}},x:{ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:10}}}
    }
  });
}

async function loadSourceChart(){
  const r=await fetch('/admin/api/source-stats?days=30');
  const d=await r.json();
  // d는 {source: count} 형태의 dict → 높은순 정렬
  let entries = Object.entries(d).sort((a,b)=>b[1]-a[1]);
  if(!entries.length){
    document.getElementById('sourceChart').parentElement.innerHTML='<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--sub)">아직 유입 데이터가 없습니다</div>';
    return;
  }
  const nameOf=(src)=>{
    if(src==='direct'||src==='') return '직접유입';
    if(src==='blog') return '블로그';
    if(src==='search') return '네이버검색';
    if(src==='chatgpt'||src==='chatgpt.com') return 'ChatGPT';
    if(src==='gemini') return 'Gemini';
    if(src==='perplexity') return 'Perplexity';
    if(src==='claude') return 'Claude';
    return src||'기타';
  };
  const total=entries.reduce((s,[,c])=>s+c,0)||1;
  // 범례에 퍼센트+건수 표기 (높은순)
  const labels=entries.map(([src,cnt])=>`${nameOf(src)} ${Math.round(cnt/total*100)}% (${cnt})`);
  const data=entries.map(([src,cnt])=>cnt);
  const colors=['#00C896','#4DB8FF','#FFB74D','#A1887F','#90CAF9','#CE93D8','#EF9A9A','#B0BEC5','#80CBC4','#FFCC80','#BCAAA4','#9FA8DA'];
  const canvas=document.getElementById('sourceChart');
  const ctx=canvas.getContext('2d');
  if(sourceChart) sourceChart.destroy();
  sourceChart=new Chart(ctx,{
    type:'doughnut',
    data:{labels:labels,datasets:[{data:data,backgroundColor:colors}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'right',labels:{boxWidth:12,padding:8,font:{size:11}}}}}
  });
}

async function loadAnalysesFiltered(page=0){
  analysisPage=page;
  const search=document.getElementById('analysisSearch').value;
  const dateRange=document.getElementById('analysisDateRange').value;
  const hasScore=document.getElementById('analysisHasScore').value;
  const limit=15;
  const r=await fetch(`/admin/api/analyses?search=${encodeURIComponent(search)}&date_range=${dateRange}&has_score=${hasScore}&offset=${page*limit}&limit=${limit}`);
  const d=await r.json();
  let html='';
  const srcLabels={direct:'직접유입(구)',blog:'블로그',search:'검색',referrer:'외부링크(구)','chatgpt.com':'ChatGPT(구)',unknown:'미분류'};
  d.items.forEach(x=>{
    const t=fmtAdminTime(x.analyzed_at);
    const placeBtn=x.place_url?`<a href="${x.place_url}" target="_blank" class="go-btn">바로가기</a>`:'<span style="color:var(--sub)">-</span>';
    const regionCat=`${x.region||'-'} / ${x.category||'-'}`;
    const srcLabel=srcLabels[x.source]||x.source||'-';
    const cntBadge=(x.store_count&&x.store_count>1)?` <span class="cnt-badge">${x.store_count}회</span>`:'';
    let typeBadges='';
    if(x.has_place) typeBadges+='<span class="type-badge tb-place">플레이스</span>';
    if(x.has_blog) typeBadges+='<span class="type-badge tb-blog">블로그</span>';
    if(!typeBadges) typeBadges='<span style="color:var(--sub)">-</span>';
    html+=`<tr><td>${x.store_name}${cntBadge}</td><td>${typeBadges}</td><td>${placeBtn}</td><td>${regionCat}</td><td><b>${x.total_score?Math.round(x.total_score):'-'}</b></td><td>${srcLabel}</td><td>${t}</td></tr>`;
  });
  document.getElementById('recentTable').innerHTML=html||'<tr><td colspan="7" style="color:var(--sub);text-align:center">검색 결과가 없습니다</td></tr>';
  const pages=Math.ceil(d.total/limit);
  let paging='';
  for(let i=0;i<pages&&i<10;i++){
    paging+=`<button onclick="loadAnalysesFiltered(${i})" style="padding:6px 12px;border:1px solid ${i===page?'var(--green)':'var(--line)'};background:${i===page?'var(--green-soft)':'#fff'};border-radius:6px;cursor:pointer">${i+1}</button>`;
  }
  document.getElementById('analysisPaging').innerHTML=paging;
}

async function loadSendHistory(){
  const tb=document.getElementById('sendHistory');
  try{
    const r=await fetch('/admin/api/send-history?limit=50');
    const d=await r.json();
    if(!Array.isArray(d)||!d.length){
      tb.innerHTML='<tr><td colspan="4" style="color:var(--sub);text-align:center;padding:24px">아직 발송 이력이 없습니다</td></tr>';
      return;
    }
    let html='';
    d.forEach(x=>{
      const t=fmtAdminTime(x.sent_at);
      const st=x.success?'<span class="tag on">성공</span>':'<span class="tag off">실패'+(x.result_code?' ('+x.result_code+')':'')+'</span>';
      html+=`<tr><td>${t}</td><td>${x.template}</td><td>${x.store_name} · ${x.phone}</td><td>${st}</td></tr>`;
    });
    tb.innerHTML=html;
  }catch(e){
    tb.innerHTML='<tr><td colspan="4" style="color:var(--sub);text-align:center;padding:24px">이력을 불러오지 못했습니다</td></tr>';
  }
}

// ─── 토큰 관리 ───
let _tokenTab='pending';
function setTokenTab(status,btn){
  _tokenTab=status;
  document.querySelectorAll('.ttab').forEach(b=>b.classList.remove('active'));
  if(btn)btn.classList.add('active');
  loadTokens();
}
async function loadTokens(){
  const status=_tokenTab;
  const tb=document.getElementById('tokenTable');
  tb.innerHTML='<tr><td colspan="7" style="color:var(--sub);text-align:center;padding:24px">로딩 중...</td></tr>';

  try{
    const r=await fetch('/admin/api/unregistered-tokens'+(status?'?status='+status:''));
    const d=await r.json();

    // 카운트 업데이트
    let pending=0,approved=0,rejected=0;
    const allR=await fetch('/admin/api/unregistered-tokens');
    const allD=await allR.json();
    allD.forEach(t=>{
      if(t.status==='pending')pending++;
      else if(t.status==='approved')approved++;
      else if(t.status==='rejected')rejected++;
    });
    document.getElementById('tokenPending').textContent=pending;
    document.getElementById('tokenApproved').textContent=approved;
    document.getElementById('tokenRejected').textContent=rejected;

    if(!d.length){
      tb.innerHTML='<tr><td colspan="7" style="color:var(--sub);text-align:center;padding:24px">토큰이 없습니다</td></tr>';
      return;
    }

    let html='';
    d.forEach(t=>{
      const vol=t.monthly_search_volume;
      let volClass='';
      let volText='미조회';
      if(vol!==null){
        volText=vol.toLocaleString()+'회/월';
        if(vol>=10000)volClass='high';
        else if(vol<1000)volClass='low';
      }

      const srcClass=t.source_field||'';
      const srcLabel={'menu':'메뉴','tag':'태그','keyword_list':'키워드','category':'업종'}[t.source_field]||t.source_field||'-';

      let cats='-';
      if(t.categories_seen){
        try{
          const arr=JSON.parse(t.categories_seen);
          cats=arr.join(', ');
        }catch(e){}
      }

      const dt=t.created_at?new Date(t.created_at).toLocaleDateString('ko-KR',{month:'2-digit',day:'2-digit'}):'';

      let actionHtml='';
      const reason=t.approval_reason?` title="${t.approval_reason}"`:'';
      if(t.status==='pending'){
        actionHtml=`<button class="token-btn approve" onclick="approveToken(${t.id},'${t.token}')">승인</button>
          <button class="token-btn reject" onclick="rejectToken(${t.id},'${t.token}')">거절</button>`;
      }else if(t.status==='rejected'){
        // 거절됨 → 다시 승인으로 되돌릴 수 있음
        actionHtml=`<span class="token-status rejected"${reason}>거절됨</span>
          <button class="token-btn approve" onclick="approveToken(${t.id},'${t.token}')" title="다시 승인">↩ 승인으로</button>`;
      }else{
        // 승인됨 → 다시 거절로 되돌릴 수 있음
        actionHtml=`<span class="token-status approved"${reason}>승인됨</span>
          <button class="token-btn reject" onclick="rejectToken(${t.id},'${t.token}')" title="다시 거절">↩ 거절로</button>`;
      }

      html+=`<tr>
        <td><span class="token-word">${t.token}</span></td>
        <td><span class="token-vol ${volClass}">${volText}</span></td>
        <td>${t.hit_count}회</td>
        <td><span class="token-source ${srcClass}">${srcLabel}</span></td>
        <td class="token-cats" title="${cats}">${cats}</td>
        <td>${dt}</td>
        <td>${actionHtml}</td>
      </tr>`;
    });
    tb.innerHTML=html;
  }catch(e){
    tb.innerHTML='<tr><td colspan="7" style="color:var(--red);text-align:center;padding:24px">로드 실패: '+e.message+'</td></tr>';
  }
}

// 승인: 원클릭 (사유 입력 없음)
async function approveToken(id,token){
  if(!confirm('"'+token+'" 승인하시겠어요?\\n다음 분석부터 즉시 키워드에 반영됩니다.'))return;
  try{
    const r=await fetch('/admin/api/token/'+id+'/approve?reason=',{method:'POST'});
    if(r.ok){ loadTokens(); }
    else{ alert('실패: '+await r.text()); }
  }catch(e){alert('오류: '+e.message);}
}

// 거절: 세분화된 사유 선택 모달 (추후 데이터 분석용)
const REJECT_REASONS=['일반 단어(사전 불필요)','지명·지역명','오타·깨진 조각','비사전 합성어(수식 결합)','브랜드·상호명','검색량 낮음','중복·유사어 있음','업종 무관','기타'];
let _rejectId=null,_rejectTk='';
function rejectToken(id,token){
  _rejectId=id;_rejectTk=token;
  document.getElementById('rejectModalTitle').textContent='"'+token+'" 거절';
  document.getElementById('rejectReasons').innerHTML=REJECT_REASONS.map((r,i)=>'<button onclick="doReject('+i+')">'+r+'</button>').join('');
  document.getElementById('rejectModal').style.display='flex';
}
function closeRejectModal(){document.getElementById('rejectModal').style.display='none';_rejectId=null;}
async function doReject(idx){
  if(_rejectId===null)return;
  const id=_rejectId,reason=REJECT_REASONS[idx]||'기타';
  try{
    const r=await fetch('/admin/api/token/'+id+'/reject?reason='+encodeURIComponent(reason),{method:'POST'});
    closeRejectModal();
    if(r.ok){ loadTokens(); }
    else{ alert('실패: '+await r.text()); }
  }catch(e){alert('오류: '+e.message);}
}

async function fetchTokenVolumes(){
  if(!confirm('네이버 광고 API로 검색량을 조회합니다.\\n(최대 50개 토큰)'))return;
  try{
    const r=await fetch('/admin/api/tokens/fetch-volume',{method:'POST'});
    const d=await r.json();
    alert('검색량 조회 완료!\\n\\n업데이트: '+d.updated+'개 / 전체: '+d.total+'개');
    loadTokens();
  }catch(e){alert('오류: '+e.message);}
}

async function loadStats(){
  const r=await fetch('/admin/api/stats');
  const d=await r.json();
  document.getElementById('statVisits').innerHTML=(d.total_visits||0).toLocaleString();
  document.getElementById('statTotal').textContent=d.total_analyses.toLocaleString();
  document.getElementById('statSubs').innerHTML=d.subscriber_count+'<small>+'+d.new_subscribers_week+' 이번주</small>';
  document.getElementById('statWeekVisits').innerHTML=(d.visits_this_week||0).toLocaleString()+'<small>+'+d.new_analyses_week+' 진단</small>';
  document.getElementById('statRevisitRate').innerHTML=(d.revisit_rate||0)+'%<small>재방문자 '+(d.returning_visitors||0)+'명</small>';
  loadFunnel();
  loadWeekCompare();
}

async function loadFunnel(){
  try{
    const r=await fetch('/admin/api/funnel');
    const d=await r.json();
    const visits=d.visits||0, analyses=d.analyses||0, leads=d.leads||0;
    const maxVal=Math.max(visits,1);
    document.getElementById('funnelVisit').style.width='100%';
    document.getElementById('funnelAnalysis').style.width=(analyses/maxVal*100)+'%';
    document.getElementById('funnelLead').style.width=(leads/maxVal*100)+'%';
    document.getElementById('funnelVisitNum').textContent=visits.toLocaleString();
    document.getElementById('funnelAnalysisNum').textContent=analyses.toLocaleString();
    document.getElementById('funnelLeadNum').textContent=leads.toLocaleString();
    document.getElementById('funnelAnalysisRate').textContent='('+d.visit_to_analysis_rate+'%)';
    document.getElementById('funnelLeadRate').textContent='('+d.analysis_to_lead_rate+'%)';
  }catch(e){console.error('funnel',e)}
}

async function loadWeekCompare(){
  try{
    const r=await fetch('/admin/api/week-compare');
    const d=await r.json();
    const tw=d.this_week, lw=d.last_week, ch=d.change;
    document.getElementById('cmpVisitThis').textContent=tw.visits;
    document.getElementById('cmpVisitLast').textContent=lw.visits;
    renderArrow('cmpVisitArrow',ch.visits);
    document.getElementById('cmpAnalysisThis').textContent=tw.analyses;
    document.getElementById('cmpAnalysisLast').textContent=lw.analyses;
    renderArrow('cmpAnalysisArrow',ch.analyses);
    document.getElementById('cmpLeadThis').textContent=tw.leads;
    document.getElementById('cmpLeadLast').textContent=lw.leads;
    renderArrow('cmpLeadArrow',ch.leads);
  }catch(e){console.error('compare',e)}
}

function renderArrow(id, ch){
  const el=document.getElementById(id);
  if(ch.value>0){
    el.className='compare-arrow up';
    el.innerHTML='↑ '+(ch.percent!==null?ch.percent+'%':'N/A');
  }else if(ch.value<0){
    el.className='compare-arrow down';
    el.innerHTML='↓ '+Math.abs(ch.percent||0)+'%';
  }else{
    el.className='compare-arrow same';
    el.innerHTML='- 0%';
  }
}

function fmtAdminTime(iso){
  if(!iso) return '';
  const d=new Date(iso), now=new Date();
  const time=d.toLocaleTimeString('ko',{hour:'2-digit',minute:'2-digit'});
  const sameDay=(a,b)=>a.getFullYear()===b.getFullYear()&&a.getMonth()===b.getMonth()&&a.getDate()===b.getDate();
  const y=new Date(now); y.setDate(now.getDate()-1);
  if(sameDay(d,now)) return '오늘 '+time;
  if(sameDay(d,y)) return '어제 '+time;
  const md=String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
  return md+' '+time;
}

async function loadRecent(){
  loadAnalysesFiltered(0);
}

async function loadSubscribersFiltered(page=0){
  subPage=page;
  const search=document.getElementById('subSearch').value;
  const status=document.getElementById('subStatusFilter').value;
  const limit=15;
  const r=await fetch(`/admin/api/subscribers-filtered?search=${encodeURIComponent(search)}&status=${status}&offset=${page*limit}&limit=${limit}`);
  const d=await r.json();
  document.getElementById('subCount').textContent=d.total;
  let html='';
  const statusLabels={new:'신규',contacted:'연락함',contracted:'계약함',hold:'보류',rejected:'거절'};
  d.items.forEach(x=>{
    const tag=x.alarm_on?'<span class="tag on">수신중</span>':'<span class="tag off">해지</span>';
    const del=`<button class="del-btn" onclick="deleteSub(${x.id},'${(x.store_name||'').replace(/'/g,"")}')">삭제</button>`;
    const statusBadge=`<span class="status-badge ${x.status}" onclick="toggleStatusDropdown(${x.id})">${statusLabels[x.status]||'신규'}</span><select id="status-${x.id}" class="status-select" style="display:none" onchange="updateStatus(${x.id},this.value)"><option value="new" ${x.status==='new'?'selected':''}>신규</option><option value="contacted" ${x.status==='contacted'?'selected':''}>연락함</option><option value="contracted" ${x.status==='contracted'?'selected':''}>계약함</option><option value="hold" ${x.status==='hold'?'selected':''}>보류</option><option value="rejected" ${x.status==='rejected'?'selected':''}>거절</option></select>`;
    const memoInput=`<input type="text" class="memo-input" value="${(x.memo||'').replace(/"/g,'&quot;')}" placeholder="메모..." onblur="updateMemo(${x.id},this.value)">`;
    const placeBtn=x.place_url?`<a href="${x.place_url}" target="_blank" class="go-btn">바로가기</a>`:'<span style="color:var(--sub)">-</span>';
    const regionCat=`${x.region||'-'} / ${x.category||'-'}`;
    // 대표 키워드: 깔끔한 드롭다운
    let kwOptions=x.keywords.map(k=>`<option value="${k}" ${(x.selected_keyword||x.keywords[0])===k?'selected':''}>${k}</option>`).join('');
    const kwSelect=x.keywords.length?`<select class="kw-select" onchange="updateKeyword(${x.id},this.value)">${kwOptions}</select>`:`<span style="color:var(--sub)">-</span>`;
    html+=`<tr><td>${x.store_name}</td><td>${placeBtn}</td><td style="font-size:12px">${regionCat}</td><td>${x.phone}</td><td>${statusBadge}</td><td>${kwSelect}</td><td>${memoInput}</td><td>${x.created_at||'-'}</td><td>${tag}</td><td>${del}</td></tr>`;
  });
  document.getElementById('subTable').innerHTML=html||'<tr><td colspan="10" style="color:var(--sub);text-align:center">알림 신청자가 없습니다</td></tr>';
  const pages=Math.ceil(d.total/limit);
  let paging='';
  for(let i=0;i<pages&&i<10;i++){
    paging+=`<button onclick="loadSubscribersFiltered(${i})" style="padding:6px 12px;border:1px solid ${i===page?'var(--green)':'var(--line)'};background:${i===page?'var(--green-soft)':'#fff'};border-radius:6px;cursor:pointer">${i+1}</button>`;
  }
  document.getElementById('subPaging').innerHTML=paging;
}

async function updateKeyword(id,keyword){
  await fetch(`/admin/api/subscriber/${id}/keyword?keyword=${encodeURIComponent(keyword)}`,{method:'PUT'});
}

function toggleStatusDropdown(id){
  const badge=event.target;
  const sel=document.getElementById('status-'+id);
  badge.style.display='none';
  sel.style.display='inline';
  sel.focus();
  sel.addEventListener('blur',()=>{
    setTimeout(()=>{
      sel.style.display='none';
      badge.style.display='inline';
    },100);
  },{once:true});
}

async function updateStatus(id,status){
  await fetch(`/admin/api/subscriber/${id}/status?status=${status}`,{method:'PUT'});
  loadSubscribersFiltered(subPage);
}

async function updateMemo(id,memo){
  await fetch(`/admin/api/subscriber/${id}/memo?memo=${encodeURIComponent(memo)}`,{method:'PUT'});
}

async function loadSubs(){
  loadSubscribersFiltered(0);
}

async function deleteSub(id, name){
  if(!confirm('['+name+'] 리드를 정말 삭제하시겠어요?\\n삭제하면 복구할 수 없습니다.')) return;
  const r=await fetch('/admin/api/subscriber/'+id,{method:'DELETE'});
  if(r.ok){ loadSubs(); loadStats(); }
  else { alert('삭제 실패: 다시 시도해주세요.'); }
}

function downloadCsv(){
  window.location.href='/admin/api/subscribers/csv';
}

async function loadInsight(){
  // 구독자 매장 현황
  const r1=await fetch('/admin/api/subscriber-stores');
  const d1=await r1.json();
  let html1='';
  d1.forEach(x=>{
    let change='<span class="same">- 유지</span>';
    if(x.last_rank&&x.this_rank){
      const diff=x.last_rank-x.this_rank;
      if(diff>0)change=`<span class="up">▲ ${diff}</span>`;
      else if(diff<0)change=`<span class="down">▼ ${Math.abs(diff)}</span>`;
    }
    const placeBtn=x.place_url?`<a href="${x.place_url}" target="_blank" class="go-btn">바로가기</a>`:'<span style="color:var(--sub)">-</span>';
    html1+=`<tr><td>${x.store_name}</td><td>${placeBtn}</td><td>${x.keyword||'-'}</td><td>${x.last_rank?x.last_rank+'위':'-'}</td><td class="rank">${x.this_rank?x.this_rank+'위':'-'}</td><td>${change}</td></tr>`;
  });
  document.getElementById('subStoreTable').innerHTML=html1||'<tr><td colspan="6" style="color:var(--sub);text-align:center">구독자가 없습니다</td></tr>';

  // 인기 분석 매장
  const r2=await fetch('/admin/api/popular-stores?limit=10');
  const d2=await r2.json();
  let html2='';
  d2.forEach(x=>{
    const placeBtn=x.place_url?`<a href="${x.place_url}" target="_blank" class="go-btn">바로가기</a>`:'<span style="color:var(--sub)">-</span>';
    html2+=`<tr><td>${x.rank}</td><td>${x.store_name}</td><td>${placeBtn}</td><td>${x.region||'-'} / ${x.category||'-'}</td><td>${x.count}회</td><td>${x.last_analyzed}</td></tr>`;
  });
  document.getElementById('popularTable').innerHTML=html2||'<tr><td colspan="6" style="color:var(--sub);text-align:center">분석 기록이 없습니다</td></tr>';

  // 업종별 통계
  const r3=await fetch('/admin/api/category-stats');
  const d3=await r3.json();
  let html3='';
  d3.forEach(x=>{
    html3+=`<div class="stat-bar"><span class="label">${x.category}</span><div class="bar"><div class="fill" style="width:${x.percent}%"></div></div><span class="count">${x.count}</span></div>`;
  });
  document.getElementById('categoryStats').innerHTML=html3||'<div style="color:var(--sub)">데이터 없음</div>';

  // 지역별 통계
  const r4=await fetch('/admin/api/region-stats');
  const d4=await r4.json();
  let html4='';
  d4.forEach(x=>{
    html4+=`<div class="stat-bar"><span class="label">${x.region}</span><div class="bar"><div class="fill" style="width:${x.percent}%"></div></div><span class="count">${x.count}</span></div>`;
  });
  document.getElementById('regionStats').innerHTML=html4||'<div style="color:var(--sub)">데이터 없음</div>';
}

async function loadMonitor(){
  loadInsight();
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
