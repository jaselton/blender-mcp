"""Pre-commit privacy check: are any of the people's names that the scrubbers remove still
anywhere in this folder (code, README, tables, raw files)?

The names are learned from the *unscrubbed* text in the local HTTP cache (ALLEN_CACHE): the
inspector comments of every getById response and the free-text Description of every permit /
licence row. The same rules as common.CommentScrubber pick out the names (words after or before
"CFM", "owner", "manager", "PIC", "spoke with", "per", ...). The check then searches every file
in this folder for them and reports file, line / column and a masked form of the name (first
letter and length), so the names themselves are never printed.

It also looks for driver's-licence numbers and the "per <Name>" / "spoke to" / "confirmed with"
phrases of permit notes in data/raw/.

    ALLEN_CACHE=/path/to/cache python privacy_check.py      # exit code 1 if anything is found

Needs the cache (it is not committed); it never goes to the network.
"""

import csv
import gzip
import io
import re
import sys
from collections import Counter, defaultdict

from common import (CERT, GETBYID_URL, HERE, RAW, TENANT_HEADERS, WORD, Client, CommentScrubber, license_body,
                    read_gz_json, scrub_contacts, search)
from fetch import ESTABLISHMENT_SOURCES, PAGE, SKIP_DETAIL_STATUSES

# Checks on data/raw/ for the kinds of note found in permit descriptions.
RAW_PATTERNS = {
    "driver_licence_number": re.compile(r"\b(?:DL|D\.L\.|driver'?s?\s+licen[cs]e)\s*[-#:]?\s*\d{6,}", re.I),
    "phone_extension_after_redaction": re.compile(r"\[phone removed\]\s*(?:x|ext\.?)\s*\d{2,5}", re.I),
    "note_phrase_then_Firstname_Lastname": re.compile(
        r"\b(?i:per|spoke\s+(?:to|with)|talked\s+(?:to|with)|confirmed\s+with)\s+[A-Z][a-z]+\s+[A-Z][a-z]+"),
}


NOTE_NAME = re.compile(r"\b(?i:per|spoke\s+(?:to|with)|talked\s+(?:to|with)|confirmed\s+with)\s+"
                       r"(?:(?i:the\s+)?(?i:owner|manager|property\s+man\w*)\s+)?([A-Z][a-z]+\s+[A-Z][a-z]+)\b")


def mask(name):
    return " ".join(w[0] + "*" * (len(w) - 1) for w in name.split())


def names_in(texts, keep=(), groups=None):
    """Names (strings) the CommentScrubber rules find in these texts."""
    sc = CommentScrubber(texts, keep, groups)
    found = set()
    for t in {t for t in texts if isinstance(t, str) and t}:
        t2 = CERT.sub(lambda m: m.group(1) + m.group(2) + " [cert]", scrub_contacts(t)[0])
        spans = sc._spans(t2, bool(re.search(r"[a-z]", t2)))
        if sc.learned_rx:
            spans += [(m.start(1), m.end(1)) for m in sc.learned_rx.finditer(t2)]
        for a, b in spans:
            found.add(re.sub(r"\s+", " ", t2[a:b]).strip())
    return found


def load_unscrubbed(client):
    listing = read_gz_json(RAW / "listing.json.gz")
    comments, inspectors, groups = [], set(), []
    for d in listing.values():
        for r in d["rows"]:
            if r.get("CaseStatus") in SKIP_DETAIL_STATUSES:
                continue
            x = client.json("GET", GETBYID_URL.format(id=r["CaseId"]), headers=TENANT_HEADERS).get("Result") or {}
            comments.append(x.get("Comment"))
            inspectors.add(x.get("AssignedInspectorName"))
            groups.append((x.get("LinkId") or r["CaseId"]).lower())
    descs, dgroups = [], []
    for label, module, tid in ESTABLISHMENT_SOURCES:
        page = 1
        while True:
            res = search(client, license_body(client, module, tid, page=page, size=PAGE))
            for r in res.get("EntityResults") or []:
                descs.append(r.get("Description"))
                dgroups.append((r.get("BusinessId") or r["CaseId"]).lower())
            if page >= res["TotalPages"]:
                break
            page += 1
    return comments, {i for i in inspectors if i}, groups, descs, dgroups


def text_files():
    for p in sorted(HERE.glob("*.py")) + [HERE / "README.md"]:
        yield p, p.read_text(encoding="utf-8", errors="replace")
    data = HERE / "data"
    for p in sorted(data.glob("*.csv")) + sorted(data.glob("*.json")) + sorted(RAW.glob("*.json")):
        yield p, p.read_text(encoding="utf-8", errors="replace")
    for p in sorted(RAW.glob("*.json.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            yield p, fh.read()


def main():
    client = Client(offline=True)
    comments, inspectors, groups, descs, dgroups = load_unscrubbed(client)
    # from permit descriptions only full names: a lone word after a role word there is usually not a name
    # ("For App Testing and Training Purposes")
    names = names_in(comments, inspectors, groups) | {n for n in names_in(descs, inspectors, dgroups)
                                                      if len(n.split()) >= 2}
    vocab = CommentScrubber(comments, inspectors, groups).vocab
    # permit notes: "per Jane Doe", "spoke to Jane Doe", "confirmed with Jane Doe"
    for t in descs:
        if isinstance(t, str):
            for m in NOTE_NAME.finditer(t):
                names.add(m.group(1))
    keep_words = {w.lower() for n in inspectors for w in WORD.findall(n)}
    full = sorted({n for n in names if len(n.split()) >= 2 and not all(w.lower() in keep_words for w in n.split())},
                  key=len, reverse=True)
    single = sorted({n for n in names if len(n.split()) == 1 and len(n) >= 3 and n.lower() not in keep_words})
    print(f"names learned from unscrubbed cache: {len(full)} full names, {len(single)} single words "
          f"(from {sum(1 for c in comments if c)} comments and {sum(1 for d in descs if d)} permit descriptions)")
    full_rx = re.compile(r"\b(" + "|".join(re.escape(n).replace(r"\ ", r"\s+") for n in full) + r")\b", re.I) \
        if full else None
    single_rx = re.compile(r"\b(" + "|".join(re.escape(n) for n in single) + r")\b") if single else None

    problems = 0
    for path, text in text_files():
        rel = path.relative_to(HERE)
        is_code = path.suffix in (".py", ".md")
        hits = defaultdict(Counter)
        if path.suffix == ".csv":
            rows = csv.reader(io.StringIO(text))
            header = next(rows)
            cells = ((f"column {header[j]}", v) for row in rows for j, v in enumerate(row) if v)
        else:
            cells = ((f"line {i}" if is_code else "text", line) for i, line in enumerate(text.splitlines(), 1))
        for where, value in cells:
            for rx, kind in ((full_rx, "full name"), (single_rx, "single name")):
                if rx is not None:
                    for m in rx.finditer(value):
                        hits[(kind, where)][mask(m.group(1))] += 1
            if path.parent == RAW:
                for label, rx in RAW_PATTERNS.items():
                    # a note phrase followed by two ordinary words ("per Approved Variance") is not a name
                    n = sum(1 for m in rx.finditer(value) if label != "note_phrase_then_Firstname_Lastname"
                            or not all(w.lower() in vocab for w in m.group().split()[-2:]))
                    if n:
                        hits[("pattern", label)]["-"] += n
        for (kind, where), c in sorted(hits.items()):
            # single first names in business-name columns are expected ("Maria's Tamales"); report them
            # for review, but count only full names, patterns, code/README hits and comment hits as problems
            serious = kind in ("full name", "pattern") or is_code or "comment" in where
            problems += sum(c.values()) if serious else 0
            print(f"  {'PROBLEM' if serious else 'review '} {rel}: {kind} in {where}: "
                  + ", ".join(f"{k} x{v}" for k, v in c.most_common(8)))
    print("privacy_check:", "OK" if not problems else f"{problems} hits to fix")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
