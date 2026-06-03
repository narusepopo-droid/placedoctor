# 플레이스닥터

네이버 플레이스 순위 진단 도구.

## 현재 단계

**3단계 완료** — FastAPI 서버 + PostgreSQL DB

## 폴더 구조

```
placedoctor/
  backend/
    main.py         # FastAPI 앱 (진입점)
    database.py     # SQLAlchemy 엔진·세션
    models.py       # DB 테이블 8개
    schemas.py      # API 요청/응답 스키마
    crud.py         # DB 읽기·쓰기
    core/
      scraper.py    # 플레이스·블로그 순위 검색 엔진
      keywords.py   # 키워드 자동 생성
      scoring.py    # 4축 점수 계산
    test_scraper.py # CLI 테스트
    requirements.txt
  .env              # DB 접속정보 (GitHub 업로드 금지)
  _reference/
    naver_tracker.py  # 원본 플마 참고용 (읽기 전용)
```

## 설치

```bash
pip install -r backend/requirements.txt
playwright install chromium
```

## 서버 실행

프로젝트 루트(`placedoctor/`)에서:

```bash
python -m uvicorn backend.main:app --reload
```

서버가 뜨면 브라우저에서 확인:
- **API 문서**: http://localhost:8000/docs
- **헬스체크**: http://localhost:8000/health

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 서버 상태 확인 |
| POST | `/diagnose` | 매장 진단 (순위·리뷰·점수·경쟁사) |
| GET | `/store/{place_id}/history` | 과거 스냅샷 이력 |
| POST | `/lead` | 연락처(리드) 저장 |

### /diagnose 예시

```json
{
  "store_name": "감동식당",
  "place_url": "https://map.naver.com/p/entry/place/1234567890",
  "force_refresh": false
}
```

- 24시간 이내 동일 매장 결과가 있으면 DB 캐시 반환 (`cached: true`)
- `force_refresh: true` 로 강제 재크롤링 가능

## CLI 테스트 (화면 없이 터미널에서)

```bash
python backend/test_scraper.py "역삼 필라테스" "https://map.naver.com/p/entry/place/xxxxxx"
```
