"""Nitehawk Cinema Prospect Park (Brooklyn).

Source: the theater's own WordPress REST API (namespace nj/v1, "Nightjar"
ticketing), scoped to the /prospectpark/ site so Williamsburg is excluded.

  /wp-json/nj/v1/showtime/listings   every upcoming showtime + purchase URL
  /wp-json/nj/v1/show?include=...    film metadata (format taxonomy: 35mm, DCP...)
  /wp-json/nj/v1/showtime?showdate=  per-showtime flags (open captions, extras)
"""
import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor
from typing import List

from .common import clean, get_json, iso, now, show, today

THEATER = {
    "id": "nitehawk-prospect-park",
    "name": "Nitehawk Cinema Prospect Park",
    "url": "https://nitehawkcinema.com/prospectpark/",
    "neighborhood": "Prospect Park / Windsor Terrace, Brooklyn",
}

API = "https://nitehawkcinema.com/prospectpark/wp-json/nj/v1"
# Film-level formats worth surfacing (DCP is the default, so it is omitted).
NOTABLE_FORMATS = {"35mm", "70mm", "16mm", "4k", "3d", "vhs", "imax"}


def _film_formats(movie_ids):
    """movie_id -> format label (e.g. '35mm'), best effort."""
    out = {}
    ids = [str(i) for i in movie_ids]
    for i in range(0, len(ids), 50):
        try:
            data = get_json("%s/show?per_page=100&include=%s" % (API, ",".join(ids[i:i + 50])))
        except Exception:
            continue
        for film in data if isinstance(data, list) else []:
            try:
                names = [clean(t.get("name")) for t in (film.get("format") or [])
                         if isinstance(t, dict)]
                names = [n for n in names if n.lower() in NOTABLE_FORMATS]
                if names:
                    out[int(film["id"])] = ", ".join(names)
            except Exception:
                continue
    return out


def _showtime_extras(dates):
    """showtime_id -> list of per-showing labels (open captions, Q&A ...)."""
    def one(d):
        try:
            return get_json("%s/showtime?per_page=100&showdate=%s" % (API, d.isoformat()))
        except Exception:
            return []

    out = {}
    with ThreadPoolExecutor(8) as ex:
        pages = list(ex.map(one, dates))
    for data in pages:
        for st in data if isinstance(data, list) else []:
            try:
                labels = []
                if st.get("_open_captions"):
                    labels.append("Open Captions")
                extra = clean(st.get("_extra_shortened") or st.get("_extra") or "")
                if extra:
                    labels.append(extra)
                if labels:
                    out[int(st["id"])] = labels
            except Exception:
                continue
    return out


def fetch(days: int = 7) -> List[dict]:
    data = get_json(API + "/showtime/listings")
    start_date = today()
    end_date = start_date + dt.timedelta(days=days)
    cutoff = now()

    names = {}
    for m in data.get("movies") or []:
        try:
            names[int(m["movie_id"])] = clean(m["movie_name"])
        except Exception:
            continue

    rows = []
    for st in data.get("showtimes") or []:
        try:
            t = dt.datetime.strptime(str(st["datetime"])[:12], "%Y%m%d%H%M")
            if t < cutoff or not (start_date <= t.date() <= end_date):
                continue
            mid = int(st["movie_id"])
            if mid not in names:
                continue
            m = re.search(r"/purchase/(\d+)", st.get("purchase_url") or "")
            rows.append((t, mid, st.get("purchase_url") or None,
                         int(m.group(1)) if m else None, clean(st.get("qualifier") or "")))
        except Exception:
            continue

    formats = _film_formats(sorted({r[1] for r in rows}))
    extras = _showtime_extras(sorted({r[0].date() for r in rows}))

    out, seen = [], set()
    for t, mid, url, sid, qualifier in rows:
        labels = []
        if formats.get(mid):
            labels.append(formats[mid])
        if qualifier:
            labels.append(qualifier)
        labels += extras.get(sid, [])
        labels = list(dict.fromkeys(labels))  # de-dupe, keep order
        start = iso(t.date(), t.hour, t.minute)
        key = (names[mid].lower(), start)
        if key in seen:
            continue
        seen.add(key)
        out.append(show(names[mid], start, url, ", ".join(labels) or None))
    out.sort(key=lambda s: (s["start"], s["title"]))
    return out
