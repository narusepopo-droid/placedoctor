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

### A 묶음 — 데이터·로직 (먼저 진행)

**4축 이름 변경** (`scoring.py`, `main.py` HTML 동시 수정)
- SEO → **검색노출(SEO)**
- 콘텐츠 → **리뷰관리**
- 활성도 → **최근활동**
- 광고 → **키워드광고**

**"최근활동" 축 구성 변경**
- 세부항목: 최근 리뷰 날짜 + 최근 30일 방문자 리뷰수 + 정보 최신성
- 현재는 최근 리뷰 날짜만 반영 → 30일 내 리뷰수 데이터 추가 수집 필요 (scraper.py)

**"리뷰관리" 축 채점 개선**
- 방문자 리뷰 총수 + 블로그 리뷰 + 별점 합산
- 별점이 null인 매장은 별점 항목 제외하고 나머지로만 점수 계산

**"키워드광고" 축 → 체크박스 입력 방식으로 변경**
- 자동 감지 제거
- 입력 화면에 체크박스 추가: 플레이스광고 / 파워링크 / 지역소상공인광고 / 블로그체험단
- 모르는 업주를 위한 "플레이스 광고 예시 캡처" 이미지 첨부 예정 (성균님 준비)
- 체크된 항목 수에 따라 점수·코멘트 자동 반영

**순위 구간별 멘트 세분화** (규칙 기반 템플릿)
- 1~5위: "첫 화면 노출 중! 유지가 관건이에요."
- 6~10위: "2페이지 진입권. 조금만 더 올리면 첫 화면이에요."
- 11~15위: "아깝다! X계단만 올리면 첫 화면입니다."
- 16~20위: "노출은 되지만 실질 클릭은 적어요."
- 미노출: "이 키워드 검색자는 우리 매장을 못 찾아요."

**닥터 코멘트 강화** (3~4문장, 규칙 기반)
- 흐름: 강점 인정 → 핵심 약점 → 가장 아까운 기회 키워드 → 해결 방향
- 예: "방문자 리뷰 N개로 콘텐츠 기반이 탄탄해요. 다만 '△△ 맛집' 키워드에서 13위로, 첫 화면까지 3계단 남았어요. 리뷰를 10개만 더 쌓으면 충분히 뒤집을 수 있는 구간이에요."
- 데이터 기반 자동 생성, AI 호출 없이 규칙 템플릿으로

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

- [ ] **기회 키워드 추천** — 현재 키워드 외에 노릴 만한 새 키워드 발굴 기능 (추후)
- [ ] **광고 집행 자동 감지** — 현재 체크박스 수동 입력 → 추후 실제 감지 구현
- [ ] **키워드 조합 단어 잘림 문제** — "돼지등갈비" → "돼지등" 등. `keywords.py` 민감 로직, 우선순위 낮음
- [ ] **별점 없는 매장 표시** — null 처리 정책 결정 (A묶음 리뷰관리 축 개선 시 함께)
- [ ] **SEO 점수 차등 검증** — 약한 매장으로 100점 도배 문제 확인 필요
- [ ] **버튼 동작 구현** — 카톡 리포트(리드 게이트) / PWA 설치 / 카톡 공유 (추후 단계)
