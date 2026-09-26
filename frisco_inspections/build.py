"""Build analysis tables from the raw scrape and cross-check every record.

Inputs (data/raw/): listing.jsonl, inspections_parsed.jsonl,
permits_parsed.jsonl, food_types.json
Outputs (data/): inspections.csv, violations.csv, measurements.csv,
establishments.csv, items.csv, validation.json

Every check in validation.json compares two independent sources (listing API,
PDF page 1, PDF violation pages, the web page, the permit page); a non-zero
count names the records to review.
"""

import csv
import datetime as dt
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from client import inspection_url, pdf_url, permit_url

HERE = Path(__file__).parent
RAW = HERE / "data" / "raw"
OUT = HERE / "data"

# Texas DSHS Food Establishment Inspection Report (the form Frisco uses):
# items 1-20 are Priority items (3 demerits), 21-33 Priority Foundation (2),
# 34-47 Core (1). build() checks each item's weight against the PDFs and
# reports any item whose points disagree with its category.
CATEGORY_POINTS = {"Priority": 3, "Priority Foundation": 2, "Core": 1}


def item_category(n):
    if 1 <= n <= 20:
        return "Priority"
    if 21 <= n <= 33:
        return "Priority Foundation"
    if 34 <= n <= 47:
        return "Core"
    return "Unknown"


def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().upper()


def clean_city(s):
    s = (s or "").strip().rstrip("/").strip()
    return s.title() if s else ""


# Permit types as the portal labels them, grouped for analysis. "Food
# establishment" (tiers I-III by menu complexity, plus REST) is the closest
# thing to "restaurant"; it also includes coffee shops, bars, convenience
# stores and institutional kitchens, so filter on food_type too if needed.
PERMIT_CATEGORIES = {
    "Food Establishment I": "Food establishment",
    "Food Establishment II": "Food establishment",
    "Food Establishment III": "Food establishment",
    "REST": "Food establishment",
    "Grocery Store I": "Grocery",
    "Grocery Store II": "Grocery",
    "Grocery Store III": "Grocery",
    "School and City": "School or city facility",
    "DAYC": "Child care",
    "HLTD": "Health care / long-term care",
    "CONC": "Concession stand",
    "FOOD TRUCK 12": "Mobile",
    "MOBILEVNDR": "Mobile",
    "SWSEMI": "Pool permit (food inspection filed under it)",
}

# Closures are recorded only as free text. The general comment (PDF) and
# the listing comment are searched for these words; violation narratives,
# which often say things like "keep lids closed", are searched only for the
# unambiguous ones. Every flagged record should be read before publication.
CLOSURE_ANY = re.compile(
    r"\b(clos(e|ed|ure|ing)|cease[sd]?|suspen(d|ded|sion)|shut\s*down|re-?\s*open(ed|ing)?)\b", re.I)
CLOSURE_STRICT = re.compile(r"\b(closure|cease[sd]?|suspen(d|ded|sion)|shut\s*down)\b", re.I)


def closure_snippets(general, listing, violation_comments):
    out = []
    for label, text, rx in (
        [("general comment", general, CLOSURE_ANY), ("listing comment", listing, CLOSURE_ANY)]
        + [("violation", c, CLOSURE_STRICT) for c in violation_comments]
    ):
        for m in rx.finditer(text or ""):
            a, b = max(0, m.start() - 70), min(len(text), m.end() + 70)
            out.append(f"[{label}] ...{text[a:b].strip()}...")
            break
    return out


def time24(s):
    try:
        return dt.datetime.strptime((s or "").strip(), "%I:%M %p").strftime("%H:%M")
    except ValueError:
        return ""


def time_flags(tin, tout):
    """(duration in minutes or "", suspect?) for printed times in/out. Many
    reports have AM/PM entry errors (e.g. in 09:45 PM, out 10:30 AM)."""
    a, b = time24(tin), time24(tout)
    if not a or not b:
        return "", bool(a or b)
    mins = (int(b[:2]) * 60 + int(b[3:])) - (int(a[:2]) * 60 + int(a[3:]))
    suspect = mins <= 0 or mins > 360 or not ("05:00" <= a <= "21:00")
    return ("" if suspect else mins), suspect


NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def reading_number(item, value):
    """Numeric reading and where it came from. Inspectors sometimes type the
    reading into the Item field ('rice 150f') and leave Measurement blank."""
    m = NUM_RE.search(value or "")
    if m:
        return float(m.group()), "value"
    if not (value or "").strip():
        m = NUM_RE.search(item or "")
        if m:
            return float(m.group()), "item_text"
    return "", ""


def normalize_code(code):
    c = re.sub(r"\s+", "", code or "")
    c = re.sub(r"^(\*?\d)\.(\d{3}\.)", r"\1-\2", c)  # '4.101.15' -> '4-101.15'
    return c


def norm_name(s):
    return re.sub(r"\s+", " ", (s or "")).strip().upper()


def location_key(address, zip_code):
    """Street address + suite, normalized, so re-permitted businesses at the
    same spot can be grouped (permit IDs change when ownership changes)."""
    a = norm_name(address)
    a = re.sub(r"[.,#]", " ", a)
    a = re.sub(r"\b(SUITE|STE|UNIT)\b", " ", a)
    a = re.sub(r"\s+", " ", a).strip()
    return f"{a} | {(zip_code or '').strip()[:5]}"


def mdy_to_iso(s):
    try:
        return dt.datetime.strptime(s.strip(), "%m/%d/%Y").date().isoformat()
    except (AttributeError, ValueError):
        return ""


def read_jsonl(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="raise")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})


def build():
    parsed_path = RAW / "inspections_parsed.jsonl"
    if not parsed_path.exists():
        parsed_path = RAW / "inspections_parsed.jsonl.gz"
    recs = read_jsonl(parsed_path)
    permits = {p["permit_id"]: p for p in read_jsonl(RAW / "permits_parsed.jsonl")}
    food_types = json.loads((RAW / "food_types.json").read_text())

    checks = defaultdict(list)
    inspections, violations, measurements = [], [], []
    item_points = defaultdict(Counter)
    item_titles = defaultdict(Counter)

    for rec in recs:
        L = rec["listing"]
        iid = rec["inspection_id"]
        P = rec["pdf"]
        H = rec["html"]
        if P is None:
            checks["pdf_missing_or_unparsed"].append({"id": iid, "errors": rec["errors"][:1]})
            continue
        if rec["errors"]:
            checks["web_page_missing_or_unparsed"].append({"id": iid, "errors": rec["errors"][:1]})
        hdr = P["header"]
        found_via = L.get("_source", "listing")
        if found_via != "listing":
            checks["found_only_on_permit_page"].append({"id": iid, "permit": L["permitID"]})
        pts = {int(k): v for k, v in P["points_by_item"].items()}
        vio = P["violations"]
        date = L["inspectionDate"][:10]

        # --- cross-checks -------------------------------------------------
        if P["parse_warnings"]:
            checks["pdf_parse_warnings"].append({"id": iid, "warnings": P["parse_warnings"]})
        if str(L["score"]) != (hdr["score"] or ""):
            checks["score_listing_vs_pdf_header"].append({"id": iid, "listing": L["score"], "pdf": hdr["score"]})
        # Source quirk, not a parse error: the official score (listing and PDF
        # header agree) occasionally differs from the points printed on the
        # form, e.g. a consumer-advisory warning (item 26, OUT-W, printed 0)
        # still counted as 2 points. Reported per inspection.
        if sum(pts.values()) != L["score"]:
            checks["source_official_score_differs_from_printed_points"].append({
                "id": iid, "official": L["score"], "printed_points": sum(pts.values()),
                "warning_items": sorted({v["item_number"] for v in vio if v["status"] == "OUT-W"})})
        text_items = {v["item_number"] for v in vio}
        if set(pts) - text_items:
            # A point value with no violation block would mean a block was missed.
            checks["points_printed_for_item_without_violation_text"].append(
                {"id": iid, "items": sorted(set(pts) - text_items)})
        if mdy_to_iso(hdr["date"]) != date:
            checks["date_listing_vs_pdf"].append({"id": iid, "listing": date, "pdf": hdr["date"]})
        for v in vio:
            p = pts.get(v["item_number"])
            if v["status"] == "OUT-W" and p not in (0, None):
                # An item can carry both OUT and OUT-W entries; points then
                # come from the OUT entry.
                if not any(x["item_number"] == v["item_number"] and x["status"] == "OUT" for x in vio):
                    checks["source_warning_item_with_points"].append({"id": iid, "item": v["item_number"], "points": p})
            if v["status"] == "OUT" and not p:
                checks["source_out_item_without_points"].append({"id": iid, "item": v["item_number"], "points": p})
            if v["status"] not in ("OUT", "OUT-W"):
                checks["unexpected_status"].append({"id": iid, "item": v["item_number"], "status": v["status"]})
        if H is not None:
            if H["score"] is not None and H["score"] != str(L["score"]):
                checks["score_listing_vs_web_page"].append({"id": iid, "listing": L["score"], "web": H["score"]})
            web_items = sorted({int(b["item_number"]) for b in H["comment_blocks"] if b["item_number"].isdigit()})
            pdf_out_items = sorted({v["item_number"] for v in vio if v["status"] == "OUT"})
            if web_items != pdf_out_items:
                checks["web_items_vs_pdf_out_items"].append({"id": iid, "web": web_items, "pdf_out": pdf_out_items})
            pdf_comments = {norm(v["comments"]) for v in vio}
            for b in H["comment_blocks"]:
                if b["comments"] and norm(b["comments"]) not in pdf_comments:
                    checks["web_comment_not_found_in_pdf"].append(
                        {"id": iid, "item": b["item_number"], "web": b["comments"][:120]})


        # --- rows ---------------------------------------------------------
        for n, p in pts.items():
            if any(v["item_number"] == n and v["status"] == "OUT" for v in vio):
                item_points[n][p] += 1
        for v in vio:
            item_titles[v["item_number"]][v["item_title"]] += 1

        out_items = {v["item_number"] for v in vio if v["status"] == "OUT"}
        closure = closure_snippets(P["general_comment"], L.get("comments"), [v["comments"] for v in vio])
        duration, times_suspect = time_flags(hdr["time_in"], hdr["time_out"])
        for v in vio:
            for other in vio:
                t = other["item_title"]
                if len(t) > 15 and (t in v["comments"] or any(t in c["code_text"] for c in v["codes"])):
                    checks["item_title_inside_narrative"].append({"id": iid, "item": v["item_number"], "title": t})
        if hdr["time_in"] and hdr["time_out"] and time24(hdr["time_out"]) < time24(hdr["time_in"]):
            checks["source_time_out_before_time_in"].append(
                {"id": iid, "time_in": hdr["time_in"], "time_out": hdr["time_out"]})
        row = {
            "inspection_id": iid,
            "inspection_date": date,
            "time_in": hdr["time_in"],
            "time_out": hdr["time_out"],
            "time_in_24h": time24(hdr["time_in"]),
            "time_out_24h": time24(hdr["time_out"]),
            "duration_minutes": duration,
            "times_suspect": times_suspect,
            "purpose": L["purpose"],
            "inspection_type": L["inspectionType"],
            "score": L["score"],
            "printed_points_total": sum(pts.values()),
            "violation_entries": len(vio),
            "scored_violation_entries": sum(v["status"] == "OUT" for v in vio),
            "warning_entries": sum(v["status"] == "OUT-W" for v in vio),
            "items_out": len(out_items),
            "priority_items_out": sum(item_category(n) == "Priority" for n in out_items),
            "priority_foundation_items_out": sum(item_category(n) == "Priority Foundation" for n in out_items),
            "core_items_out": sum(item_category(n) == "Core" for n in out_items),
            "repeat_entries": sum(v["repeat"] for v in vio),
            "corrected_on_site_entries": sum(v["corrected_on_site"] for v in vio),
            "form_repeat_count": hdr["repeat_count"],
            "form_cos_count": hdr["cos_count"],
            "followup_required": hdr["followup_required"],
            "risk_category": hdr["risk_category"],
            "general_comment": P["general_comment"],
            "listing_comment": (L.get("comments") or "").strip(),
            "closure_mentioned": bool(closure),
            "closure_text": " || ".join(closure),
            "inspector": hdr["inspector"],
            "inspector_email": hdr["inspector_email"],
            "person_in_charge": hdr["person_in_charge"],
            "person_in_charge_title": hdr["person_in_charge_title"],
            "establishment_name": L["establishmentName"],
            "permit_id": L["permitID"],
            "permit_number": hdr["permit_number"],
            "permit_type": (L["permitType"] or "").strip(),
            "permit_category": PERMIT_CATEGORIES.get((L["permitType"] or "").strip(), "Other"),
            "program": L["programName"],
            "food_type": food_types.get(L.get("food_type_managerID") or "", ""),
            "address": " ".join(x for x in [L["addressLine1"], L["addressLine2"]] if x),
            "city": clean_city(L["city"]) or clean_city(hdr["city"]),
            "zip": (L["zip"] or hdr["zip"] or "").strip(),
            "location_key": location_key(" ".join(x for x in [L["addressLine1"], L["addressLine2"]] if x),
                                         L["zip"] or hdr["zip"]),
            "owner_name": hdr["owner_name"],
            "owner_name_truncated": hdr.get("owner_name_truncated", False),
            "measurements": len(P["measurements"]),
            "pdf_pages": P["pdf_pages"],
            "found_via": found_via,
            "pdf_url": pdf_url(iid),
            "web_url": inspection_url(iid),
        }
        inspections.append(row)

        counted = set()
        for seq, v in enumerate(vio, 1):
            codes = v["codes"]
            # An item is scored once per inspection however many entries it
            # has; flag the entry whose item_points make up the total.
            counts = v["status"] == "OUT" and v["item_number"] not in counted
            if counts:
                counted.add(v["item_number"])
            violations.append({
                "inspection_id": iid,
                "inspection_date": date,
                "establishment_name": L["establishmentName"],
                "permit_id": L["permitID"],
                "permit_type": row["permit_type"],
                "purpose": L["purpose"],
                "inspection_score": L["score"],
                "seq": seq,
                "item_number": v["item_number"],
                "item_title": v["item_title"],
                "item_category": item_category(v["item_number"]),
                "status": v["status"],
                "item_points": pts.get(v["item_number"]),
                "counts_toward_score": counts,
                "corrected_on_site": v["corrected_on_site"],
                "repeat": v["repeat"],
                "correct_by_date": mdy_to_iso(v["correct_by_date"]) if v["correct_by_date"] else "",
                "comments": v["comments"],
                "code": " | ".join(c["code"] for c in codes),
                "code_normalized": " | ".join(normalize_code(c["code"]) for c in codes),
                "code_is_priority_marked": any(c["code"].startswith("*") for c in codes),
                "code_text": " | ".join(c["code_text"] for c in codes),
                "inspector": hdr["inspector"],
                "pdf_url": pdf_url(iid),
            })
        for seq, m in enumerate(P["measurements"], 1):
            num, num_from = reading_number(m["item"], m["value"])
            measurements.append({
                "inspection_id": iid,
                "inspection_date": date,
                "establishment_name": L["establishmentName"],
                "seq": seq,
                "item": m["item"],
                "location": m["location"],
                "value": m["value"],
                "unit": m["unit"],
                "value_number": num,
                "value_number_from": num_from,
            })

    # --- permit pages vs listing ----------------------------------------------
    listed = {r["inspection_id"] for r in recs}  # includes permit-page backfills
    by_permit = defaultdict(list)
    for r in inspections:
        by_permit[r["permit_id"]].append(r)
    for pid, prec in permits.items():
        if prec["error"] or not prec["page"]:
            checks["permit_page_errors"].append({"permit": pid, "error": prec["error"]})
            continue
        page_ids = {i["inspection_id"] for i in prec["page"]["inspections"]}
        mine = {r["inspection_id"] for r in recs if r["listing"]["permitID"] == pid}
        for x in sorted(page_ids - listed):
            checks["on_permit_page_not_in_listing"].append({"permit": pid, "inspection": x})
        for x in sorted(mine - page_ids):
            checks["in_listing_not_on_permit_page"].append({"permit": pid, "inspection": x})

    # --- establishments ------------------------------------------------------
    establishments = []
    for pid, rows in by_permit.items():
        # Same-day inspections: order by time in, then routine before reinspection.
        rows.sort(key=lambda r: (r["inspection_date"], r["time_in_24h"], r["purpose"] != "Routine"))
        last = rows[-1]
        routine = [r for r in rows if r["purpose"] == "Routine"]
        establishments.append({
            "permit_id": pid,
            "establishment_name": last["establishment_name"],
            "address": last["address"],
            "city": last["city"],
            "zip": last["zip"],
            "location_key": last["location_key"],
            "permit_type": last["permit_type"],
            "permit_category": last["permit_category"],
            "food_type": last["food_type"],
            "owner_name": last["owner_name"],
            "inspections": len(rows),
            "routine_inspections": len(routine),
            "reinspections": len(rows) - len(routine),
            "first_inspection": rows[0]["inspection_date"],
            "latest_inspection": last["inspection_date"],
            "latest_score": last["score"],
            "max_score": max(r["score"] for r in rows),
            "mean_routine_score": round(sum(r["score"] for r in routine) / len(routine), 2) if routine else "",
            "total_violation_entries": sum(r["violation_entries"] for r in rows),
            "total_scored_violation_entries": sum(r["scored_violation_entries"] for r in rows),
            "total_priority_items_out": sum(r["priority_items_out"] for r in rows),
            "total_repeat_entries": sum(r["repeat_entries"] for r in rows),
            "inspections_mentioning_closure": sum(r["closure_mentioned"] for r in rows),
            "permit_page_url": permit_url(pid),
        })
    establishments.sort(key=lambda r: r["establishment_name"])

    items = []
    for n in sorted(set(item_titles) | set(item_points)):
        weights = item_points.get(n, Counter())
        if len(weights) > 1:
            checks["item_with_inconsistent_weight"].append({"item": n, "weights": dict(weights)})
        if weights and weights.most_common(1)[0][0] != CATEGORY_POINTS.get(item_category(n)):
            checks["item_weight_vs_form_category"].append({"item": n, "weights": dict(weights)})
        items.append({
            "item_number": n,
            "item_title": item_titles[n].most_common(1)[0][0] if item_titles[n] else "",
            "item_category": item_category(n),
            "points_when_out": weights.most_common(1)[0][0] if weights else "",
            "violation_entries": sum(item_titles[n].values()),
        })

    inspections.sort(key=lambda r: (r["inspection_date"], r["establishment_name"], r["inspection_id"]))
    violations.sort(key=lambda r: (r["inspection_date"], r["establishment_name"], r["inspection_id"], r["seq"]))
    measurements.sort(key=lambda r: (r["inspection_date"], r["establishment_name"], r["inspection_id"], r["seq"]))

    write_csv(OUT / "inspections.csv", inspections, list(inspections[0]))
    write_csv(OUT / "violations.csv", violations, list(violations[0]))
    write_csv(OUT / "measurements.csv", measurements, list(measurements[0]))
    write_csv(OUT / "establishments.csv", establishments, list(establishments[0]))
    write_csv(OUT / "items.csv", items, list(items[0]))

    listing_meta = json.loads((RAW / "listing_meta.json").read_text())
    summary = {
        "listing": listing_meta,
        "counts": {
            "inspections_in_listing": len(recs),
            "inspections_built": len(inspections),
            "establishments": len(establishments),
            "violation_entries": len(violations),
            "measurements": len(measurements),
            "permit_pages": len(permits),
        },
        "check_failures": {k: len(v) for k, v in sorted(checks.items())},
        "details": {k: v for k, v in sorted(checks.items())},
    }
    (OUT / "validation.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: summary[k] for k in ("counts", "check_failures")}, indent=2))
    if checks.get("pdf_missing_or_unparsed"):
        # Those inspections are not in the CSVs; don't let that pass quietly.
        print(f"ERROR: {len(checks['pdf_missing_or_unparsed'])} inspections have no parsed PDF "
              "and are missing from the CSVs", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    build()
