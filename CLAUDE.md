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
├── .env                 # DB 접속정보 (GitHub 업로드 금지)
├── _reference/
│   └── naver_tracker.py # 원본 플마 — 절대 수정 금지, 읽기 전용
├── CLAUDE.md
└── README.md
```

---

## 서버 실행

```bash
# 반드시 --loop none 필요 (Windows ProactorEventLoop 문제)
python -m uvicorn backend.main:app --loop none --reload
```

- 진단 화면: http://localhost:8000/
- API 문서: http://localhost:8000/docs

### 왜 --loop none인가

uvicorn 0.48.0은 `--reload` 모드에서 Windows에 SelectorEventLoop를 강제한다.
Playwright는 ProactorEventLoop만 지원하므로 `NotImplementedError` 발생.
`--loop none` → Python 기본 정책(Windows=ProactorEventLoop) 사용.
`main.py`에서 전용 데몬 스레드로 ProactorEventLoop를 실행하고
`asyncio.run_coroutine_threadsafe()`로 진단 코루틴을 위임.

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
| GET | `/store/{place_id}/history` | 과거 순위·점수 이력 |
| POST | `/lead` | 연락처(리드) 저장 |

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

- `_reference/naver_tracker.py` — **절대 수정 금지**.
- `backend/core/` 파일만 수정.
- `.env`는 Git 업로드 금지 (`.gitignore` 처리됨).
- 진단 1회 약 60~90초 (병렬 10키워드 + 경쟁사 동시).

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

# 5. 서버 실행
python -m uvicorn backend.main:app --loop none --reload
```

브라우저에서 http://localhost:8000/ 열리면 완료.

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

- [ ] **30일 리뷰 수집이 차단을 유발하는지 모니터링** — A묶음에서 방문자 리뷰 탭 1회 방문으로 30일 리뷰수 수집 추가. 네이버 IP 차단("과도한 접근 요청") 빈도가 늘어나는지 관찰 필요. 차단 잦으면 수집 주기·스크롤 횟수 조정 또는 비활성화 검토.
- [ ] **30일 리뷰수 정확도 개선** — 감동식당(방문자 31,398개)에서 30일 5개로 과소집계. 연도 없는 "M.D" 포맷·스크롤 부족으로 누락 추정. best-effort 한계, 정렬·포맷 파싱 보강 필요.
- [ ] **기회 키워드 추천** — 현재 키워드 외에 노릴 만한 새 키워드 발굴 기능 (추후)
- [ ] **광고 집행 자동 감지** — 현재 체크박스 수동 입력 → 추후 실제 감지 구현
- [ ] **키워드 조합 단어 잘림 문제** — "돼지등갈비" → "돼지등" 등. `keywords.py` 민감 로직, 우선순위 낮음
- [ ] **별점 없는 매장 표시** — null 처리 정책 결정 (A묶음 리뷰관리 축 개선 시 함께)
- [ ] **SEO 점수 차등 검증** — 약한 매장으로 100점 도배 문제 확인 필요
- [ ] **버튼 동작 구현** — 카톡 리포트(리드 게이트) / PWA 설치 / 카톡 공유 (추후 단계)
