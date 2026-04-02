#!/usr/bin/env python3
"""
Seed Supabase via REST API — no direct DB connection needed.
Reads SUPABASE_URL and SUPABASE_SERVICE_KEY from .env
"""
import hashlib
import json
import os
import ssl
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

# Fix missing CA certs on macOS Python 3.12 installs
ssl._create_default_https_context = ssl._create_unverified_context

from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL      = os.getenv("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY       = os.getenv("SUPABASE_SERVICE_KEY", "")
JSON_PATH         = Path(__file__).parent / "saccos_2026.json"
DEV_API_KEY       = "hk_dev_testkey_localonly"
DEV_EMAIL         = "dev@hakiki.local"
BATCH_SIZE        = 100


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def rest_post(table: str, payload, on_conflict: str = "") -> tuple[int, bytes]:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("apikey", SERVICE_KEY)
    req.add_header("Authorization", f"Bearer {SERVICE_KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=minimal,resolution=merge-duplicates")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    if not SUPABASE_URL or not SERVICE_KEY:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in .env")

    with open(JSON_PATH) as f:
        records = json.load(f)
    print(f"Loaded {len(records)} SACCO records")

    # ── Upsert SACCOs in batches ──────────────────────────────────────────────
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        status, body = rest_post("saccos", batch, on_conflict="name,schedule")
        if status not in (200, 201):
            raise SystemExit(f"Batch {i} failed ({status}): {body[:300].decode()}")
        print(f"  saccos {min(i + BATCH_SIZE, len(records))}/{len(records)}")

    # ── Dev API user ──────────────────────────────────────────────────────────
    status, body = rest_post(
        "api_users",
        {"email": DEV_EMAIL, "api_key_hash": _hash(DEV_API_KEY),
         "tier": "enterprise", "credits": 9999},
        on_conflict="email",
    )
    if status not in (200, 201):
        raise SystemExit(f"api_users failed ({status}): {body[:300].decode()}")
    print(f"Dev user upserted")

    # ── Scrape history ────────────────────────────────────────────────────────
    schedule_counts = dict(Counter(r["schedule"] for r in records))
    status, _ = rest_post(
        "scrape_history",
        {"file_name": "saccos_2026.json", "records_count": len(records),
         "schedule_counts": schedule_counts},
    )
    print(f"Scrape history inserted ({status})")

    print(f"\nDone. Dev API key: {DEV_API_KEY}")


if __name__ == "__main__":
    main()
