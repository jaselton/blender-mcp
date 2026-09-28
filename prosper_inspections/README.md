# Prosper, Texas health-inspection results (as published in the Town's monthly reports)

The Town of Prosper does not publish a restaurant-scores portal, an open-data
set or per-inspection reports. The only public record of its health
inspections is a table, "Health Inspection Results", inside the Development
Services **monthly report**, plus a printed monthly count of health
inspections. This folder turns those tables into a dataset and measures how
much of the Town's own inspection count they cover.

**3,229 table rows from 94 monthly reports, January 2016 to August 2026**
(with gaps; see "Caveats for reporting"). 63 of those rows are reprints of an
earlier report, which leaves 3,166. The PDFs were collected on Sept. 28, 2026.

* 870 rows carry a score on the Town's current **0-100 scale** (Feb. 2019 on);
  455 of them are restaurants. 85 rows carry a score on the older
  **27-item demerit scale** (2016 to April 2017). The rest are follow-ups,
  complaints, pre-opening / certificate-of-occupancy checks, pools, mobile
  units and temporary-event booths, which are not scored.
* In the 79 months where both exist, the tables list **3,019 rows against
  3,572 health inspections in the Town's own monthly count (84.5%)**. The
  tables are shorter than the count in 53 months, and 10 or more short in 21 of
  them. They match in 22 months and list more rows than the count in 4. Reprinted
  rows are not counted. **The tables are a published subset, not a complete
  inspection log.**
* Dates are **month only**. There are **no item-level violations**, no
  inspector names and no inspection report documents.
* **Two reprints in the source** are flagged (`excluded_as_reprint`) and left
  out of every statistic here:
  * The June 2026 report repeats the May 2026 table (50 rows). 46 rows are
    identical in all five printed cells; the other 4 differ only in how one
    address is written.
  * Page 15 of the August 2024 report reprints page 13 of the July 2024
    report: 13 temporary-event vendors at 1551 W Frontier Pkwy, in the same
    order. Probably these vendors worked the July 4th event at Frontier Park,
    so August is the copy (inference).

Every count and statistic in this README is produced by `build.py` (or
`fetch.py`) and stored in `data/validation.json` or `data/raw/fetch_meta.json`;
dates, quotations and ordinance citations come from the documents themselves.
The exception is "Corrections after review", whose before/after figures come
from comparing the outputs of the two builds.

## What's in `data/`

| File | One row per | Notes |
|---|---|---|
| `inspections.csv` | row of a "Health Inspection Results" table | report month and PDF page, business, type, address, the score/purpose and pass/fail cells as printed, normalized purpose / result / facility class, score on the right scale, closure and re-open wording, reprint flags, establishment and permit match, hand-check result |
| `monthly_counts.csv` | calendar month, July 2015 to Aug. 2026 | the Town's printed "Health Inspections" count and year-to-date total, the same month as reprinted a year later, the year-to-date chain check, the best count, and the gap between the count and the table rows; also each table's printed heading |
| `establishments.csv` | establishment | 1,008 in all: 226 from the permit list and 782 found only in the tables. The permit-list ones are the 390 Tyler EnerGov food permits collapsed by address and name. Each has roll-ups of its rows |
| `narrative_events.csv` | health event told in prose | the August 2015 closure narrative from the 2015 staff reports |
| `hand_check.csv` | row read by hand | verdict of the reading of each selected row against the PDF text (see "How the data was checked") |
| `validation.json` | - | every check below, with its result |
| `raw/archive_index.json` | Archive Center item | the 73 items of the "Monthly Development Report" archive (ADID, title, month) |
| `raw/pdf_manifest.json` | downloaded PDF | source URL, bytes, sha256 and SHA-1, cache path, for all 105 PDFs |
| `raw/wayback_cdx.json` | Wayback file | the CDX queries, which old-site files were downloaded or skipped and why, the fallback captures of each, and the size summary of the files not downloaded |
| `raw/parsed_reports.json.gz` | PDF | everything `parse.py` read from each PDF: table rows, printed count line, scale note, table headings, page-1 department, narrative counts and events, and the independent text checks (no page text; private-looking addresses withheld) |
| `raw/permits_food.json.gz` | food operational permit | 390 of the 393 public Food Establishment permits (3 EnerGov test records dropped): number, class, status, dates, address, trade name (DBA), and an opaque company key. The legal company name is withheld where it could be a person's name (see "Privacy") |
| `raw/fetch_meta.json` | fetch run | each run's request counts from the request log (including retried 5xx responses), and the robots.txt status of each host |

The PDFs themselves (105 files, 260 MB) are not committed. They are listed with
their sha256 in `raw/pdf_manifest.json`, and `fetch.py` re-downloads any that
are missing from the cache.

## Sources

1. **Town of Prosper Archive Center, "Monthly Development Report"**
   (<https://www.prospertx.gov/Archive.aspx?AMID=40>): 73 reports, June 2020
   to August 2026. November and December 2023 are not in the archive. Each PDF
   is at `https://www.prospertx.gov/Archive.aspx?ADID={ADID}`; the ADID and
   URL are on every row read from it (Wayback rows carry the capture URL).
2. **Wayback Machine copies of the Town's old WordPress site**
   (`prospertx.gov/wp-content/uploads/`): the Development Services reports for
   July 2015 to April 2017 and for Feb., July, Aug. and Dec. 2019 and March
   and April 2020. No report for May 2017 to January 2019 was found.
   * The file named "...June-2019-1.pdf" is the July 2019 report. Its first
     page says July 2019, and the month is taken from the page.
   * "Monthly-Development-Report-September-1.pdf" is a Public Works monthly
     report (its first page says so). It is set aside, not treated as a copy
     of the Development Services report.
   * The 53 WordPress files named `Monthly-report-<month>-<year>` (2018 to 2022)
     were not downloaded. That they are Fire Department reports is an inference:
     the one opened in the recon (June 2018) is a Fire Chief activity report,
     and their Wayback records are 121,863 to 316,327 bytes, against 940,524 to
     4,160,299 bytes for the 33 Development Services report files of the same
     years.
   * Copies of months that are in the Archive Center were skipped (27 files).
     19 of them have a capture with the same SHA-1 as the Archive Center PDF.
     Each of the other 8 has a single capture of about 1 MB. That is
     consistent with the truncated 1 MiB records the Wayback Machine holds for
     some of these files (inference). The verification pass downloaded the
     December 2020 one. It stops at exactly 1,048,576 bytes with no `%%EOF`,
     yet its SHA-1 equals its CDX digest. So only the end-of-file check in
     `fetch.py` would reject such a capture.
   * The six 2015 staff reports are narrative. Their inspection lists were
     "attached" and are not in the archived PDFs. Three of them state a
     count: "Eight (8) Health Inspections were performed in October",
     "Seven (7)" in November and "Sixteen (16)" in December. These are in
     `monthly_counts.csv` (`count_line_method` = "narrative sentence"). The
     August 2015 report describes a closure (see "Closure rule"). The December 2016 report says
     "No Health Inspections were performed in December 2016".
3. **Tyler EnerGov Citizen Self Service** (tenant ProsperTXProd), the public
   search of Food Establishment operational permits: 393 permits found, of which
   3 are EnerGov test records (FOOD-26-0299, -0325 and -0336: void records with
   "test" in the name). Those three are dropped, leaving 390 (FOOD-22-0003 and
   FOOD-23-* to FOOD-26-*). Used only as an establishment master.

Not used, deliberately: the EnerGov/Civic Access endpoints that return the
latest inspection behind a permit (the project's recon found that the Town
configures its food-inspection types as hidden from public search, so those
records are not treated as published), any contact or people endpoint, and
third-party inspection portals. `fetch.py` reads each host's robots.txt before its first request and
checks every URL against it.

## How Prosper scores inspections

Two scales appear, and **they must not be mixed**:

| Era (`scale_era`) | Reports | Scale | Column |
|---|---|---|---|
| `demerit_27item` | 2016 to April 2017 | "The Health Inspector reviews 27 items... Each item... carries a demerit value of 3, 4, or 5... Best possible score is 0. An establishment 'fails' when it receives more than 30 demerits." | `demerit_27` (left as printed; **not** converted) |
| `score_100` | Feb. 2019 to Aug. 2026 | "Routine inspections will be recorded as a score from 0 to 100, with 100 being the highest score. Non-compliance deductions are in 3 point, 2 point or 1 point deductions... set by the Department of State Health Services." | `score_100`, and `demerits_equiv` = 100 - `score_100` for comparison with cities that publish demerits (Frisco, McKinney) |

The era comes from the note printed under each report's table
(`scale_era_basis`). The 0-100 note is printed in the reports from February
2019 to February 2025 (except January 2024). The 20 reports without a note
(January 2016, January 2024 and March 2025 on) are assigned their era by
inference: the same kind of scores and pass/fail pattern continue, and no
report describes another scale. The switch happened between May 2017 and January
2019, months for which no report was found.

**Closure rule.** Ordinance No. 2021-74 (passed Dec. 14, 2021, effective Jan.
1, 2022) replaced Article 6.04 of the Code. Sec. 6.04.010: "Establishments
with a sanitation score below 70 percent will be closed until such time that a
reinspection is made and all corrective action on all identified critical
violations are complete." Sec. 6.04.002(b) makes "more than 30 demerits" an
imminent health hazard. The signed ordinance is on the Wayback Machine:
<https://web.archive.org/web/20230928185438id_/https://www.prospertx.gov/DocumentCenter/View/122/Food-Establishment-Ordinance-PDF>
(its text was read in the project's recon; the live Town link now returns 404).
`closure_rule_applies` marks 0-100 scores below 70 from January 2022 on. The
rules in force before 2022 (Ordinances 17-81 and 18-69) were not obtained.

What the tables show about closures (from `validation.json` -> `closures`):

* 7 rows score below 70. 5 of them are from 2022 on; 4 of those 5 carry
  closure wording in the table ("Fail/Closure", "66/Temporary Closure",
  "Fail (Restaurant Closure)"). The fifth, a 68 in August 2022, says only
  "Fail". The table does not say whether that establishment was closed.
* Closures also appear without a score ("Complaint Inspection/ Closure",
  "Temp Closure", "Electrical Fire/ Closure") and re-openings as "Follow
  Up/Re-Opened" or "Pass (Restaurant Re-Opened)". `closure_text` and
  `reopen_text` flag the wording; every such row was read by hand.
* Before the tables begin, the August 2015 staff report tells one closure in
  prose (`narrative_events.csv`): "Ernesto's closed on Thursday, August 27,
  2015, due to a small fire. Upon inspection by the Health Inspector, a roach
  infestation was found inside the building. ... The restaurant was allowed to
  reopen on Friday, August 28, 2015."

## Data dictionary

### `inspections.csv`

| Column | Meaning |
|---|---|
| `row_id` | `PR-<report month>-<row number in that report>` |
| `report_month` | The month the report covers. **The only date there is.** |
| `source`, `report_key`, `adid`, `report_title`, `report_url` | Where the row was read: Archive Center (ADID) or Wayback (capture URL) |
| `pdf_page`, `row_on_page` | Page of the PDF and position in that page's table |
| `business_name`, `business_type` | As printed. In mid-2023 the Town sometimes put the inspection purpose in the type column. |
| `address` | As printed; blank when printed "N/A"; `[withheld]` when `address_withheld` (see "Privacy") |
| `inspection_date_printed` | Only the September 2016 table has an inspection-date column |
| `score_or_purpose_raw`, `result_raw` | The two cells exactly as printed |
| `columns_swapped_in_source` | 35 October 2022 rows and 2 March 2023 rows print "Compliant"/"Non Compliant" or "Fail" under the score heading and the score or purpose under Pass/Fail; the normalized columns put them back |
| `inspection_purpose` | Normalized from the cells: `routine` (a printed score), `follow_up`, `complaint`, `complaint_follow_up`, `fbi_complaint` (foodborne illness), `preliminary`, `final`, `certificate_of_occupancy`, `courtesy`, `change_of_ownership`, `pre_opening`, `pool_routine`, `temporary_food`, `remodel`, `plan_review`, `imminent_health_hazard`, `notice_of_violation`, `fire_sprinkler`, `not_scored`, `closure`, or `unspecified` (e.g. a pool or truck with "N/A") |
| `follow_up_number`, `row_also_lists_follow_ups` | "Follow-Up No. 2" -> 2; two rows print "85 & Follow Ups (2)" |
| `facility_class` | From the type (else the name): restaurant, store, school, daycare, concession, pool, mobile, temporary, other, unknown |
| `result` | `pass`, `fail`, `not_applicable` ("N/A"), `not_stated` (2020-2022 tables often put "Follow-Up" in the Pass/Fail column), `blank` |
| `scale_era`, `scale_era_basis` | See "How Prosper scores inspections" |
| `score_100`, `demerits_equiv`, `demerit_27` | The printed number on its own scale (never both) |
| `below_70`, `over_30_demerits`, `closure_rule_applies` | Threshold flags |
| `closure_text`, `reopen_text`, `closed_for_business_text`, `imminent_hazard_text` | Wording flags (keyword screens; the flagged rows were read by hand) |
| `table_repeats_previous_month` | "2026-05" on the 50 June 2026 rows: the whole table repeats May's |
| `reprint_of_earlier_report` | The month of the earlier report that this row's run of rows repeats. A run is 4 or more consecutive rows identical in all five printed cells and in the same order: "2024-07" on the 13 August 2024 rows, "2026-05" on 46 of the June 2026 rows |
| `excluded_as_reprint` | True when either of the two columns above is set (63 rows). These rows are kept but left out of every statistic, the count comparison and the establishment roll-ups |
| `identical_rows_in_month` | How many rows of that month's table are identical to this one (all printed cells). Kept, not de-duplicated: repeat visits (follow-ups, several event days) print the same way |
| `repeats_row_across_page_break` | True on a row that is the first on its page and identical (all five printed cells) to the last row of the previous page. It may be one row printed twice across the page break, or two visits. Six rows outside the June 2026 table: PR-2021-11-045, PR-2023-01-012, PR-2023-05-047, PR-2025-07-013, PR-2026-05-012, PR-2026-05-027 |
| `establishment_id`, `permit_numbers`, `match_method`, `match_name_similarity` | Link to `establishments.csv` (see below) |
| `hand_check_reason`, `hand_check_verdict` | `flagged` / `random_10pct`, and the verdict from `hand_check.csv`. The verdict covers all five printed cells (see "How the data was checked") |

### `monthly_counts.csv`

| Column | Meaning |
|---|---|
| `report_month`, `report_available`, `report_key`, `report_url` | The month; whether a report for it was found, and which (blank when none) |
| `table_heading_printed`, `table_heading_note` | The title printed above the month's table, and a note where it names another month or is not an inspections title (July 2024; January 2016) |
| `table_rows` | Rows in that month's table, as printed |
| `rows_reprinted_from_earlier_report`, `table_rows_net` | Rows in runs reprinted from an earlier report (13 in Aug. 2024), and `table_rows` minus those (blank for June 2026, whose whole table repeats May's) |
| `printed_count`, `printed_ytd` | This month's "Health Inspections" count and year-to-date total, read by column heading (Oct.-Dec. 2015: the count stated in a sentence) |
| `printed_prior_year_count`, `printed_prior_year_ytd` | The same month a year earlier, as this report prints it |
| `count_line_is_copy_of_previous_month` | The whole count line equals the previous report's (Sept. 2023, June 2026) |
| `count_in_next_year_report`, `ytd_in_next_year_report` | This month as reprinted in the report twelve months later |
| `ytd_chain` | Whether YTD = previous YTD + this month's count |
| `count_derived_from_ytd` | Count implied by consecutive YTD totals |
| `count_best`, `count_basis` | The count used for the gap, and which source it came from |
| `gap_count_minus_rows` | `count_best` - `table_rows_net` (blank for June 2026, for months without a table, and for the 2015 narrative months, whose lists are not in the PDFs) |
| `table_repeats_previous_month` | "2026-05" for June 2026 |
| `no_inspections_statement` | The report's own statement that no inspections were done (December 2016) |
| `count_line_method` | How the count was read: `table_header` (a table whose column headings name the month and YTD), `text_header` (a text line under a heading line), `text_no_header` / `chart_two_values` (two unlabeled numbers, assigned month / YTD by size, an inference), or `narrative sentence` |
| `count_line_header`, `count_line_values` | The column headings and the numbers exactly as read from the count line |

The count covers **all** health inspections (food, pools, child care,
pre-opening, complaints, temporary events), just as the table does.

### `establishments.csv`

Permit-list establishments (`P....`) are the 390 food permits collapsed by
address, suite and trade name (permits are renumbered on renewal: 167 of the
173 active permits are numbered FOOD-26-*). Table-only establishments
(`T....`) cluster the remaining rows by address and name.

Columns: names seen in the tables, address, facility class, permit numbers,
class and status, row counts, first/last month, latest / minimum / mean 0-100
score, fail, below-70, closure and re-open rows. Reprinted rows are not
counted.

Two permits carry no public name (their company name is withheld and they
have no DBA). They appear as "[name withheld]".

Matching (`match_method`) works as follows:

* **Row to permit.** A row needs the same street number and street as the
  permit, a compatible suite, and a similar trade name. Among equally good
  names at one address, the permit of the same kind (store or restaurant)
  wins. That is how H-E-B's grocery rows find the grocery permit rather than
  the store's BBQ-restaurant permit.
* **Classes matched.** Matching is only attempted for restaurants, stores,
  concessions, schools and unclassified rows. Pools, mobile units and
  temporary booths hold other permit types, and the permit list has **no
  daycare or school-kitchen class**, so those rows cannot match.
* **Schools.** A school's name is shared by every kitchen and stand on its
  campus, so school names must match almost exactly.
* **Permit to permit.** Two permits at one address are merged only if:
  * their names are similar;
  * they belong to the same company (the opaque company key), or their names
    agree strongly (two distinctive words in common, or near-identical);
  * one is not a store while the other is a restaurant.
* **Initialisms and generic words.** "H-E-B" is read as one word.
  "Fine", "spirits", "beer" and "grocery" do not count as distinctive.
* **One permit, two counters.** The Exxon Tiger Mart & Subway permit covers
  rows listed for both.

1,425 table rows are linked to a permit-list establishment, or 1,411 when the
reprinted rows are left out (`validation.json` -> `matching` -> `matched_rows`).
Matching is heuristic; check `names_in_tables` before relying on a link.

### `narrative_events.csv`

`report_month`, `report_key`, `report_url`, `pdf_page`, and `event_text`, the
paragraph as printed (a bulleted paragraph that mentions the Health Inspector
together with a closure, re-opening, suspension or infestation). One event:
August 2015.

## How the data was collected and built

```bash
cd prosper_inspections
export PROSPER_CACHE=/path/to/cache      # PDFs and every HTTP response are cached here
python fetch.py                          # ~100 requests; skips anything already cached
python fetch.py --offline                # rebuild data/raw from the cache only (no network)
python build.py --reparse                # parse.py over the cached PDFs, then build
python build.py --worksheet              # also write the hand-review worksheet into the cache
```

Python 3 with `requests`, `pandas`, `pdfplumber` and `pypdfium2` (installed
with pdfplumber); `pdftotext` (poppler) for the text cross-check. `python
build.py` without `--reparse` rebuilds every output from the committed
`data/raw/` files alone.

* **`fetch.py`**
  * Archive Center: the index (1 request) and the PDFs, 3 s apart. A PDF must
    be complete: it starts with `%PDF` and has `%%EOF` in its last 2 KB. Its
    sha256 and SHA-1 are recorded.
  * Wayback: the CDX queries and the downloads, 4 s apart. A capture is used
    only if it is a complete PDF whose SHA-1 equals the CDX digest; otherwise
    the next capture of the same file is tried.
  * EnerGov: the permit search, 1.5 s apart.
  * Connection resets, 403/429 and 5xx responses are retried with exponential
    backoff.
  * The network run logged 97 responses and 0 exceptions. Two of the responses
    were HTTP 503 from the Wayback CDX API, and both were retried
    successfully (`fetch_meta.json` -> `runs`).
* **`parse.py`**
  * Finds every pdfplumber table whose header holds "Business Name" and
    "Pass/Fail". That covers "Health Inspection Results",
    "Health Inspections, <Month YYYY>", "<Month YYYY> Health Inspections" and
    the "Continued" pages.
  * Maps columns by their header labels, so the June 2021 tables (returned
    with an extra empty first column) and the 2026 tables (with a three-row
    header) read the same way. Joins wrapped cells and stops at the "Note:"
    row.
  * Removes the footer page number from each page before extracting its
    tables.
  * Reads the count line by its column headings:
    * 2020 reports print prior month, current month, prior YTD, current YTD.
    * Later reports print prior month, prior YTD, current month, current YTD.
    * January 2021 and 2022 print two columns.
    * February 2019 is a bar chart. Its two values are assigned month / YTD
      by size, an inference.
  * Also records each table's printed heading, the department named on
    page 1, and the 2015 narrative counts and events.
* **`build.py`**: picks one report per month, flags reprints, normalizes rows,
  reconciles counts, builds establishments, and writes the checks.

## How the data was checked

| Check | Result (final run) |
|---|---|
| Independent text engine 1: for each of the 272 health-table pages, the sequence of Pass/Fail cells read by pdfplumber's table extractor vs. the same column read from `pdftotext -layout` | identical on 271 pages; the other differs only by one genuinely blank result cell (March 2016, a fuel-station plan review) |
| Same, for the sequence of printed scores | identical on all 272 pages |
| Independent text engine 2: every non-empty text cell of every published row looked up in the page text read by PDFium (pypdfium2), whitespace ignored | 16,127 cells: 16,103 found verbatim, 24 found once hyphens are ignored (words hyphenated across a line break), 0 not found |
| Hand check: every Fail, below-70, over-30-demerit, closure, re-open and closed-for-business row (239) plus a seeded random 10% of the other rows (299), each read against the PDF's text | 538 of 538 match (`hand_check.csv`) |
| Reprints: runs of 4+ rows identical to an earlier month's table | June 2026 (repeats May 2026) and August 2024 p.15 (repeats July 2024 p.13); no others |
| Table headings vs. report month | 2 flagged: July 2024 is headed "June 2024" (0 of its 40 rows are in the June 2024 table, so probably a heading typo; inference); January 2016 is titled "Monthly Health Permits" |
| Year-to-date chain of the printed counts | 69 months consistent; 3 inconsistent (Jan. 2021 prints 84 where February's line implies 40; June 2022 and March 2025 are off by one); 2 count lines are copies of the previous month's (Sept. 2023, June 2026) |
| Prior-year columns vs. the month's own report | 6 month counts and 3 YTD totals differ; several later reports reuse the previous report's prior-year figures (these are ignored) |
| Duplicate and misfiled PDFs | 3 alternate copies of 2016 reports dropped (identical tables); 1 Public Works report set aside |
| Privacy scan of all outputs for e-mail addresses and phone numbers | 0 |

The hand check was done by an AI model reading the `pdftotext` text of each
selected row's page line by line; it confirms the extraction, not the Town's
data. The reviewer's worksheet showed all five printed cells of each row
(name, type, address, score/purpose, pass/fail) next to the PDF lines, but at
review time only name, score/purpose and pass/fail were fingerprinted.
`hand_check.csv` now also holds a five-cell fingerprint
(`fingerprint_all_cells`). It was added after the review
(`fingerprint_all_cells_recorded` says so), for rows whose review-time
fingerprint still matched. The type and address of those 538 rows are
confirmed by the automated PDFium check above, not by a second reading. From
now on, a change to any printed cell of a checked row shows up in
`validation.json` -> `hand_check` -> `verdicts_for_rows_that_changed_since_check`.

Typos in the PDFs (e.g. "1001 West Prosper Trial") are kept as printed.

`count_best` is the printed count where its YTD chain holds. Otherwise it is the
figure reprinted a year later or one implied by neighbouring YTD totals
(`count_basis` says which). With it, counts exist for 94 of the 134 months from
July 2015 to August 2026. They include:

* 11 months for which no report survives, e.g. November 2023 = 45 and
  December 2023 = 23, from the November and December 2024 reports;
* 3 months (October to December 2015) whose count is stated in a sentence.

## Caveats for reporting

* **The tables are not a complete list of inspections.** Across the 79
  comparable months they hold 84.5% of the inspections the Town counts. The
  gap moves in both directions: up to 41 fewer rows in Dec. 2020, and 8 more
  rows than the count in April 2026. A business missing from a month's table
  may still have been inspected. Nothing here supports "X was not inspected".
* **Month-only dates.** Several visits to one place in a month (routine,
  follow-ups, re-opening) are separate rows with the same month, and they are
  not always in date order.
* **Identical rows.** 135 rows (in 38 months) are identical to another row of
  the same month's table. They are kept (`identical_rows_in_month`), since
  repeat visits print the same way.
  6 of them are the first row of a page and repeat the last row of the page
  before (`repeats_row_across_page_break`). These may be one row printed twice.
* **No violations, no inspectors, no reports.** The tables give a score or a
  purpose and pass/fail, nothing else.
* **Two scoring scales.** Never average `demerit_27` with `score_100`, and do
  not convert the 27-item demerits to a 100-point score.
* **Few restaurant scores.** 455 restaurant rows carry a 0-100 score over 7.5
  years, and 17 report months since 2019 list none.
  * Of the 166 permit-list establishments with an active permit, 109 have
    ever had a 0-100 score in a table, and 39 have one since January 2025.
  * These documents cannot show whether this reflects how often restaurants
    are inspected or only what the Town chose to list (inference; ask the
    Town).
* **Gaps in coverage.** Tables exist for Jan.-Nov. 2016, Jan.-April 2017,
  Feb., July, Aug. and Dec. 2019, March-April 2020, June 2020-Oct. 2023 and
  Jan. 2024-Aug. 2026. Nov. and Dec. 2023 reports are not in the Archive
  Center.
* **Publication errors in the source**, all flagged:
  * The June 2026 table and count line repeat May 2026.
  * Page 15 of August 2024 reprints page 13 of July 2024.
  * The July 2024 table is headed "June 2024".
  * The Sept. 2023 count line repeats Aug. 2023.
  * October 2022 swaps two columns.
  * January 2021 prints a count of 84 (the December 2020 figure) where
    February's line implies 40.
* **January 2016's table is titled "Monthly Health Permits".** Its single row
  (a mobile food unit) may record a permit rather than an inspection.
* **Names and addresses are as printed** (legal name or trade name, varying
  spelling). The establishment links are heuristic.
* The Town, not Collin County or Texas DSHS, is the regulatory authority for
  food establishments inside Prosper (ordinance; DSHS registry, per the recon).

## Privacy

No contact details are kept, and no names of staff, certified food managers or
permit holders. (Business names are kept as printed, and a trade name can
include a person's name.)

* **Health tables.** They carry no staff or certified-food-manager names
  (`parse.py` counts such mentions on the health pages: 0).
* **E-mail addresses and phone numbers** are scrubbed from all kept text,
  including the narrative event; 0 remain.
* **Addresses: 149 withheld.**
  * Mobile food units are listed with an address that is often the
    operator's base, sometimes a home or a street in another city, so those
    addresses are withheld.
  * So are temporary-event vendors' addresses that are neither a shared event
    venue (3 or more vendors) nor a fixed business location.
* **Permit list.** Only public list fields are kept; holder names and the
  applicant-written description are dropped.
  * The legal company name can itself be a person's name (one permit's
    company, an LLC, appears to be named after a person), so it is withheld
    in two cases: on every permit that has a trade name (DBA), and on a
    permit without a DBA whose company name could be a person's name (a sole
    proprietorship, or two or more plain words with no corporate marker or
    business word). That is 339 company names in all.
  * An opaque `CompanyKey` (the same company gets the same key) is kept so
    that renewals can still be grouped.
  * The 3 EnerGov test records are dropped; one carried what looks like a
    staff member's first name.

## Corrections after review

An independent verification of the first build found the following. Each was
fixed in the code and the data rebuilt from the cached sources.

* The coverage figure counted the repeated June 2026 table. With it and
  the August 2024 reprint left out, the figure went from 3,082 of 3,603
  (85.5%, 80 months) to 3,019 of 3,572 (84.5%, 79 months).
* The August 2024 reprint of a July 2024 page was not flagged.
* **One parse error.** The page number of March 2023 p.14 was read into the
  last cell: row PR-2023-03-012 had "Follow Up 1" and a follow-up number,
  where the PDF prints "Follow Up". A re-parse of all 105 PDFs, compared with
  the previous parse, changed this one cell and nothing else.
* **Permit data.**
  * Three EnerGov test records were counted as permits.
  * Two operators at one suite were merged: Jerry's Liquor & Fine Wine and
    Top Shelf Liquor.
  * H-E-B's grocery rows were linked to its BBQ-restaurant permit.
  * Company names were kept that could be personal names.
* **Unrecorded source content.**
  * The 2015 narrative counts and the August 2015 closure were not recorded.
  * The July 2024 heading and the January 2016 title were not flagged.
  * The Public Works file was described as a copy of the Development
    Services report.
* The fetch summary counted only exceptions, not the two retried HTTP 503
  responses.

## Recommended public-information request

A complete, dated, violation-level record needs a Texas Public Information Act
request through the Town's portal, <https://prospertx.justfoia.com/publicportal>.
Suggested items:

1. An export of every inspection of the food-related EnerGov inspection types
   since go-live, with:
   * inspection date, establishment and permit;
   * score, individual violations and comments, and status.

   The types are Retail Food Establishment Inspection, follow-up, complaint,
   foodborne-illness complaint, mobile, temporary and Notice of Violation
   food types.
2. The inspection worksheets (the "IM_SR_Inspection_Worksheet" reports) for
   those inspections.
3. The equivalent records from the system used before 2023 (2015-2022),
   including the inspection lists "attached" to the 2015 staff reports.
4. The November and December 2023 Development Services monthly reports, a
   corrected June 2026 health table, and the August 2024 table without the
   reprinted July page.
5. How inspections are chosen for the monthly table, and the date the Town
   moved from the 27-item demerit form to the 100-point form.
6. Any log of closures, suspensions and re-openings under Sec. 6.04.010 and
   6.04.015 since January 2022.

## AI assistance

This dataset was built with substantial assistance from an AI model (Claude,
by Anthropic): it wrote the code, ran it, read the PDF text for the hand
check, and drafted this README. The figures come from the code, not from the
model's reading, and the hand check confirms the extraction against the
PDFs. Before publication, a reporter should re-check any row or figure used in
a story against the linked PDF and with the Town.
