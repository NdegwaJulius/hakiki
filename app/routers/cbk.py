"""
CBK (Central Bank of Kenya) router — /cbk/v1/
Mirrors the SACCO router patterns: in-memory data, same auth middleware,
same rate limiting, same tier-based pagination, same error format.
"""

import json
import os
from collections import Counter
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import deduct_credit_and_log, get_current_user
from app.limiter import limiter, RATE_LIMIT
from app.models import ApiUser
import app.store as store
import app.tier_config as tier_config

# ── In-memory store (shared via app.store) ────────────────────────────────────

_CBK_ENTITIES = store.CBK_ENTITIES   # shared reference

VALID_ENTITY_TYPES = {"dcp", "psp", "forex", "mfb", "remittance"}

ENTITY_LABELS = {
    "dcp":        "Digital Credit Providers",
    "psp":        "Payment Service Providers",
    "forex":      "Forex Bureaus",
    "mfb":        "Microfinance Banks",
    "remittance": "Money Remittance Providers",
}

_ALL_FIELDS = {
    "license_number", "entity_name", "category", "sub_category",
    "status", "county", "town", "licensed_since", "license_expiry",
    "services_permitted", "source_doc", "last_scraped",
}

_TIER_FIELDS: dict[str, set] = {
    "free":       _ALL_FIELDS,
    "pro":        _ALL_FIELDS,
    "enterprise": _ALL_FIELDS,
}


def load_data(json_path: Path) -> None:
    """Load CBK entities from JSON into memory. Called from app lifespan."""
    if not json_path.exists():
        print(f"  ⚠  CBK data file not found: {json_path} — /cbk endpoints will return empty results")
        return
    with open(json_path) as f:
        data = json.load(f)
    store.reload_cbk(data)
    by_cat = Counter(r["category"] for r in data)
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items()))
    print(f"  Loaded  : {len(data)} CBK entities into memory  ({summary})")


# ── Router ────────────────────────────────────────────────────────────────────

API_VERSION = os.getenv("API_VERSION", "v1")

router = APIRouter(
    prefix=f"/cbk/{API_VERSION}",
    tags=["CBK"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_entity_type(entity_type: str) -> str:
    et = entity_type.lower()
    if et not in VALID_ENTITY_TYPES:
        raise HTTPException(
            status_code=422,
            detail={
                "error":  "invalid_entity_type",
                "valid":  sorted(VALID_ENTITY_TYPES),
                "given":  entity_type,
            },
        )
    return et


def _serialize(entity: dict, tier: str) -> dict:
    allowed = _TIER_FIELDS.get(tier, _TIER_FIELDS["free"])
    return {k: v for k, v in entity.items() if k in allowed}


def _filter(
    entities: list[dict],
    q:           Optional[str] = None,
    county:      Optional[str] = None,
    status:      Optional[str] = None,
    sub_category: Optional[str] = None,
) -> list[dict]:
    out = entities
    if q:
        q_l = q.lower()
        out = [e for e in out if q_l in e["entity_name"].lower()]
    if county:
        c_l = county.lower()
        out = [e for e in out if c_l in e["county"].lower()]
    if status:
        out = [e for e in out if e["status"] == status.lower()]
    if sub_category:
        sc_l = sub_category.lower()
        out = [e for e in out if sc_l in e["sub_category"].lower()]
    return out


def _by_type(entity_type: str) -> list[dict]:
    return [e for e in _CBK_ENTITIES if e["category"] == entity_type]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/health", summary="CBK router health check")
@limiter.limit(RATE_LIMIT)
async def health(request: Request):
    """Returns entity counts and data freshness. No auth required."""
    by_cat = Counter(e["category"] for e in _CBK_ENTITIES)
    return {
        "status":    "ok",
        "total":     len(_CBK_ENTITIES),
        "by_category": {
            et: {"label": ENTITY_LABELS[et], "count": by_cat.get(et, 0)}
            for et in ENTITY_LABELS
        },
    }


@router.get(
    "/{entity_type}/verify/{license_number:path}",
    summary="Verify a CBK licence by licence number",
)
@limiter.limit(RATE_LIMIT)
async def verify_entity(
    request: Request,
    entity_type:    str,
    license_number: str,
    db: AsyncSession = Depends(get_db),
    current_user: ApiUser = Depends(get_current_user),
):
    """
    Returns the entity record for the given licence number, or `is_licensed: false`
    if not found.  Charges 1 credit on a successful hit.
    """
    et = _validate_entity_type(entity_type)
    pool = _by_type(et)

    ln_l = license_number.strip().lower()
    matches = [e for e in pool if e["license_number"].lower() == ln_l]

    found = len(matches) > 0
    endpoint = f"/cbk/{API_VERSION}/{et}/verify"
    await deduct_credit_and_log(current_user, endpoint, db, found=found)

    return {
        "license_number": license_number,
        "entity_type":    et,
        "is_licensed":    found,
        "count":          len(matches),
        "matches":        [_serialize(m, current_user.tier) for m in matches],
    }


@router.get(
    "/{entity_type}/search",
    summary="Search CBK entities by name or filters",
)
@limiter.limit(RATE_LIMIT)
async def search_entities(
    request: Request,
    entity_type:  str,
    q:            Optional[str] = Query(None,  description="Entity name (partial match)"),
    county:       Optional[str] = Query(None,  description="Filter by county"),
    status:       Optional[str] = Query(None,  description="licensed | suspended | revoked | expired"),
    sub_category: Optional[str] = Query(None,  description="Sub-category / licence class"),
    page:         int           = Query(1, ge=1),
    db: AsyncSession  = Depends(get_db),
    current_user: ApiUser = Depends(get_current_user),
):
    et  = _validate_entity_type(entity_type)
    cfg = tier_config.get(current_user.tier)

    pool     = _by_type(et)
    filtered = _filter(pool, q=q, county=county, status=status, sub_category=sub_category)
    total    = len(filtered)

    limit      = cfg.search_limit
    page_slice = filtered[(page - 1) * limit : page * limit] if limit else filtered

    found = len(page_slice) > 0
    endpoint = f"/cbk/{API_VERSION}/{et}/search"
    await deduct_credit_and_log(current_user, endpoint, db, found=found)

    return {
        "entity_type": et,
        "total":       total,
        "page":        page,
        "results":     [_serialize(e, current_user.tier) for e in page_slice],
    }


@router.get(
    "/{entity_type}/list",
    summary="List all entities of a given type (Pro / Enterprise only)",
)
@limiter.limit(RATE_LIMIT)
async def list_entities(
    request: Request,
    entity_type:  str,
    page:         int           = Query(1, ge=1),
    county:       Optional[str] = Query(None),
    status:       Optional[str] = Query(None, description="licensed | suspended | revoked | expired"),
    sub_category: Optional[str] = Query(None),
    db: AsyncSession  = Depends(get_db),
    current_user: ApiUser = Depends(get_current_user),
):
    et  = _validate_entity_type(entity_type)
    cfg = tier_config.get(current_user.tier)

    if cfg.list_limit == -1:
        raise HTTPException(
            status_code=403,
            detail="Upgrade to Pro or Enterprise to access /list.",
        )

    pool       = _by_type(et)
    filtered   = _filter(pool, county=county, status=status, sub_category=sub_category)
    total      = len(filtered)
    limit      = cfg.list_limit
    page_slice = filtered[(page - 1) * limit : page * limit] if limit else filtered

    endpoint = f"/cbk/{API_VERSION}/{et}/list"
    await deduct_credit_and_log(current_user, endpoint, db, found=True)

    return {
        "entity_type": et,
        "total":       total,
        "page":        page,
        "results":     [_serialize(e, current_user.tier) for e in page_slice],
    }
