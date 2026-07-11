# -*- coding: utf-8 -*-
"""
지역 우선순위·구 화이트리스트·지번 동추출 회귀 테스트 (2026-07-11 세션 로직 잠금).
  - 정렬: 동 > 역 > 랜드마크 > 구(화이트리스트 bare) > 시
  - 구-접미(강남구) 생성 안 함, 화이트리스트 구만 bare
  - 지번(주소에 동 포함)에서 동 추출
실행: python test_keywords_location.py   (종료코드 0=통과, 1=실패)
"""
import sys
from backend.core.keywords import generate_keywords as G

CASES = []
def case(label, kwargs, no_first=(), has_first=(), order=(), note=""):
    # no_first: 이 지역이 첫토큰으로 절대 안 나와야
    # has_first: 이 지역이 첫토큰으로 반드시 나와야
    # order: [(A,B), ...] A가 B보다 먼저 등장(첫 등장 인덱스)
    CASES.append((label, kwargs, tuple(no_first), tuple(has_first), tuple(order), note))

# ── 정렬: 동 > 역 > 구 (우아성형외과 = 강남대로 도로명 + 논현동 지번 + 신논현역) ──
case("우아성형외과 — 동>역, 강남구 제외",
     dict(store_name='우아성형외과의원', category='성형외과',
          address='서울 강남구 강남대로 492 HM타워 서울 강남구 논현동 165-8', menu_items=[],
          official_keywords=['신논현성형외과'], nearby_stations=['신분당신논현역','신논현역'],
          keyword_list=['성형외과','신논현성형외과']),
     no_first=('강남구','신분당신논현역'), has_first=('논현동','신논현역','강남'),
     order=[('논현동','강남'),('신논현역','강남')])

# ── 감동식당 = 노원구 상계동 + 노원역 (노원구→노원, 동/역이 노원보다 앞) ──
case("감동식당 — 상계동>노원역>노원(구레벨)",
     dict(store_name='감동식당 노원본점', category='갈비,고기집',
          address='서울 노원구 한글비석로47길 58 상계동', menu_items=[],
          official_keywords=['상계동갈비찜'], nearby_stations=['노원역'],
          keyword_list=['상계동갈비찜']),
     no_first=('노원구',), has_first=('상계동','노원역','노원'),
     order=[('상계동','노원'),('노원역','노원')])

# ── 구 화이트리스트: 서울 9개 + 지정분만, 나머지 제외 ──
case("중구 신당동 — 명동/을지로/중구 전부 제외",
     dict(store_name='테스트식당', category='한식', address='서울 중구 다산로 100 신당동',
          menu_items=[], official_keywords=['신당동맛집'], nearby_stations=['신당역'],
          keyword_list=['신당동맛집']),
     no_first=('명동','을지로','중구'), has_first=('신당동',))
case("대구 수성구(미등록) — 수성/수성구 제외",
     dict(store_name='테스트', category='카페', address='대구 수성구 동대구로 100 범어동',
          menu_items=[], official_keywords=['범어동카페'], nearby_stations=[],
          keyword_list=['범어동카페']),
     no_first=('수성','수성구'), has_first=('범어동',))
case("성남 분당구(등록) — 분당(O)/분당구(X)",
     dict(store_name='테스트', category='카페', address='경기 성남시 분당구 판교로 100 정자동',
          menu_items=[], official_keywords=['정자동카페'], nearby_stations=['정자역'],
          keyword_list=['정자동카페']),
     no_first=('분당구',), has_first=('분당','정자동'))
case("부산 해운대구(등록) — 해운대(O)/해운대구(X)",
     dict(store_name='테스트', category='카페', address='부산 해운대구 해운대해변로 100 우동',
          menu_items=[], official_keywords=['우동카페'], nearby_stations=[],
          keyword_list=['우동카페']),
     no_first=('해운대구',), has_first=('해운대',))

# ── 소도시(구/역 없음): 시·랜드마크가 자동 최상위 (제천) ──
case("제천(소도시) — 시 검출됨, 구 없음",
     dict(store_name='테스트식당', category='한식', address='충북 제천시 청풍면 청풍호로 100',
          menu_items=[], official_keywords=['제천맛집'], nearby_stations=[],
          keyword_list=['제천맛집']),
     has_first=('제천',))

def run():
    all_pass = True
    for label, kwargs, no_first, has_first, order, note in CASES:
        ks = G(**kwargs)
        pos = {}
        for i, k in enumerate(ks):
            if ' ' in k:
                loc = k.split()[0]
                if loc not in pos:
                    pos[loc] = i
        firsts = set(pos.keys())
        bad = [x for x in no_first if x in firsts]
        miss = [x for x in has_first if x not in firsts]
        ord_fail = [(a, b) for a, b in order
                    if a in pos and b in pos and pos[a] >= pos[b]]
        ok = not bad and not miss and not ord_fail
        all_pass = all_pass and ok
        print(("PASS" if ok else "FAIL") + f" {label} ({len(ks)}개)")
        if bad:      print(f"   ✗ 안나와야 할 지역 등장: {bad}")
        if miss:     print(f"   ✗ 나와야 할 지역 누락: {miss}")
        if ord_fail: print(f"   ✗ 순서 위반(앞이어야 하는데 뒤): {ord_fail}")
    print("=" * 60)
    print("종합: " + ("✓ 전체 통과" if all_pass else "✗ 일부 실패"))
    return all_pass

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
