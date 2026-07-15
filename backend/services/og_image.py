# -*- coding: utf-8 -*-
"""공유용 OG 카드 이미지 생성 (카카오톡 모바일 최적화, 600x600 PNG).

- 카카오톡 모바일에서 좌우 잘림 방지를 위해 정사각형 비율 사용.
- 세로 배치로 중요 정보가 중앙에 오도록 구성.
"""
import os
import io
import glob
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

W, H = 600, 600
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
        "C:/Windows/Fonts/malgunbd.ttf",
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


def _fit_font(draw, text, max_w, start_size, min_size=18):
    """텍스트가 max_w를 넘지 않는 최대 폰트 크기를 찾는다."""
    size = start_size
    while size > min_size:
        f = _font(size)
        w = draw.textlength(text, font=f)
        if w <= max_w:
            return f
        size -= 2
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
    store = (data.get("store_name") or "내 가게").strip()[:16]
    category = (data.get("category") or "").strip()[:14]
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

    # 배경: 초록 세로 그라데이션
    top, bot = (34, 197, 94), (21, 128, 61)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(
            int(top[0] + (bot[0] - top[0]) * t),
            int(top[1] + (bot[1] - top[1]) * t),
            int(top[2] + (bot[2] - top[2]) * t)))

    # 흰 카드 패널
    PX0, PY0, PX1, PY1 = 24, 24, W - 24, H - 24
    d.rounded_rectangle([PX0, PY0, PX1, PY1], radius=24, fill=_WHITE)

    # 브랜드 (상단 중앙)
    d.text((W // 2, 56), "플레이스랭킹", font=_font(26), fill=_GREEN, anchor="mm")

    # 점수 링 (중앙)
    g, gc = _grade(score)
    cx, cy, r = W // 2, 200, 100
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=_LIGHT, width=16)
    sweep = 360 * max(0, min(100, score)) / 100
    d.arc([cx - r, cy - r, cx + r, cy + r], start=-90, end=-90 + sweep, fill=gc, width=16)
    d.text((cx, cy - 10), str(score), font=_font(80), fill=gc, anchor="mm")
    d.text((cx, cy + 46), "점", font=_font(24), fill=_GRAY, anchor="mm")
    d.text((cx, cy + r + 28), f"{g}등급", font=_font(26), fill=gc, anchor="mm")

    # 매장명 (중앙)
    name_font = _fit_font(d, store, 500, 44, 28)
    d.text((W // 2, 380), store, font=name_font, fill=_DARK, anchor="mm")
    if category:
        d.text((W // 2, 420), category, font=_font(22), fill=_GRAY, anchor="mm")

    # 핵심 한 줄 (하단)
    if best:
        line = f"'{best['keyword']}' {best['rank']}위"
        if first_page:
            line += f" · 첫 화면 {first_page}개"
    else:
        line = "내 가게 순위 무료 확인"
    line_font = _fit_font(d, line, 500, 24, 18)
    d.rounded_rectangle([50, 480, W - 50, 540], radius=14, fill=(240, 253, 244))
    d.text((W // 2, 510), line, font=line_font, fill=(21, 128, 61), anchor="mm")

    # 하단 URL
    d.text((W // 2, 568), "placeranking.com", font=_font(20), fill=_GREEN_D, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
