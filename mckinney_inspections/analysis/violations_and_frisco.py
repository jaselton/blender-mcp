"""What McKinney inspectors find most in restaurants (Oct 2017 - Sep 2026), and a like-with-like
comparison with Frisco (Jun 2025 - Sep 2026).

Part 1, McKinney (restaurant universe R from common.py: 8,242 routine inspections at 782 establishments)
  [0] universe and sources                    [4] cold and hot holding (items 2 and 3)
  [1] most common items and item groups       [5] hand sinks (item 31)
  [2] inspections with a Priority violation   [6] corrected on site and repeat marks (reports era)
  [3] pests (item 34, read with the notes)    [7] trends by year, inspector mix, 2020
Part 2, Frisco side by side
  [8] comparable sets                          [12] inspector pattern
  [9] headline numbers                         [13] closures
  [10] what inspectors find most               [14] public access
  [11] cuisine gap                             [15] "Same issue in McKinney?" table

Scores and items
  McKinney score_std is the city layer's items at form weights (3/2/1). The layer stores items corrected
  on site (COS) as 0, so score_std is a lower bound on the official score, and item counts from the layer
  leave COS items out. Frisco's official score and its OUT entries include COS items. So every
  McKinney-Frisco comparison uses McKinney's score_printed (the report PDF total = score_std + COS points)
  and adds the COS items from the reports: R inspections from 2024 on that have a published report
  (1,754), and within them the ones inside Frisco's window (Jun 23, 2025 to Sep 1, 2026). Part 1 uses
  the layer for 2017-2026 because it is the only source before 2024.
  An item counts once per inspection however many notes are written under it (both cities).
  McKinney's form has no 0-point warning; Frisco's OUT-W warnings are 0 points and are not counted as
  violations here.

Notes
  violations.comment is the text an inspector wrote under a numbered line ("34 - ...") in the inspection
  notes. Notes are sometimes carried forward from an earlier visit and occasionally numbered wrongly, so
  keyword counts are approximate.

Frisco
  Loaded from frisco_inspections/data through frisco_inspections/analysis/common.py (imported under another
  module name). Restaurant universe as in inspectors_and_public_view.py (2,180 routine inspections at
  'Food establishment' permits, food types Institutional/Convenience/Grocery excluded); cuisine groups
  from common.U (2,136 with a food-type label). Frisco's labels are the city's; McKinney's are ours.

Everything here is a pattern in published records. Inspector comparisons cannot separate how strictly
someone inspects from where they are sent or how they record what they find.

Run from this folder:  python violations_and_frisco.py   (pandas, numpy, statsmodels; about a minute)
"""
import importlib.util
import os
import re
import sys
import time
import warnings
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import ITEM_GROUP, R, ins, permits, vio  # noqa: E402

warnings.filterwarnings("ignore")
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 400)
pd.set_option("display.max_colwidth", 120)
T0 = time.time()
rng = np.random.default_rng(20260927)
B = 2000

FRISCO = HERE.parent.parent / "frisco_inspections"
F_START, F_END = pd.Timestamp("2025-06-23"), pd.Timestamp("2026-09-24")   # Frisco's data
MK_END = ins.inspection_date.max()                                          # McKinney's last inspection
AM = "American (sit-down, bars, venues)"
SALLEY, GOLDSTON, ADAGBON, SMITH, GUIDRY = ("Shane Salley", "Victoria Goldston", "Emmanuel Adagbon",
                                            "Billy Smith", "Nala Guidry")
NEW25 = [ADAGBON, SMITH, GUIDRY]                              # McKinney inspectors first seen Sep 2025-Mar 2026
F_NEW = ["Phoebe Dang", "Danea Newman", "Feyisara Abati"]    # Frisco inspectors first seen Jul-Aug 2026
TITLE = {n: t for n, t in vio[["item_number", "item_title"]].drop_duplicates().itertuples(index=False)}
SHORT = {1: "cooling", 2: "cold holding (41F)", 3: "hot holding (135F)", 4: "cooking temperature",
         6: "time as a control", 7: "approved source / adulterated", 9: "raw food not separated from RTE",
         10: "food-contact surfaces not clean/sanitized", 12: "employee health knowledge/reporting",
         13: "exclusion/restriction", 14: "hand washing / glove use", 15: "bare-hand contact with RTE food",
         18: "toxic substances (chemicals)", 19: "water source / plumbing / backflow", 20: "sewage",
         21: "person in charge / certified food manager", 22: "food handler cards", 23: "hot and cold water",
         24: "required records", 27: "cooling equipment", 28: "date marking", 29: "thermometers",
         31: "hand sinks", 32: "surfaces cleanable/in repair", 33: "warewashing facilities",
         34: "insects, rodents, animals", 35: "personal cleanliness", 36: "wiping cloths",
         37: "environmental contamination", 39: "utensils, equipment, linens stored/used",
         41: "original container labeling", 42: "non-food surfaces clean", 43: "ventilation and lighting",
         45: "physical facilities", 47: "other (mostly postings)", 5: "reheating", 8: "received temperature",
         11: "returned/reconditioned food", 16: "pasteurized foods", 17: "food additives", 25: "variance/HACCP",
         26: "consumer advisory", 30: "permit", 38: "thawing", 40: "single-service articles",
         44: "garbage", 46: "toilet facilities"}


def head(s):
    print(f"\n{'=' * 110}\n{s}\n{'=' * 110}")


def pct(a, b, d=1):
    return f"{a:,} of {b:,} ({100 * a / b:.{d}f}%)" if b else f"{a} of 0"


def cshare(values, cluster, nb=B):
    """Ratio of sums (a share when values are 0/1, a mean otherwise) with a 95% cluster-bootstrap interval
    (establishments resampled with replacement)."""
    d = pd.DataFrame({"v": np.asarray(values, float), "c": np.asarray(cluster)})
    g = d.groupby("c").v.agg(["sum", "size"])
    s, n = g["sum"].to_numpy(), g["size"].to_numpy()
    idx = rng.integers(0, len(g), (nb, len(g)))
    bs = s[idx].sum(1) / n[idx].sum(1)
    return s.sum() / n.sum(), np.percentile(bs, 2.5), np.percentile(bs, 97.5)


def fci(t, d=1, scale=100, unit="%"):
    m, lo, hi = t
    return f"{scale * m:.{d}f}{unit} (95% CI {scale * lo:.{d}f}-{scale * hi:.{d}f})"


def est_means(values, cluster):
    """Per-establishment means (each establishment weighted equally)."""
    return pd.Series(np.asarray(values, float)).groupby(np.asarray(cluster)).mean().to_numpy()


def boot_est(e, nb=B):
    bs = e[rng.integers(0, len(e), (nb, len(e)))].mean(1)
    return e.mean(), bs


def boot_mean(x, nb=4000):
    x = np.asarray(x, float)
    bs = x[rng.integers(0, len(x), (nb, len(x)))].mean(1)
    return x.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5), bs


def item_matrix(v, key, ids):
    """0/1 inspection x item matrix (items 1-47) from violation rows."""
    x = pd.crosstab(v[key], v.item_number).clip(upper=1)
    return x.reindex(index=list(ids), columns=range(1, 48), fill_value=0)


# ------------------------------------------------------------------------------------ pest classifier
PEST = {  # applied to lower-cased item-34 text; categories overlap
    "roaches": r"roach",
    "rodents or droppings": r"dropping|feces|excrement|\bmouse\b|\bmice\b|\brats?\b|gnaw|rodent (?:activity|evidence|"
                            r"infestation|observed|found|noted|sighting)|(?:live|dead|observed|noted|found) (?:a )?rodent",
    "flies, gnats or other flying insects": r"\bflies\b|\bgnats?\b|fruit ?fl|drain ?fl|bar ?fl|\bfly (?:issue|problem|"
                                            r"activity|infestation)|house ?fl|flying (?:insect|bug)",
    "other insects or animals": r"\bants?\b|beetle|weevil|\bmoths?\b|spider|\bbirds?\b|lizard|cricket|wasp|"
                                r"crawling insect|insect present|insect inside|live insect|\bbugs? (?:observed|found|"
                                r"noted|crawling)|insects? (?:observed|found|noted|were|crawling)|infestation|"
                                r"\bdogs?\b|\bcats?\b|\bpets?\b",
    "pest, type not stated": r"observed (?:a )?pests? in (?:the )?establishment|evidence of pests",
}
DEAD = r"dead (?:bug|insect|fl(?:y|ies)|cricket|roach)s?|deceased|light shield"
ENTRY = r"door|gap|seal|screen|opening|window|weather ?strip|sweep|\bholes?\b|crack|air curtain|entry|entrance of pest"
CONTROL = (r"pest control (?:record|receipt|report|log|invoice)|records?\b|receipt|invoice|household|residential|"
           r"unapproved|unpermitted|bug spray|\braid\b|pesticide|fly light|bug light|zap|glue|trap|bait|insecticide|"
           r"spray|fly strip|sticky|swatter|self|licensed|does not have pest control|no pest control")


def classify_pests(text):
    t = text.fillna("").str.lower()
    out = pd.DataFrame({k: t.str.contains(p, regex=True) for k, p in PEST.items()})
    out["pests recorded"] = out[list(PEST)].any(axis=1)
    out["dead insects only"] = t.str.contains(DEAD) & ~out["pests recorded"]
    out["entry points (doors, gaps, seals)"] = t.str.contains(ENTRY)
    out["control devices, chemicals or records"] = t.str.contains(CONTROL)
    out["no text"] = t.str.strip() == ""
    prim = np.select([out["pests recorded"], out["dead insects only"], out["entry points (doors, gaps, seals)"],
                      out["control devices, chemicals or records"], out["no text"]],
                     ["pests recorded", "dead insects only", "entry points only", "control devices/records only",
                      "no text"], "other or unclear")
    out["primary"] = prim
    return out


TEMP = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*(?:°|º|˚|degrees?|deg\b|f\b)", re.I)


def temps(text, fn):
    """Warmest (fn=max) or coolest (fn=min) food temperature written in a note, 42-134F (the 41F and 135F
    limits the notes quote are left out)."""
    xs = [float(x) for x in TEMP.findall(text if isinstance(text, str) else "")]
    xs = [x for x in xs if 41 < x < 135]
    return fn(xs) if xs else np.nan


# =================================================================================================== [0]
head("[0] Universe and sources (McKinney)")
R = R.copy()
vR = vio[vio.inspection_number.isin(R.inspection_number)].copy()
vL = vR[vR.source == "layer"]
XL = item_matrix(vL, "inspection_number", R.inspection_number)          # layer items, all years
nR, eR = len(R), R.establishment_id.nunique()
print(f"R: {nR:,} routine restaurant inspections at {eR} establishments, "
      f"{R.inspection_date.min():%Y-%m-%d} to {R.inspection_date.max():%Y-%m-%d}; inspectors: {R.inspector.nunique()}")
print(f"violation rows for R: {len(vR):,} ({(vR.source == 'layer').sum():,} layer items, "
      f"{(vR.source != 'layer').sum()} corrected-on-site items from reports)")
print(f"layer rows with note text under the item's number: {pct(int((vL.comment.fillna('') != '').sum()), len(vL))}")
print("R inspections by year:", R.groupby("year").size().astype(int).to_dict())
rep_by_year = R.groupby("year").score_printed.apply(lambda s: f"{s.notna().sum()}/{len(s)}")
print("R inspections with a published report (score_printed), by year:", rep_by_year.to_dict())
cc = R.custom_comments.fillna("")
noinsp = cc.str.contains(r"no inspection|(?:temporarily|currently) closed|closed (?:temporarily|due to covid)|"
                         r"out of business", case=False) & (R.score_std == 0)
print(f"R records at 0 whose notes say no inspection took place (closed, COVID-19, out of business): "
      f"{int(noinsp.sum())} (years {R[noinsp].year.astype(int).value_counts().sort_index().to_dict()}); "
      f"kept in R as in the other scripts; they move any share by under 0.1 points")

# =================================================================================================== [1]
head("[1] Most common items and item groups, 2017-2026 (layer; items corrected on site are not in the layer)")
cnt = XL.sum()
est_cnt = {n: vL[vL.item_number == n].establishment_id.nunique() for n in range(1, 48)}
tab = pd.DataFrame({"item": cnt.index, "what": [SHORT.get(n, TITLE[n][:40]) for n in cnt.index],
                    "category": ["Priority" if n <= 20 else "Priority Foundation" if n <= 33 else "Core"
                                 for n in cnt.index],
                    "inspections": cnt.values, "share": (cnt.values / nR * 100).round(1),
                    "establishments": [est_cnt[n] for n in cnt.index]})
tab["est_share"] = (tab.establishments / eR * 100).round(1)
print("Top 15 items, all categories (share of R inspections citing the item; establishments cited at least once):")
print(tab.sort_values("inspections", ascending=False).head(15).to_string(index=False))
print("\nPriority items (1-20), most common first:")
print(tab[tab.item <= 20].sort_values("inspections", ascending=False).head(10).to_string(index=False))
print("Priority items cited 1% of inspections or less:",
      tab[(tab.item <= 20) & (tab.share <= 1)].sort_values("inspections")[["item", "what", "inspections"]]
      .to_dict("records"))

grp = pd.Series(ITEM_GROUP)
pts = pd.Series({n: 3 if n <= 20 else 2 if n <= 33 else 1 for n in range(1, 48)})
G = pd.DataFrame({g: XL[grp[grp == g].index].max(axis=1) for g in grp.unique()})
Gp = pd.DataFrame({g: (XL[grp[grp == g].index] * pts[grp[grp == g].index]).sum(axis=1) for g in grp.unique()})
gt = pd.DataFrame({"inspections_with_any": G.sum(), "share": (G.mean() * 100).round(1),
                   "points_per_inspection": Gp.mean().round(2),
                   "share_of_all_points": (Gp.sum() / Gp.sum().sum() * 100).round(1)})
print("\nItem groups (common.ITEM_GROUP): share of inspections with at least one item, points per inspection "
      "and share of all demerit points")
print(gt.sort_values("share_of_all_points", ascending=False).to_string())
print(f"check: points per inspection sum {Gp.sum(axis=1).mean():.2f} = mean score_std {R.score_std.mean():.2f}")
cat_any = {c: int(XL[[n for n in range(1, 48) if lo <= n <= hi]].max(axis=1).sum())
           for c, (lo, hi) in {"Priority": (1, 20), "Priority Foundation": (21, 33), "Core": (34, 47)}.items()}
print("Inspections with at least one item, by category:", {k: pct(v, nR) for k, v in cat_any.items()})
i47 = vL[vL.item_number == 47].comment.fillna("").str.lower()
post = i47.str.contains(r"post|display|sign|public view|visible")
print(f"item 47 notes about postings (permit, food-manager card, 'last inspection available' sign): "
      f"{pct(int(post.sum()), len(i47))}")

# =================================================================================================== [2]
head("[2] Inspections with at least one Priority violation (layer, 2017-2026)")
R["pri"] = (XL.loc[R.inspection_number, range(1, 21)].max(axis=1).to_numpy() > 0).astype(int)
assert (R.pri == (R.priority_items > 0)).all()
t_pri = cshare(R.pri, R.establishment_id)
print(f"R inspections with 1+ Priority item: {pct(int(R.pri.sum()), nR)}; {fci(t_pri)} (cluster bootstrap by establishment)")
e_any = R.groupby("establishment_id").pri.max()
print(f"establishments with at least one such inspection, 2017-2026: {pct(int(e_any.sum()), len(e_any))}")
print(f"Priority items per inspection: mean {R.priority_items.mean():.2f}; inspections with 3+ Priority items: "
      f"{pct(int((R.priority_items >= 3).sum()), nR)}")
print(f"R inspections scoring 0 (score_std): {pct(int((R.score_std == 0).sum()), nR)}; 15+: "
      f"{pct(int((R.score_std >= 15).sum()), nR)}; mean score_std {R.score_std.mean():.2f}")

# =================================================================================================== [3]
head("[3] Pests: item 34 ('No evidence of insect contamination, rodents or other animals'), read with the notes")
v34 = vR[vR.item_number == 34].copy()
c34 = classify_pests(v34.comment)
v34 = pd.concat([v34, c34.set_index(v34.index)], axis=1)
n34 = v34.inspection_number.nunique()
print(f"item 34 cited on {pct(n34, nR)} R inspections at {v34.establishment_id.nunique()} of {eR} establishments "
      f"({(v34.source != 'layer').sum()} of the {len(v34)} citations are corrected-on-site items from reports)")
print("What the note under item 34 describes (overlapping categories):")
for k in list(PEST) + ["pests recorded", "dead insects only", "entry points (doors, gaps, seals)",
                       "control devices, chemicals or records", "no text"]:
    print(f"  {k:40s} {int(v34[k].sum()):4d}")
print("Primary reading of each citation (mutually exclusive):", v34.primary.value_counts().to_dict())
pestI = v34[v34["pests recorded"]]
t_pest = cshare(R.inspection_number.isin(pestI.inspection_number).astype(int), R.establishment_id)
print(f"R inspections whose item-34 note records pests (live or droppings, incl. unstated type): "
      f"{pct(pestI.inspection_number.nunique(), nR)}; {fci(t_pest)}; at {pestI.establishment_id.nunique()} of "
      f"{eR} establishments ({100 * pestI.establishment_id.nunique() / eR:.1f}%)")
for k in PEST:
    s = v34[v34[k]]
    print(f"  {k:40s} {s.inspection_number.nunique():4d} inspections at {s.establishment_id.nunique():3d} establishments")
nost = v34[v34["pest, type not stated"]]
print(f"'Observed pest in establishment' (type not stated) by inspector: {nost.inspector.value_counts().to_dict()}; "
      f"years {nost.inspection_date.dt.year.value_counts().sort_index().to_dict()}")
# the whole notes, not only the item-34 line (sensitivity: notes can be carried forward)
allnotes = R.custom_comments.fillna("").str.lower()
rr = allnotes.str.contains(r"roach|dropping|\bmice\b|\bmouse\b|\brats?\b|rodent (?:activity|infestation|sighting)")
print(f"R inspections whose full notes mention roaches or rodent evidence anywhere: {int(rr.sum())}; of these, "
      f"item 34 not cited: {int((rr & ~R.inspection_number.isin(v34.inspection_number)).sum())} "
      "(includes carried-forward text, pest-control log lines and advice; not counted as findings)")
yr = R.assign(p=R.inspection_number.isin(pestI.inspection_number)).groupby("year").p.agg(["sum", "mean"])
print("pests recorded under item 34, by year (inspections, share %):",
      {int(y): f"{int(r['sum'])} ({100 * r['mean']:.1f}%)" for y, r in yr.iterrows()})
by_i = R.assign(p=R.inspection_number.isin(pestI.inspection_number)).groupby("inspector").p.agg(["sum", "size"])
print("by inspector (reflects assignment as well as practice):",
      {k: f"{int(r['sum'])}/{int(r['size'])}" for k, r in by_i.sort_values("size", ascending=False).iterrows()})

# =================================================================================================== [4]
head("[4] Cold and hot holding (items 2 and 3) and other temperature items")
for n in (1, 2, 3, 4, 6, 27, 28):
    s = vL[vL.item_number == n]
    print(f"  item {n:2d} {SHORT[n]:28s} {pct(s.inspection_number.nunique(), nR)} R inspections, "
          f"{s.establishment_id.nunique()} establishments")
temp_any = XL.loc[:, 1:6].max(axis=1)
print(f"any temperature-control item (1-6): {pct(int(temp_any.sum()), nR)}")
v2 = vR[vR.item_number == 2].copy()
v2["t"] = v2.comment.map(lambda s: temps(s, max))
w2 = v2.groupby("inspection_number").t.max().dropna()
print(f"cold holding: {v2.inspection_number.nunique()} R inspections (layer + COS); a food temperature in the note: "
      f"{len(w2)}; warmest food noted: median {w2.median():.0f}F, quartiles {w2.quantile(.25):.0f}-{w2.quantile(.75):.0f}F; "
      f"50F or warmer {pct(int((w2 >= 50).sum()), len(w2))}; 70F or warmer {int((w2 >= 70).sum())}")
d2 = v2.comment.fillna("").str.contains(r"discard|threw|thrown|dispos|voluntar", case=False)
print(f"cold-holding notes that mention discarding food: {pct(int(d2.sum()), len(v2))}")
v3 = vR[vR.item_number == 3].copy()
v3["t"] = v3.comment.map(lambda s: temps(s, min))
w3 = v3.groupby("inspection_number").t.min().dropna()
print(f"hot holding: {v3.inspection_number.nunique()} R inspections; a food temperature in the note: {len(w3)}; "
      f"coolest food noted: median {w3.median():.0f}F; below 100F: {pct(int((w3 < 100).sum()), len(w3))}")

# =================================================================================================== [5]
head("[5] Hand sinks (item 31) and hand washing (item 14)")
v31 = vR[vR.item_number == 31].copy()
t31 = v31.comment.fillna("").str.lower()
s31 = vL[vL.item_number == 31]
t_31 = cshare(R.inspection_number.isin(s31.inspection_number).astype(int), R.establishment_id)
print(f"item 31 (layer) cited on {pct(s31.inspection_number.nunique(), nR)}; {fci(t_31)}; at "
      f"{s31.establishment_id.nunique()} of {eR} establishments")
kw = {"paper towels": r"towel", "soap": r"soap",
      "blocked or items in / sink used for something else": r"block|obstruct|in (?:the )?hand ?sink|items? (?:stored )?in|"
                                                             r"stored in|used for|dump|utensils in|ice in|access",
      "no (hot) water, broken, draining": r"hot water|no water|warm water|water temp|not working|broken|repair|drain|leak",
      "missing hand sink / not provided": r"no hand ?sink|install|need(?:s)? a hand ?sink|provide a hand ?sink"}
print(f"what the {len(v31)} item-31 notes mention (overlapping keyword counts):",
      {k: int(t31.str.contains(p).sum()) for k, p in kw.items()})
s14 = vL[vL.item_number == 14]
print(f"item 14 (hands washed, gloves) cited on {pct(s14.inspection_number.nunique(), nR)}")

# =================================================================================================== [6]
head("[6] Corrected on site (COS) and repeat marks: reports era (R inspections from 2024 with a report)")
RP = R[(R.year >= 2024) & R.score_printed.notna()].copy()
vP = vio[vio.inspection_number.isin(RP.inspection_number)].copy()      # layer + COS rows
print(f"reports era: {len(RP):,} of {int((R.year >= 2024).sum()):,} R inspections 2024-2026 have a report "
      f"({100 * len(RP) / (R.year >= 2024).sum():.0f}%), at {RP.establishment_id.nunique()} establishments")
RP["any_cos"] = RP.report_cos_points.fillna(0) > 0
print(f"inspections with any item marked COS: {pct(int(RP.any_cos.sum()), len(RP))}; "
      f"COS points {RP.report_cos_points.sum():.0f} of {RP.score_printed.sum():.0f} printed points "
      f"({100 * RP.report_cos_points.sum() / RP.score_printed.sum():.1f}%)")
ct = RP.groupby("inspector").agg(inspections=("any_cos", "size"), any_cos=("any_cos", "mean"),
                                 cos_points=("report_cos_points", "mean"), printed=("score_printed", "mean"),
                                 layer=("score_std", "mean"))
print("by inspector (signer):")
print(ct.sort_values("inspections", ascending=False).round(3).to_string())
cos_by_insp = RP[RP.any_cos].inspector.value_counts()
print(f"inspections with COS items, by inspector: {cos_by_insp.to_dict()} "
      f"(Salley share {100 * cos_by_insp.get(SALLEY, 0) / RP.any_cos.sum():.0f}%)")
pP = vP[vP.item_number <= 20]
print(f"Priority items on these reports: {len(pP)}; marked COS: {pct(int(pP.corrected_on_site.sum()), len(pP))}")
pS = pP[pP.inspector == SALLEY]
print(f"  Shane Salley's Priority items marked COS: {pct(int(pS.corrected_on_site.sum()), len(pS))}; "
      f"everyone else: {pct(int(pP[pP.inspector != SALLEY].corrected_on_site.sum()), int((pP.inspector != SALLEY).sum()))}")
print("items most often marked COS:", vP[vP.corrected_on_site].item_number.value_counts().head(6).to_dict())
# repeat marks
RP["any_rep"] = RP.report_repeat_items.fillna("") != ""
print(f"\ninspections with at least one item marked Repeat on the report: {pct(int(RP.any_rep.sum()), len(RP))}; "
      f"items marked Repeat: {pct(int(vP.repeat.fillna(False).astype(bool).sum()), len(vP))}")
# recurrence: an item cited at the previous routine inspection of the same establishment and cited again
S = R.sort_values(["establishment_id", "inspection_date", "time_in"]).copy()
S["prev"] = S.groupby("establishment_id").inspection_number.shift()
S["prev_date"] = S.groupby("establishment_id").inspection_date.shift()
cur = S[S.inspection_number.isin(RP.inspection_number) & S.prev.notna()
        & ((S.inspection_date - S.prev_date).dt.days >= 30)]
items_all = vio.groupby("inspection_number").item_number.apply(set).to_dict()
rep_flag = vio[vio.repeat.fillna(False).astype(bool)].groupby("inspection_number").item_number.apply(set).to_dict()
rows = []
for r in cur.itertuples():
    both = items_all.get(r.inspection_number, set()) & items_all.get(r.prev, set())
    for n in both:
        rows.append((r.inspector, n, n in rep_flag.get(r.inspection_number, set())))
rec = pd.DataFrame(rows, columns=["inspector", "item", "marked"])
print(f"items cited at an inspection and again at the establishment's next routine inspection (reports era, "
      f"30+ days apart): {len(rec)}; marked Repeat: {pct(int(rec.marked.sum()), len(rec))}")
rb = rec.groupby("inspector").marked.agg(["sum", "size"])
print("  by inspector at the second visit:",
      {k: f"{int(r['sum'])}/{int(r['size'])} ({100 * r['sum'] / r['size']:.0f}%)"
       for k, r in rb.sort_values("size", ascending=False).iterrows()})
txt_rep = vL[vL.inspection_number.isin(RP.inspection_number)].comment.fillna("").str.contains(
    r"repeat|recurring", case=False)
print(f"reports-era layer items whose note says 'repeat' or 'recurring': {int(txt_rep.sum())}")

# =================================================================================================== [7]
head("[7] Trends by year (layer score_std and layer items; inspector mix changes year to year)")
R["zero"] = (R.score_std == 0).astype(int)
R["ge15"] = (R.score_std >= 15).astype(int)
for n in (10, 18, 9, 2, 31, 34):
    R[f"i{n}"] = XL.loc[R.inspection_number, n].to_numpy()
R["pest"] = R.inspection_number.isin(pestI.inspection_number).astype(int)
yt = R.groupby("year").agg(inspections=("pri", "size"), establishments=("establishment_id", "nunique"),
                           mean=("score_std", "mean"), zero=("zero", "mean"), ge15=("ge15", "mean"),
                           any_priority=("pri", "mean"), i10=("i10", "mean"), i18=("i18", "mean"),
                           i9=("i9", "mean"), i2=("i2", "mean"), i31=("i31", "mean"), i34=("i34", "mean"),
                           pests=("pest", "mean"))
for c in ["zero", "ge15", "any_priority", "i10", "i18", "i9", "i2", "i31", "i34", "pests"]:
    yt[c] = (yt[c] * 100).round(1)
yt["mean"] = yt["mean"].round(2)
yt.index = yt.index.astype(int)
print("(shares in %; 2017 = Oct-Dec only, 2026 = Jan-Aug)")
print(yt.to_string())
mix = pd.crosstab(R.year.astype(int), R.inspector, normalize="index").mul(100).round(0)
print("\nShare of each year's R inspections by inspector (%):")
print(mix.loc[:, mix.max() >= 5].to_string())
sy = R[R.inspector == SALLEY].groupby("year").agg(n=("pri", "size"), any_priority=("pri", "mean"),
                                                  mean=("score_std", "mean"))
print("\nShane Salley, the one inspector active in every year (same person, so no mix change):")
print({int(y): f"{int(r.n)} insp, Priority {100 * r.any_priority:.0f}%, mean {r['mean']:.1f}" for y, r in sy.iterrows()})
# year effects on the any-Priority share, with and without inspector and establishment effects
M = R.assign(yr=R.year.astype(int).astype(str))
cl = {"groups": pd.factorize(M.establishment_id)[0]}
yrs = [str(y) for y in range(2017, 2027) if y != 2018]
KY = "C(yr, Treatment('2018'))[T.{}]"
res = {}
for nm, f in [("year only", "pri ~ C(yr, Treatment('2018'))"),
              ("+ inspector", "pri ~ C(yr, Treatment('2018')) + C(inspector)"),
              ("+ inspector + establishment", "pri ~ C(yr, Treatment('2018')) + C(inspector) + C(establishment_id)")]:
    m = smf.ols(f, M).fit(cov_type="cluster", cov_kwds=cl)
    ci = m.conf_int()
    res[nm] = {y: f"{100 * m.params[KY.format(y)]:+.1f}" for y in yrs}
print("\nChange in the share of inspections with a Priority violation relative to 2018 (percentage points, "
      "linear probability model, SEs clustered by establishment):")
print(pd.DataFrame(res).T.to_string())
m_full = smf.ols("pri ~ C(yr, Treatment('2018')) + C(inspector) + C(establishment_id)", M).fit(
    cov_type="cluster", cov_kwds=cl)
k19, k25 = KY.format(2019), KY.format(2025)
print(f"  full model 2019 vs 2018: {100 * m_full.params[k19]:+.1f} pp (95% CI {100 * m_full.conf_int().loc[k19, 0]:+.1f} "
      f"to {100 * m_full.conf_int().loc[k19, 1]:+.1f}); 2025 vs 2018: {100 * m_full.params[k25]:+.1f} pp "
      f"({100 * m_full.conf_int().loc[k25, 0]:+.1f} to {100 * m_full.conf_int().loc[k25, 1]:+.1f})")
# 2020
print("\n2020 (COVID-19):")
mon = R[R.year.between(2019, 2021)].groupby([R.year.astype(int), R.inspection_date.dt.month]).size().unstack(0)
print("R inspections by month (columns: year):")
print(mon.fillna(0).astype(int).T.to_string())
d20 = R[R.year == 2020].inspection_date.sort_values().drop_duplicates()
gaps = d20.diff().dt.days
big = [(d20.iloc[i - 1].date(), d20.iloc[i].date(), int(gaps.iloc[i])) for i in range(1, len(d20)) if gaps.iloc[i] >= 10]
print("gaps of 10+ days between R inspection dates in 2020 (last before, first after, days):", big)
cov = R.custom_comments.fillna("").str.contains(r"covid|corona ?virus|pandemic", case=False)
print(f"R notes mentioning COVID-19: {int(cov.sum())} ({R[cov].year.astype(int).value_counts().sort_index().to_dict()}); "
      f"records saying the establishment was closed for COVID-19 and not inspected: "
      f"{int((cov & noinsp).sum())}")
for y in (2019, 2020, 2021):
    d = R[R.year == y]
    print(f"  {y}: {len(d)} inspections, mean {d.score_std.mean():.2f}, any Priority {100 * d.pri.mean():.1f}%, "
          f"zero {100 * d.zero.mean():.1f}%; Salley/Goldston/Lerma share "
          f"{100 * d.inspector.isin([SALLEY, GOLDSTON, 'David Lerma']).mean():.0f}%")
a20 = R[(R.inspection_date >= "2020-03-16") & (R.inspection_date <= "2020-06-30")]
a19 = R[(R.inspection_date >= "2019-03-16") & (R.inspection_date <= "2019-06-30")]
print(f"  Mar 16-Jun 30: 2019 {len(a19)} inspections (Priority {100 * a19.pri.mean():.0f}%), "
      f"2020 {len(a20)} inspections (Priority {100 * a20.pri.mean():.0f}%)")

# =================================================================================================== [8]
head("[8] Frisco side by side: comparable sets")
spec = importlib.util.spec_from_file_location("frisco_common", FRISCO / "analysis" / "common.py")
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)
FI, FV = fc.ins, fc.v
FR = FI[(FI.permit_category == "Food establishment") & (FI.purpose == "Routine")
        & ~FI.food_type.isin(["Institutional", "Convenience", "Grocery"])].copy()
FU = fc.U.copy()
FVr = FV[FV.inspection_id.isin(FR.inspection_id)]
FO = FVr[FVr.status == "OUT"]
FX = item_matrix(FO, "inspection_id", FR.inspection_id)
FR["pri"] = FX.loc[FR.inspection_id, range(1, 21)].max(axis=1).to_numpy()
print(f"Frisco restaurant universe (inspectors_and_public_view.py): {len(FR):,} routine inspections at "
      f"{FR.permit_id.nunique()} permits, {FR.inspection_date.min():%Y-%m-%d} to {FR.inspection_date.max():%Y-%m-%d}; "
      f"with a food-type label (common.U): {len(FU):,} at {FU.permit_id.nunique()}")
RPW = RP[RP.inspection_date >= F_START].copy()
R24 = R[R.year >= 2024]
R24w = R[R.inspection_date >= F_START]
print(f"McKinney comparison set A: R inspections 2024-2026 with a report, score_printed: {len(RP):,} inspections at "
      f"{RP.establishment_id.nunique()} establishments ({RP.inspection_date.min():%Y-%m-%d} to {RP.inspection_date.max():%Y-%m-%d})")
print(f"McKinney comparison set B: the same, inside Frisco's window (from {F_START:%Y-%m-%d}; McKinney data end "
      f"{MK_END:%Y-%m-%d}): {len(RPW):,} inspections at {RPW.establishment_id.nunique()} establishments; "
      f"{pct(len(RPW), len(R24w))} R inspections in the window have a report")
print(f"Coverage: {pct(len(RP), len(R24))} R inspections 2024-2026 have a report "
      f"(2024 {rep_by_year.get(2024.0)}, 2025 {rep_by_year.get(2025.0)}, 2026 {rep_by_year.get(2026.0)})")
w, wo = R24[R24.score_printed.notna()], R24[R24.score_printed.isna()]
print(f"Selection check, 2024-2026, layer score_std: with a report {w.score_std.mean():.2f} "
      f"(zero {100 * (w.score_std == 0).mean():.1f}%, 15+ {100 * (w.score_std >= 15).mean():.1f}%) vs without "
      f"{wo.score_std.mean():.2f} (zero {100 * (wo.score_std == 0).mean():.1f}%, 15+ {100 * (wo.score_std >= 15).mean():.1f}%)")
for y in (2024, 2025, 2026):
    a, b = R24[(R24.year == y) & R24.score_printed.notna()], R24[(R24.year == y) & R24.score_printed.isna()]
    print(f"  {y}: with report {a.score_std.mean():.2f} (n={len(a)}), without {b.score_std.mean():.2f} (n={len(b)})")
print(f"Reports exist only for permits still on the city's map: without-report inspections 2024-2026 are at "
      f"{wo.establishment_id.nunique()} establishments, {wo.establishment_id.isin(w.establishment_id).sum()} of "
      f"the {len(wo)} inspections at establishments that also have reports")

# =================================================================================================== [9]
head("[9] Headline numbers, side by side (routine restaurant inspections)")
XP = item_matrix(vP, "inspection_number", RP.inspection_number)         # layer + COS items, reports era
RP["pri_all"] = XP.loc[RP.inspection_number, range(1, 21)].max(axis=1).to_numpy()
RPW["pri_all"] = RP.set_index("inspection_number").pri_all.reindex(RPW.inspection_number).to_numpy()


def headline(label, score, pri, est, per):
    s = np.asarray(score, float)
    m = cshare(s, est)
    z = cshare(s == 0, est)
    h = cshare(s >= 15, est)
    p = cshare(pri, est)
    e = pd.Series(np.asarray(pri)).groupby(np.asarray(est)).max()
    print(f"  {label}")
    print(f"     n={len(s):,} at {len(e):,} establishments ({len(s) / len(e):.1f} per establishment over {per})")
    print(f"     mean {m[0]:.2f} (95% CI {m[1]:.2f}-{m[2]:.2f}), median {np.median(s):.0f}; at 0: {fci(z)}; "
          f"15+: {fci(h)}; 30+: {int((s >= 30).sum())} ({100 * (s >= 30).mean():.1f}%)")
    print(f"     any Priority violation: {int(np.sum(pri)):,} = {fci(p)}; establishments with at least one: "
          f"{pct(int(e.sum()), len(e))}")
    return {"n": len(s), "mean": m, "zero": z, "ge15": h, "pri": p, "est_pri": e.mean(), "n_est": len(e)}


H = {}
H["F"] = headline("Frisco, Jun 23 2025-Sep 24 2026 (official score, COS items included)", FR.score, FR.pri,
                  FR.permit_id, "15 months")
H["A"] = headline("McKinney A, 2024-2026 with a report (score_printed; COS items included)", RP.score_printed,
                  RP.pri_all, RP.establishment_id, "32 months")
H["B"] = headline("McKinney B, Frisco's window (Jun 23 2025-Sep 1 2026) with a report (score_printed)",
                  RPW.score_printed, RPW.pri_all, RPW.establishment_id, "14 months")
H["A_std"] = headline("McKinney A on the layer score (score_std; COS left out) - NOT comparable, shown for size of "
                      "the COS effect", RP.score_std, RP.pri, RP.establishment_id, "32 months")
H["R"] = headline("McKinney R 2017-2026, layer score_std (lower bound)", R.score_std, R.pri, R.establishment_id,
                  "nine years")
ratio = H["B"]["mean"][0] / H["F"]["mean"][0]
print(f"McKinney B mean / Frisco mean: {ratio:.2f}; any-Priority share McKinney B {100 * H['B']['pri'][0]:.1f}% "
      f"vs Frisco {100 * H['F']['pri'][0]:.1f}%")
# does McKinney's higher mean come from more items or bigger items?
FR_items = FX.loc[FR.inspection_id].sum(axis=1)
RPW_items = XP.loc[RPW.inspection_number].sum(axis=1)
print(f"items cited per inspection: McKinney B {RPW_items.mean():.2f}, Frisco {FR_items.mean():.2f}; "
      f"Priority items per inspection: McKinney B {XP.loc[RPW.inspection_number, range(1, 21)].sum(axis=1).mean():.2f}, "
      f"Frisco {FX.loc[FR.inspection_id, range(1, 21)].sum(axis=1).mean():.2f}")
FRj = FR[FR.inspector.isin(F_NEW)]
FRo = FR[~FR.inspector.isin(F_NEW)]
print(f"Frisco without its three summer-2026 inspectors: {len(FRo):,} inspections, mean {FRo.score.mean():.2f}, "
      f"any Priority {100 * FRo.pri.mean():.1f}%, 15+ {100 * (FRo.score >= 15).mean():.1f}%")

# =================================================================================================== [10]
head("[10] What inspectors find most, side by side (share of routine restaurant inspections citing the item)")
it = pd.DataFrame({"what": [SHORT.get(n, TITLE[n][:34]) for n in range(1, 48)],
                   "McK_B_%": (XP.loc[RPW.inspection_number].mean() * 100).round(1).to_numpy(),
                   "McK_A_%": (XP.loc[RP.inspection_number].mean() * 100).round(1).to_numpy(),
                   "McK_R_layer_%": (XL.mean() * 100).round(1).to_numpy(),
                   "Frisco_%": (FX.loc[FR.inspection_id].mean() * 100).round(1).to_numpy()},
                  index=range(1, 48))
it["McK_B_rank"] = it["McK_B_%"].rank(ascending=False, method="min").astype(int)
it["Frisco_rank"] = it["Frisco_%"].rank(ascending=False, method="min").astype(int)
print("Priority items (1-20), ordered by Frisco:")
print(it.loc[1:20].sort_values("Frisco_%", ascending=False).head(10).to_string())
print("\nAll items, top 12 in McKinney B:")
print(it.sort_values("McK_B_%", ascending=False).head(12).to_string())
print("\nAll items, top 12 in Frisco:")
print(it.sort_values("Frisco_%", ascending=False).head(12).to_string())
topF = list(it.loc[1:20].sort_values("Frisco_%", ascending=False).head(4).index)
topM = list(it.loc[1:20].sort_values("McK_B_%", ascending=False).head(4).index)
print(f"top four Priority items: Frisco {topF}, McKinney B {topM}")
# pests with the same classifier in both cities
F34 = FVr[FVr.item_number == 34].copy()
fcl = classify_pests(F34.comments)
F34 = pd.concat([F34, fcl.set_index(F34.index)], axis=1)
fp_all = F34[F34["pests recorded"]].inspection_id.nunique()
fp_out = F34[F34["pests recorded"] & (F34.status == "OUT")].inspection_id.nunique()
mp = v34[v34["pests recorded"] & v34.inspection_number.isin(RPW.inspection_number)].inspection_number.nunique()
print(f"\npests recorded under item 34 (same keyword rules in both cities):")
print(f"  Frisco: {pct(fp_all, len(FR))} incl. 0-point warnings; scored only: {pct(fp_out, len(FR))}; "
      f"item 34 scored at all: {pct(int(FX.loc[FR.inspection_id, 34].sum()), len(FR))}")
print(f"  McKinney B: {pct(mp, len(RPW))}; item 34 cited at all: {pct(int(XP.loc[RPW.inspection_number, 34].sum()), len(RPW))}")
print(f"  McKinney R 2017-2026: {pct(pestI.inspection_number.nunique(), nR)}")
fpr = F34[F34["pests recorded"]]
print(f"  Frisco by type (inspections): " + ", ".join(
    f"{k} {fpr[fpr[k]].inspection_id.nunique()}" for k in PEST))
mpr = v34[v34["pests recorded"] & v34.inspection_number.isin(RPW.inspection_number)]
print(f"  McKinney B by type (inspections): " + ", ".join(
    f"{k} {mpr[mpr[k]].inspection_number.nunique()}" for k in PEST))
# COS and repeat
FP = FO[FO.item_category == "Priority"]
print(f"\nPriority entries marked corrected on site: Frisco {pct(int(FP.corrected_on_site.sum()), len(FP))}; "
      f"McKinney B {pct(int(vP[(vP.item_number <= 20) & vP.inspection_number.isin(RPW.inspection_number)].corrected_on_site.sum()), int(((vP.item_number <= 20) & vP.inspection_number.isin(RPW.inspection_number)).sum()))}")
f_rep = FVr[FVr.repeat == True].inspection_id.nunique()  # noqa: E712
m_rep = int(RPW.report_repeat_items.fillna("").ne("").sum())
print(f"inspections with at least one Repeat mark: Frisco {pct(f_rep, len(FR))}; McKinney B {pct(m_rep, len(RPW))}")
# cold holding temperatures, both cities
F2 = FO[FO.item_number == 2].copy()
F2["t"] = F2.comments.map(lambda s: temps(s, max))
fw2 = F2.groupby("inspection_id").t.max().dropna()
mw2 = v2[v2.inspection_number.isin(RPW.inspection_number)].groupby("inspection_number").t.max().dropna()
print(f"cold holding, warmest food in the note: Frisco median {fw2.median():.0f}F (n={len(fw2)}); "
      f"McKinney B median {mw2.median():.0f}F (n={len(mw2)}); McKinney 2017-2026 {w2.median():.0f}F (n={len(w2)})")

# =================================================================================================== [11]
head("[11] The cuisine gap, side by side (McKinney: our codes; Frisco: the city's food-type labels)")
FMAP = {"Thai": "Other Asian", "Vietnamese": "Other Asian", "Korean": "Other Asian", "Fusion": "Other Asian",
        "Asian (unspecified)": "Other Asian", "Other": "Other cuisine"}
FU["g"] = FU.group.replace(FMAP)
FU = FU[FU.g.notna() & (FU.g != "Hotel kitchens (Continental)")].copy()
RP["g"], RPW["g"], R["g"] = RP.group, RPW.group, R.group
ORDER = ["Indian", "Other Asian", "Japanese", "Chinese", "Mexican/Tex-Mex/Latin", AM,
         "Mediterranean/Middle Eastern", "Pizza/Italian", "Other cuisine", "Fast Food",
         "Coffee/Cafe/Bakery/Dessert", "Deli/Sandwich/Salad"]
SETS = {"McK A (printed)": (RP, "score_printed", "establishment_id"),
        "McK B (printed)": (RPW, "score_printed", "establishment_id"),
        "McK R 2017-26 (std)": (R, "score_std", "establishment_id"),
        "Frisco": (FU, "score", "permit_id")}
BOOT = {}
rows = []
for g in ORDER:
    row = {"group": g}
    for nm, (d, sc, est) in SETS.items():
        dd = d[d.g == g]
        e = est_means(dd[sc], dd[est])
        m, bs = boot_est(e)
        BOOT[(nm, g)] = bs
        row[f"{nm}: est"] = len(e)
        row[f"{nm}: mean"] = f"{m:.1f} ({np.percentile(bs, 2.5):.1f}-{np.percentile(bs, 97.5):.1f})"
        row[f"{nm}: 15+%"] = round(100 * (dd[sc] >= 15).mean(), 1)
    rows.append(row)
ct = pd.DataFrame(rows).set_index("group")
print("Mean of each establishment's average score (95% bootstrap CI), establishments, share of inspections at 15+")
for nm in SETS:
    print(f"\n-- {nm}")
    print(ct[[c for c in ct.columns if c.startswith(nm + ":")]].rename(columns=lambda c: c.split(": ")[1]).to_string())
print("\nRatios and differences of establishment-level means (groups bootstrapped independently):")
GAP = {}
for nm in SETS:
    for a, b in (("Indian", AM), ("Indian", "Fast Food"), ("Other Asian", AM), ("Japanese", AM), ("Chinese", AM),
                 ("Fast Food", AM)):
        ba, bb = BOOT[(nm, a)], BOOT[(nm, b)]
        d, sc, est = SETS[nm]
        ma = est_means(d[d.g == a][sc], d[d.g == a][est]).mean()
        mb = est_means(d[d.g == b][sc], d[d.g == b][est]).mean()
        GAP[(nm, a, b)] = (ma / mb, ma - mb)
        print(f"  {nm:20s} {a:12s} / {b[:9]:9s}: ratio {ma / mb:.2f} ({np.percentile(ba / bb, 2.5):.2f}-"
              f"{np.percentile(ba / bb, 97.5):.2f}), difference {ma - mb:+.1f} ({np.percentile(ba - bb, 2.5):+.1f} to "
              f"{np.percentile(ba - bb, 97.5):+.1f}) points")


def adjusted(d, score, est, chain, label):
    d = d.copy()
    d["gg"] = d.g.replace({AM: "AAmerican"})
    d["ch"] = d[chain].fillna("unknown")
    f = f"{score} ~ C(gg, Treatment('AAmerican')) + C(inspector) + C(ch) + C(month)"
    m = smf.glm(f, d, family=sm.families.Poisson()).fit(cov_type="cluster",
                                                         cov_kwds={"groups": pd.factorize(d[est])[0]})
    out = []
    for g in ("Indian", "Other Asian", "Japanese", "Chinese", "Fast Food"):
        k = f"C(gg, Treatment('AAmerican'))[T.{g}]"
        ci = np.exp(m.conf_int().loc[k])
        out.append(f"{g} {np.exp(m.params[k]):.2f} ({ci[0]:.2f}-{ci[1]:.2f})")
    print(f"  {label}: " + " | ".join(out))


print("\nAdjusted ratio to American (Poisson; inspector, chain status and month; SEs clustered by establishment):")
adjusted(RP, "score_printed", "establishment_id", "chain2", "McKinney A")
adjusted(RPW, "score_printed", "establishment_id", "chain2", "McKinney B")
adjusted(FU, "score", "permit_id", "chain_type", "Frisco    ")
top2 = {nm: ct[f"{nm}: mean"].str.split(" ").str[0].astype(float).sort_values(ascending=False).head(4).index.tolist()
        for nm in SETS}
print("four highest-scoring groups:", top2)

# =================================================================================================== [12]
head("[12] Inspector pattern, side by side")


def r2(d, score, col):
    dd = d.dropna(subset=[col])
    return smf.ols(f"np.log1p({score}) ~ C({col})", dd).fit().rsquared


for nm, (d, sc, est) in {"McKinney A (printed)": (RP, "score_printed", "establishment_id"),
                         "McKinney R 2017-26 (std)": (R, "score_std", "establishment_id"),
                         "Frisco (labelled restaurants)": (FU, "score", "permit_id")}.items():
    print(f"  {nm:30s} R-squared, log(1+score): inspector {r2(d, sc, 'inspector'):.3f}, cuisine group "
          f"{r2(d, sc, 'g'):.3f}  (n={len(d):,}, inspectors {d.inspector.nunique()})")
for nm, (d, sc) in {"McKinney A": (RP, "score_printed"), "Frisco": (FR, "score")}.items():
    im = d.groupby("inspector")[sc].agg(["size", "mean"])
    im = im[im["size"] >= 100].sort_values("mean")
    print(f"  {nm}: inspector means (100+ restaurant inspections): "
          + ", ".join(f"{k} {r['mean']:.1f} (n={int(r['size'])})" for k, r in im.iterrows()))


def pairs(df, score, est, sort, min_gap=None):
    d = df.sort_values([est] + sort).copy()
    g = d.groupby(est)
    d["prev_score"] = g[score].shift()
    d["prev_insp"] = g.inspector.shift()
    d["gap"] = (d.inspection_date - g.inspection_date.shift()).dt.days
    d = d[d.prev_score.notna()]
    if min_gap:
        d = d[d.gap.between(min_gap, 548)]
    d["delta"] = d[score] - d.prev_score
    return d


def newcomer(p, start, newcomers, est, cohort, label):
    q = p[p.inspection_date >= start]
    new = q[q.inspector.isin(newcomers) & ~q.prev_insp.isin(cohort)].groupby(est).head(1)
    same = q[~q.inspector.isin(cohort) & (q.inspector == q.prev_insp)].groupby(est).head(1)
    m1, l1, h1, b1 = boot_mean(new.delta)
    m0, l0, h0, b0 = boot_mean(same.delta)
    bd = b1 - b0
    print(f"  {label}: newcomer's visit {len(new)} establishments {new.prev_score.mean():.2f} -> "
          f"{(new.prev_score + new.delta).mean():.2f}, change {m1:+.2f} "
          f"({l1:+.2f} to {h1:+.2f}), rose {(new.delta > 0).sum()}, fell {(new.delta < 0).sum()}; "
          f"same incumbent {len(same)}: {m0:+.2f} ({l0:+.2f} to {h0:+.2f}); difference {m1 - m0:+.2f} "
          f"({np.percentile(bd, 2.5):+.2f} to {np.percentile(bd, 97.5):+.2f})")
    return m1, m0, len(new)


print("\nSame restaurants, next routine inspection by a newcomer vs by the same incumbent (Frisco's test):")
fp = pairs(FR, "score", "permit_id", ["inspection_date", "time_in_24h"])
NVF = newcomer(fp, pd.Timestamp("2026-07-01"), F_NEW, "permit_id", F_NEW,
               "Frisco, three inspectors first seen Jul-Aug 2026, from Jul 1 2026 (official score)")
mp_std = pairs(R, "score_std", "establishment_id", ["inspection_date", "time_in"], min_gap=30)
NVM = newcomer(mp_std, pd.Timestamp("2025-09-04"), [ADAGBON], "establishment_id", NEW25,
               "McKinney, Emmanuel Adagbon (first seen Sep 4 2025), score_std")
newcomer(mp_std, pd.Timestamp("2025-09-04"), [SMITH, GUIDRY], "establishment_id", NEW25,
         "McKinney, Billy Smith and Nala Guidry (first seen Jan/Mar 2026), score_std")
Rb = R[R.score_printed.notna()]
mp_pr = pairs(R.assign(sp=R.score_printed), "sp", "establishment_id", ["inspection_date", "time_in"], min_gap=30)
mp_pr = mp_pr[mp_pr.prev_score.notna() & mp_pr.sp.notna()]
newcomer(mp_pr, pd.Timestamp("2025-09-04"), [ADAGBON], "establishment_id", NEW25,
         "McKinney, Adagbon, pairs where both reports exist (score_printed)")
before = R[(R.inspection_date >= "2024-09-04") & (R.inspection_date < "2025-09-04")]
since = R[R.inspection_date >= "2025-09-04"]
print(f"McKinney restaurant mean score_std, 12 months before Sep 4 2025: {before.score_std.mean():.2f} ({len(before)}); "
      f"since: {since.score_std.mean():.2f} ({len(since)}); since, Adagbon {since[since.inspector == ADAGBON].score_std.mean():.2f} "
      f"({(since.inspector == ADAGBON).sum()}), everyone else {since[since.inspector != ADAGBON].score_std.mean():.2f}")
print(f"  share of 15+ scores since Sep 4 2025 written by Adagbon: "
      f"{pct(int((since[since.inspector == ADAGBON].score_std >= 15).sum()), int((since.score_std >= 15).sum()))}; "
      f"his share of inspections {100 * (since.inspector == ADAGBON).mean():.0f}%")
# same-establishment inspector effects, McKinney R, establishment + year effects
M2 = R.copy()
M2["insp"] = M2.inspector.where(M2.inspector.map(M2.inspector.value_counts()) >= 100, "other")
g = M2.establishment_id
X = pd.get_dummies(M2.insp, dtype=float).drop(columns=[SALLEY])
X = pd.concat([X, pd.get_dummies(M2.year.astype(int).astype(str), prefix="y", drop_first=True, dtype=float)], axis=1)
Yw = M2.score_std - M2.groupby(g).score_std.transform("mean")
Xw = X - X.groupby(g).transform("mean")
fe = sm.OLS(Yw, Xw).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(g)[0]})
lv = [c for c in X.columns if not c.startswith("y_") and c != "other"]
print("McKinney R, same establishment and year, points relative to Shane Salley (establishment FE):",
      {k: f"{fe.params[k]:+.1f} ({fe.conf_int().loc[k, 0]:+.1f} to {fe.conf_int().loc[k, 1]:+.1f})" for k in lv})

# =================================================================================================== [13]
head("[13] Closures, side by side")
CLO = pd.read_csv(HERE / "closures_reviewed.csv", dtype=str)
CLO = CLO[CLO.classification == "closed or suspended at this inspection"].drop_duplicates("inspection_number")
CLO["type"] = np.where(CLO.reason.str.contains("score above 30"), "score-based", "hazard-based")
CLO = CLO.merge(ins[["inspection_number", "inspection_date", "time_in", "establishment_id", "score_std", "score_printed",
                 "routine"]], on="inspection_number", suffixes=("_rev", ""))
CLO["in_R"] = CLO.inspection_number.isin(R.inspection_number)
A_all = ins[ins.exclude_reason.isna() & ins.inspection_date.notna()]


def next_days(row):
    s = A_all[(A_all.establishment_id == row.establishment_id) & (A_all.inspection_number != row.inspection_number)
              & ((A_all.inspection_date > row.inspection_date)
                 | ((A_all.inspection_date == row.inspection_date) & (A_all.time_in > row.time_in)))]
    return (s.inspection_date.min() - row.inspection_date).days if len(s) else np.nan


CLO["next_days"] = CLO.apply(next_days, axis=1)
routine_all = ins[ins.routine]
print(f"McKinney: {len(CLO)} inspections record a closure or suspension in the notes (no closure field), "
      f"{CLO.routine.sum()} of them routine; {CLO.in_R.sum()} in the restaurant universe R")
print(f"  per 1,000 routine inspections: all permits {1000 * CLO.routine.sum() / len(routine_all):.1f} "
      f"({len(routine_all):,}); restaurants {1000 * CLO.in_R.sum() / nR:.1f}")
print("  by type:", CLO.type.value_counts().to_dict())
hz = CLO[CLO.type == "hazard-based"]
water = hz.reason.str.contains("water|leak", case=False)
print(f"  hazard-based reasons: no hot or running water or a leak {pct(int(water.sum()), len(hz))}; "
      f"pests {int(hz.reason.str.contains('dropping|roach|ant |fly|insect', case=False).sum())}; "
      f"refrigeration {int(hz.reason.str.contains('cooler|refrig', case=False).sum())}")
for t, d in CLO.groupby("type"):
    print(f"  {t}: next published record at the establishment within 1 day {int((d.next_days <= 1).sum())}, within 14 "
          f"days {int((d.next_days <= 14).sum())}, none later {int(d.next_days.isna().sum())}, of {len(d)}; "
          f"median days to next record {d.next_days.median():.0f}")
Cw = CLO[CLO.inspection_date >= F_START]
rw = routine_all[routine_all.inspection_date >= F_START]
print(f"  in Frisco's window (from Jun 23 2025): {len(Cw)} closures ({Cw.type.value_counts().to_dict()}) in "
      f"{len(rw):,} routine inspections = {1000 * Cw.routine.sum() / len(rw):.1f} per 1,000; restaurants "
      f"{Cw.in_R.sum()} in {len(R24w):,}; with a record within 14 days {int((Cw.next_days <= 14).sum())}")
thr = routine_all[routine_all.inspection_date >= "2023-11-07"]
over = thr[thr.score_std >= 30]
over_p = thr[thr.score_printed >= 30]
print(f"  since Ord. 2023-11-075 (Nov 7 2023, closure at 30+): routine inspections at 30+ on score_std {len(over)}, "
      f"closure recorded {int(over.closure_recorded.sum())}; at 30+ on score_printed {len(over_p)} "
      f"(closure recorded {int(over_p.closure_recorded.sum())})")
# Frisco
FC = pd.read_csv(FRISCO / "analysis" / "closures_reviewed.csv", parse_dates=["inspection_date"])
FCc = FC[FC.classification == "closed or suspended at this inspection"].copy()
FIr = FI[FI.purpose == "Routine"]


def f_next(row):
    s = FI[(FI.permit_id == row.permit_id) & (FI.inspection_id != row.inspection_id)
           & ((FI.inspection_date > row.inspection_date)
              | ((FI.inspection_date == row.inspection_date) & (FI.purpose == "Reinspection")))]
    return (s.inspection_date.min() - row.inspection_date).days if len(s) else np.nan


FCc["next_days"] = FCc.apply(f_next, axis=1)
FCc["reins"] = [bool(len(FI[(FI.permit_id == r.permit_id) & (FI.purpose == "Reinspection")
                            & (FI.inspection_date >= r.inspection_date)])) for r in FCc.itertuples()]
FCc["in_R"] = FCc.inspection_id.isin(FR.inspection_id)
print(f"Frisco: {len(FCc)} routine inspections record a closure or suspension not lifted during the visit "
      f"(Frisco's report: 21 to 23, two could be read as lifted the same day); {int(FCc.in_R.sum())} in its restaurant "
      f"universe; per 1,000 routine inspections {1000 * len(FCc) / len(FIr):.1f} ({len(FIr):,} routine)")
print(f"  followed by a published reinspection: {int(FCc.reins.sum())}; any published record within 14 days: "
      f"{int((FCc.next_days <= 14).sum())}; none later: {int(FCc.next_days.isna().sum())}")
print(f"  reason mentions no hot water: {int(FCc.reason.str.contains('hot water').sum())}; mentions a score: "
      f"{int(FCc.reason.str.contains('score').sum())}")
f30 = FIr[FIr.score > 30]
print(f"  routine inspections above 30: {len(f30)}; with a recorded closure {int(f30.inspection_id.isin(FCc.inspection_id).sum())}")

# =================================================================================================== [14]
head("[14] Public access, side by side")
P = ins[ins.exclude_reason.isna()]
pub = P.groupby("year").has_report.mean().mul(100).round(1)
print("McKinney: share of inspections (all permits) with a published report, by year (%):",
      {int(k): v for k, v in pub.items()})
print(f"  all years: {pct(int(P.has_report.sum()), len(P))}; restaurant inspections before 2024: "
      f"{pct(int(R[R.year < 2024].has_report.sum()), int((R.year < 2024).sum()), 2)}")
rep = pd.read_csv(HERE.parent / "data" / "reports.csv", dtype=str)
rp = rep[rep.doc_type == "inspection_report"]
plc = rp.placement.value_counts()
print(f"  report PDFs on the city's map: {len(rp):,}; attached to their own permit's record: "
      f"{pct(int(plc.get('own permit', 0)), len(rp))}; to another establishment's record: "
      f"{pct(int(plc.get('other establishment', 0)), len(rp))}; same premises, other permit: {int(plc.get('same premises, other permit', 0))}")
print(f"  other attachments: {(rep.doc_type != 'inspection_report').sum()} copies of one e-mail (contents not described)")
m = RP
eq = (m.score_layer == m.score_printed).sum()
print(f"  McKinney A: the layer's own total (score_layer, item 34 at 2 points, COS items left out) equals the printed "
      f"score in {pct(int(eq), len(m))}; layer lower {int((m.score_layer < m.score_printed).sum())}, "
      f"higher {int((m.score_layer > m.score_printed).sum())}")
z = m[m.score_layer == 0]
print(f"  McKinney A: inspections that are 0 in the layer but not on the report (COS items): "
      f"{pct(int((z.score_printed > 0).sum()), len(z))}")
pm = permits[permits.layer_avg_score.notna()].copy()
X_ = ins.assign(s=ins.score_printed.fillna(ins.score_layer))
rows_ = X_.loc[X_.index.repeat(X_.layer_rows.fillna(1).astype(int))].sort_values(["link_number", "inspection_date", "time_in"])
mean4 = rows_.groupby("link_number").tail(4).groupby("link_number").s.mean()
pm["mean4"] = pm.link_number.map(mean4)
pm["avg"] = pd.to_numeric(pm.layer_avg_score)
lr = X_[X_.routine].sort_values(["link_number", "inspection_date", "time_in"]).groupby("link_number").tail(1)
pm["latest"] = pm.link_number.map(lr.set_index("link_number").score_printed.fillna(lr.set_index("link_number").score_std))
pl = pm.dropna(subset=["latest"])
print(f"  map pop-up 'Avg': equals the floor of the mean of the last four inspection rows (duplicates included) for "
      f"{pct(int((np.floor(pm.mean4 + 1e-9) == pm.avg).sum()), len(pm))} current permits (public_record.py, working "
      f"from the raw layer, matches 754); lower than the latest routine score for {pct(int((pl.avg < pl.latest).sum()), len(pl))}, "
      f"lower by 5+ points {int((pl.latest - pl.avg >= 5).sum())}")
# Frisco
ow = FV.status == "OUT-W"
zF = FR[FR.score == 0]
zw = zF.inspection_id.isin(FV[ow].inspection_id)
print(f"Frisco: 0-point warnings, which its inspection web page does not display: {pct(int(ow.sum()), len(FV))} "
      f"of all violation entries; 0-score restaurant inspections with a warning in the PDF: {pct(int(zw.sum()), len(zF))}")
AF = FI.sort_values(["permit_id", "inspection_date", "time_in_24h", "purpose"])
yF = AF[AF.inspection_date >= "2026-01-01"]
latest = set(yF.groupby("permit_id").tail(1).inspection_id)
hiF = yF[yF.score > 30]
print(f"  2026 inspections above 30: {len(hiF)}; not shown in the default browse list (latest per establishment only): "
      f"{int((~hiF.inspection_id.isin(latest)).sum())}")

# =================================================================================================== [15]
head("[15] Frisco's six headline findings: same issue in McKinney?")
g_mb = GAP[("McK B (printed)", "Indian", AM)]
g_ma = GAP[("McK A (printed)", "Indian", AM)]
g_f = GAP[("Frisco", "Indian", AM)]
g_r = GAP[("McK R 2017-26 (std)", "Indian", AM)]
tbl = [
    ("1 Some kinds of restaurants score far worse (Indian ~4x American)", "YES, smaller ratio",
     f"Indian-coded restaurants are the highest group in McKinney too: {g_ma[0]:.1f}x American on printed scores "
     f"2024-26 (+{g_ma[1]:.1f} points; Frisco {g_f[0]:.1f}x, +{g_f[1]:.1f}); 2017-26 layer {g_r[0]:.1f}x (+{g_r[1]:.1f}). "
     f"Point gap similar, ratio smaller because McKinney's American baseline is higher. Chinese is not in McKinney's top group."),
    ("2 Who inspects matters about as much as cuisine; newcomers score the same places higher", "YES",
     f"Inspector explains more score variation than cuisine group in McKinney (R2 {r2(R, 'score_std', 'inspector'):.2f} vs "
     f"{r2(R, 'score_std', 'g'):.2f}, 2017-26); newcomer Adagbon's first visits ran {NVM[0]:+.1f} points above the same "
     f"restaurants' previous inspection vs {NVM[1]:+.1f} for same-inspector repeats (Frisco newcomers {NVF[0]:+.1f})."),
    ("3 Same problem written up different ways (health policy, warnings, Repeat flag)", "PARTLY",
     f"No 0-point warnings and no health-policy citations in McKinney; but corrected-on-site marks, which drop items from "
     f"the public layer score, come almost entirely from one inspector ({cos_by_insp.get(SALLEY, 0)} of "
     f"{int(RP.any_cos.sum())} inspections), and Repeat marking of recurring items varies by inspector (overall "
     f"{100 * rec.marked.mean():.0f}%)."),
    ("4 Closures only in free text; most without a published reinspection", "PARTLY",
     f"Closures are free text only here too ({len(CLO)} in nine years). Score-based closures ({(CLO.type == 'score-based').sum()}) "
     f"almost always have a next-day record ({int((CLO[CLO.type == 'score-based'].next_days <= 1).sum())}); hazard closures "
     f"({(CLO.type == 'hazard-based').sum()}, mostly no hot water) rarely do ({int((CLO[CLO.type == 'hazard-based'].next_days <= 14).sum())} "
     f"within 14 days)."),
    ("5 The public portal shows less than the reports do", "YES, different mechanism",
     f"No portal equivalent: the city's map page is 'temporarily unavailable'; the unlinked app attaches "
     f"{pct(int(plc.get('own permit', 0)), len(rp))} reports to their own permit, shows a rounded-down 4-inspection "
     f"average, and its layer drops corrected-on-site items; almost no reports before 2024."),
    ("6 What inspectors find most (surfaces, raw/RTE, chemicals, cold holding; ~1/3 with a Priority violation)",
     "YES for items; NO for the rate",
     f"Same top Priority items (McKinney {topM}, Frisco {topF}); but {100 * H['B']['pri'][0]:.0f}% of McKinney "
     f"restaurant inspections in Frisco's window had a Priority violation vs {100 * H['F']['pri'][0]:.0f}% in Frisco, "
     f"and corrected-on-site marks are rare in McKinney."),
]
for f, verdict, ev in tbl:
    print(f"\n{f}\n  -> {verdict}\n     {ev}")

print(f"\n[done in {time.time() - T0:.0f}s]")
