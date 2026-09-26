"""Pull every inspection row from the public listing API.

The API caps each query at 225 rows, so the listing is walked one day at a
time (Frisco logs roughly 10-25 inspections per day), then walked again in
7-day windows as an independent cross-check. Both passes must return the same
set of inspection IDs or the script exits non-zero.

Usage: python scrape_listing.py [--start 2015-01-01] [--end YYYY-MM-DD]
"""

import argparse
import datetime as dt
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from client import MAX_START, PAGE_SIZE, get_food_types, search_inspections

OUT = Path(__file__).parent / "data" / "raw"


# Sort orders used to read each window. The API's default order is by date
# only, and when rows tie on date its offset pages are not consistent with
# each other: some rows never appear on any page and others appear twice,
# identically on every re-read (2025-08-26 has 26 inspections, but paging the
# default order only ever returns 25 distinct ones). Reading the same window
# sorted by other columns surfaces the rows the default order hides.
SORTS = [
    None,
    {"field": "establishmentName", "direction": "asc"},
    {"field": "establishmentName", "direction": "desc"},
    {"field": "score", "direction": "asc"},
    {"field": "score", "direction": "desc"},
]


def fetch_window(first, last, max_rounds=4):
    """All rows in [first, last]; raises if the window hits the API's cap.

    The number of rows in a window is stable, so the window is re-read under
    different sort orders until the union of inspection IDs reaches it.
    """
    rng = f"{first.isoformat()} to {last.isoformat()}"
    by_id = {}
    totals = set()
    for _ in range(max_rounds):
        for sort in SORTS:
            total = 0
            for start in range(0, MAX_START + 1, PAGE_SIZE):
                page = search_inspections(rng, start=start, sort=sort)
                total += len(page)
                for r in page:
                    by_id[r["inspectionID"]] = r
                if len(page) < PAGE_SIZE:
                    break
            else:
                raise RuntimeError(f"window {rng} returned the maximum {total} rows; shrink it")
            totals.add(total)
            if len(totals) > 1:
                raise RuntimeError(f"window {rng} row count changed between reads: {sorted(totals)}")
            if len(by_id) == total:
                return list(by_id.values())
            if len(by_id) > total:
                raise RuntimeError(f"window {rng}: {len(by_id)} distinct rows but API reports {total}")
    raise RuntimeError(f"window {rng}: only {len(by_id)} of {total} rows after {max_rounds} rounds")


def windows(first, last, days):
    d = first
    while d <= last:
        yield d, min(d + dt.timedelta(days=days - 1), last)
        d += dt.timedelta(days=days)


def year_has_rows(year, last):
    first = dt.date(year, 1, 1)
    end = min(dt.date(year, 12, 31), last)
    return bool(search_inspections(f"{first} to {end}"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default=dt.date.today().isoformat())
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    first = dt.date.fromisoformat(args.start)
    last = dt.date.fromisoformat(args.end)

    # Skip whole years with no data (the portal's history starts mid-2025).
    years = [y for y in range(first.year, last.year + 1) if year_has_rows(y, last)]
    print("years with data:", years, file=sys.stderr)
    active = []
    for y in years:
        active.append((max(first, dt.date(y, 1, 1)), min(last, dt.date(y, 12, 31))))

    def walk(days):
        wins = [w for a, b in active for w in windows(a, b, days)]
        done = [0]

        def fetch(w):
            rows = fetch_window(*w)
            done[0] += 1
            if done[0] % 50 == 0:
                print(f"  {days}-day windows: {done[0]}/{len(wins)}", file=sys.stderr, flush=True)
            return rows

        with ThreadPoolExecutor(args.workers) as ex:
            results = list(ex.map(fetch, wins))
        rows = [r for chunk in results for r in chunk]
        return wins, results, rows

    day_wins, day_results, day_rows = walk(1)
    week_wins, _, week_rows = walk(7)

    day_ids = [r["inspectionID"] for r in day_rows]
    week_ids = [r["inspectionID"] for r in week_rows]
    dupes = len(day_ids) - len(set(day_ids))
    print(
        f"daily pass: {len(day_rows)} rows ({len(set(day_ids))} unique, {dupes} dupes) "
        f"over {len(day_wins)} days; weekly pass: {len(week_rows)} rows",
        file=sys.stderr,
    )
    busiest = max(zip(day_results, day_wins), key=lambda x: len(x[0]))
    print(f"busiest day: {busiest[1][0]} with {len(busiest[0])} rows", file=sys.stderr)

    # Rows are keyed by inspection ID; a row that shows up in two windows must
    # be identical in both.
    by_id = {}
    for r in day_rows + week_rows:
        prev = by_id.setdefault(r["inspectionID"], r)
        if prev != r:
            print(f"WARNING: row differs between passes: {r['inspectionID']}", file=sys.stderr)

    ok = set(day_ids) == set(week_ids)
    if not ok:
        print(
            f"MISMATCH: only-daily={sorted(set(day_ids) - set(week_ids))[:20]} "
            f"only-weekly={sorted(set(week_ids) - set(day_ids))[:20]}",
            file=sys.stderr,
        )

    OUT.mkdir(parents=True, exist_ok=True)
    rows = sorted(by_id.values(), key=lambda r: (r["inspectionDate"], r["inspectionID"]))
    with open(OUT / "listing.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    food_types = get_food_types()
    (OUT / "food_types.json").write_text(json.dumps(food_types, indent=2, sort_keys=True) + "\n")
    meta = {
        "scraped_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "range": [args.start, args.end],
        "years_with_data": years,
        "rows": len(rows),
        "daily_pass_rows": len(day_rows),
        "weekly_pass_rows": len(week_rows),
        "passes_match": ok,
    }
    (OUT / "listing_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(meta, indent=2))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
