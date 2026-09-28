"""Download Allen's public food-inspection records from the city's Tyler EnerGov
Citizen Self Service portal (anonymous JSON API, no login).

    https://cityofallentx-energovweb.tylerhost.net/apps/selfservice#/home

Steps (every response is cached, so the script can be stopped and re-run and
will resume where it left off):

  1. Inspection types (GET /inspections/search/setup) and statuses. The food-
     related types are picked by name (see FOOD_TYPE_RULE).
  2. Listing: one unfiltered search per type (InspectionStatusId 'none', no date
     filter), PageSize 100, sorted by inspection number. The number of distinct
     CaseIds must equal the portal's TotalFound for the type.
  3. Status reconciliation: for every status seen in a type, the portal is asked
     for TotalFound with that status filter; it must equal our row count.
  4. Establishment tables: food and day-care operational permits (module 12) and
     food / special-event food licenses (module 10), each paged the same way.
  5. Details: GET /inspections/getById/{id} for every inspection that is not
     Cancelled / BL - Cancelled (inspector, actual date, comment, link to the
     permit or business). About 8,800 calls at >= 1.15 s apart: roughly 3 hours.
     A cached failure (e.g. a transient 'StatusCode 204') is retried once per run.

robots.txt is checked first (the EnerGov host has none). E-mail addresses and phone
numbers are removed before responses are cached (common.scrub_json), and the names and
certificate numbers of establishment staff are removed from the comments before
details.json.gz is written (common.CommentScrubber). Licence-holder names, tax ids and the
free-text permit Description are dropped from the permit rows (see PRIVATE_PERMIT_FIELDS).

Outputs under data/raw/ (gzipped JSON):
  inspection_types.json.gz, inspection_statuses.json.gz, listing.json.gz,
  status_reconciliation.json, permits_licenses.json.gz, details.json.gz,
  fetch_meta.json

Usage:
  python fetch.py                 # everything (resumes from the cache)
  python fetch.py --no-details    # steps 1-4 only
Set ALLEN_CACHE to keep the HTTP cache outside the repository.
"""

import argparse
import json
import re
import sys
import time

from common import (API, GETBYID_URL, RAW, TENANT_HEADERS, Client, CommentScrubber, Robots, inspection_body,
                    license_body, search, write_gz_json)

# Food-related inspection types, chosen from the portal's public type list by name.
# Included: EH-Food Establishment, EH-Food Truck, EH-Day Care Facility, EH-Convenience Store,
# every "OP - ..." type about food, daycare, mobile or temporary food (including the converted
# past-health-inspection type), "zzOP - Routine Food Inspection V2", and the food and day-care
# complaint types. Excluded on purpose: EH-Health - Final (construction final inspections tied to
# building permits), EH-Request Initial Inspection (requests, status 'Valid'), pools, apartments,
# liquid-waste (LW) and building types.
FOOD_TYPE_RULE = re.compile(
    r"^(EH-(Food Establishment|Food Truck|Day Care Facility|Convenience Store)"
    r"|(zz)?OP - .*(Food|Daycare|Mobile|Temp|Past Health)"
    r"|Food Complaint Inspection|Day Care Complaint Inspection)", re.I)
EXCLUDE_TYPE_RULE = re.compile(r"(Pool|\bLW\b|Apartment|Swimming)")  # case-sensitive: "Follolw Up" is food

# Establishment tables (name, prep category, open/closed): module 12 = operational permits,
# module 10 = licenses. Type ids from the portal's GlobalSearchSecondaryData case-type list.
ESTABLISHMENT_SOURCES = [
    ("op_permit_food", 12, "b594e305-2c34-61cd-aad5-59f3413b6085"),
    ("op_permit_daycare", 12, "0ff09d67-ae83-6cd7-f9fa-08b5ed587fe1"),
    ("license_food", 10, "2fe06513-8544-41b0-a65c-231b045de95c"),
    ("license_special_event_food", 10, "c721e928-a7c0-47fa-a03d-3dee7952d75a"),
]
# Fields dropped from permit/license rows before saving: license-holder personal names, tax id, and the
# free-text Description, whose staff notes name private individuals ("Closed per <name>", "Spoke to
# <name>") and once held a driver's-licence number. build.py does not use it; the only fact kept from
# it is whether it marks the permit as the city's app-testing / training record (see TEST_DESCRIPTION).
PRIVATE_PERMIT_FIELDS = {"HolderFirstName", "HolderLastName", "HolderMiddleName", "HolderCompanyName", "TaxID",
                         "Description"}
# 'For App Testing and Training Purposes - Only' (the city's "Training Food Establishment" permit)
TEST_DESCRIPTION = re.compile(r"\b(?:app\s+)?testing\b|\btest(?:ing)?\s+(?:purposes?|only|record)\b|"
                              r"\btraining\s+purposes?\b", re.I)
SKIP_DETAIL_STATUSES = {"Cancelled", "BL - Cancelled"}
PAGE = 100


def page_all(client, make_body, label):
    """Page a search to the end. Returns (rows, total_found_first, total_found_last, pages)."""
    rows, page, first_total, last_total, pages = [], 1, None, None, None
    while True:
        res = search(client, make_body(page))
        if first_total is None:
            first_total, pages = res["TotalFound"], res["TotalPages"]
        last_total = res["TotalFound"]
        batch = res.get("EntityResults") or []
        rows.extend(batch)
        if not batch or page >= res["TotalPages"]:
            break
        page += 1
    ids = [r["CaseId"].lower() for r in rows]
    print(f"  {label}: {len(rows)} rows, {len(set(ids))} distinct, TotalFound {first_total}->{last_total}",
          flush=True)
    return rows, first_total, last_total, pages


def dedupe(rows):
    seen, out = set(), []
    for r in rows:
        k = r["CaseId"].lower()
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def fetch_listing(client, types):
    listing, problems = {}, []
    for t in types:
        tid, name = t["InspectionTypeID"], t["Name"].strip()
        rows, tf0, tf1, pages = page_all(
            client, lambda p: inspection_body(client, type_id=tid, page=p, size=PAGE), name)
        rows = dedupe(rows)
        if len(rows) != tf1:
            # A second pass in another order catches rows lost to paging (none expected when
            # sorting by the unique inspection number).
            more, *_ = page_all(client, lambda p: inspection_body(
                client, type_id=tid, page=p, size=PAGE, sort="ScheduledDate", asc=False), name + " (desc)")
            rows = dedupe(rows + more)
        ok = len(rows) == tf1
        if not ok:
            problems.append(f"{name}: {len(rows)} distinct rows vs TotalFound {tf1}")
        listing[name] = {"type_id": tid, "total_found": tf1, "total_found_first_page": tf0,
                         "rows_distinct": len(rows), "matches_total_found": ok, "rows": rows}
    return listing, problems


def reconcile_statuses(client, listing):
    """Ask the portal for TotalFound per (type, status id) and compare with our rows."""
    out = {}
    for name, d in listing.items():
        counts = {}
        for r in d["rows"]:
            key = (r.get("CaseStatusId") or "none", r.get("CaseStatus"))
            counts[key] = counts.get(key, 0) + 1
        per = []
        for (sid, sname), n in sorted(counts.items(), key=lambda kv: -kv[1]):
            if sid == "none":
                per.append({"status": sname, "status_id": None, "rows": n, "portal_total": None, "match": None})
                continue
            res = search(client, inspection_body(client, type_id=d["type_id"], status_id=sid, size=10))
            per.append({"status": sname, "status_id": sid, "rows": n, "portal_total": res["TotalFound"],
                        "match": res["TotalFound"] == n})
        out[name] = {"total_found": d["total_found"], "rows": d["rows_distinct"],
                     "status_rows_sum": sum(p["rows"] for p in per),
                     "all_match": all(p["match"] is not False for p in per)
                     and sum(p["rows"] for p in per) == d["total_found"],
                     "statuses": per}
        print(f"  status check {name}: {'OK' if out[name]['all_match'] else 'MISMATCH'}", flush=True)
    return out


def fetch_establishments(client):
    out, problems = {}, []
    for label, module, tid in ESTABLISHMENT_SOURCES:
        rows, tf0, tf1, pages = page_all(client, lambda p: license_body(client, module, tid, page=p, size=PAGE),
                                         label)
        rows = dedupe(rows)
        if len(rows) != tf1:
            more, *_ = page_all(client, lambda p: license_body(client, module, tid, page=p, size=PAGE,
                                                               sort="CompanyName.keyword"), label + " (by name)")
            rows = dedupe(rows + more)
        if len(rows) != tf1:
            problems.append(f"{label}: {len(rows)} distinct rows vs TotalFound {tf1}")
        rows = [{**{k: v for k, v in r.items() if k not in PRIVATE_PERMIT_FIELDS},
                 "DescriptionMarksTestRecord": bool(TEST_DESCRIPTION.search(r.get("Description") or ""))}
                for r in rows]
        out[label] = {"module": module, "type_id": tid, "total_found": tf1, "rows_distinct": len(rows),
                      "matches_total_found": len(rows) == tf1, "rows": rows}
    return out, problems


def fetch_details(client, listing):
    todo = [(name, r) for name, d in listing.items() for r in d["rows"]
            if r.get("CaseStatus") not in SKIP_DETAIL_STATUSES]
    print(f"details: {len(todo)} inspections (skipping Cancelled / BL - Cancelled)", flush=True)
    details, errors = [], []
    t0, net0 = time.time(), client.stats["network"]
    for i, (name, r) in enumerate(todo, 1):
        url = GETBYID_URL.format(id=r["CaseId"])
        try:
            d = client.json("GET", url, headers=TENANT_HEADERS)
        except Exception as e:  # keep going; the error is recorded and a re-run retries it
            errors.append({"CaseId": r["CaseId"], "CaseNumber": r["CaseNumber"], "error": str(e)[:300]})
            continue
        if not d.get("Success") or not d.get("Result"):
            # one fresh try for a cached failure (e.g. StatusCode 204 once); a re-run tries again
            try:
                d = client.json("GET", url, headers=TENANT_HEADERS, refresh=True)
            except Exception as e:
                d = {"Success": False, "StatusCode": None, "ErrorMessage": str(e)[:200]}
        if not d.get("Success") or not d.get("Result"):
            errors.append({"CaseId": r["CaseId"], "CaseNumber": r["CaseNumber"],
                           "error": f"Success={d.get('Success')} StatusCode={d.get('StatusCode')} "
                                    f"{(d.get('ErrorMessage') or '')[:200]}"})
            continue
        details.append(d["Result"])
        if i % 200 == 0 or i == len(todo):
            net = client.stats["network"] - net0
            rate = (time.time() - t0) / max(net, 1)
            left = sum(1 for _ in todo[i:]) * rate if net else 0
            print(f"  details {i}/{len(todo)} (network {net}, errors {len(errors)}, "
                  f"~{left / 60:.0f} min left if uncached)", flush=True)
    return details, errors, len(todo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-details", action="store_true", help="skip the getById calls")
    ap.add_argument("--offline", action="store_true", help="use only the cache (fails on anything missing)")
    args = ap.parse_args()

    client = Client(offline=args.offline)
    RAW.mkdir(parents=True, exist_ok=True)
    meta = {"portal": "https://cityofallentx-energovweb.tylerhost.net/apps/selfservice#/home", "api": API,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    robots = Robots(client)
    for url in (API + "/search/search", GETBYID_URL.format(id="x")):
        if not robots.allowed(url):
            raise SystemExit(f"robots.txt disallows {url}")
    meta["robots"] = robots.log

    setup = client.json("GET", API + "/inspections/search/setup", headers=TENANT_HEADERS)
    all_types = setup["Result"]["InspectionTypes"]
    write_gz_json(RAW / "inspection_types.json.gz", all_types)
    statuses = client.json("GET", API + "/inspections/inspection/status", headers=TENANT_HEADERS)["Result"]
    write_gz_json(RAW / "inspection_statuses.json.gz", statuses)
    types = [t for t in all_types if FOOD_TYPE_RULE.search(t["Name"].strip())
             and not EXCLUDE_TYPE_RULE.search(t["Name"])]
    print(f"{len(all_types)} inspection types, {len(types)} food-related:", flush=True)
    for t in types:
        print("   ", t["Name"].strip(), t["InspectionTypeID"])

    listing, problems = fetch_listing(client, types)
    write_gz_json(RAW / "listing.json.gz", listing)
    recon = reconcile_statuses(client, listing)
    (RAW / "status_reconciliation.json").write_text(json.dumps(recon, indent=1))

    est, est_problems = fetch_establishments(client)
    problems += est_problems
    write_gz_json(RAW / "permits_licenses.json.gz", est)

    meta["listing"] = {k: {"type_id": v["type_id"], "total_found": v["total_found"], "rows": v["rows_distinct"]}
                       for k, v in listing.items()}
    meta["establishment_sources"] = {k: {"module": v["module"], "type_id": v["type_id"],
                                         "total_found": v["total_found"], "rows": v["rows_distinct"]}
                                     for k, v in est.items()}
    if not args.no_details:
        details, errors, n_todo = fetch_details(client, listing)
        details.sort(key=lambda r: r["InspectionNumber"])
        # remove names / certificate numbers of establishment staff from the comments before saving
        scrubber = CommentScrubber([d.get("Comment") for d in details],
                                   {d.get("AssignedInspectorName") for d in details},
                                   [(d.get("LinkId") or d["InspectionId"]).lower() for d in details])
        for d in details:
            d["Comment"] = scrubber(d.get("Comment"))
        meta["comment_scrub"] = scrubber.stats
        write_gz_json(RAW / "details.json.gz", details)
        meta["details"] = {"requested": n_todo, "saved": len(details), "errors": errors}
    meta["problems"] = problems
    meta["http"] = client.stats
    meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (RAW / "fetch_meta.json").write_text(json.dumps(meta, indent=1))
    print("problems:", problems or "none")
    print("http:", client.stats)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
