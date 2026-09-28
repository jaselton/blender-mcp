"""Download the archived and public-API sources for Richardson's inspection scores.

Nothing here touches the city's own inspection app (discovery.cor.gov), whose
robots.txt disallows all crawling. Instead:

1. Wayback Machine (web.archive.org), 4 s between requests, backoff on resets/5xx.
   a. CDX API: every capture of HealthTrak's JSON export
         http://discovery.cor.gov/public/health/healthtrak.nsf/CORScores.json
      asked four ways (scheme-less, http://, https://, and a prefix match that
      also catches query-string and case variants). Each capture with HTTP 200
      is downloaded raw from https://web.archive.org/web/<ts>id_/<original>,
      checked against the SHA-1 digest the CDX API reports, and saved
      byte-for-byte as data/raw/wayback/CORScores_<ts>.json.
   b. CDX + raw captures of the public scores page (webScores.html), only to
      quote the city's description of the 0-100 scale. The page carries city
      contact e-mails and phone numbers, so they are removed before the page is
      even cached, and only the scale paragraph is kept
      (data/raw/wayback/scores_page_text.json), with the hashes of the capture.
   c. CDX inventories of other archived HealthTrak "webScores*" pages and of
      archived inspection-report pages (webScoresForReadonlyAll/...).
   d. From that inventory, the weekly score listings ("WebScores?openview")
      captured inside the gap between the last LIVES inspection and the
      earliest date in any archived export (two captures, 2019). Contacts are
      scrubbed before caching and only the table rows are kept
      (data/raw/wayback/weekly_listings_in_gap.json). The other listing
      captures are left as leads (see validation.json).
2. Socrata LIVES (lives.data.socrata.com /resource API; robots.txt Crawl-delay 1):
   the whole City of Richardson LIVES dataset yf9v-pthu (one flat table of
   businesses and their inspections; the city never published a LIVES
   violations table) paged with $limit/$offset in :id order, and the feed-info
   table tqfg-ya7z. The business phone column and the feed's contact e-mail are
   left out of the $select, so they are never downloaded.
3. Municode (api.municode.com): Richardson Code sec. 10-126, the food-permit
   suspension / closure rules, from the latest published code supplement.

Every HTTP answer is cached in .cache/ (RICHARDSON_CACHE) so a re-run makes no
new requests; --offline refuses to touch the network at all. The manifest
data/raw/fetch_meta.json lists every saved file with its sha256 and source URL.

If a Wayback download keeps failing, --seed-dir can point at an earlier copy
of the same captures (files named CORScores_<ts>.json); a seed file is used
only when its SHA-1 equals the digest the CDX API reports for that capture.

Usage:
    python fetch.py [--offline] [--seed-dir DIR]
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

from common import (CDX, CORSCORES, HEALTHTRAK, LIVES_DATASET, LIVES_FEED_INFO, MUNICODE_API,
                    MUNICODE_PRODUCT, RAW, SCORES_PAGE, SEC_10_126_NODE, SEC_10_126_URL, SOCRATA,
                    Client, count_contacts, scrub_contacts, scrub_json, sha1_b32, sha256, utcnow,
                    wayback_url, write_json)

CDX_FIELDS = "urlkey,timestamp,original,mimetype,statuscode,digest,length"
CDX_QUERIES = [
    # name, extra params, what it is for
    ("corscores_any_scheme", {"url": CORSCORES}, "corscores"),
    ("corscores_http", {"url": "http://" + CORSCORES}, "corscores"),
    ("corscores_https", {"url": "https://" + CORSCORES}, "corscores"),
    ("corscores_prefix", {"url": HEALTHTRAK + "/CORScores", "matchType": "prefix"}, "corscores"),
    ("scores_page", {"url": SCORES_PAGE}, "scores_page"),
    ("webscores_prefix", {"url": HEALTHTRAK + "/webscores", "matchType": "prefix", "limit": "20000",
                          "filter": "!original:.*[Rr]eadonly[Aa]ll.*"}, "inventory"),
    ("report_pages_prefix", {"url": HEALTHTRAK + "/webScoresForReadonlyAll", "matchType": "prefix",
                             "limit": "20000"}, "report_inventory"),
]

LIVES_COLUMNS = ["business_id", "business_name", "business_address", "business_city", "business_state",
                 "business_postal_code", "business_latitude", "business_longitude", "inspection_id",
                 "inspection_date", "inspection_score"]          # business_phone_number left out
FEED_COLUMNS = ["feed_version", "municipality_name", "municipality_url", "scoring_method"]  # no contact_email
PAGE = 1000

SCALE_RE = re.compile(r"((?:The following is the list[^.]*\.\s*)?Scores are based on a scale.*?"
                      r"enforcement action\.)", re.S | re.I)


def cdx(client, params):
    q = {"output": "json", "fl": CDX_FIELDS, **params}
    url = CDX + "?" + urlencode(q)
    status, body, meta = client.get(url)
    if status != 200:
        raise RuntimeError(f"CDX query failed: HTTP {status} {url}")
    rows = json.loads(body.decode("utf-8")) if body.strip() else []
    if rows and rows[0] == CDX_FIELDS.split(","):
        rows = rows[1:]
    return url, body, meta, [dict(zip(CDX_FIELDS.split(","), r)) for r in rows]


def fetch_capture(client, cap, seed_dir):
    """Download one Wayback capture raw; returns (bytes, provenance dict)."""
    url = wayback_url(cap["timestamp"], cap["original"])
    prov = {"timestamp": cap["timestamp"], "original": cap["original"], "wayback_url": url,
            "cdx_digest": cap["digest"], "cdx_mimetype": cap["mimetype"], "cdx_length": cap["length"]}
    body, err = None, None
    try:
        status, body, meta = client.get(url)
        prov.update({"http_status": status, "final_url": meta.get("final_url"),
                     "fetched_at": meta.get("fetched_at"),
                     "memento_datetime": (meta.get("headers") or {}).get("Memento-Datetime")})
        if status != 200:
            err, body = f"HTTP {status}", None
        elif sha1_b32(body) != cap["digest"]:
            err, body = f"digest mismatch ({sha1_b32(body)} != {cap['digest']})", None
    except Exception as e:  # network trouble after all retries
        err = repr(e)[:300]
    if body is not None:
        prov["obtained_via"] = "web.archive.org id_ download"
    else:
        prov["download_error"] = err
        seed = Path(seed_dir) / f"CORScores_{cap['timestamp']}.json" if seed_dir else None
        if seed and seed.exists() and sha1_b32(seed.read_bytes()) == cap["digest"]:
            body = seed.read_bytes()
            prov["obtained_via"] = ("seed copy of the same Wayback id_ capture (earlier download), "
                                    "accepted because its SHA-1 equals the CDX digest")
            prov["seed_file"] = seed.name
        else:
            raise RuntimeError(f"could not obtain capture {url}: {err}")
    prov.update({"bytes": len(body), "sha256": sha256(body), "sha1_base32": sha1_b32(body),
                 "digest_matches_cdx": sha1_b32(body) == cap["digest"]})
    return body, prov


def wayback(client, seed_dir, manifest):
    out = RAW / "wayback"
    out.mkdir(parents=True, exist_ok=True)
    found = {"corscores": {}, "scores_page": {}, "inventory": {}, "report_inventory": {}}
    manifest["cdx_queries"] = []
    for name, params, purpose in CDX_QUERIES:
        url, body, meta, rows = cdx(client, params)
        (out / f"cdx_{name}.json").write_bytes(body)
        manifest["cdx_queries"].append({"name": name, "purpose": purpose, "url": url, "rows": len(rows),
                                        "fetched_at": meta.get("fetched_at"), "file": f"wayback/cdx_{name}.json",
                                        "sha256": sha256(body)})
        print(f"CDX {name}: {len(rows)} captures")
        for r in rows:
            found[purpose][(r["timestamp"], r["original"])] = r

    # a. every CORScores.json capture that archived an HTTP 200 answer
    caps = sorted(found["corscores"].values(), key=lambda r: r["timestamp"])
    manifest["corscores_cdx_captures"] = caps
    manifest["corscores_captures"] = []
    seen_ts = set()
    for cap in caps:
        if cap["statuscode"] != "200":
            print(f"  skip {cap['timestamp']} {cap['original']} (status {cap['statuscode']})")
            continue
        if cap["timestamp"] in seen_ts:
            raise RuntimeError(f"two 200 captures share timestamp {cap['timestamp']}")
        seen_ts.add(cap["timestamp"])
        body, prov = fetch_capture(client, cap, seed_dir)
        n_contacts = count_contacts(body.decode("utf-8", "replace"))
        fname = f"CORScores_{cap['timestamp']}.json"
        if n_contacts:  # never expected; the export has no contact fields
            text, _ = scrub_contacts(body.decode("utf-8", "replace"))
            (out / fname).write_text(text, encoding="utf-8")
            prov["contacts_scrubbed"] = n_contacts
            prov["saved_sha256"] = sha256((out / fname).read_bytes())
        else:
            (out / fname).write_bytes(body)
        prov["file"] = f"wayback/{fname}"
        manifest["corscores_captures"].append(prov)
        print(f"  capture {cap['timestamp']} {cap['original']}: {len(body):,} bytes, "
              f"digest ok={prov['digest_matches_cdx']} via {prov['obtained_via'][:30]}")

    # b. the public scores page: keep only the paragraph describing the scale
    pages = []
    for cap in sorted(found["scores_page"].values(), key=lambda r: r["timestamp"]):
        if cap["statuscode"] != "200":
            continue
        url = wayback_url(cap["timestamp"], cap["original"])
        try:  # scrub=True: contact details are removed before the page is cached
            status, body, meta = client.get(url, scrub=True)
        except Exception as e:
            pages.append({"timestamp": cap["timestamp"], "original": cap["original"], "wayback_url": url,
                          "download_error": repr(e)[:300]})
            continue
        text = body.decode("utf-8", "replace")
        m = SCALE_RE.search(text)
        para = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip() if m else None
        para, n_scrub = scrub_contacts(para) if para else (para, 0)
        pages.append({"timestamp": cap["timestamp"], "original": cap["original"], "wayback_url": url,
                      "http_status": status, "bytes": meta["bytes"], "sha256": meta["sha256"],
                      "cdx_digest": cap["digest"], "digest_matches_cdx": meta["sha1_base32"] == cap["digest"],
                      "contacts_in_full_page_not_stored": meta.get("contacts_removed", count_contacts(text)),
                      "scale_paragraph": para, "contacts_scrubbed_from_paragraph": n_scrub})
        print(f"  scores page {cap['timestamp']}: paragraph {'found' if para else 'NOT found'}")
    write_json(out / "scores_page_text.json", {
        "note": ("Only the paragraph describing the score scale is kept. The full pages carry city "
                 "contact e-mails and phone numbers, which this project does not store; sha256 is of the "
                 "full capture as downloaded."), "captures": pages})

    # c. inventory of other archived webScores* pages (listing only)
    inv = sorted(found["inventory"].values(), key=lambda r: r["timestamp"])
    write_json(out / "healthtrak_webscores_inventory.json", {
        "note": "CDX listing only; none of these captures were downloaded.", "captures": inv})
    rep = sorted(found["report_inventory"].values(), key=lambda r: r["timestamp"])
    write_json(out / "healthtrak_report_pages_inventory.json", {
        "note": ("CDX listing of archived HealthTrak inspection-report pages (webScoresForReadonlyAll/...); "
                 "none were downloaded."), "captures": rep})
    return found


# ----------------------------------------------------------------------------- weekly listings in the gap
LISTING_PATH = re.compile(r"(?i)^https?://discovery\.cor\.gov/public/health/healthtrak\.nsf/webscores(\?openview)?$")
WEEK_RE = re.compile(r"(?i)scores for the week ending\s*(\d{1,2}/\d{1,2}/\d{4})")
ROW_LINK = re.compile(r"(?i)href=\"?([^\"\s>]*webScoresForReadonly(?:All)?/([^?\"\s>]*)\?OpenDocument)")


def parse_listing(text):
    """Rows of an archived HealthTrak weekly listing: name | address | score | report link."""
    rows = []
    for seg in re.split(r"(?i)<tr\b", text)[1:]:
        seg = seg.split("</tr>")[0]
        m = ROW_LINK.search(seg)
        if not m:
            continue
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"(?is)<td\b[^>]*>(.*?)</td>", seg)]
        if len(cells) != 4:
            raise RuntimeError(f"unexpected listing row ({len(cells)} cells): {cells}")
        rows.append({"name": cells[0], "address": cells[1], "score": cells[2], "report_href": m.group(1),
                     "report_key": m.group(2)})
    return rows


def gap_listings(client, inventory, manifest):
    """Archived weekly score listings (HealthTrak 'WebScores' view) captured between the last LIVES
    inspection and the earliest date in any archived export, i.e. inside the gap the two main sources
    leave. Contacts are scrubbed before the page is cached; only the table rows are kept."""
    lives = json.loads((RAW / "socrata" / f"{LIVES_DATASET}.json").read_text(encoding="utf-8"))
    lo = max(x["inspection_date"][:10] for x in lives if x.get("inspection_date")).replace("-", "")
    export_dates = []
    for c in manifest["corscores_captures"]:
        d = json.loads((RAW / c["file"]).read_text(encoding="utf-8"), strict=False)
        for e in d["establishments"]:
            for a in e.get("addresses", []):
                for s in a.get("scores", []):
                    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", s.get("date", ""))
                    if m and int(m.group(3)) >= 1990:
                        export_dates.append(m.group(3) + m.group(1) + m.group(2))
    hi = min(export_dates)
    caps = [c for c in sorted(inventory.values(), key=lambda r: r["timestamp"])
            if c["statuscode"] == "200" and LISTING_PATH.match(c["original"]) and lo < c["timestamp"][:8] < hi]
    out = []
    for cap in caps:
        url = wayback_url(cap["timestamp"], cap["original"])
        status, body, meta = client.get(url, scrub=True)
        text = body.decode("utf-8", "replace")
        rows = parse_listing(text)
        wk = WEEK_RE.search(re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))))
        c_rows = [scrub_json(r, [0]) for r in rows]
        sm = SCALE_RE.search(text)
        para = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", sm.group(1)))).strip() if sm else None
        out.append({"timestamp": cap["timestamp"], "original": cap["original"], "wayback_url": url,
                    "http_status": status, "bytes": meta["bytes"], "sha256": meta["sha256"],
                    "cdx_digest": cap["digest"], "digest_matches_cdx": meta["sha1_base32"] == cap["digest"],
                    "contacts_in_full_page_not_stored": meta.get("contacts_removed"),
                    "fetched_at": meta.get("fetched_at"),
                    "week_ending_as_published": wk.group(1) if wk else None,
                    "report_links_on_page": len(ROW_LINK.findall(text)),
                    "scale_paragraph": scrub_contacts(para)[0] if para else None, "rows": c_rows})
        print(f"  weekly listing {cap['timestamp']}: week ending {wk.group(1) if wk else '?'}, {len(rows)} rows")
    write_json(RAW / "wayback" / "weekly_listings_in_gap.json", {
        "note": ("Archived HealthTrak weekly score listings ('WebScores?openview') captured by the Internet Archive "
                 f"between the last LIVES inspection ({lo}) and the earliest date in any archived export ({hi}). "
                 "Only the table rows are kept (name, address, score, report link as printed); the full pages "
                 "carry city contact details, which are not stored. sha256 is of the full capture as downloaded."),
        "gap_bounds_exclusive": [lo, hi], "captures": out})
    manifest["weekly_listings_in_gap"] = [{k: c[k] for k in ("timestamp", "wayback_url", "sha256", "digest_matches_cdx",
                                                            "week_ending_as_published")} | {"rows": len(c["rows"])}
                                          for c in out]


def socrata(client, manifest):
    out = RAW / "socrata"
    out.mkdir(parents=True, exist_ok=True)
    base = f"{SOCRATA}/{LIVES_DATASET}.json"

    # field list without downloading any row ($limit=0 returns only headers)
    status, _, meta = client.get(base + "?" + urlencode({"$limit": 0}))
    hdr = meta.get("headers") or {}
    fields = json.loads(hdr.get("X-SODA2-Fields", "[]"))
    types = json.loads(hdr.get("X-SODA2-Types", "[]"))

    status, body, _ = client.get(base + "?" + urlencode({"$select": "count(*) as n"}))
    n_expected = int(json.loads(body)[0]["n"])

    rows, pages, offset = [], [], 0
    select = ",".join([":id"] + LIVES_COLUMNS)
    while True:
        url = base + "?" + urlencode({"$select": select, "$order": ":id", "$limit": PAGE, "$offset": offset})
        status, body, meta = client.get(url)
        if status != 200:
            raise RuntimeError(f"Socrata page failed: HTTP {status} {url}")
        batch = json.loads(body)
        pages.append({"url": url, "rows": len(batch), "sha256": meta["sha256"], "fetched_at": meta["fetched_at"]})
        rows.extend(batch)
        offset += len(batch)
        if len(batch) < PAGE:
            break
    ids = [r[":id"] for r in rows]
    if len(rows) != n_expected or len(set(ids)) != len(ids):
        raise RuntimeError(f"LIVES: got {len(rows)} rows ({len(set(ids))} distinct), API count {n_expected}")
    counter = [0]
    rows = scrub_json(rows, counter)
    write_json(out / f"{LIVES_DATASET}.json", rows)
    print(f"Socrata {LIVES_DATASET}: {len(rows)} rows (API count {n_expected}), contact strings removed: {counter[0]}")

    feed_url = f"{SOCRATA}/{LIVES_FEED_INFO}.json?" + urlencode({"$select": ",".join(FEED_COLUMNS)})
    status, body, fmeta = client.get(feed_url)
    fcounter = [0]
    feed = scrub_json(json.loads(body), fcounter)
    write_json(out / f"{LIVES_FEED_INFO}.json", feed)

    manifest["socrata"] = {
        "dataset": LIVES_DATASET, "dataset_page": f"https://lives.data.socrata.com/d/{LIVES_DATASET}",
        "api": base, "fields_published": fields, "field_types": types,
        "fields_not_downloaded": [f for f in fields if f not in LIVES_COLUMNS + [":id"]],
        "last_modified": hdr.get("X-SODA2-Truth-Last-Modified") or hdr.get("Last-Modified"),
        "api_count": n_expected, "rows_saved": len(rows), "contact_strings_removed": counter[0],
        "pages": pages, "file": f"socrata/{LIVES_DATASET}.json",
        "sha256": sha256((out / f"{LIVES_DATASET}.json").read_bytes()),
        "feed_info": {"dataset": LIVES_FEED_INFO, "url": feed_url, "rows": len(feed),
                      "columns_left_out": ["contact_email"], "contact_strings_removed": fcounter[0],
                      "file": f"socrata/{LIVES_FEED_INFO}.json",
                      "sha256": sha256((out / f"{LIVES_FEED_INFO}.json").read_bytes()),
                      "fetched_at": fmeta.get("fetched_at")},
        "note": ("The city's LIVES feed is one flat table (business fields repeated on every inspection "
                 "row). It has no violations table and no violation columns."),
    }


def municode(client, manifest):
    status, body, meta = client.get(f"{MUNICODE_API}/Jobs/latest/{MUNICODE_PRODUCT}")
    job = json.loads(body)
    url = f"{MUNICODE_API}/CodesContent?" + urlencode(
        {"jobId": job["Id"], "nodeId": SEC_10_126_NODE, "productId": MUNICODE_PRODUCT})
    status, body, cmeta = client.get(url)
    docs = json.loads(body)["Docs"]
    doc = next(d for d in docs if d["Id"] == SEC_10_126_NODE)
    content = doc["Content"]
    text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<(p|br|li|div)[^>]*>", "\n", content)))
    text = "\n".join(re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines())
    text = re.sub(r"\n{2,}", "\n", text).strip()
    text, n = scrub_contacts(text)
    rec = {"section": doc.get("Title"), "node_id": SEC_10_126_NODE, "library_url": SEC_10_126_URL,
           "api_url": url, "job": {k: job.get(k) for k in ("Id", "Name", "PublishDate", "OnlineDate",
                                                               "BannerText")},
           "fetched_at": cmeta.get("fetched_at"), "response_sha256": cmeta.get("sha256"),
           "contacts_scrubbed": n, "text": text}
    write_json(RAW / "municode_sec_10-126.json", rec)
    manifest["municode"] = {"file": "municode_sec_10-126.json", "api_url": url, "library_url": SEC_10_126_URL,
                            "job": rec["job"], "sha256": sha256((RAW / "municode_sec_10-126.json").read_bytes())}
    print(f"Municode: {doc.get('Title')} ({len(text):,} chars) from {job.get('Name')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="use only the local cache")
    ap.add_argument("--seed-dir", help="earlier copies of Wayback captures, used only if the CDX digest matches")
    args = ap.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    client = Client(offline=args.offline)
    manifest = {"started_at": utcnow(), "offline_run": args.offline,
                "note": ("fetched_at values are the times of the network requests (kept in the cache); "
                         "started_at is when this manifest was written, which on an --offline run "
                         "is a rebuild from cache, not a new download."),
                "never_contacted": ["discovery.cor.gov (robots.txt: User-agent: * / Disallow: /)",
                                    "inspections.myhealthdepartment.com"]}
    manifest["robots"] = {}
    for host in ("web.archive.org", "lives.data.socrata.com", "api.municode.com"):
        rb = client.robots_txt(host)
        manifest["robots"][host] = {"url": rb["meta"]["url"], "status": rb["meta"]["status"],
                                    "fetched_at": rb["meta"].get("fetched_at"),
                                    "groups": sorted(rb["groups"]),
                                    "crawl_delay": re.findall(r"(?im)^crawl-delay:\s*(\S+)", rb["text"])}

    found = wayback(client, args.seed_dir, manifest)
    socrata(client, manifest)
    gap_listings(client, found["inventory"], manifest)
    municode(client, manifest)

    manifest["finished_at"] = utcnow()
    manifest["http_stats"] = client.stats
    files = sorted(p for p in RAW.rglob("*") if p.is_file() and p.name != "fetch_meta.json")
    manifest["files"] = {str(p.relative_to(RAW)): {"bytes": p.stat().st_size, "sha256": sha256(p.read_bytes())}
                         for p in files}
    write_json(RAW / "fetch_meta.json", manifest)
    print("http:", client.stats)


if __name__ == "__main__":
    sys.exit(main())
