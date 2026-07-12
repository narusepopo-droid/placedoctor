# -*- coding: utf-8 -*-
"""공유용 OG 카드 이미지 생성 (카카오톡/링크 미리보기, 1200x630 PNG).

- 매장별 분석 결과(store_name/score/대표키워드)를 담은 카드 이미지를 생성.
- 카카오톡은 og:image가 충분히 크면(권장 1200x630) '큰 이미지 카드'로 표시.
- Pillow만 사용(브라우저 무관, <100ms). 한글 폰트는 서버의 나눔/노토 CJK 자동 탐색.
"""
import os
import io
import glob
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
_GREEN = (22, 163, 74)
_GREEN_D = (21, 128, 61)
_DARK = (17, 24, 39)
_GRAY = (107, 114, 128)
_LIGHT = (243, 244, 246)
_WHITE = (255, 255, 255)


def _find_font() -> str | None:
    cands = [
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "C:/Windows/Fonts/malgunbd.ttf",  # 로컬 개발(Windows)
        "C:/Windows/Fonts/malgun.ttf",
    ]
    cands += glob.glob("/usr/share/fonts/**/Nanum*Bold*.ttf", recursive=True)
    cands += glob.glob("/usr/share/fonts/**/Nanum*.ttf", recursive=True)
    cands += glob.glob("/usr/share/fonts/**/NotoSansCJK*.*", recursive=True)
    for c in cands:
        if os.path.exists(c):
            return c
    return None


_FONT_PATH = _find_font()


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont:
    if _FONT_PATH:
        return ImageFont.truetype(_FONT_PATH, size)
    return ImageFont.load_default()


def has_font() -> bool:
    return _FONT_PATH is not None


def _fit_font(draw, text, max_w, start_size, min_size=28):
    """텍스트가 max_w를 넘지 않는 최대 폰트 크기를 찾는다."""
    size = start_size
    while size > min_size:
        f = _font(size)
        w = draw.textlength(text, font=f)
        if w <= max_w:
            return f
        size -= 4
    return _font(min_size)


def _grade(score: int):
    if score >= 80:
        return "S", _GREEN
    if score >= 60:
        return "A", (59, 130, 246)
    if score >= 40:
        return "B", (249, 115, 22)
    return "C", (239, 68, 68)


def generate_card(data: dict) -> bytes:
    store = (data.get("store_name") or "내 가게").strip()[:24]
    category = (data.get("category") or "").strip()[:22]
    scores = data.get("scores") or {}
    try:
        score = int(round(float(scores.get("total") or 0)))
    except (TypeError, ValueError):
        score = 0

    results = [r for r in (data.get("place_results") or []) if r.get("rank")]
    results.sort(key=lambda r: r["rank"])
    best = results[0] if results else None
    first_page = len([r for r in results if r["rank"] <= 10])

    img = Image.new("RGB", (W, H), _WHITE)
    d = ImageDraw.Draw(img)

    # 상단 초록 바
    d.rectangle([0, 0, W, 14], fill=_GREEN)

    # 브랜드
    d.text((70, 56), "플레이스랭킹", font=_font(42), fill=_GREEN)
    d.text((72, 116), "네이버 플레이스 순위 무료 진단", font=_font(26), fill=_GRAY)

    # 매장명 (좌측, 폭에 맞춰 자동 축소)
    name_font = _fit_font(d, store, 780, 76, 40)
    d.text((70, 214), store, font=name_font, fill=_DARK)
    if category:
        d.text((72, 214 + name_font.size + 18), category, font=_font(32), fill=_GRAY)

    # 점수 링 (우측)
    g, gc = _grade(score)
    cx, cy, r = 1006, 224, 104
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=_LIGHT, width=16)
    # 점수 비율만큼 채우는 호(arc)
    sweep = 360 * max(0, min(100, score)) / 100
    d.arc([cx - r, cy - r, cx + r, cy + r], start=-90, end=-90 + sweep, fill=gc, width=16)
    d.text((cx, cy - 12), str(score), font=_font(86), fill=gc, anchor="mm")
    d.text((cx, cy + 46), "점", font=_font(26), fill=_GRAY, anchor="mm")
    d.text((cx, cy + r + 30), f"{g}등급", font=_font(30), fill=gc, anchor="mm")

    # 핵심 한 줄 (하단 라이트 카드)
    d.rounded_rectangle([70, 404, 1130, 508], radius=22, fill=_LIGHT)
    if best:
        line = f"‘{best['keyword']}’ {best['rank']}위" + (f"  ·  첫 화면 노출 {first_page}개" if first_page else "")
    else:
        line = "지금 내 가게 순위를 무료로 확인해보세요"
    line_font = _fit_font(d, line, 1000, 40, 26)
    d.text((100, 456), line, font=line_font, fill=_DARK, anchor="lm")

    # 하단 URL
    d.text((70, 556), "placeranking.com", font=_font(30), fill=_GREEN_D)
    d.text((320, 560), "에서 내 가게 순위를 무료로 확인하세요", font=_font(26), fill=_GRAY)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
