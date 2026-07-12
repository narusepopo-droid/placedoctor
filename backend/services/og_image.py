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

    img = Image.new("RGB", (W, H), _GREEN)
    d = ImageDraw.Draw(img)

    # 배경: 초록 세로 그라데이션 (이미지 프레임 = 임팩트)
    top, bot = (34, 197, 94), (21, 128, 61)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(
            int(top[0] + (bot[0] - top[0]) * t),
            int(top[1] + (bot[1] - top[1]) * t),
            int(top[2] + (bot[2] - top[2]) * t)))

    # 흰 카드 패널 (초록 배경 위에 떠 있는 느낌)
    PX0, PY0, PX1, PY1 = 40, 40, W - 40, H - 40
    d.rounded_rectangle([PX0, PY0, PX1, PY1], radius=34, fill=_WHITE)

    # 브랜드
    d.text((86, 80), "플레이스랭킹", font=_font(44), fill=_GREEN)
    d.text((88, 142), "네이버 플레이스 순위 무료 진단", font=_font(27), fill=_GRAY)

    # 점수 링 (우측, 크게)
    g, gc = _grade(score)
    cx, cy, r = 995, 232, 128
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=_LIGHT, width=20)
    sweep = 360 * max(0, min(100, score)) / 100
    d.arc([cx - r, cy - r, cx + r, cy + r], start=-90, end=-90 + sweep, fill=gc, width=20)
    d.text((cx, cy - 14), str(score), font=_font(108), fill=gc, anchor="mm")
    d.text((cx, cy + 58), "점", font=_font(30), fill=_GRAY, anchor="mm")
    d.text((cx, cy + r + 34), f"{g}등급", font=_font(32), fill=gc, anchor="mm")

    # 매장명 (좌측, 링과 안 겹치게 폭 자동 축소)
    name_font = _fit_font(d, store, 720, 78, 42)
    d.text((86, 232), store, font=name_font, fill=_DARK)
    if category:
        d.text((88, 232 + name_font.size + 20), category, font=_font(33), fill=_GRAY)

    # 핵심 한 줄 (라이트 그린 카드)
    d.rounded_rectangle([86, 430, W - 86, 524], radius=22, fill=(240, 253, 244))
    if best:
        line = f"‘{best['keyword']}’ {best['rank']}위" + (f"  ·  첫 화면 노출 {first_page}개" if first_page else "")
    else:
        line = "지금 내 가게 순위를 무료로 확인해보세요"
    line_font = _fit_font(d, line, 980, 42, 27)
    d.text((116, 477), line, font=line_font, fill=(21, 128, 61), anchor="lm")

    # 하단 URL (겹침 방지: URL 폭 측정 후 뒤 문구 배치)
    url_font = _font(31)
    d.text((86, 548), "placeranking.com", font=url_font, fill=_GREEN_D)
    url_w = d.textlength("placeranking.com", font=url_font)
    d.text((86 + url_w + 14, 552), "에서 내 가게 순위 무료 확인", font=_font(27), fill=_GRAY)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
