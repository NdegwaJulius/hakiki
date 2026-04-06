#!/usr/bin/env python3
"""
SASRA SACCO PDF scraper - Kenya 2026
Uses word-position-based parsing to extract 5 schedules from a two-column PDF.
"""
import json
import re
import pdfplumber

PDF_PATH = (
    "/Users/jm/Downloads/SASRA--LIST OF LICENSED AND AUTHORISED SACCO SOCIETIES "
    "IN KENYA FOR THE FINANCIAL YEAR ENDING 31ST DECEMBER 2026.pdf"
)
OUTPUT_PATH = "saccos_2026.json"

EXPECTED = {"I": 176, "II": 176, "III": 5, "IV": 1, "V": 1}
BIZ_TYPE = {
    "I": "deposit_taking",
    "II": "non_deposit_taking",
    "III": "restricted",
    "IV": "revoked",
    "V": "revoked",
}

# ─── Column x-boundaries (verified from PDF word positions) ──────────────────
# Left column: header x-positions vary per page (page4 is ~2-4pt left of others).
#   Names  ≈26.95–31.4, Postal ≈119.35–123.8, Physical ≈201.45–205.89, County ≈337.47–341.91
#   Row nums sit at x≈9–13.5 on all pages.
L_COLS = [
    ("num",      (0,   26)),    # row nums at x≈9-13.5
    ("name",     (26,  118)),   # names from x≈26.95
    ("postal",   (118, 199)),   # postal from x≈119.35
    ("physical", (199, 335)),   # physical from x≈201.45
    ("county",   (335, 390)),   # county from x≈337.47
]
# Right column sub-columns (standard — Schedules I, II, III)
# Boundaries derived from column-header x-positions measured per page:
#   Names  ≈412-416, Postal ≈504-508, Physical ≈586-590, County ≈722-726
R_COLS = [
    ("num",      (390, 411)),   # row nums sit at x≈394-398
    ("name",     (411, 503)),   # names start at x≈412-416
    ("postal",   (503, 585)),   # postal "P.O" at x≈504-508
    ("physical", (585, 721)),   # physical at x≈586-720
    ("county",   (721, 800)),   # county at x≈722-726+
]
# Right column sub-columns for Schedules IV & V (different layout)
# "Particular details" col (411-504) holds name on row 1, postal on row 2.
# "Last Known Physical Location" col (504-585) — x starts at ≈505.95.
# "Reasons for Revocation" col (585+) — ignored.
R_REV_COLS = [
    ("num",      (390, 411)),
    ("name",     (411, 504)),   # name+postal (x≈413-490)
    ("physical", (504, 585)),   # last known physical (x≈505.95)
    ("reasons",  (585, 800)),   # ignored
]

Y_TOL = 4  # points of y-tolerance for grouping words into visual rows

# Words whose presence (≥2) marks a column-header row to be skipped
HEADER_WORDS = {
    "Names", "Postal", "Address", "Physical", "Location", "County",
    "Head", "Office", "Deposit", "Taking", "Non-Withdrawable",
    "Particular", "details", "Known", "Reasons", "Revocation",
}

# Words that signal the start of legal-notice boilerplate — stop processing when seen
STOP_WORDS = {"CAUTION"}  # stops at "FURTHER CAUTION AND NOTICE" legal boilerplate

KENYA_COUNTIES = {
    "Nairobi", "Mombasa", "Kisumu", "Nakuru", "Eldoret",
    "Nyeri", "Meru", "Embu", "Machakos", "Kitui",
    "Kakamega", "Bungoma", "Kisii", "Migori", "Homa Bay",
    "Siaya", "Vihiga", "Trans Nzoia", "Uasin Gishu",
    "Elgeyo Marakwet", "Nandi", "Kericho", "Bomet",
    "Baringo", "Laikipia", "Samburu", "Isiolo", "Marsabit",
    "Mandera", "Wajir", "Garissa", "Tana River", "Kilifi",
    "Kwale", "Taita Taveta", "Makueni", "Kajiado", "Narok",
    "Kirinyaga", "Murang'a", "Kiambu", "Nyandarua",
    "Tharaka Nithi", "Tharaka – Nithi", "Tharaka-Nithi",
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def clean(s: str) -> str:
    if not s:
        return ""
    # Repair hyphenated line-breaks like "Soci- ety" → "Society"
    s = re.sub(r'-\s+', '-', s)
    return re.sub(r'\s+', ' ', s).strip()


def group_rows(words, tol=Y_TOL):
    """Group words into visual rows by y proximity."""
    if not words:
        return []
    by_y = sorted(words, key=lambda w: w["top"])
    rows, cur, cur_y = [], [by_y[0]], by_y[0]["top"]
    for w in by_y[1:]:
        if w["top"] - cur_y <= tol:
            cur.append(w)
        else:
            rows.append(sorted(cur, key=lambda x: x["x0"]))
            cur, cur_y = [w], w["top"]
    rows.append(sorted(cur, key=lambda x: x["x0"]))
    return rows


def assign_subcol(x0, col_defs):
    for name, (lo, hi) in col_defs:
        if lo <= x0 < hi:
            return name
    return None


def row_to_subcols(row, col_defs):
    d = {}
    for w in row:
        sc = assign_subcol(w["x0"], col_defs)
        if sc:
            d[sc] = (d.get(sc, "") + " " + w["text"]).strip()
    return d


def detect_schedule_header(row):
    """Return roman numeral string if this row is a 'SCHEDULE X:' header, else None."""
    texts = [w["text"] for w in row]
    try:
        idx = texts.index("SCHEDULE")
    except ValueError:
        return None
    if idx + 1 < len(texts):
        m = re.match(r"^(I{1,3}V?|IV|V):$", texts[idx + 1])
        if m:
            return m.group(1)
    return None


def is_header_row(row):
    texts = {w["text"] for w in row}
    return len(texts & HEADER_WORDS) >= 2


def is_num_cell(s):
    return bool(re.match(r"^\d+\.?$", s.strip())) if s else False


def county_from_physical(physical):
    """Extract county name from the physical address text (for Schedules IV/V)."""
    for county in KENYA_COUNTIES:
        if county.lower() in physical.lower():
            return county
    return ""


# ─── Core extraction ─────────────────────────────────────────────────────────

def process_column(col_words, col_defs, schedule_changes):
    """
    Extract records from one column's words.

    schedule_changes: sorted list of (y_position, roman_numeral) tuples
                      marking where each schedule begins in this column.

    Returns list of (schedule_roman, record_dict).
    """
    rows = group_rows(col_words)

    def sched_at(y):
        result = None
        for sy, roman in schedule_changes:
            if y >= sy:
                result = roman
        return result

    records = []
    cur = None
    cur_sched = None

    for row in rows:
        row_y = min(w["top"] for w in row)
        sched = sched_at(row_y)
        if sched is None:
            continue  # before first schedule header → legal text or letterhead

        # Stop if we hit legal-notice boilerplate (e.g. "FURTHER CAUTION AND NOTICE")
        row_texts = {w["text"] for w in row}
        if row_texts & STOP_WORDS:
            if cur and cur_sched:
                records.append((cur_sched, cur))
                cur = None
            break

        # Detect schedule header rows (update current schedule)
        roman = detect_schedule_header(row)
        if roman:
            if cur and cur_sched:
                records.append((cur_sched, cur))
                cur = None
            cur_sched = roman
            continue

        if is_header_row(row):
            continue

        is_rev = sched in ("IV", "V")
        eff_cols = R_REV_COLS if (is_rev and col_defs is R_COLS) else col_defs

        sc = row_to_subcols(row, eff_cols)
        num_text = sc.get("num", "").strip()

        if is_num_cell(num_text):
            # Save previous record
            if cur and cur_sched:
                records.append((cur_sched, cur))
            cur_sched = sched

            if is_rev and col_defs is R_COLS:
                # IV/V: name col holds name; physical col holds physical start
                cur = {
                    "name": sc.get("name", ""),
                    "postal_address": "",
                    "physical_location": sc.get("physical", ""),
                    "county": "",
                    "_rev": True,
                    "_postal_captured": False,
                }
            else:
                cur = {
                    "name": sc.get("name", ""),
                    "postal_address": sc.get("postal", ""),
                    "physical_location": sc.get("physical", ""),
                    "county": sc.get("county", ""),
                    "_rev": False,
                    "_postal_captured": False,
                }

        elif cur is not None:
            if cur["_rev"] and col_defs is R_COLS:
                # For IV/V: "name" col row 2 = postal address
                name_cont = sc.get("name", "").strip()
                phys_cont = sc.get("physical", "").strip()

                if name_cont:
                    if not cur["_postal_captured"] and re.search(
                        r"P\.O|Box|Private", name_cont, re.I
                    ):
                        cur["postal_address"] = (
                            cur["postal_address"] + " " + name_cont
                        ).strip()
                        cur["_postal_captured"] = True
                    elif cur["_postal_captured"] and re.search(r"\d{3,6}", name_cont):
                        # Continuation of postal (box number or postal code overflow)
                        cur["postal_address"] = (
                            cur["postal_address"] + " " + name_cont
                        ).strip()
                    else:
                        cur["name"] = (cur["name"] + " " + name_cont).strip()

                if phys_cont:
                    cur["physical_location"] = (
                        cur["physical_location"] + " " + phys_cont
                    ).strip()
            else:
                # Standard continuation row
                for sc_key, rec_key in [
                    ("name",     "name"),
                    ("postal",   "postal_address"),
                    ("physical", "physical_location"),
                    ("county",   "county"),
                ]:
                    cont = sc.get(sc_key, "").strip()
                    if cont:
                        cur[rec_key] = (cur[rec_key] + " " + cont).strip()

    if cur and cur_sched:
        records.append((cur_sched, cur))

    return records


def find_schedule_changes(col_words):
    """
    Return sorted list of (y_position, roman_numeral) for each SCHEDULE X: header
    found in the given column's words.
    """
    changes = []
    for row in group_rows(col_words):
        roman = detect_schedule_header(row)
        if roman:
            y = min(w["top"] for w in row)
            changes.append((y, roman))
    return sorted(changes)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    all_records = []  # (schedule_roman, record_dict)

    with pdfplumber.open(PDF_PATH) as pdf:
        for page in pdf.pages:
            words = page.extract_words()

            left_words  = [w for w in words if w["x0"] <  390]
            right_words = [w for w in words if w["x0"] >= 390]

            left_changes  = find_schedule_changes(left_words)
            right_changes = find_schedule_changes(right_words)

            # Page 1 left column also has legal body text (y < ~450) that mentions
            # "SCHEDULE I herein…" without colons — detect_schedule_header already
            # filters those (requires trailing colon). The actual SCHEDULE I: header
            # in the left col of page 1 is at y≈427, so legal-text paragraph numbers
            # at y < 427 are naturally excluded by sched_at().

            all_records += process_column(left_words,  L_COLS, left_changes)
            all_records += process_column(right_words, R_COLS, right_changes)

    # ── Build final records ──────────────────────────────────────────────────
    results = []
    for roman, rec in all_records:
        name     = clean(rec.get("name", ""))
        postal   = clean(rec.get("postal_address", ""))
        physical = clean(rec.get("physical_location", ""))
        county   = clean(rec.get("county", ""))

        # For revoked schedules, extract county from physical
        if roman in ("IV", "V") and not county:
            county = county_from_physical(physical)

        # Skip obviously empty / artefact rows
        if len(name) < 3:
            continue

        results.append({
            "name":              name,
            "postal_address":    postal,
            "physical_location": physical,
            "county":            county,
            "schedule":          roman,
            "business_type":     BIZ_TYPE[roman],
            "is_visible":        True,
        })

    # ── Sort records within each schedule by their original number ───────────
    # (records come out in page/column order which equals numeric order)

    # ── Count validation ─────────────────────────────────────────────────────
    from collections import Counter
    counts = Counter(r["schedule"] for r in results)

    print("\n=== COUNT VALIDATION ===")
    for roman in ("I", "II", "III", "IV", "V"):
        actual   = counts.get(roman, 0)
        expected = EXPECTED[roman]
        status   = "✓" if actual == expected else f"⚠ WARNING: got {actual}, expected {expected}"
        print(f"  Schedule {roman:3s}: {actual:3d}  {status}")
    print(f"  {'TOTAL':6s}: {len(results)}")

    # ── Anomaly detection ────────────────────────────────────────────────────
    print("\n=== ANOMALY DETECTION ===")
    anomalies = 0
    for r in results:
        if len(r["name"]) < 5:
            print(f"  ⚠ Short name (likely split row): schedule={r['schedule']} name='{r['name']}'")
            anomalies += 1
        if not r["county"]:
            print(f"  ⚠ Null county: schedule={r['schedule']} name='{r['name']}'")
            anomalies += 1
        if not r["business_type"]:
            print(f"  ⚠ Missing business_type: schedule={r['schedule']} name='{r['name']}'")
            anomalies += 1
    if anomalies == 0:
        print("  No anomalies detected.")

    # ── Preview first 3 records per schedule ─────────────────────────────────
    print("\n=== PREVIEW: first 3 records per schedule ===")
    from itertools import groupby
    by_sched = {}
    for r in results:
        by_sched.setdefault(r["schedule"], []).append(r)

    for roman in ("I", "II", "III", "IV", "V"):
        recs = by_sched.get(roman, [])
        print(f"\n--- Schedule {roman} ({BIZ_TYPE[roman]}) ---")
        for rec in recs[:3]:
            print(f"  name:     {rec['name']}")
            print(f"  postal:   {rec['postal_address']}")
            print(f"  physical: {rec['physical_location']}")
            print(f"  county:   {rec['county']}")
            print()

    # ── Write output ─────────────────────────────────────────────────────────
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Output written to {OUTPUT_PATH}  ({len(results)} records)")


def scrape_from_file(pdf_path: str) -> list[dict]:
    """
    Importable entry point for the admin upload handler.
    Parses the given SASRA PDF and returns a list of SACCO records.
    Does not write any files or print summaries.
    """
    all_records = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            left_words  = [w for w in words if w["x0"] <  390]
            right_words = [w for w in words if w["x0"] >= 390]
            left_changes  = find_schedule_changes(left_words)
            right_changes = find_schedule_changes(right_words)
            all_records += process_column(left_words,  L_COLS, left_changes)
            all_records += process_column(right_words, R_COLS, right_changes)

    results = []
    for roman, rec in all_records:
        name     = clean(rec.get("name", ""))
        postal   = clean(rec.get("postal_address", ""))
        physical = clean(rec.get("physical_location", ""))
        county   = clean(rec.get("county", ""))
        if roman in ("IV", "V") and not county:
            county = county_from_physical(physical)
        if len(name) < 3:
            continue
        results.append({
            "name":              name,
            "postal_address":    postal,
            "physical_location": physical,
            "county":            county,
            "schedule":          roman,
            "business_type":     BIZ_TYPE[roman],
            "is_visible":        True,
        })

    return results


if __name__ == "__main__":
    main()
