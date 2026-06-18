"""
주간 리포트 발송 모듈 (스켈레톤)

실제 cron 스케줄은 EC2 crontab에 등록:
  0 9 * * 1 cd /home/ubuntu/placedoctor && /home/ubuntu/.local/bin/python -c "import asyncio; from backend.services.weekly_report import send_weekly_reports; asyncio.run(send_weekly_reports())"
"""

import json
import logging
import asyncio
from datetime import datetime, timezone

from ..database import SessionLocal
from .. import crud
from .alimtalk import send_weekly_alimtalk

logger = logging.getLogger(__name__)


def _get_best_keyword_with_ranks(place_id: str) -> tuple[str | None, int | None, int | None]:
    """
    매장의 대표 키워드와 지난주/이번주 순위를 반환.

    Returns:
        (keyword, last_rank, this_rank) 또는 (None, None, None)
    """
    db = SessionLocal()
    try:
        histories = (
            db.query(crud.models.AnalysisHistory)
            .filter(
                crud.models.AnalysisHistory.place_id == place_id,
                crud.models.AnalysisHistory.analysis_type == "place",
            )
            .order_by(crud.models.AnalysisHistory.analyzed_at.desc())
            .limit(2)
            .all()
        )

        if not histories:
            return (None, None, None)

        # 이번 주 (최신)
        latest = histories[0]
        top_keyword = None
        this_rank = None

        if latest.result_json:
            try:
                data = json.loads(latest.result_json)
                place_results = data.get("place_results", [])
                ranked = [p for p in place_results if p.get("rank")]
                if ranked:
                    best = min(ranked, key=lambda x: x["rank"])
                    top_keyword = best.get("keyword")
                    this_rank = best.get("rank")
            except Exception:
                pass

        if not top_keyword:
            return (None, None, None)

        # 지난 주 (이전)
        last_rank = None
        if len(histories) > 1:
            prev = histories[1]
            if prev.result_json:
                try:
                    data = json.loads(prev.result_json)
                    place_results = data.get("place_results", [])
                    for p in place_results:
                        if p.get("keyword") == top_keyword:
                            last_rank = p.get("rank")
                            break
                except Exception:
                    pass

        return (top_keyword, last_rank, this_rank)

    finally:
        db.close()


async def send_weekly_reports() -> dict:
    """
    주간 리포트 일괄 발송

    alarm_on=True인 모든 구독자에게 순위 변화 알림톡 발송.
    실제 발송은 템플릿 승인 후 testmode_yn="N"으로 변경해야 함.

    Returns:
        {"total": 전체수, "sent": 발송수, "skipped": 스킵수, "failed": 실패수}
    """
    db = SessionLocal()
    try:
        subscribers = crud.get_all_subscribers(db, alarm_on_only=True)
        logger.info(f"[주간리포트] 발송 대상: {len(subscribers)}명")

        total = len(subscribers)
        sent = 0
        skipped = 0
        failed = 0

        for sub in subscribers:
            place_id = sub.place_id
            if not place_id:
                logger.warning(f"[주간리포트] place_id 없음: id={sub.id}, phone={sub.phone[-4:]}")
                skipped += 1
                continue

            keyword, last_rank, this_rank = _get_best_keyword_with_ranks(place_id)

            if not keyword or this_rank is None:
                logger.warning(f"[주간리포트] 순위 데이터 없음: place_id={place_id}")
                skipped += 1
                continue

            try:
                result = await send_weekly_alimtalk(
                    phone=sub.phone,
                    store_name=sub.store_name,
                    keyword=keyword,
                    last_rank=last_rank,
                    this_rank=this_rank,
                )

                if result.get("code") == 0 or result.get("code") == "0":
                    sent += 1
                else:
                    logger.error(f"[주간리포트] 발송 실패: {result}")
                    failed += 1

            except Exception as e:
                logger.error(f"[주간리포트] 예외: {e}")
                failed += 1

            # 알리고 API 과부하 방지 (초당 10건 제한)
            await asyncio.sleep(0.15)

        result = {
            "total": total,
            "sent": sent,
            "skipped": skipped,
            "failed": failed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(f"[주간리포트] 완료: {result}")
        return result

    finally:
        db.close()


if __name__ == "__main__":
    # CLI 테스트용
    import asyncio
    logging.basicConfig(level=logging.INFO)
    asyncio.run(send_weekly_reports())
