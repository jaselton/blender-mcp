"""Download the City of McKinney's food-inspection data from its ArcGIS service.

McKinney publishes inspections through an ArcGIS map service (the same data
behind the city's "Restaurant Scores" map and its open-data site):

    https://maps.mckinneytexas.org/mckinney/rest/services/MapServices/
        FoodPermitsAndInspections/MapServer

Layers 0-3 are permits (food establishments, mobile/temporary vendors, trades
day and farmers market, schools and daycares), layer 4 is every inspection
with its 47 item scores, and table 5 lists the inspection-report PDFs attached
to permits. Every layer is paged in OBJECTID order and saved whole, with the
service's field definitions, under data/raw/. Attachments are downloaded into
a cache directory (not committed: several GB) for the build step to read.

Usage:
    python fetch.py                  # layers + attachment list
    python fetch.py --attachments    # also download every attachment PDF
"""

import argparse
import gzip
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

SERVICE = ("https://maps.mckinneytexas.org/mckinney/rest/services/MapServices/"
           "FoodPermitsAndInspections/MapServer")
LAYERS = {0: "permits", 1: "mobile_temporary", 2: "trades_day_farmers_market",
          3: "schools_daycares", 4: "inspections", 5: "attachments"}
PAGE = 1000  # below the service's maxRecordCount of 2000
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

HERE = Path(__file__).resolve().parent
RAW = HERE / "data" / "raw"
CACHE = Path(os.environ.get("MCKINNEY_CACHE", HERE / ".cache"))

session = requests.Session()
session.headers["User-Agent"] = UA


def get(url, params=None, stream=False, tries=5):
    for attempt in range(tries):
        try:
            r = session.get(url, params=params, timeout=120, stream=stream)
            if r.status_code == 200:
                return r
            err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            err = repr(e)
        time.sleep(2 ** attempt)
    raise RuntimeError(f"{url} failed after {tries} tries: {err}")


def query(layer, **params):
    base = {"f": "json", "where": "1=1", "outFields": "*", "returnGeometry": "false"}
    d = get(f"{SERVICE}/{layer}/query", {**base, **params}).json()
    if "error" in d:
        raise RuntimeError(f"layer {layer}: {d['error']}")
    return d


def fetch_layer(layer):
    meta = get(f"{SERVICE}/{layer}", {"f": "json"}).json()
    count = query(layer, returnCountOnly="true")["count"]
    out_fields = "*"
    if layer == 5:  # skip the BLOB column; PDFs come from the attachments endpoint
        out_fields = ",".join(f["name"] for f in meta["fields"] if f["type"] != "esriFieldTypeBlob")
    rows, offset = [], 0
    while True:
        d = query(layer, outFields=out_fields, orderByFields="OBJECTID" if layer != 5 else "ATTACHMENTID",
                  resultOffset=offset, resultRecordCount=PAGE)
        batch = [f["attributes"] for f in d.get("features", [])]
        rows.extend(batch)
        offset += len(batch)
        if not batch or (not d.get("exceededTransferLimit") and len(batch) < PAGE):
            break
    key = "ATTACHMENTID" if layer == 5 else "OBJECTID"
    ids = [r[key] for r in rows]
    if len(rows) != count or len(set(ids)) != len(ids):
        raise RuntimeError(f"layer {layer}: got {len(rows)} rows ({len(set(ids))} distinct), service says {count}")
    return meta, rows


def attachment_path(row):
    return CACHE / "attachments" / f"{row['REL_OBJECTID']}_{row['ATTACHMENTID']}.pdf"


def download_attachment(row):
    path = attachment_path(row)
    if path.exists() and path.stat().st_size == row["DATA_SIZE"]:
        return path, "cached"
    url = f"{SERVICE}/0/{row['REL_OBJECTID']}/attachments/{row['ATTACHMENTID']}"
    r = get(url, stream=True)
    tmp = path.with_suffix(".part")
    with open(tmp, "wb") as fh:
        for chunk in r.iter_content(1 << 16):
            fh.write(chunk)
    if tmp.stat().st_size != row["DATA_SIZE"]:
        raise RuntimeError(f"{url}: {tmp.stat().st_size} bytes, expected {row['DATA_SIZE']}")
    tmp.replace(path)
    return path, "downloaded"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--attachments", action="store_true", help="download attachment PDFs")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary = {"service": SERVICE, "fetched_at": fetched_at, "layers": {}}
    for layer, name in LAYERS.items():
        meta, rows = fetch_layer(layer)
        with gzip.open(RAW / f"{name}.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, sort_keys=True)
        (RAW / f"{name}.schema.json").write_text(json.dumps(
            {k: meta.get(k) for k in ("name", "type", "description", "fields", "relationships", "maxRecordCount")},
            indent=1, ensure_ascii=False))
        summary["layers"][name] = {"layer": layer, "rows": len(rows)}
        print(f"layer {layer} {name}: {len(rows)} rows")

    if args.attachments:
        rows = json.load(gzip.open(RAW / "attachments.json.gz", "rt", encoding="utf-8"))
        (CACHE / "attachments").mkdir(parents=True, exist_ok=True)
        done = {"cached": 0, "downloaded": 0}
        with ThreadPoolExecutor(args.workers) as ex:
            for i, (path, how) in enumerate(ex.map(download_attachment, rows), 1):
                done[how] += 1
                if i % 250 == 0:
                    print(f"attachments {i}/{len(rows)} {done}", flush=True)
        manifest = []
        for row in rows:
            p = attachment_path(row)
            manifest.append({"ATTACHMENTID": row["ATTACHMENTID"], "REL_OBJECTID": row["REL_OBJECTID"],
                             "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size})
        (RAW / "attachments_manifest.json").write_text(json.dumps(manifest, indent=0))
        summary["attachments"] = {"files": len(rows), **done}
        print("attachments:", done)

    (RAW / "fetch_meta.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
