"""HTTP client for inspections.myhealthdepartment.com (City of Frisco portal).

The site's load balancer returns 403 to requests without a browser-like
User-Agent, so every request carries one. Responses are cached on disk so the
scrape is resumable and re-parsing never needs the network.
"""

import json
import os
import random
import threading
import time
from pathlib import Path

import requests

BASE = "https://inspections.myhealthdepartment.com"
JURISDICTION = "frisco"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
# The listing API ignores the requested count and always pages by 25, and
# rejects any start offset above 200 ("bad request"), so one query can return
# at most 225 rows. Callers must shard by date so no window exceeds that.
PAGE_SIZE = 25
MAX_START = 200

_local = threading.local()


class _Throttle:
    """Process-wide request pacing shared by all worker threads.

    The site sits behind a rate-based firewall that answers bursts with 403.
    Requests are spaced by a minimum interval that widens each time a 403
    comes back and slowly relaxes again after a run of successes.
    """

    def __init__(self, interval):
        self.interval = interval
        self.floor = interval
        self.next_at = 0.0
        self.ok_streak = 0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + self.interval
        time.sleep(max(0.0, at - now))

    def blocked(self):
        with self.lock:
            self.interval = min(self.interval * 1.5, 5.0)
            self.ok_streak = 0

    def ok(self):
        with self.lock:
            self.ok_streak += 1
            if self.ok_streak >= 200 and self.interval > self.floor:
                self.interval = max(self.floor, self.interval / 1.2)
                self.ok_streak = 0


throttle = _Throttle(float(os.environ.get("FRISCO_MIN_INTERVAL", "0.8")))


def _session():
    if not hasattr(_local, "session"):
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        _local.session = s
    return _local.session


def _reset_session():
    s = getattr(_local, "session", None)
    if s is not None:
        s.close()
        del _local.session


def cache_dir():
    d = Path(os.environ.get("FRISCO_CACHE", Path(__file__).parent / ".cache"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _request(method, url, *, retries=8, **kw):
    delay = 5.0
    for attempt in range(retries):
        throttle.wait()
        try:
            r = _session().request(method, url, timeout=90, **kw)
            if r.status_code == 200:
                throttle.ok()
                return r
            if r.status_code not in (403, 429, 500, 502, 503, 504):
                r.raise_for_status()
            err = f"HTTP {r.status_code}"
            if r.status_code in (403, 429):
                # Blocked by the site's rate limiter: slow the whole process
                # down, wait out the block, and start a fresh connection.
                throttle.blocked()
                _reset_session()
                delay = max(delay, 60.0)
        except requests.RequestException as e:
            err = str(e)
        if attempt == retries - 1:
            raise RuntimeError(f"{method} {url} failed after {retries} tries: {err}")
        time.sleep(delay + random.random() * 5)
        delay = min(delay * 2, 300)


def search_inspections(date_range, start=0, purpose="", foodtype="", sort=None):
    """One page of the public listing. date_range is 'YYYY-MM-DD to YYYY-MM-DD'.

    sort is {"field": ..., "direction": "asc"|"desc"}; the default order is
    by inspection date only.
    """
    body = {
        "data": {
            "path": JURISDICTION,
            "programName": "",
            "filters": {"date": date_range, "purpose": purpose, "foodtype": foodtype},
            "start": start,
            "count": PAGE_SIZE,
            "searchQueryOverride": None,
            "searchStr": "",
            "lat": 0,
            "lng": 0,
            "sort": sort or {},
        },
        "task": "searchInspections",
    }
    r = _request(
        "POST",
        BASE + "/",
        data=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(f"listing error for {date_range} start={start}: {data}")
    return data


def _cached_get(url, path, binary=False, stale_before=None):
    """GET url, caching the body at path. A cached copy older than
    stale_before (a Unix timestamp) is fetched again."""
    if path.exists() and path.stat().st_size > 0 and (
        stale_before is None or path.stat().st_mtime >= stale_before
    ):
        return path.read_bytes() if binary else path.read_text(encoding="utf-8")
    r = _request("GET", url)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if binary:
        if not r.content.startswith(b"%PDF"):
            raise RuntimeError(f"{url} did not return a PDF")
        tmp.write_bytes(r.content)
    else:
        # The server omits a charset, so requests would guess Latin-1; the
        # pages are UTF-8 (<meta charset="utf-8">).
        text = r.content.decode("utf-8")
        tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return r.content if binary else text


def inspection_url(inspection_id):
    return f"{BASE}/{JURISDICTION}/inspection/?inspectionID={inspection_id}"


def pdf_url(inspection_id):
    return (
        f"{BASE}/{JURISDICTION}/print/?task=getPrintable&path={JURISDICTION}"
        f"&pKey={inspection_id},{inspection_id}"
    )


def permit_url(permit_id):
    return f"{BASE}/{JURISDICTION}/permit/?permitID={permit_id}"


def get_inspection_html(inspection_id):
    return _cached_get(inspection_url(inspection_id), cache_dir() / "html" / f"{inspection_id}.html")


def get_inspection_pdf_path(inspection_id):
    path = cache_dir() / "pdf" / f"{inspection_id}.pdf"
    _cached_get(pdf_url(inspection_id), path, binary=True)
    return path


def get_permit_html(permit_id, stale_before=None):
    # Permit pages list an establishment's full history, which grows; callers
    # pass the listing's scrape time so each run sees the current history.
    return _cached_get(
        permit_url(permit_id), cache_dir() / "permit" / f"{permit_id}.html", stale_before=stale_before
    )


def get_food_types():
    """{food_type_managerID: name} from the landing page's Food Type filter."""
    import re
    import html as htmllib

    text = _request("GET", f"{BASE}/{JURISDICTION}").content.decode("utf-8")
    select = text.split('id="filterfoodtype"', 1)[1].split("</select>", 1)[0]
    return {
        m.group(1): htmllib.unescape(m.group(2)).strip()
        for m in re.finditer(r'<option value="([0-9A-F-]{36})">([^<]*)</option>', select)
    }
