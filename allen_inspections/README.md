# Allen, Texas food inspection data

Every food-related inspection that the City of Allen shows the public in its
Tyler EnerGov Citizen Self Service portal, plus every health-score report
(PDF list of inspection scores) the city has published that could still be
found, parsed and matched to those inspections. Collected September 28, 2026.

**9,898 inspection records** in 10 food-related inspection types (restaurants
and other food establishments, day cares, food trucks, complaints, and the
city's new 2026 "OP - Routine" types). **14 of them are test or training
entries** made in the city's new inspection app (12 on the city's own
"Training Food Establishment", 2 whose comment reads "Testing"; see
"Test records"). They are kept and flagged (`is_test_record`) so the rows
still add up to the portal's totals, and they are left out of every other
count in this README. Of the other 9,884 records, 8,429 are completed
inspections dated May 8, 2017 through September 24, 2026; the rest are
cancelled (1,133), pending or requested (322).

They are tied to **1,594 EnerGov businesses (BusinessIds)**, taken from 5,416
public operational-permit and licence records. A BusinessId is not always one
establishment: EnerGov often keeps one place under two or three BusinessIds (an
old licence record and a newer operational-permit record). 1,201 BusinessIds
have inspections; merged by name and address (`establishment_group_id`, below)
they are **1,137 establishments**, 59 of which have inspections split across
several BusinessIds.

**Scores are only partly public.** Allen scores routine inspections on a
100-point scale, but the portal does not show the score. Two public sources
carry scores:

* the inspector's free-text comment, which from 2023 onward often starts with
  the score (1,147 inspections; see "Where the scores come from");
* 17 score reports the city published between 2015 and 2025: 8 quarterly
  reports, 3 half-year reports, 4 cumulative 2019 year-to-date reports and
  2 lists of the latest score per establishment (items 1154 and 1735, covering
  2011–March 2015 and 2015–November 2017). Two are still on the city's web
  site and 15 survive only in the Internet Archive. Together they have 5,964
  lines and 5,346 numeric scores. Because the 2019 year-to-date reports repeat
  each other, that is only 3,801 distinct numeric lines (name, street, date,
  score). **3,058 inspections are matched to a report score.**

In all, **3,945 inspections have a report score, a comment score or both**.
Where both exist they agree: in all 260 inspections that have both, the two
are identical.

## Sources

| Source | URL | What it gives |
|---|---|---|
| Tyler EnerGov Citizen Self Service (anonymous JSON API behind the public portal) | <https://cityofallentx-energovweb.tylerhost.net/apps/selfservice#/home> | every inspection: type, status, dates, address, parcel; per inspection (getById): inspector, actual date, comment, link to the business or operational permit |
| EnerGov operational permits (module 12) and licences (module 10) | same portal, search | business name, permit type (Heavy / Light / No Food Preparation, Mobile, Temporary, Day Care ...), open/closed status, address |
| City Food Safety page, including the scoring FAQ | <https://www.cityofallen.org/departments/community_enhancement/health_and_food_safety/food_safety.php> | scoring rules; says current scores are "unavailable at this time due to system updates" |
| Health score report, Jul-Sep 2025 (still on the city's CMS, no longer linked) | <https://www.cityofallen.org/Documents/Departments/Community%20Enhancement/Health%20and%20Food%20Safety/Food%20Safety/Inspection%20Scores%20July-Sept%202025.pdf> (redirects to cms3.revize.com) | 279 inspections |
| Health score report, Apr-Jun 2024 (linked from the city's Document Archive page, <https://www.cityofallen.org/services/document_archive.php>) | <https://www.cityofallen.org/Archived%20Documents/City%20Secretary/Open%20Records/Health%20Inspection%20Report%20-%20April%20to%20June%202024.pdf> | 182 inspections; byte-identical to the Wayback copy linked from the Food Safety page in Sept 2024 |
| Old CivicPlus site, Archive Center "Health Scores" category (AMID=74), in the Wayback Machine | e.g. <https://web.archive.org/web/20231211033233id_/https://www.cityofallen.org/ArchiveCenter/ViewFile/Item/2755> | 15 reports, 2015-2023 (list below; every URL is in `data/pdf_scores.csv` and `data/raw/pdf_manifest.json`) |
| Allen Code of Ordinances, Ch. 6 Art. VIII | <https://library.municode.com/tx/allen/codes/code_of_ordinances?nodeId=COOR_CH6HEEN_ARTVIIIENHE> | adopts the Texas Food Establishment Rules; permit suspension (Sec. 6-237); no numeric score or failing line |

The score reports found (all figures from `validation.json` → `pdf_reports`):

| Report (`report_id`) | Kind | Period printed on the report | Lines (printed total) | Numeric scores (range) | Lines matched to EnerGov | Report score = comment score | EnerGov "Followup Reinspection" records in the period: all / matched as a same-day pair / in an unresolved same-day tie / not in the report |
|---|---|---|---|---|---|---|---|
| allen_2025Q3_live | quarter | 2025-07-01 – 2025-09-30 | 279 (279) | 229 (73–100) | 277 (99.3%) | 122/122 | 61 / 0 / 0 / 60 |
| allen_2024Q2_document_archive | quarter | 2024-04-01 – 2024-06-30 (from the file name) | 182 (—) | 160 (69–100) | 181 (99.5%) | 79/79 | 34 / 0 / 0 / 34 |
| archivecenter_item2755 | quarter | 2023-07-01 – 2023-09-30 | 214 (214) | 172 (60–100) | 213 (99.5%) | 54/54 | 81 / 0 / 0 / 81 |
| archivecenter_item2704 | quarter | 2023-04-01 – 2023-06-30 | 117 (117) | 101 (82–100) | 115 (98.3%) | — | 23 / 0 / 0 / 23 |
| archivecenter_item2618 | quarter | 2022-10-01 – 2022-12-31 | 125 (125) | 96 (81–100) | 123 (98.4%) | — | 36 / 0 / 3 / 33 |
| archivecenter_item2537 | quarter | 2022-07-01 – 2022-09-30 | 213 (213) | 171 (63–100) | 212 (99.5%) | — | 59 / 0 / 0 / 56 |
| archivecenter_item2515 | quarter | 2022-04-01 – 2022-06-30 | 154 (154) | 132 (80–100) | 151 (98.0%) | 5/5 | 28 / 0 / 0 / 27 |
| archivecenter_item2445 | quarter | 2022-01-01 – 2022-03-31 | 259 (259) | 227 (72–100) | 255 (98.5%) | — | 25 / 0 / 0 / 24 |
| archivecenter_item2329 | half year | 2021-03-01 – 2021-08-31 | 401 (401) | 374 (56–100) | 389 (97.0%) | — | 16 / 0 / 0 / 16 |
| archivecenter_item2234 | half year | 2020-10-01 – 2021-03-31 | 420 (420) | 412 (78–100) | 414 (98.6%) | — | 28 / 0 / 0 / 25 |
| archivecenter_item2148 | half year | 2020-03-31 – 2020-10-01 | 381 (381) | 367 (71–100) | 378 (99.2%) | — | 66 / 6 / 0 / 58 |
| archivecenter_item2042 | year to date | 2019-01-01 – 2019-12-02 | 712 (712) | 645 (64–100) | 682 (95.8%) | — | 112 / 5 / 1 / 99 |
| archivecenter_item2032 | year to date | 2019-01-01 – 2019-10-31 | 658 (658) | 591 (64–100) | 626 (95.1%) | — | 98 / 3 / 1 / 90 |
| archivecenter_item2017 | year to date | 2019-01-01 – 2019-09-30 | 593 (593) | 533 (64–100) | 561 (94.6%) | — | 88 / 3 / 1 / 80 |
| archivecenter_item1976 | year to date | 2019-01-01 – 2019-06-28 | 344 (344) | 329 (64–100) | 330 (95.9%) | — | 66 / 2 / 0 / 57 |
| archivecenter_item1735 | latest score per establishment | none (latest score per establishment, 2015–Nov 2017) | 479 (—) | 426 (68–100) | 136 (44.3% of the 307 lines dated after EnerGov's first inspection) | — | n/a |
| archivecenter_item1154 | latest score per establishment | none (latest score per establishment, 2011–Mar 2015) | 433 (—) | 381 (55–100) | 0 (all before EnerGov) | — | n/a |

"—" in the comment column: no inspection in that report has a comment score
(inspectors did not write scores into comments before 2022). The four 2019
reports are cumulative, so the same inspection appears in up to four of them
(the scores never conflict; `validation.json` → `pdf_inspections_in_several_reports`).
In the last column, the first number counts every non-cancelled EnerGov
"Followup Reinspection" record dated in the report's period, and the last
counts those with no line in the report. Records in the two middle columns
are left out of the last one; see "Match methods" for what they mean.
No reports were found covering Dec 2017–Dec 2018, Dec 2019–Mar 2020,
Sep–Dec 2021, Jan–Mar 2023, Oct 2023–Mar 2024, Jul 2024–Jun 2025, or anything
after September 2025.

## What is public and what is not

Public (anonymous, no login) and in this dataset:

* every inspection in the food-related types, with type, status, requested /
  scheduled / actual date, address, parcel, inspector, free-text comment, and
  a link to the business or operational permit it belongs to;
* businesses' permits and licences: name, permit type, status, dates, address;
* the score reports listed above.

Not public (the portal answers "You must be a contact on this record to see
this information", or has no report configured; checked during the
reconnaissance on Sept. 28, 2026), and **not** in this dataset:

* the inspection score field, the 47-item checklist, violations, and
  inspector narratives per violation;
* inspection report PDFs (the portal's print button has no report template:
  `/api/report/inspectionReportName` returns nothing) and attachments;
* custom fields, holds, and any record of permit suspensions or closures.

**Item-level violations and the inspection report PDFs would require a
public-information request** to the city (Open Records portal,
<https://allentx.justfoia.com/Forms/Launch/19ef590b-eb71-420a-a4ab-612c72e9a460>),
for example an EnerGov export of all food, day-care, truck and "OP" health
inspections since 2017 with the score, checklist results, violation text and
points, report PDFs, suspensions/closures, and the missing score reports.
The city's Food Safety page itself points readers to that portal for reports.

## How Allen scores inspections

From the city's FAQ ("What do the scores mean?" / "What happens if an
establishment fails an inspection?",
<https://www.cityofallen.org/departments/community_enhancement/health_and_food_safety/food_safety.php>):

* Allen uses the Texas DSHS inspection form with 47 items. Each item carries
  3, 2 or 1 points. **Establishments start at 100 and points are deducted**;
  100 is the best possible score. (This is the opposite direction from
  Frisco and McKinney, which publish demerits.)
* "Only routine health inspections receive a score." Follow-up and courtesy
  inspections are not scored.
* **69 or below** requires immediate corrective action on priority items and
  action on all other violations within 48 hours; the inspector "may close a
  food establishment at his / her discretion", and some violations require
  immediate closure regardless of score. The original score stands until the
  next routine inspection.
* The ordinance (Ch. 6 Art. VIII) has no numeric score or failing threshold;
  the 69 line exists only in the FAQ.

For comparison with demerit-scored cities the tables include
`*_demerits_inferred` = 100 − score. That conversion is **our inference** from
the FAQ's description (start at 100, deduct the item's points); it is not a
figure the city publishes.

## Where the scores come from

* **`comment_score`** (inspections.csv): the leading integer of the
  inspector's comment, kept only when it has 2 or 3 digits, is 50–100, and
  stands alone or is followed by punctuation ("91", "84. Follow up required",
  "72 - NOV issued") or by an inspection word ("82 Follow up required").
  Comments that start with other numbers ("3 Sink Compartment Fixed",
  "15 days NOV ...", a date) are not read as scores; `comment_kind` says which
  case applies. Coverage of completed inspections by year: 0 in 2017–2021,
  8 in 2022, 165 in 2023 (16%), 502 in 2024 (43%), 403 in 2025 (40%), 69 in
  2026 (24%). No comment in the food-truck, complaint or 2026 "OP" types
  carries a score. That these numbers are scores is supported by the 260 of
  260 exact matches with report scores, but it is still an inference about
  how inspectors used the field.
* **`pdf_score`** (inspections.csv) / **`score`** (pdf_scores.csv): the number
  printed in a report's RATING column. Non-numeric ratings ("Follow up -
  Pass", "Pass", "Fail", "Complaint Insp: Unverified") are kept in
  `rating_text` and summarised in `result_text`.
* Count inspections, not report lines: 3,058 inspections have a `pdf_score`,
  1,147 a `comment_score`, 3,945 one or both. The 5,964 report lines include
  the repeats of the cumulative 2019 reports and 885 lines that match no
  inspection (776 of them in the two latest-score lists, which reach back
  before EnerGov), besides the ambiguous and low-confidence lines described
  under "Match methods".

**The reports are not complete lists of scored inspections.** In every
report period most EnerGov inspections with status "Followup Reinspection"
have no line in the report (last column of the table above), although those
are routine inspections that were scored: the 248 of them with a comment score
average 83.9 (median 85, range 61–98) against 93.9 (median 95, range 73–100)
for the 899 "Pass" inspections with one. The report usually shows only the
later follow-up visit ("Follow-up: Pass"). So the report score distributions
are biased upward and must not be used as the distribution of all routine
scores. All seven comment scores of 69 or below are on "Followup
Reinspection" records. Reading "Followup Reinspection" as "routine
inspection that needs a follow-up visit" is an inference (from the comment
scores and from what happens next: 975 of the 1,197 such records with a known
establishment are followed by another inspection of the same establishment
within 30 days, 742 of those with "Pass"); the city has not documented the
status values.

## Test records

In 2026 city staff tried out the new inspection app on live records. Two
rules flag them (`is_test_record`, `test_record_basis`; the full list is in
`validation.json` → `test_records`):

* **12 inspections of the "Training Food Establishment"** at 305 Century Pkwy
  (Allen City Hall), whose operational permit description reads "For App
  Testing and Training Purposes - Only". Their comments include "Testing
  purposes.", "Testing again....", "This is where we can leave notes and
  comments." and "What happens if I type something in comments under
  details?". They include a Follow-up Reinspection, a "Converted" record and
  pending records, one scheduled for November 2026.
* **2 inspections of a real business**, Tom Thumb #3579 - Bakery:
  FE-108851-2026 (OP - Routine Food Inspection - 120 Days, status Follow-up
  Reinspection, comment "Testing") and FE-108921-2026 (Pass, "Testing the app
  again."). That these are test entries and not real inspections is **our
  inference from the comment text**; the city has not said so.

Flagged records stay in `inspections.csv` so the rows still reconcile with the
portal (`status_counts` in `validation.json` counts them; 
`status_counts_excluding_test_records` does not). They are left out of the
establishment roll-ups, the headline and status counts in this README, the
comment-score statistics, the follow-up analysis and the report matching.
11 of the 14 are completed (Pass, Follow-up Reinspection, Converted); with
them, 8,440 records are completed inspections. Excluding them, the statuses
are: Pass 7,163, Followup Reinspection 1,173,
Cancelled 741, BL - Cancelled 392, Pending 290, Fail 64, Requested 32,
Follow-up Reinspection 25, Converted 2, Failed 2.

## What's in `data/`

| File | One row per | Notes |
|---|---|---|
| `inspections.csv` | EnerGov inspection (9,898, including the 14 test records) | status, dates, inspector, scrubbed comment, `comment_score`, `pdf_score`, establishment and establishment group, links |
| `establishments.csv` | business (EnerGov BusinessId, 1,594) | latest name and permit type, open/closed, permits, inspection roll-ups per BusinessId and per establishment group |
| `pdf_scores.csv` | line of a parsed score report (5,964) | report period, name, address, date, score or result text, matched EnerGov case and match method |
| `validation.json` | — | every check listed under "How the data was checked", and `readme_phrases`, every number-bearing phrase of this README |
| `raw/listing.json.gz` | inspection | search rows exactly as returned, per type, with the portal's TotalFound |
| `raw/details.json.gz` | non-cancelled inspection (8,765) | getById detail; e-mail/phone fields blanked and comments scrubbed (see Privacy) |
| `raw/permits_licenses.json.gz` | permit / licence (5,416) | four searches; licence-holder names, tax id and the free-text description dropped |
| `raw/inspection_types.json.gz`, `raw/inspection_statuses.json.gz` | — | the portal's type and status lists |
| `raw/status_reconciliation.json` | type × status | our row counts vs. the portal's TotalFound per status |
| `raw/fetch_meta.json`, `raw/pdf_manifest.json` | — | what was fetched when; every archive item checked and how it was classified |
| `raw/pdfs/*.pdf` | score report | the 18 PDF files (17 reports + the identical Wayback copy of Apr-Jun 2024) |

Score and demerit columns are written as whole numbers (blank when absent).

### `inspections.csv`

| Column | Meaning |
|---|---|
| `case_id`, `case_number` | EnerGov inspection id and number (FE-…, DCF-…, FT-…, DC-…) |
| `inspection_type` | EnerGov type (EH-Food Establishment, EH-Day Care Facility, EH-Food Truck, OP - Routine Food Inspection - 90/120/180/365 Days, OP - Routine Daycare Inspection - 180 Days, Food / Day Care Complaint Inspection) |
| `status` | EnerGov result: Pass, Followup Reinspection, Fail, Cancelled, BL - Cancelled, Pending, Requested; the 2026 OP types use Pass, Follow-up Reinspection, Failed, Converted |
| `is_test_record`, `test_record_basis` | test / training entry (see "Test records"); leave these rows out of any analysis |
| `inspection_date`, `date_source` | `actual_date` when there is one, otherwise `scheduled_date` |
| `scheduled_date`, `actual_date`, `request_date` | calendar dates in America/Chicago. getById returns UTC timestamps for local midnight (05:00Z/06:00Z); they are converted. Search dates carry no zone and are already local |
| `inspector` | assigned inspector (on the official record; a few are system accounts such as "Health *Pool") |
| `is_reinspection` | EnerGov's IsReinspection flag. It is **false on all 8,765 fetched records**, so it cannot identify follow-ups |
| `comment` | inspector comment, scrubbed (see Privacy) |
| `comment_score`, `comment_kind`, `comment_demerits_inferred` | see "Where the scores come from"; kinds: blank, score_only, score_then_text, result_text ("Follow-up: Pass"), narrative, number_then_words, starts_with_date (and number_over_100 / number_below_50, which do not occur now) |
| `closure_keyword` | keyword screen of the comment for closed / closure / cease / shut down / re-open / suspend / imminent health hazard (43 comments). Not a closure flag: read the comment |
| `pdf_score`, `pdf_rating_text`, `pdf_report_ids` | the matched report line(s) |
| `establishment_id`, `establishment_group_id`, `establishment_name`, `establishment_category`, `establishment_address`, `establishment_address_withheld` | the business (BusinessId) and the establishment group it belongs to, from the permit / licence tables (latest name and permit type); the address is city/state/ZIP only when withheld (see Privacy) |
| `join_method`, `join_note` | how the establishment was found (below) |
| `address`, `address_withheld`, `address_type`, `parcel` | from the inspection record. 1,645 rows carry a "Mailing Address" (often the owner's), not the site; where that looks like a private address only city, state and ZIP are published (see Privacy) |
| `link_type`, `link_number`, `link_id` | what the inspection is attached to in EnerGov (Business or Operational Permit) |
| `inspection_module`, `detail_status` | EnerGov module (Inspection / HealthInspection); whether the detail was fetched |
| `source_url`, `api_url` | the portal page and the JSON record |

`join_method`: `business_id_op_permit` (inspection's LinkId = a business's
BusinessId in the food / day-care operational permits; 7,952),
`op_permit_case_id` (2026 OP inspections: LinkId = the permit's CaseId, and
LinkNumber = its permit number in all 392 cases; 392), `business_id_license`
(the business is only in the legacy licence tables; 349),
`op_permit_number` (OP inspection whose LinkId is not in the permit table but
whose permit number is; 3), `address_fallback` (no usable link: exactly one
permit of the same kind, food or day care, was in force at the same street
number, street and unit on the date; 385, of which 345 are cancelled
inspections with no detail — treat these names as likely, not certain),
`unmatched` (817, of which 788 are cancelled inspections with no detail). Of
the 8,765 inspections with a detail, 8,696 (99.2%) are joined through their
EnerGov link, 40 by address and 29 not at all: 15 day-care inspections (10 of
them from 2017–2018) and 7 food-establishment inspections linked to business
records that have no public permit or licence, and 7 inspections from 2026
linked to operational permits that the public permit search does not return.

### `establishments.csv`

One row per EnerGov BusinessId: `establishment_id` (BusinessId, lower case),
`establishment_group_id`, `group_n_business_ids`, `name` (company name on
the latest permit), `dba`, `other_names`, `category` / `categories` (permit
types), `kind` (food, day care, special event), `mobile_vendor` (latest
permit is a truck, mobile, catering, temporary, special-event or seasonal
permit), `is_test_establishment`, `sources`, `business_status` (Open / Closed
in EnerGov: the business or permit record, not a health closure),
`opened_date`, `closed_date`, `address`, `address_withheld`, `parcel`, permit
counts and numbers, `latest_permit_url`, and roll-ups of its inspections,
test records left out: `n_inspections`, `n_inspections_not_cancelled`,
`first_inspection`, `last_inspection`, `n_followup_reinspection`, `n_fail`,
`n_comment_scores`, `latest_comment_score`, `latest_comment_score_date`; the
same roll-ups over the whole establishment group (`group_…`); and
`n_test_records`.

**Use the group roll-ups when counting establishments or their
inspections.** 1,594 counts BusinessIds, not places. `establishment_group_id`
merges BusinessIds whose latest names normalise to the same words (case,
punctuation, "LLC", "Inc", "#" and the like ignored) and whose latest permits
share the street number, street and unit, or the parcel and unit; the id is
the smallest BusinessId in the group. Examples: Braum's Ice Cream #278
(two BusinessIds with 9 and 16 inspections), Allen Eagle Food Mart (3 and 15),
Chevron Mart at 105 S Custer Rd (5, 5 and 4), 7-Eleven #26264B / 7-Eleven
26264B. Names that normalise differently ("Hyatt Place Allen" and "Hyatt
Place Allen/Dallas") are not merged, so a few splits remain.

### `pdf_scores.csv`

`report_id`, `report_period_start/end`, `page`, `row_in_report`,
`facility_name`, `address` (as printed, or city and ZIP only when withheld;
`street`, `city`, `zip` split from it), `address_withheld`, `date`, `score`,
`demerits_inferred`, `rating_text`, `result_text`, `matched_case_id`,
`energov_case_number`, `match_method`, `match_name_similarity`, the matched
inspection's type, status, date, establishment name, comment score,
`score_agrees_with_comment`, `parse_note`, `report_source_url`.

Match methods, best first, one report line to one inspection within a report
(test records are never candidates):

1. same date + street number + name;
2. same date + street number (with "+ unit" when the suite number also
   matches, and "; names share no word" when the names have no word in
   common). At a multi-tenant address, where several establishments were
   inspected that day at that street number, a candidate whose name shares no
   word with the line and whose unit does not match is dropped; a line left
   with no candidate for that reason is `unmatched: low-confidence …`
   (7 lines, e.g. "Premium Level Pantry @ AEC" at the event centre). 42
   tier-2 lines have names with no word in common: mostly spelling variants
   ("Schlotsky's" / "Schlotzsky's", "Yummy Burgers & BBQ" / "YUMMIES", "Race
   Trac" / "RaceTrac") and day cares whose business has since changed its
   name; 12 of the 42 also match the unit. Two single-tenant cases, "PPS" →
   Chipotle and "The Retail Connection" → Jamba Juice, match the address down
   to the unit and are the only inspection there that day, so they may be the
   same place under another name (inference). Check any tier-2 line you rely
   on;
3. same date + name (the report shows another address, e.g. a head office);
4. date within 3 days + street number + name.

The street number may match the inspection's address or its establishment's
permit address (the full addresses are used for matching even where the
tables publish only city and ZIP); names are compared as word sets, accents
ignored, and a name made only of common words ("Allen Cafe") keeps them.

**Same-day pairs.** When a line's best candidates are all records of one
establishment on one day (usually a "Followup Reinspection" and a "Pass"),
a numeric line is given to the Followup Reinspection record and a "Follow up
- Pass" line to the Pass record. This is an inference: EnerGov has 45
same-day Followup Reinspection + Pass pairs; 3 of them have a comment score,
and in all 3 it is on the Followup Reinspection record, none on the Pass
record (`validation.json` → `same_day_pair_evidence`). Some 2019 reports print a pair as
two identical lines; those lines are assigned one each, in case-number order.
46 lines are matched this way (the method says "same-day pair" or "one line
per same-day record"). 18 lines stay `ambiguous: n same-day records of one
establishment` (e.g. two Pass records and one numeric line); their
Followup Reinspection records are counted in the "unresolved same-day tie"
column above, not as missing. 11 other lines are `ambiguous (n equally good
inspections)` (e.g. several departments of one store inspected the same day).

## How the data was collected

1. `fetch.py` reads the portal's inspection-type list and keeps the 30
   food-related types (EH-Food Establishment, EH-Food Truck, EH-Day Care
   Facility, EH-Convenience Store, every "OP - …" food, day-care, mobile,
   temporary and converted type, "zzOP - Routine Food Inspection V2", Food
   Complaint and Day Care Complaint Inspection); 20 of them have no records.
   Excluded on purpose: EH-Health - Final (construction final inspections
   tied to building permits), EH-Request Initial Inspection (requests), pools,
   apartments and liquid waste. Each type is paged in full with no status or
   date filter (100 rows per page, sorted by inspection number), which also
   picks up the 297 records with no scheduled date (248 of them
   EH-Food Establishment).
2. For every status that appears in a type, the portal is asked for its
   TotalFound with that status filter, and the answer is compared with our rows.
3. Food and day-care operational permits (module 12) and food and
   special-event food licences (module 10) are paged the same way.
4. getById is called for every inspection that is not Cancelled or
   BL - Cancelled (8,765 calls). Three calls failed once (two HTTP 401
   responses and one "StatusCode 204" body) and succeeded when retried.
5. `fetch_pdfs.py` downloads the two city-hosted reports and searches the
   Wayback Machine: it resolves every archived capture of the old "Health
   Scores" category's "most recent item" link (`Archive.aspx?AMID=74&Type=Recent`,
   which pointed to items 1154, 1735, 2042, 2148, 2329, 2515, 2618 and 2755
   over the years), reads the 2014 capture of the category page (monthly
   reports from Dec 2010 to Jul 2014, none of which are archived), and then
   downloads every other archived Archive Center item numbered 1800 or above
   whose capture is 20 KB–1 MB and classifies it by its first-page text.
   Of 323 items checked, 15 are score reports; 248 are other city documents;
   42 (the 2010–2014 monthly reports) are not archived; 18 are captures the
   archive truncated at 1 MiB (14 of them readable enough to show they are
   budgets, minutes, agendas and similar; 4 unclassified, all from files over
   1 MiB, far larger than any score report). The 65 archived items numbered
   1800+ outside the 20 KB–1 MB window, and older items not linked from the
   category, were not checked.
6. `parse_pdfs.py` parses each report (see below); `build.py` joins everything.

Politeness: robots.txt is read and obeyed for every host (the EnerGov host and
web.archive.org have none; cms3.revize.com allows only PDF and document files,
which is all that was fetched there); at least 1.15 s between requests to
EnerGov, 1.5 s to the city site and 4 s to the Wayback Machine; exponential
backoff on 403/429/5xx and dropped connections; every response cached on disk.
Only anonymous, public endpoints were used; no contact or people endpoints.

### Parsing the reports

Three layouts occur: Crystal Reports lists (2020–2025, one line per inspection,
dates m/d/yy), Crystal Reports with the name on its own line above the address
and long dates (2019–2021), and Excel exports (2015, 2017, and the Apr-Jun 2024
print-to-PDF). Words are placed in columns by position and grouped into rows by
each row's date, so wrapped cells ("PASS - / FOLLOW UP") stay with their row.
Every report that prints "TOTALS - ALL INSPECTIONS n" (14 of the 17) parsed to
exactly n lines. Item 2329 (Mar–Aug 2021) looked like a scan, but its fonts
map every character to the Unicode private-use area (U+F0xx = character xx);
the parser reverses that mapping. No OCR was used and no report was left unparsed. A few
lines in the source PDFs are garbled (overprinted text, e.g.
"0P1iz zEa. Main St") or have a blank rating; they are kept as printed.

## How the data was checked (`validation.json`)

| Check | Result |
|---|---|
| Rows per type vs. the portal's TotalFound | equal for all 30 types (9,898 rows) |
| Rows per type and status vs. the portal's TotalFound for that status | equal for every pair; e.g. EH-Food Establishment: Pass 6,351, Followup Reinspection 1,109, Cancelled 602, BL - Cancelled 392, Fail 40, Pending 2, Requested 1, and one "Pass - NONE" record (the one earlier counts could not place) |
| Permit / licence rows vs. TotalFound | 4,088, 361, 746, 221: all equal |
| Details fetched | 8,765 of 8,765 |
| Search scheduled date vs. getById scheduled date (converted to Chicago time) | equal for 8,634 of 8,749; the 115 others are Pending/Requested records the portal re-dated between the two reads |
| Joins | 99.2% of fetched inspections through their EnerGov link; OP LinkNumber = permit number in 392/392 |
| Report lines vs. printed totals | equal for all 14 reports that print a total |
| Report lines matched to EnerGov | 94.6–99.5% per report for the 15 reports from 2019 on |
| Report score vs. comment score | 260 of 260 equal |
| Followup Reinspection records missing from each report | table above |
| Test records | 14 flagged, listed in `test_records` |
| Contact details left in any text column of the three CSVs | none |
| README numbers | `readme_phrases` holds every number-bearing phrase of this README, generated from the data; `build.py` prints any phrase the README does not contain verbatim (all present) |

Dates that depend on "today" (future-dated rows) are counted against the
data's as-of date (`as_of`: the Chicago date of the last response download,
2026-09-28), not the day `build.py` runs, so a rebuild from the same raw files
gives the same `validation.json` apart from `generated_at`.

## Privacy

* **Contact details.** E-mail addresses and phone numbers are removed from
  free-text fields, and e-mail/phone fields blanked, before any response is
  written to disk (44 replacements, including inspector e-mail addresses in
  2026 comments).
* **Names in comments.** The names and certificate numbers of certified food
  managers, owners, persons in charge and other staff are removed from
  comments by `common.CommentScrubber` (names after or before words such as
  CFM, owner, manager, PIC, "spoke with"; "Name, CFM, provider, Cert# …"
  lines; certificate numbers after CFM / certificate / licence), and any full
  name it finds is removed wherever else it appears: 34 names and 22
  certificate numbers in 32 comments. Inspector names are part of the
  official record and are kept. The scrubber is rule-based: the comments it
  changed and the remaining rare capitalised words were reviewed by hand, but
  a name written without any of those cues could remain.
* **Permit records.** Licence-holder personal names and tax ids are dropped,
  and so is the free-text permit description, whose staff notes name private
  individuals ("closed per …", "spoke to …") and once held a driver's-licence
  number; the only fact kept from it is whether it marks the city's testing
  permit. Business names are kept as the city publishes them; some mobile or
  special-event vendors are registered under a person's name.
* **Probable home addresses.** Food trucks, caterers and other mobile or
  temporary vendors often register a home address, and many inspection rows
  carry the owner's mailing address instead of a site. For these the tables
  publish **only city, state and ZIP** (and no parcel), with an
  `address_withheld` / `establishment_address_withheld` flag. The rule
  (`build.withhold_address`, `propagate_withholding`):
  1. a mailing address, and any address of a mobile or temporary vendor
     (truck, mobile, catering, construction-site, temporary, special-event or
     seasonal permit, or an EH-Food Truck inspection), is withheld unless it
     is a street address in Allen that is on a fixed establishment's permit
     (for schools, day cares and restaurants EnerGov often types the premises
     as the "mailing address") or that several businesses use as their permit
     site (an event venue, park or shopping centre);
  2. a street address withheld on one record is withheld on every inspection,
     permit and establishment row, and on every report line that prints it;
  3. a report line printing another town's address is withheld unless that
     address is kept in full in EnerGov (e.g. an Allen ISD school in Parker).

  That these are homes is an inference from the address type, street and
  location; some withheld addresses are offices, commissaries or one-off event
  sites. 551 inspection rows (533 of them mailing addresses; 161 distinct
  addresses), 282 establishments and 112 report lines are affected, and
  `validation.json` confirms that none of the withheld street addresses
  appears anywhere in the three CSVs. Matching still uses the full addresses
  internally. The full addresses remain in `data/raw/` and in the city's own
  PDFs, which are not committed (see below).
* **Code and README.** No real names are used as examples in the code.
  `privacy_check.py` learns every name the scrubbers would remove from the
  unscrubbed text in the local HTTP cache and searches the code, README,
  tables and raw files for them (printing only masked forms); run it before
  every commit.

## Caveats for reporting

* **No public score for most inspections.** Scores exist only for 3,945
  inspections: none dated 2018, none from the 2026 "OP" inspection types, and
  before 2019 only 103 (inspections dated 2017 that appear in the 2015–2017
  latest-score list, item 1735). Do not compute score trends without
  accounting for which inspections have scores and why (report coverage,
  report omissions, inspector habits in the comment field).
* **Report scores are biased upward** (they leave out most routine
  inspections that needed a follow-up); see above.
* **Test records.** Leave out rows with `is_test_record` = True (14 rows,
  two of them on a real Tom Thumb bakery).
* **Businesses are not establishments.** Count places with
  `establishment_group_id`, not `establishment_id`.
* **Status meanings are undocumented.** "Followup Reinspection" appears to
  mean "this inspection requires a follow-up" (inference); the follow-up visit
  itself is usually recorded as another "EH-Food Establishment" inspection with
  status Pass and a comment like "Follow-up: Pass". "Fail" is rare (64; 22 of
  them food-truck inspections).
* **Closures.** There is no public record of health closures or permit
  suspensions. EnerGov "Closed" statuses on businesses and permits describe
  the business or permit record (e.g. an expired event permit or a change of
  owner), not a health-ordered closure. `closure_keyword` flags 43 comments
  mentioning a closure, ceasing operations or reopening; several describe
  voluntary or ordered closures, others do not. Read each one. The only
  failing-score rule (69 or below) comes from the FAQ, and the FAQ leaves
  closure to the inspector's discretion.
* **Two systems in 2026.** The city moved to new "OP - Routine … Inspection"
  types linked to operational permits (inspection module HealthInspection)
  while EH-Food Establishment inspections continued; 2026 has both. The 290
  Pending records include 37 inspections scheduled into 2027.
* **Names are the latest ones.** `establishment_name` is the business name on
  the latest permit, which can differ from the name at the time of an older
  inspection (e.g. a report line "Kids R Kids Preschool-East Allen" matched a
  business now named "Rise Academy").
* **Day cares and schools are included** (EH-Day Care Facility; the city
  scores their kitchens), as are grocery departments, concession stands,
  trucks and temporary vendors. Filter on `inspection_type` and
  `establishment_category` for restaurant-only work.
* **Dates.** `inspection_date` is the actual date where the portal has one.
  193 completed inspections (196 rows in all) have an actual date different
  from the scheduled date. Report lines were matched on the actual date.
* The EnerGov portal is a live system: counts will drift as inspections are
  scheduled, performed or re-dated.

## Known gaps

* No inspection data before May 2017 in EnerGov; scores for 2011–2017 exist
  only as the "latest score per establishment" lists (items 1154 and 1735).
* No score reports found covering Dec 2017–Dec 2018, Dec 2019–Mar 2020,
  Sep–Dec 2021, Jan–Mar 2023, Oct 2023–Mar 2024, Jul 2024–Jun 2025, or after
  September 2025; the city deleted older quarterly files from its site. The
  2010–2014 monthly reports are listed in a 2014 archive capture but were
  never archived.
* 4 archive captures were truncated by the archive and could not be
  classified (all from files over 1 MiB).
* The item-level checklist, violation narratives, the score field, report
  PDFs, attachments and closure/suspension records are not public (see above).

## Re-running

```bash
cd allen_inspections
python -m venv .venv && . .venv/bin/activate
pip install requests pandas pdfplumber
export ALLEN_CACHE=/path/to/cache   # HTTP cache; defaults to ./.cache (not committed)
python fetch.py          # EnerGov: ~9,000 requests, about 3 hours; resumable from the cache
python fetch_pdfs.py     # score reports: ~320 requests to the Wayback Machine, about 25 minutes
python build.py          # rebuilds data/*.csv and validation.json from data/raw/ (no network)
python privacy_check.py  # needs the cache; exit code 1 if a scrubbed name or ID pattern is found
```

`build.py` needs only `data/raw/`; it never contacts the city. The published
tables (`data/*.csv`, `data/validation.json`) are committed; **`data/raw/` is
not**, because the portal rows and the city's PDFs hold the full addresses
that the CSVs publish only as city and ZIP. A fresh clone therefore has the
scripts and the tables but must run `fetch.py` and `fetch_pdfs.py` before
`build.py` can rebuild them.

## AI assistance

This dataset, the scripts and this README were produced with the help of an
AI assistant (Claude, by Anthropic), working from a prior reconnaissance and
verification of the city's sources. Every number above comes from the scripts
in this folder and `validation.json`; statements marked as inferences are
interpretations, not facts published by the city. Have a person check any
figure before publication.
