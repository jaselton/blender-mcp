"""Shared definitions for the McKinney story analysis.

Scores
  score_std      the city layer's items at form weights (3/2/1). The layer leaves out items
                 corrected on site, so this is a lower bound on the official score. Available
                 for every inspection, 2017-2026.
  score_printed  the TOTAL/SCORE printed on the report PDF (includes corrected-on-site items).
                 Only where a report is published: mostly 2024-2026.

Universe R ("restaurants"): routine inspections (purpose Routine or Unrecorded), not excluded
(void, test, non-permit), at permits whose cuisine/establishment-type code is a restaurant
category, excluding Trade Days, farmers-market and event sites.

Cuisine codes are ours, not the city's: two independent coders plus adjudication
(data/cuisine_codes.csv; category kappa 0.93).
"""
from pathlib import Path

import numpy as np
import pandas as pd

D = Path(__file__).resolve().parent.parent / "data"
ins = pd.read_csv(D / "inspections.csv", dtype={"link_number": str, "establishment_id": str},
                  parse_dates=["inspection_date"], low_memory=False)
vio = pd.read_csv(D / "violations.csv", dtype={"link_number": str, "establishment_id": str},
                  parse_dates=["inspection_date"], low_memory=False)
permits = pd.read_csv(D / "permits.csv", dtype=str)
establishments = pd.read_csv(D / "establishments.csv", dtype=str)

NON_RESTAURANT = {"Grocery/Market", "Convenience/Gas", "Institutional", "Hotel/Caterer/Commissary",
                  "Venue/Concession", "Other non-restaurant", "Unknown"}
GROUP = {
    "Indian": "Indian", "Chinese": "Chinese", "Japanese": "Japanese",
    "Thai": "Other Asian", "Vietnamese": "Other Asian", "Korean": "Other Asian", "Fusion": "Other Asian",
    "Asian (unspecified)": "Other Asian",
    "Mediterranean/Middle Eastern": "Mediterranean/Middle Eastern",
    "Mexican/Tex-Mex/Latin": "Mexican/Tex-Mex/Latin",
    "American (sit-down, bars, venues)": "American (sit-down, bars, venues)",
    "Fast Food": "Fast Food", "Pizza/Italian": "Pizza/Italian",
    "Coffee/Cafe/Bakery/Dessert": "Coffee/Cafe/Bakery/Dessert", "Deli/Sandwich/Salad": "Deli/Sandwich/Salad",
    "Other cuisine": "Other cuisine",
}

ins["year"] = ins.inspection_date.dt.year
ins["month"] = ins.inspection_date.dt.to_period("M").astype(str)
ins["group"] = ins.category.map(GROUP)
ins["chain2"] = ins.chain.map({"national_regional": "chain", "local_multi": "chain", "independent": "independent"})
ins["ge15"] = (ins.score_std >= 15).astype(int)
ins["zero"] = (ins.score_std == 0).astype(int)
cyc = permits.set_index("link_number").layer_inspection_cycle
ins["inspection_cycle"] = ins.link_number.map(cyc).fillna("not current")

R = ins[ins.routine & ~ins.category.isin(NON_RESTAURANT) & ins.category.notna()
        & ~ins.market_or_event_site.fillna(False).astype(bool)].copy()

ITEM_GROUP = {}
for n in range(1, 48):
    ITEM_GROUP[n] = ("temperature control" if n <= 6 else "source" if n <= 8 else
                     "contamination, cleaning and sanitizing" if n in (9, 10, 11, 15, 18) else
                     "hands and employee health" if n in (12, 13, 14) else
                     "other priority" if n <= 20 else "priority foundation" if n <= 33 else "core")

if __name__ == "__main__":
    print("inspections", len(ins), "routine", int(ins.routine.sum()), "restaurant universe R", len(R),
          "establishments in R", R.establishment_id.nunique())
    print(R.group.value_counts().to_string())
