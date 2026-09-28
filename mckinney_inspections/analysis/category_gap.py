"""Does McKinney show the cuisine gap that Frisco's food-type labels showed?

Frisco (city food_type labels, Jun 2025 - Sep 2026): Indian-labelled restaurants averaged about
4x the demerits of American ones before controls and 2.8x after; "orders of magnitude" was not
supported. This script asks the same question of McKinney using OUR cuisine codes
(common.GROUP) on the restaurant universe R (routine inspections, restaurant categories,
market/event sites excluded), Oct 2017 - Sep 2026.

Sections
  [0] universe, where the codes come from, coder agreement for the focal groups
  [1] establishment-level means (bootstrap 95% CIs by establishment) and shares scoring 15+
  [2] raw ratios to American (sit-down) and to Fast Food
  [3] the high-score tail (10/15/20/25/30+)
  [4] Poisson / negative binomial models with inspector, month (or year), chain status and
      inspection-cycle controls; SEs clustered by establishment
  [5] within-inspector comparisons
  [6] independent restaurants only
  [7] sensitivity checks: low-confidence codes dropped, high-confidence only, signed inspector
      only, printed score 2024-2026 (includes items corrected on site), supermarket sushi
      counters added to Japanese, Frisco's time window
  [8] which items drive the gap
  [9] comparison with Frisco (recomputed from frisco_inspections/data with Frisco's own
      common.py, when that folder is present)

Scores are score_std (form weights; excludes items corrected on site, so a lower bound on
the printed score) unless stated. Cuisine codes describe establishments, not people.

Run from this folder: python category_gap.py   (pandas, numpy, scipy, statsmodels; under a minute on an idle machine)
"""
import importlib.util
import os
import time
import warnings
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):  # small models: one BLAS thread is faster
    os.environ.setdefault(_v, "1")

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

from common import D, ITEM_GROUP, R, ins, permits

warnings.filterwarnings("ignore")
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 60)
pd.set_option("display.max_rows", 200)
T0 = time.time()
SEED = 20260926
NB = 2000  # bootstrap draws
rng = np.random.default_rng(SEED)

SHORT = {"American (sit-down, bars, venues)": "American", "Fast Food": "FastFood",
         "Coffee/Cafe/Bakery/Dessert": "Coffee", "Deli/Sandwich/Salad": "Deli", "Pizza/Italian": "Pizza",
         "Mexican/Tex-Mex/Latin": "Mexican", "Mediterranean/Middle Eastern": "MedME",
         "Other cuisine": "OtherCuisine", "Other Asian": "OtherAsian", "Indian": "Indian",
         "Chinese": "Chinese", "Japanese": "Japanese"}
ORDER = ["Indian", "OtherAsian", "Japanese", "Chinese", "Mexican", "American", "MedME", "Pizza",
         "OtherCuisine", "FastFood", "Coffee", "Deli"]
FOCAL = ["Indian", "OtherAsian", "Japanese", "Chinese", "Mexican", "Pizza", "Coffee", "Deli", "FastFood"]
COMP = ["American", "Mexican", "Pizza", "Coffee", "FastFood"]  # Frisco's comparison groups
SMALL = 20  # fewer establishments than this: indicative only

ITEMS = pd.read_csv(D / "items.csv")
ICOLS = ITEMS.column.tolist()


def hdr(s):
    print("\n" + "=" * 100 + "\n" + s + "\n" + "=" * 100)


def ci(t, d=2):
    return " ".join([f"{t[0]:.{d}f}", "(" + "-".join("n/a" if not np.isfinite(x) else f"{x:.{d}f}" for x in t[1:]) + ")"])


def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.1f}%)" if b else f"{a}/{b}"


# ----------------------------------------------------------------------------- data prep
def prep(df, insp_col="inspector"):
    M = df.copy()
    M["g"] = M.group.map(SHORT)
    M["chainf"] = M.chain2.fillna("unknown")
    M["cyc"] = M.inspection_cycle.where(M.inspection_cycle.isin(["Biannual Inspections", "not current"]),
                                        "other current cycle")
    M["insp"] = M[insp_col]
    M["yr"] = M.inspection_date.dt.year.astype(int).astype(str)
    M["iy"] = M.insp + "_" + M.yr
    return M


def est_means(df, col="score_std"):
    """per-establishment mean score within one group (array)"""
    return df.groupby("establishment_id")[col].mean().values


def boot_mean(e):
    bs = e[rng.integers(0, len(e), (NB, len(e)))].mean(1)
    return e.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5)


def boot_ratio(ea, eb):
    """ratio (and difference) of establishment-level means, groups resampled independently"""
    ba = ea[rng.integers(0, len(ea), (NB, len(ea)))].mean(1)
    bb = eb[rng.integers(0, len(eb), (NB, len(eb)))].mean(1)
    r = ba / bb
    d = ba - bb
    return ((ea.mean() / eb.mean(), np.percentile(r, 2.5), np.percentile(r, 97.5)),
            (ea.mean() - eb.mean(), np.percentile(d, 2.5), np.percentile(d, 97.5)))


def est_sums(df, cols):
    g = df.groupby("establishment_id")
    return g[cols].sum().values, g.size().values


def boot_share(df, col):
    """inspection-level share with a CI from resampling establishments"""
    s, n = est_sums(df, [col])
    s = s[:, 0]
    idx = rng.integers(0, len(n), (NB, len(n)))
    bs = s[idx].sum(1) / n[idx].sum(1)
    return s.sum() / n.sum(), np.percentile(bs, 2.5), np.percentile(bs, 97.5)


def boot_share_ratio(da, db, col):
    sa, na = est_sums(da, [col])
    sb, nb = est_sums(db, [col])
    sa, sb = sa[:, 0], sb[:, 0]
    ia = rng.integers(0, len(na), (NB, len(na)))
    ib = rng.integers(0, len(nb), (NB, len(nb)))
    ra = sa[ia].sum(1) / na[ia].sum(1)
    rb = sb[ib].sum(1) / nb[ib].sum(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = ra / rb
    pa, pb = sa.sum() / na.sum(), sb.sum() / nb.sum()
    return ((pa / pb if pb else np.nan, np.nanpercentile(r, 2.5), np.nanpercentile(r, 97.5)),
            (pa - pb, np.percentile(ra - rb, 2.5), np.percentile(ra - rb, 97.5)))


# ----------------------------------------------------------------------------- models
CTRL = {"raw": "",
        "+inspector": " + C(insp)",
        "+inspector+month": " + C(insp) + C(month)",
        "+inspector+month+chain": " + C(insp) + C(month) + C(chainf)",
        "full (month)": " + C(insp) + C(month) + C(chainf) + C(cyc)",
        "full (year)": " + C(insp) + C(yr) + C(chainf) + C(cyc)",
        "inspector x year FE": " + C(iy) + C(chainf) + C(cyc)"}
GTERM = 'C(g, Treatment("American"))'


def fit(M, y="score_std", ctrl=CTRL["full (month)"], fam="poisson", gterm=GTERM):
    M = M[M[y].notna()]
    f = f"{y} ~ {gterm}{ctrl}"
    cl = {"groups": pd.factorize(M.establishment_id)[0]}
    if fam == "poisson":
        return smf.glm(f, M, family=sm.families.Poisson()).fit(cov_type="cluster", cov_kwds=cl)
    return smf.negativebinomial(f, M).fit(disp=0, maxiter=1000, method="newton", cov_type="cluster",
                                          cov_kwds=cl)


def contrast(res, g, ref="American", gterm=GTERM, base="American"):
    """exp(b_g - b_ref) with 95% CI from the cluster-robust covariance"""
    p, V = res.params, res.cov_params()
    k = lambda x: f"{gterm}[T.{x}]"  # noqa: E731
    if g == ref:
        return (1.0, 1.0, 1.0)
    if ref == base:
        if k(g) not in p:
            return (np.nan,) * 3
        b, v = p[k(g)], V.loc[k(g), k(g)]
    elif g == base:
        if k(ref) not in p:
            return (np.nan,) * 3
        b, v = -p[k(ref)], V.loc[k(ref), k(ref)]
    else:
        if k(g) not in p or k(ref) not in p:
            return (np.nan,) * 3
        b = p[k(g)] - p[k(ref)]
        v = V.loc[k(g), k(g)] + V.loc[k(ref), k(ref)] - 2 * V.loc[k(g), k(ref)]
    se = np.sqrt(v)
    return (np.exp(b), np.exp(b - 1.96 * se), np.exp(b + 1.96 * se))


def model_line(res, groups=("Indian", "OtherAsian", "Japanese", "Chinese"), refs=("American", "FastFood")):
    out = []
    for ref in refs:
        out.append(f"vs {ref}: " + " | ".join(f"{g} {ci(contrast(res, g, ref))}" for g in groups))
    return out


M0 = prep(R)

# ============================================================================= [0]
hdr("[0] UNIVERSE AND CODES")
print(f"R: {len(R)} routine inspections at {R.establishment_id.nunique()} establishments, "
      f"{R.inspection_date.min().date()} to {R.inspection_date.max().date()}")
wc = permits[permits.link_number.isin(R.link_number)].layer_work_class.fillna("(permit not in current layer)")
print("City layer carries no cuisine field; its WORK_CLASS for R permits:", wc.value_counts().to_dict())
cc = pd.read_csv(D / "cuisine_codes.csv", dtype=str)
cc = cc[cc.link_number.isin(R.link_number)].copy()
cc["group"] = cc.category.map(lambda c: SHORT.get({"Thai": "Other Asian", "Vietnamese": "Other Asian",
                                                     "Korean": "Other Asian", "Fusion": "Other Asian",
                                                     "Asian (unspecified)": "Other Asian"}.get(c, c)))
cc["a_cat"] = cc.coder_a.str.split(" | ", regex=False).str[0]
cc["b_cat"] = cc.coder_b.str.split(" | ", regex=False).str[0]
cc["both_agree_final"] = (cc.a_cat == cc.category) & (cc.b_cat == cc.category)
print("\nCoder agreement on category for R permits (both independent coders chose the final category):")
agr = cc.groupby("group").agg(permits=("link_number", "size"), both_agree=("both_agree_final", "sum"),
                              low_conf=("confidence", lambda s: (s == "low").sum()))
agr["agree_share"] = (agr.both_agree / agr.permits).round(3)
print(agr.loc[[g for g in ORDER if g in agr.index]].to_string())


def kappa(x, y):
    cats = sorted(set(x) | set(y))
    t = pd.crosstab(pd.Categorical(x, cats), pd.Categorical(y, cats), dropna=False).values
    n = t.sum()
    po, pe = np.trace(t) / n, (t.sum(0) * t.sum(1)).sum() / n ** 2
    return (po - pe) / (1 - pe)


cc_all = pd.read_csv(D / "cuisine_codes.csv", dtype=str)
print(f"Cohen's kappa, coder A vs coder B category: all {len(cc_all)} coded permits "
      f"{kappa(cc_all.coder_a.str.split(' | ', regex=False).str[0], cc_all.coder_b.str.split(' | ', regex=False).str[0]):.3f}; "
      f"R permits {kappa(cc.a_cat, cc.b_cat):.3f}")
ff_chain = M0[M0.g == "FastFood"].groupby("establishment_id").chain2.first()
am_ind = M0[(M0.g == "American") & (M0.chain2 == "independent")].drop_duplicates("establishment_id")
print(f"Fast Food establishments coded chain: {int((ff_chain == 'chain').sum())}/{len(ff_chain)}; independent 'American' "
      f"establishments whose detail mentions burgers: {int(am_ind.cuisine_detail.str.contains('burger', case=False, na=False).sum())}")
print("\nOther Asian is pooled from:", R[R.group == "Other Asian"].groupby("category").agg(
    inspections=("score_std", "size"), establishments=("establishment_id", "nunique")).to_dict("index"))

# ============================================================================= [1]
hdr("[1] ESTABLISHMENT-LEVEL MEANS (mean of each establishment's mean score_std; 95% CI: bootstrap by establishment)")
rows = []
for g in ORDER:
    d = M0[M0.g == g]
    m = boot_mean(est_means(d))
    p15 = boot_share(d, "ge15")
    emax = d.groupby("establishment_id").score_std.max()
    ech = d.groupby("establishment_id").chain2.first()
    rows.append(dict(group=g, est=d.establishment_id.nunique(), insp=len(d), est_mean=ci(m, 1),
                     insp_mean=round(d.score_std.mean(), 2), median=d.score_std.median(),
                     share15=ci(tuple(100 * x for x in p15), 1), n15=int(d.ge15.sum()),
                     est_any15=f"{(emax >= 15).sum()}/{len(emax)}", zero=f"{100 * d.zero.mean():.1f}",
                     chain_est=f"{100 * (ech == 'chain').mean():.0f}%",
                     flag="indicative (<20 est)" if d.establishment_id.nunique() < SMALL else ""))
T1 = pd.DataFrame(rows)
print(T1.to_string(index=False))
print("(share15 = % of inspections scoring 15+, CI by establishment bootstrap; zero = % of inspections scoring 0;"
      " chain_est = % of establishments coded national/regional or local multi-unit chain)")

# ============================================================================= [2]
hdr("[2] RAW RATIOS")
E = {g: est_means(M0[M0.g == g]) for g in ORDER}
print("Establishment-level ratio and difference of means (bootstrap 95% CI, groups resampled independently):")
for g in FOCAL:
    for ref in ("American", "FastFood"):
        if g == ref:
            continue
        r, dif = boot_ratio(E[g], E[ref])
        print(f"  {g:12s} vs {ref:8s}: ratio {ci(r)}   difference {ci(dif, 1)} points")
print("\nPer-inspection raw Poisson ratios (no controls; SEs clustered by establishment):")
r_raw = fit(M0, ctrl=CTRL["raw"])
for line in model_line(r_raw, groups=FOCAL[:-1] + ["FastFood"]):
    print("  " + line)
em = {g: E[g].mean() for g in ORDER}
hi, lo = max(em, key=em.get), min(em, key=em.get)
print(f"\nLargest establishment-level ratio between any two groups: {hi} {em[hi]:.2f} / {lo} {em[lo]:.2f} = "
      f"{em[hi] / em[lo]:.2f}")
big = {g: E[g].mean() for g in ORDER if len(E[g]) >= SMALL}
hi2, lo2 = max(big, key=big.get), min(big, key=big.get)
print(f"Largest among groups with 20+ establishments: {hi2} / {lo2} = {big[hi2] / big[lo2]:.2f}")
ea = M0.groupby(["g", "establishment_id"]).score_std.mean()
for g in ["Indian", "American", "FastFood"]:
    x = ea[g]
    print(f"  {g}: establishments averaging 10+: {pct(int((x >= 10).sum()), len(x))}; averaging under 5: "
          f"{pct(int((x < 5).sum()), len(x))}")

# ============================================================================= [3]
hdr("[3] HIGH-SCORE TAIL: share of inspections at or above a cutoff (ratio CI: establishment bootstrap)")
for t in (10, 15, 20, 25, 30):
    M0[f"ge{t}"] = (M0.score_std >= t).astype(int)
    a, b, c = M0[M0.g == "Indian"], M0[M0.g == "American"], M0[M0.g == "FastFood"]
    ra, da = boot_share_ratio(a, b, f"ge{t}")
    rf, _ = boot_share_ratio(a, c, f"ge{t}")
    print(f"  {t}+: Indian {pct(int(a[f'ge{t}'].sum()), len(a))}  American {pct(int(b[f'ge{t}'].sum()), len(b))}  "
          f"FastFood {pct(int(c[f'ge{t}'].sum()), len(c))}  | Indian/American {ci(ra)}  diff {100 * da[0]:.1f} pts "
          f"({100 * da[1]:.1f} to {100 * da[2]:.1f})  | Indian/FastFood {ci(rf)}")
r15 = fit(M0, y="ge15")
print("Adjusted risk ratio for scoring 15+ (Poisson on the 0/1 outcome, full controls):")
for line in model_line(r15):
    print("  " + line)
print("Closure-threshold scores (31+ before 2023-11-07, 30+ after) by group:",
      M0.groupby("g").at_or_over_closure_threshold.sum().astype(int).to_dict())

# ============================================================================= [4]
hdr("[4] ADJUSTED MODELS (Poisson QMLE unless noted; score_std; SEs clustered by establishment)")
print(f"N = {len(M0)} inspections, {M0.establishment_id.nunique()} establishments, {M0.insp.nunique()} inspectors, "
      f"{M0.month.nunique()} months; chain levels {M0.chainf.value_counts().to_dict()}; "
      f"cycle levels {M0.cyc.value_counts().to_dict()}")
print("\nIndian vs American and vs Fast Food as controls are added:")
FITS = {}
for nm in ["raw", "+inspector", "+inspector+month", "+inspector+month+chain", "full (month)", "full (year)"]:
    FITS[nm] = fit(M0, ctrl=CTRL[nm])
    res = FITS[nm]
    print(f"  {nm:26s} Indian/American {ci(contrast(res, 'Indian'))}   Indian/FastFood "
          f"{ci(contrast(res, 'Indian', 'FastFood'))}   FastFood/American {ci(contrast(res, 'FastFood'))}")
FITS["NB full (month)"] = fit(M0, fam="nb")
res = FITS["NB full (month)"]
print(f"  {'NB full (month)':26s} Indian/American {ci(contrast(res, 'Indian'))}   Indian/FastFood "
      f"{ci(contrast(res, 'Indian', 'FastFood'))}   FastFood/American {ci(contrast(res, 'FastFood'))}"
      f"   (alpha {res.params['alpha']:.3f})")
chain_k = [k for k in FITS["full (month)"].params.index if k.startswith("C(chainf)")]
print("  chain terms (full model, vs chain):",
      {k: ci((np.exp(FITS['full (month)'].params[k]),) + tuple(np.exp(FITS['full (month)'].conf_int().loc[k])))
       for k in chain_k})
print("\nEvery group, full model (inspector, month, chain, cycle): ratio to American | ratio to Fast Food")
for nm in ["full (month)", "full (year)", "NB full (month)"]:
    res = FITS[nm]
    print(f"  -- {nm}")
    for g in ORDER:
        print(f"     {g:12s} {ci(contrast(res, g, 'American'))}   |   {ci(contrast(res, g, 'FastFood'))}")
# pooled high group vs Frisco comparison set
Mp = M0[M0.g.isin(["Indian", "OtherAsian", "Japanese"] + COMP)].copy()
Mp["hi"] = Mp.g.isin(["Indian", "OtherAsian", "Japanese"]).astype(int)
rp = fit(Mp, ctrl=CTRL["full (month)"], gterm="hi")
print("\nPooled Indian + Other Asian + Japanese vs Frisco's comparison set (American, Mexican, pizza, coffee, fast food),"
      f" full controls: {ci((np.exp(rp.params['hi']),) + tuple(np.exp(rp.conf_int().loc['hi'])))}")

# ============================================================================= [5]
hdr("[5] WITHIN-INSPECTOR COMPARISONS ('inspector' = report signer where one exists, else the listed inspector)")
rows = []
for i, d in M0.groupby("insp"):
    a, b, c = d[d.g == "Indian"], d[d.g == "American"], d[d.g == "FastFood"]
    oa = d[d.g.isin(["OtherAsian", "Japanese"])]
    rows.append(dict(inspector=i, years=f"{d.yr.min()}-{d.yr.max()}", n_all=len(d), mean_all=round(d.score_std.mean(), 1),
                     n_ind=len(a), est_ind=a.establishment_id.nunique(), mean_ind=round(a.score_std.mean(), 1),
                     n_am=len(b), mean_am=round(b.score_std.mean(), 1), n_ff=len(c), mean_ff=round(c.score_std.mean(), 1),
                     n_jpoa=len(oa), mean_jpoa=round(oa.score_std.mean(), 1),
                     ind_over_am=round(a.score_std.mean() / b.score_std.mean(), 2) if len(a) and len(b) else np.nan))
W = pd.DataFrame(rows).sort_values("n_all", ascending=False)
print(W.to_string(index=False))
for g, col in [("Indian", "mean_ind"), ("OtherAsian+Japanese", "mean_jpoa")]:
    ncol = "n_ind" if g == "Indian" else "n_jpoa"
    w = W[W[ncol] >= 5]
    print(f"Inspectors with 5+ {g} inspections: {len(w)}; {g} mean above their American mean in "
          f"{int((w[col] > w.mean_am).sum())}, above their Fast Food mean in {int((w[col] > w.mean_ff).sum())}")
r_iy = fit(M0, ctrl=CTRL["inspector x year FE"])
print("Poisson with inspector-by-year fixed effects (+ chain, cycle):")
for line in model_line(r_iy):
    print("  " + line)
print("Indian inspections by inspector:", M0[M0.g == "Indian"].insp.value_counts().to_dict())
print("15+ shares by inspector (inspectors with 5+ Indian inspections): Indian vs American")
for i, d in M0.groupby("insp"):
    a, b = d[d.g == "Indian"], d[d.g == "American"]
    if len(a) >= 5:
        print(f"  {i:18s} Indian {pct(int(a.ge15.sum()), len(a))}  American {pct(int(b.ge15.sum()), len(b))}")
for drop in (["Victoria Goldston"], ["Emmanuel Adagbon"], ["Victoria Goldston", "Emmanuel Adagbon"], ["Shane Salley"]):
    d = M0[~M0.insp.isin(drop)]
    a, b = d[d.g == "Indian"], d[d.g == "American"]
    rr, _ = boot_share_ratio(a, b, "ge15")
    print(f"  without {' and '.join(drop)}: 15+ Indian {pct(int(a.ge15.sum()), len(a))}, American "
          f"{pct(int(b.ge15.sum()), len(b))}, ratio {ci(rr)}; adjusted mean ratio Indian/American {ci(contrast(fit(d), 'Indian'))}")
print("Share of each group's inspections done by each inspector (%):")
print((pd.crosstab(M0.g, M0.insp, normalize="index") * 100).round(0).loc[ORDER].to_string())

# ============================================================================= [6]
hdr("[6] INDEPENDENT RESTAURANTS ONLY (chain2 == independent; there are no independent fast-food establishments)")
Mi = M0[M0.chain2 == "independent"]
print(f"{len(Mi)} inspections at {Mi.establishment_id.nunique()} independent establishments; fast food: "
      f"{int((Mi.g == 'FastFood').sum())} inspections")
for g in ["Indian", "OtherAsian", "Japanese", "Chinese", "Mexican", "American", "Pizza", "Coffee", "Deli"]:
    d = Mi[Mi.g == g]
    print(f"  {g:12s} est {d.establishment_id.nunique():3d} insp {len(d):4d} est-mean {ci(boot_mean(est_means(d)), 1)}"
          f"  15+ {100 * d.ge15.mean():.1f}%")
for g in ["Indian", "OtherAsian", "Japanese", "Chinese"]:
    r, dif = boot_ratio(est_means(Mi[Mi.g == g]), est_means(Mi[Mi.g == "American"]))
    print(f"  {g} / independent American, establishment level: {ci(r)}; difference {ci(dif, 1)}")
ri = fit(Mi, ctrl=" + C(insp) + C(month) + C(cyc)")
print("  Poisson, independents, inspector + month + cycle:",
      " | ".join(f"{g} {ci(contrast(ri, g))}" for g in ["Indian", "OtherAsian", "Japanese", "Chinese", "Mexican"]))
Mc = M0[M0.chain2 == "chain"]
print("Chain establishments in the focal groups:",
      {g: f"{Mc[Mc.g == g].establishment_id.nunique()} est, {len(Mc[Mc.g == g])} insp, mean {Mc[Mc.g == g].score_std.mean():.1f}"
       for g in ["Indian", "OtherAsian", "Japanese", "Chinese", "American", "FastFood"]})

# ============================================================================= [7]
hdr("[7] SENSITIVITY CHECKS (full Poisson model unless noted)")
SENS = {}


def sens(name, M, y="score_std", ctrl=CTRL["full (month)"], note=""):
    res = fit(M, y=y, ctrl=ctrl)
    Mv = M[M[y].notna()]
    ei = est_means(Mv[Mv.g == "Indian"], y)
    eam = est_means(Mv[Mv.g == "American"], y)
    eff = est_means(Mv[Mv.g == "FastFood"], y)
    rr, _ = boot_ratio(ei, eam)
    rf, _ = boot_ratio(ei, eff)
    SENS[name] = res
    print(f"\n-- {name}: {len(Mv)} inspections, {Mv.establishment_id.nunique()} establishments "
          f"(Indian {int((Mv.g == 'Indian').sum())} insp / {len(ei)} est; American {len(eam)} est; Fast Food {len(eff)} est) {note}")
    print(f"   est-level means: Indian {ei.mean():.2f}, American {eam.mean():.2f}, FastFood {eff.mean():.2f}; "
          f"raw Indian/American {ci(rr)}, Indian/FastFood {ci(rf)}")
    for line in model_line(res):
        print("   adjusted " + line)
    return res


sens("baseline (all R)", M0)
sens("drop low-confidence codes", M0[M0.category_confidence != "low"])
sens("high-confidence codes only", M0[M0.category_confidence == "high"])
Ms = prep(R[R.inspector_signed.notna()], insp_col="inspector_signed")
sens("inspector_signed only (inspector = signer)", Ms,
     note=f"years {Ms.yr.min()}-{Ms.yr.max()}")
# printed score, 2024-2026
M24 = M0[M0.inspection_date.dt.year >= 2024]
print("\n2024-2026 coverage of printed reports (score_printed present) by group:")
cov = M24.groupby("g").score_printed.agg(lambda s: f"{s.notna().sum()}/{len(s)} ({100 * s.notna().mean():.0f}%)")
print("  " + str(cov.loc[ORDER].to_dict()))
P24 = M24[M24.score_printed.notna()]
sens("2024-2026, all inspections, score_std", M24)
sens("2024-2026 with report, score_std", P24)
sens("2024-2026 with report, score_printed (includes corrected-on-site items)", P24, y="score_printed")
print("   mean corrected-on-site points per inspection (2024-2026 with report):",
      P24.groupby("g").report_cos_points.mean().round(2).loc[ORDER].to_dict())
# sushi counters
SU = ins[ins.routine & (ins.category == "Grocery/Market") & ins.cuisine_detail.fillna("").str.startswith("sushi counter")
         & ~ins.market_or_event_site.fillna(False).astype(bool)].copy()
SU["group"] = "Japanese"
print(f"\nSupermarket sushi counters: {SU.link_number.nunique()} permits, {SU.establishment_id.nunique()} establishments, "
      f"{len(SU)} routine inspections; est-level mean {ci(boot_mean(est_means(SU)), 1)}; 15+ {pct(int(SU.ge15.sum()), len(SU))}; "
      f"chain {SU.chain2.value_counts().to_dict()}")
MJ = prep(pd.concat([R, SU]))
jp = MJ[MJ.g == "Japanese"]
print(f"Japanese with sushi counters: est-level mean {ci(boot_mean(est_means(jp)), 1)} "
      f"({jp.establishment_id.nunique()} est, {len(jp)} insp); without: {ci(boot_mean(E['Japanese']), 1)}")
rj, _ = boot_ratio(est_means(jp), E["American"])
print(f"Japanese/American establishment level with counters: {ci(rj)}; without: {ci(boot_ratio(E['Japanese'], E['American'])[0])}")
sens("sushi counters added to Japanese", MJ)
Mf = M0[M0.inspection_date >= "2025-06-23"]
sens("Frisco's window (2025-06-23 onward), score_std", Mf)
sens("Frisco's window, score_printed where a report exists", Mf, y="score_printed")
# establishment age: Indian restaurants are newer, so more of their inspections are early ones
M0["seq"] = M0.sort_values(["inspection_date", "inspection_number"]).groupby("establishment_id").cumcount() + 1
M0["seqb"] = pd.cut(M0.seq, [0, 1, 2, 4, 8, 1000], labels=["1", "2", "3-4", "5-8", "9+"]).astype(str)
first = M0.groupby("establishment_id").inspection_date.transform("min")
M0["pre2018"] = (first < "2018-01-01").map({True: "first seen 2017", False: "first seen 2018+"})
print("\nShare of inspections in 2024-2026:", M0.assign(r=M0.yr.astype(int) >= 2024).groupby("g").r.mean().round(2).loc[ORDER].to_dict())
print("Inspections per establishment:", M0.groupby("g").apply(lambda d: len(d) / d.establishment_id.nunique()).round(1).loc[ORDER].to_dict())
print("Share of inspections that are an establishment's 1st or 2nd in the data:",
      M0.assign(r=M0.seq <= 2).groupby("g").r.mean().round(2).loc[ORDER].to_dict())
sens("+ inspection-sequence bucket and first-seen-2017 flag", M0, ctrl=CTRL["full (month)"] + " + C(seqb) + C(pre2018)")
for lo_, hi_ in [(2017, 2020), (2021, 2023), (2024, 2026)]:
    sens(f"period {lo_}-{hi_}", M0[M0.yr.astype(int).between(lo_, hi_)])
# leave one Indian establishment out
loo = []
for e in M0[M0.g == "Indian"].establishment_id.unique():
    loo.append(contrast(fit(M0[M0.establishment_id != e]), "Indian")[0])
print(f"\nLeave-one-Indian-establishment-out, adjusted Indian/American (full Poisson): min {min(loo):.2f}, max {max(loo):.2f} "
      f"({len(loo)} fits)")
print("\nSummary of adjusted Indian ratios across specifications:")
for k, res in SENS.items():
    print(f"  {k:75s} vs American {ci(contrast(res, 'Indian'))}  vs FastFood {ci(contrast(res, 'Indian', 'FastFood'))}")

# ============================================================================= [8]
hdr("[8] WHICH ITEMS DRIVE THE GAP (score_std points per inspection; ratio CIs: establishment bootstrap)")
X = (M0[ICOLS] > 0).astype(int)
X.columns = ITEMS.item_number.tolist()
PTS = X * ITEMS.form_points.values
IG = PTS.T.groupby(pd.Series(ITEM_GROUP)).sum().T
IG.index = M0.index
IGC = list(IG.columns)
M8 = pd.concat([M0[["establishment_id", "g", "score_std", "priority_items"]], IG], axis=1)
assert (IG.sum(1) == M0.score_std).all()
print("Mean points per inspection by item group:")
print(M8.groupby("g")[IGC + ["score_std"]].mean().round(2).loc[ORDER].to_string())


def decompose(a, b, label):
    sa, na = est_sums(a, IGC)
    sb, nb = est_sums(b, IGC)
    ma, mb = sa.sum(0) / na.sum(), sb.sum(0) / nb.sum()
    gap = ma.sum() - mb.sum()
    ia = rng.integers(0, len(na), (NB, len(na)))
    ib = rng.integers(0, len(nb), (NB, len(nb)))
    ba = sa[ia].sum(1) / na[ia].sum(1)[:, None]
    bb = sb[ib].sum(1) / nb[ib].sum(1)[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        br = ba / bb
    print(f"\n{label}: {ma.sum():.2f} vs {mb.sum():.2f} points per inspection, gap {gap:.2f} "
          f"({len(a)} vs {len(b)} inspections)")
    order = np.argsort(-(ma - mb))
    for j in order:
        print(f"  {IGC[j]:42s} {ma[j]:.2f} vs {mb[j]:.2f}  share of gap {100 * (ma[j] - mb[j]) / gap:5.1f}%  "
              f"ratio {ma[j] / mb[j]:.1f}x ({np.nanpercentile(br[:, j], 2.5):.1f}-{np.nanpercentile(br[:, j], 97.5):.1f})")
    return gap


A8, B8, C8 = M8[M8.g == "Indian"], M8[M8.g == "American"], M8[M8.g == "FastFood"]
decompose(A8, B8, "Indian vs American")
decompose(A8, C8, "Indian vs Fast Food")
decompose(M8[M8.g.isin(["OtherAsian", "Japanese"])], B8, "Other Asian + Japanese vs American")
print("\nPriority items (1-20) out per inspection: Indian {:.2f}, American {:.2f}, Fast Food {:.2f}; "
      "share with 1+ priority item: Indian {:.1f}%, American {:.1f}%, Fast Food {:.1f}%".format(
          A8.priority_items.mean(), B8.priority_items.mean(), C8.priority_items.mean(),
          100 * (A8.priority_items > 0).mean(), 100 * (B8.priority_items > 0).mean(), 100 * (C8.priority_items > 0).mean()))
print("\nItems by contribution to the Indian - American gap (share of inspections citing the item):")
XI = X.loc[M0.g == "Indian"].mean()
XA = X.loc[M0.g == "American"].mean()
XF = X.loc[M0.g == "FastFood"].mean()
fp = ITEMS.set_index("item_number").form_points
contrib = (XI - XA) * fp
gapIA = contrib.sum()
tt = ITEMS.set_index("item_number").title.str[:55]
for n in contrib.sort_values(ascending=False).index[:14]:
    print(f"  item {n:2d} {tt[n]:55s} Indian {100 * XI[n]:5.1f}%  American {100 * XA[n]:5.1f}%  FastFood {100 * XF[n]:5.1f}%"
          f"  contributes {contrib[n]:.2f} pts ({100 * contrib[n] / gapIA:.0f}% of gap)")
k = int((contrib.sort_values(ascending=False).cumsum() / gapIA < 0.5).sum()) + 1
print(f"  -> the top {k} items account for half of the Indian-American gap; items with a positive contribution: "
      f"{int((contrib > 0).sum())} of 47")
# Frisco-style grouping of items for a like-for-like comparison


def figroup(i):
    if 1 <= i <= 6:
        return "temp_1_6"
    if i in (7, 8, 9, 10, 11, 15, 18):
        return "contam_clean_7_11_15_18"
    if 12 <= i <= 14:
        return "hygiene_12_14"
    if i in (21, 22):
        return "cfm_foodhandler_21_22"
    if i in (28, 29):
        return "datemark_thermo_28_29"
    if i == 31:
        return "handsink_31"
    if i == 34:
        return "pests_34"
    if i <= 20:
        return "other_priority"
    if i <= 33:
        return "other_pf"
    return "other_core"


FG = PTS.T.groupby(PTS.columns.map(figroup)).sum().T
FG.index = M0.index
ict = M0.category.isin(["Indian", "Chinese", "Thai"])
cmp_ = M0.g.isin(COMP)
fa, fb = FG[ict].mean(), FG[cmp_].mean()
fgap = fa.sum() - fb.sum()
print(f"\nFrisco-style decomposition, Indian+Chinese+Thai ({int(ict.sum())} insp) vs American/Mexican/pizza/coffee/fast food "
      f"({int(cmp_.sum())} insp): {fa.sum():.2f} vs {fb.sum():.2f}, gap {fgap:.2f}")
for c in (fa - fb).sort_values(ascending=False).index:
    print(f"  {c:26s} {fa[c]:.2f} vs {fb[c]:.2f}  share {100 * (fa[c] - fb[c]) / fgap:5.1f}%  ratio {fa[c] / fb[c]:.1f}x")
for n in (9, 2, 3, 28, 10, 37):
    print(f"  item {n}: ICT {100 * X.loc[ict, n].mean():.1f}% vs COMP {100 * X.loc[cmp_, n].mean():.1f}% of inspections")

# ============================================================================= [9]
hdr("[9] COMPARISON WITH FRISCO")
FR = Path(__file__).resolve().parents[2] / "frisco_inspections" / "analysis" / "common.py"
if FR.exists():
    spec = importlib.util.spec_from_file_location("frisco_common", FR)
    fc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fc)
    U = fc.U.copy()
    U["g"] = U.group.map({**SHORT, "Thai": "OtherAsian", "Vietnamese": "OtherAsian", "Korean": "OtherAsian",
                          "Fusion": "OtherAsian", "Asian (unspecified)": "OtherAsian", "Other": "OtherCuisine",
                          "Hotel kitchens (Continental)": "Hotel"})
    print(f"Frisco universe (recomputed with Frisco's common.py): {len(U)} inspections, {U.permit_id.nunique()} permits, "
          f"{U.inspection_date.min().date()} to {U.inspection_date.max().date()}")
    FE = {g: U[U.g == g].groupby("permit_id").score.mean().values for g in U.g.dropna().unique()}
    FE["Indian"] = U[U.group == "Indian"].groupby("permit_id").score.mean().values
    print("Frisco establishment-level means (bootstrap CI; our 'OtherAsian' = Frisco's Thai, Vietnamese, Korean, Fusion, "
          "Asian unspecified):")
    for g in ["Indian", "OtherAsian", "Japanese", "Chinese", "Mexican", "American", "Pizza", "Coffee", "FastFood", "Deli"]:
        d = U[U.g == g]
        print(f"  {g:12s} est {len(FE[g]):3d} insp {len(d):4d} est-mean {ci(boot_mean(FE[g]), 1)}  15+ {100 * d.ge15.mean():.1f}%")
    fr_ff, _ = boot_ratio(FE["Indian"], FE["FastFood"])
    fr_am, fr_am_d = boot_ratio(FE["Indian"], FE["American"])
    am_nomv = U[(U.g == "American") & (U.chain_type != "multi_venue")].groupby("permit_id").score.mean().values
    fr_am2, _ = boot_ratio(FE["Indian"], am_nomv)
    print(f"Frisco raw Indian/American {ci(fr_am)}, difference {ci(fr_am_d, 1)} points; Indian/FastFood {ci(fr_ff)}; Indian/American without "
          f"{int((U[U.g == 'American'].groupby('permit_id').chain_type.first() == 'multi_venue').sum())} multi-venue outlets {ci(fr_am2)}")
    U["gg"] = U.g.fillna("Other")
    Mfr = U.rename(columns={"permit_id": "establishment_id"})
    Mfr["g"] = Mfr.gg
    fctrl = " + C(inspector) + C(permit_type) + C(risk) + C(chain_type) + C(month)"
    rfr = fit(Mfr, y="score", ctrl=fctrl)
    print("Frisco adjusted (Frisco's controls: inspector, permit type, risk, chain type, month; Poisson; our label pooling):")
    for line in model_line(rfr):
        print("  " + line)
    rfr2 = fit(Mfr, y="score", ctrl=" + C(inspector) + C(chain_type) + C(month)")
    print("Frisco adjusted with McKinney-like controls only (inspector, chain type, month):")
    for line in model_line(rfr2, groups=("Indian",)):
        print("  " + line)
    cnt = U[U.establishment_name.str.contains(r"AFC SUSHI|HISSHO|YUMMI SUSHI|SNOWFOX|MAI SUSHI WM", case=False, na=False)]
    print("Frisco supermarket sushi-counter brands inside the restaurant universe, by city label:",
          cnt.groupby("food_type").agg(permits=("permit_id", "nunique"), inspections=("score", "size"),
                                       mean=("score", "mean")).round(2).to_dict("index"))
    print(f"McKinney difference Indian - American (establishment level): {ci(boot_ratio(E['Indian'], E['American'])[1], 1)} points; "
          f"American baseline McKinney {E['American'].mean():.2f} vs Frisco {FE['American'].mean():.2f}")
    fch = U.groupby(["g", "permit_id"]).chain_type.first().eq("chain").groupby("g").mean()
    print("Frisco share of permits that are named chains:", (100 * fch).round(0).loc[
        ["Indian", "OtherAsian", "Japanese", "Chinese", "American", "FastFood"]].to_dict())
    print("Frisco mean score, all label groups:", round(U.score.mean(), 2), "| McKinney R mean score_std:",
          round(M0.score_std.mean(), 2), "| McKinney R 2024-2026 mean score_printed:", round(P24.score_printed.mean(), 2))
    print("\nSide by side (establishment-level raw ratio; adjusted Poisson ratio):")
    for g in ["Indian", "OtherAsian", "Japanese", "Chinese", "Mexican", "FastFood"]:
        mk_r = boot_ratio(E[g], E["American"])[0]
        fr_r = boot_ratio(FE[g], FE["American"])[0]
        print(f"  {g:12s} vs American  McKinney raw {ci(mk_r)} adj {ci(contrast(FITS['full (month)'], g))}   |   "
              f"Frisco raw {ci(fr_r)} adj {ci(contrast(rfr, g))}")
    print(f"  Indian vs FastFood   McKinney raw {ci(boot_ratio(E['Indian'], E['FastFood'])[0])} adj "
          f"{ci(contrast(FITS['full (month)'], 'Indian', 'FastFood'))}   |   Frisco raw {ci(fr_ff)} adj "
          f"{ci(contrast(rfr, 'Indian', 'FastFood'))}")
    # is McKinney's Indian/American ratio different from Frisco's? (independent samples, log scale)
    def ztest(a, b):
        la, lb = np.log(a[0]), np.log(b[0])
        sa = (np.log(a[2]) - np.log(a[1])) / 3.92
        sb = (np.log(b[2]) - np.log(b[1])) / 3.92
        z = (lb - la) / np.hypot(sa, sb)
        from scipy.stats import norm
        return f"Frisco/McKinney ratio of ratios {np.exp(lb - la):.2f}, z {z:.2f}, p {2 * norm.sf(abs(z)):.3f}"
    fr_adj = contrast(rfr, "Indian")
    print("\nTest of adjusted Indian/American, Frisco vs McKinney:")
    print("  McKinney full period, score_std:", ci(contrast(FITS["full (month)"], "Indian")), "|", ztest(contrast(FITS["full (month)"], "Indian"), fr_adj))
    print("  McKinney Frisco window, score_printed:", ci(contrast(SENS["Frisco's window, score_printed where a report exists"], "Indian")), "|",
          ztest(contrast(SENS["Frisco's window, score_printed where a report exists"], "Indian"), fr_adj))
    # difference of the establishment-level point gaps, both bootstrapped
    def boot_gap(ea, eb):
        return (ea[rng.integers(0, len(ea), (NB, len(ea)))].mean(1) - eb[rng.integers(0, len(eb), (NB, len(eb)))].mean(1))
    gm, gf = boot_gap(E["Indian"], E["American"]), boot_gap(FE["Indian"], FE["American"])
    dd = gm - gf
    print(f"  Establishment-level point gap Indian - American: McKinney {E['Indian'].mean() - E['American'].mean():.1f}, Frisco "
          f"{FE['Indian'].mean() - FE['American'].mean():.1f}; McKinney minus Frisco {dd.mean():.1f} "
          f"({np.percentile(dd, 2.5):.1f} to {np.percentile(dd, 97.5):.1f})")
    Pw = Mf[Mf.score_printed.notna()]
    ew = {g: est_means(Pw[Pw.g == g], "score_printed") for g in ["Indian", "American"]}
    print(f"  McKinney Frisco window, score_printed, establishment level: Indian {ew['Indian'].mean():.2f} vs American "
          f"{ew['American'].mean():.2f}, gap {ew['Indian'].mean() - ew['American'].mean():.1f}")
else:
    print("Frisco folder not found; skipping recomputation.")

print(f"\n[done in {time.time() - T0:.0f}s]")
