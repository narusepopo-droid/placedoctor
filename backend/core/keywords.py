import re

# ── 가짜 지역 접두어 차단 (v8.42) ─────────────────────────────────────────────
_FAKE_LOC_PREFIXES = frozenset([
    "장작", "펠렛", "화목", "벽난", "장판", "싱크", "소파", "붙박", "바닥", "천장", "단열", "타일", "도배",
    "세차", "타이어", "블랙", "튜닝", "광택", "정비", "수리", "배터", "오일",
    "할인", "이벤", "특가", "프리", "무료", "신규", "오픈", "예약", "포장", "배달",
    "테이크", "드라이", "셀프", "무인", "키오", "자동", "원데", "당일", "즉시",
    "족모임", "찐내돈", "느좋존", "유명블", "모노레",
    "청첩장", "청풍케", "밤야경",
    "골프헬", "냉온탕", "건식사", "일회권", "사우나포",
])

def _is_valid_location(loc):
    """추출된 지역 토큰이 실제 지명인지 검증"""
    if any(loc.startswith(fp) for fp in _FAKE_LOC_PREFIXES):
        return False
    # 역 이름은 숫자 허용 (종로3가역, 을지로3가역 등)
    if loc.endswith('역'):
        return True
    if any(c.isdigit() for c in loc):
        return False
    return True

# ── 검색 의도 토큰 사전 (keywordList 분해용) ─────────────────────────────────
_INTENT_TOKENS = [
    # 캠핑/야외
    "오토캠핑장","감성캠핑장","가족캠핑장","커플캠핑장","캠핑장","글램핑장","야영장",
    "오토캠핑","감성캠핑","가족캠핑","커플캠핑","글램핑","야영","카라반","차박여행","차박","계곡캠핑","캠핑",
    # 숙박
    "계곡펜션","풀빌라","독채펜션","가족펜션","커플펜션","계곡숙박",
    "펜션","리조트","숙박","게스트하우스","민박","호텔","콘도",
    # 자연/여행
    "계곡여행","가족여행","커플여행","당일치기","1박2일",
    "계곡","강변","호수","바다","산","여행","관광","나들이","힐링","드라이브","명소","체험",
    # 음식점 — 시간대/목적
    "점심특선","저녁특선","점심맛집","저녁맛집","야식맛집","아침식사","브런치",
    "점심","저녁","아침","특선","런치","포장","테이크아웃","혼밥","혼술단체",
    # 음식점 — 메뉴별
    "돼지국밥맛집","갈비찜맛집","등갈비맛집","고기맛집",
    "돼지국밥","순대국밥","갈비찜","등갈비","돼지등","등뼈찜","해장국","순대국","국밥",
    "쭈꾸미","수육","감자탕","뼈다귀탕","도가니탕","설렁탕","곰탕","부대찌개","김치찌개","된장찌개",
    "삼겹살","돼지갈비","갈비","고기집","맛집","식당","한식당","회식","단체석","혼술","술집","야식",
    "이자카야","호프","포차","막걸리집","안주","안주맛집","소주","맥주","내돈내산",
    "블루리본","미슐랭","상견례","양념돼지갈비","부모님식사","가족모임","저녁식사","점심식사",
    "족발맛집","보쌈맛집","족발","보쌈","곱창맛집","곱창","막창",
    "카페맛집","브런치카페","감성카페","루프탑카페","야경카페","정원카페","좋은카페","대형카페",
    "베이커리카페","디저트카페","카페테라스","대형베이커리카페","대형테라스카페","뷰카페","포토카페","숲속카페",
    "카페","커피","디저트","케이크","베이커리","마카롱","크로플","와플",
    "오마카세","초밥","스시","일식","돈까스","라멘","우동",
    "피자","파스타","스테이크","양식","치킨","닭갈비","닭볶음탕",
    "쌀국수","베트남음식","짬뽕","짜장면","탕수육","중식","마라탕",
    "냉면","막국수","떡볶이","분식","뷔페","무한리필","무한주류",
    # 피트니스/스포츠
    "퍼스널트레이닝","개인PT","헬스클럽","피트니스센터","다이어트PT","바디프로필",
    "재활PT","헬스장","피트니스","필라테스","요가","스포츠센터","PT","크로스핏","수영장",
    "골프","스크린골프","실내골프","골프연습장","골프레슨","그룹PT","그룹필라테스","체형교정",
    # 학원/교육 — 복합 키워드
    "영재학원","사고력학원","사고력수학","사고력교육","창의수학","영재수학","과학영재","수학경시",
    "유아수학","초등수학","중등수학","조기수학","유아교육","초등교육","영재교육","조기교육",
    "영재입시","과학고입시","영재고입시","과학학원","과학교육",
    "수학학원","영어학원","코딩학원","입시학원","피아노학원","미술학원","음악학원","태권도학원",
    "학원","교육센터","공부방","독서실","스터디카페",
    # 학원/교육 — 내신 관련
    "내신관리","내신대비","내신전문","내신수학","내신영어","내신국어","내신준비",
    # 학원/교육 — 루트 토큰 (keywordList 분해용: "사고력유아수학" → "사고력"+"유아"+"수학")
    "사고력","영재","수학","영어","유아","초등","중등","고등","입시","내신","특기","논술","과학",
    # 미용/헤어
    "미용실","헤어샵","헤어살롱","미용원","머리잘하는곳",
    "커트","펌","염색","탈색","두피케어","헤어트리트먼트","매직","셋팅펌",
    "네일샵","젤네일","네일아트","속눈썹연장","왁싱","눈썹문신","반영구",
    # 피부/뷰티
    "피부관리","피부케어","피부미용","에스테틱","에스테틱샵","피부샵","관리샵",
    "윤곽관리","윤곽마사지","여드름관리","여드름케어","여드름","모공관리","피지관리",
    "웨딩관리","웨딩케어","리프팅","탄력관리","미백관리","수분관리",
    # 산전산후 = 서비스어(임신·출산 관리). '산'을 '산(山)' 지명으로 오인식하면 안 됨.
    "산전산후관리","산후관리","산전관리","산후케어","산후비만관리","산후마사지","임산부관리","임산부마사지",
    "마사지","스파","아로마","림프마사지","왁싱관리",
    "피부과","피부과의원","레이저","보톡스","필러","성형외과",
    # 피부과 시술·기기 (써마지/울쎄라 등 오인식 방지 — location으로 잘못 분류되면 안 됨)
    "써마지","써마지FLX","울쎄라","울쎄라리프팅","슈링크","인모드","포텐자","리쥬란",
    "스킨보톡스","주름보톡스","턱보톡스","사각턱보톡스","이마보톡스","눈가보톡스",
    "주름","주름개선","주름치료","잔주름","깊은주름","목주름","이마주름","눈가주름",
    "흉터","흉터레이저","여드름흉터","흉터치료","흉터제거","여드름흉터레이저",
    "색소","색소치료","기미","기미레이저","잡티","잡티레이저","홍조","홍조치료",
    "모공","모공레이저","피부결","탄력","탄력레이저","피부리프팅","리프팅레이저",
    "스킨부스터","물광주사","수분주사","엑소좀","줄기세포",
    # 성형외과 시술 (토큰 분해용 + 직접 검색어)
    "코성형","눈성형","쌍꺼풀","쌍꺼풀수술","지방흡입","안면윤곽","지방이식",
    "눈매교정","양악수술","가슴성형","코수술","눈수술","리프팅수술","실리프팅",
    "성형","성형잘하는곳","성형추천","성형외과추천","성형외과잘하는곳",
    # 의료/건강
    "내과","정형외과","소아과","산부인과","안과","이비인후과","신경과","재활의학과",
    "치과","한의원","한방병원","침","추나","도수치료","물리치료",
    "의원","클리닉","병원","요양병원","건강검진",
    # 반려동물
    "동물병원","동물의원","펫샵","펫카페","애완동물","수의사",
    # 강아지
    "애견미용","강아지미용","강아지호텔","애견호텔","강아지위탁","애견위탁","강아지유치원","애견유치원",
    "강아지훈련","애견훈련","강아지산책","강아지돌봄","강아지케어",
    # 고양이
    "고양이호텔","캣호텔","캣스테이","고양이위탁","고양이펜션","고양이돌봄","고양이케어",
    "고양이미용","고양이병원","반려묘호텔","고양이유치원","24시고양이호텔",
    # 공통 펫
    "펫호텔","펫시터","펫케어","펫위탁","반려동물호텔","펫호텔링",
    # 자동차/모터
    "자동차정비","카센터","타이어교체","오일교환","판금도색","자동차검사",
    "세차장","셀프세차","자동세차","디테일링","광택",
    # 생활서비스
    "꽃집","꽃배달","화원","플라워","웨딩","돌잔치","파티",
    "사진관","증명사진","스튜디오","웨딩스튜디오",
    "세탁","빨래방","코인세탁","수선","신발수선",
    "안경","렌즈","안경점","열쇠","자물쇠","인테리어","도배","창호",
    # 숙박/공간대여
    "파티룸","공간대여","모임공간","회의실","연습실","스튜디오대여",
    # 수식어 (전업종 공통)
    "커플데이트","커플","가족","감성","주말","추천","잘하는곳","가성비","후기","리뷰","예약",
    "24시","새벽","당일","무료주차","주차가능","가까운","근처","주변",
    # v8.41 신규 업종 토큰 (플마 동기화 — 플랭에 통째로 빠져 있던 블록)
    # 난로/벽난로
    "벽난로","화목난로","펠렛난로","연통청소","난로시공","이동식컨테이너","이동식주택","농막",
    "화목","펠렛","장작","굴뚝","연통","온돌","화목보일러","펠렛보일러","벽난로시공",
    # 인테리어
    "인테리어","리모델링","소파","싱크대","바닥재","타일","도배","페인트","조명","가구",
    # 자동차
    "세차","자동세차","손세차","타이어","블랙박스","튜닝","광택","유리막","PPF","카센터",
    # 부동산
    "공인중개사","부동산","매매","전세","월세","원룸","투룸","오피스텔","상가","분양",
    # 법률/세무
    "변호사","법무사","세무사","회계사","노무사","법률상담","세금신고",
    # 숙박
    "펜션","게스트하우스","호텔","리조트","풀빌라","독채펜션","커플펜션","모텔",
    # 웨딩
    "웨딩홀","스드메","웨딩스튜디오","웨딩촬영","예식장","웨딩플래너",
    # 산후조리
    "산후조리원","조리원","산모","산후","신생아","모유수유","산후케어",
]

# 대학교 약칭 화이트리스트 (플마 v8.42 — "경상대 맛집" 등에서 실제 대학만 지명 인정)
_UNIV_NAMES = frozenset([
    "경상대", "부산대", "서울대", "연세대", "고려대", "한양대", "성균관대", "중앙대", "경희대", "이화여대",
    "서강대", "건국대", "동국대", "홍익대", "국민대", "숭실대", "세종대", "단국대", "인하대", "아주대",
    "경북대", "전남대", "전북대", "충남대", "충북대", "강원대", "제주대", "울산대", "창원대", "경남대",
    "동아대", "영남대", "계명대", "대구대", "조선대", "원광대", "목포대", "순천대", "안동대", "금오공대",
    "한밭대", "공주대", "청주대", "한남대", "배재대", "우송대", "호서대", "선문대", "상명대", "명지대",
    "가천대", "한신대", "수원대", "용인대", "평택대", "협성대", "한경대", "서울시립대", "한국외대", "한국항공대",
])

# 루트 토큰 → 연관 복합 키워드 자동 확장 (keywordList에 없는 조합 생성)
_TOKEN_EXPANSIONS = {
    "과학영재": ["영재학원", "과학학원", "영재교육", "과학교육"],
    "영재":     ["영재학원", "영재교육", "영재수학"],
    "사고력":   ["사고력학원", "사고력수학", "사고력교육"],
    "과학":     ["과학학원", "과학교육"],
    "수학":     ["수학학원"],
    "영어":     ["영어학원"],
    "초등":     ["초등수학학원", "초등학원"],
    "중등":     ["중등수학학원", "중등학원"],
    "유아":     ["유아학원", "유아교육"],
    "입시":     ["입시학원", "입시전문"],
    "내신":     ["내신관리", "내신대비", "내신전문"],
    "논술":     ["논술학원"],
    "헬스":     ["헬스장", "헬스클럽", "피트니스"],
    "PT":       ["개인PT", "퍼스널트레이닝"],
    "필라테스": ["필라테스학원", "필라테스센터"],
    "요가":     ["요가학원", "요가센터"],
    "헤어":     ["미용실", "헤어샵"],
    "피부":     ["피부과", "피부관리", "피부케어"],
    "피부관리": ["피부케어", "피부미용", "에스테틱"],
    "에스테틱": ["에스테틱샵", "피부관리", "피부케어"],
    "여드름":   ["여드름관리", "여드름케어"],
    "윤곽":     ["윤곽관리", "윤곽마사지"],
    "리프팅":   ["리프팅관리", "탄력관리"],
    "웨딩":     ["웨딩관리", "웨딩케어"],
    "고양이":   ["고양이호텔", "캣호텔", "고양이위탁", "고양이케어", "고양이미용"],
    "캣":       ["캣호텔", "캣스테이", "고양이호텔", "고양이위탁"],
    "강아지":   ["강아지호텔", "애견호텔", "강아지위탁", "강아지미용", "강아지돌봄"],
    "애견":     ["애견호텔", "강아지호텔", "애견미용", "애견위탁"],
    "펫":       ["펫호텔", "펫시터", "펫케어"],
    "반려묘":   ["고양이호텔", "캣호텔", "고양이위탁"],
    "반려견":   ["강아지호텔", "애견호텔", "강아지위탁"],
    "계곡":     ["계곡여행", "계곡캠핑", "계곡펜션"],
    "커플":     ["커플캠핑", "커플여행", "커플펜션"],
    "감성":     ["감성캠핑", "감성캠핑장"],
    "카라반":   ["카라반캠핑", "차박"],
    "카페":     ["정원카페", "야경카페", "좋은카페", "대형카페", "베이커리카페", "디저트카페", "카페테라스", "대형베이커리카페"],
    "커피":     ["카페", "브런치카페", "감성카페"],
    "디저트":   ["디저트카페", "베이커리카페"],
    "베이커리": ["베이커리카페", "대형베이커리카페"],
    # 피부과 시술 기기 확장
    "써마지":   ["써마지피부과", "써마지FLX", "피부리프팅"],
    "울쎄라":   ["울쎄라리프팅", "울쎄라피부과"],
    "슈링크":   ["슈링크유니버스", "피부리프팅"],
    "주름":     ["주름보톡스", "주름필러", "주름개선", "주름레이저"],
    "흉터":     ["흉터레이저", "여드름흉터", "흉터치료"],
    "기미":     ["기미레이저", "색소레이저"],
    "모공":     ["모공레이저", "모공치료"],
    # 성형외과
    "성형외과": ["코성형", "눈성형", "쌍꺼풀", "지방흡입", "안면윤곽", "성형외과추천", "성형외과잘하는곳"],
    "성형":     ["성형외과", "코성형", "눈성형", "안면윤곽"],
    "코성형":   ["코수술", "코성형잘하는곳"],
    "눈성형":   ["쌍꺼풀", "눈매교정", "눈성형잘하는곳"],
    "피부과":   ["레이저", "보톡스", "필러", "피부관리"],
}


# 동음이의 접두 오매칭 함정어 (짧은 의도 토큰이 다른 뜻의 긴 단어 속에 박혀 오매칭)
#   '고등'(고등학교/교육) ⊂ '고등어'(생선) — 생선집에 '덕양구 고등' 같은 헛키워드 방지
_TRAP_WORDS = {"고등": ("고등어",)}


def _find_tokens_in_kw(kw, locations):
    """keywordList 항목에서 지역 제거 후 의도 토큰 추출 (포함 검색)"""
    remaining = kw.strip()
    for loc in sorted(locations, key=len, reverse=True):
        if loc and len(loc) >= 2 and remaining.startswith(loc):
            remaining = remaining[len(loc):]
            break
    if len(remaining) < 2:
        return []
    remaining_lower = remaining.lower()
    found = []

    # 1) 사전 매칭 (기존 로직 유지 - 회귀 방지)
    dict_found = [t for t in _INTENT_TOKENS if len(t) >= 2 and t.lower() in remaining_lower]
    dict_found = [t for t in dict_found if not any(t != o and o.endswith(t) for o in dict_found)]

    # v8.44: 동음이의 함정어 제거 — 토큰이 함정어(고등어) 안에만 있고 독립 등장 안 하면 버림
    def _trapped(t):
        traps = _TRAP_WORDS.get(t)
        if not traps:
            return False
        stripped, hit = remaining_lower, False
        for w in traps:
            if w in stripped:
                stripped = stripped.replace(w, '')
                hit = True
        return hit and t not in stripped
    dict_found = [t for t in dict_found if not _trapped(t)]
    found.extend(dict_found)

    # 2) 지역 제거 후 남은 전체 문자열도 토큰으로 추가 (대형베이커리카페 같은 복합어 지원)
    # 단, 8글자 이상 긴 복합어는 제외 (과학영재고입시사고력유아수학 같은 SEO 합성어)
    # v8.43: 지역 suffix로 끝나면 제외 (변동금호강 같은 지역 합성어)
    _loc_suffixes = ('산', '강', '천', '동', '역', '구', '읍', '면', '리', '계곡', '공원', '호수')
    if (re.match(r'^[가-힣]+$', remaining) and remaining not in found
        and 3 <= len(remaining) <= 7
        and not any(remaining.endswith(s) for s in _loc_suffixes)):
        found.append(remaining)

    return list(dict.fromkeys(found))


def generate_keywords(store_name, category, address, menu_items, official_keywords,
                      nearby_station="", keyword_list=None, log_func=None, nearby_stations=None):
    """매장 정보로 네이버 플레이스 검색 키워드 목록 자동 생성 (최대 100개)."""
    locations = []
    clean_name = store_name.strip()

    # [v2.9.2의 완벽했던 지점명 파싱 로직 토씨 하나 안 틀리고 복사]
    for suffix in ["본점", "직영점", "지점", "점"]:
        if clean_name.endswith(suffix):
            loc_match = re.search(r'([가-힣a-zA-Z0-9]+)' + suffix + r'$', clean_name)
            if loc_match:
                loc = loc_match.group(1).split()[-1]
                if len(loc) > 5:   # 플마 v8.43: '제천청풍호반' 같은 긴 지점명은 지명 아님 → 무시
                    break
                locations.append(loc)
                # v8.44: 지점명 loc에서 역/동을 합성할 땐 '실제 지명 base'일 때만.
                #        '계양구청점' → loc '계양구청'에서 '계양구청역/계양구청동'(가짜)을
                #        만들던 문제 차단. 시설·행정 접미로 끝나거나 5자↑면 합성 안 함.
                _loc_synth_ok = (2 <= len(loc) <= 4
                                 and not loc.endswith(('청', '역', '구', '동', '점', '원', '장', '교')))
                if _loc_synth_ok:
                    locations.append(f"{loc}역")
                    if address and not address.startswith("서울") and not address.startswith("경기"):
                        locations.append(f"{loc}동")
                if "호수" in loc: locations.append(loc.replace("호수", ""))
                clean_name = clean_name.replace(loc_match.group(0), "").strip()
            break

    # 검색량 많은 구 → 지역명 화이트리스트 (강남 맛집, 종로 카페 등 검색 패턴)
    # 해당 구에 위치하면 무조건 위치 키워드에 추가
    _GU_TO_LOCATIONS = {
        "강남구": ["강남"],
        "서초구": ["서초"],
        "송파구": ["송파"],
        "마포구": ["마포"],
        "용산구": ["용산"],
        "종로구": ["종로"],
        "영등포구": ["영등포"],
        "동대문구": ["동대문"],
        "성동구": ["성수"],           # 성수동이 더 유명
        "중구": ["명동", "을지로"],   # 중구 자체보단 동 단위
        "노원구": ["노원"],
    }

    SKIP_CITIES = {"서울", "경기"}
    addr_tokens = address.replace(",", " ").split()
    for token in addr_tokens:
        if token.endswith("구") and len(token) > 1:
            locations.append(token)  # 강남구 추가
            # 화이트리스트 구만 지역명 추가 (강남구 → 강남, 중구 → 명동/을지로)
            # v8.44: 명동·을지로·성수 등은 서울 전용 지명. '중구'는 대전·부산·대구에도
            #        있어 대전 중구에 '명동'을 붙이는 오류가 있었음 → 서울 주소일 때만 적용.
            if token in _GU_TO_LOCATIONS and address.startswith("서울"):
                locations.extend(_GU_TO_LOCATIONS[token])
        elif token.endswith("군") and len(token) > 1:
            locations.append(token[:-1])
        elif token.endswith("시") and len(token) > 1:
            si = token[:-1]
            if si not in SKIP_CITIES:
                locations.append(si)
        elif token.endswith("읍") and len(token) > 1:
            locations.append(token[:-1])
        elif token.endswith("면") and len(token) > 1:
            locations.append(token[:-1])               # 청풍면 → 청풍 (플마 동기화)
        elif token.endswith("동") and len(token) > 1 and token not in ["공동", "이동", "감동", "행동"]:
            dong_name = token.replace("동", "")
            locations.append(token)  # 여의도동 추가
            # 동에서 순수 지역명 추출 (여의도동 → 여의도)
            if len(dong_name) >= 2 and not dong_name[-1].isdigit():
                locations.append(dong_name)
            # 숫자 동은 base만 (삼성1동 → 삼성동)
            base_dong = re.sub(r'\d+$', '', dong_name)
            if base_dong != dong_name and len(base_dong) >= 2:
                locations.append(f"{base_dong}동")
    KNOWN_CITIES = {"인천", "부산", "대구", "대전", "광주", "울산", "세종",
                    "수원", "성남", "안양", "부천", "고양", "용인"}
    if addr_tokens and addr_tokens[0] in KNOWN_CITIES:
        locations.append(addr_tokens[0])
    _PROV_MAP = {"강원": "강원도", "경남": "경상남도", "경북": "경상북도",
                 "전남": "전라남도", "전북": "전라북도", "충남": "충청남도",
                 "충북": "충청북도", "제주": "제주도"}
    _cat_lower = (category + " " + store_name).lower()
    _is_regional_biz = any(c in _cat_lower for c in
        ['캠핑', '야영', '글램핑', '차박', '펜션', '리조트', '숙박', '게스트하우스',
         '민박', '여행', '관광', '레저', '휴양', '낚시', '자연', '농원', '농장'])
    if _is_regional_biz and addr_tokens and addr_tokens[0] in _PROV_MAP:
        prov_short, prov_long = addr_tokens[0], _PROV_MAP[addr_tokens[0]]
        if prov_short not in locations: locations.append(prov_short)
        if prov_long not in locations: locations.append(prov_long)
    # 역 여러 개 처리 (nearby_stations 우선, nearby_station은 하위호환)
    _all_stations = nearby_stations if nearby_stations else ([nearby_station] if nearby_station else [])
    for station in _all_stations:
        if not station or station in locations or len(station) < 3:
            continue
        if len(station) <= 6:
            locations.append(station)
        else:
            # 노선명 먼저 제거 (신분당선신논현역 → 신논현역)
            _station_clean = station
            # 노선명(정식 + naver 약칭). 약칭은 '실제 역명'이 아닌 것만 (의정부·용인 등
            # 실제 역이 있는 도시명은 넣지 않음). '김포골드'=김포골드라인 약칭(마산역 등).
            for _line in ["신분당선", "수인분당선", "경의중앙선", "경춘선", "경강선",
                          "서해선", "신림선", "우이신설선", "김포골드라인", "김포골드",
                          "용인경전철", "의정부경전철", "인천1호선", "인천2호선",
                          "에버라인", "김포도시철도", "우이신설"]:
                _station_clean = _station_clean.replace(_line, "")
            _station_clean = re.sub(r'\d호선|공항철도', '', _station_clean).strip()

            # v8.44: 노선·호선 제거 후 남은 '…역' 전체를 그대로 사용한다.
            #        예전엔 마지막 2~3글자만 잘라(([가-힣]{2,3})역$) '구로디지털단지역'을
            #        '털단지역', '용인동백역'을 '인동백역'으로 조작하는 버그가 있었음.
            #        긴 역명도 실제 역이므로 통째로 유지.
            if _station_clean.endswith("역") and 3 <= len(_station_clean) <= 9:
                locations.append(_station_clean)

    locations = list(dict.fromkeys([l for l in locations if l and len(l) >= 2]))
    for skip in ["서울", "경기"]:
        if skip in locations: locations.remove(skip)
    if not locations: locations = [""]

    if log_func: log_func(f"    ㄴ 📌 위치 토큰: {', '.join(locations)}")

    _BAD_PATTERNS = re.compile(
        r'영업중|영업종료|영업시간|\d{3,}|에영업|시에|분에|\d+시\d*분|휴무|정기휴무|임시휴무|'
        r'특가|이벤트|한정|첫방문|할인쿠폰|프로모션|레귤러|수퍼스페셜|디럭스|스탠다드|'
        r'스위트룸|싱글룸|더블룸|트윈룸|\d+만원(?!대)|지인소개|기간증정|\d+회(?:추가|증정)|'
        r'뭉칠수록|혜택최대|\d+회헬스|\d+전문|냉온탕|건식사우나|일회권|사우나포함'
    )
    _bone_kw_pat = re.compile(r'뼈국밥|뼈해장국|뼈다귀|돼지뼈')
    clean_official = [tag for tag in official_keywords
                      if not _BAD_PATTERNS.search(tag) and not _bone_kw_pat.search(tag)
                      and len(tag) <= 15
                      and not (len(tag) >= 8 and ' ' not in tag)]
    # kw_list_raw: 위치·의도 토큰 추출용 (길이 필터 미적용)
    # kw_list: 직접 검색어 시드용 (10자 이상 붙임말 제외 — 검색 노이즈 방지)
    kw_list_raw = [k.strip() for k in (keyword_list or [])
                   if k and len(k.strip()) >= 2 and not _BAD_PATTERNS.search(k)]
    kw_list = [k for k in kw_list_raw
               if not (len(k) >= 10 and ' ' not in k)]

    # ── 지명 검증 기준 (v8.44 — 상시 통용 룰) ────────────────────────────────
    #  authoritative 지명 = 주소·역·지점명에서 뽑은 것 (이 시점까지의 locations).
    #  keywordList 텍스트에서 파생된 지명은 반드시 아래 _valid_derived_loc로 검증한다:
    #  실제 주소에 그 지명(또는 그 base)이 있거나, naver가 준 실제 인근역과 일치해야만
    #  인정. → '화덕생선구'(생선구이), '규동', '드마산역' 같은 가짜 지명을 근본 차단.
    _addr_nospace = re.sub(r'\s+', '', address or '')
    _auth_locations = set(locations)
    _station_names = set(l for l in locations if l.endswith('역'))
    _station_bases = set(s[:-1] for s in _station_names)

    def _valid_derived_loc(cand):
        # 대학 화이트리스트(경상대 등)는 실제 지명으로 인정
        if cand in _UNIV_NAMES:
            return True
        # 자연 랜드마크(산/강/천/호/계곡/공원/호수)는 지역형 업종에서만 추출되며 그대로 인정
        if _is_regional_biz and (cand[-1] in ('산', '강', '천', '호')
                                 or cand[-2:] in ('계곡', '공원', '호수')):
            return True
        # 역: 실제 인근역(주소 base 또는 naver 역)과 일치해야 — 잘린/가짜 역 제거
        if cand.endswith('역'):
            base = cand[:-1]
            if cand in _station_names or base in _station_bases:
                return True
            return base in _addr_nospace
        # 행정 접미(지구·구·군·시·읍·면·동·리): base가 주소나 인근역에 있어야 실제 행정지명
        for sfx in ('지구', '구', '군', '시', '읍', '면', '동', '리'):
            if cand.endswith(sfx) and len(cand) > len(sfx):
                base = cand[:-len(sfx)]
                if not base:
                    return False
                if base in _addr_nospace:
                    return True
                # 인근 역명 base와 일치하면 실제 동네로 인정 (철산동 ← 철산역).
                # 단, 역이 시설(구청·시청·대학·터미널 등)이면 '…동'은 실제 행정동이 아님 → 제외
                #   (계양구청역 → '계양구청동'은 가짜).
                return (base in _station_bases
                        and not base.endswith(('청', '교', '원', '장', '터미널', '차고지')))
        # 접미 없는 순수 지명: 주소나 역명에 등장해야 인정 (양재 ← 양재역)
        return cand in _addr_nospace or any(cand in s for s in _station_names)

    # ── keywordList에서 추가 지역 토큰 추출 (플마 v8.40 방식으로 복원) ──────────
    #  플마는 v8.40에서 '방식 2~5'(키워드 중간을 마구 쪼개 지명 만드는 로직)를 삭제했다.
    #  플랭은 그 삭제 전 코드를 물려받아 화덕생선구·규동·계양구청동 같은 가짜 지명을
    #  양산했다. 여기서 플마와 동일하게 '앞부분 suffix + 대학 화이트리스트 + 랜드마크'
    #  만 쓴다. 파생 지명은 뒤의 _valid_derived_loc(주소·역 대조)로 한 번 더 거른다.
    for kw in kw_list_raw:
        # 대학 약칭(경상대·부산대 등) — 실제 대학 화이트리스트에 있을 때만 지명 인정
        m_univ = re.match(r'^([가-힣]{2,5}대)', kw)
        if m_univ and m_univ.group(1) in _UNIV_NAMES:
            if m_univ.group(1) not in locations:
                locations.append(m_univ.group(1))
            continue

        # 방식 1: 앞부분이 역/동/구/대학/산/강/천/계곡/공원 suffix로 끝나면 지명 (구일역, 고척동)
        m = re.match(r'^[가-힣]{2,4}(?:역|동|구|대학|산|강|천|계곡|공원)', kw)
        if m:
            extra = m.group()
            # 이미 등록된 행정구(xxx구)에 '동'이 붙은 파생형이면 스킵 (북구+동=북구동 방지)
            is_derived_dong = (extra.endswith("동") and
                               any(extra == loc + "동" for loc in locations if loc.endswith("구")))
            if extra not in locations and not is_derived_dong:
                locations.append(extra)

        # 자연 랜드마크(산/강/천/계곡/공원/호수): 캠핑·펜션·여행 등 '지역형 업종'에서만.
        #   도심 서비스업은 '양재산'(산전산후)·'돈내산'(내돈내산) 같은 서비스어 오분리
        #   방지 위해 추출 안 함 (v8.44 — 플마엔 없는 플랭 개선점, 유지).
        if _is_regional_biz:
            for landmark in re.findall(r'[가-힣]{2,4}(?:산|강|천|계곡|공원|호수)', kw):
                if landmark not in locations and landmark not in _INTENT_TOKENS:
                    locations.append(landmark)

    # v8.44: authoritative(주소·역·지점명) 지명은 그대로, keywordList에서 파생된 지명은
    #        주소/인근역 앵커 검증(_valid_derived_loc)을 통과한 것만 남긴다.
    locations = list(dict.fromkeys([
        l for l in locations
        if l and len(l) >= 2 and _is_valid_location(l)
        and (l in _auth_locations or _valid_derived_loc(l))
    ]))
    for skip in ["서울", "경기"]:
        if skip in locations: locations.remove(skip)
    if not locations: locations = [""]

    # ── 1순위: keywordList 그대로 ─────────────────────────────────────────────
    def _has_location(kw):
        return any(loc and loc in kw for loc in locations if loc)
    def _multi_loc(kw):
        return sum(1 for loc in locations if loc and len(loc) >= 2 and loc in kw) >= 2
    def _has_intent(kw):
        kw_lower = kw.lower()
        return any(t.lower() in kw_lower for t in _INTENT_TOKENS if len(t) >= 2)

    kws = list(dict.fromkeys(
        k for k in kw_list
        if (len(k) > 4 or _has_location(k))
        and not _multi_loc(k)
        and (_has_location(k) or _has_intent(k))
    ))

    all_kw_tokens = []
    for kw in kw_list_raw:
        all_kw_tokens.extend(_find_tokens_in_kw(kw, locations))
    all_kw_tokens = list(dict.fromkeys(t for t in all_kw_tokens if len(t) >= 2))

    _seen_tokens = set(all_kw_tokens)
    for t in list(all_kw_tokens):
        for expanded in _TOKEN_EXPANSIONS.get(t, []):
            if expanded not in _seen_tokens:
                all_kw_tokens.append(expanded)
                _seen_tokens.add(expanded)
        for root in _TOKEN_EXPANSIONS:
            if root in t and root != t:
                for expanded in _TOKEN_EXPANSIONS[root]:
                    if expanded not in _seen_tokens:
                        all_kw_tokens.append(expanded)
                        _seen_tokens.add(expanded)

    kws_set = set(kws)
    for loc in locations:
        for token in all_kw_tokens:
            combined = f"{loc} {token}" if loc and loc not in token else token
            if combined not in kws_set:
                kws.append(combined)
                kws_set.add(combined)

    # ── 2순위: 지역 × official_keywords 조합 ─────────────────────────────────
    _kw_filter_set = [t for t in all_kw_tokens if len(t) >= 3]
    for tag in clean_official:
        clean_tag = re.sub(r'[^가-힣a-zA-Z0-9]', '', tag).strip()
        if len(clean_tag) < 2:
            continue
        if kw_list and _kw_filter_set:
            if not any(t in clean_tag or clean_tag in t for t in _kw_filter_set):
                continue
        for loc in locations:
            if loc and loc in clean_tag:
                kws.append(clean_tag)
            else:
                kws.append(f"{loc} {clean_tag}".strip())

    _MENU_GRADE_SKIP = {"스페셜", "럭셔리", "프리미엄", "베이직", "스탠다드", "기본형", "일반형", "고급형"}
    for menu in menu_items:
        clean_m = re.sub(r'[^가-힣a-zA-Z0-9]', '', menu)
        if (2 <= len(clean_m) <= 12
                and not _BAD_PATTERNS.search(clean_m)
                and clean_m not in _MENU_GRADE_SKIP
                and _has_intent(clean_m)):
            for loc in locations:
                kws.append(f"{loc} {clean_m}".strip() if loc and loc not in clean_m else clean_m)

    # ── 3순위: keywordList·official_keywords 둘 다 없을 때만 카테고리 폴백 ────
    if not kw_list and not clean_official:
        cat_str = (category + " " + store_name).lower()
        fallback = []
        if any(x in cat_str for x in ['헬스', 'pt', '피트니스', '휘트니스']):
            fallback = ["헬스장", "PT", "개인PT", "피트니스"]
        elif any(x in cat_str for x in ['학원', '교육', '영재', '사고력']):
            fallback = ["학원", "영재학원", "사고력수학", "교육센터"]
        elif any(x in cat_str for x in ['캠핑', '야영', '글램핑']):
            fallback = ["캠핑장", "글램핑", "오토캠핑"]
        elif any(x in cat_str for x in ['펜션', '풀빌라', '숙박', '호텔']):
            fallback = ["펜션", "풀빌라", "숙소"]
        elif any(x in cat_str for x in ['병원', '치과', '한의원', '클리닉']):
            fallback = ["병원", "치과", "한의원"]
        elif any(x in cat_str for x in ['미용', '헤어']):
            fallback = ["미용실", "헤어샵", "머리잘하는곳"]
        elif any(x in cat_str for x in ['카페', '커피', '디저트', '베이커리']):
            fallback = ["카페", "커피", "디저트"]
        elif any(x in cat_str for x in ['고기', '갈비', '국밥', '식당', '음식점']):
            fallback = ["맛집", "고기집", "맛있는집"]
        elif any(x in cat_str for x in ['배터리', '밧데리', '자동차', '정비', '카센터', '타이어']):
            fallback = ["자동차배터리", "배터리교체", "출장배터리", "긴급출동배터리"]
        elif any(x in cat_str for x in ['청소', '입주청소', '이사청소']):
            fallback = ["입주청소", "이사청소", "청소업체"]
        elif any(x in cat_str for x in ['철거', '폐기물']):
            fallback = ["철거업체", "철거공사", "폐기물처리"]
        elif any(x in cat_str for x in ['도배', '인테리어', '장판']):
            fallback = ["도배", "인테리어", "도배업체"]
        else:
            fallback = [w.strip() for w in category.split(',') if len(w.strip()) >= 2][:3]
            # 카테고리도 비면 매장명 기반 최소 키워드 (지역 × 매장명) — '키워드 0개' 방지
            if not fallback:
                _nm = re.sub(r'[^가-힣a-zA-Z0-9]', '', store_name)[:6]
                if len(_nm) >= 2:
                    fallback = [_nm]
            fallback.append("추천")
        for loc in locations:
            for intent in fallback:
                kws.append(f"{loc} {intent}".strip() if loc and loc not in intent else intent)

    # ── 음식점 계열 → 지역 × "맛집"/"맛집추천" 디폴트 추가 ──────────────────
    _FOOD_CAT_SIGNALS = [
        '음식점', '한식', '일식', '중식', '양식', '분식', '카페', '커피',
        '베이커리', '제과', '디저트', '아이스크림', '술집', '주점',
        '이자카야', '호프', '치킨', '피자', '햄버거', '패스트푸드', '뷔페',
        '해산물', '횟집', '수산', '고기', '갈비', '국밥', '칼국수',
        '순대', '찌개', '정식', '한정식', '삼계탕', '보쌈', '족발',
        '생선', '두부', '비빔밥', '떡볶이', '김밥', '초밥', '스시',
        '라멘', '우동', '스테이크', '파스타', '샌드위치', '쌀국수',
        '카레', '타코', '케밥', '편의점', '정육점', '식당',
    ]
    _food_check = (category + " " + store_name).lower()
    if any(sig in _food_check for sig in _FOOD_CAT_SIGNALS):
        for loc in locations:
            for food_kw in ["맛집", "맛집추천"]:
                combined = f"{loc} {food_kw}".strip() if loc else food_kw
                kws.append(combined)

    # ── 중복 제거 + 정렬 (동탄 > 동탄역, 지역명 우선) ───────────────────────────────
    _kl_text = ''.join(kw_list_raw)

    def sort_weight(kw):
        if kw in set(kw_list): return 1000
        w = 0

        # "역" 포함 키워드는 무조건 후순위
        if "역" in kw:
            w += 10
        # "동" 또는 "구" 포함 (동탄구, 영천동 등)
        elif "동" in kw or "구" in kw:
            w += 40
        # 그 외 지역 (동탄 등)
        else:
            for loc in locations:
                if loc and loc in kw and not loc.endswith('역'):
                    w += 30
                    break

        # keywordList에 있는 지역과 매칭되면 보너스
        for loc in locations:
            if loc and len(loc) >= 3 and loc in kw and loc in _kl_text:
                w += 15
                break
        return w

    # v8.42: 쓰레기 키워드 필터
    _intent_set = set(_INTENT_TOKENS)
    def _is_garbage_kw(kw):
        parts = kw.split()
        last_token = parts[-1] if parts else kw
        if _BAD_PATTERNS.search(last_token): return True
        # 8자 이상 토큰인데 알려진 의도 토큰이 아니면 쓰레기
        if len(last_token) >= 8 and last_token not in _intent_set: return True
        return False

    seen = set()
    deduped = []
    for k in kws:
        if k not in seen and not _is_garbage_kw(k):
            seen.add(k)
            deduped.append(k)

    deduped.sort(key=sort_weight, reverse=True)

    # 띄어쓰기 없는 키워드 제거 (단, keywordList 원본은 유지)
    _kw_set = set(kw_list) if kw_list else set()
    deduped = [k for k in deduped if ' ' in k or k in _kw_set]

    return deduped[:100]
