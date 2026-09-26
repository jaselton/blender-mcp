# Frisco, Texas restaurant inspection data

Every food-establishment inspection the City of Frisco Health & Food Safety
division has published on its public portal,
<https://inspections.myhealthdepartment.com/frisco>, scraped, parsed, and
cross-checked for reporting.

__SUMMARY__

## What's in `data/`

| File | One row per | Notes |
|---|---|---|
| `inspections.csv` | inspection | score, counts of violations by category, inspector, comments, links to the source |
| `violations.csv` | violation entry on an inspection report | item number and title, OUT vs. OUT-W, points, corrected-on-site / repeat flags, inspector narrative, food-code citation |
| `establishments.csv` | permit (establishment) | inspection history rolled up: latest / max / mean score, totals |
| `measurements.csv` | temperature or sanitizer reading | food item, location, value, unit |
| `items.csv` | item on the Texas inspection form | title, category, demerit points (confirmed from the PDFs) |
| `validation.json` | — | results of every cross-source check (see "How the data was checked") |
| `raw/listing.jsonl` | inspection | rows exactly as returned by the portal's listing API |
| `raw/inspections_parsed.jsonl.gz` | inspection | full parse of each PDF and web page, for provenance |
| `raw/permits_parsed.jsonl` | permit | each establishment's permit page |
| `raw/food_types.json` | — | the portal's food-type IDs and names |

Every inspection and violation row carries `pdf_url`, a link to the city's
original inspection report, so any number can be traced back to the source
document.

## How Frisco scores inspections

Frisco uses the Texas Department of State Health Services inspection form, a
**demerit** system: a *higher* score is *worse*, and 0 is a perfect
inspection. The form has 47 numbered items:

| Items | Category | Demerits when marked OUT | Must be corrected within (per the TX form) |
|---|---|---|---|
| 1–20 | Priority | 3 | immediately, no more than 3 days |
| 21–33 | Priority Foundation | 2 | 10 days |
| 34–47 | Core | 1 | 90 days |

An item is scored once per inspection even when the inspector writes up
several separate problems under it, so `violations.csv` can have more rows
than an inspection has scored items. The demerit weights above were
confirmed from the point values printed on every PDF (see `items.csv`).

**Status values**

* `OUT` — out of compliance; counted in the score.
* `OUT-W` — written up on the report with 0 points (a warning). These appear
  in the PDF but **are not shown on the portal's web page** for the
  inspection.

__DICTIONARY__

## How the data was collected

1. **Listing.** The portal's front page loads inspections from a JSON API
   (`POST /` with `task: "searchInspections"`). The API returns at most 25
   rows per call and refuses offsets past 200, so no single query can return
   more than 225 rows. `scrape_listing.py` therefore walks the calendar one
   day at a time. By default the API sorts only by date, and when
   inspections share a date its pages are inconsistent with each other:
   paging through a day can repeat one inspection and never show another,
   the same way on every try (on 2025-08-26 the API reports 26 inspections
   but default paging only ever returns 25 distinct ones). **The portal's own
   "Load More Results" list has the same flaw.** The scraper therefore reads
   each day under several sort orders (default, name A–Z and Z–A, score up
   and down) until the set of inspection IDs it has seen equals the row
   count the API reports. The whole walk is then repeated in 7-day windows,
   and the two passes must produce the identical set of inspections.
2. **Details.** For every inspection, `scrape_details.py` downloads the
   inspection PDF ("View Original Inspection PDF") and the inspection's web
   page, and for every establishment its permit page.
3. **Parsing.** `parse_pdf.py` reads the PDF. Page 1 is an image of the state
   form with the values printed on top of it; each value is identified by its
   position on the form, and the demerit points are read from next to each
   item's OUT box. Pages 2+ hold the temperature readings, the inspector's
   general comment, and one block per violation (status, corrected-on-site /
   repeat flags, correct-by date, narrative, and food-code citation).
4. **Build.** `build.py` joins everything into the CSVs and runs the checks
   below.

The site's load balancer rejects requests without a browser-like
User-Agent and briefly blocks bursts of requests, so the client sends a
browser User-Agent and paces itself (about 2 requests per second, slowing
down automatically if it is blocked).

## How the data was checked

`build.py` compares independent sources for every inspection and writes the
results to `validation.json`:

__CHECKS__

## Caveats for reporting

__CAVEATS__

## Re-running

```bash
cd frisco_inspections
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python scrape_listing.py --start 2025-01-01      # ~15 min
python scrape_details.py                          # ~1-2 h; resumable (cache in ./.cache)
python build.py
gzip -f data/raw/inspections_parsed.jsonl
```

Set `FRISCO_CACHE` to put the download cache (≈2.5 GB of PDFs) somewhere
else, and `FRISCO_MIN_INTERVAL` to change the pause between requests
(seconds, default 0.4).
