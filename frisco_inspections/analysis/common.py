"""Shared definitions for the story analysis: the restaurant universe,
cuisine groups (from the city's food_type label), chain detection by name,
and item groups. Imported by category_gap.py.

Universe U: routine inspections at 'Food establishment' permits, excluding
food types Institutional, Convenience and Grocery and rows with no food type.
Chains: a hand-built list of national/regional brand patterns (CHAIN_PAT), or
the same two-word base name at 3+ permits ('multi_venue', e.g. stadium stands).
"""
import pandas as pd, numpy as np, re
from pathlib import Path
D=str(Path(__file__).resolve().parent.parent / 'data') + '/'
ins=pd.read_csv(D+'inspections.csv', parse_dates=['inspection_date'])
v=pd.read_csv(D+'violations.csv')
est=pd.read_csv(D+'establishments.csv')

GROUP={
 'Indian':'Indian','Chinese':'Chinese','Thai':'Thai','Vietnamese':'Vietnamese',
 'Japanese':'Japanese','Korean':'Korean','Asian':'Asian (unspecified)','Fusion':'Fusion',
 'Mediterranean':'Mediterranean/Middle Eastern','Middle Eastern':'Mediterranean/Middle Eastern','Greek':'Mediterranean/Middle Eastern',
 'Mexican':'Mexican/Tex-Mex/Latin','Tex-Mex':'Mexican/Tex-Mex/Latin','Latin American':'Mexican/Tex-Mex/Latin','Brazilian':'Mexican/Tex-Mex/Latin',
 'American':'American (sit-down, bars, venues)','Barbecue':'American (sit-down, bars, venues)','Southern':'American (sit-down, bars, venues)',
 'Homestyle':'American (sit-down, bars, venues)','Cajun':'American (sit-down, bars, venues)','Seafood':'American (sit-down, bars, venues)',
 'Steak House':'American (sit-down, bars, venues)','New American':'American (sit-down, bars, venues)','Hamburgers':'American (sit-down, bars, venues)',
 'Fast Food':'Fast Food',
 'Pizza':'Pizza/Italian','Italian':'Pizza/Italian',
 'Coffee House & Cafe':'Coffee/Cafe/Bakery/Dessert','Cafe':'Coffee/Cafe/Bakery/Dessert','Bakery':'Coffee/Cafe/Bakery/Dessert',
 'Juice Bar':'Coffee/Cafe/Bakery/Dessert','Ice Cream':'Coffee/Cafe/Bakery/Dessert','Tea Rooms':'Coffee/Cafe/Bakery/Dessert',
 'Deli':'Deli/Sandwich/Salad','Sandwiches':'Deli/Sandwich/Salad','Salads':'Deli/Sandwich/Salad',
 'Continental':'Hotel kitchens (Continental)',
 'French':'Other','German':'Other','European':'Other','Australian':'Other','Ethiopian':'Other','Intl Misc.':'Other',
 'Buffets':'Other','Specialty':'Other','Vegetarian':'Other',
}
EXCL={'Institutional','Convenience','Grocery'}

CHAIN_PAT=[r"MCDONALD",r"WENDY'?S",r"WHATABURGER",r"TACO BELL",r"\bSONIC\b",r"CHICK-FIL-A",r"RAISING CANE",r"POPEYES",r"\bKFC\b",
 r"JACK IN THE BOX",r"ARBY",r"DAIRY QUEEN",r"STEAK N SHAKE",r"IN-N-OUT",r"BURGER KING",r"FIVE GUYS",r"WINGSTOP",r"BUFFALO WILD WINGS",
 r"BOJANGLES",r"CHICKEN EXPRESS",r"LAYNE'?S",r"TACO BUENO",r"MOOYAH",r"FREDDY'?S",r"CRIMSON COWARD",r"DOG HAUS",r"WAYBACK BURGERS",
 r"HAWAIIAN BROTHERS",r"MO'?BETTAHS",r"PORTILLO",r"SCOTTY P",r"CHILI'?S",r"CHEESECAKE FACTORY",r"HOOTER",r"DAVE & BUSTER",r"MAIN EVENT",
 r"COTTON PATCH",r"BABE'?S CHICKEN",r"FIRST WATCH",r"\bIHOP\b",r"SNOOZE",r"CORNER BAKERY",r"TROPICAL SMOOTHIE",r"BLACK RIFLE",r"BLACK ROCK COFFEE",
 r"KUNG FU TEA",r"SHARETEA",r"GONG CHA",r"CHATIME",r"TP TEA",r"PANDA EXPRESS",r"PEI WEI",r"\bKURA\b",r"HAIDILAO",r"MARUFUKU",r"KYURAMEN",
 r"PEPPER LUNCH",r"SARKU",r"HISSHO",r"AFC SUSHI",r"YUMMI SUSHI",r"SNOWFOX",r"MAI SUSHI",r"\bCAVA\b",r"HALAL GUYS",r"HUMMUS REPUBLIC",
 r"CHUY'?S",r"CHIPOTLE",r"TORCHY",r"FUZZY'?S",r"VELVET TACO",r"UNCLE JULIO",r"CANTINA LAREDO",r"ROSA'?S CAFE",r"FAJITA PETE",r"FREEBIRDS",
 r"MI COCINA",r"GLORIA'?S",r"BLUE GOOSE",r"BLAZE PIZZA",r"DOMINO",r"DONATOS",r"JETS? PIZZA",r"LITTLE CAESARS",r"MARCO'?S",r"MOD PIZZA",r"MR\. JIM",
 r"PAPA JOHN",r"PAPA MURPHY",r"PIE FIVE",r"PIZZA HUT",r"CICI",r"OLIVE GARDEN",r"SBARRO",r"PIADA",r"STARBUCKS",r"DUNKIN",r"DUTCH BROS",
 r"SCOOTERS",r"SUMMER MOON",r"PJ'?S COFFEE",r"7 BREW",r"BARNES & NOBLE",r"85C",r"AUNTIE ANNE",r"CINNABON",r"CINNAHOLIC",r"CRUMBL",r"DIRTY DOUGH",
 r"EINSTEIN",r"GREAT AMERICAN COOKIE",r"KOLACHE FACTORY",r"KRISPY KREME",r"NOTHING BUNDT",r"SHIPLEY",r"SMALLCAKES",r"WETZEL",r"HURTS DONUT",
 r"PEACH COBBLER FACTORY",r"MARY'?S MOUNTAIN",r"CHIP CITY",r"PAPPAROTI",r"FIREHOUSE SUBS",r"JASON'?S DELI",r"JERSEY MIKE",r"JIMMY JOHN",r"MCALISTER",
 r"NEWK'?S",r"PANERA",r"SCHLOTZSKY",r"SUBWAY",r"CHARLEY'?S",r"WHICH WICH",r"SALATA",r"SALAD AND GO",r"CHICKEN SALAD CHICK",r"TUPELO HONEY",
 r"LA MADELEINE",r"PARIS BAGUETTE",r"RED LOBSTER",r"OUTBACK",r"PERRY'?S STEAK",r"III FORKS",r"POKEWORKS",r"CLEAN JUICE",r"NEKTER",r"JAMBA",
 r"SMOOTHIE KING",r"VITALITY BOWLS",r"PURE GREEN",r"16 HANDLES",r"ANDY'?S FROZEN",r"BAHAMA BUCKS",r"BASKIN",r"COLDSTONE",r"DIPPIN DOTS",
 r"HANDEL'?S",r"MARBLE SLAB",r"PINKBERRY",r"SWEETFROG",r"SOMISOMI",r"KWALITY",r"NATURALS ROLLS",r"BRAUM'?S",r"RUDY'?S COUNTRY",r"TXB",
 r"BAWARCHI",r"\bA2B\b",r"KAILASH PARBAT",r"HYDERABAD HOUSE",r"CURRY PIZZA HOUSE",r"KROGER",r"WAL-?MART",r"TARGET",r"TOM THUMB",r"COSTCO",
 r"SPROUTS",r"\bHEB\b",r"ALDI",r"MARKET STREET",r"99 RANCH",r"PATEL BROTHERS",r"7-ELEVEN",r"7 ELEVEN",r"QUIKTRIP",r"\bRACETRAC",r"CHEVRON",r"SHELL\b",
 r"EXXON",r"MURPHY",r"CVS",r"WALGREENS",r"DOLLAR",r"CAPITAL ONE CAFE",r"KIDZANIA",r"TOP ?GOLF",r"LITTLE WOODROW",r"54TH STREET",r"RODEO GOAT",
 r"TAILGATERS",r"CINEMARK",r"LIFETIME",r"IKEA",r"NORDSTROM",r"LIVING SPACES",r"GRAZE CRAZE",r"MARBLE SLAB",r"FRUITEALICIOUS",r"KESAR"]
CHAIN_RE=re.compile('|'.join(CHAIN_PAT), re.I)

def norm(n):
    n=n.upper()
    n=re.sub(r'#\s*\S+','',n); n=re.sub(r"[^A-Z ]"," ",n.replace("'",""))
    toks=[t for t in n.split() if t not in {'LLC','INC','DBA','THE','FRISCO','AT','OF','AND'}]
    return ' '.join(toks[:2])
est['brand2']=est.establishment_name.map(norm)
cnt=est.groupby('brand2').permit_id.nunique()
est['auto_chain']=est.brand2.map(cnt)>=3
est['known_chain']=est.establishment_name.str.contains(CHAIN_RE)
est['chain']=est.auto_chain|est.known_chain

ins=ins.merge(est[['permit_id','brand2','auto_chain','known_chain','chain']],on='permit_id',how='left')
ins['group']=ins.food_type.map(GROUP)
ins['permit_year']=pd.to_numeric('20'+ins.permit_number.str.extract(r'^H(\d\d)')[0],errors='coerce')
ins['month']=ins.inspection_date.dt.to_period('M').astype(str)
ins['ge15']=(ins.score>=15).astype(int); ins['ge30']=(ins.score>=30).astype(int); ins['zero']=(ins.score==0).astype(int)
ins['risk']=ins.risk_category.fillna(-1).astype(int).astype(str)
# universe: routine inspections at Food establishment permits, excluding Institutional/Convenience/Grocery/missing food_type
U=ins[(ins.permit_category=='Food establishment')&(ins.purpose=='Routine')&ins.food_type.notna()&~ins.food_type.isin(EXCL)].copy()

# item groups
def igroup(i):
    if 1<=i<=6: return 'temp_1_6'
    if i in (7,8,9,10,11,15,18): return 'contam_clean_7_11_15_18'
    if 12<=i<=14: return 'hygiene_12_14'
    if i in (21,22): return 'cfm_foodhandler_21_22'
    if i in (28,29): return 'datemark_thermo_28_29'
    if i==31: return 'handsink_31'
    if i==34: return 'pests_34'
    if i<=20: return 'other_priority'
    if i<=33: return 'other_pf'
    return 'other_core'
v['igroup']=v.item_number.map(igroup)
vs=v[v.counts_toward_score==True].drop_duplicates(['inspection_id','item_number'])
IG=vs.pivot_table(index='inspection_id',columns='igroup',values='item_points',aggfunc='sum',fill_value=0)
ins['chain_type']=np.where(ins.known_chain,'chain',np.where(ins.auto_chain,'multi_venue','independent'))
U=U.merge(ins[['inspection_id','chain_type']],on='inspection_id',how='left')
rng=np.random.default_rng(20260926)
def boot_est_mean(df, col='score', B=2000):
    e=df.groupby('permit_id')[col].mean().values
    bs=rng.choice(e,(B,len(e)),replace=True).mean(1)
    return e.mean(), np.percentile(bs,2.5), np.percentile(bs,97.5)
def table(df, by):
    rows=[]
    for g,d in df.groupby(by):
        m,lo,hi=boot_est_mean(d)
        pe=d.groupby('permit_id').score.max()
        rows.append(dict(group=g,insp=len(d),est=d.permit_id.nunique(),mean=d.score.mean(),median=d.score.median(),
            p0=d.zero.mean(),p15=d.ge15.mean(),n15=d.ge15.sum(),p30=d.ge30.mean(),n30=d.ge30.sum(),
            est_any15=(pe>=15).mean(),est_n15=(pe>=15).sum(),est_mean=m,ci_lo=lo,ci_hi=hi,
            insp_per_est=len(d)/d.permit_id.nunique(),chain_share=(d.groupby('permit_id').chain_type.first()=='chain').mean()))
    return pd.DataFrame(rows).sort_values('est_mean',ascending=False)
