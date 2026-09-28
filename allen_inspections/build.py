"""Build the Allen inspection tables from the raw EnerGov pull and the score-report PDFs.

Inputs (data/raw/, written by fetch.py and fetch_pdfs.py):
  listing.json.gz            every food-related inspection row from the portal's search
  details.json.gz            getById detail for every inspection that is not cancelled
  permits_licenses.json.gz   food / day-care operational permits and food licenses
  status_reconciliation.json the portal's per-status totals
  fetch_meta.json, pdf_manifest.json, pdfs/*.pdf

Outputs (data/):
  inspections.csv     one row per inspection (test / training entries kept, flagged is_test_record)
  establishments.csv  one row per business (EnerGov BusinessId), with establishment_group_id and roll-ups per
                      BusinessId and per group (test records left out)
  pdf_scores.csv      one row per line of every parsed score report, matched to EnerGov
  validation.json     every cross-check (see README), and readme_phrases: every number-bearing README phrase

Privacy in the tables: probable home addresses are published as city, state and ZIP only (withhold_address,
propagate_withholding); comments are scrubbed again (common.CommentScrubber). Counts that depend on "today"
use the data's as-of date (as_of_date), so a rebuild from the same raw files gives the same numbers.

    python build.py        # reads only data/raw/; no network, no HTTP cache
"""

import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from common import (DATA, GETBYID_URL, PORTAL_INSPECTION_URL, PORTAL_LICENSE_URL, PORTAL_OP_PERMIT_URL, RAW, TZ,
                    CommentScrubber, read_gz_json)
from parse_pdfs import parse_pdf

CHI = ZoneInfo(TZ)
FAQ_URL = ("https://www.cityofallen.org/departments/community_enhancement/health_and_food_safety/"
           "food_safety.php")
CANCELLED = {"Cancelled", "BL - Cancelled"}
FOLLOWUP_STATUSES = {"Followup Reinspection", "Follow-up Reinspection"}

# report periods the PDFs do not print themselves (from the file name / link text)
PERIOD_OVERRIDES = {
    "allen_2024Q2_document_archive.pdf": ("2024-04-01", "2024-06-30", "file name: April to June 2024"),
    "allen_2024Q2_wayback.pdf": ("2024-04-01", "2024-06-30", "file name: April to June 2024"),
}


# ----------------------------------------------------------------------------- dates
def utc_to_local_date(s):
    """EnerGov detail dates are UTC ('...Z'); convert to the Allen calendar date."""
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(CHI).date()


def naive_date(s):
    """Listing / permit dates carry no zone and are already local."""
    return datetime.fromisoformat(s).date() if s else None


def iso(d):
    return d.isoformat() if d else None


# ----------------------------------------------------------------------------- comments
# Scores are 2-3 digit numbers from 50 to 100 (the lowest report score is 55, the lowest comment score 61):
# a one-digit number or one below 50 at the start of a comment is a list number or a count, not a score.
SCORE_MIN = 50
SCORE_ONLY = re.compile(r"^\s*(\d{2,3})\s*[.!]?\s*$")
# a score followed by punctuation and text ('84. Follow up required', '72 - NOV issued'); not a date or
# a decimal ('08/31/2023', '8.5'), which is why '/' and a digit after '.' are excluded
SCORE_THEN_TEXT = re.compile(r"^\s*(\d{2,3})\s*(?:[.,;:)!](?!\d)|\s-|-\s)\s*\S")
SMALL_NUMBER = re.compile(r"^\s*\d\s*[.!]?\s*$")
DATE_LIKE = re.compile(r"^\s*\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b")
SCORE_THEN_WORD = re.compile(r"^\s*(\d{2,3})\s+(?=(?:follow|routine|nov|pass|fail|complaint|re-?inspection)\b)",
                             re.I)
NUMBER_THEN_WORD = re.compile(r"^\s*(\d{1,3})\s+[A-Za-z(]")
FOLLOWUP_TEXT = re.compile(r"^\s*(re-?inspection|follow[\s-]*up|pass|fail|passed|failed)\b[\s:.\-–]*"
                           r"(pass(ed)?|fail(ed)?)?\b[\s.!]*$", re.I)


CLOSURE_RX = re.compile(r"\b(clos(?:e|ed|ing|ure)|suspen(?:d|ded|sion)|cease[sd]?|shut\s*down|re-?open(?:ed|ing)?|"
                        r"imminent\s+health\s+hazard)\b", re.I)


def closure_keyword(c):
    """Keyword screen only: the matching word, or None. 'closed' often means something else
    (a closed container, business closed for the day); read the comment before calling it a closure."""
    m = CLOSURE_RX.search(c) if isinstance(c, str) else None
    return m.group(1).lower() if m else None


def classify_comment(c):
    """(comment_kind, comment_score). The score is taken only from a leading 2-3 digit integer from 50
    to 100 that stands alone or is followed by punctuation ('91', '84. Follow up required', '72 - NOV')
    or by an inspection word ('82 Follow up required')."""
    if not isinstance(c, str) or not c.strip():
        return "blank", None
    if DATE_LIKE.match(c):
        return "starts_with_date", None
    for rx, kind in ((SCORE_ONLY, "score_only"), (SCORE_THEN_TEXT, "score_then_text")):
        m = rx.match(c)
        if m:
            n = int(m.group(1))
            return ((kind, n) if SCORE_MIN <= n <= 100 else
                    ("number_over_100", None) if n > 100 else ("number_below_50", None))
    if SMALL_NUMBER.match(c):
        return "number_below_50", None
    m = SCORE_THEN_WORD.match(c)
    if m and SCORE_MIN <= int(m.group(1)) <= 100:
        return "score_then_text", int(m.group(1))  # '82 Follow up required'
    if NUMBER_THEN_WORD.match(c):
        return "number_then_words", None  # e.g. '3 Sink Compartment Fixed', '15 days NOV': not a score
    if FOLLOWUP_TEXT.match(c):
        return "result_text", None
    return "narrative", None


# ----------------------------------------------------------------------------- names/addresses
NAME_STOP = {"THE", "LLC", "INC", "CO", "CORP", "LTD", "AND", "OF", "ALLEN", "TX", "DBA", "LP", "RESTAURANT",
             "STORE", "CAFE", "FOOD", "ESTABLISHMENT"}
DIRS = {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST"}
SUFFIXES = {"ST", "STREET", "RD", "ROAD", "DR", "DRIVE", "AVE", "AVENUE", "PKWY", "PARKWAY", "BLVD", "BOULEVARD",
            "LN", "LANE", "WAY", "CIR", "CIRCLE", "CT", "COURT", "HWY", "HIGHWAY", "EXPY", "EXPWY", "EXPRESSWAY",
            "FWY", "TRL", "PL", "PLZ", "LOOP", "STE", "SUITE", "BLDG", "UNIT"}


def norm_name(s):
    if not isinstance(s, str):
        return ""
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))  # Café -> Cafe
    s = s.upper().replace("&", " AND ").replace("’", "'")
    s = re.sub(r"'S\b", "S", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = s.replace("WAL MART", "WALMART").replace("7 ELEVEN", "7ELEVEN").replace("CHICK FIL A", "CHICKFILA")
    kept = [t for t in s.split() if t not in NAME_STOP]
    # a name made only of stop words ('Allen Cafe', 'The Food Store') keeps its words, so it can still match
    return " ".join(kept or s.split())


def jaccard(a, b):
    A, B = set(a.split()), set(b.split())
    return len(A & B) / len(A | B) if A and B else 0.0


def street_number(s):
    m = re.match(r"^\s*(\d+)", s or "")
    return m.group(1) if m else None


def street_words(s):
    """Street-name words, without number, direction, suffix and unit."""
    s = re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper())
    s = re.sub(r"\bSTATE HIGHWAY\b|\bSTATE HWY\b|\bSH\s*(?=\d)", "SH ", s)
    toks = s.split()
    if toks and toks[0].isdigit():
        toks = toks[1:]
    out = []
    for t in toks:
        if t in SUFFIXES or t.startswith("#"):
            break
        if t in DIRS and not out:
            continue
        out.append(t)
    return " ".join(out)


def listing_street(addr):
    """Street line of an EnerGov address dict ('1201 E MAIN ST')."""
    if not addr:
        return ""
    parts = [addr.get("AddressLine1"), addr.get("PreDirection"), addr.get("AddressLine2"), addr.get("StreetTypeName"),
             addr.get("PostDirection")]
    return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()


def address_key(addr):
    """(number, street words, unit) from an EnerGov address dict; None without a street number."""
    if not addr or not street_number(addr.get("AddressLine1") or ""):
        return None
    unit = " ".join(x.strip().upper() for x in (addr.get("AddressLine3"), addr.get("UnitOrSuite")) if x and x.strip())
    return (street_number(addr["AddressLine1"]), street_words(listing_street(addr)), unit)


def unit_token(s):
    """Normalised unit / suite ('Ste. 165', 'Unit: #140', 'A 100' -> '165', '140', '100')."""
    if not isinstance(s, str) or not s.strip():
        return None
    m = re.search(r"(?:#|\b(?:STE|SUITE|UNIT|SPACE|SP)\b\.?:?)\s*#?\s*([A-Z]?\d+[A-Z]?|[A-Z])\b", s.upper())
    if m:
        return m.group(1)
    nums = re.findall(r"[A-Z]?\d+[A-Z]?", s.upper())  # building + unit ('A 100'): the unit is the last number
    if nums:
        return nums[-1]
    t = re.sub(r"[^A-Z0-9]", "", s.upper())
    return t or None


def pdf_unit(street):
    """Unit / suite printed in a report's street column ('190 E. Stacy Rd #700 Bldg 700' -> '700')."""
    m = re.search(r"(?:#|\b(?:STE|SUITE|UNIT|SPACE)\b\.?:?)\s*#?\s*([A-Z]?\d+[A-Z]?)\b", (street or "").upper())
    return m.group(1) if m else None


DAYCARE_TYPES = re.compile(r"Day ?Care", re.I)


def inspection_kind(type_name):
    return "day care" if DAYCARE_TYPES.search(type_name) else "food"


# ----------------------------------------------------------------------------- privacy: addresses
# Mobile and temporary vendors (food trucks, caterers, event stands) often register a home address.
MOBILE_CATEGORY = re.compile(r"Truck|Mobile|Temporary|Special Event|Catering|Construction Site|Seasonal", re.I)


def privacy_key(s):
    """(street number, first street word) of an address, from an EnerGov address dict or a display string
    ('733 Cliffview DR Dallas TX 75217' -> ('733', 'CLIFFVIEW')); None without a street number. Coarser than
    address_key on purpose, so that spelling variants ('Hatherbrook' / 'Heatherbrook' do not both occur,
    but 'Heatherbrook' and 'Heatherbrook Plano') still meet."""
    if isinstance(s, dict):
        s = listing_street(s)
    num = street_number(s) if isinstance(s, str) else None
    words = street_words(s).split() if num else []
    return (num, words[0]) if words else None


def exempt_site_keys(pl):
    """Street addresses in Allen that are business locations, not homes: the address on the permit of a fixed
    (not mobile or temporary) establishment, whether EnerGov types it as the site or as the mailing address
    (for fixed establishments the two are the premises: schools, day cares, restaurants), and any permit site
    that several businesses use (an event venue, a park, a shopping centre). A vendor that uses one of these
    as its mailing address keeps it in the tables."""
    fixed, users = set(), defaultdict(set)
    for source, d in pl.items():
        for r in d["rows"]:
            addr = r.get("Address") or {}
            if (addr.get("City") or "").strip().upper() != "ALLEN":
                continue
            k = privacy_key(addr)
            if not k:
                continue
            if source != "license_special_event_food" and not MOBILE_CATEGORY.search(r.get("CaseType") or ""):
                fixed.add(k)
            if addr.get("AddressTypeName") == "Site Address":
                users[k].add((r.get("BusinessId") or r["CaseId"]).lower())
    return fixed | {k for k, u in users.items() if len(u) >= 2}


def withhold_address(addr, mobile, exempt=frozenset()):
    """First pass of the address rule: True when only the city, state and ZIP of this EnerGov address are
    published. A mailing address, and any address of a mobile or temporary vendor, is withheld unless it is
    an exempt street address in Allen (exempt_site_keys). Site addresses of fixed establishments are
    published in full. propagate_withholding() then withholds the same street address on every record."""
    if not addr or not any((addr.get(k) or "").strip() for k in ("AddressLine1", "AddressLine2", "POBox")):
        return False
    if privacy_key(addr) in exempt:
        return False
    return mobile or addr.get("AddressTypeName") == "Mailing Address"


def city_state_zip(addr):
    parts = [(addr.get("City") or "").strip(), (addr.get("StateName") or "").strip(),
             (addr.get("PostalCode") or "").strip()[:5]]
    return " ".join(p for p in parts if p) or None


# ----------------------------------------------------------------------------- test records
# The city's own app-testing / training establishment ("Training Food Establishment", permit
# description "For App Testing and Training Purposes - Only") and comments that say "Testing".
TEST_ESTABLISHMENT_NAME = re.compile(r"^\s*training\s+food\s+establishment\s*$", re.I)
TEST_COMMENT = re.compile(r"^\s*(?:test(?:ing)?(?:\s+(?:purposes|again|the\s+app(?:\s+again)?))?[\s.!]*$"
                          r"|what\s+happens\s+if\s+i\s+type\b)", re.I)
TEST_BASIS_ESTABLISHMENT = "city app testing/training establishment (permit description says so)"
TEST_BASIS_COMMENT = "probable test entry (inference from comment text)"


# ----------------------------------------------------------------------------- establishments
def build_establishments(pl, exempt=frozenset()):
    """Permits/licenses -> (permit table keyed by lower CaseId, business table keyed by lower BusinessId)."""
    permits = []
    for source, d in pl.items():
        for r in d["rows"]:
            category = (r.get("CaseType") or "").strip() or None
            company = (r.get("CompanyName") or "").strip() or None
            mobile = bool(MOBILE_CATEGORY.search(category or "")) or source == "license_special_event_food"
            withheld = withhold_address(r.get("Address"), mobile, exempt)
            full = (r.get("AddressDisplay") or "").strip() or None
            permits.append({
                "source": source, "module": d["module"],
                "case_id": r["CaseId"].lower(), "case_number": r["CaseNumber"],
                "business_id": (r["BusinessId"] or "").lower() or None,
                "company_name": company,
                "dba": (r.get("DBA") or "").strip() or None,
                "category": category,
                "permit_status": r.get("CaseStatus"), "business_status": r.get("BusinessStatus"),
                "issue_date": naive_date(r.get("IssueDate")), "apply_date": naive_date(r.get("ApplyDate")),
                "expire_date": naive_date(r.get("ExpireDate")),
                "opened_date": naive_date(r.get("OpenedDate")), "closed_date": naive_date(r.get("ClosedDate")),
                # published address: city/state/ZIP only for withheld addresses (see withhold_address)
                "address": city_state_zip(r["Address"]) if withheld else full,
                "address_withheld": withheld, "address_full": full, "mobile": mobile,
                "address_reduced": city_state_zip(r["Address"]) if r.get("Address") else None,
                "pkey": privacy_key(r.get("Address")),
                "street": listing_street(r.get("Address")),
                "parcel": None if withheld else (r.get("MainParcel") or None),
                "parcel_full": r.get("MainParcel") or None,
                "akey": address_key(r.get("Address")),
                "kind": "day care" if source == "op_permit_daycare" else "food",
                "test": bool(r.get("DescriptionMarksTestRecord")) or bool(TEST_ESTABLISHMENT_NAME.match(company or "")),
                "portal_url": (PORTAL_OP_PERMIT_URL if d["module"] == 12 else PORTAL_LICENSE_URL).format(id=r["CaseId"]),
            })
    by_case = {p["case_id"]: p for p in permits}
    by_bus = defaultdict(list)
    for p in permits:
        if p["business_id"]:
            by_bus[p["business_id"]].append(p)
    businesses = {}
    for bid, ps in by_bus.items():
        ps = sorted(ps, key=lambda p: (p["issue_date"] or p["apply_date"] or date.min, p["case_number"]))
        latest = ps[-1]
        op = [p for p in ps if p["module"] == 12]
        businesses[bid] = {
            "establishment_id": bid,
            "name": latest["company_name"],
            "dba": next((p["dba"] for p in reversed(ps) if p["dba"]), None),
            "other_names": " | ".join(sorted({p["company_name"] for p in ps if p["company_name"]}
                                             - {latest["company_name"]})) or None,
            "category": latest["category"],
            "categories": " | ".join(sorted({p["category"] for p in ps if p["category"]})),
            "kind": ("day care" if any(p["source"] == "op_permit_daycare" for p in ps) else
                     "special event" if all(p["source"] == "license_special_event_food" for p in ps) else "food"),
            "sources": " | ".join(sorted({p["source"] for p in ps})),
            "business_status": latest["business_status"],
            "opened_date": iso(min((p["opened_date"] for p in ps if p["opened_date"]), default=None)),
            "closed_date": iso(latest["closed_date"]),
            "address": latest["address"], "address_withheld": latest["address_withheld"],
            "mobile_vendor": latest["mobile"],
            "is_test_establishment": any(p["test"] for p in ps),
            "street": latest["street"], "parcel": latest["parcel"],
            "_address_full": latest["address_full"], "_akey": latest["akey"], "_parcel_full": latest["parcel_full"],
            "_latest_case_id": latest["case_id"],
            "n_permits": len(ps), "n_op_permits": len(op),
            "first_permit": ps[0]["case_number"], "latest_permit": latest["case_number"],
            "latest_permit_status": latest["permit_status"], "latest_permit_expires": iso(latest["expire_date"]),
            "latest_permit_url": latest["portal_url"],
        }
    return permits, by_case, businesses


def propagate_withholding(ins, permits, by_case, businesses, exempt):
    """Second pass of the address rule: a street address (privacy_key) withheld on any inspection or permit
    is withheld on every inspection, permit and establishment row, so it cannot be read off another record.
    Updates ins / permits / businesses in place; returns (the withheld keys, first-pass counts)."""
    first = {"inspection_rows": int(ins.address_withheld.sum()), "permits": sum(p["address_withheld"] for p in permits)}
    keys = ({k for k, w in zip(ins._pkey, ins.address_withheld) if w and k}
            | {p["pkey"] for p in permits if p["address_withheld"] and p["pkey"]}) - exempt
    for p in permits:
        if not p["address_withheld"] and p["pkey"] in keys:
            p.update(address_withheld=True, address=p["address_reduced"], parcel=None)
    for b in businesses.values():
        lp = by_case[b["_latest_case_id"]]
        b.update(address=lp["address"], address_withheld=lp["address_withheld"], parcel=lp["parcel"])
    more = ~ins.address_withheld & ins._pkey.isin(keys)
    ins.loc[more, "address"] = ins.loc[more, "_address_reduced"]
    ins.loc[more, "parcel"] = None
    ins.loc[more, "address_withheld"] = True
    est_addr = {bid: (b["address"], bool(b["address_withheld"])) for bid, b in businesses.items()}
    has = ins.establishment_id.notna()
    ins.loc[has, "establishment_address"] = [est_addr[e][0] for e in ins.loc[has, "establishment_id"]]
    ins.loc[has, "establishment_address_withheld"] = [est_addr[e][1] for e in ins.loc[has, "establishment_id"]]
    return keys, first


def group_businesses(businesses):
    """establishment_group_id for every BusinessId. EnerGov often keeps one physical establishment under
    several BusinessIds (a legacy licence business and a newer operational-permit business). BusinessIds
    whose latest names normalise to the same words and whose latest permits share the street number,
    street and unit, or the parcel and unit, are put in one group; the group id is the smallest BusinessId
    in it. Name variants that normalise differently ('Braums' vs 'Braum's Ice Cream') stay apart."""
    parent = {b: b for b in businesses}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    first = {}
    for bid in sorted(businesses):
        b = businesses[bid]
        name = norm_name(b["name"])
        if not name:
            continue
        keys = []
        if b["_akey"]:
            keys.append(("address", name, b["_akey"]))
        if b["_parcel_full"]:
            keys.append(("parcel", name, b["_parcel_full"], b["_akey"][2] if b["_akey"] else ""))
        for k in keys:
            if k in first:
                ra, rb = find(first[k]), find(bid)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
            else:
                first[k] = bid
    members = defaultdict(list)
    for bid in businesses:
        members[find(bid)].append(bid)
    return {bid: min(ms) for ms in members.values() for bid in ms}


def as_of_date(fmeta):
    """The date the data describe: the Chicago calendar date of the latest download of any response the
    fetch used (not the day build.py runs, so a rebuild from the same raw files gives the same numbers)."""
    ts = ((fmeta.get("http") or {}).get("responses_downloaded_between") or [None, None])[1] or fmeta.get("started_at")
    d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(CHI).date()
    return d.isoformat(), f"latest response download {ts} (UTC), as a Chicago date"


# ----------------------------------------------------------------------------- inspections
def build_inspections(listing, details, by_case, businesses, permits, fetch_errors, exempt=frozenset()):
    det = {d["InspectionId"].lower(): d for d in details}
    err = {e["CaseId"].lower(): e["error"] for e in fetch_errors}
    # address index for the fallback join: (number, street words, unit) + kind -> permits
    addr_idx = defaultdict(list)
    for p in permits:
        if p["business_id"] and p["akey"]:
            addr_idx[(p["akey"], p["kind"])].append(p)
    rows, link_checks = [], Counter()
    by_number = {}
    for p in permits:
        by_number.setdefault(p["case_number"], p)
    for type_name, d in listing.items():
        for r in d["rows"]:
            cid = r["CaseId"].lower()
            x = det.get(cid)
            status = r.get("CaseStatus")
            row = {
                "case_id": cid, "case_number": r["CaseNumber"], "inspection_type": type_name,
                "status": status,
                "scheduled_date": iso(naive_date(r.get("ScheduleDate"))),
                "request_date": iso(naive_date(r.get("RequestDate"))),
                "address": (r.get("AddressDisplay") or "").strip() or None,
                "address_type": (r.get("Address") or {}).get("AddressTypeName"),
                "parcel": r.get("MainParcel") or None,
            }
            if x:
                row.update({
                    "actual_date": iso(utc_to_local_date(x.get("ActualDate"))),
                    "detail_scheduled_date": iso(utc_to_local_date(x.get("ScheduledDate"))),
                    "entered_date": iso(utc_to_local_date(x.get("EnteredDate"))),
                    "inspector": (x.get("AssignedInspectorName") or "").strip() or None,
                    "is_reinspection": bool(x.get("IsReinspection")),
                    "comment": (x.get("Comment") or "").strip() or None,
                    "link_type": x.get("LinkTypeName"), "link_number": x.get("LinkNumber"),
                    "link_id": (x.get("LinkId") or "").lower() or None,
                    "inspection_module": {1: "Inspection", 2: "HealthInspection"}.get(x.get("InspectionTypeModuleId"),
                                                                                      x.get("InspectionTypeModuleId")),
                    "detail_status": "fetched",
                })
            else:
                row.update({"actual_date": None, "detail_scheduled_date": None, "entered_date": None,
                            "inspector": None, "is_reinspection": None, "comment": None, "link_type": None,
                            "link_number": None, "link_id": None, "inspection_module": None,
                            "detail_status": ("not fetched (cancelled)" if status in CANCELLED
                                              else f"unavailable: {err.get(cid, 'missing')}")})
            row["inspection_date"] = row["actual_date"] or row["scheduled_date"]
            row["date_source"] = "actual" if row["actual_date"] else ("scheduled" if row["scheduled_date"] else None)
            kind, score = classify_comment(row["comment"])
            row["comment_kind"], row["comment_score"] = kind, score
            row["closure_keyword"] = closure_keyword(row["comment"])

            # ---- establishment join
            bid, method, note = None, None, None
            lid = row["link_id"]
            if lid and row["link_type"] == "Operational Permit":
                p = by_case.get(lid)
                if p:
                    bid, method = p["business_id"], "op_permit_case_id"
                    link_checks["op_link_number_equals_case_number" if p["case_number"] == row["link_number"]
                                else "op_link_number_differs"] += 1
                elif row["link_number"] in by_number:  # same permit number, another case record
                    bid, method = by_number[row["link_number"]]["business_id"], "op_permit_number"
            elif lid:
                if lid in businesses:
                    srcs = businesses[lid]["sources"]
                    bid = lid
                    method = "business_id_op_permit" if "op_permit" in srcs else "business_id_license"
                elif lid in by_case:  # a link straight to a permit or licence case
                    bid, method = by_case[lid]["business_id"], "permit_case_id"
            if bid is None:
                # Fallback (flagged): exactly one permit/licence of the same kind (food / day care) in force
                # on the inspection date at the same street number, street and unit.
                d0, akey = row["inspection_date"], address_key(r.get("Address"))
                cands = addr_idx.get((akey, inspection_kind(type_name)), []) if akey else []
                live = set()
                if d0 and cands:
                    dd = date.fromisoformat(d0)
                    live = {p["business_id"] for p in cands
                            if (p["issue_date"] or p["apply_date"] or date.min) <= dd <= (p["expire_date"] or date.max)}
                why = ("link id not in the permit/licence tables" if lid else
                       "detail not fetched (cancelled)" if not x else "no link on the record")
                if len(live) == 1:
                    bid, method = live.pop(), "address_fallback"
                    note = f"{why}; one permit of this kind in force at the address on the date"
                else:
                    method = "unmatched"
                    note = f"{why}; {len(live)} permits of this kind in force at the address on the date"
            b = businesses.get(bid) if bid else None
            row.update({
                "establishment_id": bid,
                "establishment_name": b["name"] if b else None,
                "establishment_category": b["category"] if b else None,
                "establishment_address": b["address"] if b else None,
                "establishment_address_withheld": bool(b["address_withheld"]) if b else None,
                "join_method": method, "join_note": note,
                "source_url": PORTAL_INSPECTION_URL.format(id=r["CaseId"]),
                "api_url": GETBYID_URL.format(id=r["CaseId"]),
            })
            # ---- privacy: the inspection's own address (see withhold_address)
            addr = r.get("Address") or {}
            mobile = type_name == "EH-Food Truck" or bool(b and b["mobile_vendor"])
            withheld = withhold_address(addr, mobile, exempt)
            akey = address_key(addr)
            row.update({
                "address": city_state_zip(addr) if withheld else row["address"],
                "address_withheld": withheld,
                "parcel": None if withheld else row["parcel"],
                # internal (not written): full addresses for matching report lines
                "_address_full": row["address"], "_unit": akey[2] if akey else None,
                "_address_reduced": city_state_zip(addr) if addr else None, "_pkey": privacy_key(addr),
                "_est_address_full": b["_address_full"] if b else None,
                "_est_unit": b["_akey"][2] if b and b["_akey"] else None,
            })
            # ---- test / training records
            if b and b["is_test_establishment"]:
                basis = TEST_BASIS_ESTABLISHMENT
            elif isinstance(row["comment"], str) and TEST_COMMENT.search(row["comment"]):
                basis = TEST_BASIS_COMMENT
            else:
                basis = None
            row["is_test_record"], row["test_record_basis"] = basis is not None, basis
            rows.append(row)
    return pd.DataFrame(rows), link_checks


# ----------------------------------------------------------------------------- PDFs
def parse_reports(manifest):
    """Parse every score-report PDF named in the manifest. Returns (report metadata list, row DataFrame)."""
    reports, rows = [], []
    entries = []
    for c in manifest.get("city", []):
        if c.get("file"):
            entries.append({"report_id": c["name"], "file": c["file"], "source_url": c.get("cited_as") or c["url"],
                            "fetched_url": c["url"], "how_found": c["how_found"], "sha256": c.get("sha256")})
    for c in manifest.get("wayback", {}).get("checked", []):
        if c.get("file"):
            entries.append({"report_id": f"archivecenter_item{c['item']}", "file": c["file"], "source_url": c["url"],
                            "fetched_url": c["url"], "how_found": "; ".join(c["sources"]), "sha256": c.get("sha256"),
                            "archive_item": c["item"], "capture_timestamp": c.get("capture_timestamp")})
    seen_sha = {}
    for e in entries:
        meta, rs = parse_pdf(RAW / e["file"])
        ov = PERIOD_OVERRIDES.get((RAW / e["file"]).name)
        if ov and not meta["period_start"]:
            meta["period_start"], meta["period_end"] = ov[0], ov[1]
            meta["notes"].append("period from " + ov[2])
        dup_of = seen_sha.get(e["sha256"])
        seen_sha.setdefault(e["sha256"], e["report_id"])
        rep = {**e, **{k: meta[k] for k in ("pages", "period_start", "period_end", "total_reported", "status", "rows",
                                            "numeric_scores", "notes")}, "duplicate_of": dup_of}
        sc = [r["score"] for r in rs if r["score"] is not None]
        rep["score_min"], rep["score_max"] = (min(sc), max(sc)) if sc else (None, None)
        reports.append(rep)
        if dup_of:
            continue  # identical bytes to a report already parsed (the Wayback copy of the Q2 2024 file)
        for r in rs:
            rows.append({"report_id": e["report_id"], "report_period_start": meta["period_start"],
                         "report_period_end": meta["period_end"], "report_source_url": e["source_url"], **r})
    return reports, pd.DataFrame(rows)


SAME_DAY_NUMERIC = "; same-day pair: numeric line given to the Followup Reinspection record (inference)"
SAME_DAY_RESULT = "; same-day pair: follow-up / result line given to the {} record (inference)"
SAME_DAY_DUPLICATE = "; report prints one line per same-day record: lines assigned one each (inference)"
LOW_CONFIDENCE = ("unmatched: low-confidence (only a street-number match with a different name at an address "
                  "where several establishments were inspected that day)")


def match_pdf_rows(pdf, ins):
    """One-to-one match of one report's lines to EnerGov inspections.

    Candidates are non-cancelled inspections on the report line's date (or up to 3 days either side).
    Tiers, best first: 1 same date + street number + name; 2 same date + street number;
    3 same date + name (report shows another address); 4 date within 3 days + street number + name.
    The street number may match either the inspection's address or its establishment's permit
    address (1,600-odd inspection rows carry the owner's mailing address; the full addresses are used
    here even where the published tables show only city and ZIP). Name similarity is the Jaccard overlap
    of normalised name words (0.34 needed for tiers 1/4, 0.5 for tier 3). A tier-2 candidate whose name
    shares no word with the line is dropped when several establishments were inspected that day at that
    street number and the unit / suite does not confirm it (a multi-tenant address); a line left with no
    candidate for that reason is marked low-confidence. Pairs are taken greedily, best first. When a
    line's best candidates tie:
      * if they are all records of one establishment on one day (usually a 'Followup Reinspection' and a
        'Pass'), a numeric line goes to the Followup Reinspection record and a 'Follow up - Pass' line to
        the Pass record (inference: in 2022-2026, where both records of such a pair have comments, the
        comment score is on the Followup Reinspection record); if the report prints as many identical
        lines as there are records, the lines are assigned one each in case-number order;
      * otherwise the line is left unmatched as ambiguous (e.g. several departments of one store).
    Returns (matches {line: (case_id, method, name similarity)}, unmatched labels {line: label},
    unresolved same-day ties {line: [case ids]}).
    """
    done = ins[ins.inspection_date.notna() & ~ins.status.isin(CANCELLED)].copy()
    done["m_nums"] = [{n for n in (street_number(a), street_number(b)) if n}
                      for a, b in zip(done._address_full.fillna(""), done._est_address_full.fillna(""))]
    done["m_sw"] = [street_words(a) for a in done._address_full.fillna("")]
    done["m_ename"] = [norm_name(n) for n in done.establishment_name.fillna("")]
    done["m_units"] = [{u for u in (unit_token(a), unit_token(b)) if u} for a, b in zip(done._unit, done._est_unit)]
    done["m_est"] = [e if isinstance(e, str) else c for e, c in zip(done.establishment_id, done.case_id)]
    meta = {r.case_id: (r.m_est if isinstance(r.establishment_id, str) else None, r.inspection_date, r.status,
                        r.case_number) for r in done.itertuples()}
    by_date = defaultdict(list)
    for r in done.itertuples():
        by_date[r.inspection_date].append(r)
    cands, lowconf = defaultdict(list), set()
    for i, p in pdf.iterrows():
        pnum, psw, pname, punit = street_number(p.street), street_words(p.street), norm_name(p.facility_name), \
            pdf_unit(p.street)
        pd0 = date.fromisoformat(p.date)
        for delta in range(-3, 4):
            dd = (pd0 + pd.Timedelta(days=delta)).isoformat()
            day = by_date.get(dd, [])
            n_est_at_number = len({c.m_est for c in day if pnum and pnum in c.m_nums})
            for c in day:
                num_ok = bool(pnum) and pnum in c.m_nums
                ns = round(jaccard(pname, c.m_ename), 3)
                ss = round(jaccard(psw, c.m_sw), 3) if psw and c.m_sw else 0.0
                if delta == 0 and num_ok and ns >= 0.34:
                    tier, label = 1, "same date + street number + name"
                elif delta == 0 and num_ok:
                    unit_ok = bool(punit and punit in c.m_units)
                    if ns == 0 and n_est_at_number > 1 and not unit_ok:
                        lowconf.add(i)
                        continue
                    tier = 2
                    label = ("same date + street number" + (" + unit" if unit_ok else "")
                             + ("; names share no word" if ns == 0 else ""))
                elif delta == 0 and ns >= 0.5:
                    tier, label = 3, "same date + name (address differs)"
                elif num_ok and ns >= 0.34:
                    tier, label = 4, f"date within 3 days ({delta:+d}) + street number + name"
                else:
                    continue
                cands[i].append(((tier, -ns, -ss, abs(delta)), c.case_id, label, ns))
    # identical lines (a 2019 report sometimes prints a same-day pair as two identical lines)
    dup_key = {i: (p.date, p.facility_name, p.street, p.rating_text) for i, p in pdf.iterrows()}
    dup_count = Counter(dup_key.values())
    used_ins, out, unmatched, ties, dup_noted = set(), {}, {}, {}, set()
    pending = set(cands)
    while pending:
        best = None
        for i in sorted(pending):
            opts = sorted(o for o in cands[i] if o[1] not in used_ins)
            if not opts:
                continue
            if best is None or opts[0][0] < best[1][0]:
                best = (i, opts[0], opts)
        if best is None:
            break
        i, top, opts = best
        pending.discard(i)
        tied = [top] + [o for o in opts[1:] if o[0] == top[0]]
        if len(tied) == 1:
            used_ins.add(top[1])
            out[i] = (top[1], top[2], top[3])
            continue
        info = [meta[o[1]] for o in tied]
        same_day = info[0][0] is not None and len({m[:2] for m in info}) == 1
        if not same_day:
            unmatched[i] = f"ambiguous ({len(tied)} equally good inspections)"
            continue
        p = pdf.loc[i]
        if pd.notna(p.score):
            pick, note = [o for o, m in zip(tied, info) if m[2] in FOLLOWUP_STATUSES], SAME_DAY_NUMERIC
        else:
            want = ({"Pass"} if p.result_text in ("follow-up: pass", "pass (unscored)") else
                    {"Fail", "Failed"} if "fail" in (p.result_text or "") else set())
            pick = [o for o, m in zip(tied, info) if m[2] in want]
            note = SAME_DAY_RESULT.format(" / ".join(sorted(want)))
        if len(pick) != 1 and dup_count[dup_key[i]] >= len(tied):
            pick, note = [min(tied, key=lambda o: meta[o[1]][3])], SAME_DAY_DUPLICATE
            dup_noted.add(dup_key[i])
        if len(pick) == 1:
            used_ins.add(pick[0][1])
            out[i] = (pick[0][1], pick[0][2] + note, pick[0][3])
        else:
            unmatched[i] = f"ambiguous: {len(tied)} same-day records of one establishment"
            ties[i] = [o[1] for o in tied]
    for i in out:  # the second of two identical lines, matched above without a tie
        if dup_key[i] in dup_noted and SAME_DAY_DUPLICATE not in out[i][1]:
            out[i] = (out[i][0], out[i][1] + SAME_DAY_DUPLICATE, out[i][2])
    for i in lowconf:
        if i not in out and i not in unmatched:
            unmatched[i] = LOW_CONFIDENCE
    return out, unmatched, ties


def report_kind(rep, reports):
    """What a report covers: a quarter, a longer period, a cumulative year-to-date list, or (no period
    printed) a list of the latest score per establishment."""
    if not rep["period_start"]:
        return "latest score per establishment"
    if any(r is not rep and not r["duplicate_of"] and r["period_start"] == rep["period_start"]
           and r["period_end"] != rep["period_end"] for r in reports):
        return "year to date (cumulative)"
    days = (date.fromisoformat(rep["period_end"]) - date.fromisoformat(rep["period_start"])).days
    return "quarter" if days <= 100 else "half year" if days <= 200 else "longer period"


# ----------------------------------------------------------------------------- README numbers
NUM_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


def long_date(iso_date):
    d = date.fromisoformat(iso_date)
    return f"{d:%B} {d.day}, {d.year}"


def readme_phrases(v):
    """Every number-bearing phrase of the README, generated from validation.json. check_readme() fails the
    build's README check if one of them is not in the README text verbatim (whitespace ignored), so a rebuild
    that changes a number cannot leave the README stale."""
    h, hx = v["headline"]["all_rows"], v["headline"]["excluding_test_records"]
    j, d, s = v["joins"], v["dates"], v["scores_summary"]
    g, pm, pv = v["establishment_groups"], v["pdf_matching"], v["privacy"]["addresses_withheld"]
    fr, cs = v["followup_reinspection_next_inspection"], v["comment_scores"]
    st = cs["score_stats_by_status"]
    t, sd = v["test_records"], v["same_day_pair_evidence"]
    unm = j["unmatched_fetched_details"]["by_type_and_year"]
    jm, jc = j["join_method_all_rows"], j["join_method_cancelled_rows"]
    kinds = s["reports_by_kind"]
    comp_range = hx["completed_date_range"]
    tb = t["by_basis"]
    n_test_est, n_test_comment = tb.get(TEST_BASIS_ESTABLISHMENT, 0), tb.get(TEST_BASIS_COMMENT, 0)
    by_year = cs["by_year"]
    years = [y for y in sorted(by_year) if by_year[y]["with_comment_score"]]
    reports = [e for e in v["pdf_reports"] if not e["duplicate_of"]]
    rates = [e["match_rate"] for e in reports if e.get("period_start") and e["period_start"] >= "2019"]
    eh = v["status_reconciliation"]["EH-Food Establishment"]["statuses"]
    wc = Counter()
    for k, n in v["pdf_manifest_summary"]["wayback_classification"].items():
        wc["score" if k == "score report" else "none" if k == "no archived PDF" else
           "unclassified" if k.startswith("truncated capture: unclassified") else
           "truncated" if k.startswith("truncated capture") else "other"] += n
    fetched = v["details"]["saved"]
    ex = {e["name"]: e["inspections_per_business_id"] for e in g["examples"]}
    braums, eagle, chevron = (ex.get("Braum's Ice Cream # 278", [None, None]), ex.get("Allen Eagle Food Mart", [None, None]),
                              ex.get("Chevron Mart", [None]))
    pending_next_year = hx[f"pending_dated_after_{v['as_of']['date'][:4]}"]
    phrases = {
        "headline_rows": f"**{h['rows']:,} inspection records** in {len(v['listing'])} food-related inspection types",
        "headline_test": f"**{t['count']} of them are test or training entries**",
        "headline_test_split": f"({n_test_est} on the city's own \"Training Food Establishment\", {n_test_comment} "
                               f"whose comment reads \"Testing\"",
        "headline_completed": f"Of the other {hx['rows']:,} records, {hx['completed']:,} are completed inspections "
                              f"dated {long_date(comp_range[0])} through {long_date(comp_range[1])}; the rest are "
                              f"cancelled ({hx['cancelled']:,}), pending or requested ({hx['pending_or_requested']:,}).",
        "headline_businesses": f"**{g['business_ids']:,} EnerGov businesses (BusinessIds)**, taken from "
                               f"{sum(x['rows'] for x in j['permit_tables'].values()):,} public",
        "headline_groups": f"{g['business_ids_with_inspections']:,} BusinessIds have inspections",
        "headline_groups2": f"they are **{g['groups_with_inspections']:,} establishments**, "
                            f"{g['groups_with_inspections_under_several_business_ids']} of which have inspections "
                            f"split across several BusinessIds",
        "headline_comment_scores": f"({s['inspections_with_comment_score']:,} inspections; see",
        "headline_report_kinds": f"{sum(kinds.values())} score reports the city published between 2015 and 2025: "
                                 f"{kinds.get('quarter', 0)} quarterly reports, {kinds.get('half year', 0)} half-year "
                                 f"reports, {kinds.get('year to date (cumulative)', 0)} cumulative 2019 year-to-date "
                                 f"reports and {kinds.get('latest score per establishment', 0)} lists of the latest "
                                 f"score per establishment",
        "headline_lines": f"Together they have {s['report_lines']:,} lines and {s['report_lines_numeric']:,} numeric "
                          f"scores",
        "headline_distinct": f"only {s['report_lines_numeric_distinct (name, street, date, score)']:,} distinct "
                             f"numeric lines",
        "headline_pdf_scored": f"**{s['inspections_with_pdf_score']:,} inspections are matched to a report score.**",
        "headline_any": f"**{s['inspections_with_any_score']:,} inspections have a report score, a comment score or "
                        f"both**",
        "headline_agree": f"in all {v['pdf_vs_comment_all_reports']['equal']} inspections that have both"
                          if v["pdf_vs_comment_all_reports"]["equal"] == v["pdf_vs_comment_all_reports"]["both_present"]
                          else "SCORES DISAGREE",
        "coverage_by_year": ", ".join(
            f"{by_year[y]['with_comment_score']} in {y}" + (f" ({round(100 * by_year[y]['share_with_comment_score'])}%)"
                                                          if by_year[y]["share_with_comment_score"] >= 0.1 else "")
            for y in years[:-1]) + f", {by_year[years[-1]]['with_comment_score']} in {years[-1]} "
            f"({round(100 * by_year[years[-1]]['share_with_comment_score'])}%)",
        "count_inspections": f"{s['inspections_with_pdf_score']:,} inspections have a `pdf_score`, "
                             f"{s['inspections_with_comment_score']:,} a `comment_score`, "
                             f"{s['inspections_with_any_score']:,} one or both",
        "unmatched_lines": f"{pm['match_methods_all_reports'].get('unmatched', 0)} lines that match no inspection "
                           f"({v['pdf_unmatched_in_latest_score_lists']} of them in the two latest-score lists",
        "fr_scores": f"the {st['Followup Reinspection']['n']} of them with a comment score average "
                     f"{st['Followup Reinspection']['mean']:.1f} (median {st['Followup Reinspection']['median']:.0f}, "
                     f"range {st['Followup Reinspection']['min']}–{st['Followup Reinspection']['max']}) against "
                     f"{st['Pass']['mean']:.1f} (median {st['Pass']['median']:.0f}, range {st['Pass']['min']}–"
                     f"{st['Pass']['max']}) for the {st['Pass']['n']} \"Pass\" inspections with one",
        "at_or_below_69": f"All {NUM_WORDS.get(cs['at_or_below_69'], cs['at_or_below_69'])} comment scores of 69 or "
                          f"below",
        "fr_next": f"{fr['next_inspection_within_30_days']} of the {fr['records_with_establishment_and_date']:,} such "
                   f"records",
        "fr_next_pass": f"{fr['status_of_next_inspection_within_30_days'].get('Pass')} of those with \"Pass\"",
        "test_est": f"**{n_test_est} inspections of the \"Training Food Establishment\"**",
        "test_comment": f"**{n_test_comment} inspections of a real business**",
        "test_completed": f"{h['completed'] - hx['completed']} of the {t['count']} are completed",
        "test_completed2": f"with them, {h['completed']:,} records are completed inspections",
        "status_counts": "Excluding them, the statuses are: " + ", ".join(
            f"{k} {n:,}" for k, n in v["status_counts_excluding_test_records"].items()) + ".",
        "files_ins": f"EnerGov inspection ({h['rows']:,}, including the {t['count']} test records)",
        "files_est": f"business (EnerGov BusinessId, {g['business_ids']:,})",
        "files_pdf": f"line of a parsed score report ({s['report_lines']:,})",
        "files_details": f"non-cancelled inspection ({fetched:,})",
        "files_permits": f"permit / licence ({sum(x['rows'] for x in j['permit_tables'].values()):,})",
        "is_reinspection": f"**false on all {fetched:,} fetched records**" if v["details"]["is_reinspection_true"] == 0
                           else "IS_REINSPECTION TRUE SOMEWHERE",
        "closure": f"({cs['closure_keyword_comments']} comments)",
        "mailing": f"{v['privacy']['address_types_on_inspection_rows'].get('Mailing Address'):,} rows carry a "
                   f"\"Mailing Address\"",
        "join_1": f"BusinessId in the food / day-care operational permits; {jm['business_id_op_permit']:,})",
        "join_2": f"LinkNumber = its permit number in all {j['op_link_number_vs_permit_number'].get('op_link_number_equals_case_number')} "
                  f"cases; {jm['op_permit_case_id']})",
        "join_3": f"(the business is only in the legacy licence tables; {jm['business_id_license']})",
        "join_4": f"whose permit number is; {jm['op_permit_number']})",
        "join_5": f"number, street and unit on the date; {jm['address_fallback']}, of which "
                  f"{jc.get('address_fallback', 0)} are cancelled",
        "join_6": f"`unmatched` ({jm['unmatched']}, of which {jc.get('unmatched', 0)} are cancelled inspections",
        "join_7": f"Of the {fetched:,} inspections with a detail, {j['joined_fetched_details_by_link']:,} "
                  f"({100 * j['join_rate_fetched_details_by_link_only']:.1f}%) are joined through their EnerGov link, "
                  f"{j['join_method_fetched_details'].get('address_fallback', 0)} by address and "
                  f"{j['unmatched_fetched_details']['count']} not at all: "
                  f"{sum(n for k, n in unm.items() if 'Day Care' in k)} day-care inspections "
                  f"({sum(n for k, n in unm.items() if 'Day Care' in k and k[-4:] in ('2017', '2018'))} of them from "
                  f"2017–2018) and {sum(n for k, n in unm.items() if k.startswith('EH-Food Establishment'))} "
                  f"food-establishment inspections",
        "join_8": f"and {sum(n for k, n in unm.items() if k.endswith('2026') and 'Day Care' not in k)} inspections "
                  f"from 2026 linked to operational permits",
        "groups_examples": f"Braum's Ice Cream #278 (two BusinessIds with {braums[0]} and {braums[1]} inspections), "
                           f"Allen Eagle Food Mart ({eagle[0]} and {eagle[1]}), Chevron Mart at 105 S Custer Rd "
                           f"({', '.join(map(str, chevron[:-1]))} and {chevron[-1]})",
        "low_conf": f"({pm['lines_low_confidence_not_matched']} lines, e.g.",
        "tier2_0": f"{pm['tier2_lines_name_similarity_0']} tier-2 lines have names with no word in common",
        "tier2_unit": f"{pm['tier2_lines_name_similarity_0_unit_matches']} of the "
                      f"{pm['tier2_lines_name_similarity_0']} also match the unit",
        "same_day_evidence": f"EnerGov has {sd['followup_plus_pass_pairs']} same-day Followup Reinspection + Pass "
                             f"pairs; {sd['pairs_with_a_comment_score']} of them have a comment score, and in all "
                             f"{sd['score_on_followup_record_only']} it is on the Followup Reinspection record"
                             if sd["score_on_pass_record"] == 0 and sd["score_on_followup_record_only"] ==
                             sd["pairs_with_a_comment_score"] else "SAME-DAY EVIDENCE CHANGED",
        "same_day_lines": f"{pm['lines_matched_as_same_day_pair']} lines are matched this way",
        "same_day_unresolved": f"{pm['lines_ambiguous_same_day_unresolved']} lines stay `ambiguous: n same-day records",
        "ambiguous_other": f"{pm['lines_ambiguous_other']} other lines are `ambiguous (n equally good",
        "no_scheduled": f"the {d['no_scheduled_date']} records with no scheduled date "
                        f"({d['no_scheduled_date_by_type'].get('EH-Food Establishment')} of them EH-Food Establishment)",
        "getbyid_calls": f"BL - Cancelled ({v['details']['requested']:,} calls)",
        "wayback": f"Of {v['pdf_manifest_summary']['wayback_items_checked']} items checked, {wc['score']} are score "
                   f"reports; {wc['other']} are other city documents; {wc['none']} (the 2010–2014 monthly reports) are "
                   f"not archived; {wc['truncated'] + wc['unclassified']} are captures the archive truncated at 1 MiB "
                   f"({wc['truncated']} of them readable",
        "wayback_unclassified": f"{wc['unclassified']} unclassified, all from files over",
        "check_types": f"equal for all {len(v['listing']) + len(v['listing_types_with_zero_records'])} types "
                       f"({h['rows']:,} rows)" if v["listing_all_types_match"] else "LISTING MISMATCH",
        "check_eh_status": "EH-Food Establishment: " + ", ".join(
            f"{k.replace(' - Business License', '').replace(' - Business', '')} {x['rows']:,}"
            for k, x in eh.items() if k != "Pass - NONE") if v["status_reconciliation_all_match"] else "STATUS MISMATCH",
        "check_permits": ", ".join(f"{j['permit_tables'][k]['rows']:,}" for k in
                                   ("op_permit_food", "op_permit_daycare", "license_food", "license_special_event_food"))
                         + ": all equal",
        "check_details": f"{v['details']['saved']:,} of {v['details']['requested']:,}",
        "check_sched": f"equal for {d['listing_schedule_date_equals_detail_scheduled_date_local']:,} of {d['of']:,}; "
                       f"the {d['of'] - d['listing_schedule_date_equals_detail_scheduled_date_local']} others",
        "check_join": f"{100 * j['join_rate_fetched_details_by_link_only']:.1f}% of fetched inspections through their "
                      f"EnerGov link; OP LinkNumber = permit number in "
                      f"{j['op_link_number_vs_permit_number'].get('op_link_number_equals_case_number')}/"
                      f"{jm['op_permit_case_id']}",
        "check_rates": f"{100 * min(rates):.1f}–{100 * max(rates):.1f}% per report for the {len(rates)} reports from "
                       f"2019 on",
        "check_agree": f"{v['pdf_vs_comment_all_reports']['equal']} of {v['pdf_vs_comment_all_reports']['both_present']} "
                       f"equal",
        "check_test": f"{t['count']} flagged",
        "as_of": v["as_of"]["date"],
        "privacy_contacts": f"({v['fetch']['http']['contacts_scrubbed']} replacements",
        "privacy_names": f"{v['privacy']['scrub_at_fetch']['names']} names and "
                         f"{v['privacy']['scrub_at_fetch']['certificate_numbers']} certificate numbers in "
                         f"{v['privacy']['scrub_at_fetch']['comments_changed']} comments",
        "privacy_addresses": f"{pv['inspection_rows']} inspection rows ({pv['inspection_rows_mailing_address']} of them "
                             f"mailing addresses; {pv['inspection_distinct_addresses_withheld']} distinct addresses), "
                             f"{pv['establishments']} establishments and {pv['report_lines']} report lines",
        "privacy_addresses_zero": "none of the withheld street addresses appears anywhere in the three CSVs"
                                  if pv["withheld_street_addresses_still_in_csvs"] == 0 else "WITHHELD ADDRESS LEFT",
        "caveat_scores": f"Scores exist only for {s['inspections_with_any_score']:,} inspections",
        "caveat_before_2019": f"before 2019 only {s['any_score_before_2019']}",
        "caveat_test": f"({t['count']} rows, {NUM_WORDS.get(n_test_comment, n_test_comment)} of them on a real Tom "
                       f"Thumb bakery)",
        "caveat_fail": f"\"Fail\" is rare ({sum(hx['fail_by_type'].values())}; "
                       f"{hx['fail_by_type'].get('EH-Food Truck', 0)} of them food-truck inspections)",
        "caveat_closure": f"`closure_keyword` flags {cs['closure_keyword_comments']} comments",
        "caveat_pending": f"The {v['status_counts_excluding_test_records'].get('Pending')} Pending records include "
                          f"{pending_next_year} inspections scheduled into {int(v['as_of']['date'][:4]) + 1}",
        "caveat_dates": f"{d['actual_differs_from_scheduled_completed']} completed inspections "
                        f"({d['actual_differs_from_scheduled']} rows in all) have an actual date different",
    }
    return phrases


def check_readme(phrases):
    """Print every README phrase (from validation.json) that the README does not contain verbatim."""
    path = DATA.parent / "README.md"
    if not path.exists():
        return
    text = re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
    missing = {k: p for k, p in phrases.items() if re.sub(r"\s+", " ", p) not in text}
    print("README check:", f"all {len(phrases)} phrases present" if not missing else
          f"{len(missing)} of {len(phrases)} phrases NOT in README.md: {json.dumps(missing, indent=1)}")


# ----------------------------------------------------------------------------- main
def main():
    listing = read_gz_json(RAW / "listing.json.gz")
    details = read_gz_json(RAW / "details.json.gz")
    pl = read_gz_json(RAW / "permits_licenses.json.gz")
    recon = json.loads((RAW / "status_reconciliation.json").read_text())
    fmeta = json.loads((RAW / "fetch_meta.json").read_text())
    manifest = json.loads((RAW / "pdf_manifest.json").read_text())
    as_of, as_of_basis = as_of_date(fmeta)

    exempt = exempt_site_keys(pl)
    permits, by_case, businesses = build_establishments(pl, exempt)
    group_of = group_businesses(businesses)
    ins, link_checks = build_inspections(listing, details, by_case, businesses, permits,
                                         fmeta.get("details", {}).get("errors", []), exempt)
    withheld_keys, first_pass = propagate_withholding(ins, permits, by_case, businesses, exempt)
    # comments are scrubbed in fetch.py; scrub again so a stale raw file can never leak
    scrubber = CommentScrubber(list(ins.comment), set(ins.inspector.dropna()),
                               [l if isinstance(l, str) else c for l, c in zip(ins.link_id, ins.case_id)])
    ins["comment"] = [scrubber(c) if isinstance(c, str) else c for c in ins.comment]
    ins["inspection_year"] = ins.inspection_date.str[:4]
    ins["establishment_group_id"] = ins.establishment_id.map(group_of)
    real = ins[~ins.is_test_record]  # everything below except the portal reconciliation leaves test records out

    # ---- PDFs
    reports, pdf = parse_reports(manifest)
    matches, unmatched, ties = {}, {}, {}
    for _, sub in pdf.groupby("report_id"):  # one-to-one within each report; test records are not candidates
        m_, u_, t_ = match_pdf_rows(sub, real)
        matches.update(m_)
        unmatched.update(u_)
        ties.update(t_)
    ins_idx = ins.set_index("case_id")
    pdf["matched_case_id"] = [matches.get(i, (None,))[0] for i in pdf.index]
    pdf["match_method"] = [matches[i][1] if i in matches else unmatched.get(i, "unmatched") for i in pdf.index]
    pdf["match_name_similarity"] = [matches.get(i, (None, None, None))[2] for i in pdf.index]
    for col in ("case_number", "inspection_type", "status", "inspection_date", "comment_score", "comment_kind",
                "establishment_name"):
        pdf[f"energov_{col}"] = [ins_idx.at[c, col] if isinstance(c, str) else None for c in pdf.matched_case_id]
    pdf["score_agrees_with_comment"] = [
        (None if pd.isna(s) or c is None or pd.isna(c) else bool(int(s) == int(c)))
        for s, c in zip(pdf.score, pdf.energov_comment_score)]
    pdf["demerits_inferred"] = [100 - int(s) if pd.notna(s) else None for s in pdf.score]
    # an inspection can appear in several reports (the 2019 reports are cumulative year-to-date lists)
    per_case = defaultdict(list)
    for i, (cid, label, ns) in matches.items():
        per_case[cid].append((pdf.at[i, "report_id"], pdf.at[i, "score"], pdf.at[i, "rating_text"]))
    ins["pdf_score"] = [next((int(s) for _, s, _ in per_case.get(c, []) if pd.notna(s)), None) for c in ins.case_id]
    ins["pdf_rating_text"] = [next((t for _, _, t in per_case.get(c, [])), None) for c in ins.case_id]
    ins["pdf_report_ids"] = [" | ".join(sorted({r for r, _, _ in per_case[c]})) if c in per_case else None
                             for c in ins.case_id]
    pdf_conflicts = {c: v for c, v in per_case.items() if len({s for _, s, _ in v if pd.notna(s)}) > 1}
    ins["comment_demerits_inferred"] = [100 - int(s) if pd.notna(s) else None for s in ins.comment_score]
    real = ins[~ins.is_test_record]

    # ---- privacy: report lines printed with a withheld address (or another town's address) show only
    # the city and ZIP. Addresses are compared as (street number, street words).
    skey = privacy_key
    kept_keys = ({k for k, w in zip(ins._pkey, ins.address_withheld) if not w}
                 | {p["pkey"] for p in permits if not p["address_withheld"]}) - {None}
    pdf_rule = []
    for p in pdf.itertuples():
        k = skey(p.street)
        other_town = isinstance(p.city, str) and p.city.strip() and "LLEN" not in p.city.upper()
        pdf_rule.append("same address as a withheld EnerGov address" if k in withheld_keys else
                        "printed town is not Allen" if other_town and k not in kept_keys else None)
    pdf["_street_full"] = pdf.street
    pdf["address_withheld"] = [r is not None for r in pdf_rule]
    pdf["address"] = [" ".join(x for x in (c, z) if isinstance(x, str) and x) or None if w else a
                      for a, c, z, w in zip(pdf.address, pdf.city, pdf.zip, pdf.address_withheld)]
    pdf["street"] = [None if w else s for s, w in zip(pdf.street, pdf.address_withheld)]

    # ---- establishments roll-up (per BusinessId and per establishment group; test records left out)
    est = pd.DataFrame(businesses.values())
    est["establishment_group_id"] = est.establishment_id.map(group_of)
    est["group_n_business_ids"] = est.establishment_group_id.map(est.groupby("establishment_group_id").size())
    done = real[~real.status.isin(CANCELLED)]
    sc = done[done.comment_score.notna()].sort_values(["inspection_date", "case_number"], kind="stable")
    counts = ("n_inspections", "n_inspections_not_cancelled", "n_followup_reinspection", "n_fail", "n_comment_scores")
    for key, prefix in (("establishment_id", ""), ("establishment_group_id", "group_")):
        g = done.groupby(key)
        roll = {
            "n_inspections": real.groupby(key).size(),
            "n_inspections_not_cancelled": g.size(),
            "first_inspection": g.inspection_date.min(),
            "last_inspection": g.inspection_date.max(),
            "n_followup_reinspection": done[done.status.isin(FOLLOWUP_STATUSES)].groupby(key).size(),
            "n_fail": done[done.status.isin({"Fail", "Failed"})].groupby(key).size(),
            "n_comment_scores": sc.groupby(key).size(),
            "latest_comment_score": sc.groupby(key).comment_score.last(),
            "latest_comment_score_date": sc.groupby(key).inspection_date.last(),
        }
        for name, s in roll.items():
            col = est[key].map(s)
            est[prefix + name] = col.fillna(0).astype(int) if name in counts else col
    est["n_test_records"] = est.establishment_id.map(
        ins[ins.is_test_record].groupby("establishment_id").size()).fillna(0).astype(int)

    # ---- validation
    v = {"generated_at": datetime.now(CHI).isoformat(timespec="seconds"), "time_zone": TZ,
         "as_of": {"date": as_of, "basis": as_of_basis},
         "fetch": {k: fmeta.get(k) for k in ("started_at", "finished_at", "problems", "http")}}
    v["listing"] = {name: {"type_id": d["type_id"], "portal_total_found": d["total_found"],
                           "rows": d["rows_distinct"], "rows_equal_total_found": d["matches_total_found"]}
                    for name, d in listing.items() if d["total_found"]}
    v["listing_types_with_zero_records"] = sorted(n for n, d in listing.items() if not d["total_found"])
    v["listing_all_types_match"] = all(d["matches_total_found"] for d in listing.values())
    v["listing_total_rows"] = int(len(ins))
    status_names = {x["InspectionStatusId"]: x["Name"] for x in read_gz_json(RAW / "inspection_statuses.json.gz")}
    v["status_reconciliation"] = {
        name: {"all_match": d["all_match"],
               "statuses": {status_names.get(s["status_id"], f"{s['status']} ({s['status_id']})"):
                            {"rows": s["rows"], "portal_total": s["portal_total"]} for s in d["statuses"]}}
        for name, d in recon.items() if d["total_found"]}
    v["status_reconciliation_all_match"] = all(d["all_match"] for d in recon.values())
    v["status_counts"] = {k: int(n) for k, n in ins.status.value_counts().items()}
    v["status_counts_excluding_test_records"] = {k: int(n) for k, n in real.status.value_counts().items()}

    # ---- test / training records
    tr = ins[ins.is_test_record].sort_values(["inspection_date", "case_number"], na_position="last")
    v["test_records"] = {
        "rule": "inspections of the city's app-testing establishment (permit 'Training Food Establishment', "
                "description 'For App Testing and Training Purposes - Only'), and inspections whose whole comment "
                "says 'Testing' / 'Testing again' / 'Testing the app again' or asks 'What happens if I type ...'. "
                "They stay in inspections.csv (flagged, is_test_record) so the rows still reconcile with the "
                "portal, and are left out of every count, roll-up and match below this point.",
        "count": int(len(tr)),
        "by_basis": {k: int(n) for k, n in tr.test_record_basis.value_counts().items()},
        "by_status": {k: int(n) for k, n in tr.status.value_counts().items()},
        "records": [{k: (None if pd.isna(x) else x) for k, x in r.items()} for r in tr[
            ["case_number", "inspection_type", "status", "inspection_date", "establishment_name", "address",
             "test_record_basis", "comment"]].to_dict("records")],
    }

    # ---- headline counts (test records left out, except where named)
    def headline(x):
        comp = x[~x.status.isin(CANCELLED | {"Pending", "Requested"})]
        return {"rows": int(len(x)), "completed": int(len(comp)),
                "cancelled": int(x.status.isin(CANCELLED).sum()),
                "pending_or_requested": int(x.status.isin({"Pending", "Requested"}).sum()),
                "completed_date_range": [comp.inspection_date.min(), comp.inspection_date.max()],
                f"future_dated_rows (after {as_of})": int((x.inspection_date > as_of).sum())}
    v["headline"] = {"all_rows": headline(ins), "excluding_test_records": headline(real)}
    v["headline"]["excluding_test_records"].update({
        "fail_by_type": {k: int(n) for k, n in real[real.status == "Fail"].inspection_type.value_counts().items()},
        f"pending_dated_after_{as_of[:4]}": int(((real.status == "Pending")
                                                 & (real.inspection_date > f"{as_of[:4]}-12-31")).sum())})

    dm = fmeta.get("details", {})
    v["details"] = {"requested": dm.get("requested"), "saved": dm.get("saved"),
                    "errors": dm.get("errors"),
                    "detail_status": {k: int(n) for k, n in ins.detail_status.value_counts().items()},
                    "is_reinspection_true": int((ins.is_reinspection == True).sum())}  # noqa: E712
    both = ins[ins.detail_scheduled_date.notna() & ins.scheduled_date.notna()]
    differs = ins.actual_date.notna() & ins.scheduled_date.notna() & (ins.actual_date != ins.scheduled_date)
    completed_mask = ~ins.status.isin(CANCELLED | {"Pending", "Requested"})
    v["dates"] = {
        "listing_schedule_date_equals_detail_scheduled_date_local": int((both.scheduled_date ==
                                                                         both.detail_scheduled_date).sum()),
        "of": int(len(both)),
        "schedule_date_mismatch_by_status": {k: int(n) for k, n in both[both.scheduled_date !=
                                             both.detail_scheduled_date].status.value_counts().items()},
        "schedule_date_mismatch_note": "the listing was read before the details; Pending/Requested inspections "
                                       "are re-dated by the portal as days pass",
        "actual_date_missing_on_fetched_details": int((ins.detail_status.eq("fetched") & ins.actual_date.isna()).sum()),
        "actual_differs_from_scheduled": int(differs.sum()),
        "actual_differs_from_scheduled_completed": int((differs & completed_mask).sum()),
        "actual_differs_from_scheduled_completed_excluding_test_records": int(
            (differs & completed_mask & ~ins.is_test_record).sum()),
        "no_scheduled_date": int(ins.scheduled_date.isna().sum()),
        "no_scheduled_date_by_type": {k: int(n) for k, n in ins[ins.scheduled_date.isna()].inspection_type
                                      .value_counts().items()},
        "inspection_date_range": [ins.inspection_date.min(), ins.inspection_date.max()],
        f"future_dated_rows (after {as_of})": int((ins.inspection_date > as_of).sum()),
    }
    fetched = ins[ins.detail_status == "fetched"]
    unm = fetched[fetched.join_method == "unmatched"]
    v["joins"] = {
        "permit_tables": {k: {"rows": d["rows_distinct"], "portal_total_found": d["total_found"]}
                          for k, d in pl.items()},
        "businesses": len(businesses),
        "link_type_on_fetched_details": {str(k): int(n) for k, n in fetched.link_type.value_counts(dropna=False).items()},
        "join_method_all_rows": {k: int(n) for k, n in ins.join_method.value_counts().items()},
        "join_method_cancelled_rows": {k: int(n) for k, n in ins[ins.status.isin(CANCELLED)].join_method
                                       .value_counts().items()},
        "join_method_fetched_details": {k: int(n) for k, n in fetched.join_method.value_counts().items()},
        "join_notes_all_rows": {k: int(n) for k, n in ins.join_note.value_counts().items()},
        "join_rate_fetched_details": round(float(fetched.establishment_id.notna().mean()), 4),
        "join_rate_fetched_details_by_link_only": round(float(
            fetched.join_method.isin(["op_permit_case_id", "op_permit_number", "business_id_op_permit",
                                      "business_id_license", "permit_case_id"]).mean()), 4),
        "joined_fetched_details_by_link": int(fetched.join_method.isin(
            ["op_permit_case_id", "op_permit_number", "business_id_op_permit", "business_id_license",
             "permit_case_id"]).sum()),
        "join_rate_by_type": {t: round(float(x.establishment_id.notna().mean()), 4)
                              for t, x in fetched.groupby("inspection_type")},
        "unmatched_fetched_details": {
            "count": int(len(unm)),
            "by_type_and_year": {f"{t} | {y}": int(n) for (t, y), n in
                                 unm.groupby(["inspection_type", "inspection_year"]).size().items()},
            "by_link_type": {str(k): int(n) for k, n in unm.link_type.value_counts(dropna=False).items()}},
        "op_link_number_vs_permit_number": dict(link_checks),
    }
    # ---- establishment groups (several BusinessIds for one establishment)
    with_ins = est[est.n_inspections > 0]
    grp_with_ins = with_ins.groupby("establishment_group_id").size()
    v["establishment_groups"] = {
        "rule": "BusinessIds whose latest names normalise to the same words and whose latest permits share the "
                "street number, street and unit, or the parcel and unit (build.group_businesses)",
        "business_ids": int(len(est)),
        "groups": int(est.establishment_group_id.nunique()),
        "groups_with_several_business_ids": int((est.groupby("establishment_group_id").size() > 1).sum()),
        "business_ids_with_inspections": int(len(with_ins)),
        "groups_with_inspections": int(len(grp_with_ins)),
        "groups_with_inspections_under_several_business_ids": int((grp_with_ins > 1).sum()),
        "examples": [{"name": x.name.iloc[0], "business_ids": int(len(x)),
                      "inspections_per_business_id": [int(n) for n in x.n_inspections]}
                     for _, x in with_ins[with_ins.establishment_group_id.isin(grp_with_ins[grp_with_ins > 1].index)]
                     .sort_values(["name", "establishment_id"]).groupby("establishment_group_id", sort=False)][:10],
    }
    # comment scores: completed inspections only (not Pending / Requested), test records left out
    cs = fetched[~fetched.status.isin(["Pending", "Requested"]) & ~fetched.is_test_record].copy()
    cov = lambda x: {"inspections": int(len(x)), "with_comment": int(x.comment.notna().sum()),
                     "with_comment_score": int(x.comment_score.notna().sum()),
                     "share_with_comment_score": round(float(x.comment_score.notna().mean()), 4) if len(x) else None}
    v["comment_scores"] = {
        "rule": "leading 2-3 digit integer from 50 to 100 standing alone or followed by punctuation, or followed "
                "by an inspection word ('82 Follow up required'); see classify_comment(). Denominators: completed "
                "inspections with a fetched detail (Pending/Requested and test records excluded)",
        "kinds": {k: int(n) for k, n in cs.comment_kind.value_counts().items()},
        "overall": cov(cs),
        "by_year": {y: cov(x) for y, x in cs.groupby("inspection_year")},
        "by_type": {t: cov(x) for t, x in cs.groupby("inspection_type")},
        "by_status": {s: cov(x) for s, x in cs.groupby("status")},
        "by_year_status": {f"{y} | {s}": cov(x) for (y, s), x in cs.groupby(["inspection_year", "status"])
                           if len(x) >= 20},
        "score_distribution": {int(k): int(n) for k, n in cs.comment_score.dropna().astype(int).value_counts()
                               .sort_index().items()},
        "at_or_below_69": int((cs.comment_score <= 69).sum()),
        "score_stats_by_status": {
            st: {"n": int(x.comment_score.notna().sum()), "mean": round(float(x.comment_score.mean()), 2),
                 "median": float(x.comment_score.median()), "min": int(x.comment_score.min()),
                 "max": int(x.comment_score.max())}
            for st, x in cs[cs.comment_score.notna()].groupby("status")},
        "closure_keyword_comments": int(real.closure_keyword.notna().sum()),
    }
    # what follows a 'Followup Reinspection' record: the establishment's next non-cancelled inspection
    fr_rows = []
    d2 = real[~real.status.isin(CANCELLED) & real.establishment_id.notna() & real.actual_date.notna()].sort_values(
        ["establishment_id", "actual_date", "case_number"])
    for eid, x in d2.groupby("establishment_id"):
        recs = x[["actual_date", "status"]].to_records(index=False)
        for k in range(len(recs)):
            if recs[k][1] in FOLLOWUP_STATUSES:
                nxt = recs[k + 1] if k + 1 < len(recs) else None
                gap = (date.fromisoformat(nxt[0]) - date.fromisoformat(recs[k][0])).days if nxt is not None else None
                fr_rows.append((gap, nxt[1] if nxt is not None else None))
    v["followup_reinspection_next_inspection"] = {
        "records_with_establishment_and_date": len(fr_rows),
        "next_inspection_within_30_days": sum(1 for g, _ in fr_rows if g is not None and g <= 30),
        "next_inspection_within_14_days": sum(1 for g, _ in fr_rows if g is not None and g <= 14),
        "no_later_inspection": sum(1 for g, _ in fr_rows if g is None),
        "status_of_next_inspection_within_30_days": dict(Counter(st for g, st in fr_rows if g is not None and g <= 30)),
        "note": "supports reading 'Followup Reinspection' as 'this inspection needs a follow-up' (inference)",
    }
    # evidence for the same-day pair rule in match_pdf_rows: a Followup Reinspection and a Pass record of one
    # establishment on one day; where a comment carries a score, which of the two records has it?
    pairs, with_score = 0, []
    dd = real[~real.status.isin(CANCELLED) & real.establishment_id.notna() & real.actual_date.notna()]
    for (_, day), x in dd.groupby(["establishment_id", "actual_date"]):
        if len(x) != 2 or not (x.status.isin(FOLLOWUP_STATUSES).sum() == 1 and (x.status == "Pass").sum() == 1):
            continue
        pairs += 1
        if x.comment_score.notna().any():
            fr_, ps_ = x[x.status.isin(FOLLOWUP_STATUSES)].iloc[0], x[x.status == "Pass"].iloc[0]
            with_score.append({"date": day, "followup_case": fr_.case_number, "followup_comment_score": fr_.comment_score,
                               "pass_case": ps_.case_number, "pass_comment_score": ps_.comment_score,
                               "pass_comment_kind": ps_.comment_kind})
    v["same_day_pair_evidence"] = {
        "followup_plus_pass_pairs": pairs,
        "pairs_with_a_comment_score": len(with_score),
        "score_on_followup_record_only": sum(1 for p in with_score if pd.notna(p["followup_comment_score"])
                                             and pd.isna(p["pass_comment_score"])),
        "score_on_pass_record": sum(1 for p in with_score if pd.notna(p["pass_comment_score"])),
        "pairs": [{k: (None if pd.isna(x) else x) for k, x in p.items()} for p in with_score],
    }
    # ---- scores: distinct inspections, not report lines
    num_lines = pdf[pdf.score.notna()]
    v["scores_summary"] = {
        "inspections_with_comment_score": int(real.comment_score.notna().sum()),
        "inspections_with_pdf_score": int(real.pdf_score.notna().sum()),
        "inspections_with_any_score": int((real.comment_score.notna() | real.pdf_score.notna()).sum()),
        "inspections_matched_to_any_report_line": int(real.pdf_report_ids.notna().sum()),
        "report_lines": int(len(pdf)),
        "report_lines_numeric": int(len(num_lines)),
        "report_lines_numeric_distinct (name, street, date, score)": int(len(num_lines.drop_duplicates(
            ["facility_name", "_street_full", "date", "score"]))),
        "any_score_before_2019": int(((real.comment_score.notna() | real.pdf_score.notna())
                                      & (real.inspection_date < "2019-01-01")).sum()),
        "reports_by_kind": dict(Counter(report_kind(r, reports) for r in reports if not r["duplicate_of"])),
    }
    # ---- PDF matching
    tier2 = pdf.match_method.str.startswith("same date + street number") & ~pdf.match_method.str.startswith(
        "same date + street number + name")
    v["pdf_matching"] = {
        "match_methods_all_reports": {k: int(n) for k, n in pdf.match_method.value_counts().items()},
        "lines_matched_as_same_day_pair": int(pdf.match_method.str.contains("same-day pair|one line per same-day",
                                                                           regex=True).sum()),
        "lines_ambiguous_same_day_unresolved": int(len(ties)),
        "lines_ambiguous_other": int(pdf.match_method.str.startswith("ambiguous (").sum()),
        "lines_low_confidence_not_matched": int((pdf.match_method == LOW_CONFIDENCE).sum()),
        "tier2_lines": int(tier2.sum()),
        "tier2_lines_name_similarity_0": int((tier2 & (pdf.match_name_similarity == 0)).sum()),
        "tier2_lines_name_similarity_0_unit_matches": int((tier2 & (pdf.match_name_similarity == 0)
                                                           & pdf.match_method.str.contains(r"\+ unit", regex=True)).sum()),
    }
    v["pdf_unmatched_in_latest_score_lists"] = int(((pdf.match_method == "unmatched") & pdf.report_id.map(
        {r["report_id"]: report_kind(r, reports) == "latest score per establishment" for r in reports})).sum())
    v["pdf_reports"] = []
    first_energov = real[real.actual_date.notna()].actual_date.min()
    v["first_energov_inspection_date"] = first_energov
    for rep in reports:
        pr = pdf[pdf.report_id == rep["report_id"]]
        entry = {k: rep.get(k) for k in ("report_id", "source_url", "how_found", "archive_item", "pages",
                                         "period_start", "period_end", "status", "total_reported", "rows",
                                         "numeric_scores", "score_min", "score_max", "duplicate_of", "notes")}
        entry["kind"] = report_kind(rep, reports)
        entry["rows_equal_total_reported"] = (rep["rows"] == rep["total_reported"]) if rep["total_reported"] else None
        if len(pr) and not rep["duplicate_of"]:
            entry["matched"] = int(pr.matched_case_id.notna().sum())
            entry["match_rate"] = round(float(pr.matched_case_id.notna().mean()), 4)
            entry["match_methods"] = {k: int(n) for k, n in pr.match_method.value_counts().items()}
            era = pr[pr.date >= first_energov]
            entry["rows_dated_on_or_after_first_energov_inspection"] = int(len(era))
            entry["match_rate_those_rows"] = round(float(era.matched_case_id.notna().mean()), 4) if len(era) else None
            num = pr[pr.score.notna()]
            entry["numeric_rows_matched"] = int(num.matched_case_id.notna().sum())
            entry["numeric_rows_matched_energov_status"] = {
                str(k): int(n) for k, n in num.energov_status.value_counts().items()}
            agree = num[num.score_agrees_with_comment.notna()]
            entry["score_vs_comment"] = {"both_present": int(len(agree)),
                                         "equal": int(agree.score_agrees_with_comment.sum()),
                                         "differ": agree[~agree.score_agrees_with_comment.astype(bool)][
                                             ["facility_name", "date", "score", "energov_case_number",
                                              "energov_comment_score"]].to_dict("records")}
            if rep["period_start"]:
                inper = real[(real.inspection_date >= rep["period_start"]) & (real.inspection_date <= rep["period_end"])
                             & ~real.status.isin(CANCELLED)]
                matched = set(pr.matched_case_id.dropna())
                pair_matched = set(pr[pr.match_method.str.contains("same-day pair|one line per same-day",
                                                                   regex=True)].matched_case_id.dropna())
                in_tie = {c for i in pr.index if i in ties for c in ties[i]} - matched
                fr = inper[inper.status.isin(FOLLOWUP_STATUSES)]
                entry["energov_in_period"] = {
                    "not_cancelled": int(len(inper)),
                    "matched_to_this_report": int(inper.case_id.isin(matched).sum()),
                    "by_status": {s: {"energov": int(len(x)), "in_report": int(x.case_id.isin(matched).sum()),
                                      "missing_from_report": int((~x.case_id.isin(matched | in_tie)).sum())}
                                  for s, x in inper.groupby("status")},
                }
                entry["followup_reinspection_in_period"] = int(len(fr))
                entry["followup_reinspection_matched_via_same_day_pair"] = int(fr.case_id.isin(pair_matched).sum())
                entry["followup_reinspection_in_unresolved_same_day_tie"] = int(fr.case_id.isin(in_tie).sum())
                entry["followup_reinspection_missing_from_report"] = int((~fr.case_id.isin(matched | in_tie)).sum())
                entry["followup_reinspection_missing_with_comment_score"] = int(
                    (~fr.case_id.isin(matched | in_tie) & fr.comment_score.notna()).sum())
        v["pdf_reports"].append(entry)
    v["pdf_inspections_in_several_reports"] = {
        "inspections": int(sum(1 for v_ in per_case.values() if len(v_) > 1)),
        "with_conflicting_scores": {c: [list(map(str, t)) for t in v_] for c, v_ in pdf_conflicts.items()}}
    allagree = pdf[pdf.score_agrees_with_comment.notna()]
    v["pdf_vs_comment_all_reports"] = {"both_present": int(len(allagree)),
                                      "equal": int(allagree.score_agrees_with_comment.astype(bool).sum())}
    v["pdf_manifest_summary"] = {
        "city_files": [{k: c.get(k) for k in ("name", "url", "status", "sha256", "classification")}
                       for c in manifest.get("city", [])],
        "wayback_items_checked": len(manifest.get("wayback", {}).get("checked", [])),
        "wayback_classification": dict(Counter(c["classification"] for c in manifest.get("wayback", {})
                                               .get("checked", []))),
        "wayback_score_reports": manifest.get("wayback", {}).get("score_reports"),
        "robots": {h: {k: r.get(k) for k in ("robots_url", "status", "final_url")}
                   for h, r in manifest.get("robots", {}).items()},
    }
    contact_rx = (r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?<![\w-])\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?![\w-])")
    id_cols = {"case_id", "case_number", "link_number", "link_id", "establishment_id", "establishment_group_id",
               "first_permit", "latest_permit", "parcel", "source_url", "api_url", "latest_permit_url",
               "matched_case_id", "energov_case_number", "report_source_url", "pdf_report_ids"}

    def contact_hits(df):
        out = {}
        for c in df.columns:
            if c in id_cols or c.startswith("_") or not (df[c].dtype == object or pd.api.types.is_string_dtype(df[c])):
                continue
            n = int(df[c].dropna().astype(str).str.contains(contact_rx, regex=True).sum())
            if n:
                out[c] = n
        return out
    mailing_outside = (ins.address_type == "Mailing Address") & ins.address_withheld
    v["privacy"] = {
        "rule": "e-mail addresses and phone numbers are removed from free text before anything is cached; "
                "certified-food-manager / staff names and certificate numbers are removed from comments "
                "(common.CommentScrubber); the free-text permit Description is not kept (fetch.py); probable "
                "home addresses are published as city, state and ZIP only (build.withhold_address)",
        "contact_patterns_left_in_text_columns": {"inspections": contact_hits(ins), "establishments": contact_hits(est),
                                                  "pdf_scores": contact_hits(pdf)},
        "scrub_at_fetch": fmeta.get("comment_scrub"),
        "scrub_at_build (second pass)": scrubber.stats,
        "comments_with_redaction_marker": int(ins.comment.fillna("").str.contains(r"\[(?:e-mail|phone|name|cert)",
                                                                                   regex=True).sum()),
        "addresses_withheld": {
            "rule": "published as city, state and ZIP only (no parcel): a mailing address, and any address of a "
                    "mobile or temporary vendor (truck, mobile, catering, construction-site, temporary, special-event "
                    "or seasonal permit, or an EH-Food Truck inspection), unless it is a street address in Allen "
                    "that is on a fixed establishment's permit or that several businesses use as their permit "
                    "site; then the same street address on every other record; and a report line printing one of "
                    "those addresses, or another town's address that is not an EnerGov address kept in full. "
                    "Matching uses the full addresses internally (build.withhold_address, propagate_withholding).",
            "exempt_street_addresses": len(exempt),
            "first_pass": first_pass,
            "inspection_rows": int(ins.address_withheld.sum()),
            "inspection_rows_mailing_address": int(mailing_outside.sum()),
            "inspection_rows_by_type": {k: int(n) for k, n in ins[ins.address_withheld].inspection_type
                                        .value_counts().items()},
            "inspection_distinct_addresses_withheld": int(ins[ins.address_withheld]._address_full.nunique()),
            "inspection_rows_establishment_address_withheld": int((ins.establishment_address_withheld == True).sum()),  # noqa: E712
            "permits": int(sum(p["address_withheld"] for p in permits)),
            "establishments": int(est.address_withheld.sum()),
            "report_lines": int(pdf.address_withheld.sum()),
            "report_lines_by_reason": dict(Counter(r for r in pdf_rule if r)),
        },
        "address_types_on_inspection_rows": {str(k): int(n) for k, n in
                                             ins.address_type.value_counts(dropna=False).items()},
    }
    v["scoring_rules"] = {"faq_url": FAQ_URL, "scale": "100-point; start at 100, deduct 3/2/1 per item",
                          "demerits_inferred": "100 - score, for comparison with demerit-scored cities"}

    # ---- write (integer columns as integers, not floats)
    for df, cs_ in ((ins, ("comment_score", "comment_demerits_inferred", "pdf_score")),
                    (est, ("latest_comment_score", "group_latest_comment_score")),
                    (pdf, ("score", "demerits_inferred", "energov_comment_score"))):
        for c in cs_:
            df[c] = pd.to_numeric(df[c]).astype("Int64")
    cols = ["case_id", "case_number", "inspection_type", "status", "is_test_record", "test_record_basis",
            "inspection_date", "date_source", "scheduled_date", "actual_date", "request_date", "inspector",
            "is_reinspection", "comment_score", "comment_kind", "comment_demerits_inferred", "comment",
            "closure_keyword", "pdf_score", "pdf_rating_text", "pdf_report_ids", "establishment_id",
            "establishment_group_id", "establishment_name", "establishment_category", "establishment_address",
            "establishment_address_withheld", "join_method", "join_note", "address", "address_withheld",
            "address_type", "parcel", "link_type", "link_number", "link_id", "inspection_module", "detail_status",
            "source_url", "api_url"]
    ins = ins.sort_values(["inspection_date", "case_number"], na_position="last")
    ins[cols].to_csv(DATA / "inspections.csv", index=False)
    ecols = ["establishment_id", "establishment_group_id", "group_n_business_ids", "name", "dba", "other_names",
             "category", "categories", "kind", "mobile_vendor", "is_test_establishment", "sources", "business_status",
             "opened_date", "closed_date", "address", "address_withheld", "parcel", "n_permits", "n_op_permits",
             "first_permit", "latest_permit", "latest_permit_status", "latest_permit_expires", "latest_permit_url"]
    ecols += [c for c in est.columns if c not in ecols and not c.startswith("_") and c != "street"]
    est = est.sort_values(["name", "establishment_id"], na_position="last")
    est[ecols].to_csv(DATA / "establishments.csv", index=False)
    pcols = ["report_id", "report_period_start", "report_period_end", "page", "row_in_report", "facility_name",
             "address", "address_withheld", "street", "city", "zip", "date", "score", "demerits_inferred",
             "rating_text", "result_text", "matched_case_id", "energov_case_number", "match_method",
             "match_name_similarity", "energov_inspection_type", "energov_status", "energov_inspection_date",
             "energov_establishment_name", "energov_comment_score", "energov_comment_kind",
             "score_agrees_with_comment", "parse_note", "report_source_url"]
    pdf[pcols].to_csv(DATA / "pdf_scores.csv", index=False)
    # every withheld street address ('733 CLIFFVIEW'): does it still appear anywhere in the written tables?
    csv_text = "\n".join((DATA / f).read_text(encoding="utf-8").upper()
                         for f in ("inspections.csv", "establishments.csv", "pdf_scores.csv"))
    streets = {k for k in (skey(a) for a in list(ins[ins.address_withheld]._address_full)
                           + [p["address_full"] for p in permits if p["address_withheld"]]) if k and k[1]}
    left = set()
    if streets:  # one pass: '733 CLIFFVIEW', '733 W CLIFFVIEW', '733 W. CLIFFVIEW'
        rx = re.compile(r"(?<![\d-])(\d+)\s+(?:[NSEW]\.?\s+)?(" + "|".join(sorted(
            {re.escape(k[1].split()[0]) for k in streets}, key=len, reverse=True)) + r")\b")
        left = {(m.group(1), m.group(2)) for m in rx.finditer(csv_text)} & {(k[0], k[1].split()[0]) for k in streets}
    v["privacy"]["addresses_withheld"]["withheld_street_addresses_checked"] = len(streets)
    v["privacy"]["addresses_withheld"]["withheld_street_addresses_still_in_csvs"] = len(left)
    v["readme_phrases"] = readme_phrases(v)
    (DATA / "validation.json").write_text(json.dumps(v, indent=1, default=str))
    print(f"inspections {len(ins)}, establishments {len(est)}, pdf rows {len(pdf)}")
    print("join methods (fetched):", v["joins"]["join_method_fetched_details"])
    for e in v["pdf_reports"]:
        print(e["report_id"], e.get("rows"), e.get("total_reported"), e.get("match_rate"),
              e.get("followup_reinspection_missing_from_report"), e.get("score_vs_comment", {}).get("both_present"),
              e.get("score_vs_comment", {}).get("equal"))
    check_readme(v["readme_phrases"])

if __name__ == "__main__":
    main()
