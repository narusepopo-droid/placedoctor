"""
미등록 토큰 검색량 백그라운드 조회 (cron 전용).

분석 도중이 아니라 별도로 돌려, pending 상태이면서 검색량이 아직 없는 토큰의
네이버 월간 검색량을 채운다. → 이용자 분석 속도에 전혀 영향 없음.

관리자는 관리자페이지에서 채워진 검색량을 보고 승인/거절만 판단하면 됨.

크론 등록 예 (서버):
  */30 * * * * cd /home/ubuntu/placedoctor && venv/bin/python fetch_search_volumes.py >> search_volume.log 2>&1
"""
import asyncio
import logging

from backend.database import SessionLocal
from backend import crud
from backend.services.naver_ad import get_search_volume

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("fetch_search_volumes")


async def main():
    db = SessionLocal()
    try:
        tokens = crud.get_pending_tokens_for_volume_check(db, limit=50)
        if not tokens:
            logger.info("검색량 조회 필요 토큰 없음")
            return
        keyword_list = [t.token for t in tokens]
        logger.info(f"검색량 조회 대상 {len(keyword_list)}개")
        volumes = await get_search_volume(keyword_list)
        updated = 0
        for t in tokens:
            vol = volumes.get(t.token)
            if vol is not None:
                crud.update_token_search_volume(db, t.id, vol)
                updated += 1
                logger.info(f"  {t.token}: {vol:,}회/월")
        logger.info(f"완료: {updated}/{len(tokens)}개 갱신")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
