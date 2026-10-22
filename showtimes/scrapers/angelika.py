"""Angelika Film Center New York (18 W. Houston St).

angelikafilmcenter.com is a React SPA run by Reading International. Its JS
bundle points at https://production-api.readingcinemas.com:

  GET /settings/6            -> anonymous settings incl. a short-lived JWT
                                ("token"), handed to every visitor; country 6
                                is Angelika US
  GET /films?countryId=6&cinemaId=0000000005&status=getShows&flag=initial
             &selectedDate=YYYY-MM-DD   (Authorization: Bearer <token>)
                             -> nowShowing.data.movies[].showdates[].showtypes[]
                                .showtimes[] (session id, date_time with offset)

One request per date. Ticket links use the SPA route
/nyc/sessions/<session id>/<ScheduledFilmId>.
"""
import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

from .common import get_json, now, show, today

THEATER = {
    "id": "angelika",
    "name": "Angelika Film Center",
    "url": "https://angelikafilmcenter.com/nyc",
    "neighborhood": "SoHo / Greenwich Village",
}

API = "https://production-api.readingcinemas.com"
SITE = "https://angelikafilmcenter.com"
COUNTRY_ID = "6"
CINEMA_ID = "0000000005"   # "Angelika New York" in /getcinemas?countryId=6
ALIAS = "nyc"
HEADERS = {"Origin": SITE, "Referer": SITE + "/"}

# showtype labels that carry no useful format info
_PLAIN = {"standard", "english subtitles", "subtitled", "open caption", ""}
_TITLE_FMT = re.compile(r"\s+in\s+(35\s?mm|70\s?mm|16\s?mm)\s*$", re.I)


def _token():
    d = get_json("%s/settings/%s" % (API, COUNTRY_ID), headers=HEADERS)
    return d["data"]["settings"]["token"]


def _format(showtype, title_fmt):
    tags = []
    if title_fmt:
        tags.append(title_fmt)
    labels = [showtype.get("type") or ""] + list(showtype.get("amenities") or [])
    if showtype.get("subType"):
        labels.append(showtype["subType"])
    for lab in labels:
        l = lab.strip().lower()
        if l in _PLAIN:
            continue
        if "q&a" in l or "q & a" in l:
            tags.append("Q&A")
        elif re.match(r"^(35|70|16)\s?mm$", l):
            tags.append(l.replace(" ", ""))
        else:
            tags.append(lab.strip())
    return ", ".join(dict.fromkeys(tags)) or None


def _parse_dt(s):
    """'2026-10-21T21:35:00-04' -> 'YYYY-MM-DDTHH:MM' (already NY local)."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})", s or "")
    if not m:
        raise ValueError(s)
    return "%sT%s:%s" % m.groups()


def _day(token, date):
    q = urlencode({"countryId": COUNTRY_ID, "cinemaId": CINEMA_ID, "status": "getShows",
                   "flag": "initial", "selectedDate": date.isoformat()})
    h = dict(HEADERS, Authorization="Bearer " + token)
    d = get_json("%s/films?%s" % (API, q), headers=h)
    ns = (d.get("nowShowing") or {}).get("data") or {}
    return ns.get("movies") or []


def fetch(days=7):
    start_day = today()
    cur = now().strftime("%Y-%m-%dT%H:%M")
    last = (start_day + dt.timedelta(days=days - 1)).isoformat()
    token = _token()
    dates = [start_day + dt.timedelta(days=i) for i in range(days)]

    def load(date):
        try:
            return _day(token, date)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        per_day = list(pool.map(load, dates))
    seen = {}
    for movies in per_day:
        for mv in movies:
            try:
                raw = (mv.get("name") or "").strip()
                m = _TITLE_FMT.search(raw)
                title_fmt = m.group(1).replace(" ", "").lower() if m else None
                title = _TITLE_FMT.sub("", raw) if m else raw
                film_url = ("%s/%s/movies/details/%s" % (SITE, ALIAS, mv["movieSlug"])
                            if mv.get("movieSlug") else None)
                for sd in mv.get("showdates") or []:
                    for st in sd.get("showtypes") or []:
                        fmt = _format(st, title_fmt)
                        for s in st.get("showtimes") or []:
                            try:
                                start = _parse_dt(s.get("date_time"))
                                if start[:10] > last or start <= cur:
                                    continue
                                if s.get("enabled") is False:
                                    continue
                                url = film_url
                                if s.get("id") and s.get("ScheduledFilmId"):
                                    url = "%s/%s/sessions/%s/%s" % (
                                        SITE, ALIAS, s["id"], s["ScheduledFilmId"])
                                key = (title.lower(), start)
                                if key not in seen:
                                    seen[key] = show(title, start, url=url, format=fmt)
                            except Exception:
                                continue
            except Exception:
                continue
    return sorted(seen.values(), key=lambda x: (x["start"], x["title"]))
