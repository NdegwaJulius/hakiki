#!/usr/bin/env python3
"""
CBK entity PDF scraper — Kenya
Parses CBK-published PDF documents for all 5 licensed-entity categories.

Two PDF layouts are handled:

  Format A — DCP, PSP, MFB
    2-3 column table.  Col 0 = serial number.  Col 1 = multi-line text blob:
        Entity Name
        Postal Address: P.O. Box ...
        Telephone: ...
        Email: ...
        Physical Address: ...
        Date Licensed: ...          (DCP / MFB)
        Date First Licensed: ...    (PSP)
    PSP additionally has Col 2 = "Approved Services" text.

  Format B — Forex, Remittance
    5-column table.  Row 0 = merged title (skip).  Row 1 = real header.
    Cols:  serial | Name | Location | Contact Details | Date of Licencing

Usage:
    python scrape_cbk.py                        # all five entity types
    python scrape_cbk.py --entity dcp
    python scrape_cbk.py --pdf-dir /Downloads/cbk_lists
    python scrape_cbk.py --output my_output.json
"""

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

OUTPUT_PATH = "cbk_entities.json"
NOW_ISO = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

CATEGORIES: dict[str, str] = {
    "dcp":        "Digital Credit Providers",
    "psp":        "Payment Service Providers",
    "forex":      "Forex Bureaus",
    "mfb":        "Microfinance Banks",
    "remittance": "Money Remittance Providers",
}

# Format assignment
FORMAT_A = {"dcp", "psp", "mfb"}   # multi-line blob per row
FORMAT_B = {"forex", "remittance"} # columnar table

EXPECTED_MIN: dict[str, int] = {
    "dcp":        180,
    "psp":        10,
    "mfb":        13,
    "forex":      80,
    "remittance": 25,
}

DEFAULT_SERVICES: dict[str, list[str]] = {
    "dcp":        ["digital_credit"],
    "psp":        ["payment_services"],
    "forex":      ["foreign_exchange"],
    "mfb":        ["microfinance", "deposit_taking", "lending"],
    "remittance": ["money_remittance", "international_transfers"],
}

DEFAULT_PDF_DIR = Path("data/cbk_pdfs")
PDF_PATHS: dict[str, Path] = {k: DEFAULT_PDF_DIR / f"cbk_{k}.pdf" for k in CATEGORIES}

KENYA_COUNTIES: set[str] = {
    "Nairobi", "Mombasa", "Kisumu", "Nakuru", "Eldoret",
    "Nyeri", "Meru", "Embu", "Machakos", "Kitui",
    "Kakamega", "Bungoma", "Kisii", "Migori", "Homa Bay",
    "Siaya", "Vihiga", "Trans Nzoia", "Uasin Gishu",
    "Elgeyo Marakwet", "Nandi", "Kericho", "Bomet",
    "Baringo", "Laikipia", "Samburu", "Isiolo", "Marsabit",
    "Mandera", "Wajir", "Garissa", "Tana River", "Kilifi",
    "Kwale", "Taita Taveta", "Makueni", "Kajiado", "Narok",
    "Kirinyaga", "Murang'a", "Kiambu", "Nyandarua",
    "Tharaka Nithi", "Tharaka-Nithi",
}


# ── Text helpers ──────────────────────────────────────────────────────────────

def clean(s) -> str:
    if not s:
        return ""
    s = str(s)
    s = re.sub(r"-\s+", "-", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def county_from_text(text: str) -> str:
    for county in KENYA_COUNTIES:
        if re.search(r"\b" + re.escape(county) + r"\b", text, re.I):
            return county
    return ""


def town_from_location(location: str) -> str:
    """
    CBK location strings: 'Shujaa Mall, Kilimani, Ring Road, Nairobi'
    Walk comma-segments in reverse; return first non-county, non-PO-Box segment.
    """
    parts = [p.strip() for p in location.split(",") if p.strip()]
    for part in reversed(parts):
        if re.match(r"^P\.?O", part, re.I):
            continue
        if county_from_text(part):
            continue
        if re.match(r"^\d", part):
            continue
        if len(part) > 2:
            return clean(part)
    return ""


def parse_date(raw: str) -> str:
    """Return YYYY-MM-DD or '' if unparseable."""
    raw = clean(raw)
    if not raw or raw in ("-", "N/A", "n/a", "NIL"):
        return ""
    for fmt in (
        "%B %d, %Y",    # September 2, 2025
        "%d/%m/%Y",     # 02.06.2015 — handled separately
        "%d.%m.%Y",     # 02.06.2015
        "%Y-%m-%d",
        "%d %B %Y",
        "%d %b %Y",
        "%b %d, %Y",
    ):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.search(r"\b(19|20)\d{2}\b", raw)
    if m:
        return f"{m.group()}-01-01"
    return ""


def make_licence_number(category: str, name: str, serial: str = "") -> str:
    """Auto-generate a stable licence number from category + name."""
    slug = re.sub(r"[^A-Z0-9]", "", name.upper())[:12]
    prefix = category.upper()
    return f"CBK/{prefix}/{slug}"


# ── Format A parser — DCP, PSP, MFB ──────────────────────────────────────────
#
# Each data cell looks like:
#   Anjoy Credit Limited
#   Postal Address: P.O. Box 40583 – 00100, Nairobi
#   Telephone: +254769984360
#   Email: info@anjoycredit.com
#   Physical Address: Transnational House 1st Floor, Mama Ngina Street, Nairobi
#   Date Licensed: March 27, 2023

_BLOB_FIELDS = {
    "postal address":      "postal",
    "telephone":           "telephone",
    "tel":                 "telephone",
    "email":               "email",
    "website":             "website",
    "physical address":    "physical",
    "date licensed":       "since",
    "date licenced":       "since",
    "date first licensed": "since",
    "branches":            "branches",
    # ignore these
    "chief executive officer": None,
    "managing director":       None,
    "chairman":                None,
    "ceo":                     None,
}


def _parse_blob(text: str) -> dict:
    """
    Parse the multi-line text blob from a Format A cell.
    Returns dict with keys: name, postal, physical, since, telephone, email, website.
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return {}

    out: dict[str, str] = {
        "name": lines[0],
        "postal": "", "physical": "", "since": "",
        "telephone": "", "email": "", "website": "",
    }
    current_key: Optional[str] = None

    for line in lines[1:]:
        # Check if this line starts a known field
        colon_pos = line.find(":")
        if colon_pos != -1:
            candidate = line[:colon_pos].lower().strip()
            value     = line[colon_pos + 1:].strip()
            matched   = False
            for prefix, field in _BLOB_FIELDS.items():
                if candidate == prefix or candidate.startswith(prefix):
                    current_key = field  # None means "ignore"
                    if field and value:
                        out[field] = value
                    matched = True
                    break
            if matched:
                continue

        # Continuation of previous field
        if current_key and current_key in out:
            out[current_key] = (out[current_key] + " " + line).strip()

    return out


def _services_from_blob(text: str) -> list[str]:
    """Parse the 'Approved Services' column (PSP only) into a list."""
    if not text or not text.strip():
        return []
    # Split on sentence ends or "To " beginnings
    items = re.split(r"(?<=[.!?])\s+(?=To )|(?<=\n)", text)
    cleaned = [clean(s) for s in items if clean(s)]
    return cleaned if cleaned else [clean(text)]


def scrape_format_a(pdf_path: Path, category: str) -> list[dict]:
    """Extract records from a Format A PDF (DCP, PSP, MFB)."""
    import pdfplumber  # type: ignore

    records: list[dict] = []
    source_doc = str(pdf_path)
    is_psp = (category == "psp")

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                continue
            for table in tables:
                for row in table:
                    if not row or len(row) < 2:
                        continue

                    serial  = clean(str(row[0] or ""))
                    blob    = str(row[1] or "")          # keep newlines for _parse_blob
                    svc_raw = clean(str(row[2] or "")) if (is_psp and len(row) > 2) else ""

                    # Skip title rows and header rows
                    if not serial or not re.match(r"^\d+\.?$", serial):
                        continue
                    if not blob or "CENTRAL BANK" in blob.upper():
                        continue

                    parsed = _parse_blob(blob)
                    if not parsed.get("name") or len(parsed["name"]) < 3:
                        continue

                    name     = clean(parsed["name"])
                    physical = clean(parsed.get("physical", ""))
                    since    = parse_date(parsed.get("since", ""))
                    # Search physical first, then full blob (postal addr often has county)
                    county   = county_from_text(physical) or county_from_text(blob)
                    town     = town_from_location(physical)

                    services = (
                        _services_from_blob(svc_raw)
                        if svc_raw
                        else DEFAULT_SERVICES[category]
                    )

                    records.append({
                        "license_number":     make_licence_number(category, name),
                        "entity_name":        name,
                        "category":           category,
                        "sub_category":       "",
                        "status":             "licensed",
                        "county":             county,
                        "town":               town,
                        "licensed_since":     since,
                        "license_expiry":     "",
                        "services_permitted": services,
                        "source_doc":         source_doc,
                        "last_scraped":       NOW_ISO,
                    })

    return records


# ── Format B parser — Forex, Remittance ──────────────────────────────────────
#
# Row 0: merged title — skip
# Row 1: ['', 'Name of Bureau', 'Location', 'Contact Details', 'Date of\nLicencing']
# Row 2+: data
#   [serial, name, location, contact_details, date_licencing]

def scrape_format_b(pdf_path: Path, category: str) -> list[dict]:
    """Extract records from a Format B PDF (Forex, Remittance)."""
    import pdfplumber  # type: ignore

    records: list[dict] = []
    source_doc = str(pdf_path)

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                continue
            for table in tables:
                for row in table:
                    if not row or len(row) < 3:
                        continue

                    serial   = clean(str(row[0] or ""))
                    name     = clean(str(row[1] or ""))
                    location = clean(str(row[2] or ""))
                    # row[3] = contact details (P.O. Box, tel, email) — not stored
                    date_raw = clean(str(row[4] or "")) if len(row) > 4 else ""

                    # Skip title, header, and empty rows
                    if not name or len(name) < 3:
                        continue
                    if "CENTRAL BANK" in name.upper():
                        continue
                    if name.lower() in ("name of bureau", "name of mrp",
                                        "institution", "name"):
                        continue
                    if not re.search(r"[A-Za-z]{3,}", name):
                        continue

                    county = county_from_text(location)
                    town   = town_from_location(location)
                    since  = parse_date(date_raw)

                    records.append({
                        "license_number":     make_licence_number(category, name),
                        "entity_name":        name,
                        "category":           category,
                        "sub_category":       "",
                        "status":             "licensed",
                        "county":             county,
                        "town":               town,
                        "licensed_since":     since,
                        "license_expiry":     "",
                        "services_permitted": DEFAULT_SERVICES[category],
                        "source_doc":         source_doc,
                        "last_scraped":       NOW_ISO,
                    })

    return records


# ── Per-entity orchestrator ───────────────────────────────────────────────────

def scrape_entity(pdf_path: Path, category: str) -> list[dict]:
    if not pdf_path.exists():
        print(f"  ⚠  PDF not found: {pdf_path}  (skipping {CATEGORIES[category]})")
        return []

    print(f"\n[{category}] {CATEGORIES[category]}")
    print(f"  PDF : {pdf_path}")

    try:
        if category in FORMAT_A:
            records = scrape_format_a(pdf_path, category)
        else:
            records = scrape_format_b(pdf_path, category)
    except ImportError:
        print("  pdfplumber not installed — run: pip install pdfplumber")
        return []
    except Exception as e:
        print(f"  Error: {e}")
        return []

    # Deduplicate by licence number
    seen: set[str] = set()
    deduped = []
    for r in records:
        key = r["license_number"]
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    if len(deduped) < len(records):
        print(f"  Deduplicated: {len(records)} → {len(deduped)}")

    print(f"  Extracted: {len(deduped)} records")
    return deduped


# ── Anomaly detection ─────────────────────────────────────────────────────────

def detect_anomalies(records: list[dict]) -> int:
    print("\n=== ANOMALY DETECTION ===")
    anomalies = 0
    for r in records:
        if len(r["entity_name"]) < 3:
            print(f"  ⚠ Short name: category={r['category']} name='{r['entity_name']}'")
            anomalies += 1
        if not r["license_number"]:
            print(f"  ⚠ Missing licence number: name='{r['entity_name']}'")
            anomalies += 1
        if not r["county"]:
            print(f"  ⚠ Null county: category={r['category']} name='{r['entity_name']}'")
            anomalies += 1
    if anomalies == 0:
        print("  No anomalies detected.")
    return anomalies


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scrape CBK entity lists from PDF documents"
    )
    parser.add_argument(
        "--entity", choices=list(CATEGORIES), default=None,
        help="Scrape a single entity type (default: all five)",
    )
    parser.add_argument(
        "--pdf-dir", default=None,
        help=f"Directory containing CBK PDFs (default: {DEFAULT_PDF_DIR})",
    )
    parser.add_argument(
        "--output", default=OUTPUT_PATH,
        help=f"Output JSON file (default: {OUTPUT_PATH})",
    )
    args = parser.parse_args()

    if args.pdf_dir:
        pdf_dir = Path(args.pdf_dir)
        for k in PDF_PATHS:
            PDF_PATHS[k] = pdf_dir / f"cbk_{k}.pdf"

    entity_types = [args.entity] if args.entity else list(CATEGORIES)

    all_records: list[dict] = []
    for et in entity_types:
        recs = scrape_entity(PDF_PATHS[et], et)
        all_records.extend(recs)

    # ── Count validation ──────────────────────────────────────────────────────
    counts = Counter(r["category"] for r in all_records)
    print("\n=== COUNT VALIDATION ===")
    for et, label in CATEGORIES.items():
        if et not in entity_types:
            continue
        actual  = counts.get(et, 0)
        minimum = EXPECTED_MIN[et]
        status  = "✓" if actual >= minimum else f"⚠  got {actual}, expected ≥ {minimum}"
        print(f"  {label:35s}: {actual:4d}  {status}")
    print(f"  {'TOTAL':35s}: {len(all_records)}")

    # ── Anomaly detection ─────────────────────────────────────────────────────
    detect_anomalies(all_records)

    # ── Preview first 3 records per category ─────────────────────────────────
    print("\n=== PREVIEW: first 3 records per category ===")
    by_cat: dict[str, list[dict]] = {}
    for r in all_records:
        by_cat.setdefault(r["category"], []).append(r)

    for et, label in CATEGORIES.items():
        if et not in entity_types:
            continue
        recs = by_cat.get(et, [])
        print(f"\n--- {label} ({et}) ---")
        for rec in recs[:3]:
            print(f"  license_number : {rec['license_number']}")
            print(f"  entity_name    : {rec['entity_name']}")
            print(f"  county / town  : {rec['county']} / {rec['town']}")
            print(f"  licensed_since : {rec['licensed_since']}")
            print()

    # ── Write output ──────────────────────────────────────────────────────────
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    print(f"Output written to {args.output}  ({len(all_records)} records)")


if __name__ == "__main__":
    main()
