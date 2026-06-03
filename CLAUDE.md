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
│   ├── main.py          # FastAPI 앱 진입점 (라우터, ProactorEventLoop 스레드)
│   ├── database.py      # SQLAlchemy 엔진·세션
│   ├── models.py        # DB 테이블 (clients, stores, keywords, rank_snapshots,
│   │                    #             store_details, competitors, score_snapshots, leads)
│   ├── schemas.py       # Pydantic 요청/응답 스키마
│   ├── crud.py          # DB 읽기·쓰기 (캐시 24h, 이력 조회, 리드 저장)
│   ├── core/
│   │   ├── scraper.py   # 핵심 크롤링 엔진
│   │   │                # - diagnose_store(): 통합 진단 래퍼
│   │   │                # - check_place_rank(): 플레이스 순위 검색
│   │   │                # - get_store_details(): 매장 상세정보 수집
│   │   │                # - find_competitor(): 경쟁사 탐색
│   │   │                # - check_blog_ranking_deep(): 블로그 순위 딥스캔
│   │   ├── keywords.py  # 키워드 자동 생성 (generate_keywords)
│   │   └── scoring.py   # 4축 점수 계산 (SEO 40% + 콘텐츠 35% + 활성도 25%)
│   ├── test_scraper.py  # CLI 테스트 진입점 (asyncio.run 직접 호출)
│   └── requirements.txt
├── .env                 # DB 접속정보 (GitHub 업로드 금지)
├── _reference/
│   └── naver_tracker.py # 원본 플마 참고용 — 절대 수정 금지, 읽기 전용
├── CLAUDE.md            # 이 파일
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

### 왜 --loop none 인가

uvicorn 0.48.0은 `--reload` 모드에서 Windows에 SelectorEventLoop를 강제한다.
Playwright는 ProactorEventLoop만 지원하므로 `NotImplementedError`가 발생.
`--loop none`을 쓰면 Python 기본 정책(Windows에서 ProactorEventLoop)이 적용된다.
추가로 `main.py`에서 전용 데몬 스레드로 ProactorEventLoop를 실행하고
`asyncio.run_coroutine_threadsafe()`로 진단 코루틴을 위임한다.

---

## DB 정보

```
Host    : localhost:5432
DB명    : placedoctor
User    : postgres
Password: postgres
URL     : postgresql://postgres:postgres@localhost:5432/placedoctor
```

`.env` 파일에 저장됨. `database.py`가 로드. 서버 시작 시 테이블 자동 생성.

---

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/` | 진단 HTML 화면 |
| GET | `/health` | 서버 상태 |
| POST | `/diagnose` | 매장 진단 (순위·리뷰·점수·경쟁사) |
| GET | `/store/{place_id}/history` | 과거 순위·점수 이력 |
| POST | `/lead` | 연락처(리드) 저장 |

`/diagnose` 요청 예시:
```json
{
  "store_name": "감동식당",
  "place_url": "https://naver.me/xLWAPRFZ",
  "force_refresh": false
}
```

---

## 주의 사항

- `_reference/naver_tracker.py` — 원본 플마. **절대 수정 금지**.
- `backend/core/` 파일만 수정할 것.
- `.env`는 Git에 올리지 않음 (`.gitignore` 처리됨).
- 진단 1회에 1~5분 소요 (키워드 20개 순차 크롤링).

---

## 추후 개선 목록 (TODO)

- [ ] **키워드 조합 시 단어 잘림 문제**
  - 예: "돼지등갈비" → "돼지등" 으로 잘려서 키워드 생성됨
  - `backend/core/keywords.py` 관련 로직, 민감하므로 신중히 수정 필요
  - 우선순위 낮음 — 디자인 완성 이후로 미룸

- [ ] **별점 없는 매장 화면 표시 방식 결정**
  - 별점이 null인 경우 UI에서 어떻게 표시할지 정책 결정 필요

- [ ] **SEO 점수 차등 문제**
  - 순위가 좋은 매장은 SEO가 모두 100점으로 나옴
  - 약한 매장 테스트로 점수 차등이 실제로 나타나는지 검증 필요
  - `backend/core/scoring.py`의 가중치·임계값 조정 고려

- [ ] **광고 점수 축 실제 구현**
  - 현재 `scoring.py`에 자리만 있고 `ad = None` 반환
  - 네이버 광고(파워링크 등) 노출 여부 탐지 로직 구현 필요
