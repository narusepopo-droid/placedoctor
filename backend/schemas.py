from typing import Any, List, Optional
from pydantic import BaseModel


class DiagnoseRequest(BaseModel):
    store_name: str
    place_url: str
    force_refresh: bool = False


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
    keywords_used: List[str] = []
    place_results: List[Any] = []
    competitor: Any = {}
    scores: Any = {}


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
