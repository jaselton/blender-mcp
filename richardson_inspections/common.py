"""Shared pieces for the Richardson scripts: paths, the hosts this project may and
may not contact, a polite caching HTTP client with a robots.txt check, the
privacy scrubbers, and helpers for HealthTrak report keys.

Hosts
-----
The city's own inspection app (discovery.cor.gov, "HealthTrak") publishes
robots.txt "User-agent: * / Disallow: /", so this project never requests it.
The Frisco / Plano portal host is also off limits. Everything here comes from:

* web.archive.org        Wayback CDX API and raw ("id_") captures; 4 s apart
* lives.data.socrata.com /resource (SODA) API only; robots.txt Crawl-delay 1
* api.municode.com       the city code (robots.txt: none published, 404)

Any other host raises an error before a request is made. robots.txt is read
for each host first and obeyed.
"""

import base64
import gzip
import hashlib
import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RAW = DATA / "raw"
CACHE = Path(os.environ.get("RICHARDSON_CACHE", HERE / ".cache"))

# An honest, non-browser User-Agent: every host used here allows automated access.
UA = "richardson-inspections/1.1 (research; score-history archive of public records; python-requests)"

BLOCKED_HOSTS = {"discovery.cor.gov", "inspections.myhealthdepartment.com"}
ALLOWED_HOSTS = {"web.archive.org", "lives.data.socrata.com", "api.municode.com"}

# Response headers kept in the cache metadata (plus every X-SODA2-* header).
KEEP_HEADERS = {"last-modified", "memento-datetime", "link", "x-archive-src", "content-type"}

# Minimum seconds between two requests to the same host.
MIN_INTERVAL = {"web.archive.org": 4.0, "lives.data.socrata.com": 1.5, "api.municode.com": 2.0}

# ----------------------------------------------------------------------------- source URLs
HEALTHTRAK = "discovery.cor.gov/public/health/healthtrak.nsf"          # never requested
CORSCORES = HEALTHTRAK + "/CORScores.json"
SCORES_PAGE = HEALTHTRAK + "/webScores.html"
WAYBACK = "https://web.archive.org"
CDX = WAYBACK + "/cdx/search/cdx"
SOCRATA = "https://lives.data.socrata.com/resource"
LIVES_DATASET = "yf9v-pthu"          # City of Richardson - LIVES Standard Inspection Data
LIVES_FEED_INFO = "tqfg-ya7z"        # City of Richardson - LIVES Standard Feed Information
MUNICODE_API = "https://api.municode.com"
MUNICODE_PRODUCT = 10221
SEC_10_126_NODE = "PTIICOOR_CH10HEHUSE_ARTVFOSESA_S10-126RUADCOPR"
SEC_10_126_URL = ("https://library.municode.com/tx/richardson/codes/code_of_ordinances?nodeId="
                  + SEC_10_126_NODE)
GOVQA_URL = "https://richardsontx.govqa.us/WEBAPP/_rs/supporthome.aspx"


def wayback_url(ts, original):
    """Raw capture URL (the 'id_' form returns the archived bytes without the Wayback banner)."""
    return f"{WAYBACK}/web/{ts}id_/{original}"


# ----------------------------------------------------------------------------- privacy
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# NANP numbers written with separators (fictional examples): 214-555-0100, (214) 555-0100,
# 214.555.0100, +1 214 555 0100
PHONE = re.compile(r"(?<![\w-])(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?![\w-])")
# ... and without them (e.g. href="tel:2145550100"); the area code and exchange cannot start with 0/1,
# and a leading '.' or digit/letter excludes decimals, hashes and longer IDs
PHONE_BARE = re.compile(r"(?<![\w.-])(?:\+?1)?[2-9]\d{2}[2-9]\d{6}(?![\w-])")
# any tel: link target still left after the two patterns above (odd formats)
TEL_LINK = re.compile(r"(?i)\btel:(?!\[phone removed\])[^\"'<>\s]+")
CONTACT_KEYS = re.compile(r"(e-?mail|phone|fax)", re.I)
# Bump when the scrubbers change, so pages cached under an older version are scrubbed again.
SCRUB_VERSION = 2


def scrub_contacts(text):
    """Replace e-mail addresses and phone numbers in free text. Returns (text, n_replaced)."""
    if not isinstance(text, str) or not text:
        return text, 0
    text, n1 = EMAIL.subn("[e-mail removed]", text)
    text, n2 = PHONE.subn("[phone removed]", text)
    text, n3 = PHONE_BARE.subn("[phone removed]", text)
    text, n4 = TEL_LINK.subn("tel:[phone removed]", text)
    return text, n1 + n2 + n3 + n4


def count_contacts(text):
    return scrub_contacts(text)[1] if isinstance(text, str) else 0


def scrub_json(obj, counter):
    """Drop phone/e-mail/fax fields and scrub contact details out of every string."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if CONTACT_KEYS.search(k):
                counter[0] += 1
                continue
            out[k] = scrub_json(v, counter)
        return out
    if isinstance(obj, list):
        return [scrub_json(v, counter) for v in obj]
    if isinstance(obj, str):
        s, n = scrub_contacts(obj)
        counter[0] += n
        return s
    return obj


# ----------------------------------------------------------------------------- hashing / io
def sha256(b):
    return hashlib.sha256(b).hexdigest()


def sha1_b32(b):
    """The digest format the Wayback CDX API reports (base32 SHA-1 of the archived payload)."""
    return base64.b32encode(hashlib.sha1(b).digest()).decode()


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path, obj, gz=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=False) + "\n"
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def read_json(path):
    path = Path(path)
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------- report keys
KEY_RE = re.compile(r"webScoresForReadonly(?:All)?/([^?\"'\s>]*)", re.I)


def key_from_report_url(url):
    """'.../webScoresForReadonlyAll/12328~92~04/04/2024?OpenDocument' -> '12328~92~04/04/2024'.
    The 2019 weekly listings link to '.../webScoresForReadonly/<key>?OpenDocument' (no 'All')."""
    m = KEY_RE.search(url or "")
    return m.group(1) if m else None


def make_key(permit, score, mdY):
    """The city's report key format: <permit>~<score>~<MM/DD/YYYY> (score blank when unscored)."""
    return f"{permit}~{score}~{mdY}"


def parse_mdY(s):
    """'04/04/2024' -> date(2024, 4, 4); None when blank or not a full MM/DD/YYYY date."""
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if not m:
        return None
    try:
        d = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
    return d if d.year >= 1990 else None


def ts_to_datetime(ts):
    return datetime.strptime(ts, "%Y%m%d%H%M%S")


# ----------------------------------------------------------------------------- HTTP client
class Client:
    """requests.Session with an allow-list of hosts, robots.txt check, per-host pause,
    disk cache (keyed by URL) and exponential backoff on resets, 429 and 5xx."""

    def __init__(self, cache=CACHE, offline=False):
        import requests  # imported here so build.py never needs it
        self.requests = requests
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.last = {}
        self.offline = offline
        self.log_path = self.cache / "requests.log"
        self.stats = {"network": 0, "cached": 0, "retries": 0}
        self.robots = {}

    def _check_host(self, url):
        host = urlparse(url).hostname
        if host in BLOCKED_HOSTS:
            raise PermissionError(f"{host} is off limits for this project (robots.txt disallows it)")
        if host not in ALLOWED_HOSTS:
            raise PermissionError(f"{host} is not on this project's list of hosts")
        return host

    def _wait(self, host):
        """Sleep until MIN_INTERVAL seconds have passed since the previous request to host finished."""
        gap = MIN_INTERVAL.get(host, 4.0)
        dt = time.time() - self.last.get(host, 0)
        if dt < gap:
            time.sleep(gap - dt)

    def _log(self, line):
        with open(self.log_path, "a") as fh:
            fh.write(utcnow() + " " + line + "\n")

    def _send(self, url, tries=8, timeout=180):
        host = self._check_host(url)
        err = None
        for attempt in range(tries):
            self._wait(host)
            try:
                try:
                    r = self.s.get(url, timeout=timeout)
                finally:  # the pause is measured from the end of the previous request
                    self.last[host] = time.time()
                self.stats["network"] += 1
                self._log(f"GET {url} -> {r.status_code} {len(r.content)}B")
                if r.status_code == 200 or r.status_code in (301, 302, 404):
                    return r
                err = f"HTTP {r.status_code}"
                if r.status_code == 429:
                    wait = min(900, 60 * 2 ** attempt)
                elif r.status_code >= 500 or r.status_code == 403:
                    wait = min(600, 8 * 2 ** attempt)
                else:
                    return r
            except self.requests.RequestException as e:  # resets, timeouts
                err = repr(e)[:200]
                self._log(f"GET {url} -> ERROR {err}")
                wait = min(600, 8 * 2 ** attempt)
            self.stats["retries"] += 1
            time.sleep(wait)
        raise RuntimeError(f"GET {url} failed after {tries} tries: {err}")

    def get(self, url, refresh=False, scrub=False):
        """Returns (status, bytes, meta). Every answer (including 404) is cached.

        meta always holds sha256 / sha1_base32 of the bytes as served. With scrub=True
        (web pages that carry contact details) e-mail addresses and phone numbers are
        removed from the body *before* it is cached, so they are never stored; the
        returned bytes are then the scrubbed text and meta["contacts_removed"] says
        how many were taken out.
        """
        self._check_host(url)
        key = hashlib.sha256(url.encode()).hexdigest()
        host = urlparse(url).hostname
        body_path = self.cache / host / f"{key}.bin"
        meta_path = self.cache / host / f"{key}.json"
        if meta_path.exists() and not refresh:
            self.stats["cached"] += 1
            meta = json.loads(meta_path.read_text())
            body = body_path.read_bytes() if body_path.exists() else b""
            if scrub and meta.get("scrub_version") != SCRUB_VERSION:  # cached unscrubbed or by an older scrubber
                meta.setdefault("sha1_base32", sha1_b32(body))
                body, meta = self._scrub_cached(body, meta, body_path, meta_path)
            return meta["status"], body, meta
        if self.offline:
            raise FileNotFoundError(f"not cached (offline run): {url}")
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        r = self._send(url)
        meta = {"url": url, "status": r.status_code, "final_url": r.url,
                "content_type": r.headers.get("Content-Type"), "bytes": len(r.content),
                "sha256": sha256(r.content), "sha1_base32": sha1_b32(r.content), "fetched_at": utcnow(),
                "headers": {k: v for k, v in r.headers.items()
                            if k.lower() in KEEP_HEADERS or k.lower().startswith("x-soda2-")}}
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body = r.content
        if scrub:
            body, meta = self._scrub_cached(body, meta, body_path, meta_path)
        else:
            body_path.write_bytes(body)
            meta_path.write_text(json.dumps(meta, indent=1))
        return r.status_code, body, meta

    @staticmethod
    def _scrub_cached(body, meta, body_path, meta_path):
        text, n = scrub_contacts(body.decode("utf-8", "replace"))
        body = text.encode("utf-8")
        # a re-scrub of an already scrubbed body adds what the older scrubber missed
        meta["contacts_removed"] = meta.get("contacts_removed", 0) + n
        meta["scrub_version"] = SCRUB_VERSION
        meta["cached_body_sha256"] = sha256(body)
        body_path.write_bytes(body)
        meta_path.write_text(json.dumps(meta, indent=1))
        return body, meta

    # -- robots.txt (RFC 9309: '*' and '$' wildcards, longest match wins, Allow wins ties)
    def robots_txt(self, host):
        if host not in self.robots:
            url = f"https://{host}/robots.txt"
            key = hashlib.sha256(url.encode()).hexdigest()
            meta_path = self.cache / host / f"{key}.json"
            body_path = self.cache / host / f"{key}.bin"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                body = body_path.read_bytes() if body_path.exists() else b""
            elif self.offline:
                raise FileNotFoundError(f"robots.txt for {host} not cached (offline run)")
            else:
                r = self._send(url)
                body = r.content
                meta = {"url": url, "status": r.status_code, "bytes": len(body), "sha256": sha256(body),
                        "fetched_at": utcnow()}
                body_path.parent.mkdir(parents=True, exist_ok=True)
                body_path.write_bytes(body)
                meta_path.write_text(json.dumps(meta, indent=1))
            text = body.decode("utf-8", "replace") if meta["status"] == 200 else ""
            self.robots[host] = {"meta": meta, "text": text, "groups": _robots_groups(text)}
        return self.robots[host]

    def allowed(self, url):
        p = urlparse(url)
        rb = self.robots_txt(p.hostname)
        groups = rb["groups"]
        path = p.path + (f"?{p.query}" if p.query else "")
        agents = [a for a in ("claudebot", "anthropic-ai") if a in groups] or ["*"]
        applicable = [groups.get(a, []) for a in agents]
        if "*" in groups and groups["*"] not in applicable:
            applicable.append(groups["*"])
        return all(_robots_decide(rules, path) for rules in applicable)


def _robots_groups(text):
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
            if v:
                rules.append((k == "allow", v))
        else:
            last_was_agent = False
    return groups


def _robots_match(pattern, path):
    rx = "".join(".*" if ch == "*" else "$" if (ch == "$" and i == len(pattern) - 1) else re.escape(ch)
                 for i, ch in enumerate(pattern))
    return re.match(rx, path) is not None


def _robots_decide(rules, path):
    best = None  # (length, allow)
    for allow, pat in rules:
        if _robots_match(pat, path):
            cand = (len(pat), allow)
            if best is None or cand[0] > best[0] or (cand[0] == best[0] and allow):
                best = cand
    return True if best is None else best[1]
