"""Closures and enforcement in McKinney's published food-inspection records, Oct 2017 - Sep 2026.

Closures exist only in the inspectors' free-text notes (custom_comments). Every note that mentions a
closure, suspension or reopening was read and classified by hand in closures_reviewed.csv; build.py turns
the "closed or suspended at this inspection" rows into inspections.closure_recorded (91 inspections).
This script counts those closures, follows what the records show happened next, and compares them with the
scores: the closure threshold in force on the inspection date (31 demerits or more under Ord. 2019-10-072,
30 or more under Ord. 2023-11-075 from Nov 7, 2023) against score_std.

Scores
  score_std     layer items at the printed form's weights (item 34 at 1 point). The layer stores items
                corrected on site (COS) as 0, so score_std is a LOWER BOUND on the official score.
  score_layer   the city's own total: item 34 (insects/rodents) at 2 points instead of 1.
  score_printed the total on the report PDF (score_std + COS points); mostly 2024-2026.

"Premises" joins establishment_id chains that build.py left apart when the address gained a suite number
(e.g. RIVERA'S SALVA TEX-MEX, 1321 TENNESSEE ST -> 1321 N TENNESSEE ST 100): two establishment_ids are
merged when their normalized address (house number + first two street words, directions and suites
dropped) and the first word of the name agree AND one's inspections end before the other's begin
(so a store's deli and bakery, inspected side by side, stay apart).

Sections
  [0] the reviewed file                      [5] item 34 and the threshold in the city's own data
  [1] closures by reason, year and inspector [6] repeat closures at the same premises (Rivera's)
  [2] what the records show after a closure  [7] the same hazard handled differently: no hot water
  [3] at or over the threshold, no closure   [8] every quote cited here, checked against the notes
  [4] the 31-to-30 change and how notes quote the rule

Everything here is a pattern in published records. A closure that was ordered but never written into the
notes is invisible to this analysis; so is a reinspection that was done but not entered in the layer.

Run from this folder:  python closures.py      (pandas, numpy; a few seconds)
"""
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import R, ins  # noqa: E402

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 400)
pd.set_option("display.max_colwidth", 70)
HERE = Path(__file__).resolve().parent

ORD_2019 = pd.Timestamp("2019-10-15")  # Ord. 2019-10-072: closure at 31 or more
ORD_2023 = pd.Timestamp("2023-11-07")  # Ord. 2023-11-075: closure at 30 or more
CLOSED = "closed or suspended at this inspection"
REOPEN = "approved to reopen (reinspection)"
THREAT = "closure threatened (e.g. unpaid or expired permit)"


def head(s):
    print(f"\n{'=' * 100}\n{s}\n{'=' * 100}")


def norm(s):
    return re.sub(r"\s+", " ", str(s)).strip()


# ----------------------------------------------------------------------------------------- data
A = ins[ins.exclude_reason.isna()].copy()  # every inspection except void, test and non-permit rows
A["time_key"] = A.time_in.fillna("00:00")
A["notes"] = A.custom_comments.fillna("")
routine_by_year = ins[ins.routine].groupby("year").size()


# premises: merge establishment_ids at the same address with the same first name word
def addr_key(a):
    t = re.sub(r"[^A-Z0-9 ]", " ", str(a).upper()).split()
    t = [w for w in t if w not in {"N", "S", "E", "W", "STE", "SUITE"}]
    return " ".join(t[:3])


def name_key(n):
    t = [w for w in re.sub(r"[^A-Z ]", "", str(n).upper()).split() if w not in {"THE"}]
    return t[0] if t else ""


A["akey"] = A.main_address.map(addr_key) + "|" + A.permitted_name.map(name_key)
parent = {e: e for e in A.establishment_id.dropna().unique()}


def find(e):
    while parent[e] != e:
        parent[e] = parent[parent[e]]
        e = parent[e]
    return e


span = A.dropna(subset=["establishment_id"]).groupby(["akey", "establishment_id"]).inspection_date.agg(["min", "max"])
for _, grp in span.reset_index().sort_values("min").groupby("akey"):
    rows = grp.to_dict("records")
    for prev, cur in zip(rows, rows[1:]):  # merge only successive permits that do not overlap in time
        if cur["min"] > prev["max"]:
            ra, rb = find(prev["establishment_id"]), find(cur["establishment_id"])
            if ra != rb:
                parent[rb] = ra
A["premises"] = A.establishment_id.map(lambda e: find(e) if isinstance(e, str) else e)
A = A.sort_values(["premises", "inspection_date", "time_key", "inspection_number"]).reset_index(drop=True)
g = A.groupby("premises")
for k in ["inspection_number", "inspection_date", "purpose", "score_std", "inspector"]:
    A[f"next_{k}"] = g[k].shift(-1)
A["days_to_next"] = (A.next_inspection_date - A.inspection_date).dt.days

# the hand-reviewed notes
cr_rows = pd.read_csv(HERE / "closures_reviewed.csv", dtype=str)
cr = cr_rows.drop_duplicates("inspection_number").set_index("inspection_number")
A["classification"] = A.inspection_number.map(cr.classification)
A["reason"] = A.inspection_number.map(cr.reason)
A["reviewer_note"] = A.inspection_number.map(cr.reviewer_note)


def reason_group(r):
    r = str(r)
    if "score above 30" in r:
        return "score at or over threshold"
    for key, grp in [("hot water", "no hot or running water / water leak"),
                     ("running water", "no hot or running water / water leak"),
                     ("hand sink", "hand sinks broken or unusable"),
                     ("water leak", "no hot or running water / water leak"),
                     ("water line", "no hot or running water / water leak"),
                     ("wastewater", "wastewater or drains"), ("drain", "wastewater or drains"),
                     ("grease water", "wastewater or drains"),
                     ("cooler", "refrigeration failure"), ("refrigeration", "refrigeration failure"),
                     ("mouse", "pests"), ("ant infestation", "pests"), ("cockroach", "pests"), ("insect", "pests"),
                     ("permit", "permit / certificate of occupancy"), ("no CO", "permit / certificate of occupancy")]:
        if key in r:
            return grp
    return "other (vent hood, sanitizer, not stated)"


C = A[A.closure_recorded].copy()
C["type"] = np.where(C.reason.str.contains("score above 30"), "score-based", "hazard-based")
C["group"] = C.reason.map(reason_group)
C["voluntary"] = C.reason.str.contains("voluntary")
PARTIAL = r"bar only|kitchen closed|retail side|food prep|food production|prepackaged|service stopped"
C["partial"] = (C.reviewer_note.fillna("") + " " + C.reason).str.contains(PARTIAL)

# ============================================================================================ [0]
head("[0] The reviewed file (closures_reviewed.csv)")
print(f"rows {len(cr_rows)}, distinct inspections {cr_rows.inspection_number.nunique()} "
      f"(the layer duplicates some inspections; build.py keeps one row each)")
t0 = pd.DataFrame({"rows": cr_rows.classification.value_counts(),
                   "distinct inspections": cr_rows.drop_duplicates("inspection_number").classification.value_counts()})
print(t0.to_string())
print(f"closure_recorded in inspections.csv: {int(ins.closure_recorded.sum())}; at establishments (premises): "
      f"{C.premises.nunique()}; purpose of the closure record: {C.purpose.value_counts().to_dict()}")
print(f"Every reviewed inspection number is in inspections.csv: {set(cr.index) <= set(ins.inspection_number)}")
mg = A.groupby("premises").establishment_id.nunique()
print(f"Premises merges (establishment_ids joined): {int((mg > 1).sum())} premises from {int(mg[mg > 1].sum())} "
      f"establishment_ids; those with a closure:")
for p in mg[mg > 1].index.intersection(C.premises.unique()):
    x = A[A.premises == p].groupby("establishment_id").agg(name=("permitted_name", "last"), addr=("main_address", "last"),
                                                            first=("inspection_date", "min"), last=("inspection_date", "max"))
    print("   ", "; ".join(f"{e}: {r['name']} @ {r.addr} {r['first'].date()}..{r['last'].date()}" for e, r in x.iterrows()))

# ============================================================================================ [1]
head("[1] Closures by reason, year and inspector")
print("score-based = reviewer's reason cites a score above 30; check against at_or_over_closure_threshold (score_std):")
print(pd.crosstab(C.type, C.at_or_over_closure_threshold, margins=True).to_string())
print("\nBy reason group:")
print(C.groupby(["type", "group"]).size().to_string())
print(f"\nHazard-based closures described as voluntary: {int((C.voluntary & (C.type == 'hazard-based')).sum())}; "
      f"limited to part of the operation (bar, kitchen, food prep): {int((C.partial & (C.type == 'hazard-based')).sum())}")
print(C[C.partial | C.voluntary][["inspection_date", "permitted_name", "reason"]].to_string())
print(f"\nHazard-based closures by score_std: median {C[C.type == 'hazard-based'].score_std.median():.0f}, "
      f"range {C[C.type == 'hazard-based'].score_std.min()}-{C[C.type == 'hazard-based'].score_std.max()}; "
      f"under 20: {int((C[C.type == 'hazard-based'].score_std < 20).sum())} of {int((C.type == 'hazard-based').sum())}")

yt = pd.crosstab(C.year.astype(int), C.type)
yt["all"] = yt.sum(axis=1)
yt["routine inspections"] = routine_by_year.reindex(yt.index).astype(int)
yt["closures per 1,000 routine"] = (yt["all"] / yt["routine inspections"] * 1000).round(1)
yt["score-based per 1,000"] = (yt["score-based"] / yt["routine inspections"] * 1000).round(1)
print("\nBy year (2017 = Oct-Dec only; 2026 = Jan-Aug):")
print(yt.to_string())

it = pd.crosstab(C.inspector, C.type, margins=True)
rt = ins[ins.routine].groupby("inspector").size()
it["routine inspections"] = rt.reindex(it.index)
it.loc["All", "routine inspections"] = len(ins[ins.routine])
it["closures per 1,000 routine"] = (it["All"] / it["routine inspections"] * 1000).round(1)
first_last = ins[ins.routine].groupby("inspector").inspection_date.agg(["min", "max"])
it = it.join(first_last.rename(columns={"min": "first", "max": "last"}).apply(lambda s: s.dt.date))
print("\nBy inspector (signer where a report exists, else the listed inspector):")
print(it.sort_values("All", ascending=False).to_string())
print("\nAdagbon's closures, notes phrase:")
ad = C[C.inspector == "Emmanuel Adagbon"]
print(f"  {len(ad)} closures, all score-based: {bool((ad.type == 'score-based').all())}; "
      f"notes contain 'Due to scoring higher than a 30 you will have to close for 24-hours': "
      f"{int(ad.notes.map(norm).str.contains('Due to scoring higher than a 30 you will have to close for 24-hours', regex=False).sum())}")
print("\nRestaurant universe (common.R, our cuisine codes): closures among routine restaurant inspections by group")
RR = R.assign(closed=R.closure_recorded,
              score_closed=R.closure_recorded & R.at_or_over_closure_threshold,
              hazard_closed=R.closure_recorded & ~R.at_or_over_closure_threshold)
gt = RR.groupby("group").agg(inspections=("score_std", "size"), establishments=("establishment_id", "nunique"),
                             at_or_over=("at_or_over_closure_threshold", "sum"), score_closures=("score_closed", "sum"),
                             hazard_closures=("hazard_closed", "sum"))
gt["establishments with a score closure"] = RR[RR.score_closed].groupby("group").establishment_id.nunique()
gt = gt.fillna(0).astype(int)
gt.loc["All R"] = gt.sum()
gt.loc["All R", "establishments with a score closure"] = RR[RR.score_closed].establishment_id.nunique()
gt.loc["All R", "establishments"] = R.establishment_id.nunique()
gt["score closures per 1,000"] = (gt.score_closures / gt.inspections * 1000).round(1)
gt["hazard closures per 1,000"] = (gt.hazard_closures / gt.inspections * 1000).round(1)
print(gt.sort_values("score closures per 1,000", ascending=False).to_string())
for grp_set in (["Indian", "Other Asian", "Japanese"], ["Indian", "Other Asian", "Japanese", "Chinese"]):
    sub = RR[RR.group.isin(grp_set)]
    print(f"{' + '.join(grp_set)}: {len(sub)} of {len(RR)} R inspections ({len(sub) / len(RR):.1%}), "
          f"{sub.establishment_id.nunique()} of {RR.establishment_id.nunique()} establishments; score-based closures "
          f"{int(sub.score_closed.sum())} of {int(RR.score_closed.sum())} ({sub.score_closed.sum() / RR.score_closed.sum():.0%}) "
          f"at {sub[sub.score_closed].establishment_id.nunique()} establishments; at or over the threshold "
          f"{int(sub.at_or_over_closure_threshold.sum())} of {int(RR.at_or_over_closure_threshold.sum())}")
print(f"Score-based closures in R: {int(RR.score_closed.sum())} of {int((C.type == 'score-based').sum())}; "
      f"hazard-based in R: {int(RR.hazard_closed.sum())} of {int((C.type == 'hazard-based').sum())}")
print("Closure inspections where signer and listed inspector differ:")
print(C[C.inspector != C.inspector_listed][["inspection_date", "permitted_name", "inspector", "inspector_listed"]].to_string())

# ============================================================================================ [2]
head("[2] What the records show after a closure")
C = C.merge(A[["inspection_number", "next_inspection_date", "next_purpose", "next_score_std", "next_inspector",
               "days_to_next", "next_inspection_number"]], on="inspection_number", how="left", suffixes=("", "_a"))
C["next_class"] = C.next_inspection_number.map(cr.classification)
C["next_within_1d"] = C.days_to_next <= 1
C["next_within_7d"] = C.days_to_next <= 7
C["next_within_14d"] = C.days_to_next <= 14
C["no_next"] = C.next_inspection_date.isna()
fu = C.groupby("type").agg(closures=("inspection_number", "size"),
                           median_days_to_next=("days_to_next", "median"),
                           next_within_1_day=("next_within_1d", "sum"),
                           next_within_7_days=("next_within_7d", "sum"),
                           next_within_14_days=("next_within_14d", "sum"),
                           no_later_record=("no_next", "sum"))
print(fu.to_string())
print("\nPurpose of the next record (any purpose, same premises), closures with a next record within 14 days:")
print(pd.crosstab(C[C.next_within_14d].type, C[C.next_within_14d].next_purpose).to_string())
rc = C[C.next_within_14d & C.next_purpose.isin(["Routine", "Unrecorded"])]
print(f"\nRecords within 14 days of a closure coded Routine/Unrecorded (these are in the routine analysis set): {len(rc)}")
print(rc[["inspection_date", "permitted_name", "next_inspection_date", "next_purpose", "next_score_std"]].to_string())
print(f"  of which in ins.routine: {int(ins.set_index('inspection_number').routine.reindex(rc.next_inspection_number).sum())}")
print("\nNext record explicitly approves reopening (reviewer: 'approved to reopen'):",
      int((C.next_class == REOPEN).sum()), "of", len(C))
reopen = A[A.classification == REOPEN]
prev_closed = []
for _, r in reopen.iterrows():
    p = C[(C.premises == r.premises) & (C.inspection_date <= r.inspection_date)
          & (C.inspection_date >= r.inspection_date - pd.Timedelta(days=14))]
    prev_closed.append(len(p) > 0)
reopen = reopen.assign(follows_recorded_closure=prev_closed)
print(f"Reopen approvals: {len(reopen)}; following a recorded closure at the same premises within 14 days: "
      f"{int(reopen.follows_recorded_closure.sum())}; not: "
      f"{reopen[~reopen.follows_recorded_closure][['inspection_date', 'permitted_name', 'purpose']].values.tolist()}")

print("\nScore-based closures with no record at the premises within 14 days (the notes say closed until a reinspection):")
sb = C[(C.type == "score-based") & ~C.next_within_14d]
print(sb[["inspection_date", "permitted_name", "inspector", "score_std", "next_inspection_date", "next_purpose",
          "next_score_std", "days_to_next"]].to_string())
print("\nHazard-based closures: next record within 7 days:", int(C[C.type == "hazard-based"].next_within_7d.sum()),
      "of", int((C.type == "hazard-based").sum()), "; median days to next record",
      C[C.type == "hazard-based"].days_to_next.median())
hb = C[C.type == "hazard-based"].sort_values("inspection_date")
asks = hb.notes.str.contains(r"re-?inspect|call|contact", case=False, regex=True)
print(f"Hazard-based closure notes that ask the operator to call/contact the office or mention a reinspection: "
      f"{int(asks.sum())} of {len(hb)}; of those, a record within 14 days: {int(hb[asks].next_within_14d.sum())}")
print(hb[["inspection_date", "permitted_name", "group", "score_std", "next_inspection_date", "next_purpose",
          "next_score_std", "days_to_next"]].to_string())

# monthly inspection schedule promised in the notes
MONTHLY = re.compile(r"30[- ]?day (?:routine )?(?:inspection )?(?:frequency|cycle)|monthly (?:re)?inspection (?:frequency|schedule)",
                     re.I)
S = C[C.type == "score-based"].copy()
S["monthly_note"] = S.notes.str.contains(MONTHLY)
n120 = []
for _, r in S.iterrows():
    later = A[(A.premises == r.premises) & ((A.inspection_date > r.inspection_date) |
                                            ((A.inspection_date == r.inspection_date) & (A.time_key > r.time_key)))
              & (A.inspection_date <= r.inspection_date + pd.Timedelta(days=120))]
    n120.append(len(later))
S["records_120d"] = n120
m = S[S.monthly_note]
print(f"\nScore-based closures whose notes put the establishment on a monthly (30-day) inspection schedule: "
      f"{len(m)} of {len(S)}")
print(f"  records at the premises in the 120 days after the closure (including the reinspection): "
      f"median {m.records_120d.median():.0f}; 1 or fewer: {int((m.records_120d <= 1).sum())}; "
      f"3 or more: {int((m.records_120d >= 3).sum())}")
print(m[["inspection_date", "permitted_name", "inspector", "records_120d"]].to_string())

# ============================================================================================ [3]
head("[3] At or over the threshold in force, no closure recorded (score_std)")
H = A[A.at_or_over_closure_threshold].copy()
print(f"Inspections at or over: {len(H)} (routine: {int(H.routine.sum())}); closure recorded: "
      f"{int(H.closure_recorded.sum())}; not: {int((~H.closure_recorded).sum())}")
H["period"] = np.select([H.inspection_date < ORD_2019, H.inspection_date < ORD_2023],
                        ["1 before Oct 15 2019", "2 Oct 15 2019 - Nov 6 2023"], "3 from Nov 7 2023")
print(pd.crosstab(H.year.astype(int), H.closure_recorded, margins=True).to_string())
print(pd.crosstab(H.period, H.closure_recorded, margins=True).to_string())
print(pd.crosstab(H.inspector, H.closure_recorded, margins=True).to_string())
N = H[~H.closure_recorded].copy()


def enforcement_words(t):
    out = []
    if re.search(r"re-?inspection fee|\$\d+", t, re.I):
        out.append("fee")
    if MONTHLY.search(t):
        out.append("monthly schedule")
    if re.search(r"re-?inspect", t, re.I):
        out.append("reinspection mentioned")
    if re.search(r"clos", t, re.I):
        out.append("closure word")
    if re.search(r"before operation", t, re.I):
        out.append("'correct ... before operation'")
    return ", ".join(out) or "none"


N["notes_say"] = N.notes.map(enforcement_words)
N["later_closure_same_premises"] = [bool(((C.premises == r.premises) & (C.inspection_date > r.inspection_date)).any())
                                    for _, r in N.iterrows()]
print("\nThe inspections at or over the threshold with no closure recorded:")
print(N[["inspection_number", "inspection_date", "permitted_name", "inspector", "score_std", "score_layer",
         "closure_threshold", "classification", "notes_say", "next_inspection_date", "next_purpose",
         "next_score_std", "later_closure_same_premises"]].to_string())
last_miss = N.inspection_date.max()
after = H[H.inspection_date > last_miss]
print(f"\nLast at-or-over inspection without a recorded closure: {last_miss.date()}; since then "
      f"{len(after)} at or over, closure recorded at {int(after.closure_recorded.sum())}")
print(f"From Oct 15 2019: at or over {int((H.inspection_date >= ORD_2019).sum())}, no closure "
      f"{int(((H.inspection_date >= ORD_2019) & ~H.closure_recorded).sum())}; before: at or over "
      f"{int((H.inspection_date < ORD_2019).sum())}, no closure {int(((H.inspection_date < ORD_2019) & ~H.closure_recorded).sum())}")
L = H[H.inspector == "David Lerma"].sort_values("inspection_date")
print("\nDavid Lerma's at-or-over inspections in order (closure recorded?):")
print(L[["inspection_date", "permitted_name", "score_std", "closure_recorded"]].to_string())
print(f"  Lerma from {L[L.closure_recorded].inspection_date.min().date()}: first recorded closure at or over; "
      f"from 2020-02-01 all closed: {bool(L[L.inspection_date >= '2020-02-01'].closure_recorded.all())} "
      f"({int((L.inspection_date >= '2020-02-01').sum())} inspections)")
print("  printed-score check (COS included) - inspections over the threshold only on the printed score:",
      int(((A.score_printed >= A.closure_threshold) & ~A.at_or_over_closure_threshold).sum()))

# ============================================================================================ [4]
head("[4] The 31-to-30 change (Ord. 2023-11-075, Nov 7 2023) and how the notes quote the rule")
R_ = A[A.routine]
pre, post = R_[R_.inspection_date < ORD_2023], R_[R_.inspection_date >= ORD_2023]
print(f"Routine inspections with score_std exactly 30: before the change {int((pre.score_std == 30).sum())} "
      f"(closure recorded {int(pre[pre.score_std == 30].closure_recorded.sum())}; the old rule required 31), "
      f"from the change {int((post.score_std == 30).sum())} (closure recorded {int(post[post.score_std == 30].closure_recorded.sum())})")
print("  score_std == 30 before the change, by year:", pre[pre.score_std == 30].year.astype(int).value_counts().sort_index().to_dict())
print("  from the change:", post[post.score_std == 30][["inspection_date", "permitted_name", "inspector", "score_std",
                                                          "score_layer", "score_printed", "closure_recorded"]].values.tolist())
print(f"Routine inspections from Nov 7 2023: {len(post)}; at or over 30: {int(post.at_or_over_closure_threshold.sum())}; "
      f"closure recorded {int(post[post.at_or_over_closure_threshold].closure_recorded.sum())}")
per = R_[R_.inspection_date >= ORD_2019].copy()
per["rule"] = np.where(per.inspection_date >= ORD_2023, "30+ (from Nov 7 2023)", "31+ (Oct 15 2019 - Nov 6 2023)")
pt = per.groupby("rule").agg(routine=("score_std", "size"), at_or_over=("at_or_over_closure_threshold", "sum"),
                             closed_at_or_over=("closure_recorded", lambda s: int((s & per.loc[s.index, "at_or_over_closure_threshold"]).sum())),
                             score_29_30=("score_std", lambda s: int(s.between(29, 30).sum())))
pt["at_or_over per 1,000"] = (pt.at_or_over / pt.routine * 1000).round(2)
print(pt.to_string())

# how the notes state the rule
SENT = re.compile(r"[^.\n]*\b(?:3[01])\b[^.\n]*")
rules = []
for _, r in A.iterrows():
    for s in SENT.findall(r.notes):
        if not (re.search(r"clos", s, re.I) and re.search(r"demerit|point|scor", s, re.I)):
            continue
        m1 = re.search(r"\b(3[01])\s*(?:demerits?\s*|points?\s*)?(?:or|and)\s*(?:more|greater|higher|above)", s, re.I)
        m2 = re.search(r"(?:more than|over|higher than|above)\s*(?:a\s*)?(3[01])\b", s, re.I)
        m3 = re.search(r"score of (3[01]) demerits is (?:an )?(?:automatic|immediate)", s, re.I)
        if m1:
            implied, form = int(m1.group(1)), f"{m1.group(1)} or more"
        elif m2:
            implied, form = int(m2.group(1)) + 1, f"more than {m2.group(1)}"
        elif m3:
            implied, form = int(m3.group(1)), f"score of {m3.group(1)} is closure"
        else:
            continue  # a stated score ("SCORING 31 DEMERITS"), not a rule
        # a general rule ("30 demerits or more shall result in closure") vs the reason given for a closure or
        # reinspection ("due to scoring over 31 demerits, facility is closed"), which describes the score
        kind = ("reason" if re.search(r"due to|has scored", s, re.I) and not re.search(
            r"or (?:more|higher|greater|above)|shall result|will result|is an? (?:automatic|immediate)|is (?:automatic|immediate)",
            s, re.I) else "rule")
        rules.append({"inspection_number": r.inspection_number, "date": r.inspection_date, "inspector": r.inspector,
                      "score_std": r.score_std, "score_layer": r.score_layer, "score_printed": r.score_printed,
                      "threshold_in_force": r.closure_threshold, "form": form, "kind": kind, "implied_threshold": implied,
                      "closure_recorded": r.closure_recorded, "sentence": norm(s)[:100]})
Q_all = pd.DataFrame(rules).drop_duplicates("inspection_number")
Q_all["period"] = np.where(Q_all.date >= ORD_2023, "from Nov 7 2023", "before Nov 7 2023")
print(f"\nInspections whose notes put a number on the closure line: {len(Q_all)}; as a general rule "
      f"{int((Q_all.kind == 'rule').sum())}, as the reason for a closure {int((Q_all.kind == 'reason').sum())}")
print(pd.crosstab([Q_all.kind, Q_all.period, Q_all.inspector], [Q_all.form], margins=True).to_string())
Q = Q_all[Q_all.kind == "rule"].copy()
Q["matches_rule_in_force"] = Q.implied_threshold == Q.threshold_in_force
print("\nGeneral rule statements: implied threshold matches the rule in force?")
print(pd.crosstab([Q.period, Q.inspector], Q.matches_rule_in_force, margins=True).to_string())
print("Mismatches:")
print(Q[~Q.matches_rule_in_force][["inspection_number", "date", "inspector", "score_std", "score_layer", "score_printed",
                                   "threshold_in_force", "closure_recorded", "sentence"]].to_string())
print(f"Goldston's rule wording: last '31 or more' {Q[(Q.inspector == 'Victoria Goldston') & (Q.form == '31 or more')].date.max().date()}, "
      f"first '30 or more' {Q[(Q.inspector == 'Victoria Goldston') & (Q.form == '30 or more')].date.min().date()}")
Rn = Q_all[(Q_all.kind == "reason") & Q_all.closure_recorded].copy()  # on the closure record itself
Rn["score_meets_wording"] = Rn.score_std >= Rn.implied_threshold
print(f"\nReasons given on closure records: {len(Rn)}; wording implies a line above the rule in force in "
      f"{int((Rn.implied_threshold > Rn.threshold_in_force).sum())} (e.g. 'more than 31' under a 31-or-more "
      f"rule, 'higher than a 30' under a 30-or-more rule); score_std meets the literal wording in "
      f"{int(Rn.score_meets_wording.sum())}; does not:")
print(Rn[~Rn.score_meets_wording][["inspection_number", "date", "inspector", "score_std", "score_layer", "score_printed",
                                   "threshold_in_force", "sentence"]].to_string())
print("Reason wording by inspector:", Rn.groupby(["inspector", "form"]).size().to_dict())
warn = Q[~Q.closure_recorded]
print(f"Rule stated without a recorded closure (a warning): {len(warn)}; score_std of those: median {warn.score_std.median():.0f}, "
      f"range {warn.score_std.min()}-{warn.score_std.max()}; at score_std >= threshold in force: "
      f"{int((warn.score_std >= warn.threshold_in_force).sum())}")
print("\nRule stated, no closure, score_std within 1 of the threshold in force (the 'exactly at the threshold' cases):")
near = warn[warn.score_std >= warn.threshold_in_force - 1]
print(near[["inspection_number", "date", "inspector", "score_std", "score_layer", "score_printed", "threshold_in_force",
            "sentence"]].to_string())
print("Their classification in closures_reviewed.csv:", near.inspection_number.map(cr.classification).value_counts().to_dict())

# ============================================================================================ [5]
head("[5] Item 34 and the threshold in the city's own data (score_layer)")
X = A[(A.score_layer >= A.closure_threshold) & ~A.at_or_over_closure_threshold]
print(f"Inspections at or over the threshold on the layer's score but not on score_std: {len(X)} "
      f"(routine {int(X.routine.sum())}; before Nov 7 2023 {int((X.inspection_date < ORD_2023).sum())}, "
      f"from Nov 7 2023 {int((X.inspection_date >= ORD_2023).sum())}); closure recorded: {int(X.closure_recorded.sum())}")
print(f"  item 34 marked in all of them: {bool((X.no_insect_contamination > 0).all())}; score_layer - score_std = 1 in all: "
      f"{bool(((X.score_layer - X.score_std) == 1).all())}; with a printed report: {int(X.score_printed.notna().sum())}, "
      f"printed score {X.score_printed.dropna().astype(int).tolist()} (threshold "
      f"{X[X.score_printed.notna()].closure_threshold.tolist()})")
X = X.assign(rule_quoted=X.inspection_number.isin(Q.inspection_number))
print(X[["inspection_number", "inspection_date", "permitted_name", "inspector", "score_std", "score_layer",
         "score_printed", "closure_threshold", "rule_quoted", "classification"]].to_string())
print(f"Routine inspections with score_layer >= threshold: {int((A.routine & (A.score_layer >= A.closure_threshold)).sum())}; "
      f"with score_std >= threshold: {int((A.routine & A.at_or_over_closure_threshold).sum())}")
# scores stated in the notes, where the layer and score_std differ
ST = re.compile(r"(?:SCORING (?:A )?|low score of |DUE TO )(\d{2})(?= DEMERITS|,|\b)", re.I)
st = []
for _, r in A[(A.score_layer != A.score_std) & ~A.purpose.str.startswith("Reinspection")].iterrows():
    for mm in ST.finditer(r.notes):
        n = int(mm.group(1))
        if n < 25 or re.match(r"\s*(?:demerits?\s*)?or\b", r.notes[mm.end():mm.end() + 20], re.I):
            continue  # a threshold rule ("scoring 30 demerits or more"), not a stated score
        st.append((r.inspection_number, r.inspection_date.date(), r.permitted_name, r.score_std, r.score_layer,
                   r.score_printed, n, norm(r.notes[max(0, mm.start() - 20):mm.end() + 25])))
# the reinspection note that states the routine inspection's score (Shogun, Apr 1 2022)
sg = A[A.inspection_number == "FOOD-025157-2022"].iloc[0]
sg_routine = A[A.inspection_number == "FOOD-079259-2021"].iloc[0]
st.append((sg_routine.inspection_number, sg_routine.inspection_date.date(), sg_routine.permitted_name,
           sg_routine.score_std, sg_routine.score_layer, sg_routine.score_printed,
           int(re.search(r"DUE TO (\d+) DEMERITS", sg.notes).group(1)), "(stated on the same-day reinspection record) "
           + re.search(r"REINSPECTION AFTER CLOSURE[^\n]*", sg.notes).group(0)))
ST_ = pd.DataFrame(st, columns=["inspection_number", "date", "name", "score_std", "score_layer", "score_printed",
                                "score_in_notes", "context"])
ST_["equals_std"] = ST_.score_in_notes == ST_.score_std
ST_["equals_layer"] = ST_.score_in_notes == ST_.score_layer
print("\nScores written in the notes where the layer and score_std differ (item 34 marked):")
print(ST_.to_string())
print(f"  equal to score_std: {int(ST_.equals_std.sum())} of {len(ST_)}; equal to score_layer: {int(ST_.equals_layer.sum())}")

# ============================================================================================ [6]
head("[6] Repeat closures at the same premises")
rep = C.groupby("premises").agg(closures=("inspection_number", "size"), name=("permitted_name", "last"),
                                dates=("inspection_date", lambda s: ", ".join(str(d.date()) for d in sorted(s))),
                                types=("type", lambda s: ", ".join(s)))
rep = rep[rep.closures > 1].sort_values(["closures", "dates"], ascending=[False, True])
print(f"Premises with a closure: {C.premises.nunique()}; with 2 or more: {len(rep)}; closures at those: {int(rep.closures.sum())}")
print(rep.to_string())
print(f"\nPremises with 2+ closures on different visits more than 7 days apart: "
      f"{int(C.groupby('premises').inspection_date.apply(lambda s: (s.sort_values().diff().dt.days > 7).any()).sum())}")


def history(pattern, since=None):
    h = A[A.permitted_name.str.contains(pattern, case=False, na=False, regex=True)]
    if since:
        h = h[h.inspection_date >= since]
    return h[["inspection_number", "inspection_date", "link_number", "purpose", "inspector", "score_std", "score_layer",
              "score_printed", "closure_threshold", "closure_recorded", "classification", "reason"]]


for pat in ["RIVERA", "THAI NOODLE WAVE", "JUNGLE BURGER"]:
    print(f"\nFull history: {pat}")
    print(history(pat).to_string())
rv = C[C.permitted_name.str.contains("RIVERA", na=False)].sort_values("inspection_date")
print("\nRivera's closures and what followed:")
print(rv[["inspection_date", "inspector", "score_std", "score_layer", "score_printed", "reason", "next_inspection_date",
          "next_purpose", "next_score_std", "days_to_next"]].to_string())

# ============================================================================================ [7]
head("[7] The same hazard handled differently: no hot water across the facility")
# Hand-coded from reading each note (closures_reviewed.csv and a search for 'no hot water' and variants).
# scope: facility = no hot water across the facility or kitchen (or at every hand sink); the key phrase is
# checked against the notes. outcome: closed = closure or halt to food preparation recorded; restored =
# fixed during the visit; not closed = neither.
HW = [
    # closure recorded
    ("FOOD-013756-2017", "closed", "No hot water available at this establishment at time of inspection."),
    ("FOOD-073820-2018", "closed", "Missing supply of hot running water to kitchen"),
    ("FOOD-103765-2018", "closed", "Missing supply of hot running water to establishment."),
    ("FOOD-029105-2019", "closed", "Observed no hot water at any sinks in kitchen."),
    ("FOOD-028173-2020", "closed", "Noted no hot water throughout."),
    ("FOOD-028197-2020", "closed", "Noted must no hot water was provided throughout."),
    ("FOOD-028199-2020", "closed", "The bakery must close due to no hot water in the establishment."),
    ("FOOD-040513-2021", "closed", "No hot water in food service establishment."),
    ("FOOD-036030-2021", "closed", "Noted no hot water in the establishment."),
    ("FOOD-036028-2021", "closed", "Your establishment will need to close until the hot water issue has been corrected."),
    ("FOOD-013813-2023", "closed", "Noted no hot water in the building due to the leak."),
    ("FOOD-005014-2024", "closed", "Observed no hot water in facility and hot water heater not functioning."),
    ("FOOD-089398-2024", "closed", "hot water heaters not functioning. Facility shall cease all food operations until hot water is restored."),
    ("FOOD-001327-2026", "closed", "Observed no hot water in facility."),
    # fixed during the visit
    ("FOOD-094635-2018", "restored", "The manager was able to turn it back on before I left."),
    ("FOOD-124831-2018", "restored", "but slowly started getting hot as the inspection progressed."),
    ("FOOD-050472-2019", "restored", "Noted there was no hot water in the deli area. Employees were able to fix this issue."),
    ("FOOD-008223-2021", "restored", "Hot water corrected on site."),
    # neither
    ("FOOD-012076-2017", "not closed", "23 - No hot water during inspection"),
    ("FOOD-102273-2018", "not closed", "Noted no hot water throughout. This must be repair by end of business day today."),
    ("FOOD-038908-2021", "not closed", "Observed not hot water in facility. Facility’s hot water heater has not been restored and has been damaged since July."),
    ("FOOD-028097-2022", "not closed", "Observed no hot water coming out of hand sink or 3 compartment sink."),
    ("FOOD-065695-2022", "not closed", "Observed no hot water in facility. Hot water is required to operate."),
    ("FOOD-010185-2023", "not closed", "Observed no hot water in facility. Hot water is required to operate."),
    ("FOOD-087971-2023", "not closed", "Observed no hot water in facility. Hot water is required to operate."),
    ("FOOD-010368-2024", "not closed", "Observed no hot water in facility. Hot water is required to operate."),
    ("FOOD-089222-2023", "not closed", "Observed no hot water throughout. This must be repaired by 3:00pm or a closure will be issued."),
    ("FOOD-106768-2025", "not closed", "Observed that there is no hot water at any hand-sink."),
]
W = pd.DataFrame(HW, columns=["inspection_number", "outcome", "phrase"]).merge(
    A[["inspection_number", "inspection_date", "permitted_name", "category", "inspector", "score_std", "closure_recorded",
       "classification", "notes", "premises", "next_inspection_date", "next_purpose"]], on="inspection_number")
W["phrase_in_notes"] = [norm(p) in norm(n) for p, n in zip(W.phrase, W.notes)]
W["consistent_with_closure_flag"] = (W.outcome == "closed") == W.closure_recorded
W["threat_or_deadline"] = W.notes.str.contains(r"result in closure|closure will be issued|end of business day|24 hours",
                                               case=False, regex=True)
print(f"Coded inspections: {len(W)}; key phrase found in the notes: {int(W.phrase_in_notes.sum())}; outcome agrees with "
      f"closure_recorded: {int(W.consistent_with_closure_flag.sum())}")
print(W.outcome.value_counts().to_string())
print(f"Premises: closed {W[W.outcome == 'closed'].premises.nunique()}, not closed {W[W.outcome == 'not closed'].premises.nunique()}")
print(pd.crosstab(W.inspector, W.outcome, margins=True).to_string())
print(pd.crosstab(W.category, W.outcome, margins=True).to_string())
print(W[["inspection_number", "inspection_date", "permitted_name", "category", "inspector", "score_std", "outcome",
         "threat_or_deadline", "classification", "phrase_in_notes"]].sort_values("inspection_date").to_string())

print("\nElis African Market (all inspections at the premises):")
el = A[A.permitted_name.str.contains("ELIS AFRICAN", na=False)]
el = A[A.premises.isin(el.premises)]
el = el.assign(no_hot_water=el.notes.str.contains("no hot water in facility", case=False),
               closure_threat=el.notes.str.contains("Failure to comply will result in closure", regex=False),
               repeat_tag=el.notes.str.extract(r"(?:no hot water in facility[^\n]*?)(REPEAT VIOLATION(?: THIRD| FOURTH)?)",
                                               flags=re.I)[0])
print(el[["inspection_number", "inspection_date", "link_number", "inspector", "category", "score_std", "no_hot_water",
          "closure_threat", "repeat_tag", "closure_recorded", "classification"]].to_string())
e_nhw = el[el.no_hot_water]
print("Elis addresses:", {k: sorted(set(v)) for k, v in el.groupby("link_number").main_address}, "; Rivera's addresses:",
      {k: sorted(set(v)) for k, v in A[A.permitted_name.str.contains("RIVERA", na=False)].groupby("link_number").main_address})
print(f"Elis: {len(e_nhw)} consecutive inspections noting no hot water in facility, "
      f"{e_nhw.inspection_date.min().date()} to {e_nhw.inspection_date.max().date()} "
      f"({(e_nhw.inspection_date.max() - e_nhw.inspection_date.min()).days} days); closures recorded "
      f"{int(e_nhw.closure_recorded.sum())}")
for nm, pat in [("2024-01-30 reinspection date", r"Reinspection to occur 2/6"),
                ("2024-06-25 reinspection date", r"Reinspection on 6/28 with fee")]:
    print(f"  {nm}: stated in notes {bool(el.notes.str.contains(pat).any())}")
for set_on, d in [("2024-01-30", "2024-02-06"), ("2024-06-25", "2024-06-28")]:
    later = el[el.inspection_date > pd.Timestamp(set_on)]
    print(f"  record at Elis after {set_on} and within 7 days of the scheduled {d}: "
          f"{bool(((later.inspection_date - pd.Timestamp(d)).dt.days.abs() <= 7).any())}; next record after {set_on}: "
          f"{later.inspection_date.min().date()}")
nn = A[A.inspection_number == "FOOD-005014-2024"].iloc[0]
print(f"Same inspector, {(nn.inspection_date - pd.Timestamp('2024-06-25')).days} days after Elis's 2024-06-25 "
      f"inspection: {nn.permitted_name} {nn.inspection_date.date()} (closure recorded {nn.closure_recorded}; "
      f"next record {nn.next_inspection_date.date() if pd.notna(nn.next_inspection_date) else None})")

# ============================================================================================ [8]
head("[8] Quotes: every quote in closures_reviewed.csv and every quote cited from this script, checked")


def found(q, t):
    parts = [p for p in re.split(r"\s*\.\.\.\s*", norm(q)) if p]
    return all(norm(p) in norm(t) for p in parts)


notes = A.set_index("inspection_number").notes
ok = [found(q, notes.get(i, "")) for i, q in zip(cr_rows.inspection_number, cr_rows.quote.fillna(""))]
cr_rows["quote_found"] = ok
print("closures_reviewed.csv quotes found verbatim (ellipsis = elided text):")
print(cr_rows.groupby("classification").quote_found.agg(["sum", "size"]).to_string())
if not all(ok):
    print(cr_rows[~cr_rows.quote_found][["inspection_number", "quote"]].to_string())
CITED = [
    ("FOOD-002723-2020", "You will need to cease food operations until you are able to bring your score to a 20 or below."),
    ("FOOD-002723-2020", "Since your score did go above a 30, you will now be placed on a 30day inspection frequency cycle."),
    ("FOOD-110765-2023", "The establishment must close due to an insect contamination."),
    ("FOOD-110765-2023", "Observed roaches in the food prep area and on the walls at the three compartment sink area."),
    ("FOOD-036443-2025", "Due to scoring higher than a 30 you will have to close for 24-hours."),
    ("FOOD-117037-2025", "Due to scoring higher than a 30 you will have to close for 24-hours."),
    ("FOOD-115266-2025", "Due to scoring higher than a 30 you will have to close for 24-hours."),
    ("FOOD-070826-2023", "30 DEMERITS OR MORE IS AN AUTOMATIC CLOSURE."),
    ("FOOD-123882-2023", "SCORING 30 DEMERITS OR MORE IS IMMEDIATE CLOSURE."),
    ("FOOD-018259-2024", "30 DEMERITS OR MORE SHALL RESULT IN IMMEDIATE 24 HOUR CLOSURE."),
    ("FOOD-037850-2025", "SCORING A 30 OR HIGHER IS AN AUTOMATIC 24 HOUR CLOSURE WITH FINE."),
    ("FOOD-056881-2024", "A score of 31 or higher will result in a closure of facility."),
    ("FOOD-023442-2021", "Score of 31 demerits is an automatic 24 hour closure and automatically instated on a monthly inspection frequency."),
    ("FOOD-061613-2024", "Due to facility scoring more than 31 points, facility is to close and remain closed until reinspection occurs on 11/19/2024."),
    ("FOOD-003491-2025", "Due to facility scoring more than 31 points, facility is to close and remain closed until reinspection occurs on 7/30/2025."),
    ("FOOD-045736-2025", "DUE TO FACILITY SCORING OVER 30 DEMERITS, FACILITY IS CLOSED FOR 24 HOURS."),
    ("FOOD-072995-2020", "DUE TO FACILITY SCORING A 33, FACILITY IS CLOSED"),
    ("FOOD-009896-2021", "DUE TO FACILITY SCORING 31 DEMERITS FACILITY IS CLOSED UNTIL REINSPECTION OCCURS."),
    ("FOOD-049311-2019", "Due to the low score of 32, you will now be placed on a 30 day inspection cycle."),
    ("FOOD-025157-2022", "REINSPECTION AFTER CLOSURE ON 4/1/22 DUE TO 38 DEMERITS"),
    ("FOOD-040631-2019", "Due to your low score, you will be placed on a 30 day inspection cycle."),
    ("FOOD-050944-2019", "You will be placed on a 30day inspection cycle."),
    ("FOOD-027756-2019", "Facility shall correct all priority violations before operation on 7/11/19."),
    ("FOOD-065695-2022", "Observed no hot water in facility. Hot water is required to operate. Plumber shall be on site within 24 hours. Submit repair documentation. Failure to comply will result in closure."),
    ("FOOD-010368-2024", "Failure to comply will result in closure. REPEAT VIOLATION FOURTH"),
    ("FOOD-010368-2024", "Observed facility is continuing packaging operation without installed handsink, 3 compartment sink, hot water, or having an approved prep area that is free of construction material."),
    ("FOOD-010368-2024", "Facility is NOT permitted to package or handle food items without handsink, 3 compartment sink. hot water, and an approved preparation area. Facility shall cease operations."),
    ("FOOD-010368-2024", "Facility is receiving a $250 risk violation fee and a $100 reinspection fee."),
    ("FOOD-010185-2023", "FACILITY IS NOT PERMITTED TO PACKAGE OR FOOD HANDLE AS IT IS A CONSTRUCTION ZONE."),
    ("FOOD-087971-2023", "Reinspection to occur 2/6."),
    ("FOOD-087971-2023", "*Owner on file shall receive citation for operating a food establishment and handling/packaging food items without a functioning handsink, 3 compartment sink, and no hot water."),
    ("FOOD-005014-2024", "Facility shall cease all food prep and shall not be permitted to handle food items that require preparation until hot water is restored."),
    ("FOOD-102273-2018", "Noted no hot water throughout. This must be repair by end of business day today."),
    ("FOOD-089222-2023", "Observed no hot water throughout. This must be repaired by 3:00pm or a closure will be issued."),
    ("FOOD-106768-2025", "Observed that there is no hot water at any hand-sink."),
    ("FOOD-036028-2021", "Your establishment will need to close until the hot water issue has been corrected."),
    ("FOOD-036028-2021", "Noted hot water was at 84F during my inspection."),
    ("FOOD-029105-2019", "Facility is closed until hot water has been restored to kitchen."),
    ("FOOD-087971-2023", "FACILITY IS NOT PERMITTED TO PACKAGE OR FOOD HANDLE AS IT IS A CONSTRUCTION ZONE."),
    ("FOOD-010368-2024", "FACILITY IS NOT PERMITTED TO PACKAGE OR FOOD HANDLE AS IT IS A CONSTRUCTION ZONE."),
    ("FOOD-087971-2023", "Failure to comply will result in closure. REPEAT VIOLATION THIRD"),
    ("FOOD-010185-2023", "Failure to comply will result in closure. REPEAT VIOLATION"),
    ("FOOD-065695-2022", "Facility is changing from a store front with prepackaged items to a food service facility."),
    ("FOOD-019052-2021", "DUE TO SCORE FACILITY IS CLOSED UNTIL REINSPECTION ON 8/13/21."),
    ("FOOD-063428-2021", "Your establishment must close until a re-inspection has been done and you pay the $75 re-inspection fee."),
    ("FOOD-031615-2024", "The establishment must close until the above items have been corrected and a $100 re-inspection fee has been paid."),
    ("FOOD-014945-2023", "Please do not reopen until you have contacted us for a reinspection."),
    ("FOOD-001579-2023", "Facility must close."),
    ("FOOD-073130-2021", "You have passed the re-inspection"),
    ("FOOD-127886-2024", "Re-inspection following closure on 11/18/2024"),
]
res = [(i, found(q, notes.get(i, "")), q[:90]) for i, q in CITED]
print(f"\nQuotes cited from this script found verbatim: {sum(r[1] for r in res)} of {len(res)}")
for r in res:
    if not r[1]:
        print("  NOT FOUND:", r)
