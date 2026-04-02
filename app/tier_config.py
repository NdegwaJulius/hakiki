"""
In-memory cache for tier configuration.
Loaded from DB on startup, refreshed when admin saves changes.
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class TierCfg:
    tier: str
    label: str
    monthly_calls: Optional[int]   # None = unlimited
    price_usd: float               # monthly price in KES
    price_weekly: float
    price_quarterly: float
    price_annual: float
    search_limit: Optional[int]    # None = unlimited
    list_limit: Optional[int]      # None = unlimited, -1 = blocked


# Module-level cache — keyed by tier name
_cache: dict[str, TierCfg] = {}

DEFAULTS = [
    TierCfg("free",       "Free",       monthly_calls=10,   price_usd=0,    price_weekly=0,    price_quarterly=0,     price_annual=0,      search_limit=3,    list_limit=-1),
    TierCfg("pro",        "Pro",        monthly_calls=5000, price_usd=2500, price_weekly=700,  price_quarterly=7000,  price_annual=25000,  search_limit=5,    list_limit=10),
    TierCfg("enterprise", "Enterprise", monthly_calls=None, price_usd=7500, price_weekly=2000, price_quarterly=20000, price_annual=70000,  search_limit=None, list_limit=None),
]


async def load(db: AsyncSession) -> None:
    """Load tier config from DB into cache. Call on startup and after admin saves."""
    from app.models import TierConfig
    result = await db.execute(select(TierConfig))
    rows = result.scalars().all()
    _cache.clear()
    for row in rows:
        _cache[row.tier] = TierCfg(
            tier=row.tier,
            label=row.label,
            monthly_calls=row.monthly_calls,
            price_usd=float(row.price_usd),
            price_weekly=float(getattr(row, "price_weekly", 0) or 0),
            price_quarterly=float(getattr(row, "price_quarterly", 0) or 0),
            price_annual=float(getattr(row, "price_annual", 0) or 0),
            search_limit=row.search_limit,
            list_limit=row.list_limit,
        )


def get(tier: str) -> TierCfg:
    return _cache.get(tier, DEFAULTS[0])


def all_tiers() -> list[TierCfg]:
    order = ["free", "pro", "enterprise"]
    return [_cache.get(t) for t in order if t in _cache]


async def seed_defaults(db: AsyncSession) -> None:
    """Insert default config rows if the table is empty."""
    from app.models import TierConfig
    result = await db.execute(select(TierConfig))
    if result.scalars().first():
        return  # already seeded
    for d in DEFAULTS:
        db.add(TierConfig(
            tier=d.tier, label=d.label, monthly_calls=d.monthly_calls,
            price_usd=d.price_usd, price_weekly=d.price_weekly,
            price_quarterly=d.price_quarterly, price_annual=d.price_annual,
            search_limit=d.search_limit, list_limit=d.list_limit,
        ))
    await db.commit()
