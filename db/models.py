from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    text, BigInteger, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, new_uuid


class UserStatus(str, enum.Enum):
    active = "active"
    deleted = "deleted"
    blocked = "blocked"


class SourceStatus(str, enum.Enum):
    active = "active"
    paused = "paused"


class RuleType(str, enum.Enum):
    keyword = "keyword"
    category = "category"


class Verdict(str, enum.Enum):
    matched = "matched"
    rejected = "rejected"
    error = "error"


class LLMStatus(str, enum.Enum):
    ok = "ok"
    unavailable = "unavailable"
    error = "error"
    skipped = "skipped"


class DeliveryStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    failed = "failed"
    cancelled = "cancelled"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.active, nullable=False)
    # Пауза уведомлений: сбор данных продолжается, delivery не создаются
    notifications_paused: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )

    profiles: Mapped[list["Profile"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Profile(TimestampMixin, Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    min_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    user: Mapped["User"] = relationship(back_populates="profiles")
    sources: Mapped[list["Source"]] = relationship(secondary="profile_sources", back_populates="profiles")
    filter_rules: Mapped[list["FilterRule"]] = relationship(back_populates="profile", cascade="all, delete-orphan")


class Source(TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[SourceStatus] = mapped_column(Enum(SourceStatus), default=SourceStatus.active, nullable=False)
    poll_interval_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    # RSS/Atom-лента, добавленная пользователем из бота (NULL — встроенный источник)
    feed_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)  # уникальность — индекс uq_sources_feed_url
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # telegram_user_id
    feed_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)  # rss | page

    profiles: Mapped[list["Profile"]] = relationship(secondary="profile_sources", back_populates="sources")
    jobs: Mapped[list["Job"]] = relationship(back_populates="source")


class ProfileSource(Base):
    __tablename__ = "profile_sources"
    __table_args__ = (UniqueConstraint("profile_id", "source_id", name="uq_profile_source"),)

    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True)


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_job_external"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="RUB", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    source: Mapped["Source"] = relationship(back_populates="jobs")


class FilterRule(Base):
    __tablename__ = "filter_rules"
    __table_args__ = (UniqueConstraint("profile_id", "rule_type", "value", name="uq_filter_rule"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    rule_type: Mapped[RuleType] = mapped_column(Enum(RuleType), nullable=False)
    value: Mapped[str] = mapped_column(String(128), nullable=False)
    is_exclusion: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    profile: Mapped["Profile"] = relationship(back_populates="filter_rules")


class MatchDecision(TimestampMixin, Base):
    __tablename__ = "match_decisions"
    __table_args__ = (UniqueConstraint("profile_id", "job_id", name="uq_match_decision"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)

    job: Mapped["Job"] = relationship()
    profile: Mapped["Profile"] = relationship()
    verdict: Mapped[Verdict] = mapped_column(Enum(Verdict), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasons: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-список строкой
    llm_status: Mapped[LLMStatus] = mapped_column(Enum(LLMStatus), default=LLMStatus.skipped, nullable=False)


class NotificationDelivery(TimestampMixin, Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("match_decisions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[DeliveryStatus] = mapped_column(Enum(DeliveryStatus), default=DeliveryStatus.pending, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    decision: Mapped["MatchDecision"] = relationship()
