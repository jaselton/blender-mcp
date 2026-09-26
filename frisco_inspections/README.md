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

## Data dictionary

### `inspections.csv`

| Column | Meaning |
|---|---|
| `inspection_id` | The portal's ID for the inspection |
| `inspection_date` | Date of the inspection |
| `time_in`, `time_out` | As printed on the report; `time_in_24h`, `time_out_24h` are the same in 24-hour form |
| `purpose` | `Routine` or `Reinspection` |
| `inspection_type` | Always "Retail Food Establishment" |
| `score` | Official demerit score (higher is worse; 0 is perfect) |
| `printed_points_total` | Sum of the points printed next to each item on the form (usually equals `score`; see caveats) |
| `violation_entries` | Violation blocks on the report (an item can have several) |
| `scored_violation_entries` / `warning_entries` | Entries with status `OUT` / `OUT-W` |
| `items_out` | Distinct form items marked `OUT` |
| `priority_items_out`, `priority_foundation_items_out`, `core_items_out` | `items_out` split by category |
| `repeat_entries`, `corrected_on_site_entries` | Entries the inspector marked Repeat / Corrected On Site |
| `form_repeat_count`, `form_cos_count` | The "R" and "COS" boxes printed on the form |
| `followup_required` | The form's Followup box (YES/NO) |
| `risk_category` | Risk category on the form (1–3) |
| `general_comment` | The inspector's general comment on the report |
| `listing_comment` | The comment field returned by the portal's listing API (often the same note) |
| `closure_mentioned`, `closure_text` | Keyword screen for closures / suspensions / reopenings (see caveats) |
| `inspector`, `inspector_email` | City inspector who signed the report |
| `person_in_charge`, `person_in_charge_title` | Establishment employee who received the report |
| `establishment_name`, `permit_id`, `permit_number` | Establishment and its permit (`permit_number` as printed, e.g. H16-0584) |
| `permit_type`, `permit_category` | The portal's permit type, and a grouping of it (see caveats) |
| `program`, `food_type` | Portal program ("Health"; one "Swim") and the city's food-type label |
| `address`, `city`, `zip`, `location_key` | Location; `location_key` normalizes address + suite + ZIP |
| `owner_name` | Permit holder as printed on the report |
| `measurements`, `pdf_pages` | Count of readings; pages in the PDF |
| `found_via` | `listing`, or `permit_page` for an inspection the listing API hid (see "How the data was collected") |
| `pdf_url`, `web_url` | The city's report PDF and web page for the inspection |

### `violations.csv`

One row per violation block on a report, in report order (`seq`). Carries
the inspection's date, establishment, permit, purpose and score, plus:

| Column | Meaning |
|---|---|
| `item_number`, `item_title` | Item on the Texas form (1–47) |
| `item_category` | Priority / Priority Foundation / Core |
| `status` | `OUT` (scored) or `OUT-W` (warning, 0 points) |
| `item_points` | Points printed for that item on the form (the item is scored once even if it has several entries) |
| `corrected_on_site`, `repeat` | Flags from the report's "Type" line |
| `correct_by_date` | Deadline the inspector set, if any |
| `comments` | The inspector's narrative |
| `code`, `code_text` | Food-code citation(s) and rule text; several are joined with " \| ". A leading `*` marks a priority citation. Blank when the report shows none. |
| `code_is_priority_marked` | Any citation starts with `*` |

### `measurements.csv`

`item`, `location`, `value`, `unit` for each temperature or sanitizer
reading ("Measured Observations").

### `establishments.csv`

One row per permit: name, address, `location_key`, permit type/category,
food type, owner, and roll-ups of its inspections (count, routine vs.
reinspection, first/latest date, latest / max / mean routine score, total
violation entries, priority items out, repeat entries, inspections
mentioning a closure). "Latest" orders same-day inspections by time in.

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
   count the API reports.

   The API also returns **only each establishment's most recent inspection
   within the date range being browsed.** In the portal, which defaults to
   January 1 through today, a restaurant that failed an inspection and was
   reinspected a few days later shows up only with its reinspection; the
   failed inspection is visible only on the establishment's own page. (Chalo
   India's 46-point inspection on 2026-03-27, for example, is replaced in the
   browse list by its 18-point reinspection three days later.) A one-day
   window still shows every inspection unless an establishment was inspected
   twice on the same day, so the scraper also reads every establishment's
   permit page, which lists its full history, and fetches any inspection
   found there but not in the listing (`found_via = permit_page`).

   As a cross-check, the whole walk is repeated in 7-day windows. That pass
   must return exactly the inspections the latest-per-establishment rule
   predicts from the daily pass (in the final run, the weekly windows hid
   __WEEKLY_HIDDEN__ earlier same-week inspections, all accounted for).
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

* **Coverage starts June 23, 2025.** The portal holds nothing earlier (a
  query for 1900-01-01 through 2025-06-22 returns no rows), even though many
  permits are older. Earlier history has to be requested from the city.
* **Only routine inspections, plus a handful of reinspections, are
  published.** Every row is a "Retail Food Establishment" inspection. There
  are no complaint investigations, foodborne-illness investigations, pool
  inspections, temporary-event or pre-opening inspections.
* **Reinspections and reopenings are mostly missing.** Only __N_REINSPECT__
  inspections are labelled "Reinspection". Several establishments that were
  closed or told a follow-up was needed have no later record. Don't compute
  reinspection rates or time-to-reopen from this data; ask the city for its
  closure and follow-up records.
* **Closures are free text.** There is no closure field. `closure_mentioned`
  flags inspections whose general comment or listing comment mentions
  closing, ceasing operations, suspension or reopening (or whose violation
  narrative mentions closure, ceasing or suspension); `closure_text` shows
  the matching passage. It is a keyword screen: read each flagged report
  before calling it a closure.
* **The official score sometimes differs from the points printed on the
  form** (`score` vs. `printed_points_total`; __N_SCORE_DIFF__ inspections).
  In the cases examined, a consumer-advisory warning (item 26, `OUT-W`,
  printed 0) was still counted as 2 points. `score` is the city's published
  figure.
* **Establishments vs. permits.** `permit_id` identifies a permit, and a
  business that changes hands usually gets a new one, which splits its
  history. `location_key` (normalized address + suite + ZIP) groups permits
  at the same spot; check `owner_name` before treating two permits as one
  business.
* **"Restaurant" needs a definition.** `permit_category` groups the portal's
  permit types. "Food establishment" is the closest to "restaurant" but also
  covers coffee shops, bars, convenience stores and institutional kitchens
  (see `food_type`). Schools, child care centers and groceries score far
  lower as a group and pull averages down.
* One food inspection of a hotel bistro (Courtyard by Marriott, 2026-06-09)
  is filed under the hotel's pool permit (`permit_category` = "Pool permit").
* `inspection_date` is a calendar date. The API stores it as midnight UTC;
  converting it to Central time would move every inspection to the evening
  before.
* Inspection volume dips in June–August 2026 (about 140–160 a month against
  175–280 in other full months), and a few weekdays (e.g. Jan. 26–29, 2026)
  have no inspections. The daily and weekly passes agree, so this is what
  the portal holds; ask the city before reading it as a trend.
* **Portal quirks worth knowing if you check records by hand:**
  - The browse list shows only each establishment's latest inspection in the
    chosen date range (see "How the data was collected"), so a failed
    inspection followed by a reinspection disappears from it.
  - The browse list's "Reinspection" filter sends the value "Follow-Up",
    which matches nothing; it always returns no results.
  - An inspection's web page omits `OUT-W` warnings and shows only the first
    comment for each item number. The PDF is the full record.

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
