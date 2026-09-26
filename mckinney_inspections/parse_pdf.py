"""Parse the McKinney food inspection reports attached to permits on the city's
FoodPermitsAndInspections ArcGIS service (table 5, "Attachments").

What the attachments are
------------------------
Every attachment whose CONTENT_TYPE is application/pdf is the city's
"City of McKinney Health Compliance / Food Establishment Inspection Form",
generated on an iPad (Producer "iOS ... Quartz PDFContext") with a real text
layer. The layout is HTML-like: cells grow when a value wraps, and everything
below moves down, so nothing can be read at a fixed position. Values are
found relative to their labels instead.

* Page 1 is the header plus the 47-item grid.
    - Header labels are 8pt regular ("Date:", "Time in:", "Permit #:", ...)
      with the value in the same table cell on the line(s) below. The
      "Purpose of Inspection:" value sits to the right of its (bold) label.
    - "Number of Repeat Violations:" and "Number of Violations COS:" are bold
      labels with a bold 8pt number to their right.
    - TOTAL/SCORE is a 34pt number in the box below the "TOTAL/SCORE" label.
    - The "Follow-up: Yes / No" boxes are Wingdings glyphs.
    - The grid has two columns. Each item title (7 or 7.5pt) starts with
      "N." at x~76 (left) or x~364 (right). The compliance status is an 8pt
      word in the "Compliance Status" column to the left of the title:
      IN, OUT (red on newer reports, black on older ones), COS, NO or NA.
      "COS" is written *instead of* OUT when the violation was corrected on
      site; it is scored like OUT. The "R" (repeat) mark is a "*" drawn in the
      narrow R column at the right edge of each grid column (x~292 / ~583).
      Marks are vertically centred in their cell, so a mark belongs to the
      nearest item title at or above its centre.
* Page 2 repeats name / address / city / permit #, then the TEMPERATURE
  OBSERVATIONS table (3 x N cells of "item/location" and "Temp"; usually just
  "/"), then OBSERVATIONS AND CORRECTIVE ACTIONS: an optional bold
  "Permit Expiration Date:" line and the inspector's free text (8pt).
* When the free text does not fit, it continues on page 3 (no header), and
  the "Received by: / Inspected by:" signature block ends the last page. The
  printed names under the signatures are Helvetica; the inspector's name is
  under "Inspected by:".

Versions: the form's wording and layout are the same on every report
(2017-2026). Reports from 2017 to 2021 print OUT in black, use randomly
tagged font subsets and have no "Permit Expiration Date" line; later ones
print OUT in red. Some older reports put the signature names lower, and 31
(school and market permits) print only the house number as the page-2 address.

Self-checks recorded in "checks" (all 3,134 reports pass the first three):
the printed score equals the form points (3/2/1) of the items marked OUT or
COS; the "Number of Repeat Violations" equals the "*" marks; the "Number of
Violations COS" equals the COS marks. Page 2's header is compared with
page 1's, and the items cited in the free text with the items marked OUT.

The attachment's REL_OBJECTID is NOT a reliable link to the permit (most are
off by one to three OBJECTIDs); use the permit number printed on the report.

The other 388 attachments (CONTENT_TYPE "UNKNOWNMSG") are Outlook .msg files
(OLE compound files); `parse_msg` reads their subject, sender, date, body and
attachment list with a minimal compound-file reader.

Usage:
    python parse_pdf.py FILE [FILE ...]           # print the parse as JSON
    python parse_pdf.py --all ATTACH_DIR OUT.jsonl  # every {REL}_{ATT}.pdf
"""

import datetime as dt
import json
import re
import struct
import sys
from pathlib import Path

import pdfplumber

PDF_MAGIC = b"%PDF"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FORM_TITLE = "Food Establishment Inspection Form"

# Item points on the Texas DSHS form.
def form_points(n):
    return 3 if n <= 20 else 2 if n <= 33 else 1


# Items printed in the left grid column of page 1 (the rest are on the right).
LEFT_ITEMS = set(range(1, 12)) | set(range(21, 27)) | set(range(34, 41))

STATUS_WORDS = {"IN", "OUT", "COS", "NO", "NA"}

# Item 10's title has a blank the inspector sometimes fills in
# ("Cleaned and Sanitized at 160 temp/ 200 QA ppm/temperature").
ITEM10_HEAD = "Food contact surfaces and Returnables; Cleaned and Sanitized at"
ITEM10_TAIL = "ppm/temperature"


class ParseError(Exception):
    pass


# --------------------------------------------------------------------------
# word helpers

def _dedupe(page):
    """Drop characters drawn twice at the same spot (pdfplumber's dedupe_chars
    is exact but slow; these PDFs rarely need it)."""
    seen, keep = set(), set()
    for c in page.chars:
        k = (c["text"], c["fontname"], round(c["size"], 1), round(c["x0"]), round(c["top"]))
        if k not in seen:
            seen.add(k)
            keep.add(id(c))
    if len(keep) == len(page.chars):
        return page, 0
    return page.filter(lambda o: o.get("object_type") != "char" or id(o) in keep), len(page.chars) - len(keep)


def _words(page):
    # use_text_flow keeps characters in drawing order. A long header value is
    # drawn past the right edge of its cell (the page clips it), underneath
    # the next cell's value; sorted by position, the two would interleave.
    return page.extract_words(
        keep_blank_chars=True, x_tolerance=1.5, y_tolerance=2, use_text_flow=True,
        extra_attrs=["fontname", "size", "non_stroking_color"],
    )


def _bold(w):
    return "Bold" in w["fontname"]


def _sz(w):
    return round(w["size"], 1)


def _red(w):
    c = w.get("non_stroking_color")
    return isinstance(c, (tuple, list)) and len(c) == 3 and c[0] > 0.5 and c[1] < 0.3 and c[2] < 0.3


def _find(words, text, bold=None, max_top=None, min_top=None):
    for w in words:
        if w["text"].strip() != text:
            continue
        if bold is not None and _bold(w) != bold:
            continue
        if max_top is not None and w["top"] > max_top:
            continue
        if min_top is not None and w["top"] < min_top:
            continue
        return w
    return None


def _lines(words, tol=2.5):
    """Group words into lines (top within tol), each sorted left to right."""
    out = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and abs(out[-1][0]["top"] - w["top"]) <= tol:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in out]


def _join(words):
    lines = [" ".join(w["text"].strip() for w in l if w["text"].strip()) for l in _lines(words)]
    return [l for l in lines if l]


def _cell(words, x0, x1, top, bottom, clipped=None, key=None):
    """Lines of text starting inside a cell. Text running past the cell's right
    edge is cut off on the printed page; its key is added to `clipped`."""
    got = [
        w for w in words
        if x0 <= w["x0"] < x1 and top <= w["top"] < bottom and w["text"].strip()
    ]
    if clipped is not None and any(w["x1"] > x1 - 2 for w in got):
        clipped.append(key)
    return _join(got)


# --------------------------------------------------------------------------
# page 1: header

ROW1 = ["Date:", "Time in:", "Time out:", "Permit #:", "Risk Category:"]
ROW2 = ["Establishment Name:", "Contact/Owner Name:"]
ROW3 = ["Physical Address:", "City:", "Zip Code:", "Phone/Email:"]
HEADER_KEYS = {
    "Date:": "date", "Time in:": "time_in", "Time out:": "time_out",
    "Permit #:": "permit_number", "Risk Category:": "risk_category",
    "Establishment Name:": "establishment_name", "Contact/Owner Name:": "owner_contact",
    "Physical Address:": "address", "City:": "city", "Zip Code:": "zip",
    "Phone/Email:": "phone_email",
}


def parse_header(words, warnings):
    hdr = {k: None for k in HEADER_KEYS.values()}
    hdr.update(purpose=None, repeat_count=None, cos_count=None, score=None,
               follow_up=None, page_label=None)
    labels = {t: _find(words, t, bold=False, max_top=250) for t in ROW1 + ROW2 + ROW3}
    purpose = _find(words, "Purpose of Inspection:", bold=True, max_top=250)
    total = _find(words, "TOTAL/SCORE", bold=True, max_top=250)
    rep = _find(words, "Number of Repeat Violations:", bold=True, max_top=250)
    cos = _find(words, "Number of Violations COS:", bold=True, max_top=250)
    followup = _find(words, "Follow-up:", max_top=250)
    comp = next((w for w in words if w["text"].startswith("Compliance Status:") and _bold(w)), None)
    missing = [t for t, w in labels.items() if w is None]
    for name, w in [("Purpose of Inspection:", purpose), ("TOTAL/SCORE", total),
                    ("Number of Repeat Violations:", rep), ("Number of Violations COS:", cos),
                    ("Compliance Status:", comp)]:
        if w is None:
            missing.append(name)
    if missing:
        raise ParseError(f"page 1 labels not found: {missing}")

    page_w = next((w for w in words if re.fullmatch(r"Page \d+ of \d+", w["text"].strip()) and w["top"] < 120), None)
    if page_w:
        hdr["page_label"] = page_w["text"].strip()

    label_ids = {id(w) for w in labels.values()}
    total_left = total["x0"] - 8
    rows = [
        (ROW1, page_w["x0"] - 2 if page_w else total_left, purpose["top"] - 2),
        (ROW2, rep["x0"] - 2, labels["Physical Address:"]["top"] - 1),
        (ROW3, total_left, comp["top"] - 1),
    ]
    # Values are regular-weight 8pt text under their label, within the cell.
    vals = [w for w in words if not _bold(w) and 7.9 <= _sz(w) <= 8.1 and id(w) not in label_ids]
    clipped = []
    for row, right, bottom in rows:
        for i, t in enumerate(row):
            lab = labels[t]
            x1 = labels[row[i + 1]]["x0"] - 2 if i + 1 < len(row) else right
            lines = _cell(vals, lab["x0"] - 3, x1, lab["top"] + 6, bottom, clipped, HEADER_KEYS[t])
            hdr[HEADER_KEYS[t]] = " ".join(lines) if lines else None
            if len(lines) > 1:
                hdr[HEADER_KEYS[t] + "_lines"] = lines

    pv = [w for w in words if abs(w["top"] - purpose["top"]) < 3 and w["x0"] > purpose["x1"] - 1
          and (followup is None or w["x1"] < followup["x0"]) and not _bold(w)]
    hdr["purpose"] = " ".join(_join(pv)) or None

    for key, lab in (("repeat_count", rep), ("cos_count", cos)):
        v = [w for w in words if _bold(w) and abs(w["top"] - lab["top"]) < 4
             and w["x0"] > lab["x1"] - 1 and w["x0"] < total["x0"] - 5]
        txt = " ".join(_join(v))
        hdr[key] = int(txt) if txt.isdigit() else (txt or None)

    # Follow-up check boxes: Wingdings glyphs after "Follow-up:"; "\xa8" is empty.
    if followup:
        boxes = [w for w in words if "Wingdings" in w["fontname"] and abs(w["top"] - followup["top"]) < 4]
        yes = _find(words, "Yes", max_top=250)
        no = _find(words, "No", max_top=250)
        marks = {}
        for b in boxes:
            which = "yes" if yes and b["x0"] < yes["x0"] and (no is None or b["x0"] < no["x0"] - 20) else "no"
            marks[which] = b["text"]
        hdr["follow_up_boxes"] = marks
        checked = [k for k, v in marks.items() if v.strip() not in ("\xa8", "")]
        hdr["follow_up"] = checked[0].upper() if len(checked) == 1 else ("BOTH" if checked else None)
    hdr["clipped_on_page"] = clipped
    return hdr, comp, total


def parse_score(page, total, comp):
    """The 34pt number in the TOTAL/SCORE box (below its label, above the grid)."""
    chars = [c for c in page.chars if c["size"] > 20 and c["top"] < comp["top"]
             and c["x0"] > total["x0"] - 30]
    txt = "".join(c["text"] for c in sorted(chars, key=lambda c: c["x0"])).strip()
    return txt


# --------------------------------------------------------------------------
# page 1: item grid

def parse_grid(words, comp, warnings):
    grid = [w for w in words if w["top"] > comp["bottom"]]
    title_words = [w for w in grid if not _bold(w) and _sz(w) < 7.8]
    heads = []
    for w in title_words:
        m = re.match(r"\s*(\d{1,2})\.\s*", w["text"])
        if m:
            heads.append((int(m.group(1)), w))
    cols = {}
    for n, w in heads:
        side = "L" if w["x0"] < 300 else "R"
        cols.setdefault(side, []).append((n, w))
    items = {}
    for side, lst in cols.items():
        lst.sort(key=lambda t: t[1]["top"])
        for n, w in lst:
            if n in items:
                warnings.append(f"item label {n} printed twice")
            items[n] = {"side": side, "label": w}
    if sorted(items) != list(range(1, 48)):
        raise ParseError(f"grid item labels found: {sorted(items)}")
    for n, it in items.items():
        if (it["side"] == "L") != (n in LEFT_ITEMS):
            raise ParseError(f"item {n} in unexpected column")
    left_x = min(it["label"]["x0"] for it in items.values() if it["side"] == "L")
    right_x = min(it["label"]["x0"] for it in items.values() if it["side"] == "R")

    # Title text (label line plus wrapped lines at the same x, before the next item).
    for side, x in (("L", left_x), ("R", right_x)):
        lst = sorted((it["label"]["top"], n) for n, it in items.items() if it["side"] == side)
        for i, (top, n) in enumerate(lst):
            nxt = lst[i + 1][0] if i + 1 < len(lst) else 10_000
            tw = [w for w in title_words if abs(w["x0"] - x) < 3 and top - 1 <= w["top"] < nxt - 1]
            items[n]["title"] = " ".join(_join(tw))

    # Column bands, relative to the title x of each column.
    #   status: from the grid's left edge to ~4pt before the title;
    #   R: the narrow column just left of the other grid column / page edge.
    bands = {
        "L": {"status": (left_x - 56, left_x - 4), "r": (left_x + 203, left_x + 226)},
        "R": {"status": (right_x - 56, right_x - 4), "r": (right_x + 206, right_x + 232)},
    }

    def owner(side, w):
        cy = (w["top"] + w["bottom"]) / 2
        cands = [(it["label"]["top"], n) for n, it in items.items()
                 if it["side"] == side and it["label"]["top"] <= cy + 2]
        if not cands:
            return None, None
        top, n = max(cands)
        return n, cy - top

    marks = {n: {"status": [], "r": []} for n in items}
    stray = []
    for w in grid:
        t = w["text"].strip()
        if not t or _bold(w):
            continue
        side = "L" if w["x0"] < bands["R"]["status"][0] else "R"
        kind = None
        s0, s1 = bands[side]["status"]
        r0, r1 = bands[side]["r"]
        if s0 <= w["x0"] < s1 and _sz(w) >= 7.9:
            kind = "status"
        elif r0 <= w["x0"] < r1:
            kind = "r"
        if kind is None:
            if _sz(w) >= 7.9 or _sz(w) < 6.5:
                stray.append(t)
            continue
        n, dy = owner(side, w)
        if n is None or dy > 22:
            stray.append(t)
            continue
        marks[n][kind].append({"text": t, "red": _red(w), "dy": round(dy, 1)})
    for t in stray:
        warnings.append(f"unplaced grid text: {t!r}")

    out = []
    for n in range(1, 48):
        st = marks[n]["status"]
        rr = marks[n]["r"]
        if len(st) > 1:
            warnings.append(f"item {n}: several status marks {[s['text'] for s in st]}")
        raw = " ".join(s["text"] for s in st) or None
        if raw is not None and raw not in STATUS_WORDS:
            warnings.append(f"item {n}: unknown status {raw!r}")
        status = "OUT" if raw == "COS" else raw
        r_raw = " ".join(s["text"] for s in rr) or None
        if r_raw not in (None, "*"):
            warnings.append(f"item {n}: unexpected R-column text {r_raw!r}")
        rec = {
            "item": n,
            "status": status,
            "cos": raw == "COS",
            "r": r_raw is not None,
            "status_raw": raw,
            "status_red": bool(st) and all(s["red"] for s in st),
        }
        if r_raw not in (None, "*"):
            rec["r_raw"] = r_raw
        out.append(rec)
    titles = {n: items[n]["title"] for n in items}
    return out, titles


def item10_fill(title):
    t = re.sub(r"^\s*10\.\s*", "", title or "")
    if not t.startswith(ITEM10_HEAD):
        return None
    t = t[len(ITEM10_HEAD):]
    if t.rstrip().endswith(ITEM10_TAIL):
        t = t.rstrip()[: -len(ITEM10_TAIL)]
    return t.strip() or None


# --------------------------------------------------------------------------
# pages 2+: repeated header, temperature table, observations, signatures

def parse_page2_header(words):
    labels = [_find(words, t, bold=False, max_top=100) for t in
              ("Establishment Name:", "Physical Address:", "City:", "Permit #:")]
    if any(l is None for l in labels):
        return None
    page_w = next((w for w in words if re.fullmatch(r"Page \d+ of \d+", w["text"].strip()) and w["top"] < 100), None)
    ttl = _find(words, "TEMPERATURE OBSERVATIONS", max_top=200)
    bottom = ttl["top"] - 2 if ttl else labels[0]["top"] + 25
    right = page_w["x0"] - 2 if page_w else 545
    ids = {id(l) for l in labels}
    vals = [w for w in words if not _bold(w) and id(w) not in ids and w is not page_w]
    out = {}
    clipped = []
    for i, (key, lab) in enumerate(zip(("establishment_name", "address", "city", "permit_number"), labels)):
        x1 = labels[i + 1]["x0"] - 2 if i + 1 < len(labels) else right
        lines = _cell(vals, lab["x0"] - 3, x1, lab["top"] + 6, bottom, clipped, key)
        out[key] = " ".join(lines) if lines else None
    out["page_label"] = page_w["text"].strip() if page_w else None
    out["clipped_on_page"] = clipped
    return out


def parse_temp_table(words, warnings):
    ttl = _find(words, "TEMPERATURE OBSERVATIONS")
    obs = _find(words, "OBSERVATIONS AND CORRECTIVE ACTIONS")
    if not ttl or not obs:
        return None
    heads = sorted([w for w in words if w["text"].strip() == "Item/Location" and ttl["top"] < w["top"] < obs["top"]],
                   key=lambda w: w["x0"])
    temps = sorted([w for w in words if w["text"].strip() == "Temp" and ttl["top"] < w["top"] < obs["top"]],
                   key=lambda w: w["x0"])
    if len(heads) != 3 or len(temps) != 3:
        warnings.append("temperature table header not recognised")
        return None
    top = max(w["bottom"] for w in heads + temps)
    body = [w for w in words if top < w["top"] < obs["top"] - 1 and w["text"].strip()]
    cells = []
    for i in range(3):
        ix0 = heads[i]["x0"] - 4
        tx0 = temps[i]["x0"] - 8
        nx = heads[i + 1]["x0"] - 4 if i < 2 else 10_000
        iw = sorted([w for w in body if ix0 <= w["x0"] < tx0], key=lambda w: w["top"])
        tw = [w for w in body if tx0 <= w["x0"] < nx]
        # A cell's text may wrap (lines 12pt apart); cells are ~26pt apart.
        groups = []
        for l in _lines(iw):
            if groups and l[0]["top"] - groups[-1][-1][0]["top"] < 14:
                groups[-1].append(l)
            else:
                groups.append([l])
        used = set()
        for g in groups:
            text = " ".join(" ".join(w["text"].strip() for w in l) for l in g).strip()
            gtop, gbot = g[0][0]["top"], g[-1][0]["bottom"]
            t = [w for w in tw if gtop - 3 <= w["top"] <= gbot + 3]
            used.update(id(w) for w in t)
            temp = " ".join(w["text"].strip() for w in t) or None
            if text.replace("/", "").strip() or temp:
                item, _, loc = text.partition("/")
                cells.append({"column": i + 1, "text": text, "item": item.strip(),
                              "location": loc.strip(), "temp": temp})
        for w in tw:
            if id(w) not in used:
                cells.append({"column": i + 1, "text": "", "item": "", "location": "", "temp": w["text"].strip()})
                warnings.append(f"temperature without item: {w['text']!r}")
    return cells


def parse_observations(pdf_pages, warnings):
    """Free text from pages 2+, between the section heading and 'Received by:'."""
    lines = []
    expiration = None
    received_by = inspector = None
    sig_page = None
    for pno, (page, words) in enumerate(pdf_pages, start=2):
        start = 0
        if pno == 2:
            nb = _find(words, "NOTED BELOW:")
            if nb is None:
                anchor = next((w for w in words if w["text"].startswith("AN INSPECTION OF YOUR ESTABLISHMENT")), None)
                if anchor is None:
                    warnings.append("observations heading not found on page 2")
                    continue
                start = anchor["bottom"]
            else:
                start = nb["bottom"]
        rec = _find(words, "Received by:", bold=True, min_top=start)
        insp = _find(words, "Inspected by:", bold=True, min_top=start)
        end = rec["top"] - 1 if rec else 10_000
        body = [w for w in words if start < w["top"] < end and w["text"].strip()]
        exp_lab = next((w for w in body if w["text"].strip().startswith("Permit Expiration Date:")), None)
        if exp_lab:
            same = [w for w in body if abs(w["top"] - exp_lab["top"]) < 2 and _bold(w)]
            txt = " ".join(_join(same))
            expiration = txt.split(":", 1)[1].strip() or None
            body = [w for w in body if not (abs(w["top"] - exp_lab["top"]) < 2 and _bold(w))]
        # Words are split where the font changes; rebuild each printed line.
        for l in _lines(body):
            text = ""
            prev = None
            for w in l:
                gap = w["x0"] - prev["x1"] if prev else 0
                text += (" " if prev and gap > 1 else "") + w["text"]
                prev = w
            indent = round((l[0]["x0"] - 24) / 4)
            lines.append({"page": pno, "text": text.rstrip(), "indent": max(indent, 0)})
        if rec:
            sig_page = pno
            below = [w for w in words if rec["top"] < w["top"] < rec["top"] + 90 and "Helvetica" in w["fontname"]]
            split = insp["x0"] - 5 if insp else 300
            received_by = " ".join(_join([w for w in below if w["x0"] < split])) or None
            inspector = " ".join(_join([w for w in below if w["x0"] >= split])) or None
    return lines, expiration, received_by, inspector, sig_page


# Violations are usually written "NN - text", "#NN: text", "#NN. text" or
# "#NN text". Some inspectors write temperatures the same way, value first
# ("38 - Tomatoes RIC", "167 - Soup HH"), so an un-hashed "NN - text" line is
# read as a temperature when NN > 47, or when the text is a short
# equipment/food label with no instruction in it.
CITE_RE = re.compile(r"^\s*(?:#\s*(\d{1,3})(?!\d)\s*(?:-|–|:|\.(?!\d)|\))?|(\d{1,3})\s*(?:-|–|:|\.(?!\d)|\))(?!\d))\s*(.*)$")
TEMP_F_RE = re.compile(
    r"(?<![\d/.-])(-?\d{1,3}(?:\.\d)?)\s*(?:°|º|˚|degrees?|deg\.?)?\s*F(?:ahrenheit)?\b", re.I)
TEMP_TRAIL_RE = re.compile(r"^(?P<what>[A-Za-z][^#:]*?[A-Za-z)\-:]?)\s*[:\-]?\s*(?P<val>-?\d{1,3}(?:\.\d)?)\s*\**$")
ACTION_RE = re.compile(
    r"observ|clean|replace|repair|label|remove|provide|store|must|shall|please|sanitiz|discard|obtain|"
    r"post|maintain|keep|correct|ensure|install|seal|need|missing|\bno\b|\bnot\b|\buse|cover|violation|permit",
    re.I)
PLACE_RE = re.compile(
    r"\b(RIC|WIC|WIF|RIF|WI|RI|HH|CH|hot|cold|prep|cooler|freezer|fridge|refrigerator|ambient|walk|reach|"
    r"line|table|display|rinse|hold|holding|make|bar|unit|drawer|well|steam|temp)\b", re.I)
NOT_TEMP_RE = re.compile(r"ppm|sanitiz|quat|chlorine|\bCL\b|\bQA\b|gallon|cert|suite|#", re.I)


def _line_kind(text):
    """('cite', item, rest) | ('temp', value, rest) | (None, None, None)."""
    m = CITE_RE.match(text)
    if not m:
        return None, None, None
    n = int(m.group(1) or m.group(2))
    rest = m.group(3).strip()
    if m.group(1) is None:  # no '#'
        words = rest.split()
        if n > 47 or (not ACTION_RE.search(rest) and (
                (len(words) <= 6 and PLACE_RE.search(rest)) or len(words) <= 2)):
            return "temp", n, rest
    if 1 <= n <= 47:
        return "cite", n, rest
    return None, None, None


def cited_items(lines):
    out = []
    for i, ln in enumerate(lines):
        kind, n, rest = _line_kind(ln["text"])
        if kind == "cite":
            out.append({"item": n, "line": i, "text": rest, "hash": ln["text"].lstrip().startswith("#")})
    return out


def _violation_lines(lines):
    """Indexes of lines inside a written-up violation: a cited line and the
    lines that continue it, up to a blank-ish heading ('Comments:') or the
    next temperature line."""
    inside, out = False, set()
    for i, ln in enumerate(lines):
        t = ln["text"].strip()
        kind, _, _ = _line_kind(t)
        if kind == "cite":
            inside = True
        elif kind == "temp" or re.fullmatch(r"[A-Za-z /]+:?", t):
            inside = False
        if inside:
            out.add(i)
    return out


def temperature_readings(lines):
    """Temperatures written in the free text: 'NN F' / 'NN°F' / 'NN degrees
    Fahrenheit' anywhere (unit "F"), and, with no unit (unit None),
    value-first lines ('38 - Tomatoes RIC') and 'thing NN' lines
    ('Reach-in 38'). `in_violation` marks numbers inside a violation's text,
    which include code limits ('41F or below') as well as readings."""
    out = []
    vio = _violation_lines(lines)
    for i, ln in enumerate(lines):
        t = ln["text"]
        found = False
        for m in TEMP_F_RE.finditer(t):
            out.append({"line": i, "value": float(m.group(1)), "unit": "F", "text": t.strip(), "form": "unit",
                        "in_violation": i in vio})
            found = True
        if found:
            continue
        kind, n, rest = _line_kind(t)
        if kind == "temp":
            if not NOT_TEMP_RE.search(rest):
                out.append({"line": i, "value": float(n), "unit": None, "text": t.strip(), "form": "value first",
                            "in_violation": False})
        elif kind is None:
            m = TEMP_TRAIL_RE.match(t.strip())
            if (m and not re.search(r"\d\s*[/-]\s*\d", t) and not NOT_TEMP_RE.search(t)
                    and -30 <= float(m.group("val")) <= 220):
                out.append({"line": i, "value": float(m.group("val")), "unit": None, "text": t.strip(),
                            "form": "value last", "in_violation": i in vio})
    return out


# --------------------------------------------------------------------------
# whole report

def _pdf_date(v):
    """PDF 'D:20250627153908Z' / "D:20250627153908+00'00'" -> ISO string."""
    if isinstance(v, bytes):
        v = v.decode("latin-1", "replace")
    m = re.match(r"D:(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)(Z|[+-]\d\d'?\d\d'?)?", v or "")
    if not m:
        return v or None
    tz = (m.group(7) or "").replace("'", "")
    tz = "Z" if tz in ("Z", "+0000", "-0000") else (tz[:3] + ":" + tz[3:] if tz else "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}:{m.group(5)}:{m.group(6)}{tz}"


def _is_form_page1(words):
    return (any(w["text"].strip() == FORM_TITLE for w in words)
            and any(w["text"].startswith("Compliance Status:") for w in words))


def parse_report(path):
    warnings = []
    with pdfplumber.open(path) as pdf:
        n_pages = len(pdf.pages)
        meta = {"pdf_created": _pdf_date(pdf.metadata.get("CreationDate")),
                "pdf_producer": pdf.metadata.get("Producer")}
        pages = []
        for i, p in enumerate(pdf.pages, start=1):
            p, dropped = _dedupe(p)
            if dropped:
                warnings.append(f"page {i}: {dropped} duplicate characters dropped")
            pages.append(p)
        words = [_words(p) for p in pages]
        n_chars = [len(p.chars) for p in pages]
        if sum(n_chars) == 0:
            return {"doc_type": "pdf_no_text", "pdf_pages": n_pages, **meta, "parse_warnings": ["no text layer"]}
        if not any(w["text"].strip() == FORM_TITLE for w in words[0]):
            return {"doc_type": "other_pdf", "pdf_pages": n_pages, **meta,
                    "first_text": pages[0].extract_text()[:300], "parse_warnings": []}
        grid_pages = [i + 1 for i, w in enumerate(words) if _is_form_page1(w)]
        if len(grid_pages) > 1:
            warnings.append(f"several item grids (inspections?) in one PDF, pages {grid_pages}")

        hdr, comp, total = parse_header(words[0], warnings)
        hdr["score_raw"] = parse_score(pages[0], total, comp)
        hdr["score"] = int(hdr["score_raw"]) if hdr["score_raw"].isdigit() else None
        items, titles = parse_grid(words[0], comp, warnings)
        rest = list(zip(pages[1:], words[1:]))
        p2 = parse_page2_header(words[1]) if n_pages > 1 else None
        table = parse_temp_table(words[1], warnings) if n_pages > 1 else None
        obs, expiration, received_by, inspector, sig_page = parse_observations(rest, warnings)

    # ---- consistency checks
    lab = hdr.get("page_label")
    m = re.fullmatch(r"Page 1 of (\d+)", lab or "")
    if not m:
        warnings.append(f"page 1 label {lab!r}")
    elif int(m.group(1)) != n_pages:
        warnings.append(f"page label says {m.group(1)} pages, PDF has {n_pages}")
    if p2 is None and n_pages > 1:
        warnings.append("page 2 header not found")
    if sig_page is None:
        warnings.append("signature block not found")

    scored = [it for it in items if it["status"] == "OUT"]
    pts = sum(form_points(it["item"]) for it in scored)
    checks = {
        "points_from_items": pts,
        "score_matches_items": hdr["score"] == pts if hdr["score"] is not None else None,
        "r_marks": sum(it["r"] for it in items),
        "cos_marks": sum(it["cos"] for it in items),
        "blank_items": [it["item"] for it in items if it["status"] is None],
    }
    checks["repeat_count_matches"] = (hdr["repeat_count"] == checks["r_marks"]) if isinstance(hdr["repeat_count"], int) else None
    checks["cos_count_matches"] = (hdr["cos_count"] == checks["cos_marks"]) if isinstance(hdr["cos_count"], int) else None
    if p2:
        checks["page2_matches_page1"] = {
            k: (p2.get(k) or "") == (hdr.get(k) or "")
            for k in ("establishment_name", "address", "city", "permit_number")
        }
    cites = cited_items(obs)
    cited = sorted({c["item"] for c in cites})
    out_items = sorted(it["item"] for it in scored)
    checks["cited_not_out"] = [n for n in cited if n not in out_items]
    checks["out_not_cited"] = [n for n in out_items if n not in cited]

    return {
        "doc_type": "inspection_report",
        "pdf_pages": n_pages,
        **meta,
        "header": hdr,
        "items": items,
        "item10_sanitizer_fill": item10_fill(titles.get(10)),
        "page2_header": p2,
        "temperature_table": table,
        "permit_expiration_date": expiration,
        "observations": "\n".join(l["text"] for l in obs),
        "observation_lines": obs,
        "cited_items": cites,
        "temperatures": temperature_readings(obs),
        "received_by": received_by,
        "inspector": inspector,
        "checks": checks,
        "parse_warnings": warnings,
    }


# --------------------------------------------------------------------------
# Outlook .msg (OLE compound file)

def _cfb_streams(data):
    """Minimal compound-file reader: {path: bytes} for every stream."""
    if data[:8] != OLE_MAGIC:
        raise ParseError("not an OLE file")
    ssz = 1 << struct.unpack_from("<H", data, 0x1E)[0]
    mssz = 1 << struct.unpack_from("<H", data, 0x20)[0]
    n_fat, first_dir, _, cutoff, first_mini, n_mini, first_difat, n_difat = struct.unpack_from("<8I", data, 0x2C)
    sec = lambda s: data[(s + 1) * ssz:(s + 2) * ssz]
    difat = list(struct.unpack_from("<109I", data, 0x4C))
    s = first_difat
    for _ in range(n_difat):
        vals = struct.unpack(f"<{ssz // 4}I", sec(s))
        difat += vals[:-1]
        s = vals[-1]
    fat = []
    for s in difat[:n_fat]:
        fat += struct.unpack(f"<{ssz // 4}I", sec(s))

    def chain(start, table):
        out, s, seen = [], start, set()
        while s < 0xFFFFFFFA and s not in seen:
            seen.add(s)
            out.append(s)
            s = table[s]
        return out

    dir_data = b"".join(sec(s) for s in chain(first_dir, fat))
    entries = []
    for i in range(len(dir_data) // 128):
        e = dir_data[i * 128:(i + 1) * 128]
        nlen = struct.unpack_from("<H", e, 64)[0]
        name = e[:max(nlen - 2, 0)].decode("utf-16-le", "replace")
        typ = e[66]
        left, right, child = struct.unpack_from("<3I", e, 68)
        start, size = struct.unpack_from("<II", e, 116)
        entries.append(dict(name=name, type=typ, left=left, right=right, child=child, start=start, size=size))
    root = entries[0]
    mini_stream = b"".join(sec(s) for s in chain(root["start"], fat))[:root["size"]]
    minifat = []
    for s in chain(first_mini, fat):
        minifat += struct.unpack(f"<{ssz // 4}I", sec(s))

    def read(e):
        if e["size"] < cutoff:
            return b"".join(mini_stream[s * mssz:(s + 1) * mssz] for s in chain(e["start"], minifat))[:e["size"]]
        return b"".join(sec(s) for s in chain(e["start"], fat))[:e["size"]]

    streams = {}

    def walk(idx, prefix, depth=0):
        stack = [idx]
        while stack:
            i = stack.pop()
            if i >= len(entries) or depth > 50:
                continue
            e = entries[i]
            stack += [e["left"], e["right"]]
            path = prefix + e["name"]
            if e["type"] == 2:
                streams[path] = read(e)
            elif e["type"] == 1:
                walk(e["child"], path + "/", depth + 1)

    walk(root["child"], "")
    return streams


def _filetime(b):
    v = struct.unpack("<Q", b)[0]
    return (dt.datetime(1601, 1, 1) + dt.timedelta(microseconds=v // 10)).isoformat() + "Z" if v else None


def parse_msg(path):
    data = Path(path).read_bytes()
    st = _cfb_streams(data)
    u = lambda k, pre="": st.get(f"{pre}__substg1.0_{k}", b"").decode("utf-16-le", "replace").rstrip("\x00") or None
    props = {}
    raw = st.get("__properties_version1.0", b"")
    for off in range(32, len(raw) - 15, 16):
        tag = struct.unpack_from("<I", raw, off)[0]
        if tag & 0xFFFF == 0x0040:
            props[tag >> 16] = _filetime(raw[off + 8:off + 16])
    atts = []
    for p in sorted({k.split("/")[0] for k in st if k.startswith("__attach_version1.0_")}):
        body = st.get(f"{p}/__substg1.0_37010102", b"")
        atts.append({
            "name": u("3707001F", p + "/") or u("3704001F", p + "/"),
            "mime": u("370E001F", p + "/"),
            "bytes": len(body),
            "magic": body[:4].hex(),
            "is_pdf": body[:4] == PDF_MAGIC,
        })
    body = u("1000001F") or ""
    return {
        "doc_type": "outlook_msg",
        "subject": u("0037001F"),
        "sender_name": u("0C1A001F"),
        "sender_email": u("5D01001F") or u("0C1F001F"),
        "to": u("0E04001F"),
        "sent": props.get(0x0039),
        "received": props.get(0x0E06),
        "body": body,
        "attachments": atts,
        "parse_warnings": [],
    }


def parse_attachment(path):
    head = Path(path).open("rb").read(8)
    try:
        if head.startswith(PDF_MAGIC):
            return parse_report(path)
        if head == OLE_MAGIC:
            return parse_msg(path)
        return {"doc_type": "unknown", "magic": head.hex(), "parse_warnings": ["unrecognised file type"]}
    except ParseError as e:
        return {"doc_type": "inspection_report", "parse_failed": True, "parse_warnings": [f"ParseError: {e}"]}


def _one(path):
    rel, att = Path(path).stem.split("_")
    rec = {"REL_OBJECTID": int(rel), "ATTACHMENTID": int(att), "file": Path(path).name}
    rec.update(parse_attachment(path))
    return rec


def parse_all(attach_dir, out_path, workers=4):
    """Parse every cached attachment ({REL_OBJECTID}_{ATTACHMENTID}.pdf, whatever
    its real type) and write one JSON line each, with the attachment's name and
    content type from the Attachments table."""
    from multiprocessing import Pool

    from common import load_raw

    att = load_raw("attachments").set_index("ATTACHMENTID")
    files = sorted(Path(attach_dir).glob("*_*.pdf"), key=lambda p: [int(x) for x in p.stem.split("_")])
    with Pool(workers) as pool, open(out_path, "w", encoding="utf-8") as fh:
        for rec in pool.imap(_one, [str(f) for f in files], chunksize=8):
            a = att.loc[rec["ATTACHMENTID"]]
            rec = {**{k: rec[k] for k in ("REL_OBJECTID", "ATTACHMENTID", "file")},
                   "ATT_NAME": a.ATT_NAME, "CONTENT_TYPE": a.CONTENT_TYPE,
                   **{k: v for k, v in rec.items() if k not in ("REL_OBJECTID", "ATTACHMENTID", "file")}}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(files)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--all"]:
        n = parse_all(sys.argv[2], sys.argv[3])
        print(f"parsed {n} attachments -> {sys.argv[3]}")
    else:
        for p in sys.argv[1:]:
            print(json.dumps(_one(p), indent=2, ensure_ascii=False))
