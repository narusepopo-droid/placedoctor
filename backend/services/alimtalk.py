"""
알리고 알림톡 API 연동 모듈

템플릿:
- 플레이스랭킹_신청완료: 구독 완료 시 발송
- 플레이스랭킹_주간리포트: 매주 월요일 순위 변화 발송

testmode_yn="Y" — 템플릿 승인 전까지 유지 (실제 발송 안 됨)
"""

import os
import logging
import httpx
from sqlalchemy.orm import Session

from ..database import SessionLocal
from .. import crud

logger = logging.getLogger(__name__)

ALIGO_API_KEY = os.getenv("ALIGO_API_KEY", "n743layhxv8a0qsae33jm0i5hnjxpphw")
ALIGO_USER_ID = os.getenv("ALIGO_USER_ID", "metpopo")
ALIGO_SENDERKEY = os.getenv("ALIGO_SENDERKEY", "5930d2efa3fd7ee36565c19861f37db1d9266052")
ALIGO_SENDER = os.getenv("ALIGO_SENDER", "031-000-0000")  # 알리고에 등록된 발신번호로 변경 필요
ALIGO_ENDPOINT = "https://kakaoapi.aligo.in/akv10/alimtalk/send/"

# 템플릿 코드 (카카오 승인 후 알리고에서 확인 필요)
TPL_SIGNUP = os.getenv("ALIGO_TPL_SIGNUP", "TEMP_SIGNUP")  # 승인 후 실제 코드로 교체
TPL_WEEKLY = os.getenv("ALIGO_TPL_WEEKLY", "TEMP_WEEKLY")  # 승인 후 실제 코드로 교체

# 테스트 모드 (Y: 테스트만, N: 실제 발송)
TESTMODE = os.getenv("ALIGO_TESTMODE", "Y")


def _get_extra_text(template_key: str) -> str:
    """alim_templates 테이블에서 추가문구 조회"""
    db = SessionLocal()
    try:
        tpl = crud.get_alim_template(db, template_key)
        return tpl.extra_text if tpl and tpl.extra_text else ""
    finally:
        db.close()


async def _send_alimtalk(
    phone: str,
    template_code: str,
    message: str,
    button_name: str = "순위 확인하기",
    button_url: str = "https://placeranking.com",
) -> dict:
    """알리고 알림톡 API 호출 (공통)"""
    button_json = (
        f'{{"button":[{{"name":"{button_name}",'
        f'"linkType":"WL","linkTypeName":"웹링크",'
        f'"linkMo":"{button_url}","linkPc":"{button_url}"}}]}}'
    )

    data = {
        "apikey": ALIGO_API_KEY,
        "userid": ALIGO_USER_ID,
        "senderkey": ALIGO_SENDERKEY,
        "tpl_code": template_code,
        "sender": ALIGO_SENDER,
        "receiver_1": phone,
        "recvname_1": "",
        "subject_1": "플레이스랭킹",
        "message_1": message,
        "button_1": button_json,
        "testmode_yn": TESTMODE,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(ALIGO_ENDPOINT, data=data)
            result = response.json()
            logger.info(f"[알림톡] phone={phone[-4:]}, tpl={template_code}, result={result}")
            return result
    except Exception as e:
        logger.error(f"[알림톡] 발송 실패: {e}")
        return {"code": -1, "message": str(e)}


async def send_signup_alimtalk(phone: str, store_name: str, day_of_week: str = "월요일") -> dict:
    """
    신청 완료 알림톡 발송

    템플릿 변수:
    - #{매장명} = store_name
    - #{요일} = day_of_week
    """
    extra_text = _get_extra_text("signup")

    # 템플릿 메시지 (카카오 승인된 내용과 정확히 일치해야 함)
    message = (
        f"[플레이스랭킹] 주간 순위 알림 신청 완료\n\n"
        f"매장명: {store_name}\n"
        f"발송일: 매주 {day_of_week}\n\n"
        f"{extra_text}"
    ).strip()

    return await _send_alimtalk(
        phone=phone,
        template_code=TPL_SIGNUP,
        message=message,
        button_name="내 순위 확인하기",
    )


async def send_weekly_alimtalk(
    phone: str,
    store_name: str,
    keyword: str,
    last_rank: int | str,
    this_rank: int | str,
) -> dict:
    """
    주간 리포트 알림톡 발송

    템플릿 변수:
    - #{매장명} = store_name
    - #{키워드} = keyword
    - #{지난순위} = last_rank
    - #{이번순위} = this_rank
    """
    extra_text = _get_extra_text("weekly")

    # 순위 변화 텍스트
    last_str = f"{last_rank}위" if last_rank else "없음"
    this_str = f"{this_rank}위" if this_rank else "없음"

    if isinstance(last_rank, int) and isinstance(this_rank, int):
        diff = last_rank - this_rank
        if diff > 0:
            change = f"▲ {diff}단계 상승"
        elif diff < 0:
            change = f"▼ {abs(diff)}단계 하락"
        else:
            change = "- 유지"
    else:
        change = ""

    # 템플릿 메시지 (카카오 승인된 내용과 정확히 일치해야 함)
    message = (
        f"[플레이스랭킹] 주간 순위 리포트\n\n"
        f"매장명: {store_name}\n"
        f"대표 키워드: {keyword}\n\n"
        f"지난주: {last_str}\n"
        f"이번주: {this_str}\n"
        f"{change}\n\n"
        f"{extra_text}"
    ).strip()

    return await _send_alimtalk(
        phone=phone,
        template_code=TPL_WEEKLY,
        message=message,
        button_name="전체 리포트 보기",
    )
