"""Download the Town of Prosper's published health-inspection sources.

1. Archive Center index (1 request): https://www.prospertx.gov/Archive.aspx?AMID=40,
   the "Monthly Development Report" module (Development Services monthly reports,
   June 2020 onward). Every Archive.aspx?ADID=N link and its title is recorded.
2. Archive Center PDFs (one request per report, 3 s apart): Archive.aspx?ADID=N, which
   redirects to /ArchiveCenter/ViewFile/Item/N. The body must be a complete PDF (starts
   with %PDF, has %%EOF in its last 2 KB); its sha256 and SHA-1 go into the manifest.
3. Wayback Machine backfill (4 s apart): CDX queries over the Town's old WordPress
   uploads folder (prospertx.gov/wp-content/uploads/) for monthly-report file names,
   then a download of every Development Services report that covers a month before
   June 2020 (the first month in the Archive Center), via
   https://web.archive.org/web/{timestamp}id_/{original}. A download must be a complete
   PDF and its SHA-1 must equal the CDX digest of that capture; otherwise the next
   capture of the same file is tried (some captures are truncated at 1 MiB). Reports for
   June 2020 and later are skipped: the Archive Center holds them (wayback_cdx.json
   records, for each skipped file, whether a capture has the same SHA-1 as the Archive
   Center PDF of that month).
4. EnerGov food operational permits (1 criteria GET + 4 search POSTs of 100):
   the public Tyler EnerGov Citizen Self Service search, SearchModule 12, permit type
   "Food Establishment" (LicenseTypeId 35db113b-...). Only public list fields are
   kept; holder names and the applicant-written description are dropped. EnerGov test
   records (DBA or company name containing the word "test") are dropped. The legal company
   name is withheld whenever the permit has a trade name (DBA), since it can be a
   person's name, and also when a permit without a DBA carries a company name that could
   be a person's name (see could_be_person); an opaque CompanyKey (same company -> same
   key) is kept so that renewals by one company can still be grouped.

Not used, on purpose: the civicapi inspection-score-violation and inspection-by-id
endpoints (they expose inspection types the Town hides from public search), any
contact/people endpoint, inspections.myhealthdepartment.com and discovery.cor.gov.

Outputs: data/raw/archive_index.json, data/raw/wayback_cdx.json,
data/raw/pdf_manifest.json (every PDF: source URL, bytes, sha256),
data/raw/permits_food.json.gz (sanitized permit list), data/raw/fetch_meta.json.
The PDFs themselves (about 200 MB) stay in the cache: PROSPER_CACHE (default ./.cache).

    python fetch.py                # everything; cached responses are never re-fetched
    python fetch.py --skip-wayback
    python fetch.py --offline      # rebuild the data/raw files from the cache only (no network)
"""

import argparse
import copy
import json
import re
import time
from urllib.parse import quote

from common import (CACHE, RAW, Client, pdf_complete, read_json, request_log_summary, sha1_base32,
                    sha256_file, write_json)

ARCHIVE = "https://www.prospertx.gov/Archive.aspx"
AMID = 40
CDX = "https://web.archive.org/cdx/search/cdx"
FIRST_ARCHIVE_MONTH = "2020-06"
ENERGOV = "https://prospertx-energovweb.tylerhost.net/apps/selfservice/api"
TENANT = {"tenantId": "1", "tenantName": "ProsperTXProd", "Tyler-TenantUrl": "ProsperTXProd",
          "Tyler-Tenant-Culture": "en-US", "Accept": "application/json", "Content-Type": "application/json"}
FOOD_PERMIT_TYPE = "35db113b-fb78-b204-0a0c-05fbce23f61d"
PERMIT_KEEP = ("CaseId", "CaseNumber", "CaseType", "CaseWorkclass", "CaseStatus", "IssueDate", "ApplyDate",
               "ExpireDate", "AddressDisplay", "MainParcel", "DBA", "CompanyName", "CompanyTypeName",
               "BusinessTypeName", "BusinessStatus")

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
MON_RE = ("(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          "sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")


def month_num(token):
    token = token.lower()[:3]
    return [m[:3] for m in MONTHS].index(token) + 1


def month_from_text(text):
    """'... August 2016 ...' -> '2016-08' (first month-name + 4-digit-year pair), else None."""
    m = re.search(MON_RE + r"[\s_.,-]*(20\d\d)", text, re.I)
    if not m:
        return None
    return f"{m.group(2)}-{month_num(m.group(1)):02d}"


# ----------------------------------------------------------------------------- Archive Center
def archive_index(client):
    status, html = client.get(f"{ARCHIVE}?AMID={AMID}", f"archive/AMID{AMID}.html")
    html = html.decode("utf-8", "replace")
    items = {}
    for adid, title in re.findall(r'href="Archive\.aspx\?ADID=(\d+)"[^>]*>(.*?)</a>', html, flags=re.S):
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", title)).strip()
        if title and (adid not in items or not items[adid]):
            items[adid] = title
        items.setdefault(adid, title)
    out = []
    for adid, title in items.items():
        out.append({"adid": int(adid), "title": title, "title_month": month_from_text(title),
                    "url": f"{ARCHIVE}?ADID={adid}",
                    "file_url": f"https://www.prospertx.gov/ArchiveCenter/ViewFile/Item/{adid}"})
    out.sort(key=lambda r: (r["title_month"] or "", r["adid"]))
    return out


def download_archive(client, index):
    manifest = []
    for i, it in enumerate(index, 1):
        dest = f"archive/ADID{it['adid']}.pdf"
        status, content = client.get(it["url"], dest, validate=pdf_complete)
        if not pdf_complete(content):
            raise RuntimeError(f"ADID {it['adid']}: not a complete PDF (status {status})")
        path = CACHE / dest
        manifest.append({"source": "archive_center", "key": f"ADID{it['adid']}", "adid": it["adid"],
                         "title": it["title"], "title_month": it["title_month"], "url": it["url"],
                         "cache_path": dest, "bytes": path.stat().st_size, "sha256": sha256_file(path),
                         "sha1_base32": sha1_base32(content)})
        if status != "cached":
            print(f"  [{i}/{len(index)}] ADID {it['adid']} {it['title_month']} {len(content):,} B", flush=True)
    return manifest


# ----------------------------------------------------------------------------- Wayback Machine
CDX_FILTERS = [
    ("month_report", "original:.*[Mm][Oo][Nn][Tt][Hh].*[Rr][Ee][Pp][Oo][Rr][Tt].*"),
    ("monthy_typo", "original:.*[Mm][Oo][Nn][Tt][Hh][Yy].*"),
    ("development_services", "original:.*[Dd][Ee][Vv][Ee][Ll][Oo][Pp][Mm][Ee][Nn][Tt]-[Ss][Ee][Rr][Vv][Ii][Cc][Ee][Ss].*"),
]
# File names that are Development Services reports. The ambiguous "Monthly-report-<mon>-<year>"
# names on the WordPress site are not downloaded: they are inferred to be Fire Department
# reports (the one opened in the recon, June 2018, is a Fire Chief activity report, and all of
# them are far smaller than the Development Services reports; see not_ds_name_summary()).
DS_NAME = re.compile(r"(development-services-monthly-report|monthly-development-report|"
                     r"dsc-monthly-staff-report|development-and-community-services-monthly-staff-report|"
                     r"development-services-" + MON_RE + r")", re.I)
NOT_DS = re.compile(r"(library|fire|parks|police|court|audit|financ|budget|structure|public-works|"
                    r"minutes|ordinance|schedule|manual|application|checklist|summary|annual|pws-)", re.I)


def wayback_cdx(client):
    rows = []
    for name, flt in CDX_FILTERS:
        url = (f"{CDX}?url=prospertx.gov/wp-content/uploads/*&output=json"
               f"&fl=timestamp,original,statuscode,mimetype,length,digest&filter={quote(flt, safe=':*')}")
        status, body = client.get(url, f"wayback/cdx_{name}.json")
        data = json.loads(body.decode("utf-8") or "[]")
        if data:
            hdr = data[0]
            rows += [dict(zip(hdr, r), query=name) for r in data[1:]]
    return rows


def url_key(original):
    return original.split("://", 1)[-1].lower().replace("www.", "", 1)


def classify_wayback(cdx_rows, archive_sha1_by_month=None):
    """Pick one capture per original URL for every Development Services report file name: the
    earliest 200 PDF capture, with the later ones kept as fallbacks (alternate_captures).
    For a file skipped because its month is in the Archive Center, record whether any capture
    has the same SHA-1 as the Archive Center PDF of that month."""
    by_url = {}
    for r in cdx_rows:
        if r["statuscode"] != "200" or r["mimetype"] != "application/pdf":
            continue
        fname = r["original"].rsplit("/", 1)[-1]
        if not DS_NAME.search(fname) or NOT_DS.search(fname):
            continue
        by_url.setdefault(url_key(r["original"]), []).append(r)
    picked = []
    for key, caps in sorted(by_url.items()):
        first = {}
        for c in caps:  # the same capture can come back from several CDX queries: keep the first
            first.setdefault(c["timestamp"], c)
        caps = sorted(first.values(), key=lambda c: c["timestamp"])
        r = {k: v for k, v in caps[0].items()}  # earliest 200 capture of each file
        fname = r["original"].rsplit("/", 1)[-1]
        name_month = month_from_text(re.sub(r"%e2%80%93", "-", fname, flags=re.I).replace("-", " "))
        r = dict(r, file_name=fname, name_month=name_month,
                 alternate_captures=[{"timestamp": c["timestamp"], "digest": c["digest"], "length": c["length"]}
                                     for c in caps[1:]])
        if name_month and name_month >= FIRST_ARCHIVE_MONTH:
            r["decision"] = "skip: month is in the Archive Center"
            if archive_sha1_by_month is not None:
                ac = archive_sha1_by_month.get(name_month, set())
                r["a_capture_is_identical_to_archive_center_pdf"] = any(c["digest"] in ac for c in caps)
        else:
            r["decision"] = "download"
        picked.append(r)
    return picked


NOT_DS_MONTHLY = re.compile(r"^monthly-report-" + MON_RE + r"-20\d\d(-\d)?\.pdf$", re.I)


def not_ds_name_summary(cdx_rows, picked):
    """The 'Monthly-report-<mon>-<year>.pdf' files (not downloaded): how many, the years in their
    names, and the CDX record length of their earliest 200 capture, next to the Development
    Services report files named for the same years."""
    first = {}
    for r in cdx_rows:
        fname = r["original"].rsplit("/", 1)[-1]
        if r["statuscode"] != "200" or r["mimetype"] != "application/pdf" or not NOT_DS_MONTHLY.match(fname):
            continue
        k = url_key(r["original"])
        if k not in first or r["timestamp"] < first[k]["timestamp"]:
            first[k] = r
    years = sorted({int(re.search(r"(20\d\d)", r["original"].rsplit("/", 1)[-1]).group(1)) for r in first.values()})
    lens = sorted(int(r["length"]) for r in first.values())
    ds = sorted(int(r["length"]) for r in picked
                if r["name_month"] and years and years[0] <= int(r["name_month"][:4]) <= years[-1])
    return {"pattern": NOT_DS_MONTHLY.pattern, "files": len(first), "years_in_names": [years[0], years[-1]] if years else None,
            "cdx_length_bytes_min_max": [lens[0], lens[-1]] if lens else None,
            "ds_report_files_same_years": len(ds),
            "ds_report_cdx_length_bytes_min_max_same_years": [ds[0], ds[-1]] if ds else None,
            "all_smaller_than_every_ds_file_of_same_years": bool(lens and ds and lens[-1] < ds[0]),
            "classification": ("inference, not verified file by file: Fire Department monthly reports, not "
                               "downloaded. Basis: the one opened in the recon (June 2018) is a Fire Chief activity "
                               "report, and these files are much smaller than the Development Services reports "
                               "of the same years")}


def download_wayback(client, picked):
    """Download each picked file: its earliest capture, else the next one. A capture is accepted
    only if it is a complete PDF and its SHA-1 equals the CDX digest of that capture."""
    manifest = []
    for r in picked:
        if r["decision"] != "download":
            continue
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", r["file_name"])
        tried = []
        for cap in [{"timestamp": r["timestamp"], "digest": r["digest"]}] + r.get("alternate_captures", []):
            url = f"https://web.archive.org/web/{cap['timestamp']}id_/{r['original']}"
            dest = f"wayback/{cap['timestamp']}_{safe}"

            def ok(content, digest=cap["digest"]):
                return pdf_complete(content) and sha1_base32(content) == digest
            try:
                status, content = client.get(url, dest, validate=ok, ok_status=(200,), validate_tries=1)
            except RuntimeError as e:  # body never passed the check
                tried.append({"timestamp": cap["timestamp"], "rejected": str(e)[-80:]})
                continue
            if not ok(content):
                tried.append({"timestamp": cap["timestamp"], "rejected": f"status {status}"})
                continue
            break
        else:
            print(f"  wayback {r['file_name']}: no complete capture; skipped")
            r["decision"] = "download failed (no complete capture)"
            r["captures_rejected"] = tried
            continue
        r["capture_used"] = cap["timestamp"]
        r["captures_rejected"] = tried
        path = CACHE / dest
        manifest.append({"source": "wayback", "key": f"WB{cap['timestamp']}_{safe}", "title": r["file_name"],
                         "title_month": r["name_month"], "url": url, "original": r["original"],
                         "timestamp": cap["timestamp"], "cache_path": dest, "bytes": path.stat().st_size,
                         "sha256": sha256_file(path), "sha1_base32": sha1_base32(content)})
        if status != "cached":
            print(f"  wayback {r['file_name']} {len(content):,} B", flush=True)
    return manifest


# ----------------------------------------------------------------------------- EnerGov permits
def energov_permits(client):
    status, body = client.get(f"{ENERGOV}/energov/search/criteria", "energov/criteria.json", headers=TENANT)
    template = json.loads(body)["Result"]
    rows, total, page = [], None, 1
    while True:
        crit = copy.deepcopy(template)
        crit.update(Keyword="", SearchModule=12, FilterModule=12, PageNumber=page, PageSize=100,
                    SortBy="LicenseNumber.keyword", SortAscending=True)
        for k, v in crit.items():
            if k.endswith("Criteria") and isinstance(v, dict):
                v.update(PageNumber=page, PageSize=100, SortBy="LicenseNumber.keyword", SortAscending=True)
        lic = crit["LicenseCriteria"]
        for k in list(lic):
            if k.endswith("Id") and k != "ContactId":
                lic[k] = "none"
        lic["LicenseTypeId"] = FOOD_PERMIT_TYPE
        lic["IsOperationalPermit"] = True
        status, body = client.get(f"{ENERGOV}/energov/search/search", f"energov/food_permits_p{page}.json",
                                  method="POST", json_body=crit, headers=TENANT)
        res = json.loads(body)["Result"]
        total = res["TotalFound"]
        batch = res.get("EntityResults") or []
        rows += batch
        if not batch or len(rows) >= total or page >= res.get("TotalPages", page):
            break
        page += 1
    ids = [r["CaseId"] for r in rows]
    if len(rows) != total or len(set(ids)) != len(ids):
        raise RuntimeError(f"permits: got {len(rows)} ({len(set(ids))} distinct), portal says {total}")
    kept = []
    for r in rows:
        k = {f: r.get(f) for f in PERMIT_KEEP}
        k["UnitOrSuite"] = (r.get("Address") or {}).get("UnitOrSuite")
        k["PostalCode"] = (r.get("Address") or {}).get("PostalCode")
        kept.append(k)
    return sanitize_permits(kept, total)


TEST_RECORD = re.compile(r"\btest\b", re.I)
CORPORATE = re.compile(r"\b(l\.?l\.?c|inc|corp(oration)?|co|company|l\.?p|ltd|isd|pta|pto|group|holdings?|"
                       r"enterprises?|partners(hip)?|ventures?|foundation|church|association|club|fund|district)\b",
                       re.I)
BUSINESS_WORD = re.compile(r"\d|restaurant|kitchen|caf[eé]|grill|pizza|coffee|donut|chicken|sushi|yogurt|cookie|"
                           r"bagel|brew|tavern|drive-?in|\binn\b|hotel|hospital|store|market|concession|food|"
                           r"\bice\b|scoop|bowl|platter|\btea\b|\bbar\b|bbq|burger|taco|deli|bakery|liquor|wine|"
                           r"pharmacy|express|fil[- ]?a\b|school|academy|gym", re.I)


def could_be_person(name, company_type):
    """A company name (on a permit without a DBA) that could be a person's name: no corporate
    marker and no business word, and either a sole proprietorship (whose legal name is the
    owner's unless it trades under an assumed name) or two or more plain words ('jimmy johns')."""
    if CORPORATE.search(name) or BUSINESS_WORD.search(name):
        return False
    return (company_type or "").lower() == "sole proprietorship" or len(re.findall(r"[A-Za-z][A-Za-z'.-]*", name)) >= 2


def has_dba(p):
    return bool(p.get("DBA")) and p["DBA"].strip().upper() not in ("NA", "N/A", "NONE")


def sanitize_permits(kept, total):
    """Drop EnerGov test records; replace the legal company name with an opaque CompanyKey
    where it could be a person's name (see the module docstring)."""
    kept = sorted(kept, key=lambda k: k["CaseNumber"])
    tests = [k for k in kept if TEST_RECORD.search(k.get("DBA") or "") or TEST_RECORD.search(k.get("CompanyName") or "")]
    kept = [k for k in kept if k not in tests]
    keys = {}
    withheld = []
    for k in kept:
        norm_name = re.sub(r"[^a-z0-9]", "", (k.get("CompanyName") or "").lower())
        k["CompanyKey"] = keys.setdefault(norm_name, f"C{len(keys) + 1:03d}") if norm_name else None
        c = k.get("CompanyName") or ""
        if has_dba(k):
            k["CompanyName"] = "[withheld]" if c else c
        elif c and could_be_person(c, k.get("CompanyTypeName")):
            k["CompanyName"] = "[withheld]"
            withheld.append(k["CaseNumber"])
    info = {"TotalFound": total, "rows_returned": total, "test_records_dropped": len(tests),
            "test_record_case_numbers": [k["CaseNumber"] for k in tests], "rows_kept": len(kept),
            "company_name_withheld_because_dba_present": sum(1 for k in kept if has_dba(k) and k["CompanyName"]),
            "company_name_withheld_without_dba": withheld}
    return kept, info


def previous_runs():
    """Runs recorded in an earlier fetch_meta.json (the first format kept one run at top level)."""
    path = RAW / "fetch_meta.json"
    if not path.exists():
        return []
    old = read_json(path)
    if "runs" in old:
        return old["runs"]
    http = old.get("http", {})
    return [{"started": old["started"], "finished": old["finished"],
             "http": {"network": http.get("network"), "cached": http.get("cached"),
                      "exceptions": http.get("errors")},
             "note": "recorded by the first version of fetch.py, which counted only exceptions as errors"}]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-wayback", action="store_true")
    ap.add_argument("--skip-permits", action="store_true")
    ap.add_argument("--offline", action="store_true", help="use the cache only; fail on anything not cached")
    args = ap.parse_args()
    client = Client(offline=args.offline)
    RAW.mkdir(parents=True, exist_ok=True)
    runs = previous_runs()
    meta = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    index = archive_index(client)
    write_json(RAW / "archive_index.json", index)
    print(f"Archive Center AMID={AMID}: {len(index)} reports")
    manifest = download_archive(client, index)

    if not args.skip_wayback:
        cdx = wayback_cdx(client)
        ac = {}
        for m in manifest:
            ac.setdefault(m["title_month"], set()).add(m["sha1_base32"])
        picked = classify_wayback(cdx, ac)
        manifest += download_wayback(client, picked)
        write_json(RAW / "wayback_cdx.json", {"queries": CDX_FILTERS, "captures": len(cdx), "ds_files": picked,
                                              "not_downloaded_monthly_report_files": not_ds_name_summary(cdx, picked)})
        print(f"Wayback: {len(cdx)} CDX rows, {len(picked)} DS report files, "
              f"{sum(1 for p in picked if p['decision'] == 'download')} downloaded")
    elif (RAW / "pdf_manifest.json").exists():
        manifest += [m for m in read_json(RAW / "pdf_manifest.json") if m["source"] == "wayback"]

    write_json(RAW / "pdf_manifest.json", manifest)
    meta["pdfs"] = {"archive_center": sum(1 for m in manifest if m["source"] == "archive_center"),
                    "wayback": sum(1 for m in manifest if m["source"] == "wayback"),
                    "total_bytes": sum(m["bytes"] for m in manifest)}

    if not args.skip_permits:
        permits, info = energov_permits(client)
        write_json(RAW / "permits_food.json.gz", permits, gz=True)
        meta["food_permits"] = info
        print(f"EnerGov food operational permits: {len(permits)} kept (portal TotalFound {info['TotalFound']}, "
              f"{info['test_records_dropped']} test records dropped)")

    finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    this_run = {"started": meta.pop("started"), "finished": finished, "offline": args.offline,
                "http": dict(client.stats)}
    # each run's network requests as logged (this also counts 403/429/5xx responses that were
    # retried successfully, which the first fetch.py did not count)
    for run in runs + [this_run]:
        run["request_log"] = request_log_summary(client.log_path, run["started"], run["finished"])
    meta = {"runs": runs + [this_run], **meta}
    meta["robots"] = {h: s for h, (s, _) in client.robots.items()} or read_json(RAW / "fetch_meta.json").get("robots", {})
    meta["robots_cache"] = {p.stem: int(p.read_text()) for p in sorted((CACHE / "robots").glob("*.status"))}
    write_json(RAW / "fetch_meta.json", meta)
    print(json.dumps(meta, indent=1)[:3000])


if __name__ == "__main__":
    main()
