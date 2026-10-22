"""Film Forum (209 W Houston St).

Primary source: the Tessitura/TNEW ticketing API behind my.filmforum.org
(POST /api/products/productionseasons), which returns every performance with a
timezone-aware ISO timestamp and a direct purchase URL.

Enrichment: filmforum.org/events lists special screenings (Q&As, intros, live
music); those are matched to API performances by ticket performance id, or by
date + 12-hour clock time.

Fallback: if the API fails, parse the weekly tabs on filmforum.org/now_playing
(times there have no AM/PM, so a heuristic is used).
"""
import datetime as dt
import re
from typing import Dict, List, Tuple

from .common import NY, clean, get, get_json, iso, now, parse_time, show, today

THEATER = {
    "id": "filmforum",
    "name": "Film Forum",
    "url": "https://filmforum.org/now_playing",
    "neighborhood": "South Village",
}

API = "https://my.filmforum.org/api/products/productionseasons"
EVENTS = "https://filmforum.org/events"
NOW_PLAYING = "https://filmforum.org/now_playing"

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}

_OC_RE = re.compile(r"\s*-\s*(?:OPEN CAPTIONS?|OC)\s*$", re.I)


def _join(*parts):
    out = []
    for p in parts:
        if p and p not in out:
            out.append(p)
    return ", ".join(out) or None


def _event_format(subtitle):
    s = subtitle.lower()
    if "q&a" in s or "q & a" in s or "conversation" in s or "discussion" in s:
        return "Q&A"
    if "introduc" in s:
        return "Intro"
    if "piano" in s or "live music" in s or "accompaniment" in s:
        return "Live music"
    if "35mm" in s:
        return "35mm"
    return None


def _event_date(text, ref):
    """'Saturday, October 24' -> date (rolling the year forward if needed)."""
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2})\b", text.split(",", 1)[-1])
    if not m or m.group(1).lower() not in MONTHS:
        return None
    d = dt.date(ref.year, MONTHS[m.group(1).lower()], int(m.group(2)))
    if d < ref - dt.timedelta(days=60):
        d = dt.date(ref.year + 1, d.month, d.day)
    return d


def _special_events():
    """Return (by_perf_id, by_date_12h) maps of format labels from /events."""
    by_id = {}  # type: Dict[str, str]
    by_slot = {}  # type: Dict[Tuple[dt.date, int, int], str]
    try:
        page = get(EVENTS)
    except Exception:
        return by_id, by_slot
    ref = today()
    for chunk in page.split('class="film-details"')[1:]:
        try:
            m = re.search(r'class="title[^"]*">\s*<a[^>]*>(.*?)</a>', chunk, re.S)
            if not m:
                continue
            parts = re.split(r"<br\s*/?>", m.group(1), maxsplit=1)
            fmt = _event_format(clean(parts[1]) if len(parts) > 1 else "")
            if not fmt:
                continue
            for pid in re.findall(r'href="https://my\.filmforum\.org/[^"/]+/(\d+)"', chunk):
                by_id[pid] = fmt
            d = re.search(r'class="details">\s*<p>(.*?)</p>', chunk, re.S)
            if d:
                bits = re.split(r"<br\s*/?>", d.group(1))
                day = _event_date(clean(bits[0]), ref)
                if day and len(bits) > 1:
                    for t in re.findall(r"\d{1,2}:\d{2}", bits[1]):
                        h, mi = (int(x) for x in t.split(":"))
                        by_slot[(day, h % 12, mi)] = fmt
        except Exception:
            continue
    return by_id, by_slot


def _from_api(start, end):
    # type: (dt.date, dt.date) -> List[dict]
    body = {"startDate": start.strftime("%Y-%m-%dT00:00"),
            "endDate": end.strftime("%Y-%m-%dT00:00")}
    data = get_json(API, data=body, headers={"Accept": "application/json"})
    by_id, by_slot = _special_events()
    out = []
    for prod in data.get("productions") or []:
        for perf in prod.get("performances") or []:
            try:
                if perf.get("isPerformanceVisible") is False:
                    continue
                when = dt.datetime.fromisoformat(perf["iso8601DateString"][:19])
                stamp = perf["iso8601DateString"]
                tz = re.search(r"([+-]\d{2}:\d{2}|Z)$", stamp)
                if tz:  # normalise to NY local in case the offset differs
                    aware = dt.datetime.fromisoformat(
                        stamp[:19] + (tz.group(1).replace("Z", "+00:00")))
                    when = aware.astimezone(NY).replace(tzinfo=None)
                title = perf.get("performanceTitle") or prod.get("productionTitle") or ""
                fmt = None
                if _OC_RE.search(title):
                    title = _OC_RE.sub("", title)
                    fmt = "Open Captions"
                special = by_id.get(str(perf.get("id"))) or by_slot.get(
                    (when.date(), when.hour % 12, when.minute))
                url = perf.get("actionUrl") or prod.get("actionUrl")
                out.append(show(title, iso(when.date(), when.hour, when.minute),
                                url, _join(fmt, special)))
            except Exception:
                continue
    return out


def _from_now_playing():
    """Fallback: weekly tabs; tab-0 is today. Times lack AM/PM."""
    page = get(NOW_PLAYING)
    base = today()
    out = []
    for idx, body in re.findall(r'<div id="tabs-(\d+)">(.*?)</div>', page, re.S):
        day = base + dt.timedelta(days=int(idx))
        for p in re.findall(r"<p>(.*?)</p>", body, re.S):
            try:
                m = re.search(r'<strong><a href="([^"]+)">(.*?)</a></strong>', p, re.S)
                if not m:
                    continue
                for t in re.findall(r"<span>\s*(\d{1,2}:\d{2})\s*</span>", p):
                    h, mi = parse_time(t)
                    if h < 10:  # 1:00-9:59 are afternoon/evening; 10-11 morning; 12 noon
                        h += 12
                    out.append(show(m.group(2), iso(day, h, mi), m.group(1)))
            except Exception:
                continue
    return out


def fetch(days=7):
    # type: (int) -> List[dict]
    start = today()
    end = start + dt.timedelta(days=days)
    try:
        shows = _from_api(start, end)
    except Exception:
        shows = []
    if not shows:
        try:
            shows = _from_now_playing()
        except Exception:
            shows = []
    cutoff = now().strftime("%Y-%m-%dT%H:%M")
    last = iso(end, 0, 0)
    seen = {}
    for s in shows:
        if not (cutoff < s["start"] < last):
            continue
        key = (s["title"].lower(), s["start"])
        if key not in seen or (s["format"] and not seen[key]["format"]):
            seen[key] = s
    return sorted(seen.values(), key=lambda s: (s["start"], s["title"]))
