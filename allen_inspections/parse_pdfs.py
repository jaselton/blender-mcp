"""Parse the City of Allen's health-score report PDFs into rows.

Every report is a four-column list: facility name, address, RATING (a 0-100
score, or text such as "Follow up - Pass") and DATE. Three layouts occur:

  * Crystal Reports, 2022-2025 ("FACILITY NAME | ADDRESS | RATING | DATE",
    address includes "Allen, Tx 75002", dates m/d/yy);
  * Crystal Reports, 2019-2021 ("Establishment | ADDRESS | RATING | DATE",
    the name sits on its own line just above the address, dates "February 20, 2019");
  * Excel exports (2015, 2017) and an Excel print-to-PDF (2024), where the
    header row appears only on the first page and addresses start left of
    their header.

Words are placed in columns by x position and grouped into rows by the DATE
value of each row: a word belongs to the last date at or less than ~7.5 pt
below it (a wrapped cell starts up to ~6 pt above its row's baseline).
Column edges come from the header words, except the address column, whose
left edge is the most common x of street numbers. Page headers/footers and
the "TOTALS - ALL INSPECTIONS n" line are recognised and kept as report
metadata. A PDF with no usable text layer (a scan) is recorded as unparsed;
no OCR is used.

    python parse_pdfs.py            # parse every PDF in data/raw/pdfs and print a summary
"""

import bisect
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

from common import RAW

PDF_DIR = RAW / "pdfs"
DATE_RX = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")
PERIOD_RX = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})\s*TO\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
TOTAL_RX = re.compile(r"TOTALS?\s*-?\s*ALL\s+INSPECTIONS\s*:?\s*(\d+)", re.I)
CITY_RX = re.compile(r"^(?P<city>.*?)\s*,?\s*(?:T[XxNn]\.?)?\s*(?P<zip>\d{5})?\d*\s*$")
SCORE_RX = re.compile(r"^\d{1,3}$")
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
PUA = re.compile("[\uf000-\uf0ff]")
ROW_OFFSET = 7.5  # a word belongs to the last row whose date is at most this far below the word's top


def fix_pua(text):
    """One report (Archive Center item 2329) embeds its fonts with a ToUnicode map that points every
    glyph at the Private Use Area: U+F0xx stands for the character xx ('\uf043' is 'C'). Undo it."""
    return PUA.sub(lambda m: chr(ord(m.group()) - 0xF000), text) if text else text


def _split_decoded(w):
    """A decoded PUA 'word' can hold spaces (U+F020 is not whitespace to pdfplumber): split it and
    place each piece proportionally along the original word's width."""
    t = fix_pua(w["text"])
    out, n = [], max(len(t), 1)
    for m in re.finditer(r"\S+", t):
        x0 = w["x0"] + (w["x1"] - w["x0"]) * m.start() / n
        x1 = w["x0"] + (w["x1"] - w["x0"]) * m.end() / n
        out.append({**w, "text": m.group(), "x0": x0, "x1": x1})
    return out


def to_date(s):
    m = DATE_RX.match(s.strip())
    if not m:
        return None
    mo, d, y = (int(x) for x in m.groups())
    y = y + 2000 if y < 100 else y
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def normalize_result(text):
    """Coarse label for a non-numeric RATING cell (the raw text is kept in rating_text)."""
    low = re.sub(r"\s+", " ", text or "").strip().lower()
    if not low:
        return "blank"
    if "complaint" in low:
        return "complaint"
    if "follow" in low:
        return "follow-up: fail" if "fail" in low else "follow-up: pass" if "pass" in low else "follow-up: other"
    if "fail" in low:
        return "fail (unscored)"
    if "pass" in low:
        return "pass (unscored)"
    return "other"


def _header(words):
    """Header words of a page ({'NAME','ADDRESS','RATING','DATE'} -> word), or None."""
    heads = {}
    for w in words:
        u = w["text"].upper()
        key = "NAME" if u in ("FACILITY", "ESTABLISHMENT") else u
        if key in ("NAME", "ADDRESS", "RATING", "DATE") and key not in heads:
            heads[key] = w
    if len(heads) < 4 or max(h["top"] for h in heads.values()) - min(h["top"] for h in heads.values()) > 6:
        return None
    return heads


def _date_anchors(body, x_min):
    """Date cells right of x_min: short 'm/d/yy' tokens or long 'Month dd, yyyy' runs.
    Returns [(anchor word, date, x0)]."""
    out = []
    by_line = sorted([w for w in body if w["x0"] > x_min], key=lambda w: (round(w["top"]), w["x0"]))
    for i, w in enumerate(by_line):
        d = to_date(w["text"])
        if d:
            out.append((w, d, w["x0"]))
            continue
        mon = MONTHS.get(w["text"].lower().rstrip("."))
        if mon:
            nxt = [v for v in by_line if abs(v["top"] - w["top"]) < 2 and v["x0"] > w["x0"]][:2]
            if len(nxt) == 2 and re.fullmatch(r"\d{1,2},?", nxt[0]["text"]) and re.fullmatch(r"\d{4}", nxt[1]["text"]):
                try:
                    out.append((w, date(int(nxt[1]["text"]), mon, int(nxt[0]["text"].rstrip(","))), w["x0"]))
                except ValueError:
                    pass
    return out


def parse_pdf(path):
    """Returns (meta, rows)."""
    path = Path(path)
    meta = {"file": path.name, "pages": 0, "period_start": None, "period_end": None, "total_reported": None,
            "status": "parsed", "notes": [], "layout": None}
    rows = []
    with pdfplumber.open(path) as pdf:
        meta["pages"] = len(pdf.pages)
        meta["pdf_producer"] = (pdf.metadata or {}).get("Producer")
        all_text = []
        cols = None  # column edges; pages without a header row reuse the previous page's
        for pi, page in enumerate(pdf.pages, 1):
            words = page.extract_words(x_tolerance=2, y_tolerance=2)
            text = page.extract_text() or ""
            if PUA.search(text):
                meta["pua_decoded_pages"] = meta.get("pua_decoded_pages", 0) + 1
                text = fix_pua(text)
                words = [piece for w in words for piece in _split_decoded(w)]
            all_text.append(text)
            if len(re.findall(r"[A-Za-z]", text)) < 40:
                meta["notes"].append(f"page {pi}: no text layer")
                continue
            heads = _header(words)
            if heads is not None:
                body_top = max(h["bottom"] for h in heads.values()) + 1
                below = [w for w in words if w["top"] > body_top]
                # address column: most common x of street numbers between the name header and RATING
                nums = Counter(round(w["x0"]) for w in below if re.fullmatch(r"\d{1,6}", w["text"])
                               and heads["NAME"]["x0"] + 40 < w["x0"] < heads["RATING"]["x0"] - 30)
                addr_x = nums.most_common(1)[0][0] if nums else heads["ADDRESS"]["x0"]
                cols = {"addr": min(addr_x, heads["ADDRESS"]["x0"]) - 2.5,
                        "rating": heads["RATING"]["x0"] - 28,
                        "rating_mid": (heads["RATING"]["x0"] + heads["RATING"]["x1"]) / 2,
                        "name_hdr": heads["NAME"]["text"]}
                meta["layout"] = meta["layout"] or ("name above address" if heads["NAME"]["text"].upper() ==
                                                    "ESTABLISHMENT" else "one line per row")
            elif cols is None:
                meta["notes"].append(f"page {pi}: no column header found")
                continue
            else:
                body_top = 0
            # footer: the "Page n of m" line (Crystal Reports) and anything below it
            foot_top = page.height
            for w in words:
                if w["text"] == "Page" and w["top"] > page.height * 0.8:
                    foot_top = min(foot_top, w["top"] - 2)
            body = [w for w in words if body_top < w["top"] < foot_top]
            # the TOTALS line sits in the body area on the last page
            tot_words = [w for w in body if w["text"].upper().startswith("TOTALS")]
            if tot_words:
                ttop = tot_words[0]["top"]
                line = " ".join(w["text"] for w in sorted(body, key=lambda w: w["x0"]) if abs(w["top"] - ttop) < 3)
                m = TOTAL_RX.search(line)
                if m:
                    meta["total_reported"] = int(m.group(1))
                body = [w for w in body if w["top"] < ttop - 2]
            found = _date_anchors(body, cols["rating_mid"])
            if not found:
                continue
            found.sort(key=lambda t: t[0]["top"])
            anchors = [f[0] for f in found]
            date_words = set()
            for a, _, _ in found:  # the month/day/year tokens of long dates
                date_words |= {id(v) for v in body if abs(v["top"] - a["top"]) < 2 and v["x0"] >= a["x0"]}
            x_date = min(f[2] for f in found) - 1
            tops = [a["top"] for a in anchors]
            groups = [[] for _ in anchors]
            orphans = []
            for w in body:
                if id(w) in date_words:
                    continue
                k = bisect.bisect_right(tops, w["top"] + ROW_OFFSET) - 1
                (groups[k] if k >= 0 else orphans).append(w)
            if orphans:
                meta["notes"].append(f"page {pi}: {len(orphans)} words above the first row: "
                                     + " ".join(w["text"] for w in orphans)[:80])
            # where the city part of the address starts ("Allen, Tx 75002"): median x of "City,"
            # words that sit just left of a "Tx" word on the same line
            tx = [w for w in body if w["text"].upper().rstrip(".") in ("TX", "TN")
                  and cols["addr"] <= w["x0"] < cols["rating"]]
            starts = []
            for t in tx:
                prev = [w for w in body if abs(w["top"] - t["top"]) < 2 and t["x0"] - 12 < w["x1"] <= t["x0"] + 1
                        and w["text"].endswith(",")]
                if prev:
                    starts.append(prev[0]["x0"])
            city_x = sorted(starts)[len(starts) // 2] - 2 if len(starts) >= 3 else None
            for k, ((a, d, _), g) in enumerate(zip(found, groups)):
                def col(lo, hi):
                    ws = sorted([w for w in g if lo <= w["x0"] < hi], key=lambda w: (round(w["top"] / 4), w["x0"]))
                    return " ".join(w["text"] for w in ws).strip()
                name = col(-1, cols["addr"])
                address = col(cols["addr"], cols["rating"])
                rating = col(cols["rating"], x_date)
                extra = col(x_date, 10_000)
                if city_x:
                    street, city_part = col(cols["addr"], city_x), col(city_x, cols["rating"])
                else:
                    street, city_part = address, ""
                m = CITY_RX.match(city_part)
                city, zipc = (m.group("city").strip(" ,") or None, m.group("zip")) if m else (city_part or None, None)
                score = int(rating) if SCORE_RX.match(rating) and 0 <= int(rating) <= 100 else None
                date_text = " ".join(v["text"] for v in sorted(body, key=lambda v: v["x0"])
                                     if id(v) in date_words and abs(v["top"] - a["top"]) < 2)
                rows.append({
                    "report_file": path.name, "page": pi, "row_on_page": k + 1,
                    "facility_name": name, "address": address, "street": street, "city": city, "zip": zipc,
                    "rating_text": rating, "score": score,
                    "result_text": "" if score is not None else normalize_result(rating),
                    "date": d.isoformat(), "date_text": date_text,
                    "parse_note": f"extra text in date column: {extra}" if extra else "",
                })
        full = "\n".join(all_text)
        m = PERIOD_RX.search(full)
        if m:
            s, e = to_date(m.group(1)), to_date(m.group(2))
            meta["period_start"], meta["period_end"] = (s.isoformat() if s else None, e.isoformat() if e else None)
        if meta["total_reported"] is None:
            m = TOTAL_RX.search(full)
            if m:
                meta["total_reported"] = int(m.group(1))
    if not rows:
        meta["status"] = "unparsed (no text layer)" if any("no text layer" in n for n in meta["notes"]) \
            else "unparsed (no rows found)"
    meta["rows"] = len(rows)
    meta["numeric_scores"] = sum(1 for r in rows if r["score"] is not None)
    if rows:
        ds = sorted(r["date"] for r in rows)
        meta["first_row_date"], meta["last_row_date"] = ds[0], ds[-1]
    for i, r in enumerate(rows, 1):
        r["row_in_report"] = i
    return meta, rows


def main():
    files = sorted(PDF_DIR.glob("*.pdf")) if len(sys.argv) < 2 else [Path(a) for a in sys.argv[1:]]
    for f in files:
        meta, rows = parse_pdf(f)
        sc = [r["score"] for r in rows if r["score"] is not None]
        print(f"{f.name}: {meta['status']} layout={meta['layout']} pages={meta['pages']} "
              f"period={meta['period_start']}..{meta['period_end']} rows={len(rows)} "
              f"total_reported={meta['total_reported']} numeric={len(sc)} "
              f"range={min(sc) if sc else None}-{max(sc) if sc else None} "
              f"dates={meta.get('first_row_date')}..{meta.get('last_row_date')} notes={meta['notes'][:2]}")


if __name__ == "__main__":
    main()
