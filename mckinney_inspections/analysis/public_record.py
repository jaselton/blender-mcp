"""The public record: what McKinney residents can see, and how far the city's published record can be trusted.

Sections
  [a] where the published inspection reports sit on the map (own permit / same premises / another business),
      the record-number shift that causes it, and ten examples checked against the PDFs
  [b] the 388 copies of one e-mail attached to map records (described in general terms only)
  [c] the map's "Avg" number: a rounded-down mean of the last 4 printed scores, not the latest score
  [d] what the map's "Critical Violations" number counts
  [e] scoring defects in the city's inspections table: item 34 at 2 points, corrected-on-site items missing
  [f] the inspector named in the data versus the inspector who signed the report; a retroactive name change
  [g] duplicate inspection rows, and the city summary fields that count them
  [h] which inspections have a published report, by year
  [i] the city web page ("map temporarily unavailable") and the unlinked ArcGIS app (dates from ArcGIS metadata)

Inputs: data/inspections.csv, data/reports.csv, data/permits.csv, data/validation.json (through analysis/common.py
where possible), plus local copies already on disk: data/raw/*.json.gz (the map layers as pulled on 2026-09-26,
used for record numbers and popup-field types), data/raw/reports_parsed.jsonl.gz (signature names as printed),
the saved city web page and ArcGIS item JSON (EVIDENCE_DIR), and the downloaded report PDFs (PDF_CACHE, used
only to re-read the ten examples with pdftotext). Nothing here contacts the city's servers.

Private details: the e-mail copies in [b] are counted, never opened or quoted. The PDF check in [a] prints only
the permit number, date, establishment name and score it finds, never owner or contact fields.

Run from the repository folder:  python analysis/public_record.py     (a few seconds)
"""
import gzip
import html
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402  analysis/common.py

pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 45)
pd.set_option("display.max_rows", 200)

D = C.D
RAW = D / "raw"
SCRATCH = Path("/tmp/claude-0/-home-user-blender-mcp/22c7adfb-e8b7-5db8-b753-9071774cfd27/scratchpad")
EVIDENCE_DIR = Path(os.environ.get("MK_EVIDENCE_DIR", SCRATCH / "mk" / "acheck" / "web"))
PDF_CACHE = Path(os.environ.get("MK_PDF_CACHE", SCRATCH / "mk_cache" / "attachments"))
ORD_2023 = pd.Timestamp("2023-11-07")  # closure at 30 or more from this date (31 or more before)
APPID = "1f11251105aa491fbb23ace023b5b61f"
WEBMAP = "579478ffb5a340b2a2fca1f6979f0c3d"


def hdr(s):
    print("\n" + "=" * 110 + f"\n{s}\n" + "=" * 110)


def pct(a, b):
    return f"{a:,} of {b:,} ({100 * a / b:.1f}%)" if b else f"{a} of {b}"


def ms_date(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------------------------------------------- data
ins = C.ins.copy()
ins["ts"] = pd.to_datetime(ins.inspection_date.dt.strftime("%Y-%m-%d") + " " + ins.time_in.fillna("00:00"))
ins["best"] = ins.score_printed.fillna(ins.score_std)  # printed score where a report exists, else form-weight score
ok = ins.exclude_reason.isna()  # drops void, test and non-permit records
permits = C.permits.copy()
items = pd.read_csv(D / "items.csv")
COL = dict(zip(items.item_number, items.column))
TITLE = dict(zip(items.item_number, items.title))
val = json.loads((D / "validation.json").read_text())

rep = pd.read_csv(D / "reports.csv", dtype={"printed_permit": str, "attached_permit": str, "inspection_number": str})
isrep = rep.doc_type == "inspection_report"
rp = rep[isrep].copy()
rp["rdate"] = pd.to_datetime(rp.report_date)
rp["threshold"] = np.where(rp.rdate >= ORD_2023, 30, 31)
rp["fail"] = rp.printed_score >= rp.threshold

feats = pd.concat([pd.DataFrame(json.load(gzip.open(RAW / f"{n}.json.gz", "rt"))).assign(layer=n)
                   for n in ("permits", "trades_day_farmers_market", "schools_daycares")], ignore_index=True)
feats = feats.sort_values("OBJECTID").reset_index(drop=True)
OID = feats.groupby("link_number").OBJECTID.max()  # three school permits have two records; reports sit on the later
FNAME = feats.set_index("OBJECTID").description.str.split("\n").str[0].str.strip()

# each inspection's score history, by permit
hist = ins[ok].sort_values("ts").groupby("link_number")


def history(permit, n=None):
    h = hist.get_group(permit) if permit in hist.groups else ins.iloc[0:0]
    rows = [f"{d:%Y-%m-%d} {p.split(' ')[0].lower()[:7]} {b:.0f}{'' if hr else ' (no report)'}"
            f"{' [notes: closed/suspended]' if c else ''}"
            for d, p, b, hr, c in zip(h.inspection_date, h.purpose, h.best, h.has_report, h.closure_recorded)]
    return "; ".join(rows if n is None else rows[-n:])


# =========================================================================================== [a]
hdr("[a] Where the published reports sit on the map")
print("attachments on the map layers:", val["attachments"]["files"], "| inspection-report PDFs:", int(isrep.sum()),
      "| copies of one e-mail:", int((~isrep).sum()))
pc = rp.placement.value_counts()
n_rep = len(rp)
for k in ("own permit", "same premises, other permit", "other establishment"):
    print(f"  reports on {k:30s}: {pct(int(pc.get(k, 0)), n_rep)}")
print("  reports NOT on their own permit's map record:", pct(n_rep - int(pc.get('own permit', 0)), n_rep))

off = rp.objectid_offset.map(lambda x: "permit no longer on the map" if pd.isna(x) else f"{int(x):+d}")
print("\nShift = (record number of the report's own permit) - (record number it is attached to):")
print(off.value_counts().to_string())
within3 = rp.objectid_offset.between(-3, 3) & (rp.objectid_offset != 0)
print("  reports 1-3 records away from their own permit:", pct(int(within3.sum()), n_rep))

# what each map record displays
g = rp.groupby("REL_OBJECTID").placement.agg(lambda s: frozenset(s))


def classify(s):
    if not isinstance(s, frozenset):
        return "no reports"
    if s == {"own permit"}:
        return "only its own reports"
    if "own permit" in s:
        return "own and others' reports"
    if s == {"same premises, other permit"}:
        return "only a same-address neighbour's reports"
    return "only other businesses' reports"


feats["shows"] = feats.OBJECTID.map(g).map(classify)
print(f"\nMap records: {len(feats)} ({(feats.layer == 'permits').sum()} food permits, "
      f"{(feats.layer == 'schools_daycares').sum()} schools/daycares, {(feats.layer == 'trades_day_farmers_market').sum()} "
      "Trades Day/farmers market). What each record's pop-up attachment list shows:")
print(feats.shows.value_counts().to_string())
only_other = feats.shows.isin(["only other businesses' reports", "only a same-address neighbour's reports"])
print("  records showing only reports of OTHER permits:", pct(int(only_other.sum()), len(feats)))
newest = rp.sort_values("rdate").groupby("REL_OBJECTID").tail(1).set_index("REL_OBJECTID")
print("  records whose newest displayed report is their own:",
      pct(int((newest.placement == "own permit").sum()), len(newest)), "of records that show any report")
cur_permits = set(feats.link_number)
own_anywhere = set(rp.printed_permit) & cur_permits
own_on_own = set(rp.loc[rp.placement == "own permit", "printed_permit"])
print("  current permits with at least one report attached somewhere:", pct(len(own_anywhere), len(cur_permits)),
      "| with at least one on their own record:", len(own_on_own))

# --- cause: a record-number shift
hdr("[a2] Likely cause: reports are tied to record numbers, and the list was renumbered")
lk = feats.link_number.values
print("records numbered in permit-number order, newest permit first:",
      bool((lk[:-1] >= lk[1:]).all()), f"({len(feats)} records)")
hp = rp.groupby("REL_OBJECTID").printed_permit.nunique()
ph = rp.groupby("printed_permit").REL_OBJECTID.nunique()
print("records that carry reports:", len(hp), "| carrying reports of exactly one permit:", int((hp == 1).sum()))
print("permits with reports:", len(ph), "| all of whose reports sit on a single record:", int((ph == 1).sum()))
h = rp.groupby("REL_OBJECTID").printed_permit.agg(lambda s: s.value_counts().index[0]).rename("owner").reset_index()
h["own_oid"] = h.owner.map(OID)
h["shift"] = h.own_oid - h.REL_OBJECTID
seq = h.dropna(subset=["own_oid"]).sort_values("REL_OBJECTID")
seq = seq[seq["shift"].abs() <= 3]
print("order preserved (owners appear in the same order as the records they sit on):",
      bool((np.diff(seq.own_oid.values) > 0).all()))
owned = set(seq.own_oid.astype(int))
hosted = set(h.REL_OBJECTID)
changes, explained = [], 0
prev = None
for _, row in seq.iterrows():
    if prev is not None and row["shift"] != prev["shift"]:
        skipped = list(range(int(prev.own_oid) + 1, int(row.own_oid)))  # records passed over as owners
        between = list(range(int(prev.REL_OBJECTID) + 1, int(row.REL_OBJECTID)))  # records passed over as hosts
        up = row["shift"] - prev["shift"]
        why = []
        for o in skipped:
            why.append(f"record {o} ({FNAME[o]}, {OID.index[OID == o][0] if (OID == o).any() else feats.link_number[o - 1]})"
                       " has no reports of its own")
        for b in between:
            held = rp[rp.REL_OBJECTID == b]
            if len(held) and held.objectid_offset.isna().all():
                why.append(f"record {b} holds {len(held)} reports of {held.printed_permit.iloc[0]}, no longer on the map")
            elif len(held) and (held.objectid_offset.abs() > 3).all():
                why.append(f"record {b} holds a stray report (shift {int(held.objectid_offset.iloc[0])})")
            elif not len(held):
                why.append(f"record {b} (a second record for the same permit) holds no reports")
        ok_ = (up > 0 and len(skipped) - len(between) == up) or (up < 0 and len(between) - len(skipped) == -up)
        explained += ok_
        changes.append((int(prev.REL_OBJECTID), int(row.REL_OBJECTID), int(prev["shift"]), int(row["shift"]), "; ".join(why)))
    prev = row
print(f"\nThe shift changes size {len(changes)} times going down the list; each change matches records skipped "
      f"(no reports of their own) or reports of a permit that has left the map: {explained} of {len(changes)}")
for c in changes:
    print(f"  records {c[0]}->{c[1]}: shift {c[2]:+d} -> {c[3]:+d}: {c[4]}")
fi = ins[ok].groupby("link_number").inspection_date.min()
newest_rep = rp.rdate.max()
print("\nnewest attached report dated:", f"{newest_rep:%Y-%m-%d}", "| PDF creation max:", rp.pdf_created.max()[:10],
      "| newest inspection in the data:", f"{ins.inspection_date.max():%Y-%m-%d}")
new = feats[~feats.link_number.isin(set(rp.printed_permit)) & (feats.link_number.map(fi) > newest_rep)]
for _, r_ in new.iterrows():
    print(f"  added after the reports were attached: record {r_.OBJECTID} {r_.link_number} {FNAME[r_.OBJECTID]}"
          f" (first inspected {fi[r_.link_number]:%Y-%m-%d}; has no report of its own; its record displays "
          f"{int((rep.REL_OBJECTID == r_.OBJECTID).sum())} report(s) of the next permit down)")
gone = rp[rp.objectid_offset.isna()]
print(f"reports of permits no longer on the map, displayed on other records: {len(gone)} reports, "
      f"{gone.printed_permit.nunique()} permits")
print("inspections dated after the newest attached report:",
      int((ins[ok].inspection_date > newest_rep).sum()), "| of which with a published report:",
      int(((ins[ok].inspection_date > newest_rep) & ins[ok].has_report).sum()))

# --- failing / high-scoring reports displayed elsewhere
hdr("[a3] Failing reports shown on other businesses' records, and the reverse")
print("threshold used: at or over the closure threshold in force on the report date (31+ before 2023-11-07, 30+ after)")
print("reports at or over the threshold:", int(rp.fail.sum()), "| placement:", rp[rp.fail].placement.value_counts().to_dict())
print("reports scoring 20 or more:", int((rp.printed_score >= 20).sum()), "| placement:",
      rp[rp.printed_score >= 20].placement.value_counts().to_dict())
own_max = ins[ok].groupby("link_number").best.max()
own_n = ins[ok].groupby("link_number").size()
f = rp[rp.fail & (rp.placement != "own permit")].copy()
f["host_name"] = f.REL_OBJECTID.map(FNAME)
f["host_own_max"] = f.attached_permit.map(own_max)
f["host_own_n"] = f.attached_permit.map(own_n)
print(f[["REL_OBJECTID", "host_name", "attached_permit", "host_own_n", "host_own_max", "printed_permit", "printed_name",
         "report_date", "printed_score"]].sort_values("host_own_max").to_string(index=False))
clean = f[f.host_own_max <= 5]
print(f"records whose OWN inspections never scored above 5 but which display a failing report: "
      f"{clean.REL_OBJECTID.nunique()} records ({len(clean)} reports)")
fp = rp[rp.fail].groupby("printed_permit").agg(name=("printed_name", "last"), fail_date=("report_date", "max"),
                                               fail_score=("printed_score", "max"))
fp["own_record"] = fp.index.map(OID)
shown = rp.groupby("REL_OBJECTID").agg(shown_permits=("printed_permit", lambda s: ",".join(sorted(set(s)))),
                                       shown_max=("printed_score", "max"), shown_n=("printed_score", "size"))
fp = fp.join(shown, on="own_record")
fp["own_record_shows_own"] = [isinstance(s, str) and p in s for p, s in zip(fp.index, fp.shown_permits)]
print("\npermits with a failing report:", len(fp), "| whose own map record shows none of their own reports:",
      int((~fp.own_record_shows_own).sum()))
print(fp.to_string())
rev = fp[~fp.own_record_shows_own & (fp.shown_max <= 14)]
print("of these, own record displays only another business's reports all scoring 14 or less:", len(rev))

# --- ten examples, checked against the PDFs
hdr("[a4] Ten examples (what the pop-up for a business shows), re-read from the PDFs with pdftotext")
EXAMPLES = [  # (kind, permit of the business whose map record is clicked)
    ("clean record shows a failing neighbour's report", "HEALTH2025-00004"),  # McDonald's #5011 -> Tantra
    ("clean record shows a failing neighbour's report", "HEALTH2025-00012"),  # Bresnan Bread -> Thai Noodle Wave
    ("clean record shows a failing neighbour's report", "HEALTH2025-00087"),  # Dollar General -> Rivera's
    ("clean record shows a failing neighbour's report", "HEALTH2024-00191"),  # Taco Cabana #240 -> F&F Japanese Grill
    ("clean record shows a failing neighbour's report", "HEALTH2024-00065"),  # Waffle House -> ZaZa Thai
    ("failing business's record shows a clean neighbour's reports", "HEALTH2025-00001"),  # Tantra -> Harvest
    ("failing business's record shows a clean neighbour's reports", "HEALTH2025-00011"),  # Thai Noodle Wave -> Domino's
    ("failing business's record shows a clean neighbour's reports", "HEALTH2025-00086"),  # Rivera's -> Olive Garden
    ("failing business's record shows a clean neighbour's reports", "HEALTH2024-00296"),  # Donut Town -> CVS
    ("failing business's record shows a clean neighbour's reports", "HEALTH2024-00642"),  # Kyoto Hibachi -> McDonald's
]
have_pdf = PDF_CACHE.is_dir() and shutil.which("pdftotext")
print("PDF cache:", PDF_CACHE if have_pdf else "not available (checks below use the parsed report headers only)")


def pdf_check(rel, att, permit, date, name, score):
    """Re-read one PDF: does its text carry the printed permit, date, name and score? (prints nothing private)"""
    if not have_pdf:
        return "not re-read"
    p = PDF_CACHE / f"{rel}_{att}.pdf"
    if not p.exists():
        return "PDF missing"
    t = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True).stdout
    m = re.search(r"(\d\d/\d\d/\d{4}).{0,120}?\b(HEALTH\d{4}-\d+)", t, re.S)
    d_ok = bool(m) and pd.to_datetime(m.group(1), format="%m/%d/%Y").strftime("%Y-%m-%d") == date
    p_ok = bool(m) and m.group(2) == permit
    if not isinstance(name, str):
        n_ok = "blank on form"
    else:
        n_ok = "OK" if name.split("\n")[0].strip().upper()[:12] in t.upper() else "X"
    s_ok = any(line.strip() == str(int(score)) for line in t.splitlines())
    return f"PDF text: permit {'OK' if p_ok else 'X'}, date {'OK' if d_ok else 'X'}, name {n_ok}, " \
           f"score {'OK' if s_ok else 'X'}"


n_checked = n_all_ok = 0
for i, (kind, permit) in enumerate(EXAMPLES, 1):
    rec = int(OID[permit])
    print(f"\n{i}. [{kind}] {FNAME[rec]} ({permit}), map record {rec}")
    print(f"   its own inspections: {history(permit)}")
    ownrep = rp[rp.printed_permit == permit]
    where = ", ".join(f"record {r} ({FNAME.get(r, '?')})" for r in sorted(set(ownrep.REL_OBJECTID)))
    print(f"   its own {len(ownrep)} report(s) are attached to: {where}")
    disp = rp[rp.REL_OBJECTID == rec].sort_values("rdate")
    print(f"   its pop-up shows {len(disp)} report(s):")
    for _, r_ in disp.iterrows():
        chk = pdf_check(r_.REL_OBJECTID, r_.ATTACHMENTID, r_.printed_permit, r_.report_date, r_.printed_name,
                        r_.printed_score)
        n_checked += 1
        n_all_ok += chk.count("OK") + chk.count("blank on form") == 4 and " X" not in chk
        print(f"     attachment {r_.ATTACHMENTID}: {str(r_.printed_name).splitlines()[0] if isinstance(r_.printed_name, str) else '(name blank on form)'}"
              f" {r_.printed_permit}, {r_.report_date}, score {r_.printed_score:.0f} "
              f"[{r_.placement}; shift {r_.objectid_offset:+.0f}] -- {chk}")
    if disp.printed_permit.nunique() == 1:
        other = disp.printed_permit.iloc[0]
        print(f"   that business ({other}) inspections: {history(other)}")
print(f"\nexample PDFs re-read: {n_checked}; permit, date, score (and name, where the form has one) all found in the "
      f"PDF text: {n_all_ok}")

# =========================================================================================== [b]
hdr("[b] The e-mail copies (counted only; contents not opened or described)")
meta = json.loads((RAW / "fetch_meta.json").read_text())
print("pulled from the city's public map service", meta["service"], "at", meta["fetched_at"],
      "| attachment files downloaded through the public attachment endpoint:", meta["attachments"]["downloaded"])
em = rep[~isrep]
print("files:", len(em), "| distinct files (SHA-256):", em.sha256.nunique(),
      "| validation.json distinct_email_files:", val["attachments"]["distinct_email_files"])
print("file type(s):", em.doc_type.value_counts().to_dict(), "| file name(s) distinct:", em.ATT_NAME.nunique())
per = em.groupby("REL_OBJECTID").size()
print("map records carrying copies:", len(per), f"| record numbers {per.index.min()}-{per.index.max()}",
      f"| copies per record: min {per.min()}, median {per.median():.0f}, max {per.max()}")
print("records with 10 or more copies:", int((per >= 10).sum()))
emf = feats[feats.OBJECTID.isin(per.index)]
print("layers:", emf.layer.value_counts().to_dict(), "| of those records, also showing inspection reports:",
      int(emf.OBJECTID.isin(set(rp.REL_OBJECTID)).sum()))
cat = emf.link_number.map(permits.drop_duplicates("link_number").set_index("link_number").category)
print("our category of the businesses whose records carry copies:",
      cat.map(lambda c: "restaurant" if isinstance(c, str) and c not in C.NON_RESTAURANT else c).value_counts().to_dict())

# =========================================================================================== [c]
hdr("[c] The map's 'Avg' number (avg_score)")
cur = permits[permits.layer_avg_score.notna()].copy()
cur["avg"] = pd.to_numeric(cur.layer_avg_score)
rows = ins.loc[ins.index.repeat(ins.layer_rows)]  # the city's table as published, duplicates included
last4 = rows.sort_values(["ts", "inspection_number"], ascending=False).groupby("link_number").head(4)
agg = last4.groupby("link_number").agg(n4=("best", "size"), mean4=("best", "mean"), layer_mean4=("score_layer", "mean"),
                                       span=("ts", lambda x: (x.max() - x.min()).days))
dd = ins.sort_values(["ts", "inspection_number"], ascending=False).groupby("link_number").head(4)
agg["mean4_dedup"] = dd.groupby("link_number").best.mean()
m = cur.merge(agg, left_on="link_number", right_index=True, how="left")
m["floor"] = np.floor(m.mean4)
fit = int((m.avg == m["floor"]).sum())
print("current permits with an avg_score:", len(m), "| layers:", m.current_layer.value_counts().to_dict())
print("avg_score == floor(mean of printed scores, last 4 rows incl. duplicates):", pct(fit, len(m)))
print("   same with ordinary rounding:", pct(int((m.avg == np.floor(m.mean4 + 0.5)).sum()), len(m)))
print("   same using the layer's own total_score:", pct(int((m.avg == np.floor(m.layer_mean4)).sum()), len(m)))
print("   same with duplicate rows removed:", pct(int((m.avg == np.floor(m.mean4_dedup)).sum()), len(m)))
print("   'inspections' field == number of rows in the window (max 4):",
      pct(int((pd.to_numeric(m.layer_inspections) == m.n4).sum()), len(m)))
print("misfits:", m.loc[m.avg != m["floor"], ["link_number", "name", "avg", "mean4"]].to_string(index=False))
print("\nrows averaged:", m.n4.value_counts().sort_index().to_dict(),
      f"| days between oldest and newest inspection averaged: median {m.span.median():.0f}, max {m.span.max():.0f}")
ins["days_since_prev"] = ins[ok].sort_values("ts").groupby("link_number").ts.diff().dt.days
l4x = ins.loc[ins.index.repeat(ins.layer_rows)].sort_values(["ts", "inspection_number"], ascending=False) \
    .groupby("link_number").head(4)
fu = l4x.groupby("link_number").apply(lambda g_: pd.Series({
    "nonroutine": bool((~g_.purpose.isin(["Routine", "Unrecorded"])).any()),
    "quick_return": bool((g_.days_since_prev <= 14).any())}), include_groups=False)
m["window_has_followup"] = m.link_number.map(fu.nonroutine | fu.quick_return).fillna(False).astype(bool)
print("windows that average in a follow-up (purpose not routine, or a visit within 14 days of the previous one):",
      pct(int(m.window_has_followup.sum()), len(m)))
frac = m.mean4 - m["floor"]
print("shown one point lower than ordinary rounding would give (fraction >= .5):", pct(int((frac >= 0.5).sum()), len(m)))
print("avg_score stored as:", [f["type"] for f in json.load(open(RAW / "permits.schema.json"))["fields"]
                               if f["name"] == "avg_score"][0])

lr = ins[ins.routine].sort_values(["ts", "inspection_number"]).groupby("link_number").tail(1).set_index("link_number")
la = ins[ok].sort_values(["ts", "inspection_number"]).groupby("link_number").tail(1).set_index("link_number")
m["latest_routine"] = m.link_number.map(lr.best)
m["latest_routine_date"] = m.link_number.map(lr.inspection_date)
m["latest_routine_printed"] = m.link_number.map(lr.has_report)
m["latest_any"] = m.link_number.map(la.best)
print("\nlatest routine score taken from the printed report:", pct(int(m.latest_routine_printed.sum()), len(m)),
      "(else the layer's items at form weights, a lower bound)")
for col, lab in (("latest_routine", "most recent ROUTINE inspection"), ("latest_any", "most recent inspection of any kind")):
    gap = m[col] - m.avg
    print(f"vs {lab}: map number LOWER (looks better) {pct(int((gap > 0).sum()), len(m))}; "
          f"equal {int((gap == 0).sum())}; HIGHER (looks worse) {int((gap < 0).sum())}; "
          f"lower by 5+ {int((gap >= 5).sum())}; by 10+ {int((gap >= 10).sum())}")
gap = m.latest_routine - m.avg
print(f"when lower: median gap {gap[gap > 0].median():.0f} points, mean {gap[gap > 0].mean():.1f}, max {gap.max():.0f}")
print(f"when higher: median gap {(-gap[gap < 0]).median():.0f} points, max {(-gap).max():.0f}")
mr = m[m.category.notna() & ~m.category.isin(C.NON_RESTAURANT)]
gr = mr.latest_routine - mr.avg
print(f"restaurants only (our categories): {len(mr)}; lower {int((gr > 0).sum())}, 5+ {int((gr >= 5).sum())}, "
      f"10+ {int((gr >= 10).sum())}")
print("latest routine inspection at/over 30:", int((m.latest_routine >= 30).sum()),
      "| of which map number under 30:", int(((m.latest_routine >= 30) & (m.avg < 30)).sum()))
print("\nlargest gaps (map number vs most recent routine score):")
top = m.assign(gap=gap).sort_values(["gap", "avg"], ascending=[False, True]).head(12)
for _, r_ in top.iterrows():
    print(f"  {r_['name']} ({r_.link_number}): map 'Avg' {r_.avg:.0f}; latest routine {r_.latest_routine:.0f} on "
          f"{r_.latest_routine_date:%Y-%m-%d}; rows averaged {r_.n4}; recent inspections: {history(r_.link_number, 5)}")

# =========================================================================================== [d]
hdr("[d] What the map's 'Critical Violations, Last 4 Inspections' counts")
cos = ins.report_cos_items.fillna("").map(lambda s: [int(x) for x in str(s).split(",") if x])
for lab, lo, hi in (("p", 1, 20), ("pf", 21, 33), ("core", 34, 47)):
    ins[lab + "_n"] = ins[[COL[n] for n in range(lo, hi + 1)]].gt(0).sum(axis=1) + cos.map(
        lambda l, lo=lo, hi=hi: sum(lo <= n <= hi for n in l))
ins["p_layer"] = ins[[COL[n] for n in range(1, 21)]].gt(0).sum(axis=1)
ins["pf_layer"] = ins[[COL[n] for n in range(21, 34)]].gt(0).sum(axis=1)
rows = ins.loc[ins.index.repeat(ins.layer_rows)]
l4 = rows.sort_values(["ts", "inspection_number"], ascending=False).groupby("link_number").head(4)
cv = l4.groupby("link_number")[["p_n", "pf_n", "core_n", "p_layer", "pf_layer"]].sum()
mc = cur.merge(cv, left_on="link_number", right_index=True)
mc["crit"] = pd.to_numeric(mc.layer_critical_violations)
for lab, v in (("Priority + Priority Foundation items (1-33), incl. corrected on site", mc.p_n + mc.pf_n),
               ("Priority items only (1-20)", mc.p_n),
               ("items 1-33 from the layer only (no corrected-on-site)", mc.p_layer + mc.pf_layer),
               ("all items", mc.p_n + mc.pf_n + mc.core_n)):
    print(f"critical_violations == {lab}: {pct(int((mc.crit == v).sum()), len(mc))}")
print("\nacross the", len(mc), "records: Priority (3-point) items counted", int(mc.p_n.sum()),
      "| Priority Foundation (2-point) items counted", int(mc.pf_n.sum()))
print("records where Priority Foundation items outnumber Priority items:", int((mc.pf_n > mc.p_n).sum()))
print("records showing a non-zero 'critical' count with NO Priority item in the window:",
      int(((mc.p_n == 0) & (mc.crit > 0)).sum()), "| records showing 0:", int((mc.crit == 0).sum()))
pf_freq = ins[ok][[COL[n] for n in range(21, 34)]].gt(0).sum().sort_values(ascending=False)
print("most frequent Priority Foundation items in the layer (inspections):")
for c, v in pf_freq.head(6).items():
    n = [k for k, x in COL.items() if x == c][0]
    print(f"  item {n}: {TITLE[n]}: {v}")

# =========================================================================================== [e]
hdr("[e] Scoring defects in the city's inspections table")
i34 = ins.no_insect_contamination > 0
print("item 34 (insects/rodents) values in the layer:", val["item34_layer_values"], "(rows, duplicates included)")
print("inspections with item 34 marked:", int(i34.sum()), "| non-excluded:", int((i34 & ok).sum()),
      "| routine:", int((i34 & ins.routine).sum()), "| all at 2 points:",
      bool((ins.no_insect_contamination[i34] == 2).all()))
print("check: layer score == form-weight score + 1 for each of them:", val["score_std_equals_layer_minus_item34"])
R_ = ins[ins.routine]
a, b = R_.score_layer >= 30, R_.score_std >= 30
print(f"routine inspections scored 30+: layer {int(a.sum())}, with item 34 at 1 point {int(b.sum())}; "
      f"differ {int((a & ~b).sum())} (all item-34 cases: {int((a & ~b & (R_.no_insect_contamination > 0)).sum())})")
flip = R_[a & ~b]
print("  their layer score / corrected score:", flip.score_layer.value_counts().to_dict(), "/",
      flip.score_std.value_counts().to_dict(), "| with a printed report:", int(flip.has_report.sum()),
      "| printed scores:", flip.score_printed.dropna().astype(int).tolist())
print(flip[["inspection_date", "permitted_name", "score_layer", "score_std", "score_printed"]].to_string(index=False))
at, bt = R_.score_layer >= R_.closure_threshold, R_.at_or_over_closure_threshold
print(f"at/over the closure threshold in force (31 before 2023-11-07, 30 after): layer {int(at.sum())}, "
      f"corrected {int(bt.sum())}, differ {int((at & ~bt).sum())}")

H = ins[ins.has_report]
cp = H.report_cos_points.fillna(0)
print(f"\ninspections matched to a printed report: {len(H)}; printed == corrected layer score + corrected-on-site "
      f"points: {int((H.score_printed == H.score_std + cp).sum())}")
print("layer total_score vs printed score: equal", int((H.score_layer == H.score_printed).sum()),
      "| layer higher", int((H.score_layer > H.score_printed).sum()), "| layer lower",
      int((H.score_layer < H.score_printed).sum()))
print("inspections with items corrected on site (missing from the layer):", pct(int((cp > 0).sum()), len(H)),
      f"| points missing: total {cp.sum():.0f}, median {cp[cp > 0].median():.0f}, max {cp.max():.0f}")
print("  points missing per inspection:", cp[cp > 0].astype(int).value_counts().sort_index().to_dict())
print("  inspections where the missing points would lift the score to 30+:",
      int(((H.score_std < 30) & (H.score_printed >= 30)).sum()))
print("  COS items as rows in violations.csv:", val["violations"]["corrected_on_site_rows_from_reports"])
yy = ins[ok].groupby("year").agg(inspections=("has_report", "size"), with_report=("has_report", "sum"))
yy["with_COS"] = H.groupby("year").report_cos_points.apply(lambda s: int((s > 0).sum()))
print("which years can be checked (inspections / with a printed report / with corrected-on-site items):")
print(yy.fillna(0).astype(int).to_string())
pre = H[H.year < 2024]
print("pre-2024 reports by our category:", pre.category.value_counts().head(5).to_dict())

# =========================================================================================== [f]
hdr("[f] Inspector named in the data vs inspector who signed the report")
signed = ins.has_report & ins.inspector_signed.notna() & ~ins.inspectors_listed.fillna("").str.contains(";")
diff = signed & (ins.inspector_signed != ins.inspector_listed)
print("signed reports matched to an inspection (one listed name):", int(signed.sum()), "| names differ:",
      pct(int(diff.sum()), int(signed.sum())), "| validation.json:", val["inspector_listed_vs_signed"])
by = pd.DataFrame({"signed": ins[signed].year.value_counts(), "differ": ins[diff].year.value_counts()}).sort_index()
print(by.fillna(0).astype(int).T.to_string())
x = ins[signed]
tab = pd.DataFrame({"listed": x.inspector_listed.value_counts(),
                    "signed_by_listed": x[x.inspector_signed == x.inspector_listed].inspector_listed.value_counts(),
                    "signed_anywhere": x.inspector_signed.value_counts()})
print(tab.fillna(0).astype(int).sort_values("listed", ascending=False).to_string())
vg = x[x.inspector_listed == "Victoria Goldston"]
print("inspections listing Victoria Goldston with a signed report:", len(vg), "| signed by her:",
      int((vg.inspector_signed == "Victoria Goldston").sum()), "| signed by others:",
      vg.loc[vg.inspector_signed != "Victoria Goldston", "inspector_signed"].value_counts().to_dict())

sig = pd.DataFrame([{"ATTACHMENTID": int(d_["ATTACHMENTID"]), "sig": d_.get("inspector")}
                    for d_ in map(json.loads, gzip.open(RAW / "reports_parsed.jsonl.gz", "rt"))
                    if d_["doc_type"] == "inspection_report"])
sg = rp.merge(sig, on="ATTACHMENTID")
prior = sg[sg.sig.fillna("").str.startswith("Schweitzer")]
now = sg[sg.sig.fillna("").str.startswith("Goldston")]
print("\nreports signed with the earlier surname:", len(prior), "files,", prior.inspection_number.nunique(),
      f"inspections, dated {prior.report_date.min()} to {prior.report_date.max()}")
print("reports signed with the current surname:", len(now), "files,", f"dated {now.report_date.min()} to {now.report_date.max()}")
print("name listed in the city data for the earlier-surname inspections:",
      ins.set_index("inspection_number").loc[prior.inspection_number.dropna().unique(), "inspector_listed"]
      .value_counts().to_dict())
print("earlier surname anywhere in the city's inspector field:",
      int(ins.inspectors_listed.fillna("").str.contains("Schweitzer").sum()))
vgl = ins[ins.inspector_listed == "Victoria Goldston"]
print("inspections listing the current name:", len(vgl), f"dated {vgl.inspection_date.min():%Y-%m-%d} to "
      f"{vgl.inspection_date.max():%Y-%m-%d}; before {prior.report_date.max()}: "
      f"{int((vgl.inspection_date <= pd.Timestamp(prior.report_date.max())).sum())}")

# =========================================================================================== [g]
hdr("[g] Duplicate inspection rows and the city's summary fields")
print("rows in the city's inspections table:", val["layer_rows"], "| distinct inspections:", val["inspections"],
      "| copies identical in content:", val["duplicate_rows_identical_content"])
dup = ins[ins.layer_rows > 1]
print("inspections repeated:", len(dup), "| copies per repeated inspection:", dup.layer_rows.value_counts().sort_index().to_dict(),
      "| extra rows:", int((dup.layer_rows - 1).sum()))
print("repeated inspections by year:", dup.year.value_counts().sort_index().astype(int).to_dict())
w = last4.groupby("link_number").inspection_number.agg(lambda s: s.duplicated().any())
m["dup_in_window"] = m.link_number.map(w)
print("current records whose last-4 window counts an inspection twice:", int(m.dup_in_window.sum()))
chg = m.dup_in_window & (np.floor(m.mean4) != np.floor(m.mean4_dedup))
print("  of these, 'Avg' differs from the de-duplicated average:", int(chg.sum()),
      "| higher:", int((chg & (m.mean4 > m.mean4_dedup)).sum()), "| lower:", int((chg & (m.mean4 < m.mean4_dedup)).sum()))
dd4 = ins.sort_values(["ts", "inspection_number"], ascending=False).groupby("link_number").head(4)
cvd = dd4.groupby("link_number")[["p_n", "pf_n"]].sum().sum(axis=1)
mc["crit_dedup"] = mc.link_number.map(cvd)
print("  'Critical Violations' differs from the de-duplicated count:", int((mc.crit != mc.crit_dedup).sum()))
nrow = ins.groupby("link_number").layer_rows.sum()
ndist = ins.groupby("link_number").size()
m["rows_all"] = m.link_number.map(nrow)
m["dist_all"] = m.link_number.map(ndist)
print("current records whose related-inspection count (pop-up 'Total Inspections', a count of related rows) "
      "includes duplicates:", int((m.rows_all > m.dist_all).sum()))
print(m.loc[chg, ["name", "link_number", "avg", "mean4", "mean4_dedup"]].head(8).to_string(index=False))

# =========================================================================================== [h]
hdr("[h] Which inspections have a published report")
k = ins[ok].copy()
k["on_map"] = k.link_number.isin(cur_permits)
cov = k.groupby("year").agg(inspections=("has_report", "size"), published=("has_report", "sum"))
cov["pct"] = (100 * cov.published / cov.inspections).round(1)
cm = k[k.on_map].groupby("year").agg(onmap_insp=("has_report", "size"), onmap_pub=("has_report", "sum"))
co = k[~k.on_map].groupby("year").agg(offmap_insp=("has_report", "size"), offmap_pub=("has_report", "sum"))
print(cov.join(cm).join(co).fillna(0).astype({"inspections": int, "published": int}).to_string())
print("total:", pct(int(k.has_report.sum()), len(k)), "| permits on the map:",
      pct(int(k[k.on_map].has_report.sum()), int(k.on_map.sum())), "| permits no longer on the map:",
      pct(int(k[~k.on_map].has_report.sum()), int((~k.on_map).sum())))
print("published reports shown on their own record:", pct(int(k.report_shown_on_own_permit.fillna(False).astype(bool).sum()),
                                                         int(k.has_report.sum())))
pre = k[(k.year < 2024) & k.has_report]
print("pre-2024 published: ", len(pre), "| their permits' first inspection year:",
      pre.link_number.map(fi).dt.year.value_counts().sort_index().to_dict(), "| our categories:",
      pre.category.value_counts().head(4).to_dict())
print("current permits by permit-number year:", permits[permits.current_layer.notna()].link_number.str[6:10]
      .value_counts().sort_index().to_dict())
ce = set(permits.loc[permits.current_layer.notna(), "establishment_id"])
ke = k[k.establishment_id.isin(ce)]
first_cur = k[k.on_map].groupby("establishment_id").inspection_date.min()
first_all = ke.groupby("establishment_id").inspection_date.min()
print("establishments now on the map:", ke.establishment_id.nunique(), "| with inspections under an earlier permit number:",
      int((first_all < first_cur.reindex(first_all.index)).sum()))
print("their inspections since 2017:", pct(int(ke.has_report.sum()), len(ke)), "have a published report")
Rm = C.R[C.R.establishment_id.isin(ce)]
print("restaurants now on the map (common.R):", Rm.establishment_id.nunique(), "| routine inspections since 2017 with a report:",
      pct(int(Rm.has_report.sum()), len(Rm)), "| before 2024:",
      pct(int(Rm[Rm.year < 2024].has_report.sum()), int((Rm.year < 2024).sum())))
print("inspections Aug 26 - Sep 1, 2026 (after the newest attached report):", int((k.inspection_date > newest_rep).sum()),
      "| published:", int(k[k.inspection_date > newest_rep].has_report.sum()))

# =========================================================================================== [i]
hdr("[i] The city page and the ArcGIS app")
if not EVIDENCE_DIR.is_dir():
    print("evidence folder not found:", EVIDENCE_DIR)
else:
    page = (EVIDENCE_DIR / "rs_today.html").read_text(encoding="utf-8", errors="replace")
    fetched = datetime.fromtimestamp((EVIDENCE_DIR / "rs_today.html").stat().st_mtime, tz=timezone.utc)
    txt = html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", page)))
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    print(f"Restaurant Scores page saved {fetched:%Y-%m-%d %H:%M} UTC; lines quoted:")
    for kw in ("temporarily unavailable", "Temporarily Unavailable", "score beside each restaurant",
               "current and previous score", "available to view/download", "more than 30 demerits", "presence of insects"):
        for ln in lines:
            if kw in ln:
                print("   >", ln[:260])
                break
    print("page mentions the app id:", APPID in page, "| any ArcGIS app URL (apps/instant, experience, webappviewer):",
          bool(re.search(r"apps/instant|experience\.arcgis|webappviewer|appid=", page, re.I)),
          "| embedded frames other than Google Tag Manager:",
          len(re.findall(r'<iframe(?![^>]*googletagmanager)', page, re.I)), "| <table> tags:", len(re.findall(r"<table", page, re.I)))
    print("links to ArcGIS/GIS sites on the page:", sorted(set(re.findall(r'href="([^"]*(?:arcgis|gis)[^"]*)"', page, re.I))))
    app = json.loads((EVIDENCE_DIR / f"item_{APPID}.json").read_text())
    appd = json.loads((EVIDENCE_DIR / f"data_{APPID}.json").read_text())
    wm = json.loads((EVIDENCE_DIR / f"item_{WEBMAP}.json").read_text())
    v = appd["values"]
    print(f"\nArcGIS app '{v['title']}' (item title '{app['title']}', type {app['type']}, owner {app['owner']}, "
          f"access {app['access']})")
    print(f"   created {ms_date(app['created'])}; published (app setting datePublished) {ms_date(v['datePublished'])}; "
          f"last modified {ms_date(app['modified'])}; views {app['numViews']:,}; last viewed {ms_date(app['lastViewed'])}")
    print(f"   url: {app['url']}")
    print(f"   built on web map '{wm['title']}' ({WEBMAP}): created {ms_date(wm['created'])}, modified "
          f"{ms_date(wm['modified'])}, views {wm['numViews']:,}")
    pt = v["searchConfiguration"]["sources"][0]["popupTemplate"]
    print("   pop-up title template:", pt.get("title") if pt.get("title") else "(see web map)")
    wmd = json.loads((EVIDENCE_DIR / f"data_{WEBMAP}.json").read_text())
    for lyr in wmd["operationalLayers"]:
        pi = lyr.get("popupInfo") or {}
        vis = [f"{f_['fieldName']} = '{f_.get('label')}'" for f_ in pi.get("fieldInfos", []) if f_.get("visible")]
        print(f"   layer '{lyr['title']}' (on at start: {lyr.get('visibility')}): title '{pi.get('title')}'; "
              f"attachments shown: {pi.get('showAttachments')}")
        if lyr["title"] == "Food Permits":
            print("      visible fields:", "; ".join(vis))
    splash = html.unescape(re.sub(r"<[^>]+>", " ", v.get("splashContent", "")))
    print("   splash text:", re.sub(r"\s+", " ", splash).strip()[:200])
    items_ = json.loads((EVIDENCE_DIR / "gis_mckinney_items.json").read_text())
    food = [it for it in items_ if re.search(r"food|restaurant", (it.get("title") or "") + " " + " ".join(it.get("tags") or []), re.I)
            and it["type"] in ("Web Mapping Application", "Web Experience", "Dashboard", "StoryMap")]
    print(f"   city GIS items listed: {len(items_)}; food/restaurant apps among them: "
          f"{[(it['id'], it['title'], it['type']) for it in food]}")
    print("   days from app publication to page fetch:", (fetched.date() - pd.Timestamp(ms_date(v["datePublished"])).date()).days)

print("\ndone")
