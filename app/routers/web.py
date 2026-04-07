import asyncio
import hashlib
import hmac
import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import TIER_MONTHLY_LIMIT, hash_api_key
from app.models import ApiUser, UsageLog
import app.store as store

router = APIRouter()
templates = Jinja2Templates(directory="frontend/templates")


def _hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"{salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, dk_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False

SECRET_KEY   = os.getenv("SECRET_KEY", "dev-secret")
ADMIN_EMAIL  = os.getenv("ADMIN_EMAIL", "").strip().lower()
APP_URL      = os.getenv("APP_URL", "http://localhost:8000")
_signer      = URLSafeTimedSerializer(SECRET_KEY)
_reset_signer = URLSafeTimedSerializer(SECRET_KEY + "-reset")
SESSION_MAX  = 7 * 24 * 3600   # 7 days
RESET_MAX    = 1 * 60 * 60     # 1 hour


def _is_admin(user: ApiUser) -> bool:
    return bool(ADMIN_EMAIL) and user.email.strip().lower() == ADMIN_EMAIL


# ── Session helpers ───────────────────────────────────────────────────────────

def _make_cookie(user_id: str) -> str:
    return _signer.dumps(user_id)


def _read_cookie(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _signer.loads(token, max_age=SESSION_MAX)
    except BadSignature:
        return None


async def _current_user(request: Request, db: AsyncSession = Depends(get_db)) -> ApiUser | None:
    user_id = _read_cookie(request.cookies.get("session"))
    if not user_id:
        return None
    result = await db.execute(select(ApiUser).where(ApiUser.id == user_id, ApiUser.is_active == True))
    return result.scalar_one_or_none()


def _require_user(request: Request):
    """Sync check — redirect to login if no session cookie present."""
    if not request.cookies.get("session"):
        return RedirectResponse("/login", status_code=302)
    return None


# ── Pages ─────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if _read_cookie(request.cookies.get("session")):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("landing.html", {"request": request, "tiers": _tier_prices()})


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    if _read_cookie(request.cookies.get("session")):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request, "tab": "register"})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _read_cookie(request.cookies.get("session")):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request, "tab": "login"})


@router.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    phone: str = Form(...),
    company: str = Form(default=""),
    use_case: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    # Check duplicate email
    existing = await db.execute(select(ApiUser).where(ApiUser.email == email))
    if existing.scalar_one_or_none():
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": "Email already registered.", "tab": "register"},
            status_code=400,
        )

    raw_key = f"hk_{secrets.token_hex(20)}"
    tier = "enterprise" if email == ADMIN_EMAIL else "free"
    user = ApiUser(
        email=email,
        password_hash=_hash_password(password),
        api_key_hash=hash_api_key(raw_key),
        api_key_plain=raw_key,
        tier=tier,
        credits=TIER_MONTHLY_LIMIT.get(tier) or 0,
        full_name=full_name.strip() or None,
        phone=phone.strip() or None,
        company=company.strip() or None,
        use_case=use_case.strip() or None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie("session", _make_cookie(user.id), max_age=SESSION_MAX, httponly=True)
    return response


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ApiUser).where(ApiUser.email == email, ApiUser.is_active == True))
    user = result.scalar_one_or_none()

    if not user or not user.password_hash or not _verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": "Invalid email or password.", "tab": "login"},
            status_code=401,
        )

    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie("session", _make_cookie(user.id), max_age=SESSION_MAX, httponly=True)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie("session")
    return response


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, new_key: str = None, db: AsyncSession = Depends(get_db)):
    user = await _current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    # Reset quota display if new month
    monthly_limit = TIER_MONTHLY_LIMIT.get(user.tier)
    if monthly_limit:
        now = datetime.utcnow()
        last = user.credits_reset_at
        if last is None or (now.year, now.month) != (last.year, last.month):
            await db.execute(
                update(ApiUser).where(ApiUser.id == user.id)
                .values(credits=monthly_limit, credits_reset_at=now)
            )
            await db.commit()
            user.credits = monthly_limit

    # If key was created before we stored plain keys, assign one now
    if not user.api_key_plain:
        raw_key = f"hk_{secrets.token_hex(20)}"
        await db.execute(
            update(ApiUser).where(ApiUser.id == user.id)
            .values(api_key_hash=hash_api_key(raw_key), api_key_plain=raw_key)
        )
        await db.commit()
        user.api_key_plain = raw_key

    # Last 10 usage logs
    logs_result = await db.execute(
        select(UsageLog)
        .where(UsageLog.api_user_id == user.id)
        .order_by(UsageLog.created_at.desc())
        .limit(10)
    )
    logs = logs_result.scalars().all()

    return templates.TemplateResponse("dashboard.html", {
        "request":       request,
        "user":          user,
        "logs":          logs,
        "monthly_limit": monthly_limit,
        "is_admin":      _is_admin(user),
    })


@router.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(request, db)
    if not user or user.email.strip().lower() != ADMIN_EMAIL:
        return RedirectResponse("/login", status_code=302)

    users_result = await db.execute(select(ApiUser).order_by(ApiUser.created_at.desc()))
    users = users_result.scalars().all()

    return templates.TemplateResponse("admin.html", {
        "request":  request,
        "users":    users,
        "admin":    user,
        "is_admin": True,
    })


@router.post("/admin/users/{user_id}/tier")
async def update_tier(
    user_id: str,
    request: Request,
    tier: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    admin = await _current_user(request, db)
    if not admin or admin.email.strip().lower() != ADMIN_EMAIL:
        return RedirectResponse("/login", status_code=302)

    if tier not in TIER_MONTHLY_LIMIT:
        return RedirectResponse("/admin", status_code=302)

    new_credits = TIER_MONTHLY_LIMIT[tier] or 0
    await db.execute(
        update(ApiUser).where(ApiUser.id == user_id)
        .values(tier=tier, credits=new_credits, credits_reset_at=datetime.utcnow())
    )
    await db.commit()
    return RedirectResponse("/admin", status_code=302)


@router.post("/admin/users/{user_id}/toggle")
async def toggle_user(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _current_user(request, db)
    if not admin or admin.email.strip().lower() != ADMIN_EMAIL:
        return RedirectResponse("/login", status_code=302)

    result = await db.execute(select(ApiUser).where(ApiUser.id == user_id))
    target = result.scalar_one_or_none()
    if target:
        await db.execute(
            update(ApiUser).where(ApiUser.id == user_id).values(is_active=not target.is_active)
        )
        await db.commit()
    return RedirectResponse("/admin", status_code=302)


# ── Forgot / reset password ───────────────────────────────────────────────────

@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, sent: str = None):
    return templates.TemplateResponse("forgot_password.html", {"request": request, "sent": sent})


@router.post("/forgot-password")
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ApiUser).where(ApiUser.email == email, ApiUser.is_active == True))
    user = result.scalar_one_or_none()

    # Always show success to prevent email enumeration
    if user:
        token = _reset_signer.dumps(user.id)
        reset_url = f"{APP_URL}/reset-password?token={token}"
        # In production, email this link. For now, print to server console.
        print(f"\n  [PASSWORD RESET] {email} → {reset_url}\n")

    return RedirectResponse("/forgot-password?sent=1", status_code=302)


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = None):
    valid = False
    if token:
        try:
            _reset_signer.loads(token, max_age=RESET_MAX)
            valid = True
        except BadSignature:
            pass
    return templates.TemplateResponse("reset_password.html", {"request": request, "token": token, "valid": valid})


@router.post("/reset-password")
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        user_id = _reset_signer.loads(token, max_age=RESET_MAX)
    except BadSignature:
        return templates.TemplateResponse(
            "reset_password.html", {"request": request, "token": token, "valid": False}, status_code=400
        )

    await db.execute(
        update(ApiUser).where(ApiUser.id == user_id).values(password_hash=_hash_password(password))
    )
    await db.commit()
    return RedirectResponse("/login?reset=1", status_code=302)


# ── Rotate API key (security: invalidate current, issue new) ─────────────────

@router.post("/dashboard/rotate-key")
async def rotate_key(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    raw_key = f"hk_{secrets.token_hex(20)}"
    await db.execute(
        update(ApiUser).where(ApiUser.id == user.id)
        .values(api_key_hash=hash_api_key(raw_key), api_key_plain=raw_key)
    )
    await db.commit()
    return RedirectResponse("/dashboard", status_code=302)


# ── Billing ───────────────────────────────────────────────────────────────────

def _tier_prices():
    import app.tier_config as tc
    return {
        t.tier: {
            "price":     t.price_usd,        # monthly (backward compat)
            "weekly":    t.price_weekly,
            "monthly":   t.price_usd,
            "quarterly": t.price_quarterly,
            "annual":    t.price_annual,
            "label":     t.label,
            "calls":     f"{t.monthly_calls:,} / month" if t.monthly_calls else "Unlimited",
        }
        for t in tc.all_tiers()
    }

@router.get("/billing", response_class=HTMLResponse)
async def billing(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    from app.models import Payment
    payments_result = await db.execute(
        select(Payment).where(Payment.api_user_id == user.id)
        .order_by(Payment.created_at.desc()).limit(20)
    )
    payments = payments_result.scalars().all()

    monthly_limit = TIER_MONTHLY_LIMIT.get(user.tier)
    calls_used = (monthly_limit or 0) - user.credits if monthly_limit else 0

    tp = _tier_prices()
    return templates.TemplateResponse("billing.html", {
        "request":       request,
        "user":          user,
        "payments":      payments,
        "tier_info":     tp.get(user.tier, tp.get("free", {})),
        "all_tiers":     tp,
        "monthly_limit": monthly_limit,
        "calls_used":    calls_used,
        "is_admin":      _is_admin(user),
    })


# ── Admin finance ─────────────────────────────────────────────────────────────

@router.get("/admin/finance", response_class=HTMLResponse)
async def admin_finance(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _current_user(request, db)
    if not admin or admin.email.strip().lower() != ADMIN_EMAIL:
        return RedirectResponse("/login", status_code=302)

    from app.models import Payment
    from sqlalchemy import func

    users_result = await db.execute(select(ApiUser).order_by(ApiUser.created_at.desc()))
    users = users_result.scalars().all()

    payments_result = await db.execute(
        select(Payment).order_by(Payment.created_at.desc()).limit(50)
    )
    payments = payments_result.scalars().all()

    by_tier = {"free": 0, "pro": 0, "enterprise": 0}
    for u in users:
        by_tier[u.tier] = by_tier.get(u.tier, 0) + 1

    total_revenue_result = await db.execute(
        select(func.sum(Payment.amount)).where(Payment.status == "completed")
    )
    total_revenue = total_revenue_result.scalar() or 0

    return templates.TemplateResponse("admin_finance.html", {
        "request":       request,
        "admin":         admin,
        "users":         users,
        "payments":      payments,
        "by_tier":       by_tier,
        "total_revenue": total_revenue,
        "tier_prices":   _tier_prices(),
        "is_admin":      True,
    })


# ── Admin: tier config ────────────────────────────────────────────────────────

@router.get("/admin/config", response_class=HTMLResponse)
async def admin_config_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _current_user(request, db)
    if not admin or admin.email.strip().lower() != ADMIN_EMAIL:
        return RedirectResponse("/login", status_code=302)
    import app.tier_config as tc
    return templates.TemplateResponse("admin_config.html", {
        "request":  request,
        "tiers":    tc.all_tiers(),
        "saved":    request.query_params.get("saved"),
        "is_admin": True,
    })


@router.post("/admin/config")
async def admin_config_save(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await _current_user(request, db)
    if not admin or admin.email.strip().lower() != ADMIN_EMAIL:
        return RedirectResponse("/login", status_code=302)

    form = await request.form()
    from app.models import TierConfig
    import app.tier_config as tc

    for tier in ("free", "pro", "enterprise"):
        monthly   = form.get(f"{tier}_monthly")
        price_mo  = form.get(f"{tier}_price_monthly", "0")
        price_wk  = form.get(f"{tier}_price_weekly",  "0")
        price_qt  = form.get(f"{tier}_price_quarterly","0")
        price_an  = form.get(f"{tier}_price_annual",  "0")
        search    = form.get(f"{tier}_search")
        lst       = form.get(f"{tier}_list")
        label     = form.get(f"{tier}_label", tier.capitalize())

        def _f(v): return float(v) if v and v.strip() else 0

        await db.execute(
            update(TierConfig).where(TierConfig.tier == tier).values(
                label=label,
                monthly_calls=int(monthly) if monthly and monthly.strip() else None,
                price_usd=_f(price_mo),
                price_weekly=_f(price_wk),
                price_quarterly=_f(price_qt),
                price_annual=_f(price_an),
                search_limit=int(search) if search and search.strip() else None,
                list_limit=int(lst) if lst and lst.strip() else None,
            )
        )
    await db.commit()
    await tc.load(db)   # refresh cache immediately
    return RedirectResponse("/admin/config?saved=1", status_code=302)


# ── Admin: data uploads ───────────────────────────────────────────────────────

CBK_CATEGORIES = {
    "dcp":        "Digital Credit Providers",
    "psp":        "Payment Service Providers",
    "forex":      "Forex Bureaus",
    "mfb":        "Microfinance Banks",
    "remittance": "Money Remittance Providers",
}

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY  = os.getenv("SUPABASE_SERVICE_KEY", "")


def _supabase_upsert(table: str, records: list[dict], on_conflict: str) -> tuple[int, str]:
    """Upsert records to Supabase via REST API. Returns (status, message)."""
    import ssl
    import urllib.error
    import urllib.request
    ssl._create_default_https_context = ssl._create_unverified_context

    if not SUPABASE_URL or not SERVICE_KEY:
        return 0, "SUPABASE_URL / SUPABASE_SERVICE_KEY not set — skipped DB sync"

    DATE_FIELDS = {"licensed_since", "license_expiry"}

    def _clean(record: dict) -> dict:
        return {
            k: (None if (k in DATE_FIELDS and not v) else v)
            for k, v in record.items()
        }

    batch_size = 100
    for i in range(0, len(records), batch_size):
        batch = [_clean(r) for r in records[i : i + batch_size]]
        url   = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
        body  = json.dumps(batch).encode()
        req   = urllib.request.Request(url, data=body, method="POST")
        req.add_header("apikey",        SERVICE_KEY)
        req.add_header("Authorization", f"Bearer {SERVICE_KEY}")
        req.add_header("Content-Type",  "application/json")
        req.add_header("Prefer",        "return=minimal,resolution=merge-duplicates")
        try:
            with urllib.request.urlopen(req) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            return e.code, e.read()[:200].decode()

    return 200, f"{len(records)} records synced to Supabase"


@router.get("/admin/sources", response_class=HTMLResponse)
async def admin_data_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    msg: str = None,
    error: str = None,
):
    admin = await _current_user(request, db)
    if not admin or not _is_admin(admin):
        return RedirectResponse("/login", status_code=302)

    from collections import Counter
    from datetime import datetime as _dt
    cbk_counts = Counter(e["category"] for e in store.CBK_ENTITIES)

    # Compute regulator doc_date per category for display on sources page
    cbk_last_scraped: dict[str, str] = {}
    for cat in CBK_CATEGORIES:
        dates = [e.get("doc_date", "") for e in store.CBK_ENTITIES if e["category"] == cat and e.get("doc_date")]
        if dates:
            try:
                cbk_last_scraped[cat] = _dt.fromisoformat(max(dates)).strftime("%d %b %Y")
            except Exception:
                cbk_last_scraped[cat] = max(dates)

    return templates.TemplateResponse("admin_sources.html", {
        "request":          request,
        "admin":            admin,
        "is_admin":         True,
        "msg":              msg,
        "error":            error,
        "sacco_count":      len(store.SACCOS),
        "cbk_counts":       {cat: cbk_counts.get(cat, 0) for cat in CBK_CATEGORIES},
        "cbk_labels":       CBK_CATEGORIES,
        "cbk_last_scraped": cbk_last_scraped,
    })


@router.get("/admin/browse", response_class=HTMLResponse)
async def admin_browse(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await _current_user(request, db)
    if not admin or not _is_admin(admin):
        return RedirectResponse("/login", status_code=302)

    from collections import Counter
    from datetime import datetime as _dt
    cbk_counts = Counter(e["category"] for e in store.CBK_ENTITIES)

    cbk_doc_dates: dict[str, str] = {}
    for cat in CBK_CATEGORIES:
        dates = [e.get("doc_date", "") for e in store.CBK_ENTITIES if e["category"] == cat and e.get("doc_date")]
        if dates:
            try:
                cbk_doc_dates[cat] = _dt.fromisoformat(max(dates)).strftime("%d %b %Y")
            except Exception:
                cbk_doc_dates[cat] = max(dates)

    import os as _os
    _sacco_json = Path(__file__).parent.parent.parent / "saccos_2026.json"
    sacco_last_updated = None
    try:
        _mtime = _os.path.getmtime(_sacco_json)
        sacco_last_updated = _dt.utcfromtimestamp(_mtime).strftime("%d %b %Y")
    except Exception:
        pass

    return templates.TemplateResponse("admin_browse.html", {
        "request":           request,
        "is_admin":          True,
        "cbk_labels":        CBK_CATEGORIES,
        "cbk_counts":        {cat: cbk_counts.get(cat, 0) for cat in CBK_CATEGORIES},
        "cbk_doc_dates":     cbk_doc_dates,
        "cbk_total":         len(store.CBK_ENTITIES),
        "sacco_count":       len(store.SACCOS),
        "sacco_last_updated": sacco_last_updated,
    })


@router.get("/admin/data/sacco", response_class=HTMLResponse)
async def admin_data_sacco(
    request: Request,
    q: str = None,
    county: str = None,
    page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    admin = await _current_user(request, db)
    if not admin or not _is_admin(admin):
        return RedirectResponse("/login", status_code=302)

    records = store.SACCOS
    if q:
        q_l = q.lower()
        records = [r for r in records if q_l in r.get("name", "").lower()]
    if county:
        c_l = county.lower()
        records = [r for r in records if c_l in r.get("county", "").lower()]

    page_size = 50
    total = len(records)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    page_records = records[(page - 1) * page_size : page * page_size]

    counties = sorted(set(r.get("county", "") for r in store.SACCOS if r.get("county")))

    # Use JSON file mtime as last-updated proxy for SACCOs (no last_scraped in data)
    import os as _os
    _sacco_json = Path(__file__).parent.parent.parent / "saccos_2026.json"
    try:
        _mtime = _os.path.getmtime(_sacco_json)
        from datetime import datetime as _dt
        last_updated = _dt.utcfromtimestamp(_mtime).strftime("%d %b %Y")
    except Exception:
        last_updated = None

    return templates.TemplateResponse("admin_data_table.html", {
        "request":      request,
        "is_admin":     True,
        "title":        "SACCO Societies",
        "label":        "sacco",
        "columns":      ["name", "county", "schedule", "business_type"],
        "records":      page_records,
        "total":        total,
        "page":         page,
        "pages":        pages,
        "q":            q or "",
        "county":       county or "",
        "counties":     counties,
        "back_url":     "/admin/sources",
        "last_updated": last_updated,
    })


@router.get("/admin/data/cbk/{entity_type}", response_class=HTMLResponse)
async def admin_data_cbk(
    request: Request,
    entity_type: str,
    q: str = None,
    county: str = None,
    status: str = None,
    page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    admin = await _current_user(request, db)
    if not admin or not _is_admin(admin):
        return RedirectResponse("/login", status_code=302)

    if entity_type not in CBK_CATEGORIES:
        raise HTTPException(status_code=404, detail="Unknown entity type")

    records = [e for e in store.CBK_ENTITIES if e["category"] == entity_type]
    if q:
        q_l = q.lower()
        records = [r for r in records if q_l in r.get("entity_name", "").lower()]
    if county:
        c_l = county.lower()
        records = [r for r in records if c_l in r.get("county", "").lower()]
    if status:
        records = [r for r in records if r.get("status") == status.lower()]

    page_size = 50
    total = len(records)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    page_records = records[(page - 1) * page_size : page * page_size]

    pool = [e for e in store.CBK_ENTITIES if e["category"] == entity_type]
    counties  = sorted(set(r.get("county", "") for r in pool if r.get("county")))
    statuses  = sorted(set(r.get("status", "") for r in pool if r.get("status")))

    # doc_date is the regulator's own "as of" date printed on the PDF
    doc_dates = [r.get("doc_date", "") for r in pool if r.get("doc_date")]
    last_updated = None
    if doc_dates:
        try:
            from datetime import datetime as _dt
            last_updated = _dt.fromisoformat(max(doc_dates)).strftime("%d %b %Y")
        except Exception:
            last_updated = max(doc_dates)

    return templates.TemplateResponse("admin_data_table.html", {
        "request":     request,
        "is_admin":    True,
        "title":       CBK_CATEGORIES[entity_type],
        "label":       entity_type,
        "columns":     ["entity_name", "license_number", "county", "status", "sub_category", "licensed_since", "license_expiry"],
        "records":     page_records,
        "total":       total,
        "page":        page,
        "pages":       pages,
        "q":           q or "",
        "county":      county or "",
        "status":      status or "",
        "counties":    counties,
        "statuses":    statuses,
        "back_url":    "/admin/sources",
        "last_updated": last_updated,
    })


@router.post("/admin/upload/sacco")
async def upload_sacco(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    admin = await _current_user(request, db)
    if not admin or not _is_admin(admin):
        return RedirectResponse("/login", status_code=302)

    if not file.filename.lower().endswith(".pdf"):
        return RedirectResponse("/admin/sources?error=Please+upload+a+PDF+file", status_code=302)

    # Save to temp file then parse in thread pool (pdfplumber is sync)
    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        from scrape_sasra import scrape_from_file
        loop    = asyncio.get_event_loop()
        records = await loop.run_in_executor(None, scrape_from_file, tmp_path)
    except Exception as e:
        return RedirectResponse(f"/admin/sources?error=Parse+error:+{str(e)[:80]}", status_code=302)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if not records:
        return RedirectResponse("/admin/sources?error=No+records+extracted+from+PDF", status_code=302)

    # Reload in-memory store
    store.reload_saccos(records)

    # Persist JSON (local / Railway ephemeral)
    json_path = Path(__file__).parent.parent.parent / "saccos_2026.json"
    with open(json_path, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    # Sync to Supabase
    status, db_msg = _supabase_upsert("saccos", records, "name,schedule")

    msg = f"SACCO: {len(records)} records loaded into memory. {db_msg}"
    return RedirectResponse(f"/admin/sources?msg={msg}", status_code=302)


@router.post("/admin/upload/cbk/{entity_type}")
async def upload_cbk(
    entity_type: str,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    admin = await _current_user(request, db)
    if not admin or not _is_admin(admin):
        return RedirectResponse("/login", status_code=302)

    if entity_type not in CBK_CATEGORIES:
        return RedirectResponse("/admin/sources?error=Unknown+entity+type", status_code=302)

    if not file.filename.lower().endswith(".pdf"):
        return RedirectResponse("/admin/sources?error=Please+upload+a+PDF+file", status_code=302)

    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        from scrape_cbk import scrape_entity
        loop    = asyncio.get_event_loop()
        records = await loop.run_in_executor(None, scrape_entity, Path(tmp_path), entity_type)
    except Exception as e:
        return RedirectResponse(f"/admin/sources?error=Parse+error:+{str(e)[:80]}", status_code=302)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if not records:
        return RedirectResponse(
            f"/admin/sources?error=No+records+extracted+from+PDF+(entity_type={entity_type})",
            status_code=302,
        )

    # Hot-reload only this category in memory
    store.reload_cbk_category(entity_type, records)

    # Persist full JSON
    json_path = Path(__file__).parent.parent.parent / "cbk_entities.json"
    with open(json_path, "w") as f:
        json.dump(store.CBK_ENTITIES, f, ensure_ascii=False, indent=2)

    # Sync to Supabase
    status, db_msg = _supabase_upsert("cbk_entities", records, "license_number")

    label = CBK_CATEGORIES[entity_type]
    msg   = f"{label}: {len(records)} records loaded into memory. {db_msg}"
    return RedirectResponse(f"/admin/sources?msg={msg}", status_code=302)
