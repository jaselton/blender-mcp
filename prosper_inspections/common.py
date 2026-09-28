"""Shared helpers for the Prosper inspection data: paths, a polite cached HTTP client,
robots.txt checks, and the privacy scrubbers.

Every HTTP response is cached on disk (under PROSPER_CACHE, default ./.cache), so a
re-run never re-fetches. Requests to one host are spaced by a per-host minimum
interval, and 403/429/5xx responses and connection resets are retried with
exponential backoff. Before the first request to a host its /robots.txt is read and
every URL is checked against it (for "*" and for the Anthropic crawler tokens).
"""

import base64
import gzip
import hashlib
import json
import os
import re
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

import requests

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RAW = DATA / "raw"
CACHE = Path(os.environ.get("PROSPER_CACHE", HERE / ".cache"))

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36 (public-records research; prosper_inspections/fetch.py)")

# Seconds between requests to one host (task rules: >= 1.1 s anywhere, 3 s for
# prospertx.gov, 4 s for the Wayback Machine).
MIN_INTERVAL = {
    "www.prospertx.gov": 3.0,
    "web.archive.org": 4.0,
    "prospertx-energovweb.tylerhost.net": 1.5,
}
DEFAULT_INTERVAL = 1.5
# Hosts this project must never contact (their robots.txt disallows automated access).
FORBIDDEN_HOSTS = {"inspections.myhealthdepartment.com", "discovery.cor.gov"}
ROBOT_AGENTS = ("*", "ClaudeBot", "anthropic-ai", "Claude-User", "Claude-SearchBot")

# Privacy scrubbers (project-wide patterns; the lookarounds keep IDs such as
# FE-102744-2026 from being read as phone numbers).
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"(?<![\w-])\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?![\w-])")


def scrub(text):
    """Remove e-mail addresses and phone numbers from free text. (The monthly-report health
    tables carry no certified-food-manager or staff names; parse.py counts any
    'food manager' / 'CFM' mention it finds so that this stays checked.)"""
    if not isinstance(text, str):
        return text
    text = EMAIL.sub("[e-mail]", text)
    text = PHONE.sub("[phone]", text)
    return text


# Mobile food units are listed in the tables with an address that is often the operator's base,
# which can be a home (e.g. a residential street, or a street in another city). Those addresses
# are withheld from everything this project writes to data/.
MOBILE_TYPE = re.compile(r"mobile|\bMFE\b|truck|trailer|food unit|\bcart\b|mobile food vendor", re.I)
OTHER_CITY = re.compile(r",?\s*(?!prosper\b)[A-Za-z .]+,\s*(TX|Texas)\b", re.I)


def withhold_address(business_type, address):
    """True when a table row's address should not be published (mobile unit, or a non-Prosper city)."""
    if MOBILE_TYPE.search(business_type or ""):
        return True
    a = address or ""
    return bool(OTHER_CITY.search(a)) and not re.search(r"\bprosper\b", a, re.I)


def write_json(path, obj, gz=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if gz:  # mtime=0 keeps the compressed bytes identical from run to run
        with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fh:
            fh.write(json.dumps(obj, ensure_ascii=False, indent=0, sort_keys=True).encode("utf-8"))
    else:
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=1))


def read_json(path):
    path = Path(path)
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(path.read_text())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Blocked(Exception):
    pass


class OfflineMiss(Exception):
    """Raised in offline mode when a request is not in the cache."""


class Client:
    """requests.Session with per-host pacing, backoff, robots.txt checks and a disk cache."""

    def __init__(self, cache=CACHE, log_name="requests.log", offline=False):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.last = {}
        self.robots = {}
        self.offline = offline  # True: serve from the cache only, never touch the network
        self.log_path = self.cache / log_name
        # network: HTTP responses received; cached: served from disk; exceptions: connection
        # errors / timeouts; retried_http_responses: 403/429/5xx responses that were retried
        self.stats = {"network": 0, "cached": 0, "exceptions": 0, "retried_http_responses": 0}

    # ------------------------------------------------------------------ logging / pacing
    def log(self, line):
        with open(self.log_path, "a") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + line + "\n")

    def _wait(self, host):
        gap = MIN_INTERVAL.get(host, DEFAULT_INTERVAL)
        dt = time.time() - self.last.get(host, 0.0)
        if dt < gap:
            time.sleep(gap - dt)
        self.last[host] = time.time()

    # ------------------------------------------------------------------ robots.txt
    def _robots_for(self, host):
        if host in self.robots:
            return self.robots[host]
        path = self.cache / "robots" / f"{host}.txt"
        status_path = path.with_suffix(".status")
        if not path.exists():
            if self.offline:
                raise OfflineMiss(f"robots.txt of {host} is not cached")
            status, body = self._raw("GET", f"https://{host}/robots.txt", host, check_robots=False,
                                     ok_status=(200, 404, 410, 401, 403))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            status_path.write_text(str(status))
        status = int(status_path.read_text())
        rp = urllib.robotparser.RobotFileParser()
        if status in (401, 403):
            rp.disallow_all = True
        elif status >= 400:
            rp.allow_all = True
        else:
            text = path.read_bytes().decode("utf-8", "replace")
            if "<html" in text[:500].lower():  # an HTML error page served with 200
                rp.allow_all = True
            else:
                rp.parse(text.splitlines())
        self.robots[host] = (status, rp)
        return self.robots[host]

    def check_allowed(self, url):
        host = urlsplit(url).hostname
        if host in FORBIDDEN_HOSTS:
            raise Blocked(f"{host} is off-limits for this project")
        _, rp = self._robots_for(host)
        for agent in ROBOT_AGENTS:
            if not rp.can_fetch(agent, url):
                raise Blocked(f"robots.txt of {host} disallows {url} for {agent}")

    # ------------------------------------------------------------------ requests
    def _raw(self, method, url, host, check_robots=True, ok_status=(200,), tries=6, **kw):
        if self.offline:
            raise OfflineMiss(f"{method} {url} is not in the cache (offline mode)")
        if check_robots:
            self.check_allowed(url)
        err = None
        for attempt in range(tries):
            self._wait(host)
            try:
                r = self.s.request(method, url, timeout=180, **kw)
            except requests.RequestException as e:
                err = f"{type(e).__name__}"
                self.log(f"{method} {url} -> EXC {err} (try {attempt + 1})")
                self.stats["exceptions"] += 1
                time.sleep(min(6 * 2 ** attempt, 120))
                continue
            self.stats["network"] += 1
            self.log(f"{method} {url} -> {r.status_code} {r.headers.get('content-type', '')} {len(r.content)}B")
            if r.status_code in ok_status:
                return r.status_code, r.content
            if r.status_code in (403, 429) or r.status_code >= 500:
                err = f"HTTP {r.status_code}"
                self.stats["retried_http_responses"] += 1
                time.sleep(min(6 * 2 ** attempt, 120))
                continue
            return r.status_code, r.content  # 404 and friends: no retry
        raise RuntimeError(f"{method} {url} failed after {tries} tries: {err}")

    def get(self, url, dest, method="GET", json_body=None, headers=None, validate=None, ok_status=(200,),
            validate_tries=4):
        """Fetch url into the cache file dest (relative to the cache root) and return its bytes.

        A cached file is returned without any network request. validate(bytes) -> bool rejects
        (and retries) a bad body, e.g. an HTML error page where a PDF was expected.
        Returns (status, content); status is 'cached' when served from disk.
        """
        dest = self.cache / dest
        if dest.exists() and dest.stat().st_size > 0:
            content = dest.read_bytes()
            if validate is None or validate(content):
                self.stats["cached"] += 1
                return "cached", content
        host = urlsplit(url).hostname
        for attempt in range(validate_tries):
            status, content = self._raw(method, url, host, json=json_body, headers=headers,
                                        allow_redirects=True, ok_status=ok_status)
            if status in ok_status and (validate is None or validate(content)):
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(dest.suffix + ".part")
                tmp.write_bytes(content)
                tmp.replace(dest)
                return status, content
            if status not in ok_status:
                return status, content
            self.log(f"{method} {url} -> body failed validation (try {attempt + 1})")
            if attempt + 1 < validate_tries:
                time.sleep(8 * (attempt + 1))
        raise RuntimeError(f"{url}: body never passed validation")


def is_pdf(content):
    return content[:5] == b"%PDF-" or content[:4] == b"%PDF"


def pdf_complete(content):
    """A PDF body that is not truncated: starts with %PDF and has %%EOF in its last 2 KB.
    (The Wayback Machine holds some captures cut off at exactly 1 MiB; they start with
    %PDF but have no end-of-file marker.)"""
    return is_pdf(content) and b"%%EOF" in content[-2048:]


def sha1_base32(content):
    """SHA-1 of a body in the form the Wayback CDX 'digest' field uses (base32)."""
    return base64.b32encode(hashlib.sha1(content).digest()).decode("ascii")


LOG_LINE = re.compile(r"^(\S+Z) (GET|POST) (\S+) -> (EXC \S+|\d{3})")


def request_log_summary(log_path, started=None, finished=None):
    """Network requests in requests.log between two timestamps (inclusive): responses by host
    and status, the non-2xx/404 responses, and exceptions. Built from the log, so it also counts
    responses that were retried successfully."""
    out = {"requests": 0, "by_host_status": {}, "exceptions": 0, "non_ok_responses": []}
    if not Path(log_path).exists():
        return out
    for line in Path(log_path).read_text().splitlines():
        m = LOG_LINE.match(line)
        if not m:
            continue
        ts, method, url, status = m.groups()
        if (started and ts < started) or (finished and ts > finished):
            continue
        host = urlsplit(url).hostname
        key = f"{host} {status.split()[0] if status.startswith('EXC') else status}"
        out["by_host_status"][key] = out["by_host_status"].get(key, 0) + 1
        if status.startswith("EXC"):
            out["exceptions"] += 1
            continue
        out["requests"] += 1
        if status not in ("200", "404") or (status == "404" and not url.endswith("/robots.txt")):
            out["non_ok_responses"].append({"time": ts, "host": host, "status": int(status)})
    out["by_host_status"] = dict(sorted(out["by_host_status"].items()))
    return out
