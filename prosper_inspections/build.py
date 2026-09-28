"""Build the Prosper health-inspection tables from the parsed monthly reports.

Inputs (data/raw/, written by fetch.py and parse.py):
  pdf_manifest.json        every downloaded PDF (source URL, sha256)
  parsed_reports.json.gz   table rows, printed counts and cross-check tokens of every PDF
  permits_food.json.gz     the Town's public EnerGov food operational-permit list
  fetch_meta.json, wayback_cdx.json   fetch runs and the Wayback file decisions
  ../hand_check.csv        rows read by hand against the PDF text (see --worksheet)

Outputs (data/): inspections.csv, monthly_counts.csv, establishments.csv, narrative_events.csv,
validation.json.

  python build.py                 # from the committed parsed data
  python build.py --reparse       # first re-run parse.py over the PDF cache (PROSPER_CACHE)
  python build.py --worksheet     # also write the hand-review worksheet into the cache
  python build.py --record-cell-fingerprints   # add five-cell fingerprints to hand_check.csv rows
                                               # whose reviewed fingerprint still matches

Every number printed here or written to validation.json is computed by this script.
"""

import argparse
import collections
import csv
import difflib
import json
import random
import re
import sys
from pathlib import Path

import pandas as pd

from common import CACHE, DATA, RAW, read_json, write_json

HERE = Path(__file__).resolve().parent
HAND_CHECK = HERE / "data" / "hand_check.csv"
ORDINANCE_URL = "https://web.archive.org/web/20230928185438id_/https://www.prospertx.gov/DocumentCenter/View/122/Food-Establishment-Ordinance-PDF"
ORDINANCE_EFFECTIVE = "2022-01"  # Ord. No. 2021-74, passed 2021-12-14, effective 2022-01-01
SAMPLE_SEED = 20260928
SAMPLE_SHARE = 0.10


# ============================================================================ report selection
def month_add(ym, k):
    y, m = map(int, ym.split("-"))
    m += k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def row_sig(r):
    return (r["name"].lower(), r["score"].lower(), r["result"].lower())


def select_reports(parsed):
    """One primary report per month. Returns (primary: month -> report, notes: list, other: list).
    A PDF whose page 1 names another department (e.g. the Public Works report saved on the old
    site as 'Monthly-Development-Report-September-1.pdf') is not a Development Services report
    and is set aside (other)."""
    by_month = collections.defaultdict(list)
    other = []
    for p in parsed:
        dept = p.get("page1_department")
        if dept and dept != "Development Services":
            other.append({"key": p["key"], "page1_department": dept, "page1_month": p["page1_month"],
                          "file_name_month": p["title_month"], "table_rows": len(p["rows"]),
                          "decision": f"not a Development Services report (page 1: {dept} Monthly Report); not used"})
            continue
        by_month[p["report_month"]].append(p)
    primary, notes = {}, []
    for month, reps in sorted(by_month.items()):
        arch = [p for p in reps if p["source"] == "archive_center"]
        if arch:
            chosen = arch[0]
        else:
            # identical files collapse; otherwise the copy with the most table rows wins
            chosen = sorted(reps, key=lambda p: (-len(p["rows"]), p["key"]))[0]
        primary[month] = chosen
        for p in reps:
            if p is chosen:
                continue
            same_file = p["sha256"] == chosen["sha256"]
            same_rows = [row_sig(r) for r in p["rows"]] == [row_sig(r) for r in chosen["rows"]]
            notes.append({"month": month, "kept": chosen["key"], "dropped": p["key"],
                          "identical_file": same_file, "identical_table_rows": same_rows,
                          "dropped_rows": len(p["rows"])})
    return primary, notes, other


def repeated_reports(primary):
    """Months whose table repeats the previous month's table (name/score/result signatures)."""
    out = {}
    months = sorted(primary)
    for a, b in zip(months, months[1:]):
        if month_add(a, 1) != b:
            continue
        ra = [row_sig(r) for r in primary[a]["rows"]]
        rb = [row_sig(r) for r in primary[b]["rows"]]
        if not rb:
            continue
        common = sum((collections.Counter(ra) & collections.Counter(rb)).values())
        share = common / len(rb)
        if share >= 0.8:
            out[b] = {"repeats": a, "shared_rows": common, "rows": len(rb), "share": round(share, 3)}
    return out


def cells_sig(r):
    """All five printed cells of a parsed row (address as published: withheld stays withheld)."""
    return tuple((r.get(k) or "").strip().lower() for k in ("name", "type", "address", "score", "result"))


MIN_REPRINT_RUN = 4


def reprinted_runs(primary):
    """Runs of MIN_REPRINT_RUN or more consecutive rows of one month's table that are identical,
    in all five printed cells and in the same order, to a run in an earlier month's table: a
    page or table the Town reprinted from an earlier report (e.g. August 2024 p.15 repeats July
    2024 p.13). Returns ({(month, row index): earlier month}, [run details]). When a run occurs
    in several earlier reports, the latest of them is named."""
    months = sorted(primary)
    seqs = {m: [cells_sig(r) for r in primary[m]["rows"]] for m in months}
    flags, runs = {}, []
    for i, b in enumerate(months):
        sb = seqs[b]
        best = {}
        for a in months[:i]:
            sa = seqs[a]
            pos = collections.defaultdict(list)
            for y, sig in enumerate(sa):
                pos[sig].append(y)
            for x, sig in enumerate(sb):
                for y in pos.get(sig, []):
                    if x and y and sb[x - 1] == sa[y - 1]:
                        continue  # not the start of a run
                    k = 0
                    while x + k < len(sb) and y + k < len(sa) and sb[x + k] == sa[y + k]:
                        k += 1
                    if k >= MIN_REPRINT_RUN:
                        for j in range(x, x + k):
                            best[j] = a  # months are in order: a later earlier-month overwrites
                        runs.append({"month": b, "rows": [x + 1, x + k], "earlier_month": a,
                                     "earlier_rows": [y + 1, y + k], "length": k,
                                     "pages": sorted({primary[b]["rows"][j]["page"] for j in range(x, x + k)}),
                                     "earlier_pages": sorted({primary[a]["rows"][j]["page"] for j in range(y, y + k)})})
        for j, a in best.items():
            flags[(b, j)] = a
    # keep, for each run in a month, only the latest earlier month it matches
    latest = {}
    for r in runs:
        key = (r["month"], r["rows"][0], r["rows"][1])
        if key not in latest or r["earlier_month"] > latest[key]["earlier_month"]:
            latest[key] = r
    return flags, sorted(latest.values(), key=lambda r: (r["month"], r["rows"][0]))


def page_break_repeats(primary):
    """Rows that are the first row of a page and identical (all five printed cells) to the last
    row of the previous page of the same table: possibly the last row printed twice across a
    page break, possibly two visits. Returns {(month, row index)}."""
    out = set()
    for m, p in primary.items():
        rows = p["rows"]
        for j in range(1, len(rows)):
            if rows[j]["page"] != rows[j - 1]["page"] and cells_sig(rows[j]) == cells_sig(rows[j - 1]):
                out.add((m, j))
    return out


def heading_flags(primary):
    """Reports whose printed table heading names another month, or is not an inspections title."""
    out = {}
    for m, p in sorted(primary.items()):
        notes = []
        heads = p.get("table_headings") or []
        for h in heads:
            if h["month"] and h["month"] != m:
                shared = "that month's report is not in this dataset"
                if h["month"] in primary:  # does the table repeat the month its heading names?
                    common = collections.Counter(cells_sig(r) for r in p["rows"]) & \
                        collections.Counter(cells_sig(r) for r in primary[h["month"]]["rows"])
                    shared = f"{sum(common.values())} of its {len(p['rows'])} rows are in the {h['month']} table"
                notes.append(f"heading on p.{h['page']} says {h['month']}: '{h['text']}' ({shared})")
            if h["kind"] != "inspections":
                notes.append(f"table on p.{h['page']} is titled '{h['text']}', not an inspections title")
        if p["rows"] and not heads:
            notes.append("no printed heading found above the table")
        if notes:
            out[m] = {"key": p["key"], "heading": heads[0]["text"] if heads else "", "notes": notes}
    return out


# ============================================================================ row normalization
RESULT_LIKE = re.compile(r"^(pass(ed)?|pas|fail(ed)?|compliant|non[- ]?compliant)\b", re.I)
NUMERIC = re.compile(r"^(\d{1,3})(?=$|\s*[/&]|\s)")


def result_class(s):
    t = (s or "").strip().lower()
    if not t:
        return "blank"
    if re.match(r"^fail(ed)?\b", t) or re.match(r"^non[- ]?compliant", t):
        return "fail"
    if re.match(r"^(pass(ed)?|pas)\b", t) or re.match(r"^compliant", t) or re.search(r"\bapproved\b", t):
        return "pass"
    if re.fullmatch(r"n/?a", t):
        return "not_applicable"
    return "not_stated"


PURPOSES = [
    ("fbi_complaint", r"\bfbi\b|foodborne"),
    ("complaint_follow_up", r"complaint.*follow|follow.*complaint"),
    ("complaint", r"complaint"),
    ("follow_up", r"follow[- ]?ups?\b|re-?inspection|remained closed|re-?opened|\breopen"),
    ("fire_sprinkler", r"sprinkler"),
    ("imminent_health_hazard", r"imminent health hazard|electrical fire|\bfire\b"),
    ("change_of_ownership", r"change of ownership|owner change"),
    ("courtesy", r"courtesy"),
    ("notice_of_violation", r"\bnov\b|notice of violation"),
    ("pre_opening", r"pre-?opening"),
    ("preliminary", r"prelim"),
    ("final", r"\bfinal\b|health approval"),
    ("certificate_of_occupancy", r"\bt?co\b|c/o|c\.o\.|certificate of occupancy"),
    ("plan_review", r"review"),
    ("remodel", r"remodel|kiosk"),
    ("pool_routine", r"routine pool|pool inspection|splash.*inspection|piwf inspection|pool construction|"
                     r"multi-?family inspection"),
    ("temporary_food", r"temporary food|temp food"),
    ("mobile_annual", r"annual (inspection|permit)"),
    ("routine", r"routine inspection"),
    ("not_scored", r"not scored"),
    ("closure", r"closure"),
]
TYPE_IS_PURPOSE = re.compile(r"inspection|prelim|final|review|pre-opening|complaint", re.I)

FACILITY = [
    ("pool", r"pool|\bspa\b|splash|lagoon|piwf|swimming|multi-?family"),
    ("mobile", r"mobile|\bmfe\b|\bmef\b|truck|trailer|food unit"),
    ("temporary", r"\btemp|booth"),
    ("daycare", r"day ?care|child ?care"),
    ("school", r"school|cafeteria"),
    ("concession", r"concession|sno-?cone|snow cone|seasonal"),
    ("restaurant", r"restaurant|juice|coffee|donut|smoothie|yogurt|pizza|catering|\bbar\b"),
    ("store", r"convenience|liquor|grocery|retail|pharmacy|stroe|walmart"),
    ("other", r"pantry|other business|plan review|outdoor kitchen|health final|refrigeration"),
]
NAME_FACILITY = [
    ("pool", r"\bpool\b|\bhoa\b|splash|lagoon|apartments?\b"),
    ("school", r"\bisd\b|school|elementary|middle|high school|academy of|cafeteria"),
    ("daycare", r"montessori|kinder ?care|primrose|goddard|daycare|children'?s lighthouse|kiddie|kids ?r"),
]


def facility_class(btype, name, purpose):
    t = btype or ""
    if purpose == "pool_routine" or re.search(r"pool", t, re.I):
        return "pool"
    if purpose == "temporary_food":
        return "temporary"
    for cls, rx in FACILITY:
        if re.search(rx, t, re.I):
            return cls
    for cls, rx in NAME_FACILITY:
        if re.search(rx, name or "", re.I):
            return cls
    return "unknown"


def follow_up_number(text):
    t = text.lower()
    m = re.search(r"(?:no\.?|#)\s*(\d)\b", t) or re.search(r"\b(\d)(?:st|nd|rd|th)\s+follow", t) \
        or re.search(r"follow[- ]?up\s+(\d)\b", t)
    return int(m.group(1)) if m else None


def normalize_row(r, report, era, era_basis):
    name, btype, addr = r["name"], r["type"], r["address"]
    score_raw, result_raw = r["score"], r["result"]
    swapped = False
    # October 2022: the Town printed 'Compliant'/'Non Compliant' under 'Score or Inspection Purpose'
    # and the score or purpose under 'Pass/Fail'. Put them back where they belong.
    if RESULT_LIKE.match(score_raw) and not RESULT_LIKE.match(result_raw):
        score_raw, result_raw, swapped = result_raw, score_raw, True
    res = result_class(result_raw)
    text_parts = []
    m = NUMERIC.match(score_raw)
    number = int(m.group(1)) if m else None
    rest = score_raw[m.end():].strip(" /&") if m else score_raw
    if rest and not re.fullmatch(r"n/?a\s*\(?", rest, re.I):
        text_parts.append(rest)
    if res == "not_stated":
        text_parts.append(result_raw)
    if TYPE_IS_PURPOSE.search(btype):
        text_parts.append(btype)
    if re.search(r"annual (inspection|permit)", addr, re.I):
        text_parts.append(addr)
    ptext = " / ".join(text_parts)
    purpose = None
    for key, rx in PURPOSES:
        if re.search(rx, ptext, re.I):
            purpose = key
            break
    if number is not None and (purpose in (None, "follow_up", "closure") and not TYPE_IS_PURPOSE.search(btype)):
        followups_in_row = purpose == "follow_up"
        purpose = "routine"
    else:
        followups_in_row = False
    if purpose is None:
        purpose = "unspecified"
    fac = facility_class(btype, name, purpose)
    all_text = " | ".join([btype, score_raw, result_raw])
    closure = bool(re.search(r"clos(ed|ure)", all_text, re.I)) and not re.search(r"closed for business", all_text, re.I)
    reopen = bool(re.search(r"\bre-?open", all_text, re.I))  # not 'Pre-Opening'
    score_100 = number if (number is not None and era == "score_100") else None
    demerit_27 = number if (number is not None and era == "demerit_27item") else None
    out = {
        "business_name": name,
        "business_type": btype,
        "address": "" if re.fullmatch(r"n/?a", addr or "", re.I) else addr,
        "address_withheld": addr == "[withheld]",
        "inspection_date_printed": r.get("date", ""),
        "score_or_purpose_raw": r["score"],
        "result_raw": r["result"],
        "columns_swapped_in_source": swapped,
        "inspection_purpose": purpose,
        "follow_up_number": follow_up_number(ptext) if purpose in ("follow_up", "complaint_follow_up") else None,
        "row_also_lists_follow_ups": followups_in_row,
        "facility_class": fac,
        "result": res,
        "scale_era": era if number is not None else (era or ""),
        "scale_era_basis": era_basis,
        "score_100": score_100,
        "demerits_equiv": (100 - score_100) if score_100 is not None else None,
        "demerit_27": demerit_27,
        "below_70": score_100 is not None and score_100 < 70,
        "over_30_demerits": demerit_27 is not None and demerit_27 > 30,
        "closure_text": closure,
        "reopen_text": reopen,
        "closed_for_business_text": bool(re.search(r"closed for business", all_text, re.I)),
        "imminent_hazard_text": bool(re.search(r"imminent health hazard", all_text, re.I)),
    }
    out["closure_rule_applies"] = bool(out["below_70"] and report["report_month"] >= ORDINANCE_EFFECTIVE)
    return out


def scale_eras(primary):
    """Scale era for every primary report: the report's own printed note, else inferred."""
    eras = {}
    notes = sorted((m, p["scale_note"]) for m, p in primary.items() if p["scale_note"])
    last_100 = max((m for m, s in notes if s == "score_100"), default=None)
    first_100 = min((m for m, s in notes if s == "score_100"), default=None)
    last_dem = max((m for m, s in notes if s == "demerit_27item"), default=None)
    for m, p in primary.items():
        if not p["rows"]:
            eras[m] = ("", "no health-inspection table in this report")
        elif p["scale_note"]:
            eras[m] = (p["scale_note"], f"note printed under the table ({p['key']} p.{p['scale_note_page']})")
        elif last_dem and m <= last_dem:
            eras[m] = ("demerit_27item", f"inferred: no note in this report; the 27-item demerit note is printed "
                                         f"through {last_dem}")
        elif first_100 and m >= first_100:
            eras[m] = ("score_100", f"inferred: no note in this report; the 0-100 note is printed from "
                                    f"{first_100} to {last_100}, and no later report describes another scale")
        else:
            eras[m] = ("unknown", "no note and outside the dated eras")
    return eras


# ============================================================================ printed counts
def count_values(p):
    """(current month, current YTD, prior-year month, prior-year YTD, basis) from a report's count line,
    or, in the 2015 narrative staff reports, from the sentence 'Eight (8) Health Inspections were
    performed in October'."""
    c = p.get("count")
    nc = p.get("narrative_count")
    if not c and nc:
        return {"cur_month": nc["value"], "cur_ytd": None, "prior_month": None, "prior_ytd": None,
                "basis": "narrative sentence", "header": None, "values_raw": [str(nc["value"])],
                "page": nc["page"], "narrative": nc["text"]}
    if not c:
        return None
    y = int(p["report_month"][:4])
    cur_m = cur_y = pri_m = pri_y = None
    basis = c["method"]
    if c.get("columns"):
        for col in c["columns"]:
            if col["year"] == y:
                if col["kind"] == "month":
                    cur_m = col["value"]
                else:
                    cur_y = col["value"]
            elif col["year"] == y - 1:
                if col["kind"] == "month":
                    pri_m = col["value"]
                else:
                    pri_y = col["value"]
    elif c["method"] in ("chart_two_values", "text_no_header") and len(c["values_raw"]) == 2:
        a, b = (int(v.replace(",", "")) for v in c["values_raw"])
        cur_m, cur_y = min(a, b), max(a, b)
        basis = f"{c['method']}; month/YTD assigned by magnitude (inferred)"
    return {"cur_month": cur_m, "cur_ytd": cur_y, "prior_month": pri_m, "prior_ytd": pri_y, "basis": basis,
            "header": c.get("header"), "values_raw": c.get("values_raw"), "page": c.get("page")}


def monthly_counts(primary, rows_by_month, repeats, reprinted_by_month, headings):
    """One row per calendar month from the first to the last report, reconciling the printed
    'Health Inspections' line with the table rows.

    Each count line is checked against its neighbours: YTD(m) must equal YTD(m-1) + count(m)
    (January: YTD = count). Lines the Town re-used unchanged from the previous month's report
    ('stale': all printed values identical) are not trusted, and neither are prior-year columns
    copied unchanged from the previous report. Where a month's own line fails, the prior-year
    column of the report twelve months later, or a value derived from neighbouring YTD totals,
    is used instead; count_basis says which.
    """
    cv = {m: count_values(p) for m, p in primary.items()}
    months = sorted(primary)
    all_months, m = [], months[0]
    while m <= months[-1]:
        all_months.append(m)
        m = month_add(m, 1)

    def own(m, k):
        return (cv.get(m) or {}).get(k)

    stale, stale_prior = {}, {}
    for m in all_months:
        a, b = cv.get(m), cv.get(month_add(m, -1))
        stale[m] = bool(a and b and a.get("values_raw") and a["values_raw"] == b.get("values_raw")
                        and a["basis"] != "narrative sentence" and b["basis"] != "narrative sentence")
        stale_prior[m] = bool(a and b and a.get("prior_month") is not None and
                              (a["prior_month"], a["prior_ytd"]) == (b.get("prior_month"), b.get("prior_ytd")))

    def nxt(m, k):
        n = month_add(m, 12)
        if not cv.get(n) or stale.get(n) or stale_prior.get(n):
            return None
        return cv[n].get(k)

    def ytd_ref(m):
        """Best available YTD total for month m, and where it came from."""
        if cv.get(m) and not stale[m] and own(m, "cur_ytd") is not None:
            return own(m, "cur_ytd"), "own report"
        if nxt(m, "prior_ytd") is not None:
            return nxt(m, "prior_ytd"), "next year's report"
        if m.endswith("-01") and nxt(m, "prior_month") is not None:
            return nxt(m, "prior_month"), "next year's report (January count = YTD)"
        if m.endswith("-01") and cv.get(m) and not stale[m] and own(m, "cur_month") is not None:
            return own(m, "cur_month"), "own report (January count = YTD)"
        return None, ""

    def forward_ytd(m):
        """YTD(m) implied by the next month's line: YTD(m+1) - count(m+1)."""
        n = month_add(m, 1)
        if n.endswith("-01") or not cv.get(n) or stale.get(n):
            return None
        if own(n, "cur_ytd") is None or own(n, "cur_month") is None:
            return None
        return own(n, "cur_ytd") - own(n, "cur_month")

    out = []
    for m in all_months:
        p = primary.get(m)
        v = cv.get(m) or {}
        repeat = repeats.get(m, {}).get("repeats", "")
        n_rows = len(rows_by_month.get(m, [])) if (p and p["rows"]) else (0 if p else None)
        n_reprint = reprinted_by_month.get(m, 0)
        rec = {"report_month": m, "report_key": p["key"] if p else "", "report_url": p["url"] if p else "",
               "report_available": bool(p),
               "table_heading_printed": ((p or {}).get("table_headings") or [{}])[0].get("text", ""),
               "table_heading_note": "; ".join(headings.get(m, {}).get("notes", [])),
               "table_rows": n_rows,
               "rows_reprinted_from_earlier_report": n_reprint if p else None,
               # blank when the whole table repeats the previous month's (June 2026)
               "table_rows_net": (n_rows - n_reprint) if (n_rows is not None and not repeat) else None,
               "printed_count": v.get("cur_month"), "printed_ytd": v.get("cur_ytd"),
               "printed_prior_year_count": v.get("prior_month"), "printed_prior_year_ytd": v.get("prior_ytd"),
               "count_line_method": v.get("basis", ""),
               "count_line_header": " | ".join(x for x in (v.get("header") or []) if x),
               "count_line_values": " ".join(v.get("values_raw") or []),
               "count_line_is_copy_of_previous_month": stale[m],
               "count_in_next_year_report": nxt(m, "prior_month"),
               "ytd_in_next_year_report": nxt(m, "prior_ytd"),
               "no_inspections_statement": ((p or {}).get("no_inspections_statement") or {}).get("text", ""),
               "table_repeats_previous_month": repeat}
        cnt, ytd = rec["printed_count"], rec["printed_ytd"]
        prev = month_add(m, -1)
        prev_ytd, prev_src = ytd_ref(prev) if not m.endswith("-01") else (0, "start of year")
        # ---- YTD chain for this month's own line
        if not p:
            chain = "no report"
        elif cnt is None:
            chain = "no printed count"
        elif stale[m]:
            chain = "count line is a copy of the previous month's"
        elif m.endswith("-01"):
            ref = ytd if ytd is not None else forward_ytd(m)
            chain = "not checkable" if ref is None else ("ok" if ref == cnt else
                                                         f"mismatch (YTD implied by February = {ref})")
        elif ytd is None or prev_ytd is None:
            chain = "not checkable"
        elif stale.get(prev) and prev_src == "":
            chain = "not checkable (previous month's line is a copy)"
        else:
            chain = "ok" if prev_ytd + cnt == ytd else f"mismatch (previous YTD {prev_ytd} + {cnt} != {ytd})"
        rec["ytd_chain"] = chain
        # ---- counts derived from YTD totals
        this_ytd, _ = ytd_ref(m)
        derived = None
        if this_ytd is not None and prev_ytd is not None:
            derived = this_ytd - prev_ytd
        fwd = forward_ytd(m)
        backward = (fwd - prev_ytd) if (fwd is not None and prev_ytd is not None) else None
        rec["count_derived_from_ytd"] = derived if derived is not None else backward
        alt = rec["count_in_next_year_report"]
        # ---- best count
        best, basis = None, ""
        if p and cnt is not None and v.get("basis") == "narrative sentence":
            best, basis = cnt, f"narrative sentence in this month's report (\"{v['narrative']}\"); no YTD printed"
        elif p and cnt is not None and not stale[m]:
            if chain == "ok":
                best, basis = cnt, "printed in this month's report; YTD chain ok"
            elif chain.startswith("not checkable"):
                best, basis = cnt, "printed in this month's report; YTD chain not checkable"
            elif alt is not None and alt == cnt:
                best, basis = cnt, "printed in this month's report and again in next year's; YTD totals disagree"
            elif alt is not None and alt in (derived, backward):
                best, basis = alt, "next year's report, agreeing with the YTD totals (this month's printed count fails the YTD chain)"
            elif m.endswith("-01") and fwd is not None:
                best, basis = fwd, "YTD implied by February's line (this month's printed count fails the check)"
            else:
                best, basis = cnt, "printed in this month's report; YTD chain fails, no agreeing alternative"
        elif p and stale[m]:
            if alt is not None:
                best, basis = alt, "next year's report (this month's count line is a copy of the previous month's)"
            elif backward is not None:
                best, basis = backward, ("derived: next month's YTD minus next month's count minus previous "
                                         "month's YTD (this month's count line is a copy of the previous month's)")
        else:
            if alt is not None:
                best, basis = alt, "next year's report (no count in this month's report)" if p else \
                    "next year's report (no report for this month)"
            elif derived is not None:
                best, basis = derived, "derived from YTD totals"
        if best is None and rec["no_inspections_statement"]:
            best, basis = 0, "the report states no health inspections were performed"
        rec["count_best"] = best
        rec["count_basis"] = basis
        # rows the Town reprinted from an earlier report are not counted against this month's count
        rec["gap_count_minus_rows"] = (best - rec["table_rows_net"]) if (
            best is not None and rec["table_rows"] is not None and not repeat and p and
            (p["rows"] or rec["no_inspections_statement"])) else None
        out.append(rec)
    return out


# ============================================================================ establishments
DIRS = {"N": "N", "NORTH": "N", "S": "S", "SOUTH": "S", "E": "E", "EAST": "E", "W": "W", "WEST": "W"}
STREET_TYPES = {"ROAD", "RD", "DRIVE", "DR", "STREET", "ST", "BOULEVARD", "BLVD", "PARKWAY", "PKWY", "TRAIL",
                "TRL", "LANE", "LN", "AVENUE", "AVE", "CIRCLE", "CIR", "PLACE", "PL", "COURT", "CT", "HWY",
                "HIGHWAY", "WAY", "PKY"}
NAME_STOP = {"the", "llc", "inc", "co", "corp", "corporation", "ltd", "lp", "no", "of", "and", "a", "dba", "texas",
             "tx", "prosper", "restaurant", "restaurants", "company", "store", "stores", "at", "in", "na"}


def parse_address(s):
    """'4740 West University Drive, Suite 90' -> ('4740', 'UNIVERSITY', '90')."""
    if not s or s == "[withheld]":
        return None, None, None
    t = s.upper().replace(".", " ")
    t = re.sub(r",?\s*\bPROSPER\s*,?\s*(TX|TEXAS)\b.*$", "", t)  # the city, not 'Prosper Trail'
    t = re.sub(r",?\s*\b(TX|TEXAS)\s+\d{5}\b.*$", "", t)
    suite_rx = r"(?:SUITE|STE|UNIT|BLDG|#)\s*:?\s*#?\s*(?:(?:STE|SUITE)\s*#?\s*)?([A-Z]?\d+[A-Z]?)\b"
    suites = re.findall(suite_rx, t)
    t = re.sub(suite_rx, " ", t)
    toks = re.findall(r"[A-Z0-9]+", t)
    if not toks or not toks[0].isdigit():
        return None, None, None
    core = [x for x in toks[1:] if x not in DIRS and x not in STREET_TYPES and x not in ("UNIT", "SUITE", "STE")]
    if not core:
        return None, None, None
    return toks[0], " ".join(core[:3]), (suites[-1].lstrip("0") or "0") if suites else None


def name_tokens(s):
    s = (s or "").lower().replace("&", " and ").replace("’", "'")
    s = re.sub(r"'s\b", "s", s).replace("'", "")
    # initialisms written with hyphens or dots are one word: 'H-E-B', 'H.E.B.' -> 'heb'
    s = re.sub(r"\b[a-z](?:[-.][a-z])+\b\.?", lambda m: re.sub(r"[-.]", "", m.group()), s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return [t for t in s.split() if t not in NAME_STOP and not t.isdigit()]


# words that say what kind of place it is, not which one: they do not count as a shared name
GENERIC = set("""food foods kitchen grill cafe coffee tea bar pizza market bakery shop express house station
school high middle elementary isd hs ms academy concession concessions stand arena home away visitor
ranch hoa pool truck trailer mobile unit shaved shave ice snow sno cone cones eatery cuisine mexican
italian indian chinese thai sushi bbq taco tacos burger burgers chicken donut donuts bagels wine liquor
convenience catering fresh family group holdings enterprises services partners frozen yogurt custard
treats kroger target costco walmart wal mart town fine spirits beer wines grocery supermarket""".split())
# a host store's name (Kroger, Target, Costco, Walmart) marks where a counter is, not who runs it


def name_numbers(s):
    """Store / unit numbers in a name: any 2+ digit run, or a digit after '#', 'No.', 'Truck', 'Pool', 'Stand'."""
    s = s or ""
    return set(re.findall(r"\d{2,}", s)) | set(re.findall(r"(?:#|\bno\.?|\bnumber|\bstand|\bpool|\btruck)\s*(\d+)", s, re.I))


def name_sim(a, b, strict=False):
    """1.0 for the same name words; else the share of distinctive (non-generic) words in common,
    or the string similarity of the sorted words when that is at least 0.88. Names carrying
    different unit or store numbers ('Truck #2005' vs 'Truck #1143') score 0."""
    na, nb = name_numbers(a), name_numbers(b)
    if na and nb and not (na & nb):
        return 0.0
    ta, tb = set(name_tokens(a)), set(name_tokens(b))
    if not ta or not tb:
        return 0.0
    if ta == tb or "".join(name_tokens(a)) == "".join(name_tokens(b)):  # 'Wingstop' = 'Wing Stop'
        return 1.0
    ratio = difflib.SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    if strict:  # campus names: the school's own name is shared by every kitchen and stand on it
        return ratio if ratio >= 0.95 else 0.0
    sa, sb = ta - GENERIC, tb - GENERIC
    contain = 0.0
    if sa and sb:
        inter = sa & sb
        if any(len(x) >= 3 for x in inter):
            contain = len(inter) / min(len(sa), len(sb))
    return max(contain, ratio if ratio >= 0.88 else 0.0)


WITHHELD = "[withheld]"


def permit_name(p):
    """The trade name (DBA); the legal company name only when there is no DBA (a host store's
    company, e.g. 'Kroger Texas LP' on a Starbucks-kiosk permit, would match the whole store) and
    fetch.py did not withhold it. None when the permit has no public name."""
    dba = p["DBA"] if p["DBA"] and p["DBA"].strip().upper() not in ("NA", "N/A", "NONE") else None
    name = dba or p["CompanyName"]
    return name if name and name != WITHHELD else None


def place_kind(text):
    """'restaurant' or 'store' from a name or a printed business type, else None. Separates two
    operations of one company at one address (H-E-B's grocery store and its BBQ restaurant)."""
    t = (text or "").lower()
    if re.search(r"\bbbq\b|restaurant|grill|kitchen|\bcafe\b|pizza|taco|burger|sushi|bakery|coffee|donut", t):
        return "restaurant"
    if re.search(r"grocery|supermarket|food store|convenience|liquor|wine|pharmacy|drug store", t):
        return "store"
    return None


def kind_score(a, b):
    return 0 if not a or not b else (1 if a == b else -1)


def shared_distinctive(a, b):
    return len((set(name_tokens(a)) - GENERIC) & (set(name_tokens(b)) - GENERIC))


def permit_establishments(permits):
    """Collapse permits (renewals get new numbers) into establishments. A permit joins an earlier
    group at the same street number, street and suite when the names are similar (name_sim >= 0.5)
    and either the company is the same (CompanyKey) or the names agree strongly (name_sim >= 0.88
    or two distinctive words in common), and the two are not one a store and the other a
    restaurant. A permit with no public name joins a group of the same company at that address."""
    groups = []
    for p in sorted(permits, key=lambda r: r["CaseNumber"]):
        name = permit_name(p)
        names = {name} if name else set()
        kind = place_kind(name)
        num, core, suite = parse_address(p["AddressDisplay"])
        if p.get("UnitOrSuite"):
            _, _, s2 = parse_address(f"1 X ST STE {p['UnitOrSuite']}".replace("STE SUITE", "STE"))
            suite = s2 or suite
        best, best_key = None, None
        for g in groups:
            if (g["num"], g["core"], g["suite"]) != (num, core, suite) or kind_score(kind, g["kind"]) < 0:
                continue
            same_co = bool(p.get("CompanyKey")) and p["CompanyKey"] in g["company_keys"]
            if names and g["names"]:
                sim = max(name_sim(a, b) for a in names for b in g["names"])
                strong = any(name_sim(a, b) >= 0.88 or shared_distinctive(a, b) >= 2 for a in names for b in g["names"])
                ok = sim >= 0.5 and (same_co or strong)
            else:
                sim, ok = 0.0, same_co
            if ok and (best_key is None or (same_co, sim) > best_key):
                best, best_key = g, (same_co, sim)
        if best is not None:
            best["permits"].append(p)
            best["names"] |= names
            best["company_keys"].add(p.get("CompanyKey"))
            best["kind"] = best["kind"] or kind
        else:
            groups.append({"num": num, "core": core, "suite": suite, "names": set(names), "permits": [p],
                           "company_keys": {p.get("CompanyKey")}, "kind": kind,
                           "address": re.sub(r"\s+PROSPER TX \d{5}$", "", p["AddressDisplay"], flags=re.I)})
    for i, g in enumerate(sorted(groups, key=lambda g: g["permits"][0]["CaseNumber"]), 1):
        g["id"] = f"P{i:04d}"
        by_date = sorted(g["permits"], key=lambda r: (r["IssueDate"] or r["ApplyDate"] or "", r["CaseNumber"]))
        g["latest"] = by_date[-1]
        g["name"] = next((permit_name(r) for r in reversed(by_date) if permit_name(r)), "[name withheld]")
    return groups


# Food Establishment permits cover restaurants, stores and concessions. Pools, mobile units and
# temporary-event booths hold other permit types (not fetched), and daycares have no permit class.
MATCH_CLASSES = {"restaurant", "store", "concession", "school", "other", "unknown"}
SCHOOLISH = re.compile(r"\bisd\b|school|\bhs\b|\bms\b|elementary|middle", re.I)


def match_rows(rows, groups):
    """Attach each table row to a permit establishment: address + name, else a unique name."""
    by_addr = collections.defaultdict(list)
    for g in groups:
        if g["num"]:
            by_addr[(g["num"], g["core"])].append(g)
    for r in rows:
        r["_addr"] = parse_address(r["address"])
        num, core, suite = r["_addr"]
        best, how, sim = None, "none", 0.0
        if r["facility_class"] not in MATCH_CLASSES:
            r["permit_establishment"], r["match_method"], r["match_name_similarity"] = None, "not attempted (class)", None
            continue
        if num:
            cands = [g for g in by_addr.get((num, core), []) if not (suite and g["suite"] and suite != g["suite"])]
            strict = r["facility_class"] == "school" or bool(SCHOOLISH.search(r["business_name"]))
            rkind = {"restaurant": "restaurant", "store": "store"}.get(r["facility_class"])
            # exact names first; among equally good names, the permit of the same kind (store vs
            # restaurant); then the higher similarity
            scored = [(max((name_sim(r["business_name"], n, strict) for n in g["names"]), default=0.0), g)
                      for g in cands]
            scored = sorted((x for x in scored if x[0] >= 0.5),
                            key=lambda x: (-(x[0] >= 0.99), -kind_score(rkind, x[1]["kind"]), -x[0]))
            if scored:                                                  # vendor on its campus
                sim, best = scored[0]
                how = "address+name" if (suite and best["suite"] == suite) or not (suite or best["suite"]) \
                    else "address+name (suite missing on one side)"
        if best is None:
            scored = sorted(((max((name_sim(r["business_name"], n) for n in g["names"]), default=0.0), g)
                             for g in groups), key=lambda x: -x[0])
            top = [s for s in scored if s[0] >= 0.99]
            if len(top) == 1 and len(name_tokens(r["business_name"])) >= 1 and not num:
                sim, best = top[0]
                how = "name only (unique permit name)"
        r["permit_establishment"] = best
        r["match_method"] = how
        r["match_name_similarity"] = round(sim, 2) if best else None
    return rows


def table_establishments(rows, start_index):
    """Cluster rows without a permit match into table-only establishments."""
    clusters = []
    for r in rows:
        if r["permit_establishment"] is not None:
            continue
        num, core, suite = r["_addr"]
        key_name = " ".join(sorted(name_tokens(r["business_name"]))) + "|" + " ".join(sorted(name_numbers(r["business_name"])))
        strict = bool(SCHOOLISH.search(r["business_name"]))  # kitchens / stands on one campus stay apart
        for c in clusters:
            if c["cls"] != r["facility_class"]:
                continue
            if num and (c["num"], c["core"]) == (num, core) and (suite == c["suite"] or not suite or not c["suite"]) \
                    and max(name_sim(r["business_name"], n, strict) for n in c["names"]) >= 0.5:
                break
            if not num and not c["num"] and key_name.strip("|") and key_name == c["key_name"]:
                break
        else:
            c = {"num": num, "core": core, "suite": suite, "names": set(), "key_name": key_name, "rows": [],
                 "cls": r["facility_class"]}
            clusters.append(c)
        c["names"].add(r["business_name"])
        c["rows"].append(r)
        r["_cluster"] = c
    for i, c in enumerate(clusters, start_index):
        c["id"] = f"T{i:04d}"
    return clusters


# ============================================================================ hand check
def hand_check_sample(df):
    """All Fail, sub-70, over-30-demerit, closure and re-open rows, plus a seeded 10% of the rest."""
    flagged = df[(df.result == "fail") | df.below_70 | df.over_30_demerits | df.closure_text | df.reopen_text |
                 df.closed_for_business_text]
    rest = df[~df.index.isin(flagged.index)]
    rng = random.Random(SAMPLE_SEED)
    ids = sorted(rest.row_id)
    k = round(len(ids) * SAMPLE_SHARE)
    sample = set(rng.sample(ids, k))
    return set(flagged.row_id), sample


def worksheet(df, parsed_full, flagged, sample, path):
    """Plain-text worksheet: for each row to check, the parsed fields next to the pdftotext layout
    lines of that row (located by the row's position in the page's result-column sequence)."""
    full = {p["key"]: p for p in parsed_full}
    lines = []
    for _, r in df[df.row_id.isin(flagged | sample)].iterrows():
        p = full[r.report_key]
        pg = str(r.pdf_page)
        lt = (p.get("layout_text") or {}).get(pg) or ""
        idx = (p.get("layout_result_lines") or {}).get(pg) or []
        k = r.row_on_page - 1
        L = lt.translate(str.maketrans({"‐": "-"})).splitlines()
        if k < len(idx):
            i = idx[k]
            excerpt = [l for l in L[max(0, i - 2): i + 3] if l.strip()]
        else:
            excerpt = ["(row not located by position; lines with the first name word:)"] + \
                [l for l in L if r.business_name.split(" ")[0] in l][:4]
        tag = "FLAG" if r.row_id in flagged else "SAMPLE"
        lines.append(f"=== {r.row_id} [{tag}] {r.report_key} p.{pg} row {r.row_on_page}")
        lines.append(f"    parsed: {r.business_name} || {r.business_type} || {r.address} || "
                     f"{r.score_or_purpose_raw} || {r.result_raw}")
        lines += ["    | " + re.sub(r"\s{3,}", "   ", l.strip()) for l in excerpt]
    path.write_text("\n".join(lines))
    return len(lines)


def load_hand_check():
    if not HAND_CHECK.exists():
        return {}
    with open(HAND_CHECK, newline="") as fh:
        return {row["row_id"]: row for row in csv.DictReader(fh)}


def record_cell_fingerprints(hc, fp3, fp5):
    """Add the five-cell fingerprint to reviewed rows that lack one, but only where the fingerprint
    taken at review time (name, score/purpose, result) still matches the row. The date it was
    added is recorded, so it is not mistaken for a review-time fingerprint."""
    import time
    today = time.strftime("%Y-%m-%d")
    fields = list(next(iter(hc.values())).keys())
    for extra in ("fingerprint_all_cells", "fingerprint_all_cells_recorded"):
        if extra not in fields:
            fields.insert(fields.index("fingerprint") + 1 + (extra == "fingerprint_all_cells_recorded"), extra)
    n = 0
    for rid, row in hc.items():
        if row.get("fingerprint_all_cells") or rid not in fp3 or row.get("fingerprint") != fp3[rid]:
            continue
        row["fingerprint_all_cells"] = fp5[rid]
        row["fingerprint_all_cells_recorded"] = (f"{today}, after the review, from the rows as then built; type and "
                                                 f"address were checked by the automated PDFium cell check, not re-read")
        n += 1
    with open(HAND_CHECK, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in hc.values():
            w.writerow({k: row.get(k, "") for k in fields})
    print(f"hand_check.csv: five-cell fingerprints added to {n} rows")


# ============================================================================ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reparse", action="store_true", help="re-run parse.py over the PDF cache first")
    ap.add_argument("--worksheet", action="store_true", help="write the hand-review worksheet to the cache")
    ap.add_argument("--record-cell-fingerprints", action="store_true",
                    help="add a five-cell fingerprint to hand_check.csv rows whose three-cell fingerprint matches")
    args = ap.parse_args()
    if args.reparse:
        import parse
        parse.main([])

    parsed = read_json(RAW / "parsed_reports.json.gz")
    manifest = read_json(RAW / "pdf_manifest.json")
    index = read_json(RAW / "archive_index.json")
    permits = read_json(RAW / "permits_food.json.gz")
    fetch_meta = read_json(RAW / "fetch_meta.json")
    wayback = read_json(RAW / "wayback_cdx.json")
    V = {"sources": {}, "parse": {}, "counts": {}, "scale": {}, "rows": {}, "closures": {}, "matching": {},
         "hand_check": {}, "privacy": {}}

    # ---------------------------------------------------------------- sources
    V["sources"] = {
        "archive_center_items": len(index),
        "archive_center_months": [m["title_month"] for m in index],
        "archive_center_pdfs_downloaded": sum(1 for m in manifest if m["source"] == "archive_center"),
        "all_pdfs_have_sha256_and_sha1": all(m.get("sha256") and m.get("sha1_base32") for m in manifest),
        "wayback_pdfs_downloaded": sum(1 for m in manifest if m["source"] == "wayback"),
        "pdf_bytes_total": sum(m["bytes"] for m in manifest),
        "food_permits_portal_total": fetch_meta["food_permits"]["TotalFound"],
        "food_permits_test_records_dropped": fetch_meta["food_permits"]["test_records_dropped"],
        "food_permits": len(permits),
        "fetch_runs": [{"started": r["started"], "finished": r["finished"], "offline": r.get("offline", False),
                        "network_requests": r["request_log"]["requests"],
                        "exceptions": r["request_log"]["exceptions"],
                        "non_ok_http_responses_retried": r["request_log"]["non_ok_responses"]}
                       for r in fetch_meta["runs"]],
    }
    skipped = [r for r in wayback["ds_files"] if r["decision"].startswith("skip")]
    V["sources"]["wayback_skipped_copies"] = {
        "files": len(skipped),
        "with_a_capture_identical_to_the_archive_center_pdf": sum(
            1 for r in skipped if r.get("a_capture_is_identical_to_archive_center_pdf")),
        "without": [{"file": r["file_name"], "captures": 1 + len(r["alternate_captures"]),
                     "cdx_length": int(r["length"])}
                    for r in skipped if not r.get("a_capture_is_identical_to_archive_center_pdf")],
    }
    V["sources"]["wayback_downloads"] = {
        "files": sum(1 for r in wayback["ds_files"] if r["decision"] == "download"),
        "checked_complete_pdf_and_cdx_sha1": sum(1 for r in wayback["ds_files"] if r.get("capture_used")),
        "fell_back_to_a_later_capture": [r["file_name"] for r in wayback["ds_files"] if r.get("captures_rejected")],
    }
    V["sources"]["monthly_report_files_not_downloaded"] = wayback["not_downloaded_monthly_report_files"]
    months_arch = sorted(m["title_month"] for m in index)
    missing = []
    m = months_arch[0]
    while m <= months_arch[-1]:
        if m not in months_arch:
            missing.append(m)
        m = month_add(m, 1)
    V["sources"]["archive_center_missing_months"] = missing

    primary, dedupe, other = select_reports(parsed)
    repeats = repeated_reports(primary)
    reprint_flags, reprint_runs = reprinted_runs(primary)
    page_break = page_break_repeats(primary)
    headings = heading_flags(primary)
    V["sources"]["duplicate_or_alternate_copies"] = dedupe
    V["sources"]["not_development_services_reports"] = other
    V["sources"]["wayback_month_from_pdf_differs_from_file_name"] = [
        {"key": p["key"], "file_name_month": p["title_month"], "pdf_page1_month": p["page1_month"]}
        for p in parsed if p["source"] == "wayback" and p["title_month"] and p["page1_month"]
        and p["title_month"] != p["page1_month"]]
    V["sources"]["reports_without_health_table"] = [
        {"key": p["key"], "month": p["report_month"],
         "statement": (p.get("no_inspections_statement") or {}).get("text")}
        for m_, p in sorted(primary.items()) if not p["rows"]]
    V["sources"]["table_repeats_previous_month"] = repeats
    V["sources"]["rows_reprinted_from_an_earlier_report"] = {
        "rule": (f"a run of {MIN_REPRINT_RUN} or more consecutive rows identical in all five printed cells, in the "
                 "same order, to a run in an earlier month's table"),
        "runs": reprint_runs,
        "rows_outside_tables_that_repeat_the_previous_month": sum(
            1 for (m, j) in reprint_flags if m not in repeats)}
    V["sources"]["table_heading_flags"] = headings

    # ---------------------------------------------------------------- parse cross-checks (all PDFs)
    def canon(t):
        t = (t or "").lower().replace("‐", "-").strip()
        if not t:
            return ""
        if t.startswith("pass") or t == "pas":
            return "pass"
        if t.startswith("fail"):
            return "fail"
        if t in ("n/a", "na"):
            return "na"
        if t.startswith("follow"):
            return "follow-up"
        if t.startswith("non"):
            return "non-compliant"
        return t.split(" ")[0]

    pages = mism = score_mism = 0
    mismatch_pages = []
    for p in parsed:
        for pg in p["health_pages"]:
            rows = [r for r in p["rows"] if r["page"] == pg]
            pages += 1
            a = [canon(r["result"]) for r in rows]
            b = [canon(x) for x in (p.get("layout_results") or {}).get(str(pg)) or []]
            if a != b:
                mism += 1
                mismatch_pages.append({"key": p["key"], "page": pg, "table_rows": len(a), "text_rows": len(b),
                                       "table_results": a, "text_results": b})
            sa = []
            for r in rows:
                for c in (r["score"], r["result"]):
                    mm = re.match(r"^(\d{1,3})(?:\s*/.*)?$", c or "")
                    if mm:
                        sa.append(int(mm.group(1)))
                        break
            if sa != ((p.get("layout_scores") or {}).get(str(pg)) or []):
                score_mism += 1
    V["parse"] = {
        "pdfs_parsed": len(parsed), "health_pages": pages, "table_rows_all_pdfs": sum(len(p["rows"]) for p in parsed),
        "text_crosscheck": ("pdfplumber table rows vs. pdftotext -layout text, page by page: the sequence of "
                            "result-column values and the sequence of numeric scores must be identical"),
        "pages_result_sequence_mismatch": mism, "pages_score_sequence_mismatch": score_mism,
        "result_mismatch_pages": mismatch_pages,
        "continuation_rows_merged": sum(r["merged_rows"] for p in parsed for r in p["rows"]),
        "headerless_continuation_tables": sum(len(p["headerless_tables"]) for p in parsed),
        "table_layouts": dict(collections.Counter(
            " | ".join(h["labels"]) for p in parsed for h in p["headers"]).most_common()),
        "staff_or_food_manager_mentions_on_health_pages": sum(p.get("staff_mentions", 0) for p in parsed),
        "page_number_overlaps_table_pages": {p["key"]: p["page_number_overlaps_table"] for p in parsed
                                             if p.get("page_number_overlaps_table")},
        "page_number_rule": ("the footer page number is removed before table extraction (it was read into the "
                             "last cell of March 2023 p.14 before)"),
    }
    # independent second engine: every text cell looked up in the PDFium text of its page
    def cell_summary(rows):
        c = collections.Counter()
        missing = []
        for key, r in rows:
            c["cells_checked"] += r.get("pdfium_cells_checked", 0)
            for fld, level in (r.get("pdfium_cells_nonverbatim") or {}).items():
                c[level] += 1
                if level == "not found":
                    missing.append({"row": key, "field": fld})
        c["found_verbatim"] = c["cells_checked"] - sum(v for k, v in c.items() if k != "cells_checked")
        return {**dict(c), "not_found_cells": missing}
    V["parse"]["pdfium_cell_check"] = {
        "method": ("each non-empty text cell (name, type, address, score/purpose, pass/fail, date) looked up in the "
                   "page text read by PDFium (pypdfium2, a second PDF engine not used for the extraction), with "
                   "whitespace ignored and dashes, quotes and ligature glyphs unified; 'ignoring hyphens' = found "
                   "once hyphens are ignored (a word hyphenated across a line break)"),
        "all_pdfs": cell_summary([(f"{p['key']} p.{r['page']} row {r['row_on_page']}", r)
                                  for p in parsed for r in p["rows"]]),
    }

    # ---------------------------------------------------------------- rows
    eras = scale_eras(primary)
    rows = []
    for month, p in sorted(primary.items()):
        era, basis = eras[month]
        for j, r in enumerate(p["rows"]):
            n = normalize_row(r, p, era, basis)
            table_rep = repeats.get(month, {}).get("repeats", "")
            reprint = reprint_flags.get((month, j), "")
            rows.append({"row_id": None, "report_month": month, "report_key": p["key"], "source": p["source"],
                         "report_title": p["title"], "report_url": p["url"],
                         "adid": int(p["key"][4:]) if p["key"].startswith("ADID") else None,
                         "pdf_page": r["page"], "row_on_page": r["row_on_page"],
                         "table_repeats_previous_month": table_rep,
                         "reprint_of_earlier_report": reprint,
                         "excluded_as_reprint": bool(table_rep or reprint),
                         "repeats_row_across_page_break": (month, j) in page_break,
                         "_pdfium": r.get("pdfium_cells_nonverbatim") or {},
                         **n})
    for month in sorted(primary):
        k = 0
        for r in rows:
            if r["report_month"] == month:
                k += 1
                r["row_id"] = f"PR-{month}-{k:03d}"
    rows_by_month = collections.defaultdict(list)
    for r in rows:
        rows_by_month[r["report_month"]].append(r)
    reprinted_by_month = collections.Counter(r["report_month"] for r in rows if r["reprint_of_earlier_report"])
    V["parse"]["pdfium_cell_check"]["published_rows"] = cell_summary(
        [(r["row_id"], {"pdfium_cells_checked": c, "pdfium_cells_nonverbatim": r["_pdfium"]})
         for r, c in zip(rows, [pr.get("pdfium_cells_checked", 0) for m_, p_ in sorted(primary.items())
                                for pr in p_["rows"]])])

    # ---------------------------------------------------------------- establishments
    groups = permit_establishments(permits)
    match_rows(rows, groups)
    clusters = table_establishments(rows, 1)
    for r in rows:
        g = r["permit_establishment"]
        r["establishment_id"] = g["id"] if g else r["_cluster"]["id"]
        r["permit_numbers"] = " ".join(p["CaseNumber"] for p in g["permits"]) if g else ""

    df = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_") and k != "permit_establishment"}
                       for r in rows])
    # identical rows within one month's table are kept: repeat visits (follow-ups, event days)
    # print the same way, so an identical row is not evidence of a duplicate listing
    same = ["report_month", "business_name", "business_type", "address", "score_or_purpose_raw", "result_raw"]
    df["identical_rows_in_month"] = df.groupby(same)["row_id"].transform("size")
    V["parse"]["pdfium_cell_check"]["hand_checked_rows_with_a_cell_not_found"] = None  # filled below

    # ---------------------------------------------------------------- hand check
    flagged, sample = hand_check_sample(df)
    hc = load_hand_check()
    df["hand_check_reason"] = df.row_id.map(lambda i: "flagged" if i in flagged else ("random_10pct" if i in sample else ""))
    df["hand_check_verdict"] = df.row_id.map(lambda i: hc.get(i, {}).get("verdict", ""))
    # fingerprints: the three cells fingerprinted when the rows were reviewed, and all five printed cells
    fp = df.business_name + " | " + df.score_or_purpose_raw + " | " + df.result_raw
    fp5 = (df.business_name + " | " + df.business_type + " | " + df.address + " | " + df.score_or_purpose_raw +
           " | " + df.result_raw)
    if args.record_cell_fingerprints and hc:
        record_cell_fingerprints(hc, dict(zip(df.row_id, fp)), dict(zip(df.row_id, fp5)))
        hc = load_hand_check()
    stale = [i for i, f, f5 in zip(df.row_id, fp, fp5) if i in hc and (
        (hc[i].get("fingerprint") and hc[i]["fingerprint"] != f) or
        (hc[i].get("fingerprint_all_cells") and hc[i]["fingerprint_all_cells"] != f5))]
    if args.worksheet:
        full = read_json(CACHE / "parsed_full.json.gz")
        path = CACHE / "hand_check_worksheet.txt"
        worksheet(df, full, flagged, sample, path)
        pd.DataFrame({"row_id": df.row_id, "fingerprint": fp, "fingerprint_all_cells": fp5})[
            df.row_id.isin(flagged | sample)].to_csv(CACHE / "hand_check_fingerprints.csv", index=False)
        print(f"worksheet: {path}")
    checked = df[df.hand_check_reason != ""]
    V["hand_check"] = {
        "method": ("Each selected row's parsed fields were compared by reading the pdftotext -layout text of "
                   "its PDF page (the lines of that row); verdicts are in data/hand_check.csv. The reviewer is "
                   "an AI model (Claude), not a person."),
        "selection": {"flagged_rule": "result Fail, score below 70, over 30 demerits, closure / re-open / "
                                      "closed-for-business wording", "flagged_rows": len(flagged),
                      "random_share_of_other_rows": SAMPLE_SHARE, "random_seed": SAMPLE_SEED,
                      "random_rows": len(sample)},
        "verdicts": dict(collections.Counter(checked.hand_check_verdict.replace("", "not yet checked"))),
        "rows_selected_without_verdict": int((checked.hand_check_verdict == "").sum()),
        "verdicts_for_rows_that_changed_since_check": stale,
        "fingerprints": {
            "reviewed_cells": ("the reviewer read all five printed cells (name, type, address, score/purpose, "
                               "pass/fail) against the PDF text; at review time only name, score/purpose and "
                               "result were fingerprinted"),
            "rows_with_three_cell_fingerprint": sum(1 for v in hc.values() if v.get("fingerprint")),
            "rows_with_five_cell_fingerprint": sum(1 for v in hc.values() if v.get("fingerprint_all_cells")),
            "five_cell_fingerprint_recorded": sorted({v.get("fingerprint_all_cells_recorded", "") for v in hc.values()
                                                      if v.get("fingerprint_all_cells")}),
        },
        "mismatches": [{"row_id": i, "note": hc[i].get("note", "")} for i in checked.row_id
                       if hc.get(i, {}).get("verdict") not in (None, "", "match")],
    }
    hc_rows = [r for r in rows if r["row_id"] in hc]
    V["parse"]["pdfium_cell_check"]["hand_checked_rows_with_a_cell_not_found"] = [
        r["row_id"] for r in hc_rows if "not found" in r["_pdfium"].values()]
    V["parse"]["pdfium_cell_check"]["hand_checked_rows_type_and_address_found"] = sum(
        1 for r in hc_rows if r["_pdfium"].get("type") != "not found" and r["_pdfium"].get("address") != "not found")

    # ---------------------------------------------------------------- monthly counts
    mc = monthly_counts(primary, rows_by_month, repeats, reprinted_by_month, headings)
    mdf = pd.DataFrame(mc)
    # months with a count and a table (or a 'no inspections' statement) that is not a copy of the
    # previous month's; rows reprinted from an earlier report are not counted (table_rows_net)
    have = mdf[mdf.gap_count_minus_rows.notna()]
    V["counts"] = {
        "months_in_range": len(mdf), "months_with_report": int(mdf.report_available.sum()),
        "months_with_printed_count_in_own_report": int(mdf.printed_count.notna().sum()),
        "months_with_printed_count_line": int((mdf.printed_count.notna() & (mdf.count_line_method != "narrative sentence")).sum()),
        "months_with_narrative_count": int((mdf.count_line_method == "narrative sentence").sum()),
        "months_with_best_count": int(mdf.count_best.notna().sum()),
        "ytd_chain": dict(collections.Counter(re.sub(r" \(.*", "", s) for s in mdf.ytd_chain)),
        "count_lines_copied_from_previous_month": mdf[mdf.count_line_is_copy_of_previous_month].report_month.tolist(),
        "ytd_chain_mismatches": [{"month": r.report_month, "printed_count": r.printed_count,
                                  "printed_ytd": r.printed_ytd, "detail": r.ytd_chain,
                                  "count_best": r.count_best, "basis": r.count_basis}
                                 for r in mdf.itertuples() if r.ytd_chain.startswith("mismatch")],
        "prior_year_ytd_disagrees_with_own_report": [
            {"month": r.report_month, "own": r.printed_ytd, "next_year_report": r.ytd_in_next_year_report}
            for r in mdf.itertuples() if pd.notna(r.printed_ytd) and pd.notna(r.ytd_in_next_year_report)
            and r.printed_ytd != r.ytd_in_next_year_report],
        "prior_year_column_disagrees_with_own_report": [
            {"month": r.report_month, "own": r.printed_count, "next_year_report": r.count_in_next_year_report}
            for r in mdf.itertuples() if pd.notna(r.printed_count) and pd.notna(r.count_in_next_year_report)
            and r.printed_count != r.count_in_next_year_report],
        "rows_vs_count": {"months_compared": len(have),
                          "excluded": ("months whose table repeats the previous month's (2026-06), and rows "
                                       "reprinted from an earlier report (table_rows_net)"),
                          "equal": int((have.gap_count_minus_rows == 0).sum()),
                          "fewer_rows_than_count": int((have.gap_count_minus_rows > 0).sum()),
                          "more_rows_than_count": int((have.gap_count_minus_rows < 0).sum()),
                          "sum_count_best": int(have.count_best.sum()),
                          "sum_table_rows_net": int(have.table_rows_net.sum()),
                          "table_rows_as_share_of_count": round(float(have.table_rows_net.sum() / have.count_best.sum()), 3),
                          "months_short_by_10_or_more": int((have.gap_count_minus_rows >= 10).sum()),
                          "reprinted_rows_excluded_in_compared_months": int(have.rows_reprinted_from_earlier_report.sum())},
        "largest_shortfalls": [{"month": r.report_month, "count": int(r.count_best), "rows": int(r.table_rows_net)}
                               for r in have.sort_values("gap_count_minus_rows", ascending=False).head(10).itertuples()],
        "overages": [{"month": r.report_month, "count": int(r.count_best), "rows": int(r.table_rows_net)}
                     for r in have[have.gap_count_minus_rows < 0].itertuples()],
        "narrative_counts_2015": [{"month": r.report_month, "count_best": int(r.count_best), "basis": r.count_basis}
                                  for r in mdf[mdf.count_line_method == "narrative sentence"].itertuples()],
        "counts_for_months_without_report": [{"month": r.report_month, "count_best": r.count_best, "basis": r.count_basis}
                                             for r in mdf[~mdf.report_available & mdf.count_best.notna()].itertuples()],
    }

    # ---------------------------------------------------------------- scale / rows summary
    V["scale"] = {
        "era_by_report_month": {m: e for m, (e, b) in sorted(eras.items())},
        "basis_counts": dict(collections.Counter("printed note" if b.startswith("note") else
                                                 ("no table" if not e else "inferred") for e, b in eras.values())),
        "inferred_months": sorted(m for m, (e, b) in eras.items() if e and not b.startswith("note")),
        "first_score_100_note": min((m for m, p in primary.items() if p["scale_note"] == "score_100"), default=None),
        "last_score_100_note": max((m for m, p in primary.items() if p["scale_note"] == "score_100"), default=None),
        "last_demerit_note": max((m for m, p in primary.items() if p["scale_note"] == "demerit_27item"), default=None),
        "score_100_range": [int(df.score_100.min()), int(df.score_100.max())],
        "demerit_27_range": [int(df.demerit_27.min()), int(df.demerit_27.max())],
        "score_100_values_below_50": df[df.score_100 < 50].row_id.tolist(),
        "demerit_27_values_above_30": df[df.demerit_27 > 30].row_id.tolist(),
    }
    # the statistics below leave out rows the Town reprinted (the June 2026 table, the August 2024
    # page copied from July 2024): they are copies, not further inspections
    prim_df = df[~df.excluded_as_reprint]
    V["rows"] = {
        "inspections_csv_rows": len(df), "report_months": int(df.report_month.nunique()),
        "first_month": df.report_month.min(), "last_month": df.report_month.max(),
        "rows_in_tables_that_repeat_previous_month": int((df.table_repeats_previous_month != "").sum()),
        "rows_reprinted_from_earlier_report": int((df.reprint_of_earlier_report != "").sum()),
        "rows_reprinted_from_earlier_report_outside_repeated_tables": int(
            ((df.reprint_of_earlier_report != "") & (df.table_repeats_previous_month == "")).sum()),
        "rows_excluded_as_reprint": int(df.excluded_as_reprint.sum()),
        "rows_excluding_reprints": len(prim_df),
        "by_scale_era_numeric": {"score_100": int(prim_df.score_100.notna().sum()),
                                 "demerit_27": int(prim_df.demerit_27.notna().sum())},
        "by_purpose": dict(prim_df.inspection_purpose.value_counts()),
        "by_facility_class": dict(prim_df.facility_class.value_counts()),
        "by_result": dict(prim_df.result.value_counts()),
        "score_100_by_facility_class": dict(prim_df[prim_df.score_100.notna()].facility_class.value_counts()),
        "restaurant_score_100_rows": int(((prim_df.facility_class == "restaurant") & prim_df.score_100.notna()).sum()),
        "score_100_summary": {k: round(float(v), 2) for k, v in prim_df.score_100.describe().items()},
        "restaurant_score_100_summary": {k: round(float(v), 2) for k, v in
                                         prim_df[prim_df.facility_class == "restaurant"].score_100.describe().items()},
        "demerit_27_summary": {k: round(float(v), 2) for k, v in prim_df.demerit_27.describe().items()},
        "score_100_rows_by_year": {k: int(v) for k, v in
                                   prim_df[prim_df.score_100.notna()].report_month.str[:4].value_counts().sort_index().items()},
        "rows_with_an_identical_row_in_same_month": int((prim_df.identical_rows_in_month > 1).sum()),
        "months_with_identical_rows": int(prim_df[prim_df.identical_rows_in_month > 1].report_month.nunique()),
        "rows_repeating_the_row_above_across_a_page_break": prim_df[prim_df.repeats_row_across_page_break].row_id.tolist(),
        "rows_repeating_the_row_above_across_a_page_break_in_repeated_tables":
            df[df.repeats_row_across_page_break & df.excluded_as_reprint].row_id.tolist(),
        "columns_swapped_in_source": int(df.columns_swapped_in_source.sum()),
        "columns_swapped_by_month": dict(df[df.columns_swapped_in_source].report_month.value_counts().sort_index()),
        "numeric_rows_including_reprints": {"score_100": int(df.score_100.notna().sum()),
                                            "demerit_27": int(df.demerit_27.notna().sum())},
        "addresses_withheld_mobile_or_out_of_town": int(df.address_withheld.sum()),
        "months_with_zero_numeric_restaurant_scores": sorted(set(prim_df.report_month) - set(
            prim_df[(prim_df.facility_class == "restaurant") &
                    (prim_df.score_100.notna() | prim_df.demerit_27.notna())].report_month)),
    }
    V["rows"]["months_since_2019_with_zero_numeric_restaurant_scores"] = len(
        [m for m in V["rows"]["months_with_zero_numeric_restaurant_scores"] if m >= "2019-01"])
    cl = df[df.closure_text | df.reopen_text | df.below_70 | df.over_30_demerits | df.closed_for_business_text |
            df.imminent_hazard_text]
    V["closures"] = {
        "ordinance": {"number": "2021-74", "section": "6.04.010 Inspections-Score", "effective": "2022-01-01",
                      "rule": "a sanitation score below 70 percent: closed until a reinspection is made and all "
                              "corrective action on critical violations is complete (Sec. 6.04.010); more than 30 "
                              "demerits is an imminent health hazard (Sec. 6.04.002(b))",
                      "url": ORDINANCE_URL},
        "rows": [{"row_id": r.row_id, "month": r.report_month, "name": r.business_name,
                  "score_or_purpose": r.score_or_purpose_raw, "result": r.result_raw, "score_100": r.score_100,
                  "demerit_27": r.demerit_27, "closure_text": bool(r.closure_text), "reopen_text": bool(r.reopen_text),
                  "below_70": bool(r.below_70), "closure_rule_applies": bool(r.closure_rule_applies),
                  "hand_check": r.hand_check_verdict} for r in cl.itertuples()],
        "below_70_rows": int(prim_df.below_70.sum()),
        "below_70_rows_since_ordinance": int(prim_df.closure_rule_applies.sum()),
        "below_70_since_ordinance_with_closure_wording": int((prim_df.closure_rule_applies & prim_df.closure_text).sum()),
        "below_70_rows_including_reprints": int(df.below_70.sum()),
        "narrative_events": [{"month": m, "key": p["key"], "page": e["page"], "text": e["text"]}
                             for m, p in sorted(primary.items()) for e in p.get("narrative_events") or []],
    }

    # ---------------------------------------------------------------- establishments table
    est = []
    ptab = df.set_index("row_id")
    for g in groups:
        rs = df[df.establishment_id == g["id"]]
        est.append(est_record(g["id"], "permit list", g["name"], sorted(g["names"]), g["address"], rs, g))
    for c in clusters:
        rs = df[df.establishment_id == c["id"]]
        names = sorted(c["names"])
        addr = rs.address.mode().iloc[0] if len(rs) else ""
        est.append(est_record(c["id"], "tables only", rs.business_name.iloc[-1], names, addr, rs, None))
    est = [e for e in est if e["table_rows"] or e["source"] == "permit list"]
    edf = pd.DataFrame(est)
    pdf_ = pd.DataFrame(permits)
    pdf_["prefix"] = pdf_.CaseNumber.str[:7]
    perm_est = edf[edf.source == "permit list"]
    active = perm_est[perm_est.any_active_permit]
    matched = ~df.match_method.isin(["none", "not attempted (class)"])
    V["matching"] = {
        "permits_by_number_prefix": dict(pdf_.prefix.value_counts().sort_index()),
        "permits_by_status": dict(pdf_.CaseStatus.value_counts()),
        "active_permits_by_number_prefix": dict(pdf_[pdf_.CaseStatus == "Active"].prefix.value_counts().sort_index()),
        "permit_establishments": len(groups), "permits": len(permits),
        "portal_total_found": fetch_meta["food_permits"]["TotalFound"],
        "test_records_dropped": fetch_meta["food_permits"]["test_record_case_numbers"],
        "permits_without_public_name": [p["CaseNumber"] for p in permits if not permit_name(p)],
        "permit_establishments_named_withheld": int((perm_est.name == "[name withheld]").sum()),
        "matched_rows": {"all_rows": int(matched.sum()), "excluding_reprints": int((matched & ~df.excluded_as_reprint).sum())},
        "active_permit_establishments_ever_scored_0_100": int((active.score_100_rows > 0).sum()),
        "active_permit_establishments_scored_0_100_since_2025_01": int((active.latest_score_month >= "2025-01").sum()),
        "permit_establishments_with_active_permit": int(edf[edf.source == "permit list"].any_active_permit.sum()),
        "table_only_establishments": len(clusters),
        "rows_by_match_method": dict(df.match_method.value_counts()),
        "matched_share_by_facility_class": {
            k: {"rows": int(len(v)), "matched": int((~v.match_method.isin(["none", "not attempted (class)"])).sum())}
            for k, v in prim_df.groupby("facility_class")},
        "permit_establishments_never_in_tables": int(((edf.source == "permit list") & (edf.table_rows == 0)).sum()),
        "active_permit_establishments_never_scored_in_tables": int(
            ((edf.source == "permit list") & edf.any_active_permit & (edf.score_100_rows == 0)).sum()),
    }
    V["privacy"] = {
        "addresses_withheld": int(df.address_withheld.sum()),
        "rule": ("addresses of mobile food units, addresses in another city, and addresses of temporary-event "
                 "vendors that are not a shared venue (3+ vendors) or a fixed business location are withheld "
                 "(parse.sanitized)"),
        "email_or_phone_left_in_outputs": int(sum(
            bool(re.search(r"[\w.+-]+@[\w-]+(\.[\w-]+)+|(?<![\w-])\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?![\w-])", str(v)))
            for col in ("business_name", "business_type", "address", "score_or_purpose_raw", "result_raw")
            for v in df[col])),
        "permit_fields_dropped": "holder first/middle/last names, applicant-written description, tax id",
        "permit_company_name": ("withheld ([withheld]) when the permit has a DBA, and when a permit without a DBA "
                                "has a company name that could be a person's name (fetch.could_be_person); "
                                "an opaque CompanyKey is kept for grouping"),
        "permit_company_names_withheld": sum(1 for p in permits if p["CompanyName"] == WITHHELD),
        "permit_test_records_dropped": fetch_meta["food_permits"]["test_records_dropped"],
        "email_or_phone_in_narrative_events": sum(
            bool(re.search(r"[\w.+-]+@[\w-]+(\.[\w-]+)+|(?<![\w-])\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?![\w-])", e["text"]))
            for e in V["closures"]["narrative_events"]),
    }

    # ---------------------------------------------------------------- write
    DATA.mkdir(exist_ok=True)
    cols = ["row_id", "report_month", "source", "report_key", "adid", "report_title", "report_url", "pdf_page",
            "row_on_page", "business_name", "business_type", "address", "address_withheld",
            "inspection_date_printed", "score_or_purpose_raw", "result_raw", "columns_swapped_in_source",
            "inspection_purpose", "follow_up_number", "row_also_lists_follow_ups", "facility_class", "result",
            "scale_era", "scale_era_basis", "score_100", "demerits_equiv", "demerit_27", "below_70",
            "over_30_demerits", "closure_rule_applies", "closure_text", "reopen_text", "closed_for_business_text",
            "imminent_hazard_text", "table_repeats_previous_month", "reprint_of_earlier_report",
            "excluded_as_reprint", "identical_rows_in_month", "repeats_row_across_page_break",
            "establishment_id", "permit_numbers",
            "match_method", "match_name_similarity", "hand_check_reason", "hand_check_verdict"]
    out = df[cols].copy()
    for c in ("adid", "score_100", "demerits_equiv", "demerit_27", "follow_up_number"):
        out[c] = out[c].astype("Int64")
    out.to_csv(DATA / "inspections.csv", index=False)
    mcols = ["report_month", "report_available", "report_key", "report_url", "table_heading_printed",
             "table_heading_note", "table_rows", "rows_reprinted_from_earlier_report", "table_rows_net", "printed_count",
             "printed_ytd", "printed_prior_year_count", "printed_prior_year_ytd", "count_line_is_copy_of_previous_month",
             "count_in_next_year_report",
             "ytd_in_next_year_report", "ytd_chain", "count_derived_from_ytd", "count_best", "count_basis",
             "gap_count_minus_rows", "table_repeats_previous_month", "no_inspections_statement",
             "count_line_method", "count_line_header", "count_line_values"]
    mo = mdf[mcols].copy()
    for c in ("table_rows", "rows_reprinted_from_earlier_report", "table_rows_net", "printed_count", "printed_ytd",
              "printed_prior_year_count", "printed_prior_year_ytd",
              "count_in_next_year_report", "ytd_in_next_year_report", "count_derived_from_ytd", "count_best",
              "gap_count_minus_rows"):
        mo[c] = mo[c].astype("Int64")
    mo.to_csv(DATA / "monthly_counts.csv", index=False)
    edf.to_csv(DATA / "establishments.csv", index=False)
    pd.DataFrame([{"report_month": e["month"], "report_key": e["key"], "report_url": primary[e["month"]]["url"],
                   "pdf_page": e["page"], "event_text": e["text"]} for e in V["closures"]["narrative_events"]],
                 columns=["report_month", "report_key", "report_url", "pdf_page", "event_text"]).to_csv(
        DATA / "narrative_events.csv", index=False)
    write_json(DATA / "validation.json", json.loads(json.dumps(V, default=to_builtin)))
    print(f"inspections.csv {len(out)} rows; monthly_counts.csv {len(mo)}; establishments.csv {len(edf)}")
    print(json.dumps({k: V[k] for k in ("rows", "matching")}, default=to_builtin, indent=1)[:4000])


def to_builtin(o):
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def est_record(eid, source, name, names, address, rs, g):
    rs = rs[~rs.excluded_as_reprint]  # reprinted rows (June 2026 table, Aug. 2024 p.15) are not counted twice
    scored = rs[rs.score_100.notna()] if len(rs) else rs
    last = scored.sort_values(["report_month", "pdf_page", "row_on_page"]).iloc[-1] if len(scored) else None
    rec = {
        "establishment_id": eid, "source": source, "name": name,
        "names_in_tables": " | ".join(sorted(set(rs.business_name))) if len(rs) else "",
        "address": address,
        "facility_class": rs.facility_class.mode().iloc[0] if len(rs) else "",
        "permit_numbers": " ".join(p["CaseNumber"] for p in g["permits"]) if g else "",
        "permit_class": g["latest"]["CaseType"] if g else "",
        "latest_permit_status": g["latest"]["CaseStatus"] if g else "",
        "any_active_permit": any(p["CaseStatus"] == "Active" for p in g["permits"]) if g else False,
        "latest_permit_issue_date": (g["latest"]["IssueDate"] or "")[:10] if g else "",
        "table_rows": len(rs),
        "first_report_month": rs.report_month.min() if len(rs) else "",
        "last_report_month": rs.report_month.max() if len(rs) else "",
        "score_100_rows": len(scored),
        "demerit_27_rows": int(rs.demerit_27.notna().sum()) if len(rs) else 0,
        "latest_score_100": int(last.score_100) if last is not None else None,
        "latest_score_month": last.report_month if last is not None else "",
        "min_score_100": int(scored.score_100.min()) if len(scored) else None,
        "mean_score_100": round(float(scored.score_100.mean()), 1) if len(scored) else None,
        "fail_rows": int((rs.result == "fail").sum()) if len(rs) else 0,
        "below_70_rows": int(rs.below_70.sum()) if len(rs) else 0,
        "closure_text_rows": int(rs.closure_text.sum()) if len(rs) else 0,
        "reopen_text_rows": int(rs.reopen_text.sum()) if len(rs) else 0,
    }
    for k in ("latest_score_100", "min_score_100"):
        rec[k] = rec[k] if rec[k] is not None else pd.NA
    return rec


if __name__ == "__main__":
    main()
