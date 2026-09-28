"""Shared pieces for the Allen inspection scripts: paths, a polite caching HTTP
client, the EnerGov request templates, and the privacy scrubbers.

Every HTTP response is cached on disk (ALLEN_CACHE, default ./.cache), keyed by
method + URL + request body, so a re-run never fetches the same thing twice.
Requests to one host are spaced by at least MIN_INTERVAL[host] seconds, and
403/429/5xx responses or dropped connections are retried with exponential
backoff. E-mail addresses and phone numbers are removed from JSON responses
*before* they are written to the cache, and inspector e-mail/phone fields are
blanked, so no contact details are ever stored.
"""

import copy
import gzip
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RAW = Path(os.environ.get("ALLEN_RAW", DATA / "raw"))
CACHE = Path(os.environ.get("ALLEN_CACHE", HERE / ".cache"))
TZ = "America/Chicago"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")

# ----------------------------------------------------------------------------- EnerGov
ENERGOV = "https://cityofallentx-energovweb.tylerhost.net/apps/selfservice"
API = ENERGOV + "/api/energov"
TENANT_HEADERS = {"tenantId": "1", "tenantName": "AllenTXProd", "Tyler-TenantUrl": "AllenTXProd",
                  "Tyler-Tenant-Culture": "en-US", "Accept": "application/json",
                  "Content-Type": "application/json"}
PORTAL_INSPECTION_URL = ENERGOV + "#/inspectionDetail/inspection/{id}"  # the portal page for one inspection
PORTAL_OP_PERMIT_URL = ENERGOV + "#/operationalPermit/{id}"
PORTAL_LICENSE_URL = ENERGOV + "#/businessLicense/{id}"
GETBYID_URL = API + "/inspections/getById/{id}"

# Minimum seconds between two requests to the same host.
MIN_INTERVAL = {
    "cityofallentx-energovweb.tylerhost.net": 1.15,
    "web.archive.org": 4.0,
    "www.cityofallen.org": 1.5,
    "cms3.revize.com": 1.5,
}
DEFAULT_INTERVAL = 2.0

# ----------------------------------------------------------------------------- privacy
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"(?<![\w-])\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?![\w-])")
CONTACT_KEYS = re.compile(r"(e-?mail|phone|fax)", re.I)


def scrub_contacts(text):
    """Replace e-mail addresses and phone numbers in free text. Returns (text, n_replaced)."""
    if not isinstance(text, str) or not text:
        return text, 0
    text, n1 = EMAIL.subn("[e-mail removed]", text)
    text, n2 = PHONE.subn("[phone removed]", text)
    return text, n1 + n2


# ---- names and certificate numbers in inspector comments
# Words that introduce a person at the establishment. A name right after one of these (or right
# before "-owner", "(PIC)", ", manager" ...) is removed unless the word is ordinary vocabulary (it
# appears at least VOCAB_MIN times elsewhere in the corpus, not right after a role word), a stop
# word, or part of a city inspector's name (inspector names are part of the official record and
# are kept). In a comment written in mixed case only Capitalised words count as names.
ROLE = (r"co-?owners?|owners?|franchisee|operators?|general\s+manager|kitchen\s+manager|store\s+manager|"
        r"district\s+manager|managers?|mgr|gm|pic|person\s+in\s+charge|certified\s+food\s+managers?|"
        r"food\s+managers?|cfm|chef|cooks?|employees?|staff|director|supervisor|assistant|"
        r"spoke\s+(?:with|to)|talked\s+(?:with|to)|met\s+with|per|contact|named?|fsm|cfpm|training(?:\s+with)?|"
        r"(?:food\s+manager\s+|cfm\s+|fsm\s+)?certificates?(?=\s*:)")
ROLE_SEQ = re.compile(rf"(?:\b(?:{ROLE})\b[\s:,\-–(]*)+", re.I)
ROLE_AFTER = re.compile(rf"\b([A-Z][a-z'’]+(?:\s+[A-Z][a-z'’]+)?)(?:\s*[-(]\s*|\s*,\s*(?:the\s+)?)"
                        rf"(?=(?:{ROLE})\b)", re.I)
NAME_THEN_CERT = re.compile(r"\b([A-Z][a-z'’]+(?:\s+[A-Z][a-z'’]+)?)\s+(?:provided|showed|presented|has|had|holds)\s+"
                            r"(?:(?:a|an|the|his|her|their|valid|current|expired|new)\s+)?"
                            r"(?:fsm|cfm|food\s+manager|food\s+handler|certificate|card)", re.I)
# "Jane Doe, CFM, ServSafe, Cert# ...", "John Roe, ServSafe, CFM, ...", "Mary Q Public, CFM" (made-up examples)
NAME_THEN_CFM = re.compile(r"\b([A-Z][a-z'’]+(?:\s+(?:[A-Z]\.?|[A-Z][a-z'’]+(?:-[A-Z][a-z'’]+)?)){0,3})\s*,\s*"
                           r"(?:[A-Za-z0-9 ]{2,40},\s*)?(?:CFM|CFPM|FSM|Certified\s+Food\s+(?:Protection\s+)?Manager)\b")
POSSESSIVE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)['’]s\s+(?i:current\s+|valid\s+|expired\s+)?"
                        r"(?i:reply|response|email|e-mail|call|phone|number|text|message|cfm|fsm|food\s+manager|"
                        r"certificate|card)\b")
CERT = re.compile(r"(?i)\b(cfm|certified\s+food\s+manager|food\s+manager|cert(?:ificate|ification)?|servsafe|"
                  r"food\s+handler|license|lic|fm)\b([^\n]{0,40}?)(#?\s*[A-Z]{0,3}(?=[\d-]*\d{5})\d[\d-]{4,}\d)")
STOP = {"the", "a", "an", "and", "or", "of", "to", "on", "in", "at", "for", "is", "was", "were", "be", "been",
        "has", "had", "have", "that", "this", "with", "will", "not", "no", "by", "from", "as", "it", "he", "she",
        "they", "his", "her", "their", "all", "said", "stated", "states", "mr", "ms", "mrs", "dr", "certificate",
        "certificates", "card", "cards", "expired", "posted", "present", "onsite", "on-site", "required", "needed",
        "informed", "notified", "request", "requested", "provided", "was", "who", "which", "if", "about",
        "approval", "email", "emails", "e-mail", "needs", "need", "wants", "want", "asked", "came", "left", "arrived",
        "understood", "understands", "agrees", "agreed", "explained", "told", "knows", "knew", "did", "does", "do",
        "would", "could", "should", "must", "may", "might", "can", "cannot", "will", "shall", "confirmed", "via",
        "available", "unavailable", "responsible", "verbal", "verbally", "phone", "call", "text", "contact", "info",
        "information", "signed", "sign", "signature", "absent", "off", "out", "away", "today", "yesterday",
        "tomorrow", "again", "also", "only", "just", "then", "there", "here", "now", "still", "currently", "new",
        "old", "previous", "former", "current", "future", "same", "other", "another", "both", "each", "every",
        "some", "any", "many", "more", "most", "less", "very", "too", "so", "such", "what", "when", "where", "why",
        "how", "into", "onto", "over", "under", "up", "down", "after", "before", "during", "until", "since",
        "while", "because", "but", "however", "therefore", "on", "site", "shift", "training", "trained", "on-duty",
        "duty", "time", "visit", "follow", "followup", "follow-up", "inspection", "routine", "approved",
        # certificate providers and words that follow "CFM," in the 2026 comments
        "servsafe", "l2s", "learn2serve", "learn2serv", "certus", "fmc", "aaa", "national", "registry", "classes",
        "prometric", "nrfsp", "premier", "texas", "tx", "cert", "certification", "expires", "occupancy",
        "above", "360training", "statefoodsafety", "americanfoodsafety", "tabc"}
VOCAB_MIN = 3
WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’]*")


class CommentScrubber:
    """Removes e-mail addresses, phone numbers, certificate numbers and the names of people at the
    establishment (owners, managers, persons in charge, certified food managers, staff) from
    inspector comments. Built from the whole comment corpus so that ordinary words can be told
    apart from names without a dictionary. Inspector names are kept."""

    def __init__(self, corpus, keep_names=(), groups=None):
        """corpus: comment texts; keep_names: inspector names; groups: optional parallel list of the
        establishment each comment belongs to. A word is ordinary vocabulary when it appears (not right
        after a role word) in the comments of at least VOCAB_MIN different establishments (or distinct
        comments, without groups): a manager's name recurs on one establishment's records only."""
        self.keep = {w.lower() for n in keep_names if isinstance(n, str) for w in WORD.findall(n)}
        corpus = list(corpus)
        groups = list(groups) if groups is not None else list(corpus)
        seen_in = {}
        for text, g in set((t, g) for t, g in zip(corpus, groups) if isinstance(t, str)):
            after = set()
            for m in ROLE_SEQ.finditer(text):
                nxt = WORD.search(text, m.end())
                if nxt and nxt.start() == m.end():
                    after.add(nxt.start())
            for w in {m.group().lower() for m in WORD.finditer(text) if m.start() not in after}:
                seen_in.setdefault(w, set()).add(g)
        self.vocab = {w for w, gs in seen_in.items() if len(gs) >= VOCAB_MIN}
        # Full names (two or more words) found by the rules anywhere in the corpus are removed
        # wherever else they occur ("Jane Doe stated she ...", "Jane Doe's current ...").
        learned = set()
        for text in {t for t in corpus if isinstance(t, str)}:
            t2 = scrub_contacts(text)[0]
            for a, b in self._spans(t2, bool(re.search(r"[a-z]", t2))):
                name = re.sub(r"\s+", " ", t2[a:b]).strip()
                if len(WORD.findall(name)) >= 2:
                    learned.add(name.lower())
        self.learned = sorted(learned, key=len, reverse=True)
        self.learned_rx = (re.compile(r"\b(" + "|".join(re.escape(n).replace("\\ ", "\\s+") for n in self.learned)
                                      + r")\b", re.I) if self.learned else None)
        self.stats = {"names": 0, "certificate_numbers": 0, "contacts": 0, "comments_changed": 0,
                      "distinct_full_names_learned": len(self.learned)}

    def _is_name(self, w, mixed):
        lw = w.lower().strip(".’'")
        if mixed and not re.match(r"^[A-Z][a-z'’]", w):
            return False
        if not mixed and re.search(r"(ed|ing|ly|tion|sion|ment)$", lw):
            return False  # all-caps text: verbs and nouns such as REMOVED, EMAILED, CORRECTION
        return (len(lw) >= 2 and lw not in STOP and lw not in self.keep and lw not in self.vocab
                and not ROLE_SEQ.fullmatch(lw))

    def _spans(self, text, mixed):
        spans = []
        for m in ROLE_SEQ.finditer(text):  # role word(s), then name words ("A B and C D")
            pos, taken, start = m.end(), 0, None
            while taken < 8:
                w = WORD.match(text, pos)
                if not w or not self._is_name(w.group(), mixed):
                    break
                spans.append((w.start(), w.end()))
                taken += 1
                sep = re.match(r"[ \t]+(?:(?:and|&)\s+)?|\s*\n\s*(?:and|&)\s+|,[ \t]*", text[w.end():])
                if not sep:
                    break
                pos = w.end() + sep.end()
        for rx in (NAME_THEN_CERT, NAME_THEN_CFM, POSSESSIVE):
            for m in rx.finditer(text):
                words = [w for w in WORD.finditer(m.group(1)) if len(w.group()) > 1]  # skip initials
                if words and all(self._is_name(w.group(), mixed) for w in words):
                    spans.append((m.start(1), m.end(1)))
        for m in ROLE_AFTER.finditer(text):  # "Jane-owner", "John (PIC)", "Jane Doe, the manager"
            words = list(WORD.finditer(m.group(1)))
            if words and all(self._is_name(w.group(), mixed) for w in words):
                spans.append((m.start(1), m.end(1)))
        # merge adjacent word spans ("Jane" + "Doe") so full names can be learned
        merged = []
        for a, b in sorted(set(spans)):
            if merged and a >= merged[-1][0] and re.fullmatch(r"[ \t]*", text[merged[-1][1]:a]):
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            elif merged and a < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        return merged

    def __call__(self, text):
        if not isinstance(text, str) or not text:
            return text
        orig = text
        text, n = scrub_contacts(text)
        self.stats["contacts"] += n

        def cert(m):
            self.stats["certificate_numbers"] += 1
            gap = re.match(r"#?(\s*)", m.group(3)).group(1)
            return m.group(1) + m.group(2) + gap + "[cert no. removed]"
        text = CERT.sub(cert, text)
        spans = self._spans(text, bool(re.search(r"[a-z]", text)))
        if self.learned_rx:
            spans += [(m.start(1), m.end(1)) for m in self.learned_rx.finditer(text)]
        if spans:
            spans = sorted(set(spans))
            out, last = [], 0
            for a, b in spans:
                if a < last:
                    continue
                out.append(text[last:a])
                out.append("[name removed]")
                self.stats["names"] += 1
                last = b
            out.append(text[last:])
            text = re.sub(r"\[name removed\]((\s*|\s+and\s+|,\s*)\[name removed\])+", "[name removed]", "".join(out))
        if text != orig:
            self.stats["comments_changed"] += 1
        return text


def scrub_comment(text):
    """Contacts only (used where no corpus is at hand); see CommentScrubber for the full rules."""
    return scrub_contacts(text)[0]


# Free-text fields that can hold contact details. Only these are scrubbed: running the phone
# pattern over every string would also hit identifiers such as business numbers ("001113-2017").
FREE_TEXT_KEYS = {"Comment", "Description", "Highlights", "ProjectName", "Notes", "Note"}


def scrub_json(obj, counter=None):
    """Recursively blank e-mail/phone/fax fields and scrub contacts out of free-text fields."""
    if counter is None:
        counter = [0]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if CONTACT_KEYS.search(k) and isinstance(v, str):
                if v:
                    counter[0] += 1
                out[k] = ""
            elif k in FREE_TEXT_KEYS and isinstance(v, str):
                out[k], n = scrub_contacts(v)
                counter[0] += n
            else:
                out[k] = scrub_json(v, counter)
        return out
    if isinstance(obj, list):
        return [scrub_json(v, counter) for v in obj]
    return obj


# ----------------------------------------------------------------------------- HTTP client
class Client:
    """requests.Session with a per-host pause, disk cache and backoff."""

    def __init__(self, cache=CACHE, log_path=None, offline=False):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.last = {}
        self.log_path = Path(log_path) if log_path else self.cache / "requests.log"
        self.offline = offline
        self.stats = {"network": 0, "cached": 0, "retries": 0, "contacts_scrubbed": 0}

    # -- helpers
    def _key(self, method, url, body):
        b = json.dumps(body, sort_keys=True, separators=(",", ":")) if body is not None else ""
        return hashlib.sha256(f"{method} {url}\n{b}".encode()).hexdigest()

    def _path(self, host, key, ext):
        return self.cache / host / key[:2] / f"{key}{ext}"

    def _wait(self, host):
        gap = MIN_INTERVAL.get(host, DEFAULT_INTERVAL)
        dt = time.time() - self.last.get(host, 0)
        if dt < gap:
            time.sleep(gap - dt)
        self.last[host] = time.time()

    def _log(self, line):
        with open(self.log_path, "a") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + line + "\n")

    def _send(self, method, url, body=None, headers=None, tries=6, ok=(200,), allow_redirects=True):
        host = urlparse(url).hostname
        err = None
        for attempt in range(tries):
            self._wait(host)
            try:
                r = self.s.request(method, url, json=body, headers=headers, timeout=120,
                                   allow_redirects=allow_redirects)
                self.stats["network"] += 1
                self._log(f"{method} {url} -> {r.status_code} {len(r.content)}B")
                if r.status_code in ok:
                    return r
                err = f"HTTP {r.status_code}"
                if r.status_code in (403, 429) or r.status_code >= 500:
                    wait = min(600, (30 if r.status_code in (403, 429) else 5) * 2 ** attempt)
                elif r.status_code == 401 and attempt < 2:
                    wait = 10  # EnerGov answered one getById with a transient 401; it succeeded on retry
                else:
                    return r  # 404 etc.: a real answer, not worth retrying
            except requests.RequestException as e:
                err = repr(e)[:200]
                self._log(f"{method} {url} -> ERROR {err}")
                wait = min(600, 5 * 2 ** attempt)
            self.stats["retries"] += 1
            time.sleep(wait)
        raise RuntimeError(f"{method} {url} failed after {tries} tries: {err}")

    # -- JSON (EnerGov): scrubbed before caching
    def json(self, method, url, body=None, headers=None, refresh=False):
        key = self._key(method, url, body)
        path = self._path(urlparse(url).hostname, key, ".json.gz")
        if path.exists() and not refresh:
            self.stats["cached"] += 1
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                entry = json.load(fh)
            self.stats["contacts_scrubbed"] += entry.get("contacts_scrubbed", 0)
            self._seen(entry.get("fetched_at"))
            return entry["response"]
        if self.offline:
            raise FileNotFoundError(f"not cached (offline): {method} {url}")
        r = self._send(method, url, body=body, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"{method} {url}: HTTP {r.status_code}")
        counter = [0]
        data = scrub_json(r.json(), counter)
        self.stats["contacts_scrubbed"] += counter[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump({"method": method, "url": url, "body": body, "fetched_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "contacts_scrubbed": counter[0], "response": data}, fh)
        tmp.replace(path)
        self._seen(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return data

    def _seen(self, ts):
        """Earliest and latest time the responses used in this run were actually downloaded."""
        if ts:
            lo, hi = self.stats.get("responses_downloaded_between", [ts, ts])
            self.stats["responses_downloaded_between"] = [min(lo, ts), max(hi, ts)]

    # -- binary (PDFs, HTML): cached as-is with a small metadata sidecar
    def binary(self, url, refresh=False, ok=(200,), allow_redirects=True):
        """Returns (status_code, bytes, meta). 404s are cached too (as a negative answer)."""
        key = self._key("GET", url, None)
        host = urlparse(url).hostname
        path = self._path(host, key, ".bin")
        meta_path = self._path(host, key, ".meta.json")
        if meta_path.exists() and not refresh:
            self.stats["cached"] += 1
            meta = json.loads(meta_path.read_text())
            content = path.read_bytes() if path.exists() else b""
            return meta["status"], content, meta
        if self.offline:
            raise FileNotFoundError(f"not cached (offline): GET {url}")
        r = self._send("GET", url, ok=ok, allow_redirects=allow_redirects)
        meta = {"url": url, "status": r.status_code, "final_url": r.url,
                "location": r.headers.get("Location"),
                "content_type": r.headers.get("Content-Type"), "last_modified": r.headers.get("Last-Modified"),
                "bytes": len(r.content), "sha256": hashlib.sha256(r.content).hexdigest(),
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        path.parent.mkdir(parents=True, exist_ok=True)
        if r.status_code == 200:
            path.write_bytes(r.content)
        meta_path.write_text(json.dumps(meta, indent=1))
        return r.status_code, r.content if r.status_code == 200 else b"", meta


# ----------------------------------------------------------------------------- robots.txt
class Robots:
    """robots.txt per RFC 9309: '*' and '$' wildcards, longest match wins, Allow wins ties.

    Python's urllib.robotparser ignores wildcards, which matters here: cms3.revize.com
    allows only '/*.pdf$' (and a few other document types) and disallows everything else.
    A missing robots.txt (4xx) means no restrictions. Rules for the '*' group and for
    ClaudeBot / anthropic-ai (if the file names them) must all allow a URL.
    """

    AGENTS = ("claudebot", "anthropic-ai")

    def __init__(self, client):
        self.client, self.rules, self.log = client, {}, {}

    @staticmethod
    def _groups(text):
        groups, agents, rules, last_was_agent = {}, [], [], False
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            k, v = [x.strip() for x in line.split(":", 1)]
            k = k.lower()
            if k == "user-agent":
                if not last_was_agent:
                    agents, rules = [], []
                agents.append(v.lower())
                for a in agents:
                    groups[a] = rules
                last_was_agent = True
            elif k in ("allow", "disallow"):
                last_was_agent = False
                if v or k == "allow":
                    rules.append((k == "allow", v))
            else:
                last_was_agent = False
        return groups

    @staticmethod
    def _match(pattern, path):
        rx = "".join(".*" if ch == "*" else "$" if (ch == "$" and i == len(pattern) - 1) else re.escape(ch)
                     for i, ch in enumerate(pattern))
        return re.match(rx, path) is not None

    def _decide(self, rules, path):
        best = None  # (length, allow)
        for allow, pat in rules:
            if pat and self._match(pat, path):
                cand = (len(pat), allow)
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and allow):
                    best = cand
        return True if best is None else best[1]

    def allowed(self, url):
        p = urlparse(url)
        host = p.hostname
        if host not in self.rules:
            robots_url = f"{p.scheme}://{host}/robots.txt"
            status, content, meta = self.client.binary(robots_url, ok=(200, 404))
            text = content.decode("utf-8", "replace") if status == 200 else ""
            groups = self._groups(text) if "user-agent" in text.lower() else {}
            self.rules[host] = groups
            self.log[host] = {"robots_url": robots_url, "status": status, "final_url": meta.get("final_url"),
                              "groups": sorted(groups), "text": text[:2000]}
        groups = self.rules[host]
        path = p.path + (f"?{p.query}" if p.query else "")
        applicable = [groups[a] for a in self.AGENTS if a in groups] or [groups.get("*", [])]
        if "*" in groups and groups["*"] not in applicable:
            applicable.append(groups["*"])
        return all(self._decide(r, path) for r in applicable)


# ----------------------------------------------------------------------------- EnerGov bodies
_CRITERIA = None


def criteria(client):
    """The portal's own blank search body (GET /search/criteria)."""
    global _CRITERIA
    if _CRITERIA is None:
        d = client.json("GET", API + "/search/criteria", headers=TENANT_HEADERS)
        _CRITERIA = d["Result"]
    return copy.deepcopy(_CRITERIA)


def inspection_body(client, type_id="none", status_id="none", page=1, size=100,
                    sort="InspectionNumber.keyword", asc=True):
    c = criteria(client)
    c.update({"Keyword": "", "SearchModule": 4, "FilterModule": 4, "PageNumber": page, "PageSize": size,
              "SortBy": sort, "SortAscending": asc})
    c["InspectionCriteria"].update({"InspectionTypeId": type_id, "InspectionStatusId": status_id,
                                    "PageNumber": page, "PageSize": size, "SortBy": sort,
                                    "SortAscending": asc})
    return c


def license_body(client, module, type_id, page=1, size=100, sort="LicenseNumber.keyword", asc=True):
    """module 12 = operational permits, module 10 = licenses (the portal's 'License' search)."""
    c = criteria(client)
    c.update({"Keyword": "", "SearchModule": module, "FilterModule": module, "PageNumber": page,
              "PageSize": size, "SortBy": sort, "SortAscending": asc})
    c["LicenseCriteria"].update({"LicenseTypeId": type_id, "LicenseClassId": "none", "LicenseStatusId": "none",
                                 "BusinessStatusId": "none", "CompanyTypeId": "none", "BusinessTypeId": "none",
                                 "PageNumber": page, "PageSize": size, "SortBy": sort, "SortAscending": asc})
    return c


def search(client, body):
    d = client.json("POST", API + "/search/search", body=body, headers=TENANT_HEADERS)
    if not d.get("Success", True) and d.get("Result") is None:
        raise RuntimeError(f"search failed: {d.get('ErrorMessage')!r} {d.get('StatusCode')}")
    return d["Result"]


# ----------------------------------------------------------------------------- raw files
def write_gz_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # mtime=0 and no file name in the gzip header: the same content always gives the same bytes
    with open(tmp, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        gz.write(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    tmp.replace(path)


def read_gz_json(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)
