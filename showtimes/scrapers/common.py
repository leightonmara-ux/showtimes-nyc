"""Shared helpers for theater scrapers (stdlib only, Python 3.9+).

Every scraper module exposes:

    THEATER = {"id": ..., "name": ..., "url": ..., "neighborhood": ...}
    def fetch(days: int = 7) -> List[dict]

Each showtime dict:
    title   str   film title, cleaned of HTML entities / extra whitespace
    start   str   local New York time, ISO "YYYY-MM-DDTHH:MM" (no timezone)
    url     str   ticket link if available, else film page, else None
    format  str   optional, e.g. "35mm", "70mm", "Q&A" (None if unknown)
"""
import datetime as dt
import gzip
import html
import json
import re
import urllib.request
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def get(url, headers=None, data=None, timeout=20):
    """GET (or POST when data is given) and return the decoded body."""
    h = {"User-Agent": UA, "Accept-Encoding": "gzip", "Accept-Language": "en-US,en;q=0.9"}
    h.update(headers or {})
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, headers=h, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        charset = r.headers.get_content_charset() or "utf-8"
        return body.decode(charset, errors="replace")


def get_json(url, **kw):
    return json.loads(get(url, **kw))


def now():
    return dt.datetime.now(NY).replace(tzinfo=None)


def today():
    return now().date()


def clean(s):
    """Strip tags, unescape entities, collapse whitespace."""
    if s is None:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_time(s):
    """'7:30 PM' / '7:30pm' / '19:30' / '7pm' -> (hour, minute)."""
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m?\.?", s, re.I)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
        if ap == "p" and h != 12:
            h += 12
        if ap == "a" and h == 12:
            h = 0
        return h, mi
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    raise ValueError("unparseable time: %r" % s)


def iso(date, hour, minute):
    return "%s-%02d-%02dT%02d:%02d" % (date.year, date.month, date.day, hour, minute)


def show(title, start, url=None, format=None):
    return {"title": clean(title), "start": start, "url": url, "format": format or None}
