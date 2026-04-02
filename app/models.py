import uuid
from datetime import datetime
from sqlalchemy import Boolean, Integer, Numeric, String, Text, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ApiUser(Base):
    __tablename__ = "api_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="free")
    credits: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    credits_reset_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=True, default=None)
    api_key_plain: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    password_hash: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    full_name: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    phone: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    company: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    use_case: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=datetime.utcnow)


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    api_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    credits_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=datetime.utcnow)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    api_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="KES")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    provider: Mapped[str] = mapped_column(Text, nullable=True)
    provider_ref: Mapped[str] = mapped_column(Text, nullable=True)
    credits_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=datetime.utcnow)


class TierConfig(Base):
    __tablename__ = "tier_config"

    tier: Mapped[str] = mapped_column(String(20), primary_key=True)
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    monthly_calls: Mapped[int] = mapped_column(Integer, nullable=True)   # NULL = unlimited
    price_usd: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)       # monthly (KES)
    price_weekly: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    price_quarterly: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    price_annual: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    search_limit: Mapped[int] = mapped_column(Integer, nullable=True)    # NULL = unlimited
    list_limit: Mapped[int] = mapped_column(Integer, nullable=True)      # NULL = unlimited, -1 = blocked


class ScrapeHistory(Base):
    __tablename__ = "scrape_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_url: Mapped[str] = mapped_column(Text, nullable=True)
    file_name: Mapped[str] = mapped_column(Text, nullable=True)
    records_count: Mapped[int] = mapped_column(Integer, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=datetime.utcnow)
