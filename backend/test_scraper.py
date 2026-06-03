"""
플레이스닥터 엔진 테스트

사용법:
    python backend/test_scraper.py "JW메리어트호텔 서울" "https://map.naver.com/p/entry/place/11583195"
    python backend/test_scraper.py "매장명"   # URL 없이 키워드만 자동생성
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core.scraper import diagnose_store

logging.basicConfig(level=logging.INFO, format="%(message)s")


def _fmt(v, suffix=""):
    return f"{v}{suffix}" if v is not None else "(없음)"

def _fmt_gap(v):
    if v is None: return "(비교불가)"
    if v > 0:     return f"{v}개 뒤처짐"
    if v < 0:     return f"{abs(v)}개 앞섬"
    return "동률"


async def main():
    if len(sys.argv) < 2:
        print("사용법: python backend/test_scraper.py <매장명> [네이버플레이스URL]")
        sys.exit(1)

    store_name = sys.argv[1]
    place_url  = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"\n{'='*55}")
    print(f"  플레이스닥터 진단: {store_name}")
    if place_url:
        print(f"  URL: {place_url}")
    print(f"{'='*55}\n")

    r = await diagnose_store(store_name=store_name, place_url=place_url)

    # ── 우리 매장 ──────────────────────────────────────────────────────────
    print("[우리 매장]")
    print(f"  플레이스ID : {r['place_id'] or '(없음)'}")
    print(f"  주소       : {r['address'] or '(없음)'}")
    print(f"  업종       : {r['category'] or '(없음)'}")
    print(f"  별점       : {_fmt(r['star_score'])}")
    print(f"  리뷰 수    : 방문자 {_fmt(r['visitor_reviews'])} / 블로그 {_fmt(r['blog_reviews'])}")
    print(f"  사진 수    : {_fmt(r['photo_count'])}")
    print(f"  최근 리뷰  : {_fmt(r['latest_review_date'])}")

    # ── 키워드별 순위 ──────────────────────────────────────────────────────
    print(f"\n[키워드별 순위]")
    found_any = False
    for item in r["place_results"]:
        kw, rank = item["keyword"], item["rank"]
        if rank:
            print(f"  ✅ {kw} → {rank}위")
            found_any = True
        else:
            print(f"  ─  {kw} → 30위 밖")
    if not found_any:
        print("  30위 이내 노출 없음")

    # ── 경쟁사 비교 ────────────────────────────────────────────────────────
    comp = r.get("competitor", {})
    comp_d = comp.get("details", {})
    if comp.get("competitor_id"):
        print(f"\n[경쟁사 1위 — place_id: {comp['competitor_id']}]")
        print(f"  업종       : {comp_d.get('category') or '(없음)'}")
        print(f"  별점       : {_fmt(comp_d.get('star_score'))}")
        print(f"  리뷰 수    : 방문자 {_fmt(comp_d.get('visitor_reviews'))} / 블로그 {_fmt(comp_d.get('blog_reviews'))}")
        gap = comp.get("gap", {})
        print(f"\n[격차]")
        print(f"  방문자 리뷰 : {_fmt_gap(gap.get('visitor_reviews'))}")
        print(f"  블로그 리뷰 : {_fmt_gap(gap.get('blog_reviews'))}")
        my_r = comp.get("my_rank")
        print(f"  검색 순위   : {'1위' if not my_r else f'{my_r}위 (1위와 {my_r-1}계단 차이)'}")
    else:
        print("\n[경쟁사] 탐색 실패 또는 우리 매장이 1위")

    # ── 4축 점수 ───────────────────────────────────────────────────────────
    sc = r.get("scores", {})
    if sc:
        print(f"\n[점수]")
        print(f"  SEO      : {sc.get('seo', 0)}점")
        print(f"  콘텐츠   : {sc.get('content', 0)}점")
        print(f"  활성도   : {sc.get('activity', 0)}점")
        print(f"  광고     : (미구현)")
        print(f"  종합     : {sc.get('total', 0)}점")

    print(f"\n{'='*55}")
    print("  진단 완료")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    asyncio.run(main())
