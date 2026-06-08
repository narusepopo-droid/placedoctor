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


class BlogAnalyzeRequest(BaseModel):
    store_name: str
    place_id: str
    address: str = ""
    keywords: List[str] = []


class BlogStandaloneRequest(BaseModel):
    store_name: str
    place_url: str


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
