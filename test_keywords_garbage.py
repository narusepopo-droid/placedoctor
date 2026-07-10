# -*- coding: utf-8 -*-
"""
가짜 지명·쓰레기 키워드 회귀 테스트 (2026-07 55개 실매장 감사에서 발견한 실제 케이스).
목적: 여기 잡힌 쓰레기들이 앞으로 어떤 룰 변경에도 '다시는' 나오지 않도록 잠금.
      부정형(이 가짜 지명은 절대 안 나온다)이 핵심 — 이게 확실한 정답.
실행: python test_keywords_garbage.py   (종료코드 0=통과, 1=실패)
"""
import sys
from backend.core.keywords import generate_keywords as G

CASES = []
def case(label, kwargs, bad_first=(), must_have=(), bad_contains=(), note=""):
    CASES.append((label, kwargs, tuple(bad_first), tuple(must_have), tuple(bad_contains), note))

# ── 가짜 산/강/천 (서비스어 글자를 지명으로 오인) ────────────────────────────
case("끌레르 에스테틱 — 양재산/재산전산",
     dict(store_name='끌레르 에스테틱', category='피부,체형관리',
          address='서울 서초구 강남대로 224 한신휴플러스 305호', menu_items=[],
          official_keywords=['양재산전산후관리'], nearby_stations=['양재역'],
          keyword_list=['양재역피부관리','양재산전산후관리','양재역윤곽관리','서초피부관리']),
     bad_first=('양재산','재산전산','전산후관리'), must_have=('양재역','서초'))
case("YS휘트니스 — 부평산",
     dict(store_name='YS휘트니스 헬스PT 산곡점', category='헬스,PT',
          address='인천 부평구 마장로 334 산곡동', menu_items=[],
          official_keywords=['부평산곡헬스장'], nearby_stations=['산곡역'],
          keyword_list=['부평산곡헬스장','산곡동헬스','부평구PT']),
     bad_first=('부평산',))
case("빅포1982 — 돈내산(내돈내산)",
     dict(store_name='빅포1982 마곡점', category='베트남음식,쌀국수',
          address='서울 강서구 마곡중앙로 76 마곡동', menu_items=[],
          official_keywords=['마곡내돈내산'], nearby_stations=[],
          keyword_list=['마곡내돈내산','엠벨리쌀국수','내돈내산']),
     bad_first=('돈내산',))
case("화화돼지왕갈비 — 임제천/돈내산/제천청풍호반",
     dict(store_name='화화돼지왕갈비 제천청풍호반점', category='돼지갈비,고기집',
          address='충북 제천시 청풍면 청풍명월로4길 57 101', menu_items=[],
          official_keywords=['제천내돈내산'], nearby_stations=[],
          keyword_list=['제천청풍호반내돈내산','청풍돼지갈비','임제천고기집']),
     bad_first=('임제천','돈내산','제천청풍호반동','제천청풍호반'))

# ── 메뉴/일반명사가 가짜 구/동/리 ────────────────────────────────────────────
case("유평리 생선집 — 화덕생선구/고등어구/고등",
     dict(store_name='유평리 생선집 향동본점', category='생선구이',
          address='경기 고양시 덕양구 향동로 217 향동동',
          menu_items=['화덕생선구이','고등어구이'], official_keywords=['향동생선구이','고등어구이'],
          nearby_stations=[], keyword_list=['향동생선구이','고등어구이','화덕생선구이']),
     bad_first=('화덕생선구','고등어구','고등'))
case("준스이 — 규동(일식 메뉴)",
     dict(store_name='준스이', category='일식당', address='경기 부천시 원미구 상동로 69',
          menu_items=['규동'], official_keywords=['규동'], nearby_stations=['상동역'],
          keyword_list=['규동','부천우동','원미구혼밥']),
     bad_first=('규동',), must_have=('상동역','원미구'))
case("준스이 계양구청점 — 계양구청동",
     dict(store_name='준스이 계양구청점', category='일식당',
          address='인천 계양구 계산새로 89 1층 105호', menu_items=['우동'],
          official_keywords=['계양구청우동'], nearby_stations=['계양구청역'],
          keyword_list=['계양구청우동','계양구준스이','규동']),
     bad_first=('계양구청동','규동'), must_have=('계양구',))
case("광동숯불오리 — 고추장오리",
     dict(store_name='광동숯불오리', category='오리요리',
          address='경북 칠곡군 동명면 구덕길 40-6', menu_items=[],
          official_keywords=['고추장오리','칠곡오리고기'], nearby_stations=[],
          keyword_list=['고추장오리오리고기','칠곡가족']),
     bad_first=('고추장오리',), must_have=('칠곡',))

# ── 서울 전용 지명이 타지역에 ────────────────────────────────────────────────
case("육식문화(대전 중구) — 명동/을지로",
     dict(store_name='육식문화', category='육류,고기요리',
          address='대전 중구 목중로19번길 10 1층', menu_items=[],
          official_keywords=['중구룸고기집'], nearby_stations=[],
          keyword_list=['대전룸고기집','중촌동고기집']),
     bad_first=('명동','을지로'))

# ── 역 이름 조작/합성 ────────────────────────────────────────────────────────
case("거북솥삼겹살 — 털단지역(구로디지털단지역)",
     dict(store_name='거북솥삼겹살', category='삼겹살,고기집',
          address='서울 구로구 디지털로32길 97-28 1층 구로동', menu_items=[],
          official_keywords=['구로삼겹살'], nearby_stations=['구로디지털단지역','구로역'],
          keyword_list=['구로삼겹살','구디삼겹살','구로동고기집']),
     bad_first=('털단지역',))
case("에스와이커스텀 — 드마산역/김포골드마산역 → 마산역",
     dict(store_name='에스와이커스텀', category='튜닝',
          address='경기 김포시 김포한강8로194번길 143 1층 전체', menu_items=[],
          official_keywords=['김포유리막','김포PPF'], nearby_stations=['김포골드마산역'],
          keyword_list=['김포유리막','김포PPF','김포스팀세차','김포랩핑']),
     bad_first=('드마산역','김포골드마산역','김포골드'), must_have=('마산역','김포 유리막'))
case("자바누수 — 서북역(천안 서북구)",
     dict(store_name='자바누수', category='누수탐지',
          address='충남 천안시 서북구 불당26로 80 D101호 불당동', menu_items=[],
          official_keywords=['불당누수탐지'], nearby_stations=[],
          keyword_list=['불당동누수탐지','천안누수탐지','서북구청주누수탐지']),
     bad_first=('서북역',))

# ── 비사전 합성어 차단 (사전에 없는 단어는 조합 안 함) ───────────────────────
case("에스와이커스텀 — 랩핑전체부분/PPF전체부분/생보종생보종(비사전 합성어)",
     dict(store_name='에스와이커스텀', category='튜닝',
          address='경기 김포시 김포한강8로194번길 143 1층 전체',
          menu_items=['랩핑PPF전체부분','생보종생보종'],
          official_keywords=['김포랩핑','김포PPF전체부분','랩핑전체부분'],
          nearby_stations=['김포골드마산역'],
          keyword_list=['김포랩핑','김포PPF','김포유리막','김포스팀세차']),
     bad_contains=('전체부분','생보종'), must_have=('김포 랩핑','김포 PPF'))

# ── 회귀: 유지되어야 할 정상 동작 ────────────────────────────────────────────
case("[회귀] 캠핑장 랜드마크 유지",
     dict(store_name='금호강 오토캠핑장', category='캠핑장,야영장',
          address='대구 동구 팔공산로 123', menu_items=[],
          official_keywords=['금호강캠핑장','팔공산글램핑'], nearby_stations=[],
          keyword_list=['금호강캠핑장','팔공산글램핑','대구캠핑장']),
     must_have=('금호강','팔공산'))
case("[회귀] 대학 인식(경상대)",
     dict(store_name='백세돼지국밥', category='돼지국밥,한식',
          address='경남 진주시 가호로 52 호탄동', menu_items=[],
          official_keywords=['호탄동돼지국밥','경상대야식술집'], nearby_stations=[],
          keyword_list=['호탄동돼지국밥','경상대야식술집','경상대돼지국밥']),
     bad_first=('진주호탄동',), must_have=('호탄동','경상대'))

# ── 실행 ─────────────────────────────────────────────────────────────────────
def run():
    all_pass = True
    for label, kwargs, bad_first, must_have, bad_contains, note in CASES:
        ks = G(**kwargs)
        firsts = set(k.split()[0] for k in ks if ' ' in k)
        found_bad = [b for b in bad_first if b in firsts]
        found_sub = [b for b in bad_contains if any(b in k for k in ks)]
        missing = [m for m in must_have if not any(m in k for k in ks)]
        ok = not found_bad and not found_sub and not missing
        all_pass = all_pass and ok
        print(("PASS" if ok else "FAIL") + f" {label} ({len(ks)}개)")
        if found_bad: print(f"   ✗ 쓰레기 잔존(지명): {found_bad}")
        if found_sub: print(f"   ✗ 쓰레기 잔존(합성어): {found_sub}")
        if missing:   print(f"   ✗ 정상 누락: {missing}")
    print("=" * 60)
    print("종합: " + ("✓ 전체 통과" if all_pass else "✗ 일부 실패"))
    return all_pass

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
