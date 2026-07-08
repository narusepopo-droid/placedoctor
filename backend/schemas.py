from typing import Any, List, Optional
from pydantic import BaseModel


class DiagnoseRequest(BaseModel):
    store_name: str
    place_url: str
    force_refresh: bool = False
    # 키워드광고 체크박스 (자동 감지 제거 → 업주 입력)
    ad_place: bool = False       # 플레이스 광고
    ad_powerlink: bool = False   # 파워링크
    ad_local: bool = False       # 지역소상공인광고
    ad_blog: bool = False        # 블로그 체험단
    # K단계: 익명 식별자
    anon_id: Optional[str] = None
    # 유입 키워드: 사용자가 입력한 검색어 (관리자 분석용)
    search_query: Optional[str] = None


class DiagnoseResponse(BaseModel):
    cached: bool = False
    store_name: str
    place_id: Optional[str] = None
    address: Optional[str] = None
    category: Optional[str] = None
    visitor_reviews: Optional[int] = None
    blog_reviews: Optional[int] = None
    star_score: Optional[float] = None
    photo_count: Optional[int] = None
    latest_review_date: Optional[str] = None
    review_activity: Optional[str] = None
    recent_30d_reviews: Optional[int] = None
    keywords_used: List[str] = []
    place_results: List[Any] = []
    competitor: Any = {}
    scores: Any = {}
    ad_flags: Any = {}
    prev_analysis: Any = None  # 직전 분석 결과 (비교용)
    # J단계: 히스토리 추세 표시용
    analysis_count: int = 0  # 이 가게 N번째 분석
    prev_analyzed_at: Optional[str] = None  # 지난 분석 날짜
    keyword_history: Any = {}  # 키워드별 과거 순위 {"키워드": [{"rank": N, "date": "MM/DD"}, ...]}


class BlogAnalyzeRequest(BaseModel):
    store_name: str
    place_id: str
    address: str = ""
    category: str = ""
    keywords: List[str] = []


class BlogStandaloneRequest(BaseModel):
    store_name: str
    place_url: str
    anon_id: Optional[str] = None  # K단계: 익명 식별자
    search_query: Optional[str] = None  # 유입 키워드


class BlogAnalyzeResponse(BaseModel):
    blog_results: List[Any] = []
    total_matched: int = 0
    analyzed_keywords: int = 0


class LeadRequest(BaseModel):
    contact: str
    source: str = "web"
    store_id: Optional[int] = None


class LeadResponse(BaseModel):
    id: int
    contact: Optional[str]
    source: Optional[str]
    status: str

    model_config = {"from_attributes": True}


# K단계: 최근 본 매장
class RecentStore(BaseModel):
    place_id: str
    store_name: str
    address: str = ""
    category: str = ""
    analysis_type: str = "place"
    total_score: Optional[float] = None
    analyzed_at: Optional[str] = None


class RecentStoresResponse(BaseModel):
    stores: List[RecentStore] = []


# M단계: 내 매장 / 경쟁 매장 등록
class RegisterStoreRequest(BaseModel):
    anon_id: str
    place_id: str
    store_name: str
    store_type: str  # 'my' | 'rival'


class RegisteredStoreInfo(BaseModel):
    id: int
    place_id: str
    store_name: str
    store_type: str
    registered_at: Optional[str] = None
    # 추가 정보 (최근 분석 결과에서)
    total_score: Optional[float] = None
    analyzed_at: Optional[str] = None
    top_keyword: Optional[str] = None
    top_rank: Optional[int] = None


class RegisteredStoresResponse(BaseModel):
    my_stores: List[RegisteredStoreInfo] = []
    rival_stores: List[RegisteredStoreInfo] = []


# ─────────────────────────────────────────────────────────────────────────────
# 알림톡 구독
# ─────────────────────────────────────────────────────────────────────────────

class SubscribeRequest(BaseModel):
    store_name: str
    phone: str
    store_url: Optional[str] = None
    place_id: Optional[str] = None
    anon_id: Optional[str] = None
    agreed: bool = False  # 수신 동의 체크


class SubscribeResponse(BaseModel):
    id: int
    store_name: str
    phone: str
    alarm_on: bool
    message: str = "신청 완료"

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# 관리자 API
# ─────────────────────────────────────────────────────────────────────────────

class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminStatsResponse(BaseModel):
    total_analyses: int
    registered_stores: int
    subscriber_count: int
    new_subscribers_week: int
    new_analyses_week: int


class SubscriberInfo(BaseModel):
    id: int
    store_name: str
    phone: str
    place_id: Optional[str] = None
    alarm_on: bool
    created_at: Optional[str] = None
    last_analyzed_at: Optional[str] = None


class AlimTemplateUpdate(BaseModel):
    template_key: str
    extra_text: str
