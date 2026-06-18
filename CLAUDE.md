# 플레이스닥터 — 프로젝트 컨텍스트

## 프로젝트 개요

네이버 플레이스 순위 진단 도구. Playwright로 네이버를 크롤링해 매장의 키워드 순위·리뷰·경쟁사를 분석하고 4축 점수를 산출한다.

---

## 완료된 단계

| 단계 | 내용 |
|------|------|
| 1단계 | 네이버 플레이스 순위 검색 엔진 (Playwright 기반) |
| 2단계 | 리뷰 수·별점 수집, 경쟁사 자동 탐색, 4축 점수 계산 |
| 3단계 | FastAPI 서버 + PostgreSQL DB (캐시, 이력, 리드) |
| 4단계 | 진단 화면 HTML UI + Windows 이벤트루프 버그 수정 |
| 5단계 | 그린 브랜드 디자인, 모바일 우선 반응형, 원형 게이지, 4축 카드, 경쟁사 막대, 키워드 카드, 광고축 15% 비중 적용 |
| 6단계 | 속도 개선(순차 4분→병렬 65초), 기회 키워드 태그(아깝다/놓침/노출중), 규칙 기반 멘트 |
| A묶음 | 4축 이름 변경, 리뷰관리 합산 채점(별점 null 안전), 최근활동에 30일 리뷰수 수집, 키워드광고 체크박스 입력, 순위 구간별 멘트, 닥터 코멘트 3~4문장 |
| B묶음 | 키워드 태그 7구간 배색·라벨+숫자, 멘트풀 전 구간, 등급배지 위치, 경쟁사 비교 기준 문구 |
| C묶음 | businesses_total 수집(Apollo state), 등급(S/A/B/C) 상대백분율 계산, 업체수순 정렬, 순위 크게 강조 레이아웃, 경쟁사 1위→2위 보정 |
| D묶음 | 키워드 로직 최신화(방식5·복합어·필터), 리뷰버그(URL·폴백·30일단순화), 닥터코멘트 키워드성과, 재검색 초기화 |
| E묶음 | kw_list_raw/kw_list 분리(복합어 필터), sort_weight 역(30+15)>동(20+15) 보정, MAX_KW=30, map.naver businesses_total 폴백, SyntaxWarning 전수 제거 |
| F묶음 | 키워드 생성 디버깅 로그 추가, 키워드 등급(S/A/B/C) businesses_total 상대백분율 UI, 등록업체수 표시, 순위 강조 레이아웃 |
| H단계 | 플레이스/블로그 선택 분석 + DB 히스토리 누적 저장 + 직전 비교 표시 |
| I단계 | **고질병 2개 근본 해결** — ① 좀비 서버: `--reload` 폐기 + `restart.py`/`restart.bat`(포트+상속소켓좀비 정리, 단일프로세스 기동). ② IP 차단: 플마와 스텔스 1:1 동일화(create_browser args/context 정리), naver.me 단축링크 place_id 네비게이션 해석, 블로그 분석 병렬화(N_BLOG=3)+딥스캔 지터딜레이+키워드폭15, generate_blog_keywords "맛집" 하드코딩 제거. 라보떼 3회 연속 7매칭·차단 0건 검증 |
| J단계 | **히스토리 추세 표시** — 분석 결과에 과거 기록 녹여서 표시: ① 분석 횟수/시점 안내 ("이 가게 N번째 분석 · 지난 분석 MM/DD") ② 종합점수 직전 비교 ("지난번 78점 → 이번 82점 (+4)") ③ 키워드별 순위 추세 (기록 2개↑: "13위→9위→2위", 기록 1개: "전: N위 ▲▼", 첫분석: "(첫 분석)") ④ 블로그 분석도 동일 적용. crud.py에 `get_keyword_rank_history`, `get_analysis_count` 추가 |
| K단계 | **익명ID 검색기록 + 최근매장 + 재검색 + 정렬/점수강조** — ① 익명 ID(anon_id): localStorage UUID 발급 → 분석 시 DB 기록 ② 최근 본 매장 목록: 입력폼 아래 표시, 탭하면 저장 결과 즉시(place/blog 둘 다 로드해 탭 전환) ③ 재검색: "다른 매장 검색 / 다시 분석" 버튼(새로고침 없이 JS 전환), goBackToSearch 버튼 상태 리셋 ④ 키워드 정렬: 내 순위 높은 순→업체수순→놓침 뒤로 ⑤ 점수 변화 강조: 상승=초록/하락=빨강 그라데이션 + 큰 숫자 + 단계별 멘트(±1~3/±4~9/±10↑). API 3개 추가(아래 표) |
| L단계 | **이름변경 + 랜딩 + 클린 메디컬 톤 (UI만, 내부 변수·DB·파일명 무변경)** — ① 사이트명 플레이스닥터→**플레이스랭킹**(타이틀·헤더 로고 🩺→📊·"닥터 코멘트"→"분석 코멘트"), 헤더 흰 배경+차분한 초록 로고+부제("네이버 플레이스 순위 분석", 모바일 숨김) ② **랜딩 페이지 신규**: 히어로(🏆 "내 가게 네이버 몇 위?" + "내 순위 확인하기"=폼으로 스크롤)+가치4카드+작동3스텝+진단 미리보기+검색폼, 새로고침 없이 전환 ③ 결과/블로그/최근매장 톤 통일(무거운 그림자→얇은 보더 1px, radius 14, 흰카드+여백, 점수변화 강조·키워드정렬 유지) ④ "진단하기"→"내 순위 확인하기", 강제 재크롤 체크박스 제거(다시분석 버튼은 내부 `_forceRefresh`로 유지) |
| L단계-fix | **히스토리 누적 버그 수정** — 같은 가게를 여러 번 분석해도 "첫 분석"만 뜨던 문제. 원인 2개: ① `/diagnose` 캐시 적중 시 `save_analysis_history`를 건너뜀 → 캐시 적중도 1회 누적하도록 ② naver.me 단축URL은 `_extract_place_id`가 None → 크롤 전 직전기록·추세·횟수 조회가 통째 skip되어 항상 비었음 → **크롤로 해석된 place_id로 직전기록 재조회**하도록. 백세돼지국밥(naver.me)으로 검증: count 누적·"지난 분석 06/10"·점수/키워드 추세 정상 |
| O단계 | **매장 삭제 + 리뷰 수집 개선** — ① 최근 본 매장/등록 매장 카드에 × 삭제 버튼 추가 (localStorage 숨김 처리) ② 방문자 리뷰수 수집: Apollo State(`window.__APOLLO_STATE__`)에서 `visitorReviewCount` 추출 폴백 (리뷰 탭 차단 우회) ③ 최근활동 점수: 리뷰 탭에서 날짜 파싱해 30일 이내 개수(`recent_30d_reviews`) 기반 점수 계산 |
| O단계-fix | **거짓 최신리뷰 날짜 제거 + 활동 점수 graceful (B)** — ① 메인페이지 `"date"` 전역 스캔이 추천/고정 리뷰의 옛 날짜(예: 7개월 전 2025.11.14)를 최신 리뷰로 잘못 잡아 최근활동을 왜곡하던 것 **제거** → `latest_review_date`는 **리뷰 탭(최신순 맨 위)에서만** 신뢰, 실패 시 None ② 리뷰 활동 전혀 수집 못 하면(latest None+30d None, 보통 m.place IP차단) `activity=None` → 종합점수에서 **제외(나머지 3축 재정규화)**, 화면은 **"리뷰 활동 정보 수집 중"** 중립 표시 (거짓 낮은 점수 방지). `calculate_total`이 activity None 시 재정규화. ※ 리뷰탭 성공 경로 검증은 m.place 차단 해제 후(A) |
| P단계 | **경쟁사 비교 개편 + 속도 개선** — ① **속도**: 경쟁사 탐색의 `find_competitor`(경쟁사에 `get_store_details` 전체 크롤 — 주소·키워드·리뷰·m.place 리뷰탭 = 504 유발) **제거**. 경쟁사 비교는 내 키워드 검색 결과를 재사용(추가 요청 0) + 1위 매장 이름만 가볍게(`_fetch_place_name`, map 타이틀, ≤3개 병렬). m.place 안 건드림. ② **로직**: 등급 백엔드 이식(`_calc_grades`, businesses_total 백분율) → **S우선→A, 내가 1위 아닌 키워드 상위 최대 3개**(`_build_competitor_compare`). status: `ok`/`no_sa`(S/A 없음)/`all_first`(전부 1위). ③ **UI**: 반응형 카드(grid auto-fit minmax 185px → PC 2열·모바일 1열) — 등급뱃지("S급 키워드")+키워드+내순위 vs 1위 이름+계단차이. ④ **통찰 멘트**: 1~2계단=근소(주황)/3~5=광고·상위노출 추정/6+=리뷰·키워드·광고 추정(빨강). 추정·여지 표현(단정X·광고티X). SyntaxWarning(`hideRecentStore\(`) 정리. ※ 경쟁사 비교는 점수에 영향 없음(competitor_data 미사용). 전체 e2e 속도측정은 /diagnose 1회(own리뷰 m.place 1히트) 필요 |
| Q단계 | **속도 개선 (목표 1분30초)** — 키워드 순위 검색이 키워드당 5~20초라 ~5분 504. 진범 2개 제거: ① **businesses_total의 map.naver 폴백 제거** (`_fetch_place_ranking`에서 goto+2.5초 ≈ 키워드당 6초, 거의 매번 발동했지만 결과도 자주 None, 점수엔 미사용=등급 뱃지 전용). search.naver 1차 시도만 유지. None이면 등급 뱃지만 graceful 생략(점수·순위·경쟁사 선정 무영향). ② **스크롤 12→4회** — search.naver는 인라인 5개만 주고 스크롤로 안 늘어남(4·8·12회 모두 ranked 5개 확인) → 12회는 4.4초 순수낭비, 4회로 정확도 손실 0·~3초/키워드 절약. 키워드 개수(MAX_KW=30) 유지. 로컬 측정: 키워드당 ~3.4초(기존 5~20초). ※ 서버 /diagnose 1회로 전체 시간 측정. (참고: businesses_total None↑ → 등급/S·A 경쟁사 카드가 더 자주 빔 = 폴백 있어도 거의 None이던 것의 연장, graceful) · **Q단계 효과 0(여전히 4분30초)** → 진범 재조사: 병렬은 정상(3.9x)이나 4페이지 동시=경합으로 키워드당 3.2s→9.5s, get_store_details에 고정 sleep ~11.5s(1+3+2+3+2.5) + 네비 4~5회. `diagnose_store`에 단계별 타이밍 로그(⏱ 브라우저/상세/랭킹/이름/총합) 추가 — 서버 1회로 270초 분해 후 진짜 레버 결정 |
| R단계 | **백그라운드 SSE + 게임형 UI (504 근본 해결)** — ① **SSE 스트리밍**: `/diagnose-stream` 엔드포인트 추가, started 이벤트 즉시 전송(504 방지), keyword 이벤트 실시간 전송, complete에 전체 결과. nginx `proxy_buffering off` 설정. ② **게임 UI**: 키워드+순위 팝업 애니메이션(커졌다 작아짐), 순위별 리액션(1위 "오~! 🎉"/2~3위 "Nice!"/4~5위 "Good!"/그 이하 담백), 점수 차오르는 연출(1~3등 +2/4~10등 +1), 천장 규칙(종합점수 초과 안 함). ③ **최적화**: N_WORKERS=2(t3.small 측정상 최적), 키워드 9글자(공백 제외) 초과 필터링. |
| S단계 | **게임 UI 다듬기** — ① **가짜 5단계 제거** → 실제 진행률 "키워드 N/M" 표시 (SSE progress/total 연동). ② **상위 키워드 칩 누적**: 10위 이내 키워드를 플레이스 지수 아래에 칩으로 쌓음 (순위별 색 뱃지: 1위=금, 2~3=은, 4~5=동, 6~10=회색). ③ **키워드 사이 생동감**: 결과 표시 후 1.2초 뒤 "분석 중" 펄스/스피너로 전환 → 다음 키워드 도착 시 팝업 (끊김 없음). ④ **순위 흐름 날짜 표기**: 날짜별 대표 1개만 표시 (같은 날 중복 제거) + 가로 흐름에 날짜·순위 세로 배치. |
| T단계 | **키워드 정확도 플마 수준 복원** — R단계에서 추가된 필터들이 플마보다 키워드를 적게 생성하는 문제. ① `clean_official` 8글자+공백없음 필터 **제거** (긴 태그도 검색 대상) ② `kw_list` 10글자+공백없음 필터 **제거** (긴 키워드도 시드로 사용) ③ 최종 9글자 필터 **제거** (R단계 추가분 롤백) ④ `_find_tokens_in_kw` 플마 1:1 동일화 (사전 매칭 없을 때만 5글자 이하 fallback). 테스트: 동일 입력으로 66개+ 키워드 생성 확인. |
| U단계 | **관리자 페이지 + 전화번호 수집** — ① **DB 테이블 2개 추가**: `subscribers`(알림 구독자), `alim_templates`(알림톡 추가문구). ② **전화번호 수집**: 분석 결과 화면에 "매주 순위 알림 받기" 폼 (휴대폰+수신동의) → `/subscribe` API로 저장. ③ **관리자 페이지 `/admin`**: 환경변수 인증(`ADMIN_USER`/`ADMIN_PASS`), 4개 메뉴(대시보드/회원·리드/매장 모니터링/알림톡 관리). ④ **대시보드**: 총 진단·등록매장·리드수·이번주 신규 통계 + 최근 진단 테이블. ⑤ **회원·리드**: 구독자 목록(전화번호 포함) + CSV 내보내기. ⑥ **매장 모니터링**: 내 매장 등록된 매장들의 순위 변화 추적. ⑦ **알림톡 관리**: 카카오 승인 골격(읽기전용) + 추가문구 편집·저장. |
| V단계 | **알림톡 API 연동 (알리고)** — ① **services 모듈 신규**: `backend/services/alimtalk.py`(발송 함수), `weekly_report.py`(주간 리포트 스켈레톤). ② **신청 완료 알림톡**: `/subscribe` API에서 구독 저장 후 `send_signup_alimtalk()` 자동 호출 (실패해도 구독은 성공). ③ **주간 리포트 발송**: `send_weekly_reports()` — alarm_on=True 구독자 전원에게 대표 키워드 순위 변화 발송. 실제 cron은 EC2 crontab에 등록. ④ **환경변수**: `ALIGO_API_KEY`, `ALIGO_USER_ID`, `ALIGO_SENDERKEY`, `ALIGO_SENDER`, `ALIGO_TESTMODE`(Y=테스트). ⑤ **테스트 모드**: 템플릿 승인 전까지 `testmode_yn="Y"` 유지 (실제 발송 안 됨). 승인 후 N으로 변경 + `ALIGO_TPL_SIGNUP`/`ALIGO_TPL_WEEKLY` 실제 코드로 교체. |

---

## 4축 이름 (A묶음 적용)

| 점수 키(코드/DB) | 화면 라벨 | 채점 구성 |
|---|---|---|
| `seo`      | 검색노출(SEO) | 키워드 순위 + 정보 완성도 + 사진 |
| `content`  | 리뷰관리       | 방문자 리뷰 + 블로그 리뷰 + 별점 **합산** (별점 None이면 제외하고 환산) |
| `activity` | 최근활동       | 최근 리뷰 날짜 + 최근 30일 방문자 리뷰수 + 정보 최신성 (30일값 None이면 제외하고 환산) |
| `ad`       | 키워드광고     | 체크박스 입력 기반 (미체크 20 / 일부 40 / 전부 60). 비중 15% |

> 내부 점수 키는 DB 컬럼·캐시 호환 위해 그대로 유지하고 화면 라벨만 변경.
> 광고 체크박스는 크롤링 대상이 아니라 요청 입력값 → 캐시에 굳지 않고 `apply_ad_flags()`로 응답 시 ad·total 재계산.

---

## 기술 스택

- **언어**: Python 3.14
- **웹 프레임워크**: FastAPI + uvicorn 0.48.0
- **크롤링**: Playwright (async_api, Chromium headless)
- **DB**: PostgreSQL + SQLAlchemy (sync ORM)
- **스키마 검증**: Pydantic v2

---

## 파일 구조

```
placedoctor/
├── backend/
│   ├── main.py          # FastAPI 앱 + 전체 HTML UI (인라인)
│   │                    # ProactorEventLoop 데몬 스레드 포함
│   ├── database.py      # SQLAlchemy 엔진·세션
│   ├── models.py        # DB 테이블 8개
│   ├── schemas.py       # Pydantic 요청/응답 스키마
│   ├── crud.py          # DB 읽기·쓰기 (캐시 24h, 이력, 리드)
│   ├── core/
│   │   ├── scraper.py   # 핵심 크롤링 엔진
│   │   │                # - diagnose_store(): 통합 진단 (병렬 N_WORKERS=4, MAX_KW=10)
│   │   │                # - check_place_rank(): 플레이스 순위 검색
│   │   │                # - get_store_details(): 매장 상세정보 수집
│   │   │                # - find_competitor(): 경쟁사 탐색
│   │   ├── keywords.py  # 키워드 자동 생성 (우선순위: keywordList > 역 > 동 > 구)
│   │   └── scoring.py   # 4축 점수 (SEO 34% + 콘텐츠 30% + 활성도 21% + 광고 15%)
│   ├── test_scraper.py  # CLI 테스트 진입점
│   └── requirements.txt
├── restart.py           # ⭐ 서버 재시작(좀비 정리+단일프로세스 기동). 항상 이걸로만 띄움
├── restart.bat          # restart.py 더블클릭용 래퍼
├── server.log           # 서버 콘솔 로그 (restart.py가 생성, .gitignore)
├── .env                 # DB 접속정보 (GitHub 업로드 금지)
├── _reference/
│   └── naver_tracker.py # 원본 플마 — 절대 수정 금지, 읽기 전용 (스텔스/우회의 "정답")
├── CLAUDE.md
└── README.md
```

---

## 서버 실행 — ⚠️ 반드시 restart 스크립트로만 (I단계)

```bash
python restart.py        # 또는 restart.bat 더블클릭
```

- 진단 화면: http://localhost:8000/  · API 문서: http://localhost:8000/docs
- 서버 콘솔 로그: `server.log` (UTF-8, .gitignore 처리됨)

### 🚫 `uvicorn --reload` 를 직접 쓰지 말 것 (좀비 서버 원인)

며칠간 "코드를 고쳐도 화면이 안 바뀜"이 반복된 근본 원인:
Windows에서 `uvicorn --reload`는 워커를 `multiprocessing.spawn`으로 띄우는데,
reload(부모)가 죽으면 **워커(자식)가 LISTEN 소켓을 상속한 채 좀비로 남는다.**
이때 netstat은 그 소켓 소유자를 **죽은 부모 PID**로 잘못 표시 → "포트 소유자만 kill"
하면 죽은 PID를 죽이려다 실패, 포트가 안 풀리고 **옛 코드로 계속 응답**한다.

**근본 해결:** `--reload` 폐기 + 항상 **단일 프로세스**로 기동. `restart.py`가:
1. 포트 8000 점유 프로세스 종료 (소유자가 죽었으면 `--multiprocessing-fork`·`parent_pid=<죽은PID>`
   자식까지 추적해 종료 — 상속-소켓 좀비 대응). 플마 GUI(pythonw 플레이스마스터)는 안 건드림.
2. `python -m uvicorn backend.main:app --loop none` (**--reload 없음**) 으로 기동.
3. `/health` 200 확인 후 "준비 완료" 출력.

→ 화면이 안 바뀌면 `restart.py` 한 번이면 끝. netstat로 찾아 죽이는 수작업 전부 불필요.

### 왜 --loop none인가 (그대로 유지)

uvicorn 0.48.0은 Windows에서 SelectorEventLoop를 쓰는데 Playwright는 ProactorEventLoop만
지원 → `NotImplementedError`. `--loop none` → Python 기본 정책(Windows=ProactorEventLoop).
`main.py`가 전용 데몬 스레드로 ProactorEventLoop를 돌리고
`asyncio.run_coroutine_threadsafe()`로 진단 코루틴을 위임한다.

---

## DB 정보

```
Host     : localhost:5432
DB명     : placedoctor
User     : postgres
Password : postgres
URL      : postgresql://postgres:postgres@localhost:5432/placedoctor
```

`.env` 파일에 저장. `database.py`가 로드. 서버 시작 시 테이블 자동 생성.

---

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/` | 진단 HTML 화면 |
| GET | `/health` | 서버 상태 |
| POST | `/diagnose` | 매장 진단 (순위·리뷰·점수·경쟁사) |
| GET | `/diagnose-stream` | (R단계) SSE 스트리밍 진단 (started→keyword→complete) |
| GET | `/store/{place_id}/history` | 과거 순위·점수 이력 |
| POST | `/lead` | 연락처(리드) 저장 |
| GET | `/recent-stores/{anon_id}` | (K단계) 익명ID의 최근 본 매장 목록 |
| GET | `/history-result/{place_id}` | (K단계) 저장된 최신 분석 결과 |
| GET | `/history-result-all/{place_id}` | (K단계) place+blog 분석 결과 둘 다 반환 |
| POST | `/subscribe` | (U단계) 알림 구독 신청 |
| POST | `/unsubscribe/{id}` | (U단계) 알림 해지 |
| GET | `/admin` | (U단계) 관리자 페이지 HTML |
| POST | `/admin/login` | (U단계) 관리자 로그인 |
| GET | `/admin/api/stats` | (U단계) 대시보드 통계 |
| GET | `/admin/api/subscribers` | (U단계) 구독자 목록 |
| GET | `/admin/api/subscribers/csv` | (U단계) 구독자 CSV 다운로드 |
| GET | `/admin/api/monitored-stores` | (U단계) 모니터링 매장 목록 |
| GET | `/admin/api/alim-templates` | (U단계) 알림톡 템플릿 조회 |
| POST | `/admin/api/alim-templates` | (U단계) 알림톡 추가문구 저장 |

---

## 현재 화면 구조 (5~6단계 기준)

`backend/main.py`의 `_HTML`에 인라인. 섹션 순서:

1. 헤더 — 그린 sticky + "무료 진단" 뱃지
2. 입력폼 — 매장명+URL, 진단하기 버튼
3. **로딩 화면** — 5단계 순차 진행 (도트 애니메이션, 진행바 95%→100%)
4. **결과** (진단 후)
   - 매장 정보 카드
   - 종합 게이지 (SVG 원형, 0→실제점수 애니메이션)
   - **4축 카드**: SEO / 콘텐츠 / 활성도 / 광고 (점수+진행바+세부3항목)
   - **경쟁사 비교**: 가로 막대 (우리 vs 1위)
   - **키워드 카드**: 기회 태그(아깝다/놓침/경쟁사우위/노출중) + 기회 키워드 상단 정렬 + 규칙 멘트
   - 닥터 코멘트 (규칙 기반 자동 생성)
   - 버튼 3종 (카톡 리포트 / 홈화면추가PWA / 카톡공유) — 자리만, 동작은 추후

**점수 색**: 70↑ 초록 / 40↑ 주황 / 미만 빨강 | **브랜드색**: `#03c75a`

---

## 주의 사항

- `_reference/naver_tracker.py` — **절대 수정 금지**. (스텔스/차단우회의 "정답" 레퍼런스)
- `backend/core/` 파일만 수정.
- `.env`는 Git 업로드 금지 (`.gitignore` 처리됨).
- 진단 1회 약 60~90초 (병렬 10키워드 + 경쟁사 동시).
- **서버는 `restart.py`로만** 기동. `uvicorn --reload` 직접 실행 금지(좀비 서버 원인). 화면 안 바뀌면 restart.
- **차단 우회 설정(create_browser/create_stealth_page)은 플마와 1:1 동일 유지.** launch args·context·init script를
  임의로 추가/변경 말 것 — 한 군데라도 플마와 달라지면 그게 차단 원인이 될 수 있음. 바꿔야 하면 플마부터 대조.
- **모든 페이지는 `create_stealth_page()`로만 생성** (raw `context.new_page()` 금지 — webdriver 스텔스 누락 방지).
- **단계(묶음) 완료 시 CLAUDE.md를 반드시 갱신** — "완료된 단계" 표 + (API 추가 시) 엔드포인트 표. 커밋 전 체크.
  (K단계에서 표 갱신을 빠뜨려 작업이 유실된 줄 알고 헤맨 적 있음. 갱신·커밋·푸시는 한 묶음으로.)

---

## 새 PC 세팅 방법

```bash
# 1. 클론
git clone https://github.com/narusepopo-droid/placedoctor.git
cd placedoctor

# 2. 의존성 설치
pip install -r backend/requirements.txt
playwright install chromium

# 3. PostgreSQL 설치 후 DB 생성
#    설치: https://www.postgresql.org/download/
#    DB명/유저/비번 모두 postgres 로 통일

# 4. .env 파일 생성 (git에 없으므로 직접 만들어야 함)
#    placedoctor/ 루트에 .env 파일 생성 후 아래 내용 붙여넣기:
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/placedoctor
DB_HOST=localhost
DB_PORT=5432
DB_NAME=placedoctor
DB_USER=postgres
DB_PASSWORD=postgres

# 5. 서버 실행 (--reload 쓰지 말 것 — 좀비 서버 원인)
python restart.py
```

브라우저에서 http://localhost:8000/ 열리면 완료.

---

## 배포(Deployment) 정보

| 항목 | 값 |
|------|-----|
| 서버 | AWS EC2 Ubuntu 24.04 t3.small (서울 리전) |
| IP | **3.37.138.148** (Elastic IP 고정, 2026-06-17). 이전: 54.180.91.222 → 13.125.37.99 → 3.35.233.85(인스턴스는 동일, EIP 부착 전) |
| 도메인 | https://placeranking.com (가비아, DNS A @/www → 3.37.138.148) |
| DB | PostgreSQL 16, placedoctor |
| 상시구동 | systemd `placeranking.service` |
| 웹서버 | nginx 80/443 → 127.0.0.1:8000 |
| HTTPS | certbot (만료 2026-09-13, 자동갱신) |
| 스왑 | 2GB `/swapfile` (2026-06-17 추가, `/etc/fstab` 등록 — 재부팅 유지). t3.small RAM 2GB 보강용 |

### 서버 접속

```bash
ssh -i placeranking-key.pem ubuntu@3.37.138.148
```

> **IP 변경 주의**: EIP 부착 전에는 인스턴스 재시작마다 퍼블릭 IP가 바뀌어 도메인이 죽었음(DNS는 옛 IP를 가리킴). 2026-06-17 **Elastic IP `3.37.138.148` 고정**으로 해결. 이후 IP 변동 없음.

### 코드 업데이트 (배포)

```bash
cd placedoctor
git pull
sudo systemctl restart placeranking
```

> **주의**: 서버에서 uvicorn은 `backend.main:app`으로 실행 (placedoctor 폴더 안에서, 상위 아님)

---

## 다음 작업 (A/B 묶음)

### ✅ A 묶음 — 데이터·로직 (완료)

6가지 모두 반영. 위 "4축 이름" 표 참고. 핵심 구현 위치:
- **4축 이름 변경**: `main.py` HTML(`buildSeoCard`/`buildContentCard`/`buildActivityCard`/`buildAdCard`) + `scoring.py` 문서주석.
- **최근활동 30일 리뷰수**: `scraper.py` `get_store_details()` — 방문자 리뷰 탭 1회 방문 안에서 리뷰 작성일을 days-ago로 환산해 30일 이내 카운트(`recent_30d_reviews`). best-effort, 실패 시 None. 차단 민감 구간이라 추가 네비게이션 없이 한 페이지에서 처리.
- **리뷰관리 합산 채점**: `scoring.py` — 방문자(최대50)+블로그(최대30)+별점(최대20). 별점 None이면 80점 만점 환산.
- **키워드광고 체크박스**: 입력폼 4종 체크박스 → `DiagnoseRequest.ad_*` → `calculate_ad_score()`. 캐시에 굳지 않게 `apply_ad_flags()`로 응답 시 재계산.
- **순위 구간 멘트**: `main.py` `RANK_MENTS`(top5/top10/top15/page2/none) 딕셔너리 한 곳에 모음.
- **닥터 코멘트**: `main.py` `renderComment()` — 강점→약점→아까운 키워드→해결방향 3~4문장.

> 미해결/추후: 30일 리뷰 카운트는 네이버 날짜 표기(특히 연도 없는 "M.D" 현재년 포맷)를 일부 놓칠 수 있어 보수적으로 적게 셀 수 있음(graceful). 정확도 개선은 추후. 기존 캐시(24h)는 구 공식 content/activity 값을 보일 수 있으나 만료/강제재크롤로 자연 갱신.

### B 묶음 — 화면·표시 (A 완료 후 진행)

**경쟁사 비교 개편**
- 어떤 키워드 기준으로 경쟁사를 찾았는지 명시 (예: "'상계동 맛집' 기준 1위")
- 비교 지표 3개: 플레이스 순위 + 블로그 리뷰수 + 방문자 리뷰수 (가로 막대)
- 합산 점수 그래프 1개 (우리 vs 1위 총점 비교) 추가

**키워드 순위 표시 개선**
- 실제 순위 명확히: "경상대 야식술집 13위" 형태로
- 부연 멘트는 기회 키워드(아깝다/놓침)에만 표시 (전체 아님)

**태그 배색 다양화**
- 아깝다 → 주황
- 놓침 → 빨강
- 노출중(1~5위) → 초록
- 상위권(6~10위) → 연초록
- 경쟁사우위 → 빨강 (현재와 동일하나 사용 범위 조정)

---

## 추후 개선 목록 (TODO)

- [ ] **m.place.naver.com(리뷰 엔드포인트) 차단 대응** — m.place(리뷰·home)는 **요청량 기반 IP 차단**에 민감하다(navigator.webdriver 등 스텔스로는 안 풀림, 수십분~몇시간 자동 해제). map.naver.com은 상대적으로 괜찮음. 리뷰는 현재 **graceful None 처리**(차단 시 latest_review_date=None, activity 점수 제외). 자동 크롤링·리뷰 알림 기능 만들 때 m.place 차단을 진지하게 고려(요청 분산/프록시/저빈도 스케줄 검토).
- [ ] **단축URL(naver.me) 캐시 최적화** — 현재 단축URL은 place_id를 미리 못 뽑아(`_extract_place_id`가 숫자 없는 단축URL에서 None) 매번 재크롤(~134초). 단축URL을 먼저 풀어서 place_id 확보 후 캐시 조회하면 빨라짐. 실사용자 늘기 전에 처리. (L단계 히스토리 수정 시 직전기록 조회는 해석된 place_id로 고쳤지만, 캐시 조회는 여전히 크롤 전 URL place_id에 의존)
- [ ] **30일 리뷰 수집이 차단을 유발하는지 모니터링** — A묶음에서 방문자 리뷰 탭 1회 방문으로 30일 리뷰수 수집 추가. 네이버 IP 차단("과도한 접근 요청") 빈도가 늘어나는지 관찰 필요. 차단 잦으면 수집 주기·스크롤 횟수 조정 또는 비활성화 검토.
- [x] **키워드 검출 정상 확인** — 2026-06-08 테스트에서 두 케이스 모두 동일하게 잘 잡힘 확인
- [ ] **30일 리뷰수 정확도 개선** — 감동식당(방문자 31,398개)에서 30일 5개로 과소집계. 연도 없는 "M.D" 포맷·스크롤 부족으로 누락 추정. best-effort 한계, 정렬·포맷 파싱 보강 필요.
- [ ] **기회 키워드 추천** — 현재 키워드 외에 노릴 만한 새 키워드 발굴 기능 (추후)
- [ ] **광고 집행 자동 감지** — 현재 체크박스 수동 입력 → 추후 실제 감지 구현
- [ ] **키워드 조합 단어 잘림 문제** — "돼지등갈비" → "돼지등" 등. `keywords.py` 민감 로직, 우선순위 낮음
- [ ] **별점 없는 매장 표시** — null 처리 정책 결정 (A묶음 리뷰관리 축 개선 시 함께)
- [ ] **SEO 점수 차등 검증** — 약한 매장으로 100점 도배 문제 확인 필요
- [ ] **버튼 동작 구현** — 카톡 리포트(리드 게이트) / PWA 설치 / 카톡 공유 (추후 단계)
