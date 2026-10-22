"""The Paris Theater (Netflix), Midtown Manhattan.

Source: the public, unauthenticated Strapi CMS behind paristheaternyc.com
(cms.ntflxthtrs.com/api/events). It holds dated *events* (special screenings,
intros, Q&As) with Vista ticket links, shared across Netflix venues, so we
keep only those associated with "Paris".

Caveat: the regular daily schedule is NOT in the CMS or the server-rendered
HTML. The site's browser JS loads it from a Vista OCAPI endpoint
(digital-api.paristheaternyc.com/ocapi/v1) that requires an OAuth password
grant token; this scraper deliberately does not sign in, so only CMS events
are returned.
"""
import datetime as dt
import re
import urllib.parse
from typing import List

from .common import clean, get_json, iso, now, parse_time, show, today

THEATER = {
    "id": "paris",
    "name": "The Paris Theater",
    "url": "https://www.paristheaternyc.com/",
    "neighborhood": "Midtown",
}

CMS = "https://cms.ntflxthtrs.com/api/events"
SITE = "https://www.paristheaternyc.com"


def _labels(text, film_format):
    labels = []
    fmt = clean(film_format)
    if fmt and fmt.upper() != "DCP" and not re.search(r"\d+\s?mm", fmt, re.I):
        labels.append(fmt)
    text = fmt + " " + text
    for pat, label in ((r"\b70\s?mm\b", "70mm"), (r"\b35\s?mm\b", "35mm"),
                       (r"\b16\s?mm\b", "16mm"), (r"Q\s?&\s?A", "Q&A"),
                       (r"\bintro(duced|duction)?\b", "Intro"),
                       (r"\bsneak preview\b", "Sneak Preview")):
        if re.search(pat, text, re.I):
            labels.append(label)
    return ", ".join(dict.fromkeys(labels)) or None


def _query(start_date, end_date, page):
    params = [
        ("filters[EventDate][$gte]", start_date.isoformat()),
        ("filters[EventDate][$lte]", end_date.isoformat()),
        ("populate[films][fields][0]", "FilmName"),
        ("populate[films][fields][1]", "Slug"),
        ("populate[films][fields][2]", "FilmFormat"),
        ("sort[0]", "EventDate:asc"),
        ("pagination[page]", str(page)),
        ("pagination[pageSize]", "100"),
    ]
    return CMS + "?" + urllib.parse.urlencode(params)


def fetch(days: int = 7) -> List[dict]:
    start_date = today()
    end_date = start_date + dt.timedelta(days=days)
    cutoff = now()

    events, page = [], 1
    while page <= 10:
        data = get_json(_query(start_date, end_date, page))
        events += data.get("data") or []
        meta = (data.get("meta") or {}).get("pagination") or {}
        if page >= int(meta.get("pageCount") or 1):
            break
        page += 1

    out, seen = [], set()
    for ev in events:
        try:
            a = ev.get("attributes") or {}
            if "Paris" not in (a.get("Association") or []):
                continue
            if not a.get("EventDate") or not a.get("EventTime"):
                continue
            d = dt.date.fromisoformat(a["EventDate"])
            h, mi = parse_time(a["EventTime"])
            if dt.datetime(d.year, d.month, d.day, h, mi) < cutoff:
                continue
            films = [(f.get("attributes") or {}) for f in ((a.get("films") or {}).get("data") or [])]
            film = films[0] if films else {}
            name = clean(a.get("EventName"))
            title = clean(film.get("FilmName")) or name.split("|")[0].strip().title()
            url = a.get("TicketLink") or (
                "%s/film/%s" % (SITE, film["Slug"]) if film.get("Slug") else
                "%s/event/%s" % (SITE, a["Slug"]) if a.get("Slug") else None)
            fmt = _labels(name + " " + clean(a.get("HeroDetails")), film.get("FilmFormat"))
            start = iso(d, h, mi)
            key = (title.lower(), start)
            if key in seen:
                continue
            seen.add(key)
            out.append(show(title, start, url, fmt))
        except Exception:
            continue
    out.sort(key=lambda s: (s["start"], s["title"]))
    return out
