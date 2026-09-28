"""Download the City of Allen's published health-score reports (PDF lists of
inspection scores) from the city's web site and from the Internet Archive.

Sources
  * Jul-Sep 2025 report, still on the city's CMS but no longer linked.
  * Apr-Jun 2024 report, linked from the city's Document Archive page
    (https://www.cityofallen.org/services/document_archive.php); the Wayback
    copy is fetched too, to confirm the two files are identical.
  * The old CivicPlus site's Archive Center "Health Scores" category (AMID=74),
    preserved only in the Wayback Machine:
      1. every capture of Archive.aspx?AMID=74&Type=Recent (which redirected to
         the category's newest report) is resolved to its ArchiveCenter item;
      2. the 2014 capture of the category page lists the 2010-2014 monthly items;
      3. every other ArchiveCenter item id >= 1800 that Wayback holds as a PDF
         whose capture is 20 KB - 1 MB (captures of the reports reached through
         the category links are 70-154 KB) is downloaded once and classified by
         its first-page text.
    Items that turn out to be score reports are kept; the rest are recorded in
    the manifest as "not a score report", "truncated capture" (the archive holds
    only the first 1 MiB of some files) or, for scans, as read by eye.

robots.txt is read for each host first and obeyed. Requests to web.archive.org
are spaced 4 s apart. Everything is cached (ALLEN_CACHE).

Output: data/raw/pdfs/<name>.pdf for every score report found, and
data/raw/pdf_manifest.json describing every file that was checked.
"""

import io
import json
import re
import sys
import time
import zlib
from collections import defaultdict
from urllib.parse import quote

import pdfplumber

from common import RAW, Client, Robots, scrub_contacts
from parse_pdfs import fix_pua

PDF_DIR = RAW / "pdfs"
WB = "https://web.archive.org"

CITY_REPORTS = [
    {"name": "allen_2025Q3_live",
     "url": "https://cms3.revize.com/revize/allentx/Documents/Departments/Community%20Enhancement/"
            "Health%20and%20Food%20Safety/Food%20Safety/Inspection%20Scores%20July-Sept%202025.pdf",
     "cited_as": "https://www.cityofallen.org/Documents/Departments/Community%20Enhancement/"
                 "Health%20and%20Food%20Safety/Food%20Safety/Inspection%20Scores%20July-Sept%202025.pdf",
     "how_found": "city CMS (orphaned: no longer linked from the Food Safety page)"},
    {"name": "allen_2024Q2_document_archive",
     "url": "https://www.cityofallen.org/Archived%20Documents/City%20Secretary/Open%20Records/"
            "Health%20Inspection%20Report%20-%20April%20to%20June%202024.pdf",
     "cited_as": None,
     "how_found": "city Document Archive page https://www.cityofallen.org/services/document_archive.php"},
    {"name": "allen_2024Q2_wayback",
     "url": WB + "/web/20240928153516id_/https://cms3.revize.com/revize/allentx/Documents/Departments/"
                 "Community%20Enhancement/Health%20and%20Food%20Safety/Food%20Safety/"
                 "Health%20Inspection%20Report%20-%20April%20to%20June%202024.pdf",
     "cited_as": None,
     "how_found": "Wayback copy of the file linked from the Food Safety page in Sept 2024 (checksum comparison)"},
]

REPORT_TEXT = re.compile(r"HEALTH\s+INSPECTION\s+REPORT|INSPECTION\s+SCORES|TOTALS\s*-\s*ALL\s+INSPECTIONS|"
                         r"HEALTH\s+SCORES|FACILITY\s+NAME.*RATING", re.I | re.S)
CDX_MIN, CDX_MAX, MIN_ITEM = 20_000, 1_000_000, 1800


def pdf_info(content):
    """Pages, first-page text, and whether the file has a usable text layer."""
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            n = len(pdf.pages)
            first = fix_pua(pdf.pages[0].extract_text() or "")
            meta = {k: str(v)[:120] for k, v in (pdf.metadata or {}).items()
                    if k in ("Title", "Producer", "Creator", "CreationDate")}
    except Exception as e:
        return {"pdf_error": repr(e)[:200]}
    letters = len(re.findall(r"[A-Za-z]", first))
    return {"pages": n, "first_page_chars": len(first), "first_page_letters": letters,
            "first_page_head": scrub_contacts(re.sub(r"\s+", " ", first)[:160])[0], "pdf_meta": meta}


# Scanned items (no text layer) whose first page was looked at by eye.
READ_BY_EYE = {
    2078: "not a score report (scan: City of Allen declaration of local disaster for public health emergency, "
          "March 2020; page 1 read by eye)",
    2080: "not a score report (scan: City of Allen emergency order, March 2020; page 1 read by eye)",
}
TRUNCATED = 1 << 20  # Wayback holds only the first 1 MiB of some captures (x-archive-orig-content-length)


def peek_truncated(content):
    """Text from the first page-content streams of a truncated PDF (best effort, for the manifest):
    only Flate streams with text operators, only strings shown with Tj/TJ, only printable text."""
    texts = []
    for sm in re.finditer(rb"stream\r?\n(.*?)endstream", content, re.S):
        try:
            d = zlib.decompress(sm.group(1))
        except zlib.error:
            continue
        if b"BT" not in d or not (b"Tj" in d or b"TJ" in d):
            continue
        parts = re.findall(rb"\(((?:[^()\\]|\\.){1,200})\)\s*Tj", d)
        for arr in re.findall(rb"\[(.*?)\]\s*TJ", d, re.S):
            parts.append(b"".join(re.findall(rb"\(((?:[^()\\]|\\.){0,200})\)", arr)))
        t = re.sub(r"\s+", " ", b" ".join(parts).decode("latin-1")).strip()
        if len(t) >= 20 and sum(ch.isprintable() and ord(ch) < 128 for ch in t) / len(t) > 0.9:
            texts.append(t[:120])
        if len(texts) >= 2:
            break
    return scrub_contacts(" / ".join(texts))[0]


def classify(info, item=None, content=b""):
    if "pdf_error" in info:
        if len(content) == TRUNCATED:
            head = peek_truncated(content)
            if REPORT_TEXT.search(head):
                return "truncated capture (looks like a score report)"
            info["truncated_peek"] = head
            return ("truncated capture: not a score report (archive holds only the first 1 MiB; "
                    "text seen: " + head[:80] + ")") if head else \
                "truncated capture: unclassified (archive holds only the first 1 MiB of a larger file)"
        return "not a PDF / unreadable"
    if info["first_page_letters"] < 40:
        if item in READ_BY_EYE:
            return READ_BY_EYE[item]
        if "agenda" in (info.get("pdf_meta", {}).get("Title") or "").lower():
            return "not a score report (scan; PDF title 'Allen Agenda')"
        return "no text layer"
    return "score report" if REPORT_TEXT.search(info["first_page_head"] + " ") else "not a score report"


def cdx(client, params):
    url = WB + "/cdx/search/cdx?" + "&".join(f"{k}={quote(str(v), safe='*:/,.')}" for k, v in params.items())
    status, content, _ = client.binary(url, ok=(200,))
    if status != 200:
        raise RuntimeError(f"CDX {url}: HTTP {status}")
    rows = json.loads(content or b"[]")
    return url, rows[1:] if rows else []


def wayback_location(client, ts, original):
    """Where a captured redirect pointed (Location header of the id_ replay)."""
    status, _, meta = client.binary(f"{WB}/web/{ts}id_/{original}", ok=(200, 301, 302, 404),
                                    allow_redirects=False)
    loc = meta.get("location") or ""
    m = re.search(r"/ArchiveCenter/ViewFile/Item/(\d+)", loc)
    return status, loc, int(m.group(1)) if m else None


def main():
    client = Client()
    robots = Robots(client)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "robots": robots.log,
                "city": [], "wayback": {}}

    # ---------------------------------------------------------------- city-hosted reports
    for src in CITY_REPORTS:
        if not robots.allowed(src["url"]):
            manifest["city"].append({**src, "result": "skipped: disallowed by robots.txt"})
            continue
        status, content, meta = client.binary(src["url"], ok=(200, 404))
        if status == 406:  # the city's server sometimes answers 406 once; the cached retry is a new request
            status, content, meta = client.binary(src["url"], ok=(200, 404), refresh=True)
        rec = {**src, "status": status, "bytes": meta["bytes"], "sha256": meta["sha256"],
               "last_modified": meta.get("last_modified")}
        if status == 200:
            info = pdf_info(content)
            rec.update(info, classification=classify(info, None, content), file=f"pdfs/{src['name']}.pdf")
            (PDF_DIR / f"{src['name']}.pdf").write_bytes(content)
        manifest["city"].append(rec)
        print(src["name"], status, rec.get("classification"), flush=True)

    # ---------------------------------------------------------------- Wayback: category redirects
    wb = manifest["wayback"]
    if not robots.allowed(WB + "/web/2020/https://www.cityofallen.org/ArchiveCenter/ViewFile/Item/2148"):
        print("web.archive.org disallowed by robots.txt; stopping")
        (RAW / "pdf_manifest.json").write_text(json.dumps(manifest, indent=1))
        return 1
    url, amid = cdx(client, {"url": "cityofallen.org/Archive.aspx", "matchType": "prefix", "output": "json",
                             "fl": "timestamp,original,statuscode", "filter": "original:.*AMID=74.*"})
    wb["cdx_amid74"] = {"query": url, "captures": len(amid)}
    items = defaultdict(lambda: {"sources": set()})
    redirects = []
    for ts, original, st in amid:
        if "Type=Recent" not in original:
            continue
        status, loc, item = wayback_location(client, ts, original)
        redirects.append({"timestamp": ts, "original": original, "cdx_status": st, "replay_status": status,
                          "location": loc, "item": item})
        if item:
            items[item]["sources"].add(f"AMID=74 Type=Recent capture {ts}")
    wb["amid74_recent_redirects"] = redirects
    print("Type=Recent redirects ->", sorted(i for i in items), flush=True)

    # 2014 listing of the category page: monthly items 2010-2014
    listing_2014 = [r for r in amid if r[0].startswith("2014") and "Type=" not in r[1] and r[2] == "200"]
    listed = []
    for ts, original, _ in listing_2014[:1]:
        status, content, _ = client.binary(f"{WB}/web/{ts}id_/{original}", ok=(200,))
        html = content.decode("utf-8", "replace")
        for m in re.finditer(r"Archive\.aspx\?ADID=(\d+)\"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{3,60})", html):
            listed.append((int(m.group(1)), re.sub(r"\s+", " ", m.group(2)).strip()))
        wb["amid74_listing_2014"] = {"capture": f"{WB}/web/{ts}/{original}", "items": listed}
    for item, label in listed:
        items[item]["sources"].add(f"AMID=74 listing 2014 ({label})")
        items[item]["label_2014"] = label

    # ---------------------------------------------------------------- Wayback: all archived items
    url, caps = cdx(client, {"url": "cityofallen.org/ArchiveCenter/ViewFile/Item/", "matchType": "prefix",
                             "output": "json", "fl": "timestamp,original,statuscode,mimetype,length"})
    wb["cdx_items"] = {"query": url, "captures": len(caps)}
    by_item = defaultdict(list)
    for ts, original, st, mime, ln in caps:
        m = re.search(r"/Item/(\d+)$", original)
        if m:
            by_item[int(m.group(1))].append({"timestamp": ts, "original": original, "status": st, "mime": mime,
                                             "length": int(ln) if ln.isdigit() else None})
    for i, cs in by_item.items():
        pdf = [c for c in cs if c["status"] == "200" and c["mime"] == "application/pdf"]
        if i >= MIN_ITEM and pdf and CDX_MIN <= min(c["length"] or 0 for c in pdf) <= CDX_MAX:
            items[i]["sources"].add(f"candidate: item >= {MIN_ITEM}, PDF capture {CDX_MIN // 1000}-"
                                    f"{CDX_MAX // 1000} KB")
    wb["items_in_cdx"] = len(by_item)
    wb["pdf_items_ge_min"] = sum(1 for i, cs in by_item.items() if i >= MIN_ITEM and any(
        c["status"] == "200" and c["mime"] == "application/pdf" for c in cs))

    # ---------------------------------------------------------------- download + classify
    checked = []
    for n, item in enumerate(sorted(items), 1):
        cs = by_item.get(item, [])
        pdf_caps = sorted([c for c in cs if c["status"] == "200" and c["mime"] == "application/pdf"],
                          key=lambda c: c["timestamp"])
        rec = {"item": item, "sources": sorted(items[item]["sources"]), "label_2014": items[item].get("label_2014"),
               "captures": len(cs)}
        cap = pdf_caps[0] if pdf_caps else None
        if cap is None:
            # only redirects captured: follow the first one to see whether its target is archived
            red = sorted([c for c in cs if c["status"] in ("301", "302")], key=lambda c: c["timestamp"])
            if red:
                st, loc, _ = wayback_location(client, red[0]["timestamp"], red[0]["original"])
                rec["redirect_capture"] = {"timestamp": red[0]["timestamp"], "location": loc, "status": st}
                if loc:
                    target = loc if loc.startswith("http") else WB + loc
                    status, content, meta = client.binary(target, ok=(200, 404))
                    rec["redirect_target_status"] = status
                    if status == 200 and content[:4] == b"%PDF":
                        cap = {"timestamp": red[0]["timestamp"], "original": target, "direct": True}
            if cap is None:
                rec["classification"] = "no archived PDF"
                checked.append(rec)
                continue
        src = cap["original"] if cap.get("direct") else f"{WB}/web/{cap['timestamp']}id_/{cap['original']}"
        status, content, meta = client.binary(src, ok=(200, 404))
        rec.update({"url": src, "status": status, "bytes": meta["bytes"], "sha256": meta["sha256"],
                    "capture_timestamp": cap["timestamp"]})
        if status == 200:
            info = pdf_info(content)
            rec.update(info)
            rec["classification"] = classify(info, item, content)
            if rec["classification"] == "no text layer" and "Type=Recent" in " ".join(rec["sources"]):
                rec["classification"] = "score report (no text layer)"  # the category's own newest item
            if rec["classification"].startswith("score report"):
                name = f"allen_archivecenter_item{item}"
                (PDF_DIR / f"{name}.pdf").write_bytes(content)
                rec["file"] = f"pdfs/{name}.pdf"
        else:
            rec["classification"] = f"HTTP {status}"
        checked.append(rec)
        if n % 20 == 0 or rec["classification"].startswith("score report"):
            print(f"  [{n}/{len(items)}] item {item}: {rec['classification']} {rec.get('first_page_head', '')[:70]}",
                  flush=True)
    wb["checked"] = checked
    wb["score_reports"] = [c["item"] for c in checked if c["classification"].startswith("score report")]
    manifest["http"] = client.stats
    (RAW / "pdf_manifest.json").write_text(json.dumps(manifest, indent=1, default=sorted))
    print("score reports:", wb["score_reports"])
    print("http:", client.stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
