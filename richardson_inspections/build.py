"""Build Richardson's score-level inspection history from the raw files fetch.py saved.

Inputs (data/raw/, see fetch_meta.json for URLs, capture times and sha256):
    wayback/CORScores_<ts>.json   Wayback captures of the city's JSON export
    socrata/yf9v-pthu.json        LIVES open-data feed, 2015-2018
    wayback/weekly_listings_in_gap.json  two archived weekly listings from 2019 (inside the gap)
    wayback/scores_page_text.json the city's description of the 0-100 scale
    wayback/*inventory*.json      CDX listings of other archived HealthTrak pages
    municode_sec_10-126.json      Richardson Code sec. 10-126

Outputs:
    data/inspections.csv     one row per report key (Wayback exports and weekly listings) or
                             LIVES inspection
    data/establishments.csv  one row per permit
    data/validation.json     integrity checks, counts, capture overlaps, re-keyed /
                             vanished records, score distribution, gaps

Only the standard library is used, so the build runs anywhere without network
access.

Each Wayback capture is a snapshot of the city's export on one day. The export
holds a rolling ~3-year window of inspections, and only for permits listed at
the time. Captures are compared in time order: a report key that is in one
capture but not the next is classified as
    aged_out            older than anything in the next capture (rolling window)
    rekeyed             the same inspection reappears under a different key
                        (permit, score or date edited); see superseded_by
    permit_not_listed   the whole permit is gone from the next export
    vanished            the permit is still listed and the date is inside the
                        next capture's window, but the record is gone
and a key that is new in a capture but dated before the previous export was
made is "backfilled" (entered late, or re-keyed; see replaces); one dated on or
after that export day is "new", and one without a usable date "undated".
"""

import csv
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from urllib.parse import urljoin

from common import (DATA, LIVES_DATASET, RAW, SEC_10_126_URL, GOVQA_URL, key_from_report_url, make_key,
                    parse_mdY, read_json, sha256, ts_to_datetime, utcnow, wayback_url)

SOCRATA_ROW_URL = "https://lives.data.socrata.com/resource/" + LIVES_DATASET + ".json?inspection_id={}"

INSPECTION_COLUMNS = [
    "report_key", "permit", "establishment_name", "address", "inspection_date", "date_as_published",
    "score_100", "demerits_equiv", "scored", "score_band", "source", "source_url",
    "first_seen_capture", "last_seen_capture", "n_captures", "captures", "presence",
    "first_seen_status", "later_status", "superseded_by", "replaces", "rekey_change",
    "date_flag", "rows_with_this_key", "names_seen", "city_report_url",
    "lives_inspection_id", "key_constructed",
]


def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def band(score):
    if score is None:
        return "unscored"
    if score >= 90:
        return "90-100 excellent"
    if score >= 80:
        return "80-89 good"
    if score >= 70:
        return "70-79 acceptable"
    if score >= 60:
        return "60-69 marginal"
    return "below 60"


BANDS = ["90-100 excellent", "80-89 good", "70-79 acceptable", "60-69 marginal", "below 60", "unscored"]

# source prefixes in inspections.csv
SRC_EXPORT = "wayback:"            # Wayback capture of the CORScores.json export
SRC_LISTING = "wayback_listing:"   # Wayback capture of a weekly listing page (2019, inside the gap)
SRC_LIVES = "socrata:"             # Socrata LIVES feed


def is_export(r):
    return r["source"].startswith(SRC_EXPORT)


def is_listing(r):
    return r["source"].startswith(SRC_LISTING)


def is_lives(r):
    return r["source"].startswith(SRC_LIVES)


def to_score(s):
    s = ("" if s is None else str(s)).strip()
    if s == "":
        return None
    v = float(s)
    return int(v) if v == int(v) else v


def name_stem(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())[:5]


# ----------------------------------------------------------------------------- integrity
def check_integrity(meta):
    bad, checked = [], 0
    for rel, info in meta["files"].items():
        p = RAW / rel
        checked += 1
        if not p.exists() or sha256(p.read_bytes()) != info["sha256"]:
            bad.append(rel)
    caps = []
    for c in meta["corscores_captures"]:
        b = (RAW / c["file"]).read_bytes()
        caps.append({"timestamp": c["timestamp"], "sha256_ok": sha256(b) == c["sha256"],
                     "digest_matches_cdx": c["digest_matches_cdx"]})
    return {"files_checked": checked, "sha256_mismatches": bad, "captures": caps}


# ----------------------------------------------------------------------------- captures
def load_capture(c):
    d = json.loads((RAW / c["file"]).read_text(encoding="utf-8"), strict=False)
    created = datetime.strptime(d["created"], "%m/%d/%Y").date()
    rows, blocks, est_blocks = [], 0, 0
    for est in d["establishments"]:
        est_blocks += 1
        for addr in est.get("addresses", []):
            blocks += 1
            for s in addr.get("scores", []):
                permit = norm(s.get("permit"))
                score_raw = norm(s.get("score"))
                date_raw = norm(s.get("date"))
                url = norm(s.get("report"))
                key = key_from_report_url(url) or make_key(permit, score_raw, date_raw)
                rows.append({"key": key, "permit": permit, "name": norm(est.get("name")),
                             "address": norm(addr.get("address")), "score": to_score(score_raw),
                             "score_raw": score_raw, "date_raw": date_raw, "date": parse_mdY(date_raw),
                             "report_url": url,
                             "key_matches_fields": key == make_key(permit, score_raw, date_raw)})
    return {"ts": c["timestamp"], "original": c["original"], "created": created, "title": d.get("name"),
            "rows": rows, "blocks": blocks, "establishment_blocks": est_blocks,
            "wayback_url": wayback_url(c["timestamp"], c["original"])}


def capture_stats(cap):
    rows = cap["rows"]
    keys = Counter(r["key"] for r in rows)
    dates = sorted(r["date"] for r in rows if r["date"])
    scores = [r["score"] for r in rows if r["score"] is not None]
    return {
        "timestamp": cap["ts"], "captured_at_utc": ts_to_datetime(cap["ts"]).isoformat() + "Z",
        "original_url": cap["original"], "wayback_url": cap["wayback_url"],
        "export_title": cap["title"], "export_created": cap["created"].isoformat(),
        "establishment_blocks": cap["establishment_blocks"], "name_address_blocks": cap["blocks"],
        "score_rows": len(rows), "unique_report_keys": len(keys),
        "duplicate_keys": {k: n for k, n in keys.items() if n > 1},
        "permits": len({r["permit"] for r in rows}),
        "rows_with_blank_permit": sum(1 for r in rows if not r["permit"]),
        "names": len({r["name"] for r in rows}),
        "scored_rows": len(scores), "unscored_rows": len(rows) - len(scores),
        "rows_with_unusable_date": [{"key": r["key"], "date_as_published": r["date_raw"], "name": r["name"]}
                                    for r in rows if not r["date"]],
        "rows_dated_after_export": [{"key": r["key"], "date": r["date"].isoformat(), "name": r["name"]}
                                    for r in rows if r["date"] and r["date"] > cap["created"]],
        "rows_whose_key_differs_from_fields": sum(1 for r in rows if not r["key_matches_fields"]),
        "earliest_date": dates[0].isoformat() if dates else None,
        "latest_date": dates[-1].isoformat() if dates else None,
        "window_days_before_export": (cap["created"] - dates[0]).days if dates else None,
    }


def compare(a, b, capA, capB):
    """Classify keys that leave between capture A and the next capture B, and keys new in B."""
    rowsA = {r["key"]: r for r in capA["rows"]}
    rowsB = {r["key"]: r for r in capB["rows"]}
    permitsB = {r["permit"] for r in capB["rows"]}
    permitsA = {r["permit"] for r in capA["rows"]}
    datesB = [r["date"] for r in capB["rows"] if r["date"]]
    startB = min(datesB)
    gone = [rowsA[k] for k in sorted(set(rowsA) - set(rowsB))]
    new = [rowsB[k] for k in sorted(set(rowsB) - set(rowsA))]
    # a key new in B that is dated on/before A's export date should already have been in A
    unexplained_new = [r for r in new if r["date"] is None or r["date"] <= capA["created"]]

    by_permit_date = defaultdict(list)
    by_permit_score = defaultdict(list)
    by_date_score = defaultdict(list)
    for r in unexplained_new:
        by_permit_date[(r["permit"], r["date_raw"])].append(r)
        by_permit_score[(r["permit"], r["score_raw"])].append(r)
        by_date_score[(r["date_raw"], r["score_raw"])].append(r)

    used, gone_class, matches = set(), {}, []
    candidates_in_window = [g for g in gone if not (g["date"] and g["date"] < startB)]
    for g in gone:
        if g["date"] and g["date"] < startB:
            gone_class[g["key"]] = "aged_out"
    for g in candidates_in_window:
        match, change = None, None
        c = [r for r in by_permit_date[(g["permit"], g["date_raw"])] if r["key"] not in used]
        if len(c) == 1:
            match, change = c[0], "score"
        if not match:
            c = [r for r in by_date_score[(g["date_raw"], g["score_raw"])] if r["key"] not in used
                 and r["permit"] != g["permit"]
                 and (name_stem(r["name"]) == name_stem(g["name"]) or norm(r["address"]).lower() == norm(g["address"]).lower())]
            if len(c) == 1:
                match, change = c[0], "permit"
        if not match:
            c = [r for r in by_permit_score[(g["permit"], g["score_raw"])] if r["key"] not in used]
            if len(c) == 1:
                match, change = c[0], "date"
        if match:
            used.add(match["key"])
            gone_class[g["key"]] = "rekeyed"
            matches.append({"old_key": g["key"], "new_key": match["key"], "change": change,
                            "old_name": g["name"], "new_name": match["name"],
                            "old_address": g["address"], "new_address": match["address"],
                            "last_capture_with_old_key": a, "first_capture_with_new_key": b})
        elif g["permit"] not in permitsB:
            gone_class[g["key"]] = "permit_not_listed"
        else:
            gone_class[g["key"]] = "vanished"

    new_class = {}
    for r in new:
        if r["key"] in used:
            new_class[r["key"]] = "rekey_target"
        elif r["date"] is None:
            new_class[r["key"]] = "undated"
        elif r["date"] >= capA["created"]:   # on/after the day A was exported: entered later
            new_class[r["key"]] = "new"
        elif r["permit"] not in permitsA:
            new_class[r["key"]] = "backfilled_permit_newly_listed"
        else:
            new_class[r["key"]] = "backfilled"
    lags = sorted((capA["created"] - r["date"]).days for r in new
                  if new_class[r["key"]].startswith("backfilled"))

    # vanished records, grouped by permit: did the later export drop the permit's whole earlier history?
    vanished_by_permit = []
    gone_v = defaultdict(list)
    for g in gone:
        if gone_class[g["key"]] == "vanished":
            gone_v[g["permit"]].append(g)
    for p, gs in sorted(gone_v.items()):
        later = sorted(r["date"] for r in capB["rows"] if r["permit"] == p and r["date"])
        earlier_kept = [d for d in later if d <= capA["created"]]
        vanished_by_permit.append({
            "permit": p, "name": gs[0]["name"], "records_vanished": len(gs),
            "vanished_dates": [min(g["date"] for g in gs).isoformat(), max(g["date"] for g in gs).isoformat()],
            "records_of_permit_in_later_capture": len(later),
            "later_capture_dates": [later[0].isoformat(), later[-1].isoformat()] if later else None,
            "pattern": ("all in-window history before the earlier export was dropped" if not earlier_kept
                        else "individual records dropped")})
    summary = {
        "from": a, "to": b, "keys_from": len(rowsA), "keys_to": len(rowsB),
        "window_start_to": startB.isoformat(),
        "keys_gone": len(gone), "gone_by_class": dict(Counter(gone_class.values())),
        "keys_new": len(new), "new_by_class": dict(Counter(new_class.values())),
        "backfilled_days_before_previous_export": ({"min": lags[0], "median": statistics.median(lags),
                                                    "max": lags[-1], "n": len(lags),
                                                    "within_7_days": sum(1 for x in lags if x <= 7)}
                                                   if lags else None),
        "rekeyed": matches,
        "vanished": [{"key": g["key"], "name": g["name"], "address": g["address"],
                      "date": g["date"].isoformat() if g["date"] else g["date_raw"], "score": g["score"]}
                     for g in gone if gone_class[g["key"]] == "vanished"],
        "vanished_by_permit": vanished_by_permit,
        "permits_no_longer_listed": len(permitsA - permitsB),
        "permits_newly_listed": len(permitsB - permitsA),
    }
    return gone_class, new_class, matches, summary


# ----------------------------------------------------------------------------- main
def main():
    meta = read_json(RAW / "fetch_meta.json")
    integrity = check_integrity(meta)
    if integrity["sha256_mismatches"]:
        sys.exit(f"raw files changed since fetch: {integrity['sha256_mismatches']}")

    caps = sorted((load_capture(c) for c in meta["corscores_captures"]), key=lambda c: c["ts"])
    order = [c["ts"] for c in caps]
    by_ts = {c["ts"]: c for c in caps}

    # ---- union of captures, keyed by report key
    union = {}
    for c in caps:
        per_key = Counter(r["key"] for r in c["rows"])
        for r in c["rows"]:
            u = union.setdefault(r["key"], {"key": r["key"], "captures": [], "rows": {}, "max_rows": 0})
            if c["ts"] not in u["captures"]:
                u["captures"].append(c["ts"])
            u["rows"][c["ts"]] = r
            u["max_rows"] = max(u["max_rows"], per_key[r["key"]])

    transitions, gone_cls, new_cls, rekeys = [], {}, {}, []
    for i in range(len(caps) - 1):
        g, n, m, s = compare(order[i], order[i + 1], caps[i], caps[i + 1])
        transitions.append(s)
        for k, v in g.items():
            gone_cls[(k, order[i])] = v
        for k, v in n.items():
            new_cls[(k, order[i + 1])] = v
        rekeys.extend(m)
    superseded = {m["old_key"]: m for m in rekeys}
    replaces = {m["new_key"]: m for m in rekeys}

    rows_out = []
    for k, u in union.items():
        first, last = u["captures"][0], u["captures"][-1]
        r = u["rows"][last]
        names = sorted({x["name"] for x in u["rows"].values()})
        presence = "".join("1" if ts in u["captures"] else "0" for ts in order)
        later = "in_latest_capture" if last == order[-1] else gone_cls.get((k, last), "")
        first_status = "in_first_capture" if first == order[0] else new_cls.get((k, first), "")
        flag = ""
        if not r["date"]:
            flag = "blank_date" if not r["date_raw"] else "invalid_date"
        elif any(x["date"] and x["date"] > by_ts[ts]["created"] for ts, x in u["rows"].items()):
            flag = "dated_after_export"
        sc = r["score"]
        rows_out.append({
            "report_key": k, "permit": r["permit"], "establishment_name": r["name"], "address": r["address"],
            "inspection_date": r["date"].isoformat() if r["date"] else "", "date_as_published": r["date_raw"],
            "score_100": "" if sc is None else sc, "demerits_equiv": "" if sc is None else 100 - sc,
            "scored": int(sc is not None), "score_band": band(sc),
            "source": f"wayback:{first}", "source_url": by_ts[first]["wayback_url"],
            "first_seen_capture": first, "last_seen_capture": last, "n_captures": len(u["captures"]),
            "captures": ";".join(u["captures"]), "presence": presence,
            "first_seen_status": first_status, "later_status": later,
            "superseded_by": superseded[k]["new_key"] if k in superseded else "",
            "replaces": replaces[k]["old_key"] if k in replaces else "",
            "rekey_change": (superseded.get(k) or replaces.get(k) or {}).get("change", ""),
            "date_flag": flag, "rows_with_this_key": u["max_rows"],
            "names_seen": " | ".join(names) if len(names) > 1 else "",
            "city_report_url": r["report_url"], "lives_inspection_id": "", "key_constructed": 0,
        })

    # ---- Socrata LIVES
    lives = read_json(RAW / "socrata" / f"{LIVES_DATASET}.json")
    lives_keys = Counter()
    lives_rows = []
    for x in lives:
        d = datetime.fromisoformat(x["inspection_date"][:19]).date() if x.get("inspection_date") else None
        sc = to_score(x.get("inspection_score"))
        permit = norm(x.get("business_id"))
        mdY = d.strftime("%m/%d/%Y") if d else ""
        key = make_key(permit, "" if sc is None else sc, mdY)
        lives_keys[key] += 1
        addr = ", ".join(p for p in [norm(x.get("business_address")), norm(x.get("business_city")),
                                     " ".join(p for p in [norm(x.get("business_state")),
                                                          norm(x.get("business_postal_code"))] if p)] if p)
        lives_rows.append({
            "report_key": key, "permit": permit, "establishment_name": norm(x.get("business_name")),
            "address": addr, "inspection_date": d.isoformat() if d else "",
            "date_as_published": x.get("inspection_date", ""),
            "score_100": "" if sc is None else sc, "demerits_equiv": "" if sc is None else 100 - sc,
            "scored": int(sc is not None), "score_band": band(sc),
            "source": f"socrata:{LIVES_DATASET}", "source_url": SOCRATA_ROW_URL.format(x["inspection_id"]),
            "first_seen_capture": "", "last_seen_capture": "", "n_captures": "", "captures": "",
            "presence": "", "first_seen_status": "", "later_status": "", "superseded_by": "", "replaces": "",
            "rekey_change": "", "date_flag": "" if d else "blank_date", "rows_with_this_key": 0,
            "names_seen": "", "city_report_url": "", "lives_inspection_id": x["inspection_id"],
            "key_constructed": 1,
        })
    for r in lives_rows:
        r["rows_with_this_key"] = lives_keys[r["report_key"]]
    rows_out.extend(lives_rows)

    # ---- archived weekly listings captured inside the gap (2019)
    listing_rows, listing_stats, listing_skipped = [], [], []
    lpath = RAW / "wayback" / "weekly_listings_in_gap.json"
    listings = read_json(lpath) if lpath.exists() else {"captures": [], "gap_bounds_exclusive": None}
    other_keys = {r["report_key"] for r in rows_out}
    lunion = {}
    for c in sorted(listings["captures"], key=lambda c: c["timestamp"]):
        per_key = Counter(x["report_key"] for x in c["rows"])
        n_match = 0
        for x in c["rows"]:
            k = x["report_key"]
            parts = k.split("~")
            if len(parts) != 3:
                raise SystemExit(f"unexpected report key in weekly listing {c['timestamp']}: {k!r}")
            permit, score_raw, mdY = (norm(p) for p in parts)
            n_match += norm(x["score"]) == score_raw
            u = lunion.setdefault(k, {"captures": [], "rows": [], "max_rows": 0, "listing": c})
            if c["timestamp"] not in u["captures"]:
                u["captures"].append(c["timestamp"])
            u["rows"].append(x)
            u["max_rows"] = max(u["max_rows"], per_key[k])
            u.update(permit=permit, score_raw=score_raw, mdY=mdY)
        dts = sorted(d for d in (parse_mdY(x["report_key"].split("~")[2]) for x in c["rows"]) if d)
        listing_stats.append({
            "timestamp": c["timestamp"], "wayback_url": c["wayback_url"], "digest_matches_cdx": c["digest_matches_cdx"],
            "week_ending_as_published": c["week_ending_as_published"], "rows": len(c["rows"]),
            "report_links_on_page": c["report_links_on_page"], "unique_report_keys": len(per_key),
            "duplicate_keys": {k: n for k, n in per_key.items() if n > 1},
            "score_cell_equals_score_in_key": n_match,
            "earliest_date": dts[0].isoformat() if dts else None, "latest_date": dts[-1].isoformat() if dts else None})
    for k, u in lunion.items():
        if k in other_keys:           # never expected: listing dates fall between the other sources
            listing_skipped.append(k)
            continue
        x, c = u["rows"][0], u["listing"]
        d = parse_mdY(u["mdY"])
        sc = to_score(u["score_raw"])
        names = sorted({norm(y["name"]) for y in u["rows"]})
        listing_rows.append({
            "report_key": k, "permit": u["permit"], "establishment_name": norm(x["name"]),
            "address": norm(x["address"]), "inspection_date": d.isoformat() if d else "",
            "date_as_published": u["mdY"],
            "score_100": "" if sc is None else sc, "demerits_equiv": "" if sc is None else 100 - sc,
            "scored": int(sc is not None), "score_band": band(sc),
            "source": SRC_LISTING + u["captures"][0], "source_url": c["wayback_url"],
            "first_seen_capture": "", "last_seen_capture": "", "n_captures": "", "captures": "",
            "presence": "", "first_seen_status": "", "later_status": "", "superseded_by": "", "replaces": "",
            "rekey_change": "", "date_flag": "" if d else ("blank_date" if not u["mdY"] else "invalid_date"),
            "rows_with_this_key": u["max_rows"], "names_seen": " | ".join(names) if len(names) > 1 else "",
            "city_report_url": urljoin(c["original"], x["report_href"]), "lives_inspection_id": "",
            "key_constructed": 0,
        })
    rows_out.extend(listing_rows)
    rows_out.sort(key=lambda r: (r["inspection_date"] or "9999", r["permit"], r["report_key"], r["source"]))

    with open(DATA / "inspections.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=INSPECTION_COLUMNS)
        w.writeheader()
        w.writerows(rows_out)

    # ---- establishments
    counted = [r for r in rows_out if r["later_status"] != "rekeyed"]
    by_permit = defaultdict(list)
    for r in counted:
        by_permit[r["permit"]].append(r)
    listed_in = defaultdict(list)
    for c in caps:
        for p in sorted({r["permit"] for r in c["rows"]}):
            listed_in[p].append(c["ts"])
    est_rows = []
    for p, rs in sorted(by_permit.items(), key=lambda kv: (kv[0] == "", kv[0].zfill(10))):
        rs = sorted(rs, key=lambda r: (r["inspection_date"] or "0000", r["source"]))
        scored = [r for r in rs if r["scored"]]
        vals = [r["score_100"] for r in scored]
        dated = [r for r in rs if r["inspection_date"]]
        latest = dated[-1] if dated else rs[-1]
        latest_scored = [r for r in scored if r["inspection_date"]]
        est_rows.append({
            "permit": p, "establishment_name": latest["establishment_name"],
            "all_names": " | ".join(sorted({r["establishment_name"] for r in rs})),
            "address": latest["address"], "all_addresses": " | ".join(sorted({r["address"] for r in rs})),
            "first_inspection": dated[0]["inspection_date"] if dated else "",
            "last_inspection": dated[-1]["inspection_date"] if dated else "",
            "inspections": len(rs),
            "inspections_socrata": sum(1 for r in rs if is_lives(r)),
            "inspections_wayback": sum(1 for r in rs if is_export(r)),
            "inspections_weekly_listing": sum(1 for r in rs if is_listing(r)),
            "scored": len(scored), "unscored": len(rs) - len(scored),
            "mean_score": round(statistics.mean(vals), 2) if vals else "",
            "min_score": min(vals) if vals else "", "max_score": max(vals) if vals else "",
            "latest_score": latest_scored[-1]["score_100"] if latest_scored else "",
            "latest_score_date": latest_scored[-1]["inspection_date"] if latest_scored else "",
            "listed_in_captures": ";".join(listed_in.get(p, [])),
            "in_latest_capture": int(order[-1] in listed_in.get(p, [])),
            "in_socrata": int(any(is_lives(r) for r in rs)),
        })
    with open(DATA / "establishments.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(est_rows[0]))
        w.writeheader()
        w.writerows(est_rows)

    # ---- validation.json
    wb = [r for r in counted if is_export(r)]
    lv = [r for r in counted if is_lives(r)]
    ls = [r for r in counted if is_listing(r)]

    def per_year(rs):
        return dict(sorted(Counter(r["inspection_date"][:4] or "no date" for r in rs).items()))

    def dist(rs):
        vals = [r["score_100"] for r in rs if r["scored"]]
        hist = Counter(f"{int(v // 5 * 5)}-{int(v // 5 * 5 + 4)}" if v < 100 else "100" for v in vals)
        return {"rows": len(rs), "scored": len(vals), "unscored": len(rs) - len(vals),
                "min": min(vals) if vals else None, "max": max(vals) if vals else None,
                "mean": round(statistics.mean(vals), 2) if vals else None,
                "median": statistics.median(vals) if vals else None,
                "city_bands": {b: sum(1 for r in rs if r["score_band"] == b) for b in BANDS},
                "five_point_bins": dict(sorted(hist.items(), key=lambda kv: int(kv[0].split("-")[0])))}

    overlaps = []
    for i in range(len(caps)):
        for j in range(i + 1, len(caps)):
            A, B = caps[i], caps[j]
            ka, kb = {r["key"] for r in A["rows"]}, {r["key"] for r in B["rows"]}
            lo = max(min(r["date"] for r in A["rows"] if r["date"]), min(r["date"] for r in B["rows"] if r["date"]))
            hi = min(A["created"], B["created"])
            wa = {r["key"] for r in A["rows"] if r["date"] and lo <= r["date"] <= hi}
            wbk = {r["key"] for r in B["rows"] if r["date"] and lo <= r["date"] <= hi}
            overlaps.append({
                "a": A["ts"], "b": B["ts"], "keys_a": len(ka), "keys_b": len(kb), "both": len(ka & kb),
                "only_a": len(ka - kb), "only_b": len(kb - ka), "jaccard": round(len(ka & kb) / len(ka | kb), 4),
                "shared_date_range": [lo.isoformat(), hi.isoformat()],
                "in_shared_range": {"a": len(wa), "b": len(wbk), "both": len(wa & wbk), "only_a": len(wa - wbk),
                                    "only_b": len(wbk - wa)}})

    # months with no records, from the first record to the month of the newest export
    last_export = by_ts[order[-1]]["created"]
    months = Counter(r["inspection_date"][:7] for r in counted
                     if r["inspection_date"] and r["inspection_date"] <= last_export.isoformat())
    first_m, last_m = min(months), last_export.isoformat()[:7]
    y, m = int(first_m[:4]), int(first_m[5:])
    empty = []
    while f"{y:04d}-{m:02d}" <= last_m:
        if not months.get(f"{y:04d}-{m:02d}"):
            empty.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    runs, start = [], None
    for idx, mo in enumerate(empty):
        if start is None:
            start = mo
        nxt = empty[idx + 1] if idx + 1 < len(empty) else None
        yy, mm = int(mo[:4]), int(mo[5:])
        follow = f"{yy + (mm == 12):04d}-{(mm % 12) + 1:02d}"
        if nxt != follow:
            runs.append({"from": start, "to": mo,
                         "months": (int(mo[:4]) - int(start[:4])) * 12 + int(mo[5:]) - int(start[5:]) + 1})
            start = None

    lv_dates = sorted(r["inspection_date"] for r in lv if r["inspection_date"])
    wb_dates = sorted(r["inspection_date"] for r in wb if r["inspection_date"])
    gap_days = (date.fromisoformat(wb_dates[0]) - date.fromisoformat(lv_dates[-1])).days - 1
    # stretches with no record at all between the end of LIVES and the start of the exports
    span_dates = sorted({lv_dates[-1], wb_dates[0]} | {r["inspection_date"] for r in counted if r["inspection_date"]
                                                        and lv_dates[-1] < r["inspection_date"] < wb_dates[0]})
    empty_spans = [{"after": a, "before": b,
                    "days_with_no_records": (date.fromisoformat(b) - date.fromisoformat(a)).days - 1}
                   for a, b in zip(span_dates, span_dates[1:])
                   if (date.fromisoformat(b) - date.fromisoformat(a)).days > 31]
    ls_dates = sorted(r["inspection_date"] for r in ls if r["inspection_date"])

    # LIVES business_id vs Wayback permits
    wb_names = defaultdict(set)
    for r in wb:
        wb_names[r["permit"]].add(r["establishment_name"])
    lv_names = {}
    for r in lv:
        lv_names[r["permit"]] = r["establishment_name"]
    shared = sorted(set(lv_names) & set(wb_names))
    agree = sum(1 for p in shared if any(name_stem(lv_names[p]) == name_stem(n) for n in wb_names[p]))

    # 2019 weekly listings: the key's first part should be the same permit the other sources use
    other_names = defaultdict(set)
    for r in wb + lv:
        other_names[r["permit"]].add(r["establishment_name"])
    ls_known = [r for r in ls if r["permit"] in other_names]
    listing_check = {
        "listing_permits": len({r["permit"] for r in ls}),
        "also_in_exports": len({r["permit"] for r in ls} & {r["permit"] for r in wb}),
        "also_in_lives": len({r["permit"] for r in ls} & {r["permit"] for r in lv}),
        "rows_at_permits_seen_elsewhere": len(ls_known),
        "name_first_5_alnum_chars_agree": sum(1 for r in ls_known if any(
            name_stem(n) == name_stem(r["establishment_name"]) for n in other_names[r["permit"]]))}

    scores_page = read_json(RAW / "wayback" / "scores_page_text.json")
    paras = Counter(c.get("scale_paragraph") and re.sub(r"^The following is the list[^.]*\.\s*", "",
                                                         c["scale_paragraph"]) for c in scores_page["captures"])
    inv = read_json(RAW / "wayback" / "healthtrak_webscores_inventory.json")["captures"]
    inv_summary = Counter()
    in_gap = []
    gap_lo, gap_hi = lv_dates[-1].replace("-", ""), wb_dates[0].replace("-", "")
    for c in inv:
        path = re.sub(r"^https?://[^/]+", "", c["original"]).split("?")[0]
        inv_summary[f"{path} {c['timestamp'][:4]} status {c['statuscode']}"] += 1
        if gap_lo < c["timestamp"][:8] < gap_hi:
            in_gap.append({k: c[k] for k in ("timestamp", "original", "statuscode")}
                          | {"downloaded": c["timestamp"] in {x["timestamp"] for x in listings["captures"]}})
    listing_leads = [{k: c[k] for k in ("timestamp", "original")} for c in inv
                     if c["statuscode"] == "200" and re.search(r"(?i)/webscores(\?openview)?$", c["original"])
                     and not gap_lo < c["timestamp"][:8] < gap_hi and c["timestamp"] >= "2018"]
    rep_inv_path = RAW / "wayback" / "healthtrak_report_pages_inventory.json"
    report_pages = None
    if rep_inv_path.exists():
        rp = read_json(rep_inv_path)["captures"]
        report_pages = {"captures": len(rp), "by_status": dict(Counter(c["statuscode"] for c in rp))}

    muni = read_json(RAW / "municode_sec_10-126.json")
    text = muni["text"]
    ordinance = {
        "section": muni["section"], "library_url": SEC_10_126_URL, "code_version": muni["job"]["Name"],
        "code_banner": re.sub(r"\s+", " ", muni["job"]["BannerText"] or ""),
        "fetched_at": muni["fetched_at"],
        "mentions_score_or_numeric_threshold": bool(re.search(r"\bscore|\b(60|70)\b", text, re.I)),
        "suspension_subsection_present": "Suspension of permit" in text,
        "immediate_cessation_phrase": "food operations shall immediately cease" in text,
        "no_resumption_until_reinspection_phrase": "shall not resume operations until" in text,
    }

    validation = {
        "generated_at": utcnow(),
        "integrity": integrity,
        "sources": {
            "wayback_captures": [capture_stats(c) for c in caps],
            "cdx_queries": [{k: q[k] for k in ("name", "url", "rows", "fetched_at")} for q in meta["cdx_queries"]],
            "cdx_captures_not_downloaded": [c for c in meta["corscores_cdx_captures"] if c["statuscode"] != "200"],
            "socrata": {k: v for k, v in meta["socrata"].items() if k != "pages"} | {
                "rows": len(lv), "distinct_inspection_ids": len({r["lives_inspection_id"] for r in lv}),
                "businesses": len({r["permit"] for r in lv}),
                "earliest_date": lv_dates[0], "latest_date": lv_dates[-1],
                "constructed_keys_shared_by_more_than_one_row": sum(1 for n in lives_keys.values() if n > 1),
                "note_on_shared_keys": ("LIVES rows are kept one per inspection_id, so a constructed key shared by "
                                        "two inspections appears on two rows (rows_with_this_key = 2).")},
            "weekly_listings_in_gap": {"gap_bounds_exclusive": listings.get("gap_bounds_exclusive"),
                                       "captures": listing_stats},
        },
        "union": {
            "report_keys_across_captures": len(union),
            "rekeyed_keys_superseded": len(superseded),
            "wayback_inspections_counted": len(wb),
            "socrata_inspections": len(lv),
            "weekly_listing_inspections": len(ls),
            "weekly_listing_keys_already_in_other_sources": listing_skipped,
            "inspections_counted_all_sources": len(counted),
            "rows_in_inspections_csv": len(rows_out),
            "presence_patterns": dict(Counter(r["presence"] for r in rows_out if r["presence"])),
            "later_status": dict(Counter(r["later_status"] for r in rows_out if is_export(r))),
            "first_seen_status": dict(Counter(r["first_seen_status"] for r in rows_out
                                              if is_export(r))),
            "keys_listed_twice_in_a_capture": sum(1 for r in rows_out if r["rows_with_this_key"] > 1
                                                  and is_export(r)),
            "date_flags": dict(Counter(r["date_flag"] for r in rows_out if r["date_flag"])),
            "establishments": len(est_rows),
            "key_overlap_socrata_vs_wayback": len({r["report_key"] for r in lv} & {r["report_key"] for r in wb}),
            "note": ("'Counted' excludes the old key of every re-keyed record, whose successor key is also "
                     "in the table, so each inspection is counted once."),
        },
        "counts_per_source_and_year": {
            "all_sources": per_year(counted), "wayback_union": per_year(wb), "socrata": per_year(lv),
            "weekly_listings": per_year(ls),
            "per_capture": {c["ts"]: dict(sorted(Counter((r["date"].isoformat()[:4] if r["date"] else "no date")
                                                         for r in c["rows"]).items())) for c in caps},
        },
        "capture_overlaps": overlaps,
        "capture_transitions": transitions,
        "score_distribution": {"all_sources": dist(counted), "wayback_union": dist(wb), "socrata": dist(lv),
                               "weekly_listings": dist(ls)},
        "lives_permits_vs_wayback_permits": {
            "lives_businesses": len(lv_names), "also_in_wayback_captures": len(shared),
            "name_first_5_alnum_chars_agree": agree},
        "weekly_listing_permits_vs_other_sources": listing_check,
        "gaps": {
            "socrata_last_date": lv_dates[-1], "wayback_first_date": wb_dates[0],
            "days_between_lives_end_and_export_start": gap_days,
            "weekly_listing_inspections_inside": len(ls),
            "weekly_listing_dates": [ls_dates[0], ls_dates[-1]] if ls_dates else None,
            "stretches_over_a_month_with_no_records": empty_spans,
            "statement": (f"The LIVES feed ends on {lv_dates[-1]} and the oldest archived city export "
                          f"(captured {ts_to_datetime(order[0]):%Y-%m-%d}) starts on {wb_dates[0]}, about three "
                          f"years before its capture: {gap_days} days apart. Inside that span the only public "
                          f"scores found are {len(ls)} inspections from {len(listings['captures'])} archived weekly "
                          f"listings ({', '.join('week ending ' + (c['week_ending_as_published'] or '?') for c in listings['captures'])}). "
                          + "; ".join(f"{e['days_with_no_records']} days with no records between {e['after']} and "
                                      f"{e['before']}" for e in empty_spans) + "."),
            "empty_month_runs_up_to_last_export": runs,
            "records_dated_after_last_export": sum(1 for r in counted if r["inspection_date"]
                                                   and r["inspection_date"] > last_export.isoformat()),
            "after_last_capture": (f"The newest capture is {order[-1]} (export created "
                                   f"{by_ts[order[-1]]['created'].isoformat()}). Later inspections exist only in "
                                   "the city's live app, which this project does not access."),
            "permits_only_listed_while_active": ("Each export lists only the permits it currently carries. "
                                                 "Inspections of permits dropped before the first capture, and "
                                                 "inspections of dropped permits dated between captures, are "
                                                 "missing."),
            "item_level_violations": ("Item-level details (the 47-item form with demerits, inspector remarks, "
                                      "follow-up notes) exist only on the city's own HealthTrak report pages "
                                      "(discovery.cor.gov), whose robots.txt disallows all automated access. This "
                                      "project does not scrape them. The LIVES feed carries scores only."),
            "wayback_report_page_captures": report_pages,
            "public_information_request": GOVQA_URL,
        },
        "scoring_text": {
            "distinct_scale_paragraphs": {p: n for p, n in paras.items() if p},
            "weekly_listings_2019_same_paragraph": [
                {"timestamp": c["timestamp"],
                 "same_as_scores_page": bool(c.get("scale_paragraph")) and any(
                     p and p in re.sub(r"\s+", " ", c["scale_paragraph"]) for p in paras)}
                for c in listings["captures"]],
            "captures": [{k: c.get(k) for k in ("timestamp", "wayback_url", "digest_matches_cdx", "scale_paragraph")}
                         for c in scores_page["captures"]],
        },
        "ordinance": ordinance,
        "wayback_leads_not_mined": {"webscores_pages_by_path_year_status": dict(sorted(inv_summary.items())),
                                    "captures_dated_inside_the_gap": in_gap,
                                    "weekly_listing_captures_since_2018_outside_the_gap_not_downloaded": listing_leads,
                                    "note": ("The in-gap weekly listings marked downloaded are in inspections.csv "
                                             "(source wayback_listing:<ts>). The other weekly-listing captures each "
                                             "show one week that the exports also cover, except for permits an "
                                             "export no longer lists, and the April 2026 one, which is after the "
                                             "newest export; they were not downloaded.")},
    }
    (DATA / "validation.json").write_text(json.dumps(validation, indent=1, ensure_ascii=False, default=str) + "\n",
                                          encoding="utf-8")

    print(f"captures: {', '.join(order)}")
    print(f"inspections.csv: {len(rows_out)} rows ({len(wb)} Wayback export + {len(ls)} weekly listing + "
          f"{len(lv)} LIVES counted, {len(superseded)} superseded keys)")
    print(f"establishments.csv: {len(est_rows)} permits")
    for t in transitions:
        print(f"  {t['from']} -> {t['to']}: gone {t['gone_by_class']}, new {t['new_by_class']}")


if __name__ == "__main__":
    main()
