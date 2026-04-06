import hashlib
from datetime import datetime, timezone
from fastapi import Header, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.database import get_db
from app.models import ApiUser, UsageLog

def _monthly_limit(tier: str) -> int | None:
    """Pull from live tier config cache."""
    from app.tier_config import get as get_cfg
    return get_cfg(tier).monthly_calls

# Keep for backward compat with web.py imports
TIER_MONTHLY_LIMIT: dict[str, int | None] = {
    "free": 10, "pro": 5000, "enterprise": None,
}


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def _reset_quota_if_new_month(user: ApiUser, db: AsyncSession) -> ApiUser:
    """Reset credits to monthly limit on first call of a new month."""
    monthly_limit = TIER_MONTHLY_LIMIT.get(user.tier)
    if monthly_limit is None:
        return user  # enterprise — no quota to reset

    now = datetime.utcnow()
    last = user.credits_reset_at

    if last is None or (now.year, now.month) != (last.year, last.month):
        await db.execute(
            update(ApiUser)
            .where(ApiUser.id == user.id)
            .values(credits=monthly_limit, credits_reset_at=now)
        )
        await db.commit()
        user.credits = monthly_limit
        user.credits_reset_at = now

    return user


async def get_current_user(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> ApiUser:
    key_hash = hash_api_key(x_api_key)
    result = await db.execute(
        select(ApiUser).where(ApiUser.api_key_hash == key_hash, ApiUser.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    user = await _reset_quota_if_new_month(user, db)

    monthly_limit = _monthly_limit(user.tier)
    if monthly_limit is not None and user.credits <= 0:
        raise HTTPException(
            status_code=402,
            detail=f"Monthly limit of {monthly_limit} calls reached. Upgrade to continue.",
        )

    return user


async def deduct_credit_and_log(
    user: ApiUser,
    endpoint: str,
    db: AsyncSession,
    found: bool = True,
) -> None:
    """
    Log every call. Deduct 1 credit only when a SACCO was found (successful hit).
    Enterprise users are never deducted.
    """
    credits_used = 0
    if found and _monthly_limit(user.tier) is not None:
        credits_used = 1
        await db.execute(
            update(ApiUser)
            .where(ApiUser.id == user.id)
            .values(credits=ApiUser.credits - 1)
        )
    db.add(UsageLog(api_user_id=user.id, endpoint=endpoint, credits_used=credits_used))
    await db.commit()
