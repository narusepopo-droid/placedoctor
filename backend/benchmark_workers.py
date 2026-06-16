"""
워커 수 최적화 벤치마크 스크립트
search.naver 키워드 랭킹 검색만 측정 (m.place 안 건드림)
"""
import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core.scraper import (
    create_browser,
    close_browser,
    create_stealth_page,
    _fetch_place_ranking,
)

# 테스트용 고정 키워드셋 (실제 매장 분석에서 나올 법한 키워드 15개)
TEST_KEYWORDS = [
    "강남역 맛집",
    "홍대 카페",
    "이태원 레스토랑",
    "명동 음식점",
    "신촌 술집",
    "건대 맛집",
    "압구정 카페",
    "잠실 맛집",
    "여의도 점심",
    "판교 맛집",
    "성수동 카페",
    "을지로 맛집",
    "합정 브런치",
    "연남동 맛집",
    "서울역 음식점",
]


async def benchmark_single_keyword(page, keyword):
    """단일 키워드 검색 시간 측정"""
    start = time.perf_counter()
    try:
        await _fetch_place_ranking(page, keyword, safe_mode=True)
        elapsed = time.perf_counter() - start
        return elapsed, True
    except Exception as e:
        elapsed = time.perf_counter() - start
        print(f"  [오류] {keyword}: {e}")
        return elapsed, False


async def benchmark_workers(n_workers: int, keywords: list[str]):
    """
    n_workers개 페이지로 keywords를 병렬 처리하고 총 wall-clock 시간 측정
    """
    print(f"\n{'='*60}")
    print(f"워커 수: {n_workers}, 키워드 수: {len(keywords)}")
    print(f"{'='*60}")

    playwright, browser, context = await create_browser()

    # 워커 페이지 생성
    pages = [await create_stealth_page(context) for _ in range(n_workers)]

    wall_start = time.perf_counter()

    # 라운드 로빈으로 키워드 분배
    kw_times = []

    async def worker_task(page, kw_list):
        """한 워커가 자기 키워드 리스트를 순차 처리"""
        worker_results = []
        for kw in kw_list:
            elapsed, success = await benchmark_single_keyword(page, kw)
            worker_results.append((kw, elapsed, success))
            print(f"  {kw}: {elapsed:.2f}s {'OK' if success else 'FAIL'}")
        return worker_results

    # 키워드를 워커에 분배
    kw_per_worker = [[] for _ in range(n_workers)]
    for i, kw in enumerate(keywords):
        kw_per_worker[i % n_workers].append(kw)

    # 병렬 실행
    tasks = [worker_task(pages[i], kw_per_worker[i]) for i in range(n_workers)]
    all_results = await asyncio.gather(*tasks)

    wall_elapsed = time.perf_counter() - wall_start

    # 결과 집계
    total_kw_time = 0
    success_count = 0
    for worker_results in all_results:
        for kw, elapsed, success in worker_results:
            kw_times.append(elapsed)
            total_kw_time += elapsed
            if success:
                success_count += 1

    avg_kw_time = total_kw_time / len(keywords) if keywords else 0
    parallelism = total_kw_time / wall_elapsed if wall_elapsed > 0 else 0

    print(f"\n--- 결과 (워커 {n_workers}개) ---")
    print(f"총 wall-clock: {wall_elapsed:.1f}초")
    print(f"키워드당 평균: {avg_kw_time:.2f}초")
    print(f"병렬 효율: {parallelism:.2f}x (이상적={n_workers}x)")
    print(f"성공률: {success_count}/{len(keywords)}")

    # 정리
    for page in pages:
        await page.close()
    await close_browser(playwright, browser)

    return {
        "n_workers": n_workers,
        "wall_clock": wall_elapsed,
        "avg_per_kw": avg_kw_time,
        "parallelism": parallelism,
        "success": success_count,
        "total": len(keywords),
    }


async def main():
    print("=" * 60)
    print("워커 수 벤치마크 (search.naver 랭킹 검색만)")
    print(f"키워드 {len(TEST_KEYWORDS)}개 고정 셋으로 측정")
    print("=" * 60)

    worker_counts = [2, 3, 4, 6]
    results = []

    for n in worker_counts:
        result = await benchmark_workers(n, TEST_KEYWORDS)
        results.append(result)
        # 브라우저 재시작 간 짧은 대기
        await asyncio.sleep(2)

    # 최종 비교
    print("\n" + "=" * 60)
    print("최종 비교")
    print("=" * 60)
    print(f"{'워커':>6} | {'Wall-clock':>12} | {'키워드당':>10} | {'병렬효율':>10}")
    print("-" * 50)

    best = min(results, key=lambda x: x["wall_clock"])
    for r in results:
        marker = " <-BEST" if r == best else ""
        print(f"{r['n_workers']:>6} | {r['wall_clock']:>10.1f}s | {r['avg_per_kw']:>8.2f}s | {r['parallelism']:>8.2f}x{marker}")

    print(f"\n최적 워커 수: {best['n_workers']} (wall-clock {best['wall_clock']:.1f}초)")

    # 30개 키워드 추정
    estimated_30 = best["wall_clock"] * (30 / len(TEST_KEYWORDS))
    print(f"30개 키워드 추정: {estimated_30:.0f}초 ({estimated_30/60:.1f}분)")


if __name__ == "__main__":
    asyncio.run(main())
