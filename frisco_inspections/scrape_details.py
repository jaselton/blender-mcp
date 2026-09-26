"""Download and parse every inspection's PDF and web page, and every
establishment's permit page, for the rows in data/raw/listing.jsonl.

Raw downloads are cached under $FRISCO_CACHE (default ./.cache) so the run is
resumable. Parsed output goes to data/raw/:

  inspections_parsed.jsonl  one record per inspection: listing row, parsed PDF,
                            parsed web page, and any fetch/parse errors
  permits_parsed.jsonl      one record per establishment (permit page)

Usage: python scrape_details.py [--workers 4] [--parse-workers N] [--limit N]
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

import client
from parse_html import parse_inspection_html, parse_permit_html
from parse_pdf import parse_pdf

RAW = Path(__file__).parent / "data" / "raw"


def listing_scraped_at():
    meta = json.loads((RAW / "listing_meta.json").read_text())
    return dt.datetime.fromisoformat(meta["scraped_at"]).timestamp()


def download(kind, key, stale_before=None):
    try:
        if kind == "pdf":
            client.get_inspection_pdf_path(key)
        elif kind == "html":
            client.get_inspection_html(key)
        else:
            client.get_permit_html(key, stale_before=stale_before)
        return kind, key, None
    except Exception as e:  # recorded, not fatal: the run reports every failure
        return kind, key, str(e)


HERE = Path(__file__).parent
# Parsed results are cached per inspection, keyed by the parser source, so a
# re-run only re-parses when the parser changes or a file was re-downloaded.
PARSER_VERSION = hashlib.sha1(
    b"".join((HERE / f).read_bytes() for f in ("parse_pdf.py", "parse_html.py"))
).hexdigest()[:12]


def parse_one(row, cache):
    iid = row["inspectionID"]
    pdf_path = Path(cache) / "pdf" / f"{iid}.pdf"
    html_path = Path(cache) / "html" / f"{iid}.html"
    memo = Path(cache) / "parsed" / PARSER_VERSION / f"{iid}.json"
    stamp = [p.stat().st_mtime if p.exists() else None for p in (pdf_path, html_path)]
    if memo.exists():
        saved = json.loads(memo.read_text(encoding="utf-8"))
        if saved["stamp"] == stamp:
            saved["rec"]["listing"] = row
            return saved["rec"]

    rec = {"inspection_id": iid, "listing": row, "pdf": None, "html": None, "errors": []}
    if pdf_path.exists():
        try:
            rec["pdf"] = parse_pdf(pdf_path)
        except Exception as e:
            rec["errors"].append(f"pdf: {type(e).__name__}: {e}")
            rec["errors"].append(traceback.format_exc(limit=3))
    else:
        rec["errors"].append("pdf: not downloaded")
    if html_path.exists():
        try:
            rec["html"] = parse_inspection_html(html_path.read_text(encoding="utf-8"))
        except Exception as e:
            rec["errors"].append(f"html: {type(e).__name__}: {e}")
    else:
        rec["errors"].append("html: not downloaded")
    memo.parent.mkdir(parents=True, exist_ok=True)
    memo.write_text(json.dumps({"stamp": stamp, "rec": rec}, ensure_ascii=False), encoding="utf-8")
    return rec


def run_downloads(jobs, workers):
    failures = []
    stale_before = listing_scraped_at()
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(download, k, key, stale_before) for k, key in jobs]
        for n, f in enumerate(as_completed(futs), 1):
            kind, key, err = f.result()
            if err:
                failures.append((kind, key, err))
            if n % 250 == 0:
                print(f"downloaded {n}/{len(jobs)} ({len(failures)} failed)", file=sys.stderr)
    print(f"downloads done: {len(jobs)} jobs, {len(failures)} failed", file=sys.stderr)
    for f in failures:
        print("FAILED", *f, file=sys.stderr)


def rows_from_permit_pages(rows, permit_recs):
    """Listing-shaped rows for inspections that appear on a permit page but not
    in the listing.

    The listing API returns only an establishment's latest inspection in the
    queried range, so two inspections of one establishment on the same day
    would leave one out of even a one-day query. Establishment fields are
    copied from that permit's listing rows; inspection fields come from the
    permit page (and are checked against the PDF in build.py).
    """
    listed = {r["inspectionID"] for r in rows}
    by_permit = {}
    for r in rows:
        by_permit.setdefault(r["permitID"], r)
    extra = []
    for prec in permit_recs:
        page = prec.get("page") or {}
        for insp in page.get("inspections", []):
            iid = insp["inspection_id"]
            if not iid or iid in listed:
                continue
            base = dict(by_permit[prec["permit_id"]])
            itype, _, purpose = (insp["type_purpose"] or "").partition(" | ")
            date = dt.datetime.strptime(insp["date_text"], "%B %d, %Y").date()
            score = (insp["score"] or "").strip()
            base.update({
                "inspectionID": iid,
                "inspectionDate": f"{date.isoformat()}T00:00:00.000Z",
                "inspectionType": itype.strip(),
                "purpose": purpose.strip(),
                "score": int(score) if score.isdigit() else None,
                "comments": "",
                "timein": None,
                "_source": "permit_page",
            })
            extra.append(base)
            listed.add(iid)
    return extra


def parse_permits(permits, cache):
    recs = []
    for p in permits:
        path = Path(cache) / "permit" / f"{p}.html"
        rec = {"permit_id": p, "page": None, "error": None}
        try:
            rec["page"] = parse_permit_html(path.read_text(encoding="utf-8"))
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
        recs.append(rec)
    return recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--parse-workers", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--download-only", action="store_true")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(RAW / "listing.jsonl", encoding="utf-8")]
    if args.limit:
        rows = rows[: args.limit]
    permits = sorted({r["permitID"] for r in rows})
    cache = str(client.cache_dir())

    if not args.skip_download:
        # PDFs are the primary source; permit and web pages are cross-checks.
        run_downloads(
            [("pdf", r["inspectionID"]) for r in rows]
            + [("permit", p) for p in permits]
            + [("html", r["inspectionID"]) for r in rows],
            args.workers,
        )

    permit_recs = parse_permits(permits, cache)
    with open(RAW / "permits_parsed.jsonl", "w", encoding="utf-8") as fh:
        for rec in permit_recs:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"parsed {len(permits)} permit pages", file=sys.stderr)

    extra = rows_from_permit_pages(rows, permit_recs)
    print(f"{len(extra)} inspections on permit pages missing from the listing", file=sys.stderr)
    if extra and not args.skip_download:
        run_downloads(
            [("pdf", r["inspectionID"]) for r in extra] + [("html", r["inspectionID"]) for r in extra],
            args.workers,
        )
    rows = rows + extra

    if args.download_only:
        return

    out = RAW / "inspections_parsed.jsonl"
    n_err = 0
    with ProcessPoolExecutor(args.parse_workers) as ex, open(out, "w", encoding="utf-8") as fh:
        futs = {ex.submit(parse_one, r, cache): r["inspectionID"] for r in rows}
        recs = []
        for n, f in enumerate(as_completed(futs), 1):
            rec = f.result()
            n_err += bool(rec["errors"])
            recs.append(rec)
            if n % 250 == 0:
                print(f"parsed {n}/{len(rows)} ({n_err} with errors)", file=sys.stderr)
        recs.sort(key=lambda r: (r["listing"]["inspectionDate"], r["inspection_id"]))
        for rec in recs:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"parsed {len(rows)} inspections, {n_err} with errors -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
