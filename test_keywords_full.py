import json
from backend.core.keywords import generate_keywords

results = {}

# 1. CMS 신동탄 (학원) - 지시서 테스트 케이스
cms = generate_keywords(
    store_name="CMS 신동탄영재교육센터 사고력관",
    category="학원",
    address="경기 화성시 동탄구 영천동",
    menu_items=[],
    official_keywords=["동탄 조기수학", "동탄역 영재고입시", "동탄 사고력"],
    nearby_station="동탄역",
    keyword_list=["동탄 조기수학", "동탄 사고력", "동탄역 영재", "동탄구 조기수학교육"]
)
results["CMS_신동탄"] = {
    "count": len(cms),
    "pass": len(cms) >= 10 and "동탄 조기수학" in cms and "동탄역 영재" in cms,
    "keywords": cms[:15]
}

# 2. YS휘트니스 (헬스장) - 회귀 테스트
ys = generate_keywords(
    store_name="YS휘트니스",
    category="헬스장,피트니스센터",
    address="서울 노원구 상계동 123-45",
    menu_items=["PT", "개인레슨"],
    official_keywords=["노원 헬스장", "상계동 PT"],
    nearby_station="노원역",
    keyword_list=["노원 헬스장", "상계동 PT", "노원역 피트니스"]
)
results["YS휘트니스"] = {
    "count": len(ys),
    "pass": len(ys) >= 4 and any("헬스" in k for k in ys),
    "keywords": ys[:15]
}

# 3. 가짜 지역 필터 테스트 (세차, 타이어 등이 지역으로 잡히면 안됨)
fake_loc_test = generate_keywords(
    store_name="블랙박스할인매장",
    category="자동차용품",
    address="서울 강남구 역삼동",
    menu_items=[],
    official_keywords=["블랙박스 할인", "타이어 교체"],
    nearby_station="역삼역",
    keyword_list=["세차장추천", "타이어교체", "블랙박스설치"]
)
# "세차", "타이어", "블랙" 등이 지역으로 잡혀서 "세차 맛집" 같은 쓰레기가 생성되면 안됨
has_fake_loc = any(k.startswith("세차 ") or k.startswith("타이어 ") or k.startswith("블랙 ") for k in fake_loc_test)
results["가짜지역필터"] = {
    "count": len(fake_loc_test),
    "pass": not has_fake_loc,
    "keywords": fake_loc_test[:10],
    "note": "세차/타이어/블랙이 지역으로 잡히지 않아야 함"
}

# 4. 랜드마크 추출 테스트 (팔공산, 금호강 등)
landmark_test = generate_keywords(
    store_name="팔공산자연휴양림",
    category="캠핑장",
    address="대구 동구 용계동",
    menu_items=[],
    official_keywords=["팔공산 캠핑"],
    nearby_station="",
    keyword_list=["팔공산캠핑장", "금호강카페", "대구계곡"]
)
has_landmark = any("팔공산" in k for k in landmark_test) or any("금호강" in k for k in landmark_test)
results["랜드마크추출"] = {
    "count": len(landmark_test),
    "pass": has_landmark,
    "keywords": landmark_test[:15],
    "note": "팔공산/금호강이 지역으로 추출되어야 함"
}

# 5. 노선명 제거 테스트 (신분당선신논현역 → 신논현역)
station_test = generate_keywords(
    store_name="신논현맛집",
    category="음식점",
    address="서울 강남구 논현동",
    menu_items=[],
    official_keywords=[],
    nearby_station="신분당선신논현역",
    keyword_list=["신논현역맛집", "강남맛집"]
)
has_clean_station = any("신논현역" in k for k in station_test)
has_dirty_station = any("신분당선" in k for k in station_test)
results["노선명제거"] = {
    "count": len(station_test),
    "pass": has_clean_station and not has_dirty_station,
    "keywords": station_test[:10],
    "note": "신분당선 제거되고 신논현역만 남아야 함"
}

# 6. 쓰레기 키워드 필터 테스트 (8자+ 미식별 토큰)
garbage_test = generate_keywords(
    store_name="테스트매장",
    category="음식점",
    address="서울 마포구 합정동",
    menu_items=[],
    official_keywords=["합정맛집추천드립니다여러분"],  # 긴 쓰레기
    nearby_station="합정역",
    keyword_list=["합정 맛집", "마포구 맛집"]
)
has_garbage = any("추천드립니다여러분" in k for k in garbage_test)
results["쓰레기필터"] = {
    "count": len(garbage_test),
    "pass": not has_garbage,
    "keywords": garbage_test[:10],
    "note": "긴 쓰레기 토큰이 필터링되어야 함"
}

# 결과 출력
print("=" * 60)
print("키워드 로직 테스트 결과")
print("=" * 60)

all_pass = True
for name, data in results.items():
    status = "✓ PASS" if data["pass"] else "✗ FAIL"
    all_pass = all_pass and data["pass"]
    print(f"\n[{name}] {status} (키워드 {data['count']}개)")
    if "note" in data:
        print(f"  검증: {data['note']}")
    print(f"  샘플: {data['keywords'][:8]}")

print("\n" + "=" * 60)
print(f"종합 결과: {'✓ 모든 테스트 통과' if all_pass else '✗ 일부 테스트 실패'}")
print("=" * 60)

# JSON 저장
with open("test_keywords_result.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
