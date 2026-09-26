"""Build the McKinney inspection tables from the raw ArcGIS pull and the parsed reports.

Inputs (all under data/):
  raw/inspections.json.gz, raw/permits.json.gz, raw/schools_daycares.json.gz,
  raw/trades_day_farmers_market.json.gz, raw/attachments.json.gz  (fetch.py)
  raw/reports_parsed.jsonl.gz   parsed report PDFs, private fields removed (see --parsed)
  cuisine_codes.csv             our cuisine / establishment-type codes, one row per permit
  ../analysis/closures_reviewed.csv   closures read and classified by hand

Outputs (data/): inspections.csv, violations.csv, permits.csv, establishments.csv,
reports.csv, items.csv and validation.json. Every rule below was established by an audit
of the raw layer against the 3,134 report PDFs; validation.json re-checks each one.

  python build.py                          # from the committed parsed reports
  python build.py --parsed full.jsonl      # re-sanitize a fresh parse_pdf.py run first
"""

import argparse
import gzip
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from common import ITEMS, ITEM_COLUMNS, RAW, category, form_points, load_raw, wall_clock

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PARSED = RAW / "reports_parsed.jsonl.gz"
COL2N = {c: n for n, c, _ in ITEMS}

TEST_INSPECTIONS = {"FOOD-083269-2019"}  # "TEST FOR EMAIL NOTIFICATIONS": every item marked, score 101
INSPECTOR_ALIASES = {"Victoria Schweitzer": "Victoria Goldston"}  # signed as Schweitzer until mid-2024
REINSPECTION_TEXT = re.compile(r"^\s*(RE-?INSPECTION|REINSPECT|FOLLOW[- ]?UP)", re.I)
ORD_2023 = pd.Timestamp("2023-11-07")  # Ord. 2023-11-075: closure at 30 or more (before: 31 or more)
# Trade Days, farmers-market and event grounds, where most permits are weekend vendor booths
MARKET_SITES = ("4550 W UNIVERSITY DR", "4550 UNIVERSITY DR", "315 S CHESTNUT ST", "317 S CHESTNUT ST",
                "101 E HUNT ST", "111 N TENNESSEE ST")
# header cells kept from the report; the owner/contact and phone/e-mail cells are dropped
HEADER_KEEP = {"date", "time_in", "time_out", "permit_number", "risk_category", "purpose", "establishment_name",
               "address", "city", "zip", "score", "score_raw", "repeat_count", "cos_count", "follow_up",
               "page_label", "clipped_on_page"}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\w-])\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?![\w-])")


def redact(text):
    """Remove e-mail addresses and phone numbers from free text."""
    if not isinstance(text, str):
        return text
    return PHONE.sub("[phone]", EMAIL.sub("[e-mail]", text))


# ----------------------------------------------------------------------------- parsed reports
def sanitize(full_path):
    """Write raw/reports_parsed.jsonl.gz from a full parse_pdf.py run, minus private details.

    Dropped: the owner/contact and phone/e-mail header cells, the "received by" signature,
    e-mail addresses and phone numbers in the notes, and everything but the identity of the
    Outlook e-mail attached 388 times to 80 permits (a private message with an operator's
    personal address and photos).
    """
    out = []
    with open(full_path, encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            keep = {k: d.get(k) for k in ("REL_OBJECTID", "ATTACHMENTID", "file", "ATT_NAME", "CONTENT_TYPE",
                                          "doc_type", "parse_warnings")}
            if d["doc_type"] == "inspection_report":
                keep["pdf_pages"] = d.get("pdf_pages")
                keep["pdf_created"] = d.get("pdf_created")
                keep["header"] = {k: v for k, v in d["header"].items() if k in HEADER_KEEP}
                for k in ("items", "item10_sanitizer_fill", "temperature_table", "permit_expiration_date",
                          "inspector", "checks"):
                    keep[k] = d.get(k)
                keep["observations"] = redact(d.get("observations"))
                keep["cited_items"] = [{**c, "text": redact(c.get("text"))} for c in d.get("cited_items") or []]
                keep["temperatures"] = [{**t, "text": redact(t.get("text"))} for t in d.get("temperatures") or []]
            out.append(keep)
    out.sort(key=lambda r: (int(r["REL_OBJECTID"]), int(r["ATTACHMENTID"])))
    with gzip.open(PARSED, "wt", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    return len(out)


def load_reports():
    with gzip.open(PARSED, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


# ----------------------------------------------------------------------------- normalization
SUFFIX = {"STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "DRIVE": "DR", "ROAD": "RD", "PARKWAY": "PKWY",
          "PKY": "PKWY", "BOULEVARD": "BLVD", "LANE": "LN", "HIGHWAY": "HWY", "CIRCLE": "CIR", "COURT": "CT",
          "PLACE": "PL", "TRAIL": "TRL", "FREEWAY": "FWY", "EXPRESSWAY": "EXPY", "WEST": "W", "EAST": "E",
          "NORTH": "N", "SOUTH": "S"}
STREET_TYPES = {"ST", "AVE", "DR", "RD", "PKWY", "BLVD", "LN", "HWY", "CIR", "CT", "PL", "TRL", "FWY", "EXPY",
                "WAY", "LOOP", "PLZ", "SQ", "TER", "ROW", "XING", "PATH", "RUN", "PASS"}
UNIT_WORDS = {"STE", "SUITE", "UNIT", "BLDG", "BUILDING", "APT", "SP", "SPACE", "NO"}
NAME_STOP = {"THE", "LLC", "INC", "CO", "CORP", "LTD", "AND", "OF", "MCKINNEY", "MCK", "RESTAURANT", "CAFE",
             "DBA", "TX", "LP"}


def norm_addr(a):
    """(street, unit): street is the number and street words, unit whatever follows."""
    if not isinstance(a, str) or not a.strip():
        return ("", "")
    s = re.sub(r"[.,#]", " ", a.upper().replace("\n", " "))
    s = re.sub(r"\bSTATE HIGHWAY\b|\bSTATE HWY\b|\bS H\b", "SH", s)
    s = re.sub(r"\bU S\b|\bUS HIGHWAY\b|\bUS HWY\b", "US", s)
    toks = [SUFFIX.get(t, t) for t in s.split()]
    cut = None
    for i, t in enumerate(toks[1:], 1):
        if t in STREET_TYPES:
            cut = i + 1
    if cut is None:  # "SH 121 568", "US 75": the number after SH/US/FM belongs to the street
        for i, t in enumerate(toks):
            if t in ("SH", "US", "FM") and i + 1 < len(toks):
                cut = i + 2
    if cut is None:
        cut = len(toks)
    return (" ".join(toks[:cut]), " ".join(t for t in toks[cut:] if t not in UNIT_WORDS))


def norm_name(n):
    if not isinstance(n, str):
        return ""
    lines = [x.strip() for x in n.splitlines() if x.strip()]
    s = lines[0] if lines else ""
    s = re.sub(r"\*.*$", "", s)
    s = re.sub(r"(?i)\s*-?\s*change of ownership.*$", "", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = s.upper().replace("&", " AND ").replace("’", "'")
    s = re.sub(r"#\s*\d+", " ", s)
    s = re.sub(r"'S\b", "S", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s).replace("WAL MART", "WALMART")
    return " ".join(t for t in s.split() if t not in NAME_STOP and not re.fullmatch(r"\d{3,}", t))


def _similar(a, b):
    A, B = set(a.split()), set(b.split())
    if not A or not B:
        return False
    if a == b:
        return True
    return len(A & B) / len(A | B) > 0.5 or ((A <= B or B <= A) and a.split()[0] == b.split()[0])


def _jacc(a, b):
    A, B = set(a.split()), set(b.split())
    return len(A & B) / len(A | B) if A and B else 0


def link_establishments(p, slack_days=60):
    """Chain permits into establishments: each permit links to at most one earlier permit at the
    same street (same unit, or unit blank on one side) with a similar name that ended no later
    than 60 days after it began (exact-name duplicates may overlap)."""
    p = p.sort_values(["first", "link_number"]).reset_index(drop=True)
    by_street = {}
    for r in p.to_dict("records"):
        by_street.setdefault(r["street"], []).append(r)
    pred, has_succ = {}, set()
    slack = pd.Timedelta(days=slack_days)
    for street, rows in by_street.items():
        if not street:
            continue
        for i, a in enumerate(rows):
            best = None
            for b in rows[:i]:
                if b["link_number"] in has_succ or (a["unit"] and b["unit"] and a["unit"] != b["unit"]):
                    continue
                exact = a["nname"] == b["nname"] and a["nname"] != ""
                if not exact and (not _similar(a["nname"], b["nname"]) or b["last"] > a["first"] + slack):
                    continue
                score = (exact, _jacc(a["nname"], b["nname"]), b["last"], b["link_number"])
                if best is None or score > best[0]:
                    best = (score, b["link_number"])
            if best:
                pred[a["link_number"]] = best[1]
                has_succ.add(best[1])

    def root(x):
        while x in pred:
            x = pred[x]
        return x
    p["establishment_id"] = p.link_number.map(root)
    return p


def sig_to_name(s):
    s = re.sub(r"\(on phone\)", "", s or "").strip()
    if "," in s:
        last, first = [t.strip() for t in s.split(",", 1)]
        s = f"{first} {last}"
    return INSPECTOR_ALIASES.get(s, s) or None


CITE = re.compile(r"^\s*#?\s*(\d{1,2})\s*[-–:.)]\s*(.+)$")


def cited_text(comments):
    """{item: text} from inspector notes written as "NN - text" / "#NN text" (4+ words, so
    temperature lines such as "38 - Milk WIC" are skipped)."""
    out = {}
    for line in (comments if isinstance(comments, str) else "").splitlines():
        m = CITE.match(line)
        if not m:
            continue
        n, text = int(m.group(1)), m.group(2).strip()
        if 1 <= n <= 47 and len(text.split()) >= 4:
            out.setdefault(n, []).append(text)
    return {k: " | ".join(v) for k, v in out.items()}


# ----------------------------------------------------------------------------- build
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", help="full parse_pdf.py output (jsonl) to sanitize into raw/reports_parsed.jsonl.gz")
    args = ap.parse_args()
    if args.parsed:
        print("sanitized reports:", sanitize(args.parsed))
    checks = {}

    # --- inspections: one row per inspection number (the layer repeats 174 of them) ------------
    raw = load_raw("inspections")
    content = ["actual_start_date", "actual_end_date", "link_number", "total_score", "custom_comments", "status",
               "purpose_of_inspection"] + ITEM_COLUMNS
    varies = raw.groupby("inspection_number")[content].nunique(dropna=False).max(axis=1)
    checks["duplicate_rows_identical_content"] = bool((varies == 1).all())
    assert checks["duplicate_rows_identical_content"], "copies of an inspection differ in content"
    raw = raw.sort_values("OBJECTID")
    agg = raw.groupby("inspection_number").agg(
        layer_rows=("OBJECTID", "size"),
        inspectors_listed=("inspector_name", lambda s: "; ".join(sorted(set(s.dropna())))))
    ins = raw.drop_duplicates("inspection_number").set_index("inspection_number").join(agg).reset_index()
    ins["permitted_name"] = ins.permitted_name.map(redact)  # a few vendor permits carry a phone number
    checks["layer_rows"] = len(raw)
    checks["inspections"] = len(ins)

    ins["start"] = wall_clock(ins.actual_start_date)
    ins["end"] = wall_clock(ins.actual_end_date)
    ins["inspection_date"] = ins.start.dt.date.astype("string")
    ins["time_in"] = ins.start.dt.strftime("%H:%M")
    ins["time_out"] = ins.end.dt.strftime("%H:%M")
    dur = (ins.end - ins.start).dt.total_seconds() / 60
    ins["duration_min"] = dur.round(1)
    ins["time_flag"] = np.select(
        [ins.start.isna(), ins.start.dt.hour < 6, dur < 0, dur > 480],
        ["no start date", "starts before 6am", "end before start", "longer than 8h"], default="")

    ins["exclude_reason"] = ""
    ins.loc[ins.link_type != "Permit", "exclude_reason"] = "not tied to a permit (code case or none)"
    ins.loc[ins.status == "Void", "exclude_reason"] = "void"
    ins.loc[ins.inspection_number.isin(TEST_INSPECTIONS), "exclude_reason"] = "test record"

    p = ins.purpose_of_inspection.str.replace(r"^\d+ - ", "", regex=True)
    from_text = ins.custom_comments.fillna("").map(lambda t: bool(REINSPECTION_TEXT.match(t)))
    ins["purpose"] = p.fillna("Unrecorded")
    ins.loc[(p.isna() | p.eq("Routine")) & from_text, "purpose"] = "Reinspection (per notes)"
    ins["routine"] = (ins.exclude_reason == "") & ins.purpose.isin(["Routine", "Unrecorded"]) & ins.start.notna()

    # scores: the layer weights item 34 at 2 (form and ordinance: 1) and stores items corrected
    # on site as 0, so score_std re-weights the layer's items at form points
    ins["score_layer"] = ins.total_score
    ins["score_std"] = sum(ins[c].gt(0) * form_points(n) for n, c, _ in ITEMS).astype(int)
    checks["score_std_equals_layer_minus_item34"] = bool(
        (ins.score_std == ins.score_layer - ins.no_insect_contamination.gt(0)).all())
    checks["item34_layer_values"] = {str(k): int(v) for k, v in raw.no_insect_contamination.value_counts().items()}
    ins["items_out"] = ins[ITEM_COLUMNS].gt(0).sum(axis=1)
    ins["priority_items"] = ins[[c for n, c, _ in ITEMS if n <= 20]].gt(0).sum(axis=1)
    ins["closure_threshold"] = np.where(ins.start >= ORD_2023, 30, 31)
    ins["at_or_over_closure_threshold"] = ins.score_std >= ins.closure_threshold
    cites = ins.custom_comments.map(cited_text)
    ins["unscored_writeup"] = (ins.score_layer == 0) & cites.map(bool)

    # --- reports: printed permit + printed date (+ time in) tie a PDF to its inspection ------
    reports = load_reports()
    rep = []
    for r in reports:
        row = {"REL_OBJECTID": int(r["REL_OBJECTID"]), "ATTACHMENTID": int(r["ATTACHMENTID"]),
               "ATT_NAME": r["ATT_NAME"], "doc_type": r["doc_type"]}
        if r["doc_type"] == "inspection_report":
            h = r["header"]
            items = {it["item"]: it for it in r["items"]}
            row.update({
                "printed_permit": h.get("permit_number"), "printed_date": h.get("date"),
                "printed_time_in": h.get("time_in"), "printed_name": h.get("establishment_name"),
                "printed_address": h.get("address"), "printed_purpose": h.get("purpose"),
                "printed_score": h.get("score"), "printed_repeat_count": h.get("repeat_count"),
                "printed_cos_count": h.get("cos_count"),
                # a corrected-on-site item is written "COS" in place of "OUT" (parsed as status OUT, cos True)
                "out_items": ",".join(str(n) for n in sorted(items)
                                      if items[n]["status"] == "OUT" and not items[n]["cos"]),
                "cos_items": ",".join(str(n) for n in sorted(items) if items[n]["cos"]),
                "repeat_items": ",".join(str(n) for n in sorted(items) if items[n].get("r")),
                "inspector_signed": sig_to_name(r.get("inspector")),
                "pdf_created": r.get("pdf_created"),
                "score_matches_items": r["checks"]["score_matches_items"]})
        rep.append(row)
    rep = pd.DataFrame(rep)
    manifest = pd.DataFrame(json.loads((RAW / "attachments_manifest.json").read_text()))
    rep = rep.merge(manifest[["ATTACHMENTID", "sha256"]], on="ATTACHMENTID", how="left")
    isrep = rep.doc_type == "inspection_report"
    checks["attachments"] = {"files": len(rep), "reports": int(isrep.sum()),
                             "outlook_email_copies": int((rep.doc_type == "outlook_msg").sum()),
                             "distinct_email_files": int(rep.loc[~isrep, "sha256"].nunique()),
                             "reports_score_matches_items": int(rep.loc[isrep, "score_matches_items"].sum())}

    # the feature each file is attached to (layers 0, 2 and 3 share one OBJECTID space)
    feats = pd.concat([load_raw(n).assign(layer=n) for n in ("permits", "trades_day_farmers_market",
                                                              "schools_daycares")], ignore_index=True)
    fmap = feats.set_index("OBJECTID")
    rep["attached_permit"] = rep.REL_OBJECTID.map(fmap.link_number)
    rep["attached_name"] = rep.REL_OBJECTID.map(fmap.description)
    date_from_name = rep.ATT_NAME.str.extract(r"\((\d\d)-(\d\d)-(\d{4})\)")
    name_date = date_from_name[2] + "-" + date_from_name[0] + "-" + date_from_name[1]
    pd_ = pd.to_datetime(rep.printed_date, format="%m/%d/%Y", errors="coerce").dt.strftime("%Y-%m-%d")
    rep["report_date"] = pd_.fillna(name_date.where(isrep))
    rep["report_date_source"] = np.where(pd_.notna(), "printed", np.where(isrep, "file name", ""))

    def clock(t):
        m = re.match(r"(\d{1,2}):(\d\d)\s*([ap])m", str(t or "").strip(), re.I)
        if not m:
            return None
        h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
        return f"{h:02d}:{m.group(2)}"
    rep["report_time_in"] = rep.printed_time_in.map(clock)

    key = ins[ins.exclude_reason != "test record"][["inspection_number", "link_number", "inspection_date",
                                                    "time_in"]]
    cand = rep[isrep].reset_index().merge(key, left_on=["printed_permit", "report_date"],
                                          right_on=["link_number", "inspection_date"], how="left")
    n_cand = cand.groupby("index").inspection_number.transform("nunique")
    cand = cand[(n_cand <= 1) | (cand.time_in == cand.report_time_in)]
    match = cand.dropna(subset=["inspection_number"]).drop_duplicates("index").set_index("index").inspection_number
    rep["inspection_number"] = match.reindex(rep.index)
    rep["match_method"] = np.where(rep.inspection_number.notna(), "permit + date", "")
    # a few inspections have no start date: tie those by permit and the year in the inspection number
    undated = ins[ins.start.isna() & (ins.exclude_reason == "")][["inspection_number", "link_number"]]
    undated = undated.assign(year=undated.inspection_number.str[-4:])
    for i in rep.index[isrep & rep.inspection_number.isna()]:
        c = undated[(undated.link_number == rep.at[i, "printed_permit"])
                    & (undated.year == str(rep.at[i, "report_date"])[:4])]
        if len(c) == 1:
            rep.at[i, "inspection_number"] = c.inspection_number.iloc[0]
            rep.at[i, "match_method"] = "permit + number year (inspection has no date)"
    checks["reports_matched_to_inspection"] = int(rep.inspection_number.notna().sum())
    checks["reports_unmatched"] = rep.loc[isrep & rep.inspection_number.isna(),
                                          ["ATTACHMENTID", "printed_permit", "report_date"]].astype(str).to_dict("records")

    # where the report is shown: on its own permit, a neighbour at the same premises, or elsewhere
    feat_addr = rep.REL_OBJECTID.map(fmap.ADDRESS_LINE1).map(norm_addr)
    own_addr = rep.printed_permit.map(feats.drop_duplicates("link_number").set_index("link_number").ADDRESS_LINE1)
    own_addr = own_addr.fillna(rep.printed_address).map(norm_addr)
    same_premises = [bool(f[0]) and f[0] == o[0] and (f[1] == o[1] or not f[1] or not o[1])
                     for f, o in zip(feat_addr, own_addr)]
    rep["placement"] = np.select(
        [~isrep, rep.printed_permit == rep.attached_permit, np.array(same_premises)],
        ["not a report (e-mail copy)", "own permit", "same premises, other permit"], default="other establishment")
    own_oid = rep.printed_permit.map(feats.drop_duplicates("link_number").set_index("link_number").OBJECTID)
    rep["objectid_offset"] = (own_oid - rep.REL_OBJECTID).where(isrep)
    checks["report_placement"] = {k: int(v) for k, v in rep.placement.value_counts().items()}
    checks["report_objectid_offset"] = {str(int(k)): int(v) for k, v in
                                        rep.objectid_offset.value_counts().sort_index().items()}

    # one report per inspection: prefer the version whose OUT items equal the layer's, then the latest
    layer_out = ins.set_index("inspection_number")[ITEM_COLUMNS].gt(0)
    layer_out = layer_out.apply(lambda r: ",".join(str(COL2N[c]) for c in ITEM_COLUMNS if r[c]), axis=1)
    rm = rep.dropna(subset=["inspection_number"]).copy()
    rm["agrees_with_layer"] = rm.out_items == rm.inspection_number.map(layer_out)
    rm = rm.sort_values(["inspection_number", "agrees_with_layer", "pdf_created"], ascending=[True, False, False])
    versions = rm.drop_duplicates(["inspection_number", "printed_score", "out_items", "cos_items"]) \
        .groupby("inspection_number").size()
    files = rm.groupby("inspection_number").ATTACHMENTID.agg(lambda s: ";".join(str(x) for x in sorted(s)))
    own = rm.groupby("inspection_number").placement.agg(lambda s: (s == "own permit").any())
    best = rm.drop_duplicates("inspection_number").set_index("inspection_number")
    ins = ins.set_index("inspection_number")
    ins["report_files"] = files
    ins["report_versions"] = versions
    ins["report_shown_on_own_permit"] = own
    for c in ("printed_score", "cos_items", "repeat_items", "inspector_signed", "agrees_with_layer"):
        ins["report_" + c if not c.startswith("report") else c] = best[c]
    ins = ins.rename(columns={"report_printed_score": "score_printed"}).reset_index()
    ins["has_report"] = ins.report_files.notna()
    rdate = rm.drop_duplicates("inspection_number").set_index("inspection_number").report_date
    fill = ins.inspection_date.isna() & ins.inspection_number.isin(rdate.index)
    ins["date_source"] = np.where(fill, "report", np.where(ins.inspection_date.notna(), "layer", ""))
    ins.loc[fill, "inspection_date"] = ins.loc[fill, "inspection_number"].map(rdate)
    cos_pts = ins.report_cos_items.fillna("").map(
        lambda s: sum(form_points(int(n)) for n in s.split(",") if n))
    ins["report_cos_points"] = cos_pts.where(ins.has_report)
    ok = ins.has_report & (ins.score_printed == ins.score_std + ins.report_cos_points)
    checks["reports_printed_score_equals_std_plus_cos"] = {"matched_inspections": int(ins.has_report.sum()),
                                                           "equal": int(ok.sum()),
                                                           "not_equal": ins.loc[ins.has_report & ~ok,
                                                                                "inspection_number"].tolist()}
    checks["reports_out_items_equal_layer"] = {"equal": int(ins.report_agrees_with_layer.fillna(False).sum()),
                                               "differ": ins.loc[ins.has_report & ~ins.report_agrees_with_layer
                                                                 .fillna(False).astype(bool),
                                                                 "inspection_number"].tolist()}
    tmatch = rm.drop_duplicates("inspection_number")
    tmatch = tmatch.report_time_in == tmatch.inspection_number.map(ins.set_index("inspection_number").time_in)
    checks["reports_time_in_equals_layer_clock"] = {"equal": int(tmatch.sum()), "of": int(len(tmatch))}

    # inspector: the layer's name is the assigned inspector; the report is signed by who inspected
    ins["inspector_listed"] = ins.inspector_name
    ins["inspector_signed"] = ins.report_inspector_signed
    ins["inspector"] = ins.inspector_signed.fillna(ins.inspector_listed)
    signed = ins.has_report & ins.inspector_signed.notna() & ~ins.inspectors_listed.str.contains(";")
    checks["inspector_listed_vs_signed"] = {"signed_reports": int(signed.sum()),
                                            "differ": int((signed & (ins.inspector_signed != ins.inspector_listed)).sum())}

    # --- permits and establishments -------------------------------------------------------
    kept = ins[ins.exclude_reason != "test record"]
    na = kept.main_address.map(norm_addr)
    pk = kept.assign(street=na.map(lambda x: x[0]), unit=na.map(lambda x: x[1]),
                     nname=kept.permitted_name.map(norm_name)).sort_values("start")
    permits = pk.groupby("link_number").agg(
        name=("permitted_name", "last"), address=("main_address", "last"), nname=("nname", "last"),
        street=("street", "last"), unit=("unit", "last"), first=("start", "min"), last=("start", "max"),
        inspections=("inspection_number", "size")).reset_index()
    health = permits.link_number.str.startswith("HEALTH")
    linked = link_establishments(permits[health].copy())
    permits = permits.merge(linked[["link_number", "establishment_id"]], on="link_number", how="left")
    permits["establishment_id"] = permits.establishment_id.fillna(permits.link_number)
    permits["market_or_event_site"] = permits.street.isin([norm_addr(a)[0] for a in MARKET_SITES])
    cur = feats.drop_duplicates("link_number").set_index("link_number")  # a few permits have two features
    permits["current_layer"] = permits.link_number.map(cur.layer)
    for c in ("WORK_CLASS", "PERMIT_STATUS", "inspection_cycle", "avg_score", "trend", "critical_violations"):
        permits["layer_" + c.lower()] = permits.link_number.map(cur[c])
    permits["layer_inspections"] = permits.link_number.map(cur.inspections)
    codes = pd.read_csv(DATA / "cuisine_codes.csv", dtype=str)
    codes = codes[["link_number", "category", "cuisine_detail", "chain", "grocery_type", "confidence"]].rename(
        columns={"confidence": "category_confidence"})
    permits = permits.merge(codes, on="link_number", how="left")
    checks["permits"] = len(permits)
    checks["permits_without_code"] = int(permits.category.isna().sum())
    checks["establishments"] = int(permits.establishment_id.nunique())

    est = permits.sort_values("last").groupby("establishment_id").agg(
        name=("name", "last"), names=("name", lambda s: " | ".join(dict.fromkeys(s.dropna().astype(str)))),
        address=("address", "last"), permits=("link_number", lambda s: ";".join(s.dropna().astype(str))),
        first_inspection=("first", "min"), last_inspection=("last", "max"),
        inspections=("inspections", "sum"), category=("category", "last"), chain=("chain", "last"),
        grocery_type=("grocery_type", "last"), market_or_event_site=("market_or_event_site", "any"),
        current=("current_layer", lambda s: s.notna().any())).reset_index()

    pcols = ["establishment_id", "category", "cuisine_detail", "chain", "grocery_type", "category_confidence",
             "market_or_event_site"]
    ins = ins.merge(permits[["link_number"] + pcols], on="link_number", how="left")
    rev = pd.read_csv(HERE / "analysis" / "closures_reviewed.csv", dtype=str)
    closed = set(rev.loc[rev.classification == "closed or suspended at this inspection", "inspection_number"])
    ins["closure_recorded"] = ins.inspection_number.isin(closed)
    checks["closures_recorded"] = int(ins.closure_recorded.sum())

    # --- violations: the layer's scored items, plus items corrected on site from the reports ------
    v = ins[["inspection_number"] + ITEM_COLUMNS].melt("inspection_number", var_name="column", value_name="points_layer")
    v = v[v.points_layer > 0].copy()
    v["item_number"] = v.column.map(COL2N)
    v["corrected_on_site"] = False
    v["source"] = "layer"
    cos = ins.loc[ins.report_cos_items.fillna("") != "", ["inspection_number", "report_cos_items"]]
    cos = cos.assign(item_number=cos.report_cos_items.str.split(",")).explode("item_number")
    cos = cos.assign(item_number=cos.item_number.astype(int), points_layer=0, corrected_on_site=True,
                     source="report (corrected on site)")
    v = pd.concat([v.drop(columns="column"), cos.drop(columns="report_cos_items")], ignore_index=True)
    title = {n: t for n, _, t in ITEMS}
    v["item_title"] = v.item_number.map(title)
    v["item_category"] = v.item_number.map(category)
    v["points_std"] = v.item_number.map(form_points)
    rep_items = ins.set_index("inspection_number").report_repeat_items.dropna()
    rset = {k: {int(x) for x in s.split(",") if x} for k, s in rep_items.items()}
    has_rep = set(ins.loc[ins.has_report, "inspection_number"])
    v["repeat"] = [(n in rset.get(i, set())) if i in has_rep else None
                   for i, n in zip(v.inspection_number, v.item_number)]
    ctext = dict(zip(ins.inspection_number, cites))
    v["comment"] = [ctext[i].get(n, "") for i, n in zip(v.inspection_number, v.item_number)]
    v = v.merge(ins[["inspection_number", "inspection_date", "link_number", "establishment_id", "permitted_name",
                     "inspector", "purpose", "routine"]], on="inspection_number")
    v = v.sort_values(["inspection_date", "inspection_number", "item_number"])
    checks["violations"] = {"layer_rows": int((v.source == "layer").sum()),
                            "corrected_on_site_rows_from_reports": int((v.source != "layer").sum())}
    # cited in the notes but not scored in the layer: shown on the report as COS or IN
    unscored = sum(len(set(c) - {COL2N[col] for col in ITEM_COLUMNS if r[col] > 0})
                   for c, (_, r) in zip(cites, ins[ITEM_COLUMNS].iterrows()))
    checks["notes_cite_unscored_items"] = int(unscored)

    # --- write -----------------------------------------------------------------------------------
    DATA.mkdir(exist_ok=True)
    icols = ["inspection_number", "inspection_date", "date_source", "time_in", "time_out", "duration_min", "time_flag",
             "link_number", "establishment_id", "permitted_name", "main_address", "purpose", "purpose_of_inspection",
             "status", "exclude_reason", "routine", "inspector", "inspector_listed", "inspectors_listed",
             "inspector_signed", "score_std", "score_layer", "score_printed", "report_cos_points", "items_out",
             "priority_items", "closure_threshold", "at_or_over_closure_threshold", "closure_recorded",
             "unscored_writeup", "has_report", "report_files", "report_versions", "report_shown_on_own_permit",
             "report_cos_items", "report_repeat_items", "category", "cuisine_detail", "chain", "grocery_type",
             "category_confidence", "market_or_event_site", "layer_rows", "custom_comments"] + ITEM_COLUMNS
    ins["custom_comments"] = ins.custom_comments.map(redact)
    v["comment"] = v.comment.map(redact)
    ins.sort_values(["inspection_date", "time_in", "inspection_number"])[icols].to_csv(DATA / "inspections.csv",
                                                                                       index=False)
    v[["inspection_number", "inspection_date", "link_number", "establishment_id", "permitted_name", "inspector",
       "purpose", "routine", "item_number", "item_title", "item_category", "points_std", "points_layer",
       "corrected_on_site", "repeat", "source", "comment"]].to_csv(DATA / "violations.csv", index=False)
    permits.drop(columns=["nname"]).assign(first=permits["first"].dt.date, last=permits["last"].dt.date) \
        .to_csv(DATA / "permits.csv", index=False)
    est.assign(first_inspection=est.first_inspection.dt.date, last_inspection=est.last_inspection.dt.date) \
        .to_csv(DATA / "establishments.csv", index=False)
    rep.drop(columns=["printed_time_in"]).to_csv(DATA / "reports.csv", index=False)
    pd.DataFrame([{"item_number": n, "column": c, "title": t, "category": category(n), "form_points": form_points(n)}
                  for n, c, t in ITEMS]).to_csv(DATA / "items.csv", index=False)

    r = ins[ins.routine]
    checks["routine_inspections"] = int(len(r))
    checks["routine_by_year"] = {str(y): int(n) for y, n in r.inspection_date.str[:4].value_counts().sort_index().items()}
    checks["routine_at_or_over_closure_threshold"] = int(r.at_or_over_closure_threshold.sum())
    checks["routine_layer_score_30_plus"] = int((r.score_layer >= 30).sum())
    checks["excluded"] = {k: int(v) for k, v in ins.exclude_reason.value_counts().items() if k}
    checks["purpose"] = {k: int(v) for k, v in ins.purpose.value_counts().items()}
    checks["time_flags"] = {k: int(v) for k, v in ins.time_flag.value_counts().items() if k}
    (DATA / "validation.json").write_text(json.dumps(checks, indent=1, default=str))
    print(json.dumps({k: checks[k] for k in checks if not isinstance(checks[k], (list, dict)) or len(str(checks[k])) < 300},
                     indent=1, default=str))


if __name__ == "__main__":
    main()
