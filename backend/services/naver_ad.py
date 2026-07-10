"""
네이버 검색광고 API — 키워드 월간 검색량 조회
"""
import hashlib
import hmac
import base64
import time
import os
import logging
import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.searchad.naver.com"
API_KEY = os.getenv("NAVER_AD_API_KEY", "")
SECRET_KEY = os.getenv("NAVER_AD_SECRET_KEY", "")
CUSTOMER_ID = os.getenv("NAVER_AD_CUSTOMER_ID", "")


def _generate_signature(timestamp: str, method: str, uri: str) -> str:
    """HMAC-SHA256 서명 생성"""
    message = f"{timestamp}.{method}.{uri}"
    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).digest()
    return base64.b64encode(signature).decode("utf-8")


def _get_headers(method: str, uri: str) -> dict:
    """API 요청 헤더 생성"""
    timestamp = str(int(time.time() * 1000))
    signature = _generate_signature(timestamp, method, uri)
    return {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Timestamp": timestamp,
        "X-API-KEY": API_KEY,
        "X-Customer": CUSTOMER_ID,
        "X-Signature": signature,
    }


async def get_search_volume(keywords: list[str]) -> dict[str, int | None]:
    """
    키워드 목록의 월간 검색량 조회

    Args:
        keywords: 검색할 키워드 목록 (최대 100개)

    Returns:
        {키워드: 월간검색량} 딕셔너리. 조회 실패 시 None.
    """
    if not API_KEY or not SECRET_KEY or not CUSTOMER_ID:
        logger.warning("네이버 광고 API 키가 설정되지 않음")
        return {kw: None for kw in keywords}

    if not keywords:
        return {}

    # 100개 제한
    keywords = keywords[:100]

    uri = "/keywordstool"
    method = "GET"

    result = {kw: None for kw in keywords}

    try:
        headers = _get_headers(method, uri)

        # 키워드별로 개별 조회 (한번에 여러개는 hintKeywords 방식)
        async with httpx.AsyncClient(timeout=30.0) as client:
            for kw in keywords:
                params = {
                    "hintKeywords": kw,
                    "showDetail": "1",
                }
                resp = await client.get(
                    f"{API_URL}{uri}",
                    headers=headers,
                    params=params,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    keyword_list = data.get("keywordList", [])

                    # 정확히 일치하는 키워드 찾기
                    for item in keyword_list:
                        if item.get("relKeyword", "").strip() == kw.strip():
                            # PC + 모바일 합산
                            pc = item.get("monthlyPcQcCnt", 0)
                            mo = item.get("monthlyMobileQcCnt", 0)
                            # "< 10" 같은 문자열 처리
                            if isinstance(pc, str):
                                pc = 10 if "<" in pc else int(pc) if pc.isdigit() else 0
                            if isinstance(mo, str):
                                mo = 10 if "<" in mo else int(mo) if mo.isdigit() else 0
                            result[kw] = (pc or 0) + (mo or 0)
                            break
                else:
                    logger.warning(f"키워드 검색량 조회 실패: {kw}, status={resp.status_code}")

    except Exception as e:
        logger.error(f"네이버 광고 API 오류: {e}")

    return result


async def get_single_search_volume(keyword: str) -> int | None:
    """단일 키워드 검색량 조회"""
    result = await get_search_volume([keyword])
    return result.get(keyword)
