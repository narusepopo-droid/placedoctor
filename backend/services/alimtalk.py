"""
알리고 알림톡 API 연동 모듈

템플릿:
- UJ_0602: 알림등록문구 (알림 신청 완료)
- UJ_0612: 순위보고리포트 (주간 순위 리포트)
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

TPL_SIGNUP = os.getenv("ALIGO_TPL_SIGNUP", "UJ_0602")
TPL_WEEKLY = os.getenv("ALIGO_TPL_WEEKLY", "UJ_0612")
TESTMODE = os.getenv("ALIGO_TESTMODE", "N")


def _record_log(template_key: str, template_code: str, phone: str,
                store_name: str, result: dict) -> None:
    """발송 결과를 alimtalk_logs 테이블에 기록 (실패해도 발송 흐름엔 영향 없음)."""
    try:
        from ..database import SessionLocal
        from .. import crud
        code = result.get("code") if isinstance(result, dict) else None
        success = str(code) == "0"
        db = SessionLocal()
        try:
            crud.create_alimtalk_log(
                db,
                template_key=template_key,
                template_code=template_code,
                phone=phone,
                store_name=store_name,
                success=success,
                result_code=code if code is not None else "",
                message=(result.get("message", "") if isinstance(result, dict) else str(result)),
            )
        finally:
            db.close()
    except Exception as e:
        print(f"[알림톡] 이력 기록 실패: {e}")


def _get_extra_text(template_key: str) -> str:
    """DB에서 추가문구 가져오기"""
    try:
        from ..database import SessionLocal
        from .. import crud
        db = SessionLocal()
        try:
            tpl = crud.get_alim_template(db, template_key)
            return tpl.extra_text if tpl and tpl.extra_text else ""
        finally:
            db.close()
    except Exception as e:
        print(f"[알림톡] 추가문구 조회 실패: {e}")
        return ""


async def send_signup_alimtalk(phone: str, store_name: str, day_of_week: str = "월요일") -> dict:
    """
    알림 신청 완료 알림톡 발송 (UJ_0602)

    템플릿 변수: #{매장명}, #{요일}, #{추가문구}
    """
    extra_text = _get_extra_text("signup")

    # 템플릿 (UJ_0602)과 정확히 일치해야 함
    message = (
        f"[플레이스랭킹] 순위 알림 신청이 완료되었습니다.\n\n"
        f"{store_name}님의 플레이스 순위 모니터링을 시작합니다.\n\n"
        f"매주 {day_of_week}에 플레이스 키워드 순위 변화를 정리하여 보내드립니다.\n"
        f"{extra_text}"
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
        "subject_1": "알림등록문구",
        "message_1": message,
        "button_1": button_json,
        "testmode_yn": TESTMODE,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(ALIGO_ENDPOINT, data=data)
            result = response.json()
            print(f"[알림톡] phone={phone[-4:]}, tpl={TPL_SIGNUP}, result={result}")
            _record_log("signup", TPL_SIGNUP, phone, store_name, result)
            return result
    except Exception as e:
        print(f"[알림톡] 발송 실패: {e}")
        err = {"code": -1, "message": str(e)}
        _record_log("signup", TPL_SIGNUP, phone, store_name, err)
        return err


async def send_weekly_alimtalk(
    phone: str,
    store_name: str,
    keyword: str,
    last_rank: int | str,
    this_rank: int | str,
) -> dict:
    """
    주간 순위 리포트 알림톡 발송 (UJ_0612)

    템플릿 변수: #{매장명}, #{키워드}, #{지난순위}, #{이번순위}, #{추가문구}
    """
    last_str = str(last_rank) if last_rank else "-"
    this_str = str(this_rank) if this_rank else "-"
    extra_text = _get_extra_text("weekly")

    # 강조 타이틀 (파란색 헤더)
    emtitle = "이번주 플레이스 순위 리포트"

    # 템플릿 (UJ_0612)과 정확히 일치해야 함
    message = (
        f"[플레이스랭킹] {store_name} 이번주 순위 리포트\n\n"
        f"대표 키워드 '{keyword}'\n"
        f"{last_str}위 → {this_str}위\n\n"
        f"{store_name}의 플레이스 전체 키워드별\n"
        f"순위변화를 확인하실 수 있습니다.\n\n"
        f"경쟁 매장 변화까지 리포트로\n"
        f"정리해 두었으니 확인해 보세요.\n\n"
        f"{extra_text}"
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
        "subject_1": "순위보고리포트",
        "emtitle_1": emtitle,
        "message_1": message,
        "button_1": button_json,
        "testmode_yn": TESTMODE,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(ALIGO_ENDPOINT, data=data)
            result = response.json()
            print(f"[알림톡] phone={phone[-4:]}, tpl={TPL_WEEKLY}, result={result}")
            _record_log("weekly", TPL_WEEKLY, phone, store_name, result)
            return result
    except Exception as e:
        print(f"[알림톡] 발송 실패: {e}")
        err = {"code": -1, "message": str(e)}
        _record_log("weekly", TPL_WEEKLY, phone, store_name, err)
        return err
