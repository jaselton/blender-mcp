"""Reproduces the restaurant-category findings: establishment-level means with
bootstrap CIs by permit, high-score shares, Poisson / OLS / negative binomial
models with inspector, permit-type, risk, chain and month controls
(cluster-robust by permit), per-inspector comparisons, and groceries.

Run from this folder: python category_gap.py  (needs pandas, statsmodels)
"""
from common import *  # noqa: F401,F403
import statsmodels.formula.api as smf, statsmodels.api as sm, warnings; warnings.filterwarnings('ignore')
pd.set_option('display.width',250)
ICT=['Indian','Chinese','Thai']; COMP=['American (sit-down, bars, venues)','Mexican/Tex-Mex/Latin','Pizza/Italian','Coffee/Cafe/Bakery/Dessert','Fast Food']
print('UNIVERSE', len(U), 'inspections', U.permit_id.nunique(), 'permits')
print('\n[A] group table (est_mean = mean of per-permit mean scores; CI = bootstrap by permit, 2000 draws, seed 20260926)')
print(table(U,'group').round(2).to_string(index=False))
print('\n[B] ICT vs COMP inspection-level shares by threshold')
A=U[U.group.isin(ICT)]; B=U[U.group.isin(COMP)]
for t in [10,15,20,25,30]:
    print(t,(A.score>=t).sum(),len(A),round((A.score>=t).mean(),3),(B.score>=t).sum(),len(B),round((B.score>=t).mean(),4))
print('\n[C] models (Poisson QMLE, cluster-robust by permit_id), ratio of mean score vs American reference')
M=U.copy(); M['g']=M.group.replace({'American (sit-down, bars, venues)':'AAmerican'}); REF="C(g, Treatment('AAmerican'))"
ctrl=" + C(inspector) + C(permit_type) + C(risk) + C(chain_type) + C(month)"
cl={'groups':pd.factorize(M.permit_id)[0]}
for nm,rhs,fam in [('Poisson raw',REF,'p'),('Poisson full',REF+ctrl,'p'),('OLS log1p full',REF+ctrl,'o'),('NB full',REF+ctrl,'n')]:
    if fam=='p': r=smf.glm('score ~ '+rhs,M,family=sm.families.Poisson()).fit(cov_type='cluster',cov_kwds=cl)
    elif fam=='o': r=smf.ols('np.log1p(score) ~ '+rhs,M).fit(cov_type='cluster',cov_kwds=cl)
    else: r=smf.negativebinomial('score ~ '+rhs,M).fit(disp=0,maxiter=500,cov_type='cluster',cov_kwds=cl)
    s=[]
    for k in ['Indian','Chinese','Thai','Japanese','Asian (unspecified)','Mexican/Tex-Mex/Latin','Pizza/Italian','Coffee/Cafe/Bakery/Dessert','Fast Food']:
        kk=f"{REF}[T.{k}]"; ci=np.exp(r.conf_int().loc[kk]); s.append(f"{k[:12]} {np.exp(r.params[kk]):.2f} [{ci[0]:.2f},{ci[1]:.2f}]")
    print(nm,' | '.join(s))
S=U[U.group.isin(ICT+COMP)].copy(); S['ict']=S.group.isin(ICT).astype(int); cls={'groups':pd.factorize(S.permit_id)[0]}
for nm,f in [('ICT pooled, inspector FE only',' + C(inspector)'),('ICT pooled, full',ctrl)]:
    r=smf.glm('score ~ ict'+f,S,family=sm.families.Poisson()).fit(cov_type='cluster',cov_kwds=cls)
    print(nm, np.exp(r.params['ict']).round(2), np.exp(r.conf_int().loc['ict']).round(2).values)
r=smf.glm('ge15 ~ ict'+ctrl,S,family=sm.families.Poisson()).fit(cov_type='cluster',cov_kwds=cls)
print('adj RR score>=15', np.exp(r.params['ict']).round(2), np.exp(r.conf_int().loc['ict']).round(2).values)
print('\n[D] per-inspector ICT vs COMP means')
print(S.groupby(['inspector','ict']).score.agg(['size','mean']).unstack().round(2))
print('\n[E] grocery chain vs independent (routine, permit_category==Grocery; chain = known_chain regex)')
G=ins[(ins.purpose=='Routine')&(ins.permit_category=='Grocery')]
print(table(G.assign(cg=np.where(G.known_chain,'chain','independent')),'cg').round(2).to_string(index=False))
