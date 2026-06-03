"""
플레이스닥터 엔진 테스트

사용법:
    python backend/test_scraper.py "역삼 필라테스" "https://map.naver.com/p/entry/place/xxxxxx"
    python backend/test_scraper.py "역삼 필라테스"  # URL 없이 키워드만 자동생성
"""

import asyncio
import logging
import sys
import os
import io

# Windows 한글/유니코드 깨짐 방지
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 프로젝트 루트를 sys.path에 추가 (어디서 실행해도 import 가능하게)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core.scraper import diagnose_store

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)


async def main():
    if len(sys.argv) < 2:
        print("사용법: python backend/test_scraper.py <매장명> [네이버플레이스URL]")
        sys.exit(1)

    store_name = sys.argv[1]
    place_url = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"\n{'='*50}")
    print(f"  플레이스닥터 진단 시작: {store_name}")
    if place_url:
        print(f"  URL: {place_url}")
    print(f"{'='*50}\n")

    result = await diagnose_store(store_name=store_name, place_url=place_url)

    print(f"\n[결과]")
    print(f"  매장명   : {result['store_name']}")
    print(f"  플레이스ID: {result['place_id'] or '(없음)'}")
    print(f"  주소     : {result['address'] or '(없음)'}")
    print(f"  업종     : {result['category'] or '(없음)'}")
    print(f"\n[키워드별 순위]")
    found_any = False
    for item in result["place_results"]:
        kw = item["keyword"]
        rank = item["rank"]
        if rank:
            print(f"  ✅ {kw} → {rank}위")
            found_any = True
        else:
            print(f"  ─  {kw} → 30위 밖 또는 미노출")

    if not found_any:
        print("\n  ⚠️  검색된 키워드에서 30위 이내 노출 없음")

    print(f"\n{'='*50}")
    print("  진단 완료")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    asyncio.run(main())
