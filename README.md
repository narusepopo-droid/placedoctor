# 플레이스닥터

네이버 플레이스 순위 진단 도구.

## 현재 단계

**1단계 완료** — 화면 없이 터미널에서 동작하는 순위 검색 엔진

## 폴더 구조

```
placedoctor/
  backend/
    core/
      scraper.py      # 플레이스·블로그 순위 검색 엔진
      keywords.py     # 키워드 자동 생성
    test_scraper.py   # CLI 테스트
    requirements.txt
  _reference/
    naver_tracker.py  # 원본 플마 참고용 (읽기 전용)
```

## 설치 및 실행

```bash
pip install -r backend/requirements.txt
playwright install chromium

python backend/test_scraper.py "역삼 필라테스" "https://map.naver.com/p/entry/place/xxxxxx"
```
