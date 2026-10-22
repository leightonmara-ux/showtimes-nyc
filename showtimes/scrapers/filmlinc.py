"""Film at Lincoln Center (Walter Reade Theater + Elinor Bunin Munroe Film Center).

Source: the public JSON feed at https://api.filmlinc.org/showtimes, which the
filmlinc.org calendar uses. It returns {"queueStatus": ..., "films": [...]};
each film has id/title/slug and a list of showtimes with dateTimeET, venue,
ticketsUrl (purchase.filmlinc.org/<season>/<perf>), openCaptions, status, and
sometimes a presaleSchedule (member presale tiers; presaleType "nyff" or
"met-guild"). presaleType is NOT used as the series: e.g. The Devils carries
"nyff" but the site files its current run under "New Releases".

www.filmlinc.org itself is behind a Cloudflare bot challenge, so film pages
(and their series labels, Q&A / intro notes, 35mm info) can't be fetched with
urllib. "series" is therefore always None.

Venues: Walter Reade Theater; Francesca Beale Theater, Howard Gilman Theater
and the Amphitheater are in the Elinor Bunin Munroe Film Center. The feed's venue
field is not surfaced.
"""
import datetime as dt
import re
from typing import List

from .common import clean, get_json, now, show, today

THEATER = {
    "id": "filmlinc",
    "name": "Film at Lincoln Center",
    "url": "https://www.filmlinc.org/",
    "neighborhood": "Upper West Side",
}

API = "https://api.filmlinc.org/showtimes"
FILM_URL = "https://www.filmlinc.org/films/%s/"


def _local(s):
    """'2026-10-22T13:00:00-04:00' -> '2026-10-22T13:00' (already ET local)."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})", s or "")
    return "%sT%s:%s" % m.groups() if m else None


def _format(st, title):
    parts = []
    # The performance description is usually just the title; look only at
    # whatever extra text follows it (e.g. "Title + Q&A", "Title (35mm)").
    low = clean(st.get("description")).lower().replace(title.lower(), " ", 1)
    g = re.search(r"\b(16|35|70)\s*mm\b", low)
    if g:
        parts.append(g.group(1) + "mm")
    if "q&a" in low or "q & a" in low:
        parts.append("Q&A")
    elif re.search(r"\bintro(duc|\b)", low):
        parts.append("Intro")
    if st.get("openCaptions"):
        parts.append("Open Captions")
    if st.get("freeEvent"):
        parts.append("Free")
    return ", ".join(parts) or None


def fetch(days=7):
    # type: (int) -> List[dict]
    start_d = today()
    end_d = start_d + dt.timedelta(days=max(days, 1) - 1)
    cutoff = now().strftime("%Y-%m-%dT%H:%M")

    data = get_json(API)
    out = {}
    for film in (data or {}).get("films") or []:
        try:
            title = clean(film.get("title"))
            if not title:
                continue
            slug = film.get("slug")
            film_url = FILM_URL % slug if slug else None
            for st in film.get("showtimes") or []:
                try:
                    s = _local(st.get("dateTimeET"))
                    if not s:
                        continue
                    d = dt.date(int(s[:4]), int(s[5:7]), int(s[8:10]))
                    if not (start_d <= d <= end_d) or s <= cutoff:
                        continue
                    key = (title.lower(), s)
                    if key in out:
                        continue
                    rec = show(title, s, st.get("ticketsUrl") or film_url, _format(st, title),
                               film_url=film_url)
                    rec["series"] = None  # API carries no series field; see module doc
                    out[key] = rec
                except Exception:
                    continue
        except Exception:
            continue

    return sorted(out.values(), key=lambda x: (x["start"], x["title"]))
