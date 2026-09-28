# Richardson, Texas restaurant inspection scores (archived history)

A score-level history of City of Richardson food-establishment inspections,
built **only from archived copies and public open-data APIs**. The city's own
inspection app is not accessed (see "What is missing and why").

**8,021 inspections of 1,087 permits** from three sources:

* **2,167 inspections, Nov. 10, 2015 to Nov. 6, 2018**, from the city's
  LIVES open-data feed on Socrata (452 businesses).
* **47 inspections from two weeks in 2019** (Apr. 22–26 and June 24–27;
  46 permits), from two Internet Archive (Wayback Machine) captures of the
  city's weekly score listing. These are the only public scores found for the
  stretch between the other two sources.
* **5,807 inspections, May 12, 2021 to Oct. 17, 2025**, from the three
  Wayback Machine captures of the city's own JSON export, `CORScores.json`,
  dated May 13, 2024, June 12, 2024 and Oct. 17, 2025 (886 permits). One
  record is dated Dec. 1, 2025, after the export that holds it, and 2 have no
  usable date.

**Apart from those two weeks, there are no public scores for Nov. 2018 to
May 2021.** There are 917 days between the last LIVES inspection and the
first date in the oldest export. Inside that span there are 166 days with no records before the first 2019 week,
58 days between the two weeks and 684 days after the second. No records fall
in Dec. 2018 through March 2019, in May 2019, or in July 2019 through
April 2021. None of this data has item-level violations. Those exist only on
the city's own report pages, which this project does not scrape.

Scores run from 0 to 100 and **higher is better**, the reverse of the
Frisco and McKinney demerit totals. `demerits_equiv` (= 100 − score) puts
them on the same footing. 7,429 inspections are scored and 592 have a
blank score. The mean score is 91.96, the median 94, and the range 56 to 100.
Only 2 scores fall below 60: 56 (LIVES, 2018) and 58 (Wayback export, 2023).

Built Sept. 28, 2026. All figures in this README come from `build.py`'s
output (`data/validation.json`).

## What's in `data/`

| File | One row per | Notes |
|---|---|---|
| `inspections.csv` | report key (Wayback export or weekly listing) or LIVES inspection | score, demerit equivalent, source and capture provenance, re-keying flags |
| `establishments.csv` | permit | names and addresses seen, first/last inspection, counts by source, mean / min / max / latest score, which captures list the permit |
| `validation.json` | — | integrity checks, counts per source and year, capture overlaps, re-keyed / vanished records, score distribution, gaps, the scoring text, the ordinance check, Wayback leads not mined |
| `raw/wayback/CORScores_<ts>.json` | capture | the three Wayback captures, byte-for-byte as archived |
| `raw/wayback/cdx_*.json` | CDX query | raw Wayback CDX API answers (capture lists) |
| `raw/wayback/scores_page_text.json` | capture of the scores page | the city's description of the scale, from 7 captures of `webScores.html` (only that paragraph is kept; see "Privacy") |
| `raw/wayback/weekly_listings_in_gap.json` | capture of a weekly listing | the two 2019 weekly listings: capture provenance and the table rows only (name, address, score, report link) |
| `raw/wayback/healthtrak_*_inventory.json` | archived URL | CDX listings of other archived HealthTrak pages (only the two 2019 listings were downloaded) |
| `raw/socrata/yf9v-pthu.json` | LIVES inspection | the whole LIVES dataset, minus the phone column |
| `raw/socrata/tqfg-ya7z.json` | — | the LIVES feed-information table, minus its contact e-mail |
| `raw/municode_sec_10-126.json` | — | text of Richardson Code sec. 10-126 as published on Municode |
| `raw/fetch_meta.json` | — | manifest: every request URL, fetch time, robots.txt results, and the sha256 of every raw file |

## Sources and provenance

### 1. Wayback Machine captures of the city's JSON export

The city's HealthTrak app publishes a one-file export,
`http://discovery.cor.gov/public/health/healthtrak.nsf/CORScores.json`, with
each listed establishment's name, address(es), and every inspection's permit
number, date, score and report link. **The copies used here were captured by
the Internet Archive, not by this project.** `fetch.py` asked the Wayback
CDX API for this URL four ways: without a scheme, as `http://`, as `https://`,
and as a prefix match that would also catch query-string or case variants.
All four returned the same three captures, all HTTP 200. A later, separate
check found nothing more. It used an exact-URL query, a wider `…/cor`
prefix, a domain-wide query over all of `cor.gov` and `cor.net` filtered to
any URL containing `corscores`, and the Wayback timemap API. A domain-wide
search of `discovery.cor.gov` for JSON captures found no other export of the
scores.

| Capture timestamp (UTC) | Archived URL | Export `created` | Score rows | Unique report keys | Permits | Earliest / latest date |
|---|---|---|---|---|---|---|
| 20240513021424 | `http://discovery.cor.gov/public/health/healthtrak.nsf/CORScores.json` | 05/12/2024 | 3,946 | 3,942 | 767 | 2021-05-12 / 2024-05-10 |
| 20240612124719 | `https://discovery.cor.gov/public/health/healthtrak.nsf/CORScores.json` | 06/12/2024 | 3,961 | 3,957 | 764 | 2021-06-15 / 2024-06-11 |
| 20251017180715 | `https://discovery.cor.gov/public/health/healthtrak.nsf/CORScores.json` | 10/17/2025 | 3,732 | 3,729 | 767 | 2022-10-15 / 2025-12-01 |

Each capture was downloaded raw from
`https://web.archive.org/web/<timestamp>id_/<archived URL>` (for example
<https://web.archive.org/web/20240513021424id_/http://discovery.cor.gov/public/health/healthtrak.nsf/CORScores.json>).
Its SHA-1 matched the digest the CDX API reports for that capture, so the
files are exactly what the archive holds. Their sha256 values are in
`raw/fetch_meta.json` and are re-checked by every build. Each row of
`inspections.csv` names the capture it was first seen in (`source`,
`source_url`) and every capture that holds it (`captures`).

Each export reaches back about three years: 1,093 to 1,098 days before its
export date. So the window rolls forward and older inspections drop out.
Combining the captures recovers 2021-05-12 onward.

### 2. Socrata LIVES open data (2015–2018)

The city published inspection scores in the LIVES format (the standard Yelp
used) on `lives.data.socrata.com`. Dataset `yf9v-pthu`, "City of Richardson -
LIVES Standard Inspection Data", was last modified Apr. 23, 2019 (per the
API's `Last-Modified` header) and holds 2,167 inspections, 2015-11-10 to
2018-11-06. `fetch.py`
pulled every row through the SODA `/resource` API, paged with
`$limit`/`$offset` in `:id` order, and checked the total against the API's
`count(*)`. The city's feed is a single flat table of businesses and their
inspections. It has no violations table and no violation columns. The feed's
own information table (`tqfg-ya7z`) gives feed version 0.4.2 and scoring
method "graded". LIVES rows link to
`https://lives.data.socrata.com/resource/yf9v-pthu.json?inspection_id=<id>`.
Two pairs of LIVES inspections share a permit, date and score (Alamo
Drafthouse, 2018-04-12, and McDonald's, 2018-08-21). Each pair has two
distinct `inspection_id`s, so both rows are kept.

LIVES `business_id` is the same number as the HealthTrak permit: 260 of the
452 LIVES businesses appear as permits in the Wayback exports, and for 251
of those the first five letters and digits of the names agree. So histories
can be joined by `permit`.

### 3. Two archived weekly listings from 2019 (inside the gap)

The city's HealthTrak app also had a weekly listing,
`https://discovery.cor.gov/public/health/healthtrak.nsf/WebScores?openview`,
showing each inspection of the past week with name, address, score and a
report link. The Internet Archive captured it twice between the end of the
LIVES feed and the start of the oldest export. `fetch.py` downloads every
HTTP 200 capture of that page dated inside that span, which comes to exactly
these two:

| Capture | Week ending (as printed) | Rows | Report keys | Inspection dates |
|---|---|---|---|---|
| [20190504095141](https://web.archive.org/web/20190504095141id_/https://discovery.cor.gov/public/health/healthtrak.nsf/WebScores?openview) | 04/27/2019 | 34 | 33 | 2019-04-22 to 2019-04-26 |
| [20190630073245](https://web.archive.org/web/20190630073245id_/https://discovery.cor.gov/public/health/healthtrak.nsf/WebScores?openview) | 06/29/2019 | 14 | 14 | 2019-06-24 to 2019-06-27 |

Each row links to `webScoresForReadonly/<permit>~<score>~<MM/DD/YYYY>`, the
same key format the exports use, so the permit and date come from the key.
The listing itself prints no date. In all 48 rows the printed score equals
the score in the key. One key (Bukri's Taste of Ethiopia,
11430~85~04/24/2019) is listed twice, so the 48 rows hold 47 inspections at
46 permits. The permit reading holds up: 28 of the 46 permits are also in the
exports and 20 in LIVES, and all 38 rows at such permits agree on the name
(first five letters and digits). Each capture's SHA-1 matches its CDX
digest. The pages carry city contact details, so only the table rows are
stored (`raw/wayback/weekly_listings_in_gap.json`). These rows have
`source = wayback_listing:<capture timestamp>`.

### 4. The city's scoring text

The city's public "Restaurant Scores" page,
`http://discovery.cor.gov/public/health/healthtrak.nsf/webScores.html`, says:

> Scores are based on a scale of zero to 100. A score of 90 to 100 is
> excellent; 80 to 89 is good; 70 to 79 is acceptable; and 60 to 69 is
> marginal. A score below 60 may be grounds for the closing of an
> establishment or other enforcement action.

This text was first read in the project's reconnaissance capture of that
page (Sept. 28, 2026; see the note under "What is missing and why"). The quote
above comes from the Wayback Machine, where it is identical in all 7 captures
of the page from May 13, 2024 to Aug. 13, 2026 (for example
<https://web.archive.org/web/20260813084931id_/http://discovery.cor.gov/public/health/healthtrak.nsf/webScores.html>;
all listed in `raw/wayback/scores_page_text.json`). Both 2019 weekly
listings print the same paragraph (`validation.json` → `scoring_text`).
`score_band` in `inspections.csv` uses these bands.

### 5. The ordinance: Richardson Code sec. 10-126

[Sec. 10-126, "Rules adopted; compliance procedures"](https://library.municode.com/tx/richardson/codes/code_of_ordinances?nodeId=PTIICOOR_CH10HEHUSE_ARTVFOSESA_S10-126RUADCOPR)
(Code of Ordinances, Supp. No. 35, Update 2, codified through Ordinance
No. 4590 of June 15, 2026; text in `raw/municode_sec_10-126.json`) adopts the
Texas Food Establishment Rules (25 TAC ch. 228) and makes the city Health
Department the regulatory authority. It **sets no score threshold.** The
"below 60" language appears only on the city's HealthTrak score pages. The closure powers are
permit-based:

* (d)(1): the Health Department "may without warning, notice, or hearing
  suspend any permit … if the operation of the food establishment otherwise
  constitutes a substantial hazard to public health … When a permit is
  suspended, food operations shall immediately cease."
* (e): revocation, after an opportunity for a hearing, "for serious or
  repeated violations".
* (i)(1)–(2): missing correction deadlines "may result in cessation of food
  operations", and an establishment required to cease "shall not resume
  operations until such time as a reinspection determines that conditions
  responsible for the requirement to cease operations no longer exists."

## How the captures were combined

`build.py` reads every capture and keys each row by the city's **report key**,
`<permit>~<score>~<MM/DD/YYYY>` (the score is empty for unscored visits). This
key is the last part of each report link, and it matches the permit, score
and date fields in every row. One row is written per key, with the first and
last capture it appears in and a `presence` string (e.g. `110` = in the first
two captures, not the third).

Consecutive captures are then compared. A key that is present in one capture
and gone from the next is classified as:

| `later_status` | Meaning | Records |
|---|---|---|
| `in_latest_capture` | still in the Oct. 17, 2025 capture | 3,729 |
| `aged_out` | older than anything in the next capture (the rolling window) | 1,730 |
| `permit_not_listed` | the whole permit is gone from the next export | 336 |
| `vanished` | permit still listed, date inside the next window, record gone | 12 |
| `rekeyed` | the same inspection reappears under a different key | 1 |

**Re-keying.** The city's report key changes whenever the permit, score or
date is edited. `build.py` pairs a vanished key with a key that is new in the
next capture and dated before the previous export. It pairs on: same permit
and date with a different score; same date and score with a different permit
at the same name or address; or same permit and score with a different date.
The rule makes one pair. Between the May and June 2024 captures,
**ZDAR Market's 2024-03-20 inspection (score 88, permit 12312) gave way to
the same date and score under permit 12311 (ZDAR Deli, same address)**. That
reads as a permit-number correction. The two permits are usually inspected on
the same day, though, so a deleted Market record plus a late-entered Deli
record with the same score cannot be ruled out. The old key is kept with
`later_status = rekeyed` and `superseded_by`, and the new key carries
`replaces`. The old key is not counted, so the inspection counts once.

**Vanished records.** All 12 belong to three permits whose whole earlier
history disappeared between the June 2024 and Oct. 2025 exports, even though
each permit is still listed under the same name:

* Royal Sichuan, permit 10592: 6 records, 2022-10-20 to 2024-04-16
* Papa Murphy's Take 'N Bake Pizza, permit 11004: 3 records, 2022-11-28 to 2023-12-07
* Taqueria American Grill, permit 11761: 3 records, 2023-01-05 to 2024-04-19

In the later export these permits show only 2025 inspections. The export is
not a full database dump, so a record missing from it is not proof that the
city deleted it.

**New records.** A key is `new` when it is dated on or after the previous
export's date, and `backfilled` when it is dated before that date but was
absent from the previous export. No keys were backfilled. 15 keys new in the
Oct. 2025 capture are dated on or before the June 2024 export's date. All 15
carry that date, 2024-06-12, so they were most likely entered after that day's
export was made. 2 are `undated`.

Overlap: the two 2024 captures share 3,847 keys. The June 2024 and Oct. 2025
captures share 1,973. Full pairwise figures, with their shared date ranges,
are in `validation.json` → `capture_overlaps`.

The 47 inspections from the 2019 weekly listings share no key with the
exports or LIVES, since their dates fall between them. They are added as
their own rows, and the capture-comparison columns are blank for them.

## Data dictionary

### `inspections.csv`

| Column | Meaning |
|---|---|
| `report_key` | `<permit>~<score>~<MM/DD/YYYY>` as the city publishes it in its report links (exports and 2019 listings); for LIVES rows it is built the same way from the LIVES fields (`key_constructed` = 1). Unique across Wayback rows. Two LIVES keys each appear on two rows (see `rows_with_this_key`) |
| `permit` | HealthTrak permit number (= LIVES `business_id`) |
| `establishment_name`, `address` | As in the latest capture holding the key. LIVES addresses include city, state and ZIP; export and listing addresses are street only |
| `inspection_date` | ISO date; blank when the published date is blank or invalid |
| `date_as_published` | Date string exactly as in the source (for 2019 listing rows, the date part of the report key; the listing prints no date column) |
| `score_100` | City score, 0–100, higher is better; blank when unscored |
| `demerits_equiv` | 100 − `score_100` (blank when unscored), comparable to Frisco / McKinney demerit totals |
| `scored` | 1 if the source has a score, else 0 |
| `score_band` | City band: 90–100 excellent, 80–89 good, 70–79 acceptable, 60–69 marginal, below 60, or unscored |
| `source` | `wayback:<capture timestamp>` (first export capture holding the key), `wayback_listing:<capture timestamp>` (2019 weekly listing) or `socrata:yf9v-pthu` |
| `source_url` | Wayback raw-capture URL for that capture, or the LIVES API URL for the row |
| `first_seen_capture`, `last_seen_capture`, `n_captures`, `captures`, `presence` | Which export captures hold the key (`presence` has one digit per capture, oldest first); blank for listing and LIVES rows |
| `first_seen_status` | `in_first_capture`, `new`, `backfilled`, `rekey_target` or `undated` |
| `later_status` | See the table above |
| `superseded_by`, `replaces`, `rekey_change` | Re-keying links, and which field changed (`permit`, `score` or `date`) |
| `date_flag` | `dated_after_export` (1: 10758~97~12/01/2025 in the Oct. 17, 2025 export), `invalid_date` (1: `08/04/202`), `blank_date` (1) |
| `rows_with_this_key` | 2 when the same key is listed twice in one export capture (5 keys) or 2019 listing (1 key). The city's key cannot tell such listings apart, so those collapse to one row per key and could undercount by up to 6 inspections. For LIVES, 2 keys are shared by two distinct `inspection_id`s each. Those rows are kept, so each of the 2 keys appears on 2 rows marked 2 |
| `names_seen` | All names the key appeared under, when more than one |
| `city_report_url` | The report link as printed in the export, or the 2019 listing's relative link resolved against the page URL. **Not accessed by this project.** The Wayback Machine's only captures of report pages are 404s, and the project's reconnaissance saw older report links return 404 on the city site (see the note under "What is missing and why") |
| `lives_inspection_id` | LIVES `inspection_id` (a Lotus Domino document ID) |

### `establishments.csv`

One row per permit: latest name and address and all of them, first and last
inspection date, counts (all, LIVES, Wayback export, 2019 weekly listing,
scored, unscored), mean / min / max score, latest score and its date, the
export captures that list the permit, `in_latest_capture`, and `in_socrata`.
Re-keyed old keys are not counted. By source: 609 permits appear only in the
exports, 183 only in LIVES and 9 only in the 2019 listings. 249 are in the
exports and LIVES, 17 in the exports and the listings, 9 in the listings and
LIVES, and 11 in all three.

## Score distribution

| | Inspections | Scored | Unscored | Mean | Median | 90–100 | 80–89 | 70–79 | 60–69 | <60 |
|---|---|---|---|---|---|---|---|---|---|---|
| LIVES 2015–2018 | 2,167 | 2,135 | 32 | 92.24 | 94 | 1,526 | 516 | 88 | 4 | 1 |
| Weekly listings 2019 | 47 | 44 | 3 | 95.68 | 97 | 38 | 6 | 0 | 0 | 0 |
| Wayback exports 2021–2025 | 5,807 | 5,250 | 557 | 91.82 | 93 | 3,705 | 1,223 | 289 | 32 | 1 |
| All | 8,021 | 7,429 | 592 | 91.96 | 94 | 5,269 | 1,745 | 377 | 36 | 2 |

The two 2019 weeks are a small sample and say little about 2019 as a whole.

Inspections by year: 2015: 133 (from Nov. 10), 2016: 596, 2017: 675, 2018:
763 (to Nov. 6), 2019: 47 (two weeks), 2020: none, 2021: 718 (from May 12),
2022: 1,293, 2023: 1,310, 2024: 1,349, 2025: 1,135 (1,134 through Oct. 17
plus the one dated Dec. 1), no date: 2.

## What is missing and why

* **The city's own app is not accessed.** HealthTrak at `discovery.cor.gov`
  publishes the current inspections, a month-by-month listing and a full
  report for each inspection. Its `robots.txt` says `User-agent: *` /
  `Disallow: /`, so this project does not request anything from that host.
  `common.py` refuses to (as it does for `inspections.myhealthdepartment.com`).
  Everything here comes from `web.archive.org`, `lives.data.socrata.com`
  (`/resource` API; its robots.txt allows it and asks for a 1-second crawl
  delay, and requests were spaced 1.5 s apart) and `api.municode.com` (no
  robots.txt). Wayback Machine requests were paced 4 s apart, with backoff
  on connection resets and 5xx errors. In the first download run the 4 s was
  counted from the start of the previous request, so after a slow CDX answer
  the next request could follow within about a second. `common.py` now waits
  4 s after each request finishes. *Disclosure:* the project's earlier
  reconnaissance on Sept. 28, 2026 did request pages from `discovery.cor.gov`
  (the scores page, the JSON export, listings and a sample of report pages)
  before the decision to honor its robots.txt. No file in `data/` and no
  figure in this README comes from those requests, and the quote above is
  taken from the Wayback captures. Three caveats below rest on what the
  reconnaissance saw there and are marked "(recon)".
* **Nov. 2018 to May 2021: almost no public scores.** The LIVES feed ends on
  2018-11-06, and the oldest archived export starts on 2021-05-12, with 917
  days in between. The only scores found in between are the 47 from the two archived
  2019 weekly listings. That leaves 166, 58 and 684 days with no records, and
  no records in Dec. 2018–March 2019, May 2019 or July 2019–April 2021
  (22 months).
* **After Oct. 17, 2025**, inspections exist only in the city's live app.
* **Records before about Sept. 2023, and all item-level details, require a
  public-information request.** Each export covers about three years. By that
  rule the city's app, as of late Sept. 2026, reaches back only to about
  Sept. 2023. Its report pages for older inspections appear to be gone. The
  Wayback Machine holds 7 captures of report pages, and all 7 are 404s, and
  older report links returned 404 on the city site (recon). The 47-item
  inspection form, with demerits per item, inspector remarks and follow-up
  and closure notes, is published only on those city pages. The pre-window
  reports, the Nov. 2018 to May 2021 scores, and closure or suspension
  records can be requested under the Texas Public Information Act through
  the city's GovQA portal,
  <https://richardsontx.govqa.us/WEBAPP/_rs/supporthome.aspx>.
* **Only permits listed at export time.** Each export carries only the
  permits it currently lists. An inspection of a business whose permit was
  dropped before a capture was made is missing: for example, permits closed
  before May 2024 that were inspected after May 2021, or permits dropped
  between June 2024 and Oct. 2025. Between the two 2024 captures 9 permits
  stopped being listed, and between June 2024 and Oct. 2025 110 did. Treat
  the 2021–2025 counts as a lower bound, not a census. They are biased toward
  businesses that survived to a capture date.
* **Three snapshots.** An inspection that was entered and then removed or
  re-keyed between captures leaves no trace. The June 2024 to Oct. 2025
  stretch rests on one capture.
* **Unscored visits.** A blank score means the city recorded the visit
  without scoring it. What such a visit was is written only on the city's
  report pages. The sampled ones were follow-ups, visits when the business was
  closed, and permit suspensions (recon).
* **Scale assumption.** `demerits_equiv` assumes the score is 100 minus the
  demerits on the city's Texas-style 47-item form. This held on the sampled
  report pages (recon), but this dataset cannot re-check it, because it has no
  item-level data.
* **Leads not mined.** CDX lists 11 other HTTP 200 captures of the weekly
  listing since 2018, dated Aug. 2018, Dec. 2021 (2), May 2022, March 2023,
  June 2024 (4), April 2025 and April 2026. None is inside the gap. Judging
  by the 2019 ones, each shows a single week. Where LIVES or the exports also
  cover that week, a listing could add only inspections those sources miss,
  such as permits an export no longer lists. The April 2026 one was captured
  after the newest export. They were not downloaded. CDX also lists an
  archived 404 report URL whose key, `11348~95~12/09/2019`, names an
  inspection inside the gap. With no page behind it, it is not counted. See
  `validation.json` → `wayback_leads_not_mined`.

## Privacy

The LIVES dataset has a business phone column (`business_phone_number`) and
the feed table a contact e-mail. Both are left out of the API `$select`, so
they are never downloaded. The archived scores pages carry city office
e-mails and phone numbers, and so do the 2019 weekly listings. `fetch.py`
removes them before a page is even cached, and keeps only the scale paragraph
and the listing rows. The first version of the scrubber missed a phone number
written without separators in a `tel:` link on one cached scores page. It now
also catches unseparated numbers and `tel:` links, and pages cached under the
older scrubber are scrubbed again. A scan of every file in `data/` and in the
local `.cache/` finds no e-mail addresses or phone numbers. The exports
themselves hold only establishment names, addresses, permits, dates, scores
and report links.

## How the data was checked

`build.py` writes every check to `validation.json`:

* **Integrity:** the sha256 of all 17 raw files matches `fetch_meta.json`, and
  each capture's SHA-1 matches its CDX digest.
* **Keys:** the report key in every link matches the permit, score and date
  fields in all rows of all three captures. In the 2019 listings the printed
  score equals the key's score in all 48 rows, and names agree with the other
  sources at every shared permit.
* **Completeness:** LIVES rows saved (2,167) equal the API's `count(*)`, and
  the 2,167 `inspection_id`s are distinct.
* **Captures:** duplicate keys, unusable dates and dates after the export date
  are listed for each capture.
* **Cross-check:** a separate script (outside `build.py`) re-derived the union
  of report keys from the raw captures. It got the same 5,808 keys, the same
  capture lists, `demerits_equiv` = 100 − score on every scored row, and the
  same set of LIVES inspection IDs.
* **Independent verification:** a second pass with its own code, not
  importing the project's, did the following:
  * re-derived every count in this README from `data/raw/`
  * compared every row of `inspections.csv` field by field with the raw
    captures, the LIVES rows and a separate parse of the 2019 listings,
    including a 40-row spot check
  * recomputed the capture transitions, re-keying and vanished records
  * re-queried the CDX API and the LIVES `count(*)`

  Nothing disagreed except whitespace, which `build.py` collapses in names
  and addresses. That pass found and fixed the gaps described above: the
  2019 listings, the phone-number scrubber, the request pacing, and the
  README's statement about LIVES duplicate keys.

## Re-running

```bash
cd richardson_inspections
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt      # requests (build.py needs only the standard library)
python fetch.py                      # about 30 requests, a few minutes (the Wayback CDX API is slow)
python build.py
```

Every HTTP answer is cached in `.cache/` (set `RICHARDSON_CACHE` to move it).
`python fetch.py --offline` rebuilds `data/raw/` from the cache without
touching the network. `build.py` reads only `data/raw/`. If a Wayback download
keeps failing, `--seed-dir DIR` accepts an earlier copy of a capture
(`CORScores_<ts>.json`), but only if its SHA-1 equals the CDX digest.

## AI assistance

This folder (the scripts, the data files and this README) was produced with
an AI assistant, Claude (Anthropic), working from a human-set brief and
rules: which hosts are off limits, request pacing, privacy, and that every
number must come from code that was run. Every figure above was computed by
`build.py` or the cross-check described above, from the raw files in
`data/raw/`. The re-keying and vanishing classifications are rules applied by
code, and the three permits with vanished histories were looked at
individually. A second AI pass checked the work and made the fixes listed
under "How the data was checked". The results have not been independently
reviewed by a person.
Check the underlying records before publishing any finding, and ask the city
to confirm anything that depends on the export's behavior.
