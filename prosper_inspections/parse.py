"""Parse the health-inspection tables and the printed monthly counts out of the Town of
Prosper's Development Services monthly report PDFs.

For every PDF in data/raw/pdf_manifest.json (read from the PROSPER_CACHE download cache):

* Report month: the Archive Center title ("... Monthly Report August 2026"); for Wayback
  copies, the first "<Month> <year>" on page 1, checked against the file name.
* Health table rows: every pdfplumber table whose header row holds "Business Name" and
  "Pass/Fail" (the "Health Inspection Results" / "Health Inspections, <Month YYYY>" /
  "<Month YYYY> Health Inspections" tables and their "Continued" pages). Columns are
  mapped by their header labels, not by position, so a table that pdfplumber returns with
  an extra empty first column (June 2021) or with a three-row header (2026) is read the
  same way. Cells split over two table rows (a name or address that wraps with every
  other cell empty) are joined back onto the row above. The "Note:" row ends a table.
* Scoring note: the "Note:" paragraph under the table ("reviews 27 items ... demerit" vs
  "score from 0 to 100"), used to tag each report's scale era.
* Printed count: the "Health Inspections" line of the Health & Code Compliance summary.
  Columns are identified from the table's own header cells (e.g. "June 2020" / "YTD 2020")
  wherever pdfplumber returns the summary as a table; otherwise from the header text line
  printed above the numbers; the 2019 bar-chart layout gives only two unlabeled numbers.
* Page text: pdfplumber's extract_text() of every health page (independent of the table
  extraction), kept for the text cross-check and the hand review.
* Page numbers: the page number printed in the footer is removed before the tables are
  extracted (on one page, March 2023 p.14, it sits inside the last table cell and pdfplumber
  read it into that cell).
* Independent cell check: every text cell of every row is looked up in the page text read by a
  second PDF engine (PDFium, via pypdfium2), with whitespace ignored; cells not found verbatim
  are recorded per row (pdfium_cells_nonverbatim).
* Table heading: the printed title above each health page's table ("Health Inspection
  Results, June 2024", "Monthly Health Permits"), with the month it names.
* Page-1 department ("Development Services", "Public Works", ...), so that a report of another
  department saved under a Development Services file name is recognised.
* Narrative health text (2015 staff reports): "Eight (8) Health Inspections were performed in
  October" gives a count; paragraphs that mention the Health Inspector together with a
  closure or re-opening are kept as narrative events.

Outputs: PROSPER_CACHE/parsed_full.json.gz (everything, including the page text used for the
hand review) and data/raw/parsed_reports.json.gz (committed: the same records without page
text, and with private-looking addresses withheld: mobile units, other cities, and temporary
vendors not at a shared venue; see sanitized()). E-mail addresses
and phone numbers are scrubbed from all kept text.

    python parse.py            # parse every PDF in the manifest
    python parse.py ADID723    # parse one report and print it
"""

import re
import subprocess
import sys
import unicodedata

import pdfplumber
import pypdfium2

from common import CACHE, RAW, read_json, scrub, withhold_address, write_json

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
MON_RE = ("(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          "sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")


def norm(s):
    """Whitespace- and dash-normalized cell text ('Inspec‐\\ntion' -> 'Inspection')."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    # unmapped ligature glyphs in some 2021-2024 reports: 'ti' (415), 'tt' (425), 'ft' (332)
    s = s.replace("(cid:415)", "ti").replace("(cid:425)", "tt").replace("(cid:332)", "ft")
    s = s.replace("‐", "-").replace("‑", "-").replace("‒", "-").replace("–", "-")
    s = s.replace("—", "-").replace("­", "")
    s = re.sub(r"(\w)-\n(?=[a-z])", r"\1", s)  # hyphenated line break inside a word
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(\w)- (?=[a-z])", r"\1", s)   # 'Inspec- tion' -> 'Inspection'
    s = re.sub(r"(\w)- (?=[A-Z])", r"\1-", s)  # 'Follow- Up' -> 'Follow-Up'
    return s.strip()


def month_num(token):
    return [m[:3] for m in MONTHS].index(token.lower()[:3]) + 1


def month_from_text(text):
    m = re.search(r"\b" + MON_RE + r"\b[\s_.,-]*(20\d\d)\b", text, re.I)
    return f"{m.group(2)}-{month_num(m.group(1)):02d}" if m else None


# ----------------------------------------------------------------------------- table rows
HEADER_KEYS = {
    "name": re.compile(r"business\s*name", re.I),
    "type": re.compile(r"business\s*type|^type$", re.I),
    "address": re.compile(r"address|event", re.I),
    "score": re.compile(r"score|inspection|purpose", re.I),
    "result": re.compile(r"pass\s*/?\s*fail|result", re.I),
    "date": re.compile(r"date", re.I),
}


def find_header(table):
    """Index of the row holding 'Business Name' in a health table, else None."""
    for i, row in enumerate(table[:4]):
        cells = [norm(c) for c in row]
        joined = " | ".join(cells)
        if re.search(r"business\s*name", joined, re.I) and re.search(r"pass\s*/?\s*fail", joined, re.I) \
                and not re.search(r"square\s*footage", joined, re.I):
            return i
        if re.search(r"business\s*name", joined, re.I) and re.search(r"score", joined, re.I) \
                and not re.search(r"square\s*footage|\buse\b", joined, re.I):
            return i
    return None


def column_map(table, h):
    """Map the logical columns (name, type, address, score/purpose, pass/fail, and the rare
    inspection date) to cell indices, from header row h plus adjacent header-only rows."""
    ncol = max(len(r) for r in table)
    labels = [""] * ncol
    # header rows: the Business Name row and neighbouring rows that hold only header words
    rows = [h]
    if h > 0 and all(not norm(c) or re.search(r"score|inspection|purpose|pass|fail", norm(c), re.I)
                     for c in table[h - 1]):
        rows.insert(0, h - 1)
    j = h + 1
    while j < len(table) and j <= h + 3:
        cells = [norm(c) for c in table[j]]
        if all(not c or re.fullmatch(r"(score or|inspection|purpose|name|\(purpose\)|pass/fail)", c, re.I)
               for c in cells):
            rows.append(j)
            j += 1
        else:
            break
    for r in rows:
        for k, c in enumerate(table[r]):
            if norm(c):
                labels[k] = (labels[k] + " " + norm(c)).strip()
    cmap = {}
    for key in ("name", "type", "result", "date", "address", "score"):
        for k, lab in enumerate(labels):
            if k in cmap.values() or not lab:
                continue
            if HEADER_KEYS[key].search(lab):
                if key == "score" and re.search(r"business|address|pass", lab, re.I):
                    continue
                if key == "address" and re.search(r"business|score", lab, re.I):
                    continue
                if key == "score" and re.search(r"date", lab, re.I) and not re.search(r"score", lab, re.I):
                    continue
                cmap[key] = k
                break
    return cmap, labels, rows[-1] + 1


def is_note(cells):
    return bool(cells) and re.match(r"^\s*(\*?\s*note\b|\*)", cells[0] or "", re.I) is not None \
        and all(not c for c in cells[1:])


def table_rows(table, cmap, start):
    """Data rows of one health table as dicts, with wrapped-cell continuation rows merged."""
    out = []
    for i in range(start, len(table)):
        raw = [norm(c) for c in table[i]]
        if not any(raw):
            continue
        if is_note(raw) or re.match(r"^note\s*:", raw[0], re.I) or \
                (not raw[0] and any(re.match(r"^note\s*:", c, re.I) for c in raw)):
            break
        rec = {k: (raw[idx] if idx < len(raw) else "") for k, idx in cmap.items()}
        for k in ("name", "type", "address", "score", "result", "date"):
            rec.setdefault(k, "")
        extra = [c for j, c in enumerate(raw) if c and j not in cmap.values()]
        if re.fullmatch(r"business name.*", rec["name"], re.I):
            continue  # a repeated header inside the table
        continuation = out and not rec["score"] and not rec["result"] and (not rec["type"] or not rec["name"])
        if continuation:
            prev = out[-1]
            for k in ("name", "type", "address", "score", "result"):
                if rec[k]:
                    prev[k] = (prev[k] + " " + rec[k]).strip()
            prev["merged_rows"] += 1
            continue
        rec["extra_cells"] = extra
        rec["table_row"] = i
        rec["merged_rows"] = 0
        out.append(rec)
    return out


# ----------------------------------------------------------------------------- printed counts
def header_kind(cell):
    c = norm(cell)
    m = re.search(r"(YTD|year\s*to\s*date|" + MON_RE + r")\s*(20\d\d)?", c, re.I)
    y = re.search(r"(20\d\d)", c)
    if not m:
        return None
    kind = "ytd" if re.match(r"(YTD|year)", m.group(1), re.I) else "month"
    return kind, int(y.group(1)) if y else None


def count_from_tables(tables):
    for t in tables:
        for i, row in enumerate(t):
            cells = [norm(c) for c in row]
            if not cells or not re.match(r"^health inspections?$", cells[0], re.I):
                continue
            nums = [(k, c) for k, c in enumerate(cells) if re.fullmatch(r"[\d,]+", c)]
            if len(nums) < 2:
                continue
            # header: every row above the first data row, joined column by column (the month
            # name and the year are sometimes in separate rows: 'April' / '' / '2025')
            first_data = next((j for j, r in enumerate(t) if norm(r[0]) and
                               any(re.fullmatch(r"[\d,]+", norm(c)) for c in r[1:])), i)
            ncol = max(len(r) for r in t)
            labels = [" ".join(norm(r[k]) for r in t[:first_data] if k < len(r) and norm(r[k])).strip()
                      for k in range(ncol)]
            hdr = [header_kind(c) if c and re.search(r"20\d\d", c) else None for c in labels]
            cols = {}
            for k, c in nums:
                if k < len(hdr) and hdr[k]:
                    cols[k] = (hdr[k], int(c.replace(",", "")))
            if len(cols) == len(nums):
                return {"method": "table_header", "header": labels,
                        "values_raw": [c for _, c in nums],
                        "columns": [{"kind": h[0], "year": h[1], "value": v} for h, v in cols.values()]}
            return {"method": "table_no_header", "header": labels, "values_raw": [c for _, c in nums],
                    "columns": None}
    return None


def count_from_text(text):
    # the numbers must be on the label's own line (in the 2019 bar chart the next line's number
    # belongs to the next metric)
    m = re.search(r"Health Inspections[ \t]+([\d,]+)[ \t]+([\d,]+)(?:[ \t]+([\d,]+)[ \t]+([\d,]+))?", text)
    if not m:
        return None
    vals = [v for v in m.groups() if v]
    # header text: the nearest line above that names months / YTD
    before = text[:m.start()].splitlines()
    header = None
    for line in reversed(before[-8:]):
        toks = re.findall(r"(YTD\s*20\d\d|" + MON_RE + r"\s*20\d\d)", line, re.I)
        if len(toks) >= len(vals):
            header = [t[0] if isinstance(t, tuple) else t for t in toks]
            break
    if header:
        cols = []
        for h, v in zip(header, vals):
            k = header_kind(h)
            cols.append({"kind": k[0], "year": k[1], "value": int(v.replace(",", ""))})
        return {"method": "text_header", "header": header, "values_raw": vals, "columns": cols,
                "line": norm(text[m.start():m.end()])}
    return {"method": "text_no_header", "values_raw": vals, "columns": None,
            "line": norm(text[m.start():m.end()])}


def count_from_chart(text):
    """Feb 2019 style bar chart: '42\\nHealth Inspections 31' (two unlabeled values)."""
    m = re.search(r"(\d+)\s*\n\s*Health Inspections\s+(\d+)\s*\n", text)
    if m and "Year-to-Date" in text:
        return {"method": "chart_two_values", "values_raw": [m.group(1), m.group(2)], "columns": None,
                "line": norm(m.group(0))}
    return None


# ----------------------------------------------------------------------------- independent text check
RESULT_TOKEN = re.compile(r"^(Pass(?:ed)?|Fail(?:ed)?|Pas|N/?A|Follow[- ]?Up|Complaint|Re-?Inspection|"
                          r"Temporary Closure|Closed?|CO|C/O|Non[- ]?Compliant|Compliant|Approved|"
                          r"Preliminary|Kiosk|Remodel|Fire|\d{1,3})\b", re.I)
DASHES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-"})


def layout_text(path, page):
    """pdftotext -layout text of one page (poppler: a second, independent text engine)."""
    try:
        out = subprocess.run(["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(path), "-"],
                             capture_output=True, timeout=120, check=True)
        return out.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return None


def layout_results(text):
    """Result-column tokens and numeric scores from the pdftotext layout text.

    On every line below a 'Pass/Fail' header, the cell (a text run separated by 2+ spaces)
    whose centre is nearest the header's centre is taken, if it lies within 9 characters of
    it and starts with a result-like word: the first line of each result cell gives one token
    per table row (a wrapped second line such as 'No. 2' does not match). Separately, every
    cell nearest the 'Score' header's centre (or the Pass/Fail centre) that starts with a 1-3
    digit number is taken as a printed score, e.g. '66/Temporary Closure' -> 66.
    Returns (result_tokens, scores)."""
    if not text:
        return None, None
    centre = score_centre = None
    toks, scores = [], []
    for lineno, line in enumerate(text.translate(DASHES).splitlines()):
        m = re.search(r"Pass\s*/\s*Fail", line)
        s = re.search(r"\bScore\b", line)
        if s and re.search(r"Business|Pass|Score or|^\s*Score\s*$", line):
            score_centre = s.start() + 4
        if m:
            centre = (m.start() + m.end()) / 2
            continue
        if centre is None:
            continue
        if re.match(r"^\s*\*?\s*Note\b", line):
            centre = score_centre = None
            continue
        cells = [(c.start() + len(c.group()) / 2, c.group()) for c in re.finditer(r"\S+(?: \S+)*", line)]
        if not cells or (len(cells) == 1 and re.fullmatch(r"\d+( \| P ?a ?g ?e)?", cells[0][1])):
            continue  # blank line or the page number
        dist, cell = min((abs(c - centre), t) for c, t in cells)
        mt = RESULT_TOKEN.match(cell)
        if dist <= 9 and mt:
            toks.append((mt.group(1), lineno))
        for ctr in (score_centre, centre):
            if ctr is None:
                continue
            d, c = min((abs(x - ctr), t) for x, t in cells)
            ms = re.match(r"^(\d{1,3})(?:\s*/.*)?$", c)
            if d <= 9 and ms:
                scores.append(int(ms.group(1)))
                break
    return toks, scores


# ----------------------------------------------------------------------------- page number, second engine
FOOTER_NUMBER = re.compile(r"\d{1,3}(?:\s*\|\s*P\s*a\s*g\s*e)?")


def without_page_number(page):
    """The page with the characters of its footer page number ('14', '12 | Page') removed, and
    the bounding box of that number (None when the bottom line is not a page number)."""
    words = page.extract_words()
    if not words:
        return page, None
    bottom = max(w["top"] for w in words)
    line = sorted((w for w in words if w["top"] >= bottom - 2), key=lambda w: w["x0"])
    text = " ".join(w["text"] for w in line)
    if bottom < page.height - 40 or not FOOTER_NUMBER.fullmatch(text):
        return page, None
    box = (min(w["x0"] for w in line) - 0.5, min(w["top"] for w in line) - 0.5,
           max(w["x1"] for w in line) + 0.5, max(w["bottom"] for w in line) + 0.5)

    def keep(obj):
        return not (obj.get("object_type") == "char" and obj["x0"] >= box[0] and obj["x1"] <= box[2]
                    and obj["top"] >= box[1] and obj["bottom"] <= box[3])
    return page.filter(keep), box


PDFIUM_MAP = str.maketrans({"\u019f": "ti", "\u01a9": "tt", "\u014c": "ft",  # unmapped ligature glyphs
                            "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
                            "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
                            "\ufffe": None, "\u00ad": None, "\u0002": None, "\u200b": None})
LIGATURE_LETTERS = re.compile(r"ffi|ffl|ff|fi|fl|ft|tt|ti")


def cell_key(s):
    """Text compared by the independent cell check: NFKC, ligature glyphs and dashes and quotes
    unified, all whitespace removed, lower case."""
    s = unicodedata.normalize("NFKC", s or "").replace("(cid:415)", "ti").replace("(cid:425)", "tt") \
        .replace("(cid:332)", "ft").translate(PDFIUM_MAP)
    return re.sub(r"\s+", "", s).lower()


def check_cells(row, page_key):
    """Look up each non-empty text cell of a parsed row in the PDFium page text. Returns
    (cells checked, {field: level}) for the cells not found verbatim; level is 'ignoring hyphens'
    (a word hyphenated across a line break), 'ignoring ligature letters', or 'not found'."""
    checked, out = 0, {}
    for k in ("name", "type", "address", "score", "result", "date"):
        v = cell_key(row.get(k, ""))
        if not v:
            continue
        checked += 1
        if v in page_key:
            continue
        if v.replace("-", "") in page_key.replace("-", ""):
            out[k] = "ignoring hyphens"
        elif LIGATURE_LETTERS.sub("", v.replace("-", "")) in LIGATURE_LETTERS.sub("", page_key.replace("-", "")):
            out[k] = "ignoring ligature letters"
        else:
            out[k] = "not found"
    return checked, out


# ----------------------------------------------------------------------------- headings, department, narrative
TITLE = re.compile(r"^(?:(?P<pre>[A-Za-z]+\.?\s+20\d\d)\s+)?(?P<title>health inspection results|health inspections?|"
                   r"monthly health permits|health permits)(?P<rest>(?:\s*[,:]?\s*[A-Za-z]+\.?\s+20\d\d)?"
                   r"(?:\s*-\s*continued)?)\s*$", re.I)


def table_heading(text):
    """The printed title nearest above the first 'Business ...' header line of a health page."""
    lines = [norm(l) for l in (text or "").splitlines()]
    first = next((i for i, l in enumerate(lines) if re.match(r"^business\b", l, re.I)), None)
    if first is None:
        return None
    for l in reversed(lines[max(0, first - 4):first]):
        m = TITLE.match(l)
        if m:
            month = month_from_text((m.group("pre") or "") + " " + (m.group("rest") or ""))
            return {"text": l, "month": month,
                    "kind": "inspections" if re.search(r"inspection", m.group("title"), re.I) else "permits"}
    return None


DEPARTMENTS = [("Development Services", r"development\s+services|monthly\s+development|development\s+and\s+community"),
               ("Public Works", r"public\s+works"), ("Fire Department", r"fire\s+(department|chief)"),
               ("Parks and Recreation", r"parks\s+(and|&)\s+recreation")]


def page1_department(text):
    """The department named first in the first lines of page 1."""
    head = " ".join((text or "").splitlines()[:8])
    hits = [(m.start(), name) for name, rx in DEPARTMENTS for m in [re.search(rx, head, re.I)] if m]
    return min(hits)[1] if hits else None


NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
NUMBER_WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90})
NARRATIVE_COUNT = re.compile(r"\b([A-Za-z]+(?:-[A-Za-z]+)?)\s*\((\d+)\)\s+Health\s+Inspections?\s+(?:were|was)\s+"
                             r"(?:performed|conducted)(?:\s+in\s+([A-Za-z]+))?", re.I)


def number_word(w):
    parts = w.lower().split("-")
    try:
        return sum(NUMBER_WORDS[p] for p in parts)
    except KeyError:
        return None


def narrative_count(text):
    m = NARRATIVE_COUNT.search(norm(text))
    if not m:
        return None
    return {"text": m.group(0), "value": int(m.group(2)), "word_value": number_word(m.group(1)),
            "month_word": m.group(3)}


def narrative_events(text):
    """Bulleted paragraphs that mention the Health Inspector together with a closure, re-opening,
    suspension or infestation (the 2015 staff reports describe these in prose)."""
    out = []
    for para in re.split(r"[\uf0b7\u2022\u25cf\u25aa]", text or ""):
        p = norm(para)
        if re.search(r"health inspector", p, re.I) and re.search(r"clos|re-?open|suspend|infest|imminent", p, re.I) \
                and not re.search(r"reviews 27 items|score from 0 to 100", p, re.I):
            out.append(scrub(p))
    return out


# ----------------------------------------------------------------------------- per report
def scale_note(text):
    if re.search(r"27 items|demerit value|Best possible score is 0|more than 30 demerits", text, re.I):
        return "demerit_27item"
    if re.search(r"score from 0 to 100|100 being the highest", text, re.I):
        return "score_100"
    return None


def parse_pdf(path):
    rec = {"pages": 0, "health_pages": [], "rows": [], "count": None, "scale_note": None,
           "scale_note_page": None, "page1_month": None, "page1_department": None, "headers": [], "page_text": {},
           "headerless_tables": [], "no_inspections_statement": None, "staff_mentions": 0,
           "table_headings": [], "page_number_overlaps_table": [], "narrative_count": None, "narrative_events": []}
    pdfium_doc = pypdfium2.PdfDocument(str(path))
    with pdfplumber.open(path) as pdf:
        rec["pages"] = len(pdf.pages)
        prev_health = False
        prev_cmap = None
        for pn, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if pn == 1:
                rec["page1_month"] = month_from_text(norm(text))
                rec["page1_department"] = page1_department(text)
            has_health_word = bool(re.search(r"Health Inspection", text, re.I))
            m = re.search(r"No Health Inspections were (?:performed|conducted)[^\n.]*", text, re.I)
            if m:
                rec["no_inspections_statement"] = {"page": pn, "text": norm(m.group(0))}
                rec["page_text"][str(pn)] = scrub(text)
            nc = narrative_count(text) if rec["narrative_count"] is None else None
            if nc:
                rec["narrative_count"] = dict(nc, page=pn)
                rec["page_text"][str(pn)] = scrub(text)
            for ev in narrative_events(text):
                rec["narrative_events"].append({"page": pn, "text": ev})
                rec["page_text"][str(pn)] = scrub(text)
            tables = []
            if has_health_word or prev_health or "Business Name" in text:
                tpage, box = without_page_number(page)
                found = tpage.find_tables()
                tables = [t.extract() for t in found]
                if box and any(box[0] < t.bbox[2] and t.bbox[0] < box[2] and box[1] < t.bbox[3] and t.bbox[1] < box[3]
                               for t in found):  # the page number overlaps a table (it was read into a cell)
                    rec["page_number_overlaps_table"].append(pn)
            if rec["count"] is None and re.search(r"Health Inspections", text):
                c = count_from_tables(tables) or count_from_text(text) or count_from_chart(text)
                if c:
                    c["page"] = pn
                    rec["count"] = c
            page_rows = []
            for ti, t in enumerate(tables):
                h = find_header(t)
                if h is None:
                    continue
                cmap, labels, start = column_map(t, h)
                if "name" not in cmap or "result" not in cmap:
                    continue
                rec["headers"].append({"page": pn, "table": ti, "labels": labels, "cmap": cmap,
                                       "ncols": max(len(r) for r in t)})
                prev_cmap = (cmap, max(len(r) for r in t))
                for r in table_rows(t, cmap, start):
                    r["page"] = pn
                    r["table"] = ti
                    page_rows.append(r)
            if not page_rows and prev_health and tables:
                # a continuation page without a header row: record it for review
                for ti, t in enumerate(tables):
                    ncols = max(len(r) for r in t)
                    if prev_cmap and ncols == prev_cmap[1]:
                        rec["headerless_tables"].append({"page": pn, "table": ti, "rows": len(t),
                                                         "first": [norm(c) for c in t[0]]})
            is_health = bool(page_rows)
            if is_health:
                rec["health_pages"].append(pn)
                rec["page_text"][str(pn)] = scrub(text)
                h = table_heading(text)
                if h:
                    rec["table_headings"].append(dict(h, page=pn))
                page_key = cell_key(pdfium_doc[pn - 1].get_textpage().get_text_range())
                for r in page_rows:
                    r["pdfium_cells_checked"], r["pdfium_cells_nonverbatim"] = check_cells(r, page_key)
                lt = layout_text(path, pn)
                rec.setdefault("layout_text", {})[str(pn)] = scrub(lt) if lt else None
                toks, nums = layout_results(lt)
                rec.setdefault("layout_results", {})[str(pn)] = [t for t, _ in toks] if toks is not None else None
                rec.setdefault("layout_result_lines", {})[str(pn)] = [i for _, i in toks] if toks is not None else None
                rec.setdefault("layout_scores", {})[str(pn)] = nums
                for k, r in enumerate(page_rows):
                    r["row_on_page"] = k + 1
                rec["rows"] += page_rows
            if is_health or (has_health_word and re.search(r"Note:", text)):
                s = scale_note(norm(text))
                if s and rec["scale_note"] is None:
                    rec["scale_note"] = s
                    rec["scale_note_page"] = pn
            # the count page text is kept too
            if rec["count"] and rec["count"]["page"] == pn and str(pn) not in rec["page_text"]:
                rec["page_text"][str(pn)] = scrub(text)
            prev_health = is_health
    pdfium_doc.close()
    for r in rec["rows"]:
        for k in ("name", "type", "address", "score", "result"):
            r[k] = scrub(r[k])
        r["extra_cells"] = [scrub(c) for c in r["extra_cells"]]
    rec["staff_mentions"] = sum(len(re.findall(r"food manager|\bCFM\b|food handler", t or "", re.I))
                                for t in rec["page_text"].values())
    return rec


TEMPORARY = re.compile(r"\btemp|booth|event|vendor", re.I)


def addr_key(a):
    return re.sub(r"[^A-Z0-9]", "", (a or "").upper())[:14]


def public_venues(parsed):
    """Addresses a temporary vendor may be listed at without it being a private address: event
    venues shared by 3+ differently named vendors, and any address also used by a fixed business."""
    names, fixed = {}, set()
    for p in parsed:
        for r in p["rows"]:
            k = addr_key(r["address"])
            if not k or not re.match(r"\d", k):
                continue
            if TEMPORARY.search(r["type"]) or re.search(r"temporary food|temp food", r["score"], re.I):
                names.setdefault(k, set()).add(r["name"].lower())
            elif not withhold_address(r["type"], r["address"]):
                fixed.add(k)
    return fixed | {k for k, v in names.items() if len(v) >= 3}


def sanitized(p, venues):
    """The committed copy of a parsed report: no page text; the addresses of mobile units, of
    places in another city, and of temporary vendors not at a known venue are withheld."""
    q = {k: v for k, v in p.items() if k not in ("page_text", "layout_text")}
    rows = []
    for r in p["rows"]:
        r = dict(r)
        a = r["address"]
        temp = TEMPORARY.search(r["type"]) or re.search(r"temporary food|temp food", r["score"], re.I)
        private = withhold_address(r["type"], a) or (
            temp and re.match(r"\d", addr_key(a)) and addr_key(a) not in venues)
        if private and a not in ("", "N/A", "NA", "N/a"):
            r["address"] = "[withheld]"
            r["extra_cells"] = []
        rows.append(r)
    q["rows"] = rows
    return q


def report_month(entry, parsed):
    if entry["source"] == "archive_center":
        return entry["title_month"], "archive_title"
    if parsed["page1_month"]:
        return parsed["page1_month"], "pdf_page1"
    return entry.get("title_month"), "file_name"


def main(argv):
    manifest = read_json(RAW / "pdf_manifest.json")
    if argv:
        manifest = [m for m in manifest if m["key"] in argv]
    out = []
    for i, m in enumerate(manifest, 1):
        p = parse_pdf(CACHE / m["cache_path"])
        month, how = report_month(m, p)
        p.update(key=m["key"], source=m["source"], url=m["url"], sha256=m["sha256"],
                 report_month=month, report_month_from=how, title=m["title"],
                 title_month=m.get("title_month"))
        out.append(p)
        c = p["count"]
        print(f"[{i}/{len(manifest)}] {m['key']:<60.60} {month} rows={len(p['rows']):3d} "
              f"pages={p['health_pages']} scale={p['scale_note']} count={c and c['values_raw']} "
              f"{c and c['method']}", flush=True)
    if argv:
        import json
        for p in out:
            print(json.dumps({k: v for k, v in p.items() if k != "page_text"}, indent=1)[:20000])
        return
    out.sort(key=lambda p: (p["report_month"] or "", p["key"]))
    write_json(CACHE / "parsed_full.json.gz", out, gz=True)  # with page text: stays in the cache
    venues = public_venues(out)
    write_json(RAW / "parsed_reports.json.gz", [sanitized(p, venues) for p in out], gz=True)


if __name__ == "__main__":
    main(sys.argv[1:])
