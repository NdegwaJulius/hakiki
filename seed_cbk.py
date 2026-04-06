#!/usr/bin/env python3
"""
Seed CBK entities into Supabase via REST API — no direct DB connection needed.
Reads SUPABASE_URL and SUPABASE_SERVICE_KEY from .env

Usage:
    python seed_cbk.py                        # load all from cbk_entities.json
    python seed_cbk.py --file my_output.json  # custom JSON path
"""
import argparse
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

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY  = os.getenv("SUPABASE_SERVICE_KEY", "")
BATCH_SIZE   = 100

CATEGORIES = {
    "dcp":        "Digital Credit Providers",
    "psp":        "Payment Service Providers",
    "forex":      "Forex Bureaus",
    "mfb":        "Microfinance Banks",
    "remittance": "Money Remittance Providers",
}


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
    parser = argparse.ArgumentParser(description="Seed CBK entities into Supabase")
    parser.add_argument(
        "--file", default="cbk_entities.json",
        help="Path to the JSON file produced by scrape_cbk.py (default: cbk_entities.json)",
    )
    parser.add_argument(
        "--entity", choices=list(CATEGORIES), default=None,
        help="Load only one entity type (default: all)",
    )
    args = parser.parse_args()

    if not SUPABASE_URL or not SERVICE_KEY:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in .env")

    json_path = Path(args.file)
    if not json_path.exists():
        raise SystemExit(
            f"File not found: {json_path}\n"
            "Run: python scrape_cbk.py  first to generate it."
        )

    with open(json_path) as f:
        all_records = json.load(f)

    if args.entity:
        all_records = [r for r in all_records if r["category"] == args.entity]

    counts = Counter(r["category"] for r in all_records)
    print(f"Loaded {len(all_records)} CBK entity records")
    for et, label in CATEGORIES.items():
        print(f"  {label:35s}: {counts.get(et, 0)}")

    # ── Upsert cbk_entities in batches ────────────────────────────────────────
    # services_permitted is stored as JSONB — Supabase accepts a Python list
    for i in range(0, len(all_records), BATCH_SIZE):
        batch = all_records[i : i + BATCH_SIZE]
        status, body = rest_post("cbk_entities", batch, on_conflict="license_number")
        if status not in (200, 201):
            raise SystemExit(
                f"Batch {i}–{i + len(batch)} failed ({status}): {body[:300].decode()}"
            )
        print(f"  cbk_entities {min(i + BATCH_SIZE, len(all_records))}/{len(all_records)}")

    # ── Scrape history ─────────────────────────────────────────────────────────
    category_counts = dict(counts)
    status, _ = rest_post(
        "scrape_history",
        {
            "file_name":      json_path.name,
            "records_count":  len(all_records),
            "schedule_counts": category_counts,   # reuse JSONB column
        },
    )
    print(f"Scrape history inserted ({status})")

    print(f"\nDone. {len(all_records)} CBK entities loaded into Supabase.")


if __name__ == "__main__":
    main()
