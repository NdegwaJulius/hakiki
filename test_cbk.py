#!/usr/bin/env python3
"""
CBK endpoint smoke tests — runs against a live local server.

Usage:
    # 1. Start the server first:
    #    uvicorn app.main:app --reload

    # 2. In a second terminal:
    python test_cbk.py

    # Custom host / API key:
    python test_cbk.py --base-url http://localhost:8000 --api-key <your-api-key>
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE  = "http://localhost:8000"
DEFAULT_KEY   = os.getenv("DEV_API_KEY", "")
ENTITY_TYPES  = ["dcp", "psp", "forex", "mfb", "remittance"]


def _load_known() -> dict:
    """Pick the first real record per category from cbk_entities.json."""
    path = Path("cbk_entities.json")
    if not path.exists():
        return {}
    data = json.load(open(path))
    known = {}
    for cat in ENTITY_TYPES:
        rec = next((r for r in data if r["category"] == cat), None)
        if rec:
            known[cat] = {
                "license":       rec["license_number"],
                "name_fragment": rec["entity_name"].split()[0],
            }
    return known

_pass = 0
_fail = 0


def _req(url: str, api_key: str) -> tuple[int, dict]:
    req = urllib.request.Request(url)
    req.add_header("X-API-Key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except Exception as e:
        return 0, {"error": str(e)}


def check(label: str, condition: bool, detail: str = "") -> None:
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  ✓  {label}")
    else:
        _fail += 1
        print(f"  ✗  {label}" + (f"  ({detail})" if detail else ""))


def section(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


def run(base: str, key: str, known: dict) -> None:
    # ── /cbk/v1/health ────────────────────────────────────────────────────────
    section("GET /cbk/v1/health  (no auth required)")
    status, body = _req(f"{base}/cbk/v1/health", key)
    check("HTTP 200",            status == 200, str(status))
    check("status == ok",        body.get("status") == "ok")
    check("total > 0",           body.get("total", 0) > 0, str(body.get("total")))
    check("by_category has dcp", "dcp" in body.get("by_category", {}))
    print(f"     total={body.get('total')}  categories={list(body.get('by_category', {}).keys())}")

    for et in ENTITY_TYPES:
        known_lic  = known[et]["license"]
        known_frag = known[et]["name_fragment"]

        # ── verify hit ────────────────────────────────────────────────────────
        section(f"GET /cbk/v1/{et}/verify/{known_lic}")
        status, body = _req(f"{base}/cbk/v1/{et}/verify/{known_lic}", key)
        check("HTTP 200",          status == 200, str(status))
        check("is_licensed=true",  body.get("is_licensed") is True)
        check("count >= 1",        body.get("count", 0) >= 1)
        check("entity_type field", body.get("entity_type") == et)

        # ── verify miss ───────────────────────────────────────────────────────
        section(f"GET /cbk/v1/{et}/verify/BOGUS-999  (expect miss)")
        status, body = _req(f"{base}/cbk/v1/{et}/verify/BOGUS-999", key)
        check("HTTP 200",           status == 200, str(status))
        check("is_licensed=false",  body.get("is_licensed") is False)
        check("count == 0",         body.get("count") == 0)

        # ── search by name ────────────────────────────────────────────────────
        section(f"GET /cbk/v1/{et}/search?q={known_frag}")
        status, body = _req(f"{base}/cbk/v1/{et}/search?q={known_frag}", key)
        check("HTTP 200",        status == 200, str(status))
        check("results list",    isinstance(body.get("results"), list))
        check("at least 1 hit",  body.get("total", 0) >= 1)

        # ── search by county ──────────────────────────────────────────────────
        section(f"GET /cbk/v1/{et}/search?county=Nairobi")
        status, body = _req(f"{base}/cbk/v1/{et}/search?county=Nairobi", key)
        check("HTTP 200",      status == 200, str(status))
        check("results list",  isinstance(body.get("results"), list))

        # ── list (enterprise key should work) ─────────────────────────────────
        section(f"GET /cbk/v1/{et}/list")
        status, body = _req(f"{base}/cbk/v1/{et}/list", key)
        check("HTTP 200",       status == 200, str(status))
        check("total > 0",      body.get("total", 0) > 0)
        check("results list",   isinstance(body.get("results"), list))
        check("entity_type",    body.get("entity_type") == et)

    # ── invalid entity type → 422 ─────────────────────────────────────────────
    section("GET /cbk/v1/INVALID/verify/X  (expect 422)")
    status, body = _req(f"{base}/cbk/v1/INVALID/verify/X", key)
    check("HTTP 422",          status == 422, str(status))
    check("error field",       "valid" in str(body))

    # ── missing API key → 422 ─────────────────────────────────────────────────
    section("GET /cbk/v1/dcp/verify/X  (no API key → 422)")
    req = urllib.request.Request(f"{base}/cbk/v1/dcp/verify/X")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            check("should have failed", False, "got 200")
    except urllib.error.HTTPError as e:
        check("HTTP 422", e.code == 422, str(e.code))

    # ── wrong API key → 401 ───────────────────────────────────────────────────
    section("GET /cbk/v1/dcp/verify/X  (bad API key → 401)")
    status, body = _req(f"{base}/cbk/v1/dcp/verify/X", "bad_key")
    check("HTTP 401", status == 401, str(status))

    # ── Summary ───────────────────────────────────────────────────────────────
    total = _pass + _fail
    print(f"\n{'═' * 55}")
    print(f"  Results: {_pass}/{total} passed  {'✓ ALL PASS' if _fail == 0 else f'✗ {_fail} FAILED'}")
    print(f"{'═' * 55}\n")

    sys.exit(0 if _fail == 0 else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--api-key",  default=DEFAULT_KEY)
    args = parser.parse_args()

    known = _load_known()
    if not known:
        print("cbk_entities.json not found — run: python scrape_cbk.py  first")
        sys.exit(1)

    print(f"\nTesting CBK endpoints at {args.base_url}")
    print(f"API key : {args.api_key[:12]}...")
    run(args.base_url, args.api_key, known)
