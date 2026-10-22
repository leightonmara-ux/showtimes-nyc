"""Metrograph NYC (7 Ludlow St).

Source: the server-rendered showtimes page https://metrograph.com/nyc/, which
contains one <div id="calendar-list-day-YYYY-MM-DD"> block per date (several
weeks ahead). No public JSON API was found; the ticketing system (Vista,
t.metrograph.com) is only exposed as per-session links, which we use as URLs.

Per film: title, film page, "Director / Year / Runtime / Format" metadata
(35mm, 16mm, DCP, 4K DCP ...), an optional event note ("Q&A with ... on
Saturday, October 24th"), and showtime links (or sold-out spans).
"""
import datetime as dt
import re
from typing import List, Optional

from .common import clean, get, iso, now, parse_time, show, today

THEATER = {
    "id": "metrograph",
    "name": "Metrograph",
    "url": "https://metrograph.com/nyc/",
    "neighborhood": "Lower East Side",
}

PAGE = "https://metrograph.com/nyc/"

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
_MD_RE = re.compile(r"\b(%s)\s+(\d{1,2})(?:st|nd|rd|th)?\b" % "|".join(MONTHS), re.I)


def _note_dates(note, year):
    """Dates mentioned in an event note; 'from Oct 23rd-Oct 25th' is a range."""
    found = []
    for m in _MD_RE.finditer(note):
        try:
            found.append(dt.date(year, MONTHS[m.group(1).lower()], int(m.group(2))))
        except ValueError:
            pass
    if len(found) == 2 and re.search(r"\bfrom\b", note, re.I) and found[0] <= found[1]:
        span = (found[1] - found[0]).days
        found = [found[0] + dt.timedelta(days=i) for i in range(span + 1)]
    return set(found)


def _note_format(note, day):
    # type: (str, dt.date) -> Optional[str]
    if not note:
        return None
    low = note.lower()
    if "q&a" in low or "discussion" in low or "in person" in low or "conversation" in low:
        label = "Q&A"
    elif "introduc" in low:
        label = "Intro"
    else:
        return None
    dates = _note_dates(note, day.year)
    return label if day in dates else None


def _parse_film(block, day):
    # type: (str, dt.date) -> List[dict]
    m = re.search(r'<a href="([^"]+)" class="title">(.*?)</a>', block, re.S)
    if not m:
        return []
    film_url, title = m.group(1), m.group(2)
    meta = re.search(r'class="film-metadata">(.*?)</div>', block, re.S)
    fmt = None
    if meta:
        last = clean(meta.group(1)).split(" / ")[-1].strip()
        if re.search(r"mm\b|DCP|Digital|Video|VHS", last, re.I):
            fmt = last
    note = re.search(r'class="film-description">(.*?)</div>', block, re.S)
    special = _note_format(clean(note.group(1)) if note else "", day)
    fmt = ", ".join(x for x in (fmt, special) if x) or None

    times = re.search(r'<div class="showtimes">(.*?)</div>', block, re.S)
    out = []
    if not times:
        return out
    for attrs, label in re.findall(r"<a([^>]*)>(.*?)</a>", times.group(1), re.S):
        try:
            h, mi = parse_time(clean(label))
        except ValueError:
            continue
        href = re.search(r'href="([^"]+)"', attrs)
        if href:
            out.append(show(title, iso(day, h, mi), href.group(1).replace("&amp;", "&"), fmt))
        else:  # Metrograph drops the ticket link once a show sells out
            out.append(show(title, iso(day, h, mi), film_url, ", ".join(x for x in (fmt, "Sold out") if x)))
    return out


def fetch(days=7):
    # type: (int) -> List[dict]
    page = get(PAGE)
    start = today()
    end = start + dt.timedelta(days=days)
    cutoff = now().strftime("%Y-%m-%dT%H:%M")

    # Split into per-day chunks keyed by the id's date.
    marks = list(re.finditer(r'id="calendar-list-day-(\d{4}-\d{2}-\d{2})"', page))
    seen = {}
    for i, mk in enumerate(marks):
        try:
            day = dt.date.fromisoformat(mk.group(1))
        except ValueError:
            continue
        if not (start <= day < end):
            continue
        chunk = page[mk.end(): marks[i + 1].start() if i + 1 < len(marks) else len(page)]
        for block in chunk.split('class="item film-thumbnail')[1:]:
            try:
                shows = _parse_film(block, day)
            except Exception:
                continue
            for s in shows:
                if s["start"] <= cutoff:
                    continue
                seen.setdefault((s["title"].lower(), s["start"]), s)
    return sorted(seen.values(), key=lambda s: (s["start"], s["title"]))
