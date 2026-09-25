"""Parse the portal's server-rendered HTML pages.

inspection page (/frisco/inspection/?inspectionID=...): the web view of one
inspection. It renders violations with inline scripts that skip any item
number already shown, so it lists only the first comment per item and omits
OUT-W (warning) items. The PDF is the complete record; this page is parsed
only to cross-check the PDF parse.

permit page (/frisco/permit/?permitID=...): one establishment and every
inspection on file for it.
"""

import html
import re

from bs4 import BeautifulSoup

ITEM_COMMENT_RE = re.compile(
    r'var itemNum = "(?P<item>[^"]*)";\s*var comments = `(?P<comments>.*?)`\.trim\(\);', re.S
)
ITEM_TITLE_RE = re.compile(r'<div class="text-block-11">(?P<title>.*?)</div>', re.S)


def _clean(s):
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def parse_inspection_html(text):
    soup = BeautifulSoup(text, "lxml")
    out = {}
    h1 = soup.select_one(".section---inspection-area h1")
    out["type_purpose"] = _clean(h1.get_text()) if h1 else None
    date_div = h1.find_next_sibling("div") if h1 else None
    out["date_text"] = _clean(date_div.get_text().split("|")[0]) if date_div else None
    score = soup.select_one(".inspection-score-v2 strong")
    out["score"] = _clean(score.get_text()) if score else None
    name = soup.select_one(".establishment-main-header-section h2 a")
    out["establishment_name"] = _clean(name.get_text()) if name else None
    permit = re.search(r"permitID=([0-9A-Fa-f-]{36})", text)
    out["permit_id"] = permit.group(1) if permit else None

    comments = [
        {"item_number": m.group("item"), "comments": _clean(m.group("comments"))}
        for m in ITEM_COMMENT_RE.finditer(text)
    ]
    out["comment_blocks"] = comments
    titles = []
    for m in ITEM_TITLE_RE.finditer(text):
        t = _clean(m.group("title"))
        if t and t not in titles:
            titles.append(t)
    out["item_titles"] = titles
    return out


def parse_permit_html(text):
    soup = BeautifulSoup(text, "lxml")
    name = soup.select_one("h1.establishment-page-left-name")
    addr = soup.select_one(".establishment-page-left-address")
    inspections = []
    for sec in soup.select(".inspection-listing-section"):
        header = sec.select_one(".inspection-listing-header")
        date = sec.select_one(".inspection-listing-date")
        link = sec.select_one("a[href*=inspectionID]")
        score = sec.select_one(".inspection-listing-score")
        m = re.search(r"inspectionID=([0-9A-Fa-f-]{36})", link["href"]) if link else None
        inspections.append({
            "inspection_id": m.group(1) if m else None,
            "type_purpose": _clean(header.get_text()) if header else None,
            "date_text": _clean(date.get_text()) if date else None,
            "score": _clean(score.get_text()) if score else None,
        })
    return {
        "establishment_name": _clean(name.get_text()) if name else None,
        "address": _clean(addr.get_text()) if addr else None,
        "inspections": inspections,
    }
