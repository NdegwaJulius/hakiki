import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, Base, engine, get_db
from app.dependencies import deduct_credit_and_log, get_current_user, hash_api_key
from app.models import ApiUser
from app.routers import web as web_router
import app.tier_config as tier_config

load_dotenv()

RATE_LIMIT  = os.getenv("RATE_LIMIT", "100/minute")
API_VERSION = os.getenv("API_VERSION", "v1")
PREFIX      = f"/sacco/{API_VERSION}"

DEV_API_KEY = os.getenv("DEV_API_KEY", "")
DEV_EMAIL   = "dev@hakiki.local"

JSON_PATH = Path(__file__).parent.parent / "saccos_2026.json"

_SACCOS: list[dict] = []

_ALL_FIELDS = {"name", "county", "schedule", "business_type", "is_visible",
               "postal_address", "physical_location"}

_TIER_FIELDS: dict[str, set] = {
    "free":       _ALL_FIELDS,
    "pro":        _ALL_FIELDS,
    "enterprise": _ALL_FIELDS,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # ADD COLUMN IF NOT EXISTS works on Postgres; SQLite uses try/except
        _pg = str(conn.engine.url).startswith("postgresql")
        for col_sql in [
            "ALTER TABLE api_users ADD COLUMN{} credits_reset_at TIMESTAMP",
            "ALTER TABLE api_users ADD COLUMN{} password_hash TEXT",
            "ALTER TABLE api_users ADD COLUMN{} api_key_plain TEXT",
            "ALTER TABLE api_users ADD COLUMN{} full_name TEXT",
            "ALTER TABLE api_users ADD COLUMN{} phone TEXT",
            "ALTER TABLE api_users ADD COLUMN{} company TEXT",
            "ALTER TABLE api_users ADD COLUMN{} use_case TEXT",
            "ALTER TABLE tier_config ADD COLUMN{} price_weekly NUMERIC(10,2) NOT NULL DEFAULT 0",
            "ALTER TABLE tier_config ADD COLUMN{} price_quarterly NUMERIC(10,2) NOT NULL DEFAULT 0",
            "ALTER TABLE tier_config ADD COLUMN{} price_annual NUMERIC(10,2) NOT NULL DEFAULT 0",
        ]:
            try:
                await conn.execute(text(col_sql.format(" IF NOT EXISTS" if _pg else "")))
            except Exception:
                pass

    async with AsyncSessionLocal() as db:
        # Seed dev user (only when DEV_API_KEY is explicitly set — never in production)
        if DEV_API_KEY:
            existing = await db.execute(select(ApiUser).where(ApiUser.email == DEV_EMAIL))
            if not existing.scalar_one_or_none():
                db.add(ApiUser(email=DEV_EMAIL, api_key_hash=hash_api_key(DEV_API_KEY), tier="enterprise", credits=0))
                await db.commit()

        # Seed + load tier config
        await tier_config.seed_defaults(db)
        await tier_config.load(db)

    with open(JSON_PATH) as f:
        _SACCOS.extend(json.load(f))

    print(f"\n  API key : {DEV_API_KEY}")
    print(f"  Loaded  : {len(_SACCOS)} SACCOs into memory")
    print(f"  Docs    : http://localhost:8000/docs\n")

    yield


def _rate_key(request: Request) -> str:
    return request.headers.get("X-API-Key") or (request.client.host if request.client else "unknown")

limiter = Limiter(key_func=_rate_key)

app = FastAPI(
    title="Hakiki API",
    version=API_VERSION,
    lifespan=lifespan,
    docs_url=None,       # disable public docs
    redoc_url=None,      # disable public redoc
    openapi_url=None,    # disable public schema
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Protected docs ────────────────────────────────────────────────────────────

from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from itsdangerous import URLSafeTimedSerializer as _Signer, BadSignature as _Bad
import os as _os

_doc_signer = _Signer(_os.getenv("SECRET_KEY", "dev-secret"))
_DOC_SESSION_MAX = 7 * 24 * 3600


def _doc_user(request: Request) -> bool:
    token = request.cookies.get("session")
    if not token:
        return False
    try:
        _doc_signer.loads(token, max_age=_DOC_SESSION_MAX)
        return True
    except _Bad:
        return False


@app.get("/openapi.json", include_in_schema=False)
async def openapi_schema(request: Request):
    if not _doc_user(request):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login")
    return get_openapi(title=app.title, version=app.version, routes=app.routes)


@app.get("/docs", include_in_schema=False)
async def swagger_ui(request: Request):
    if not _doc_user(request):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login")
    return get_swagger_ui_html(openapi_url="/openapi.json", title="Hakiki API Docs")
app.include_router(web_router.router)


def _serialize(sacco: dict, tier: str) -> dict:
    allowed = _TIER_FIELDS.get(tier, _TIER_FIELDS["free"])
    return {k: v for k, v in sacco.items() if k in allowed}


def _filter(
    saccos: list[dict],
    q: Optional[str] = None,
    county: Optional[str] = None,
    schedule: Optional[str] = None,
    business_type: Optional[str] = None,
) -> list[dict]:
    out = saccos
    if q:
        q_l = q.lower()
        out = [s for s in out if q_l in s["name"].lower()]
    if county:
        c_l = county.lower()
        out = [s for s in out if c_l in s["county"].lower()]
    if schedule:
        out = [s for s in out if s["schedule"] == schedule.upper()]
    if business_type:
        out = [s for s in out if s["business_type"] == business_type.lower()]
    return out


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get(f"{PREFIX}/verify")
@limiter.limit(RATE_LIMIT)
async def verify_sacco(
    request: Request,
    name: str = Query(..., description="SACCO name or partial name"),
    db: AsyncSession = Depends(get_db),
    current_user: ApiUser = Depends(get_current_user),
):
    matches = _filter(_SACCOS, q=name)[:10]
    found = len(matches) > 0
    await deduct_credit_and_log(current_user, f"{PREFIX}/verify", db, found=found)
    return {
        "query":       name,
        "is_licensed": found,
        "count":       len(matches),
        "matches":     [_serialize(s, current_user.tier) for s in matches],
    }


@app.get(f"{PREFIX}/search")
@limiter.limit(RATE_LIMIT)
async def search_saccos(
    request: Request,
    q:             Optional[str] = Query(None),
    county:        Optional[str] = Query(None),
    schedule:      Optional[str] = Query(None, description="I | II | III | IV | V"),
    business_type: Optional[str] = Query(None),
    page:          int           = Query(1, ge=1),
    db: AsyncSession  = Depends(get_db),
    current_user: ApiUser = Depends(get_current_user),
):
    cfg = tier_config.get(current_user.tier)
    filtered = _filter(_SACCOS, q=q, county=county, schedule=schedule, business_type=business_type)
    total    = len(filtered)

    limit      = cfg.search_limit
    page_slice = filtered[(page - 1) * limit : page * limit] if limit else filtered

    found = len(page_slice) > 0
    await deduct_credit_and_log(current_user, f"{PREFIX}/search", db, found=found)
    return {"total": total, "page": page, "results": [_serialize(s, current_user.tier) for s in page_slice]}


@app.get(f"{PREFIX}/list")
@limiter.limit(RATE_LIMIT)
async def list_saccos(
    request: Request,
    page:     int           = Query(1, ge=1),
    county:   Optional[str] = Query(None),
    schedule: Optional[str] = Query(None, description="I | II | III | IV | V"),
    db: AsyncSession  = Depends(get_db),
    current_user: ApiUser = Depends(get_current_user),
):
    cfg = tier_config.get(current_user.tier)
    if cfg.list_limit == -1:
        raise HTTPException(status_code=403, detail="Upgrade to Pro or Enterprise to access /list.")

    filtered   = _filter(_SACCOS, county=county, schedule=schedule)
    total      = len(filtered)
    limit      = cfg.list_limit
    page_slice = filtered[(page - 1) * limit : page * limit] if limit else filtered

    await deduct_credit_and_log(current_user, f"{PREFIX}/list", db, found=True)
    return {"total": total, "page": page, "results": [_serialize(s, current_user.tier) for s in page_slice]}


@app.get(f"{PREFIX}/stats")
@limiter.limit(RATE_LIMIT)
async def stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: ApiUser = Depends(get_current_user),
):
    from collections import Counter
    by_schedule     = Counter(s["schedule"]      for s in _SACCOS)
    by_type         = Counter(s["business_type"] for s in _SACCOS)
    by_county       = Counter(s["county"]        for s in _SACCOS)
    await deduct_credit_and_log(current_user, f"{PREFIX}/stats", db, found=True)
    return {
        "total": len(_SACCOS),
        "by_schedule":     dict(sorted(by_schedule.items())),
        "by_business_type": dict(by_type.most_common()),
        "by_county":       dict(by_county.most_common()),
    }
