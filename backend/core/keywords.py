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
]

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
                locations.extend([loc, f"{loc}역"])
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
            if token in _GU_TO_LOCATIONS:
                locations.extend(_GU_TO_LOCATIONS[token])
        elif token.endswith("군") and len(token) > 1:
            locations.append(token[:-1])
        elif token.endswith("시") and len(token) > 1:
            si = token[:-1]
            if si not in SKIP_CITIES:
                locations.append(si)
        elif token.endswith("읍") and len(token) > 1:
            locations.append(token[:-1])
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
            for _line in ["신분당선", "수인분당선", "경의중앙선", "경춘선", "경강선",
                          "서해선", "신림선", "우이신설선", "김포골드라인", "용인경전철", "의정부경전철"]:
                _station_clean = _station_clean.replace(_line, "")
            _station_clean = re.sub(r'\d호선|공항철도', '', _station_clean)

            m_st = re.search(r'([가-힣]{2,3})역$', _station_clean)
            if not m_st:
                m_st = re.search(r'([가-힣]{4})역$', _station_clean)
            if m_st:
                locations.append(m_st.group(1) + "역")

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

    # ── keywordList에서 추가 지역 토큰 추출 ──
    for kw in kw_list_raw:
        # v8.43: "대"로 끝나는 건 2글자만 허용 (상대, 홍대 등), 3글자 이상은 오인식 위험
        m = re.match(r'^[가-힣]{2,3}(?:역|동|구)|^[가-힣]{2}대', kw)
        added_by_method1 = False
        if m:
            extra = m.group()
            is_derived_dong = (extra.endswith("동") and
                               any(extra == loc + "동" for loc in locations if loc.endswith("구")))
            # v8.43: 기존 지역을 포함하면 제외 (동변동대 ← 동변동 포함)
            contains_existing = any(loc in extra and loc != extra for loc in locations if len(loc) >= 2)
            if extra not in locations and not is_derived_dong and not contains_existing:
                locations.append(extra)
                added_by_method1 = True

        # v8.42: 랜드마크 전체 검색 (산/강/천/계곡/공원/호수)
        # keywordList 어디에 있든 추출 — 금호강, 용오름계곡, 팔공산 등
        # v8.43: 짧은 것 우선 추출 (동금호강보다 금호강 우선)
        landmarks_found = []
        for landmark in re.findall(r'[가-힣]{2}(?:산|강|천)|[가-힣]{2}(?:계곡|공원|호수)', kw):
            landmarks_found.append(landmark)
        for landmark in re.findall(r'[가-힣]{3}(?:산|강|천)|[가-힣]{3,4}(?:계곡|공원|호수)', kw):
            if not any(lm in landmark for lm in landmarks_found):
                landmarks_found.append(landmark)
        for landmark in landmarks_found:
            if landmark not in locations and landmark not in _INTENT_TOKENS:
                locations.append(landmark)

        if not added_by_method1:
            for plen in (3, 2):
                if len(kw) > plen + 2:
                    prefix = kw[:plen]
                    rest = kw[plen:]
                    is_derived2 = (prefix.endswith("동") and
                                   any(prefix == loc + "동" for loc in locations if loc.endswith("구")))
                    # v8.43: 기존 지역을 prefix로 포함하면 확장형이므로 제외 (동변동대 ← 동변동 포함)
                    is_extension2 = any(loc in prefix and loc != prefix
                                        for loc in locations if len(loc) >= 2)
                    _is_intent_prefix = (prefix in _INTENT_TOKENS or
                                         any(t.startswith(prefix) and len(t) > len(prefix)
                                             for t in _INTENT_TOKENS) or
                                         any(prefix.startswith(t) and len(t) >= 2 and t != prefix
                                             for t in _INTENT_TOKENS))
                    if (re.match(r'^[가-힣]+$', prefix) and not is_derived2 and not is_extension2
                            and not _is_intent_prefix
                            and any(t in rest for t in _INTENT_TOKENS if len(t) >= 3)):
                        if prefix not in locations:
                            locations.append(prefix)
                        if not prefix.endswith(('역', '동', '구', '시', '군', '읍', '면', '리', '대', '산', '강')):
                            st_cand = prefix + "역"
                            if st_cand not in locations:
                                locations.append(st_cand)
                        break

        cands3_all = []
        for loc_m in re.finditer(r'(?=([가-힣]{2,3}(?:동|역)))', kw):
            extra3 = loc_m.group(1)
            is_derived3 = (extra3.endswith("동") and
                           any(extra3 == loc + "동" for loc in locations if loc.endswith("구")))
            if not is_derived3:
                cands3_all.append(extra3)
        cands3_ok = [c for c in cands3_all if not any(c != o and o in c for o in cands3_all)]
        for extra3 in cands3_ok:
            if extra3 not in locations:
                locations.append(extra3)

        remaining4 = kw
        while remaining4:
            loc_matched = False
            for loc in sorted(locations, key=len, reverse=True):
                if loc and remaining4.startswith(loc):
                    remaining4 = remaining4[len(loc):]
                    loc_matched = True
                    break
            if not loc_matched:
                break
        for t in sorted(_INTENT_TOKENS, key=len, reverse=True):
            if len(t) >= 3 and t in remaining4:
                remaining4 = remaining4.replace(t, '', 1)
                break
        _LOC_SFXS = {'역', '동', '구', '산', '강', '천', '호', '읍', '면', '리'}
        _LOC_SFXS2 = {'공원', '호수', '댐', '계곡'}
        for chunk in re.findall(r'[가-힣]{2,5}', remaining4):
            loc_suffix_ok = (chunk[-1] in _LOC_SFXS or chunk[-2:] in _LOC_SFXS2)
            # v8.43: 기존 지역(특히 랜드마크)을 포함하면 제외 (변동금호강 ← 금호강 포함)
            is_superset = any(loc in chunk and loc != chunk for loc in locations if len(loc) >= 2)
            if (chunk not in locations and chunk not in _INTENT_TOKENS
                    and not any(chunk == loc + "동" for loc in locations if loc.endswith("구"))
                    and loc_suffix_ok and not is_superset):
                locations.append(chunk)

        # 방식 5: keywordList 앞 3~4글자가 지명 suffix로 끝나면 추출 (화담공원, 팔공산 등)
        # v8.43: 5글자 제외 (변동금호강 같은 합성어 오인식 방지), 기존 랜드마크 포함 시 제외
        for plen in [4, 3]:  # 4글자 우선 (화담공원), 3글자
            if len(kw) >= plen + 2:
                prefix = kw[:plen]
                if re.match(r'^[가-힣]+$', prefix) and prefix not in locations:
                    has_loc_suffix = (prefix[-1] in _LOC_SFXS or prefix[-2:] in _LOC_SFXS2)
                    is_intent = prefix in _INTENT_TOKENS
                    # 기존 랜드마크를 포함하면 추가하지 않음 (변동금호강 → 금호강 이미 있음)
                    contains_existing = any(loc in prefix and loc != prefix for loc in locations if len(loc) >= 2)
                    if has_loc_suffix and not is_intent and not contains_existing:
                        locations.append(prefix)
                        break

    locations = list(dict.fromkeys([l for l in locations if l and len(l) >= 2 and _is_valid_location(l)]))
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
        else:
            fallback = [w.strip() for w in category.split(',') if len(w.strip()) >= 2][:3]
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
