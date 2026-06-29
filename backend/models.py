from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, Text,
)
from sqlalchemy.orm import relationship
from .database import Base


def _now():
    return datetime.now(timezone.utc)


class Client(Base):
    __tablename__ = "clients"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(100), nullable=False)
    contact    = Column(String(200))
    plan       = Column(String(50))
    status     = Column(String(50))
    created_at = Column(DateTime(timezone=True), default=_now)

    stores = relationship("Store", back_populates="client")


class Store(Base):
    __tablename__ = "stores"

    id         = Column(Integer, primary_key=True, index=True)
    client_id  = Column(Integer, ForeignKey("clients.id"), nullable=True)
    name       = Column(String(200), nullable=False)
    place_id   = Column(String(20), unique=True, index=True, nullable=True)
    place_url  = Column(Text)
    category   = Column(String(100))
    address    = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_now)

    client          = relationship("Client", back_populates="stores")
    keywords        = relationship("Keyword", back_populates="store", cascade="all, delete-orphan")
    rank_snapshots  = relationship("RankSnapshot", back_populates="store")
    detail          = relationship("StoreDetail", back_populates="store", uselist=False)
    competitors     = relationship("Competitor", back_populates="store")
    score_snapshots = relationship("ScoreSnapshot", back_populates="store")
    leads           = relationship("Lead", back_populates="store")


class Keyword(Base):
    __tablename__ = "keywords"

    id             = Column(Integer, primary_key=True, index=True)
    store_id       = Column(Integer, ForeignKey("stores.id"), nullable=False)
    keyword        = Column(String(200), nullable=False)
    is_custom      = Column(Boolean, default=False)
    auto_generated = Column(Boolean, default=True)

    store = relationship("Store", back_populates="keywords")


class RankSnapshot(Base):
    __tablename__ = "rank_snapshots"

    id          = Column(Integer, primary_key=True, index=True)
    store_id    = Column(Integer, ForeignKey("stores.id"), nullable=False)
    keyword     = Column(String(200), nullable=False)
    mode        = Column(String(10), nullable=False, default="place")  # 'place' | 'blog'
    rank        = Column(Integer, nullable=True)
    captured_at = Column(DateTime(timezone=True), default=_now)

    store = relationship("Store", back_populates="rank_snapshots")


class StoreDetail(Base):
    __tablename__ = "store_details"

    store_id            = Column(Integer, ForeignKey("stores.id"), primary_key=True)
    visitor_reviews     = Column(Integer, nullable=True)
    blog_reviews        = Column(Integer, nullable=True)
    star_score          = Column(Float, nullable=True)
    photo_count         = Column(Integer, nullable=True)
    latest_review_date  = Column(String(20), nullable=True)
    updated_at          = Column(DateTime(timezone=True), default=_now)
    cached_json         = Column(Text, nullable=True)

    store = relationship("Store", back_populates="detail")


class Competitor(Base):
    __tablename__ = "competitors"

    id                   = Column(Integer, primary_key=True, index=True)
    store_id             = Column(Integer, ForeignKey("stores.id"), nullable=False)
    keyword              = Column(String(200))
    competitor_place_id  = Column(String(20))
    rank                 = Column(Integer, nullable=True)
    visitor_reviews      = Column(Integer, nullable=True)
    captured_at          = Column(DateTime(timezone=True), default=_now)

    store = relationship("Store", back_populates="competitors")


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"

    id          = Column(Integer, primary_key=True, index=True)
    store_id    = Column(Integer, ForeignKey("stores.id"), nullable=False)
    seo         = Column(Float, nullable=True)
    content     = Column(Float, nullable=True)
    activity    = Column(Float, nullable=True)
    ad          = Column(Float, nullable=True)
    total       = Column(Float, nullable=True)
    captured_at = Column(DateTime(timezone=True), default=_now)

    store = relationship("Store", back_populates="score_snapshots")


class Lead(Base):
    __tablename__ = "leads"

    id         = Column(Integer, primary_key=True, index=True)
    store_id   = Column(Integer, ForeignKey("stores.id"), nullable=True)
    contact    = Column(String(200))
    source     = Column(String(100))
    status     = Column(String(20), default="new")  # new | contacted | won | lost
    created_at = Column(DateTime(timezone=True), default=_now)

    store = relationship("Store", back_populates="leads")


class AnalysisHistory(Base):
    """분석 히스토리 — 분석할 때마다 1레코드 누적 (덮어쓰기 X)"""
    __tablename__ = "analysis_history"

    id             = Column(Integer, primary_key=True, index=True)
    place_id       = Column(String(20), index=True, nullable=False)
    store_name     = Column(String(200), nullable=False)
    analysis_type  = Column(String(10), nullable=False)  # 'place' | 'blog'
    analyzed_at    = Column(DateTime(timezone=True), default=_now, index=True)
    total_score    = Column(Float, nullable=True)  # 플레이스 분석 시 종합점수
    result_json    = Column(Text, nullable=True)   # 키워드별 순위 등 전체 결과 JSON
    anon_id        = Column(String(36), index=True, nullable=True)  # K단계: 익명 식별자 (UUID)
    source         = Column(String(20), nullable=True)  # 유입경로: direct | blog | search | etc


class RegisteredStore(Base):
    """M단계: 내 매장 / 경쟁 매장 등록"""
    __tablename__ = "registered_stores"

    id            = Column(Integer, primary_key=True, index=True)
    anon_id       = Column(String(36), index=True, nullable=False)
    place_id      = Column(String(20), index=True, nullable=False)
    store_name    = Column(String(200), nullable=False)
    store_type    = Column(String(10), nullable=False)  # 'my' | 'rival'
    registered_at = Column(DateTime(timezone=True), default=_now)


class Subscriber(Base):
    """알림톡 구독자 (전화번호 수집)"""
    __tablename__ = "subscribers"

    id               = Column(Integer, primary_key=True, index=True)
    anon_id          = Column(String(36), index=True, nullable=True)
    store_name       = Column(String(200), nullable=False)
    store_url        = Column(Text, nullable=True)
    place_id         = Column(String(20), index=True, nullable=True)
    phone            = Column(String(20), nullable=False)
    alarm_on         = Column(Boolean, default=True)
    agreed_at        = Column(DateTime(timezone=True), default=_now)
    created_at       = Column(DateTime(timezone=True), default=_now)
    last_analyzed_at = Column(DateTime(timezone=True), nullable=True)
    memo             = Column(Text, nullable=True)
    status           = Column(String(20), default="new")  # new | contacted | contracted | hold | rejected


class AlimTemplate(Base):
    """알림톡 템플릿 (추가문구 관리)"""
    __tablename__ = "alim_templates"

    template_key = Column(String(50), primary_key=True)
    extra_text   = Column(Text, nullable=True)
    updated_at   = Column(DateTime(timezone=True), default=_now)


class AlimtalkLog(Base):
    """알림톡 발송 이력 (발송 시마다 1건 기록)"""
    __tablename__ = "alimtalk_logs"

    id            = Column(Integer, primary_key=True, index=True)
    sent_at       = Column(DateTime(timezone=True), default=_now, index=True)
    template_key  = Column(String(50), nullable=True)   # signup / weekly
    template_code = Column(String(50), nullable=True)   # UI_7449 / UI_7456
    phone         = Column(String(20), nullable=True)
    store_name    = Column(String(200), nullable=True)
    success       = Column(Boolean, default=False)
    result_code   = Column(String(20), nullable=True)
    message       = Column(Text, nullable=True)
