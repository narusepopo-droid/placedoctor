import json
from backend.core.keywords import generate_keywords

# 백세돼지국밥 테스트
baekse = generate_keywords(
    store_name="백세돼지국밥",
    category="음식점,국밥",
    address="서울 노원구 상계동",
    menu_items=["돼지국밥", "순대국밥", "수육"],
    official_keywords=["상계동 국밥", "노원 맛집"],
    nearby_station="노원역",
    keyword_list=["상계동국밥", "노원돼지국밥", "상계맛집", "노원역맛집"]
)

# 감동식당 테스트
gamdong = generate_keywords(
    store_name="감동식당",
    category="음식점,한식",
    address="부산 해운대구 우동",
    menu_items=["삼겹살", "목살", "된장찌개"],
    official_keywords=["해운대 맛집", "우동 고기집"],
    nearby_station="해운대역",
    keyword_list=["해운대맛집", "우동삼겹살", "해운대고기집", "부산맛집", "센텀맛집"]
)

result = {
    "백세돼지국밥": {
        "count": len(baekse),
        "keywords": baekse[:30]
    },
    "감동식당": {
        "count": len(gamdong),
        "keywords": gamdong[:30]
    }
}

with open("test_kw_compare.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"백세돼지국밥: {len(baekse)}개")
print(f"감동식당: {len(gamdong)}개")
