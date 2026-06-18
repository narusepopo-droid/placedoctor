"""
알리고 알림톡 API 연동 모듈

템플릿:
- UI_7449: 알림 신청 완료
- UI_7456: 주간 순위 리포트
"""

import os
import logging
import httpx

logger = logging.getLogger(__name__)

ALIGO_API_KEY = os.getenv("ALIGO_API_KEY", "")
ALIGO_USER_ID = os.getenv("ALIGO_USER_ID", "")
ALIGO_SENDERKEY = os.getenv("ALIGO_SENDERKEY", "")
ALIGO_SENDER = os.getenv("ALIGO_SENDER", "")
ALIGO_ENDPOINT = "https://kakaoapi.aligo.in/akv10/alimtalk/send/"

TPL_SIGNUP = os.getenv("ALIGO_TPL_SIGNUP", "UI_7449")
TPL_WEEKLY = os.getenv("ALIGO_TPL_WEEKLY", "UI_7456")
TESTMODE = os.getenv("ALIGO_TESTMODE", "N")


async def send_signup_alimtalk(phone: str, store_name: str, day_of_week: str = "월요일") -> dict:
    """
    알림 신청 완료 알림톡 발송

    템플릿 변수: #{매장명}, #{요일} → 직접 치환해서 전송
    """
    # 변수를 직접 치환한 메시지
    message = (
        f"[플레이스랭킹] 순위 알림 신청이 완료되었습니다.\n\n"
        f"{store_name}님의 플레이스 순위 모니터링을 시작합니다.\n\n"
        f"매주 {day_of_week}에 키워드 순위 변화를 정리하여 보내드립니다."
    )

    button_json = '{"button":[{"name":"순위 확인하기","linkType":"WL","linkTypeName":"웹링크","linkMo":"https://placeranking.com","linkPc":"https://placeranking.com"}]}'

    data = {
        "apikey": ALIGO_API_KEY,
        "userid": ALIGO_USER_ID,
        "senderkey": ALIGO_SENDERKEY,
        "tpl_code": TPL_SIGNUP,
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
            print(f"[알림톡] phone={phone[-4:]}, tpl={TPL_SIGNUP}, result={result}")
            return result
    except Exception as e:
        print(f"[알림톡] 발송 실패: {e}")
        return {"code": -1, "message": str(e)}


async def send_weekly_alimtalk(
    phone: str,
    store_name: str,
    keyword: str,
    last_rank: int | str,
    this_rank: int | str,
) -> dict:
    """
    주간 순위 리포트 알림톡 발송

    템플릿 변수: #{매장명}, #{키워드}, #{지난순위}, #{이번순위} → 직접 치환해서 전송
    """
    last_str = str(last_rank) if last_rank else "-"
    this_str = str(this_rank) if this_rank else "-"

    # 변수를 직접 치환한 메시지
    message = (
        f"[플레이스랭킹] {store_name} 이번주 순위 리포트\n\n"
        f"대표 키워드 '{keyword}'\n"
        f"{last_str}위 → {this_str}위\n\n"
        f"경쟁 매장 변화까지 전체 리포트를 정리했습니다."
    )

    button_json = '{"button":[{"name":"키워드 전체 보기","linkType":"WL","linkTypeName":"웹링크","linkMo":"https://placeranking.com","linkPc":"https://placeranking.com"}]}'

    data = {
        "apikey": ALIGO_API_KEY,
        "userid": ALIGO_USER_ID,
        "senderkey": ALIGO_SENDERKEY,
        "tpl_code": TPL_WEEKLY,
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
            print(f"[알림톡] phone={phone[-4:]}, tpl={TPL_WEEKLY}, result={result}")
            return result
    except Exception as e:
        print(f"[알림톡] 발송 실패: {e}")
        return {"code": -1, "message": str(e)}
