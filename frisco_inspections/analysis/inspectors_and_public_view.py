"""Reproduces the inspector-consistency and public-view findings.

Restaurant universe R: routine inspections at 'Food establishment' permits,
excluding food types Institutional, Convenience and Grocery (rows with no
food type are kept here, unlike category_gap.py).

Run from this folder: python inspectors_and_public_view.py  (needs pandas)
"""
from pathlib import Path

import numpy as np
import pandas as pd

D = Path(__file__).resolve().parent.parent / "data"
I = pd.read_csv(D / "inspections.csv")
V = pd.read_csv(D / "violations.csv")
rng = np.random.default_rng(20260926)

R = I[(I.permit_category == "Food establishment") & (I.purpose == "Routine")
      & ~I.food_type.isin(["Institutional", "Convenience", "Grocery"])].copy()
print(f"Restaurant universe: {len(R)} routine inspections, {R.permit_id.nunique()} permits")

NEW = {"Phoebe Dang", "Danea Newman", "Feyisara Abati"}  # first reports Jul 20 - Aug 28, 2026


def boot_diff(before, after, B=4000):
    d = (after - before).to_numpy()
    bs = rng.choice(d, (B, len(d)), replace=True).mean(1)
    return d.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5)


# --- 1. Same restaurants, next inspection in Jul-Sep 2026 -------------------
r = R.sort_values(["permit_id", "inspection_date", "time_in_24h"]).copy()
r["prev_score"] = r.groupby("permit_id").score.shift()
r["prev_inspector"] = r.groupby("permit_id").inspector.shift()
q = r[(r.inspection_date >= "2026-07-01") & r.prev_score.notna()]
new = q[q.inspector.isin(NEW)].groupby("permit_id").head(1)
same = q[~q.inspector.isin(NEW) & (q.inspector == q.prev_inspector)].groupby("permit_id").head(1)
m1, lo1, hi1 = boot_diff(new.prev_score, new.score)
m2, lo2, hi2 = boot_diff(same.prev_score, same.score)
print("\n[1] Next routine inspection in Jul-Sep 2026, same restaurant")
print(f"  by a new inspector:    {len(new)} restaurants  {new.prev_score.mean():.2f} -> {new.score.mean():.2f}"
      f"  change {m1:+.2f} (95% CI {lo1:+.2f} to {hi1:+.2f});"
      f" rose {(new.score > new.prev_score).sum()}, fell {(new.score < new.prev_score).sum()}")
print(f"  by the same incumbent: {len(same)} restaurants  {same.prev_score.mean():.2f} -> {same.score.mean():.2f}"
      f"  change {m2:+.2f} (95% CI {lo2:+.2f} to {hi2:+.2f})")
print(f"  difference: {m1 - m2:+.2f} points")
print("  by previous inspector:",
      new.groupby("prev_inspector").apply(lambda d: f"{(d.score - d.prev_score).mean():+.1f} (n={len(d)})",
                                          include_groups=False).to_dict())

# --- 2. The summer-2026 jump --------------------------------------------------
print("\n[2] Mean restaurant score by period")
before = R[R.inspection_date < "2026-07-01"]
q3 = R[R.inspection_date >= "2026-07-01"]
print(f"  Jun 2025-Jun 2026: {before.score.mean():.2f} ({len(before)})   Jul-Sep 2026: {q3.score.mean():.2f} ({len(q3)})")
for label, d in (("new inspectors", q3[q3.inspector.isin(NEW)]), ("everyone else", q3[~q3.inspector.isin(NEW)])):
    print(f"  Jul-Sep 2026 {label}: {d.score.mean():.2f} ({len(d)} inspections, {(d.score >= 15).sum()} at 15+)")

print("  monthly mean (inspections): new inspectors | everyone else")
R["month"] = R.inspection_date.str[:7]
mon = R.groupby(["month", R.inspector.isin(NEW)]).score.agg(["mean", "size"]).unstack()
for mth, row in mon.iterrows():
    n_new = "" if pd.isna(row[("mean", True)]) else f"{row[('mean', True)]:.2f} ({int(row[('size', True)])})"
    print(f"    {mth}: {n_new:>13} | {row[('mean', False)]:.2f} ({int(row[('size', False)])})")

# --- 3. Inspector summary -----------------------------------------------------
print("\n[3] Inspectors (restaurant universe)")
vr = V[V.inspection_id.isin(R.inspection_id)]
warn = vr.groupby("inspector").status.apply(lambda s: (s == "OUT-W").mean())
g = R.groupby("inspector").agg(inspections=("score", "size"), first=("inspection_date", "min"),
                               last=("inspection_date", "max"), mean=("score", "mean"),
                               zero=("score", lambda s: (s == 0).mean()),
                               ge15=("score", lambda s: (s >= 15).mean()))
g["warning_share_of_entries"] = warn
print(g.round(2).sort_values("mean").to_string())

# --- 4. Priority violations ---------------------------------------------------
pri = V[(V.item_category == "Priority") & (V.status == "OUT")].inspection_id.unique()
has = R.inspection_id.isin(pri)
print(f"\n[4] Restaurant inspections with >=1 Priority item OUT: {has.sum()} of {len(R)} ({has.mean():.1%});"
      f" establishments with at least one: {R[has].permit_id.nunique()} of {R.permit_id.nunique()}")
top = V[V.inspection_id.isin(R.inspection_id) & (V.status == "OUT") & (V.item_category == "Priority")]
print("  most common Priority items (inspections):")
print(top.drop_duplicates(["inspection_id", "item_number"]).groupby(["item_number", "item_title"])
      .size().sort_values(ascending=False).head(6).to_string())

# --- 5. What the inspection web page does not display -------------------------
# The page skips OUT-W entries, and its script shows only the first non-blank
# comment for each item number.
print("\n[5] Web page vs PDF")
ow = V.status == "OUT-W"
print(f"  all reports: {ow.sum()} of {len(V)} entries are OUT-W warnings ({ow.mean():.1%})")
shown = V[~ow].copy()
shown["has_text"] = shown.comments.fillna("").str.strip() != ""
first = shown[shown.has_text].groupby(["inspection_id", "item_number"]).seq.transform("min")
dup = (shown[shown.has_text].seq != first).sum()
print(f"  further comments under an item already shown: {dup} ({dup / len(V):.1%})")
z = R[R.score == 0]
zw = z.inspection_id.isin(V[ow].inspection_id)
print(f"  zero-score restaurant inspections whose PDF has a warning: {zw.sum()} of {len(z)} ({zw.mean():.1%})")

# --- 6. Failed inspections hidden from the portal's browse list ---------------
# The browse list shows each establishment's latest inspection in the chosen
# range (default: Jan 1 of the current year through today).
A = I.sort_values(["permit_id", "inspection_date", "time_in_24h", "purpose"])
y = A[A.inspection_date >= "2026-01-01"]
latest = set(y.groupby("permit_id").tail(1).inspection_id)
hi = y[y.score > 30]
hidden = hi[~hi.inspection_id.isin(latest)]
print(f"\n[6] 2026 inspections scoring above 30: {len(hi)}; not shown in the default browse list: {len(hidden)}")
for _, h in hidden.iterrows():
    shown_row = y[(y.permit_id == h.permit_id) & y.inspection_id.isin(latest)].iloc[0]
    print(f"  {h.establishment_name}: {h.score} on {h.inspection_date} -> list shows {shown_row.score} ({shown_row.inspection_date})")

# --- 7. Missing employee-health policy, three ways ----------------------------
code = V.code_normalized.fillna("").str.contains(r"3-301\.11\(E\)\(3\)", regex=True)
hp = V[code & V.inspection_id.isin(R.inspection_id)]
scored = hp[hp.status == "OUT"].drop_duplicates("inspection_id")
warned = hp[hp.status == "OUT-W"].drop_duplicates("inspection_id")
print("\n[7] Missing written employee-health policy (citation 3-301.11(E)(3)), restaurant inspections")
print("  scored as a 3-point violation:", len(scored), scored.inspector.value_counts().to_dict())
print("  written as a 0-point warning: ", len(warned), warned.inspector.value_counts().to_dict())
none = R[~R.inspector.isin(set(hp.inspector))]
print(f"  inspectors who never cite it: {sorted(set(R.inspector) - set(hp.inspector))} ({len(none)} inspections)")
