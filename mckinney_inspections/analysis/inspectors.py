"""Who inspects: inspector patterns in McKinney's published food-inspection records, Oct 2017 - Sep 2026.

Frisco's records showed that who inspects matters about as much as what kind of restaurant it is: three
inspectors who started in summer 2026 scored the same restaurants about 9 points higher. This script runs
the McKinney equivalent over nine years of the city layer (plus the report PDFs for 2024-2026).

Scores are score_std (layer items at form weights; items corrected on site are left out, so it is a lower
bound on the official score). "inspector" is the signer where a report exists, else the name in the layer,
which is the ASSIGNED inspector (data/inspections.csv; see build.py). Before 2024 almost no inspection has a
signed report, so for 2017-2023 the name is the assigned inspector.

Universe: common.R (routine restaurant inspections, 8,242 at 782 establishments) unless stated. Section [9]
(enforcement) uses every routine inspection.

Sections
  [0] coverage             [5] 2025-2026 newcomers (Adagbon, Smith, Guidry)
  [1] inspector summary    [6] Shane Salley at the same establishments
  [2] fixed-effect models  [7] listed vs signed inspector
  [3] consecutive visits   [8] corrected-on-site (COS) use, reports era
  [4] the 2018-2019 jump   [9] enforcement at or over the closure threshold

Everything here is a pattern in published records. The data cannot separate leniency or strictness from
assignment (who is sent where, and when) or from recording practice (what gets written into the layer).

Run from this folder:  python inspectors.py      (pandas, numpy, statsmodels; about a minute)
"""
import os
import sys
import warnings
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):  # one BLAS thread: fast and steady under load
    os.environ.setdefault(_v, "1")

import numpy as np
import pandas as pd
import statsmodels.api as sm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import R, ins, vio  # noqa: E402

warnings.filterwarnings("ignore")
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 400)
rng = np.random.default_rng(20260926)
HERE = Path(__file__).resolve().parent

SALLEY, GOLDSTON, LERMA, DALEY = "Shane Salley", "Victoria Goldston", "David Lerma", "Cathleen Daley"
ADAGBON, SMITH, GUIDRY = "Emmanuel Adagbon", "Billy Smith", "Nala Guidry"
NEW25 = [ADAGBON, SMITH, GUIDRY]
ORD_2019 = pd.Timestamp("2019-10-15")  # Ord. 2019-10-072: closure at 31 or more
MAIN = ["Shane Salley", "Victoria Goldston", "David Lerma", "Jadyn Starnes", "Cathleen Daley", "Josh Depuydt",
        "Emmanuel Adagbon", "Billy Smith", "Nala Guidry"]  # Richard Milam (25) and Shirley Snyder (1) too few

A = ins[ins.routine].copy()  # every routine inspection (void, test and non-permit rows are not routine)
R = R.copy()


def head(s):
    print(f"\n{'=' * 100}\n{s}\n{'=' * 100}")


def fmt_ci(m, lo, hi, d=2):
    return f"{m:+.{d}f} (95% CI {lo:+.{d}f} to {hi:+.{d}f})"


# ----------------------------------------------------------------------------------------- estimators
def within_ols(d, y, factor, ref, time="year", cluster="establishment_id", extra=None):
    """OLS of y on factor dummies (ref omitted) + time dummies with establishment fixed effects absorbed
    (within transformation, identical point estimates to establishment dummies). Cluster-robust SEs by
    establishment. Returns a table of factor coefficients with 95% CIs."""
    d = d.dropna(subset=[y, factor]).copy()
    X = pd.get_dummies(d[factor], dtype=float)
    X = X.drop(columns=[ref])
    lv = list(X.columns)
    X = pd.concat([X, pd.get_dummies(d[time].astype(str), prefix="t", drop_first=True, dtype=float)], axis=1)
    if extra is not None:
        X = pd.concat([X, extra.loc[d.index]], axis=1)
    g = d.establishment_id
    Yw = d[y].astype(float) - d.groupby(g)[y].transform("mean")
    Xw = X - X.groupby(g).transform("mean")
    keep = Xw.columns[(Xw.abs() > 1e-12).any()]
    Xw = Xw[keep]
    m = sm.OLS(Yw, Xw).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(d[cluster])[0]})
    ci = m.conf_int()
    lv = [c for c in lv if c in keep]
    n = d[factor].value_counts()
    return pd.DataFrame({"coef": m.params[lv], "lo": ci.loc[lv, 0], "hi": ci.loc[lv, 1], "p": m.pvalues[lv],
                         "inspections": n[lv].astype(int)}), m


def fe_poisson(d, y, factor, ref, time="year"):
    """Poisson with establishment and time dummies (ratios); establishments whose scores are all 0 carry
    no information and are dropped. Cluster-robust SEs by establishment."""
    d = d.dropna(subset=[y, factor]).copy()
    d = d[d.groupby("establishment_id")[y].transform("sum") > 0]
    Xi = pd.get_dummies(d[factor], dtype=float).drop(columns=[ref])
    lv = list(Xi.columns)
    X = pd.concat([Xi, pd.get_dummies(d[time].astype(str), prefix="t", drop_first=True, dtype=float),
                   pd.get_dummies(d.establishment_id, prefix="e", drop_first=True, dtype=float)], axis=1)
    X.insert(0, "const", 1.0)
    m = sm.GLM(d[y].astype(float), X, family=sm.families.Poisson()).fit(
        cov_type="cluster", cov_kwds={"groups": pd.factorize(d.establishment_id)[0]})
    ci = m.conf_int()
    return pd.DataFrame({"ratio": np.exp(m.params[lv]), "lo": np.exp(ci.loc[lv, 0]), "hi": np.exp(ci.loc[lv, 1]),
                         "inspections": d[factor].value_counts()[lv].astype(int)})


def boot_mean(x, B=4000):
    x = np.asarray(x, float)
    bs = x[rng.integers(0, len(x), (B, len(x)))].mean(1)
    return x.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5), bs


def boot_did(d1, d0, B=4000):
    m1, lo1, hi1, b1 = boot_mean(d1, B)
    m0, lo0, hi0, b0 = boot_mean(d0, B)
    bd = b1 - b0
    return (m1, lo1, hi1), (m0, lo0, hi0), (m1 - m0, np.percentile(bd, 2.5), np.percentile(bd, 97.5))


def make_pairs(df, min_gap=30, max_gap=548):
    """Consecutive routine inspections at the same establishment (gap 30-548 days)."""
    d = df.sort_values(["establishment_id", "inspection_date", "time_in"]).copy()
    g = d.groupby("establishment_id")
    for c in ("score_std", "inspector", "inspection_date", "zero"):
        d["prev_" + c] = g[c].shift()
    d["gap"] = (d.inspection_date - d.prev_inspection_date).dt.days
    d["delta"] = d.score_std - d.prev_score_std
    return d, d[d.prev_score_std.notna() & d.gap.between(min_gap, max_gap)].copy()


def sym_gap(p, a, b, B=2000):
    """(mean change a->b  -  mean change b->a) / 2: b's score level relative to a's at the same establishments,
    net of any common drift; cluster bootstrap over establishments."""
    ab = p[(p.prev_inspector == a) & (p.inspector == b)]
    ba = p[(p.prev_inspector == b) & (p.inspector == a)]
    if len(ab) < 10 or len(ba) < 10:
        return None
    est = (ab.delta.mean() - ba.delta.mean()) / 2
    E = pd.Index(sorted(set(ab.establishment_id) | set(ba.establishment_id)))
    s1 = ab.groupby("establishment_id").delta.agg(["sum", "size"]).reindex(E, fill_value=0)
    s2 = ba.groupby("establishment_id").delta.agg(["sum", "size"]).reindex(E, fill_value=0)
    W = rng.multinomial(len(E), np.full(len(E), 1 / len(E)), size=B)
    with np.errstate(invalid="ignore", divide="ignore"):
        bs = (W @ s1["sum"].to_numpy() / (W @ s1["size"].to_numpy())
              - W @ s2["sum"].to_numpy() / (W @ s2["size"].to_numpy())) / 2
    bs = bs[np.isfinite(bs)]
    return est, np.percentile(bs, 2.5), np.percentile(bs, 97.5), len(ab), len(ba), ab.delta.mean(), ba.delta.mean()


def next_visit_test(pairs, start, end, newcomers, label, cohort=None):
    """Frisco-style: first inspection by a newcomer at each establishment in [start, end] whose previous
    inspection was by an incumbent, against same-inspector repeat visits by incumbents in the same window.
    cohort = every newcomer of the period (kept out of the previous visit and out of the comparison groups)."""
    cohort = list(newcomers) if cohort is None else cohort
    w = pairs[(pairs.inspection_date >= start) & (pairs.inspection_date <= end)]
    new = w[w.inspector.isin(newcomers) & ~w.prev_inspector.isin(cohort)].groupby("establishment_id").head(1)
    same = w[~w.inspector.isin(cohort) & (w.inspector == w.prev_inspector)].groupby("establishment_id").head(1)
    other = w[~w.inspector.isin(cohort) & ~w.prev_inspector.isin(cohort)
              & (w.inspector != w.prev_inspector)].groupby("establishment_id").head(1)
    (m1, l1, h1), (m0, l0, h0), (dd, dl, dh) = boot_did(new.delta, same.delta)
    print(f"  {label}: window {start} to {end}")
    print(f"    newcomer's visit:          {len(new):4d} establishments  {new.prev_score_std.mean():5.2f} -> "
          f"{new.score_std.mean():5.2f}  change {fmt_ci(m1, l1, h1)};  rose {(new.delta > 0).sum()}, "
          f"fell {(new.delta < 0).sum()}, unchanged {(new.delta == 0).sum()}")
    print(f"    same incumbent again:      {len(same):4d} establishments  {same.prev_score_std.mean():5.2f} -> "
          f"{same.score_std.mean():5.2f}  change {fmt_ci(m0, l0, h0)}")
    if len(other):
        mo, lo_, ho, _ = boot_mean(other.delta)
        print(f"    one incumbent to another:  {len(other):4d} establishments  {other.prev_score_std.mean():5.2f} -> "
              f"{other.score_std.mean():5.2f}  change {fmt_ci(mo, lo_, ho)}")
    print(f"    difference-in-differences (newcomer minus same incumbent): {fmt_ci(dd, dl, dh)}")
    print(f"    median days since the previous inspection: newcomer's visit {new.gap.median():.0f}, "
          f"same incumbent {same.gap.median():.0f}")
    parts = []
    for k, v in new.groupby("prev_inspector"):
        if len(v) >= 20:
            mk, lk, hk, _ = boot_mean(v.delta)
            parts.append(f"{k} {v.prev_score_std.mean():.2f} -> {v.score_std.mean():.2f}, {fmt_ci(mk, lk, hk)} (n={len(v)})")
        else:
            parts.append(f"{k} {v.delta.mean():+.2f} (n={len(v)})")
    print("    newcomer's visit, by previous inspector:\n      " + "\n      ".join(parts))
    return new, same


# ============================================================================================ [0]
head("[0] Coverage")
print(f"Routine inspections (all permits): {len(A):,}, {A.inspection_date.min():%Y-%m-%d} to "
      f"{A.inspection_date.max():%Y-%m-%d}; restaurant universe R: {len(R):,} at "
      f"{R.establishment_id.nunique()} establishments")
dates = A.groupby("inspector").inspection_date.agg(["min", "max", "size"])
dates["share_of_all_routine"] = dates["size"] / len(A)
print("Each inspector's first and last routine inspection (all permits), count, share of all routine:")
print(dates.sort_values("min").assign(share_of_all_routine=lambda t: t.share_of_all_routine.round(3)).to_string())
sig = A.groupby("year").inspector_signed.apply(lambda s: s.notna().mean())
print("Share of routine inspections with a signed report, by year:", sig.round(3).to_dict())

# ============================================================================================ [1]
head("[1] Inspector summary, restaurant universe R (raw; reflects assignment as well as inspector)")
g = R.groupby("inspector").agg(inspections=("score_std", "size"), establishments=("establishment_id", "nunique"),
                               first=("inspection_date", "min"), last=("inspection_date", "max"),
                               mean=("score_std", "mean"), median=("score_std", "median"),
                               zero=("zero", "mean"), ge15=("ge15", "mean"))
g["share_of_R"] = g.inspections / len(R)
print(g.sort_values("mean").round(3).to_string())
print(f"All inspectors: mean {R.score_std.mean():.2f}, zero {R.zero.mean():.3f}, 15+ {R.ge15.mean():.3f}")
ga = A.groupby("inspector").agg(inspections=("score_std", "size"), mean=("score_std", "mean"),
                                zero=("zero", "mean"), ge15=("ge15", "mean"))
print("\nAll routine inspections (every permit type):")
print(ga.sort_values("mean").round(3).to_string())

print("\nRestaurant (R) mean by inspector and year (inspections in brackets); inspector levels are not constant:")
ym = R.pivot_table(index="year", columns="inspector", values="score_std", aggfunc="mean")
yn = R.pivot_table(index="year", columns="inspector", values="score_std", aggfunc="size")
yc = (ym.round(1).astype(str) + " [" + yn.fillna(0).astype(int).astype(str) + "]").where(yn.notna(), "")
print(yc[[c for c in MAIN if c in yc.columns]].to_string())

print("\nCaseload mix (R): share of each inspector's inspections at chains, fast food, and Indian/Chinese/"
      "Japanese/Other Asian (the highest-scoring groups citywide)")
HIGHG = ["Indian", "Chinese", "Japanese", "Other Asian"]
mix = R.groupby("inspector").agg(chain=("chain2", lambda s: (s == "chain").mean()),
                                 fast_food=("group", lambda s: (s == "Fast Food").mean()),
                                 asian_indian=("group", lambda s: s.isin(HIGHG).mean()))
print(mix.loc[MAIN].round(3).to_string())

# leave-one-inspector-out baseline: how do OTHER inspectors score this inspector's establishments?
rows = []
for x in MAIN:
    mine = R[R.inspector == x]
    oth = R[R.inspector != x]
    est_other = oth.groupby("establishment_id").score_std.mean()
    shared = mine.establishment_id.isin(est_other.index)
    rows.append({"inspector": x, "inspections": len(mine), "at_shared_establishments": int(shared.sum()),
                 "own_mean_there": mine[shared].score_std.mean(),
                 "others_mean_at_same_establishments": mine[shared].establishment_id.map(est_other).mean(),
                 "others_mean_elsewhere": oth[~oth.establishment_id.isin(mine.establishment_id)].score_std.mean()})
print("\nAssignment check: other inspectors' average at this inspector's establishments "
      "(weighted by this inspector's visits), vs other inspectors' average at establishments this inspector "
      "never visited:")
print(pd.DataFrame(rows).set_index("inspector").round(2).to_string())

# how much variation does each factor account for?
print("\nShare of variation in log(1+score) explained (OLS R-squared, R):")
y = np.log1p(R.score_std.astype(float))
for label, cols in (("inspector", ["inspector"]), ("cuisine group", ["group"]), ("year", ["year"]),
                    ("inspector + cuisine group", ["inspector", "group"]),
                    ("inspector + cuisine group + year", ["inspector", "group", "year"]),
                    ("establishment", ["establishment_id"])):
    X = pd.get_dummies(R[cols].astype(str), drop_first=True, dtype=float)
    m = sm.OLS(y, sm.add_constant(X)).fit()
    print(f"  {label:34s} R2 {m.rsquared:.3f}  adj R2 {m.rsquared_adj:.3f}")

print("\nCuisine vs inspector in one model: score ~ cuisine group + inspector + year (no establishment effects),"
      " cluster-robust by establishment; references American and Shane Salley")
X = pd.get_dummies(R[["group", "inspector", "year"]].astype(str), dtype=float)
X = X.drop(columns=["group_American (sit-down, bars, venues)", "inspector_Shane Salley", "year_2018.0"])
m = sm.OLS(R.score_std.astype(float), sm.add_constant(X)).fit(
    cov_type="cluster", cov_kwds={"groups": pd.factorize(R.establishment_id)[0]})
ci = m.conf_int()
for c in [c for c in X.columns if c.startswith("group_") or c.startswith("inspector_")]:
    nm = c.split("_", 1)[1]
    if nm in ("Richard Milam",):
        continue
    print(f"  {nm:34s} {fmt_ci(m.params[c], ci.loc[c, 0], ci.loc[c, 1])}")

# ============================================================================================ [2]
head("[2] Same-establishment comparisons: establishment + year fixed effects (R)")
k = R.groupby("establishment_id").inspector.nunique()
multi = R.establishment_id.map(k) >= 2
print(f"Establishments seen by 2+ inspectors: {(k >= 2).sum()} of {len(k)}; they account for {multi.sum():,} "
      f"of {len(R):,} inspections ({multi.mean():.1%}). Only these identify inspector differences.")
Rm = R[R.inspector.isin(MAIN)].copy()
t_ols, _ = within_ols(Rm, "score_std", "inspector", SALLEY)
t_zero, _ = within_ols(Rm, "zero", "inspector", SALLEY)
t_15, _ = within_ols(Rm, "ge15", "inspector", SALLEY)
t_poi = fe_poisson(Rm, "score_std", "inspector", SALLEY)
print("Relative to Shane Salley at the same establishments, same year (points; cluster-robust 95% CI):")
print(t_ols.round(3).to_string())
print("\nShare scoring 0, percentage points relative to Salley:")
print((t_zero[["coef", "lo", "hi"]] * 100).round(1).assign(inspections=t_zero.inspections).to_string())
print("\nShare scoring 15+, percentage points relative to Salley:")
print((t_15[["coef", "lo", "hi"]] * 100).round(1).assign(inspections=t_15.inspections).to_string())
print("\nPoisson (score ratio to Salley, establishment + year effects):")
print(t_poi.round(2).to_string())

print("\nSensitivity: year-quarter effects instead of year")
R["quarter"] = R.inspection_date.dt.to_period("Q").astype(str)
Rm = R[R.inspector.isin(MAIN)].copy()
tq, _ = within_ols(Rm, "score_std", "inspector", SALLEY, time="quarter")
print(tq.round(2)[["coef", "lo", "hi", "inspections"]].to_string())
print("\nSensitivity: every routine inspection (all permit types, incl. grocery, schools, markets)")
Am = A[A.inspector.isin(MAIN)].copy()
ta, _ = within_ols(Am, "score_std", "inspector", SALLEY)
print(ta.round(2)[["coef", "lo", "hi", "inspections"]].to_string())

print("\nPooled: each inspector vs all other inspectors combined (establishment + year effects, R)")
for x in [SALLEY, GOLDSTON, LERMA, ADAGBON, SMITH, GUIDRY, "Jadyn Starnes", DALEY, "Josh Depuydt"]:
    R["_is"] = np.where(R.inspector == x, x, "others")
    out = []
    for yv in ("score_std", "zero", "ge15"):
        t, _ = within_ols(R, yv, "_is", "others")
        r = t.loc[x]
        sc = 100 if yv != "score_std" else 1
        out.append(f"{yv}: {fmt_ci(r.coef * sc, r.lo * sc, r.hi * sc, 1 if sc == 100 else 2)}"
                   + (" pp" if sc == 100 else ""))
    print(f"  {x:18s} n={int((R.inspector == x).sum()):5d}  " + " | ".join(out))

# ============================================================================================ [3]
head("[3] Consecutive routine inspections at the same establishment (gap 30-548 days, R)")
seq, P = make_pairs(R)
print(f"Pairs: {len(P):,} (of {seq.prev_score_std.notna().sum():,} consecutive pairs; "
      f"{(seq.gap < 30).sum()} under 30 days and {(seq.gap > 548).sum()} over 548 days left out)")
P["switch"] = P.inspector != P.prev_inspector
for s, d in P.groupby("switch"):
    print(f"  {'inspector changed' if s else 'same inspector   '}: {len(d):5d} pairs, mean change "
          f"{d.delta.mean():+.2f}, mean |change| {d.delta.abs().mean():.2f}")
print("\nMean change in score, previous inspector (rows) -> next inspector (columns); pairs in brackets")
mm = P[P.inspector.isin(MAIN) & P.prev_inspector.isin(MAIN)]
mt = mm.pivot_table(index="prev_inspector", columns="inspector", values="delta", aggfunc="mean")
nt = mm.pivot_table(index="prev_inspector", columns="inspector", values="delta", aggfunc="size")
cell = mt.round(1).astype(str) + " [" + nt.fillna(0).astype(int).astype(str) + "]"
print(cell.where(nt >= 15, "").to_string())
print("\nSymmetric same-establishment gap vs Shane Salley: (change Salley->X  minus  change X->Salley) / 2")
for x in MAIN[1:]:
    r = sym_gap(P, SALLEY, x)
    if r:
        est, lo, hi, n1, n2, d1, d2 = r
        print(f"  {x:18s} {fmt_ci(est, lo, hi)}   Salley->X {d1:+.2f} ({n1} pairs), X->Salley {d2:+.2f} ({n2})")
r = sym_gap(P, GOLDSTON, ADAGBON)
if r:
    print(f"  Adagbon vs Goldston: {fmt_ci(*r[:3])} ({r[3]} and {r[4]} pairs)")
print("\nZero-score transitions: share of next inspections scoring 0 after a previous 0")
for x in [SALLEY, GOLDSTON, LERMA, "Jadyn Starnes", ADAGBON]:
    a = P[(P.prev_zero == 1) & (P.inspector == x) & (P.prev_inspector == x)]
    b = P[(P.prev_zero == 1) & (P.inspector == x) & (P.prev_inspector != x)]
    print(f"  {x:18s} same inspector before: {a.zero.mean():.2f} ({len(a)}), other inspector before: "
          f"{b.zero.mean():.2f} ({len(b)})")

# ============================================================================================ [4]
head("[4] The 2018 -> 2019 jump")
yr = R.groupby("year").score_std.agg(["mean", "size"])
print("Restaurant (R) mean by year:", {int(k): (round(float(v["mean"]), 2), int(v["size"])) for k, v in yr.iterrows()})
ya = A.groupby("year").score_std.mean()
print("All routine mean by year:", {int(k): round(float(v), 2) for k, v in ya.items()})
for y_ in (2018, 2019):
    d = R[R.year == y_]
    t = d.groupby("inspector").score_std.agg(["size", "mean"]).assign(share=lambda t: t["size"] / len(d))
    print(f"  {y_}: " + "; ".join(f"{i} {int(r['size'])} ({r['share']:.0%}) mean {r['mean']:.1f}"
                               for i, r in t.sort_values("size", ascending=False).iterrows()))
d19 = R[R.year == 2019]
d18 = R[R.year == 2018]
print(f"2019 without Goldston: {d19[d19.inspector != GOLDSTON].score_std.mean():.2f} "
      f"({(d19.inspector != GOLDSTON).sum()}); Goldston 2019: {d19[d19.inspector == GOLDSTON].score_std.mean():.2f} "
      f"({(d19.inspector == GOLDSTON).sum()})")
inc = [SALLEY, LERMA, DALEY]
print(f"Continuing inspectors (Salley, Lerma, Daley) 2018 -> 2019: "
      f"{d18[d18.inspector.isin(inc)].score_std.mean():.2f} ({d18.inspector.isin(inc).sum()}) -> "
      f"{d19[d19.inspector.isin(inc)].score_std.mean():.2f} ({d19.inspector.isin(inc).sum()})")
print(f"Inspectors who left by early 2019 (Daley, Depuydt): share of 2018 R inspections "
      f"{d18.inspector.isin([DALEY, 'Josh Depuydt']).mean():.1%}, their 2018 mean "
      f"{d18[d18.inspector.isin([DALEY, 'Josh Depuydt'])].score_std.mean():.2f}")

print("\nQuarterly restaurant mean by inspector, 2018Q1-2020Q1 (inspections in brackets)")
q = R[(R.inspection_date >= "2018-01-01") & (R.inspection_date < "2020-04-01")]
qm = q.pivot_table(index="quarter", columns="inspector", values="score_std", aggfunc="mean")
qn = q.pivot_table(index="quarter", columns="inspector", values="score_std", aggfunc="size")
qc = qm.round(1).astype(str) + " [" + qn.fillna(0).astype(int).astype(str) + "]"
qc = qc.where(qn.notna(), "")
qc["ALL"] = q.groupby("quarter").score_std.mean().round(2).astype(str)
print(qc.to_string())

print("\nItems rarely cited before September 2018: share of R inspections citing item 19 (water/plumbing/"
      "backflow), 35 (personal cleanliness) or 36 (wiping cloths), by inspector and half-year")
cited = set(vio[(vio.source == "layer") & vio.item_number.isin([19, 35, 36])].inspection_number)
R["cites_19_35_36"] = R.inspection_number.isin(cited).astype(int)
R["half"] = R.year.astype(int).astype(str) + "H" + np.where(R.inspection_date.dt.month <= 6, "1", "2")
h = R[(R.year >= 2018) & (R.year <= 2019) & R.inspector.isin([SALLEY, LERMA, DALEY, "Josh Depuydt", GOLDSTON])]
hm = h.pivot_table(index="half", columns="inspector", values="cites_19_35_36", aggfunc="mean")
hn = h.pivot_table(index="half", columns="inspector", values="cites_19_35_36", aggfunc="size")
print((hm.round(2).astype(str) + " [" + hn.fillna(0).astype(int).astype(str) + "]").where(hn.notna(), "").to_string())
lerma_first = R[R.inspector == LERMA].inspection_date.sort_values()
print(f"  Lerma's restaurant inspections: 2 in 2017, then from {lerma_first[lerma_first.dt.year >= 2018].min():%Y-%m-%d}")

print("\nSame-establishment year effects relative to 2018 (points), establishment FE, with and without "
      "inspector effects (R)")
Rm = R[R.inspector.isin(MAIN)].copy()
Rm["yr"] = Rm.year.astype(int).astype(str)
Xy = pd.get_dummies(Rm.yr, prefix="y", dtype=float).drop(columns=["y_2018"])
g_ = Rm.establishment_id
Yw = Rm.score_std - Rm.groupby(g_).score_std.transform("mean")
res = {}
for lab, X in (("no inspector effects", Xy),
               ("with inspector effects", pd.concat([Xy, pd.get_dummies(Rm.inspector, dtype=float)
                                                    .drop(columns=[SALLEY])], axis=1))):
    Xw = X - X.groupby(g_).transform("mean")
    m = sm.OLS(Yw, Xw).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(g_)[0]})
    ci = m.conf_int()
    res[lab] = {c[2:]: f"{m.params[c]:+.2f} ({ci.loc[c, 0]:+.2f} to {ci.loc[c, 1]:+.2f})" for c in Xy.columns}
print(pd.DataFrame(res).to_string())

print("\nNext-visit test, Goldston's first year (Frisco-style)")
new19, same19 = next_visit_test(P, "2019-01-10", "2019-12-31", [GOLDSTON], "Goldston's first visit to an establishment")
print("    same-incumbent repeats by inspector: " + "; ".join(
    f"{k} {v.delta.mean():+.1f} (n={len(v)})" for k, v in same19.groupby("inspector")))

# ============================================================================================ [5]
head("[5] 2025-2026: Emmanuel Adagbon (from 2025-09-04), Billy Smith, Nala Guidry")
for x in NEW25:
    d = A[A.inspector == x]
    print(f"  {x}: first routine inspection {d.inspection_date.min():%Y-%m-%d}, {len(d)} routine "
          f"({(R.inspector == x).sum()} restaurant)")
for x in [GOLDSTON, LERMA, "Jadyn Starnes"]:
    print(f"  {x}: last routine inspection {A[A.inspector == x].inspection_date.max():%Y-%m-%d}")
print("\nQuarterly restaurant mean by inspector, 2024Q3-2026Q3 (inspections in brackets)")
q = R[R.inspection_date >= "2024-07-01"]
qm = q.pivot_table(index="quarter", columns="inspector", values="score_std", aggfunc="mean")
qn = q.pivot_table(index="quarter", columns="inspector", values="score_std", aggfunc="size")
qc = (qm.round(1).astype(str) + " [" + qn.fillna(0).astype(int).astype(str) + "]").where(qn.notna(), "")
qc["ALL"] = q.groupby("quarter").score_std.mean().round(2).astype(str)
qc["ALL excl. newcomers"] = q[~q.inspector.isin(NEW25)].groupby("quarter").score_std.mean().round(2).astype(str)
print(qc.drop(columns=[c for c in qc.columns if c in ("Richard Milam",)]).to_string())
b = R[(R.inspection_date >= "2024-09-04") & (R.inspection_date < "2025-09-04")]
a_ = R[R.inspection_date >= "2025-09-04"]
print(f"\nRestaurant mean, 12 months before Adagbon's start: {b.score_std.mean():.2f} ({len(b)}); since: "
      f"{a_.score_std.mean():.2f} ({len(a_)}); since, excluding the three newcomers: "
      f"{a_[~a_.inspector.isin(NEW25)].score_std.mean():.2f} ({(~a_.inspector.isin(NEW25)).sum()}); newcomers: "
      f"{a_[a_.inspector.isin(NEW25)].score_std.mean():.2f} ({a_.inspector.isin(NEW25).sum()}, "
      f"{a_[a_.inspector.isin(NEW25)].ge15.sum()} at 15+)")
print(f"  Share of restaurant inspections since 2025-09-04 done by the newcomers: {a_.inspector.isin(NEW25).mean():.1%};"
      f" by Salley: {(a_.inspector == SALLEY).mean():.1%}")
print(f"  15+ scores since 2025-09-04: {a_.ge15.sum()}, of which Adagbon {a_[a_.inspector == ADAGBON].ge15.sum()}"
      f" ({(a_.inspector == ADAGBON).mean():.1%} of inspections)")

print("\nNext-visit tests (Frisco-style), window 2025-09-04 to 2026-09-01")
new25, same25 = next_visit_test(P, "2025-09-04", "2026-09-01", NEW25, "any of the three newcomers")
print("    same-incumbent repeats by inspector: " + "; ".join(
    f"{k} {v.delta.mean():+.1f} (n={len(v)})" for k, v in same25.groupby("inspector")))
for x in NEW25:
    next_visit_test(P, "2025-09-04", "2026-09-01", [x], x, cohort=NEW25)

# ============================================================================================ [6]
head("[6] Shane Salley: does the low average hold at the same establishments?")
s = R[R.inspector == SALLEY]
o = R[R.inspector != SALLEY]
print(f"Salley: {len(A[A.inspector == SALLEY]):,} of {len(A):,} routine inspections "
      f"({(A.inspector == SALLEY).mean():.1%}); restaurants {len(s):,} of {len(R):,} ({len(s) / len(R):.1%})")
print(f"Raw (R): Salley mean {s.score_std.mean():.2f}, zero {s.zero.mean():.1%}, 15+ {s.ge15.mean():.1%}; "
      f"everyone else mean {o.score_std.mean():.2f}, zero {o.zero.mean():.1%}, 15+ {o.ge15.mean():.1%}")
print(f"Raw (all routine): Salley mean {A[A.inspector == SALLEY].score_std.mean():.2f}, zero "
      f"{A[A.inspector == SALLEY].zero.mean():.1%}; others mean {A[A.inspector != SALLEY].score_std.mean():.2f}, zero "
      f"{A[A.inspector != SALLEY].zero.mean():.1%}")

# establishment-year cells visited by Salley and by someone else in the same calendar year
R["is_s"] = (R.inspector == SALLEY).astype(int)
cell = R.groupby(["establishment_id", "year", "is_s"]).agg(m=("score_std", "mean"), z=("zero", "mean"),
                                                            n=("score_std", "size")).unstack("is_s").dropna()
diff = cell[("m", 1)] - cell[("m", 0)]
zd = cell[("z", 1)] - cell[("z", 0)]
m_, lo_, hi_, _ = boot_mean(diff)
mz, loz, hiz, _ = boot_mean(zd)
print(f"\nEstablishment-years inspected by Salley and by someone else in the same year: {len(cell)} "
      f"({cell.index.get_level_values(0).nunique()} establishments)")
print(f"  Salley mean {cell[('m', 1)].mean():.2f} vs others {cell[('m', 0)].mean():.2f}: difference "
      f"{fmt_ci(m_, lo_, hi_)}; Salley lower in {(diff < 0).sum()}, higher in {(diff > 0).sum()}, equal in "
      f"{(diff == 0).sum()}")
print(f"  share scoring 0: Salley {cell[('z', 1)].mean():.1%} vs others {cell[('z', 0)].mean():.1%}: difference "
      f"{fmt_ci(mz * 100, loz * 100, hiz * 100, 1)} pp")

shared_s = R[R.inspector == SALLEY].establishment_id.isin(R[R.inspector != SALLEY].establishment_id)
print(f"\nSalley's restaurant inspections at establishments no other inspector visited (2017-2026): "
      f"{(~shared_s).sum():,} of {len(shared_s):,} ({(~shared_s).mean():.1%}); these add nothing to the "
      "same-establishment estimates")
print(f"  Salley's mean at those establishments {R[R.inspector == SALLEY][~shared_s].score_std.mean():.2f}, zero "
      f"{R[R.inspector == SALLEY][~shared_s].zero.mean():.1%}; at shared establishments "
      f"{R[R.inspector == SALLEY][shared_s].score_std.mean():.2f}, zero {R[R.inspector == SALLEY][shared_s].zero.mean():.1%}")

PERIODS = (("Oct 2017-2018", "2017-01-01", "2018-12-31"), ("2019-2023", "2019-01-01", "2023-12-31"),
           ("2024-Aug 2025", "2024-01-01", "2025-09-03"), ("Sep 2025-2026", "2025-09-04", "2026-12-31"),
           ("all years", "2017-01-01", "2026-12-31"))
for X_ in (SALLEY, GOLDSTON, LERMA):
    print(f"\nPooled {X_} vs all others, establishment + year effects, by period (R)")
    for lab, lo_d, hi_d in PERIODS:
        d = R[(R.inspection_date >= lo_d) & (R.inspection_date <= hi_d)].copy()
        if (d.inspector == X_).sum() < 30:
            continue
        d["_is"] = np.where(d.inspector == X_, X_, "others")
        t, _ = within_ols(d, "score_std", "_is", "others")
        tz, _ = within_ols(d, "zero", "_is", "others")
        r, rz = t.loc[X_], tz.loc[X_]
        print(f"  {lab:14s} {X_.split()[1]} {int((d.inspector == X_).sum()):5d} of {len(d):5d}: points {fmt_ci(r.coef, r.lo, r.hi)};"
              f" zero {fmt_ci(rz.coef * 100, rz.lo * 100, rz.hi * 100, 1)} pp; raw means {d[d.inspector == X_].score_std.mean():.2f}"
              f" vs {d[d.inspector != X_].score_std.mean():.2f}")

# ============================================================================================ [7]
head("[7] Listed (assigned) vs signed inspector")
sg = A[A.inspector_signed.notna()]
print(f"Routine inspections with a signed report: {len(sg):,}; signer differs from listed: "
      f"{(sg.inspector_signed != sg.inspector_listed).sum()} ({(sg.inspector_signed != sg.inspector_listed).mean():.1%})")
gl = sg[sg.inspector_listed == GOLDSTON]
print(f"Goldston listed on {len(gl)} signed routine reports; she signed {(gl.inspector_signed == GOLDSTON).sum()}; "
      f"others: {gl[gl.inspector_signed != GOLDSTON].inspector_signed.value_counts().to_dict()}")
print("By year, Goldston-listed signed reports signed by someone else:",
      gl.groupby("year").apply(lambda d: f"{(d.inspector_signed != GOLDSTON).sum()}/{len(d)}").to_dict())
g45 = gl[gl.year.isin([2024, 2025])]
print(f"2024-2025 (reports era): {(g45.inspector_signed != GOLDSTON).sum()} of {len(g45)} Goldston-listed signed reports "
      f"({(g45.inspector_signed != GOLDSTON).mean():.1%}) were signed by someone else; before 2024: "
      f"{(gl[gl.year < 2024].inspector_signed != GOLDSTON).sum()} of {(gl.year < 2024).sum()}")
glr = R[(R.inspector_listed == GOLDSTON) & R.inspector_signed.notna()]
print("Restaurant inspections listed as Goldston, by signer: " + "; ".join(
    f"{k} mean {v.score_std.mean():.2f}, zero {v.zero.mean():.0%} (n={len(v)})"
    for k, v in glr.groupby("inspector_signed") if len(v) >= 5))
sl = R[(R.inspector_listed == SALLEY) & R.inspector_signed.notna()]
print("Restaurant inspections listed as Salley, by signer: " + "; ".join(
    f"{k} mean {v.score_std.mean():.2f} (n={len(v)})" for k, v in sl.groupby("inspector_signed") if len(v) >= 5))

print("\nSigned restaurant inspections only (mostly 2024-2026): inspector effects vs Salley, signer vs listed name")
Rs = R[R.inspector_signed.notna() & R.inspector_signed.isin(MAIN) & R.inspector_listed.isin(MAIN)].copy()
big = set(Rs.inspector_signed.value_counts()[lambda v: v >= 30].index) & set(
    Rs.inspector_listed.value_counts()[lambda v: v >= 30].index)
Rs = Rs[Rs.inspector_signed.isin(big) & Rs.inspector_listed.isin(big)]
print(f"  {len(Rs)} signed restaurant inspections by inspectors with 30+ under both names; "
      f"signer differs from listed on {(Rs.inspector_signed != Rs.inspector_listed).sum()}")
ts, _ = within_ols(Rs, "score_std", "inspector_signed", SALLEY)
tl, _ = within_ols(Rs, "score_std", "inspector_listed", SALLEY)
print(pd.DataFrame({"signed coef": ts.coef, "signed CI": ts.lo.round(2).astype(str) + " to " + ts.hi.round(2).astype(str),
                    "n signed": ts.inspections, "listed coef": tl.coef, "listed CI": tl.lo.round(2).astype(str)
                    + " to " + tl.hi.round(2).astype(str), "n listed": tl.inspections}).round(2).to_string())
print("\nWhole period using the listed name for every inspection (vs the main 'signed where available' name):")
Rl = R[R.inspector_listed.isin(MAIN)].copy()
tl2, _ = within_ols(Rl, "score_std", "inspector_listed", SALLEY)
print(pd.DataFrame({"main": t_ols.coef, "listed only": tl2.coef, "listed lo": tl2.lo, "listed hi": tl2.hi}).round(2).to_string())

# ============================================================================================ [8]
head("[8] Corrected-on-site (COS) points by inspector, reports era (R inspections with a matched report)")
C = R[R.has_report & R.report_cos_points.notna() & (R.inspection_date >= "2024-01-01")].copy()
cov = R[R.inspection_date >= "2024-01-01"].groupby("inspector").apply(
    lambda d: f"{(d.has_report & d.report_cos_points.notna()).mean():.0%} of {len(d)}")
print("Share of each inspector's 2024-2026 restaurant inspections with a matched report:", cov.to_dict())
cg = C.groupby("inspector").agg(n=("score_std", "size"), any_cos=("report_cos_points", lambda s: (s > 0).mean()),
                                cos_points=("report_cos_points", "mean"), layer=("score_std", "mean"),
                                printed=("score_printed", "mean"),
                                zero_layer=("score_std", lambda s: (s == 0).mean()),
                                zero_printed=("score_printed", lambda s: (s == 0).mean()))
cg["cos_share_of_printed"] = C.groupby("inspector").report_cos_points.sum() / C.groupby("inspector").score_printed.sum()
print(cg.sort_values("n", ascending=False).round(3).to_string())
nc = C[C.report_cos_points > 0]
print(f"Inspections with any COS points: {len(nc)}; by inspector {nc.inspector.value_counts().to_dict()}")
cv = vio[(vio.source == "report (corrected on site)") & vio.inspection_number.isin(C.inspection_number)]
print(f"COS items (rows) on these inspections: {len(cv)}; by inspector {cv.inspector.value_counts().to_dict()}")
print("Most common COS items (Salley):", cv[cv.inspector == SALLEY].item_number.value_counts().head(6).to_dict())
Cm = C[C.inspector.isin(MAIN)].copy()
Cm["_is"] = np.where(Cm.inspector == SALLEY, SALLEY, "others")
for yv in ("score_std", "score_printed", "zero"):
    if yv == "zero":
        Cm["zp"] = (Cm.score_printed == 0).astype(int)
        for y2, lab in (("zero", "zero on layer score"), ("zp", "zero on printed score")):
            t, _ = within_ols(Cm, y2, "_is", "others")
            r = t.loc[SALLEY]
            print(f"  Salley vs others, {lab}: {fmt_ci(r.coef * 100, r.lo * 100, r.hi * 100, 1)} pp")
    else:
        t, _ = within_ols(Cm, yv, "_is", "others")
        r = t.loc[SALLEY]
        print(f"  Salley vs others ({len(Cm)} inspections), {yv}: {fmt_ci(r.coef, r.lo, r.hi)} points "
              "(establishment + year effects)")
ta_, _ = within_ols(Cm, "score_std", "_is", "others")
tb_, _ = within_ols(Cm, "score_printed", "_is", "others")
print(f"  Share of Salley's reports-era layer-score gap that disappears on the printed score: "
      f"{1 - tb_.loc[SALLEY].coef / ta_.loc[SALLEY].coef:.0%}")
t1, _ = within_ols(Cm, "score_std", "inspector", SALLEY)
t2, _ = within_ols(Cm, "score_printed", "inspector", SALLEY)
print("Inspector effects vs Salley in the reports era, layer score vs printed score:")
print(pd.DataFrame({"layer": t1.coef, "layer lo": t1.lo, "layer hi": t1.hi, "printed": t2.coef, "printed lo": t2.lo,
                    "printed hi": t2.hi, "n": t2.inspections}).round(2).to_string())

# ============================================================================================ [9]
head("[9] Enforcement at or over the closure threshold, by inspector (all routine inspections)")
cr = pd.read_csv(HERE / "closures_reviewed.csv", dtype=str).drop_duplicates("inspection_number")
A["classification"] = A.inspection_number.map(cr.set_index("inspection_number").classification)
A["reason"] = A.inspection_number.map(cr.set_index("inspection_number").reason)
A["over_printed"] = (A.score_printed >= A.closure_threshold) & ~A.at_or_over_closure_threshold
print(f"Routine inspections at or over the threshold on the layer score: {A.at_or_over_closure_threshold.sum()}; "
      f"additionally over only on the printed score (COS included): {A.over_printed.sum()}")
H = A[A.at_or_over_closure_threshold | A.over_printed].copy()
H["outcome"] = np.where(H.closure_recorded, "closure recorded",
                        np.where(H.classification.notna(), "other closure language", "no closure language"))
H["period"] = np.where(H.inspection_date < ORD_2019, "before Ord. 2019-10-072", "Ord. 2019-10-072 or later")
tab = pd.crosstab([H.inspector], [H.period, H.outcome], margins=True)
print(tab.to_string())
rate = A.groupby("inspector").agg(routine=("score_std", "size"), over=("at_or_over_closure_threshold", "sum"),
                                  closures_any_score=("closure_recorded", "sum"))
rate["over_per_1000"] = rate.over / rate.routine * 1000
rr = R.groupby("inspector").agg(restaurant=("score_std", "size"), over_restaurant=("at_or_over_closure_threshold", "sum"))
rate = rate.join(rr)
rate["over_per_1000_restaurant"] = rate.over_restaurant / rate.restaurant * 1000
print(rate.round(1).to_string())
late = H[H.period == "Ord. 2019-10-072 or later"]
print(f"From 2019-10-15: {len(late)} inspections at or over the threshold; closure recorded at "
      f"{late.closure_recorded.sum()} ({late.closure_recorded.mean():.0%}); before: {H.closure_recorded[H.period != late.period.iloc[0]].sum()}"
      f" of {(H.period != late.period.iloc[0]).sum()}")
print("From 2019-10-15, at/over threshold with no closure recorded:")
print(late[~late.closure_recorded][["inspection_date", "inspector", "permitted_name", "score_std", "score_printed",
                                    "closure_threshold", "classification", "reason"]].to_string())
print("Closures recorded below the threshold (other reasons), by inspector:",
      A[A.closure_recorded & ~A.at_or_over_closure_threshold].inspector.value_counts().to_dict())
print("Their reasons:", A[A.closure_recorded & ~A.at_or_over_closure_threshold].reason.value_counts().head(8).to_dict())

# follow-up: the next inspection of any kind at the same establishment
AL = ins[ins.exclude_reason.isna()].sort_values(["establishment_id", "inspection_date", "time_in"])
nxt = AL.groupby("establishment_id")[["inspection_date", "score_std", "purpose"]].shift(-1)
AL = AL.assign(next_date=nxt.inspection_date, next_score=nxt.score_std, next_purpose=nxt.purpose)
prv = AL.groupby("establishment_id")[["inspection_date", "score_std"]].shift(1)
AL = AL.assign(prev_date=prv.inspection_date, prev_score=prv.score_std)
H = H.merge(AL[["inspection_number", "next_date", "next_score", "next_purpose", "prev_date", "prev_score"]],
            on="inspection_number", how="left")
H["days_to_next"] = (H.next_date - H.inspection_date).dt.days
fu = H.groupby("inspector").agg(over=("days_to_next", "size"), median_days_to_next=("days_to_next", "median"),
                                next_within_30d=("days_to_next", lambda s: int((s <= 30).sum())),
                                closure_recorded=("closure_recorded", "sum"))
print("\nFollow-up after an inspection at or over the threshold (next inspection of any purpose, same establishment):")
print(fu.to_string())
print("Context for the four from 2019-10-15 with no closure recorded (previous and next inspection):")
print(H[(H.period == "Ord. 2019-10-072 or later") & ~H.closure_recorded][
    ["inspection_date", "inspector", "score_std", "prev_date", "prev_score", "next_date", "next_score",
     "next_purpose"]].to_string())
