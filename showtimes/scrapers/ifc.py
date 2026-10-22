"""IFC Center (323 Sixth Ave, Greenwich Village).

Source: the homepage's server-rendered "Showtimes" widget, which holds one
<div class="daily-schedule ..."> block per day for the next 7 days, each with
film links and ticket links (tickets.ifccenter.com) per time. The site has no
public showtime JSON (the WordPress REST API doesn't expose schedules).

Extras:
  * Each film's page is fetched (in parallel) for its "Format" field (35mm,
    70mm, ...; DCP is treated as the default and not reported) and for its
    full schedule list, which fills in dates past the homepage's 7-day window.
  * The homepage "Special Events" sidebar ("Wed Oct 21: <film> - Q&A ... after
    7:00 show") is used to tag Q&A / intro screenings.
"""
import datetime as dt
import html
import re
from concurrent.futures import ThreadPoolExecutor

from .common import clean, get, iso, now, parse_time, show, today

THEATER = {
    "id": "ifc",
    "name": "IFC Center",
    "url": "https://www.ifccenter.com/",
    "neighborhood": "Greenwich Village",
}

BASE = "https://www.ifccenter.com/"
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _date(label, ref):
    """'Wed Oct 21' / 'Thu, Oct 22' -> date, choosing the year nearest ref."""
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})\b", label.split(",")[-1] if "," in label else label)
    if not m:
        m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})\b", label)
    if not m or m.group(1).lower() not in MONTHS:
        raise ValueError("bad date %r" % label)
    mon, day = MONTHS[m.group(1).lower()], int(m.group(2))
    cands = []
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            cands.append(dt.date(y, mon, day))
        except ValueError:
            pass
    return min(cands, key=lambda d: abs((d - ref).days))


def _href(s):
    return html.unescape(s).strip().rstrip("&").strip() or None


def _norm(url):
    return (url or "").split("?")[0].rstrip("/").lower()


def _parse_homepage(page, ref):
    """-> list of (date, title, film_url, (h, m), ticket_url)."""
    out = []
    end = page.find('class="daily-schedule show-coming-soon"')
    blocks = re.split(r'<div class="daily-schedule\s+(?:mon|tue|wed|thu|fri|sat|sun)\b[^"]*">', page)
    for blk in blocks[1:]:
        if end != -1 and 'show-coming-soon' in blk:
            blk = blk.split('show-coming-soon')[0]
        hm = re.search(r"<h3>([^<]+)</h3>", blk)
        if not hm:
            continue
        try:
            day = _date(hm.group(1), ref)
        except ValueError:
            continue
        for film in re.split(r'<div class="details">', blk)[1:]:
            try:
                tm = re.search(r'<h3>\s*<a href="([^"]+)"[^>]*>(.*?)</a>', film, re.S)
                if not tm:
                    continue
                furl, title = _href(tm.group(1)), clean(tm.group(2))
                times = re.search(r'<ul class="times">(.*?)</ul>', film, re.S)
                if not times:
                    continue
                for a in re.finditer(r'<a href="([^"]*)"[^>]*>(.*?)</a>', times.group(1), re.S):
                    try:
                        hhmm = parse_time(clean(a.group(2)))
                    except ValueError:
                        continue
                    out.append((day, title, furl, hhmm, _href(a.group(1))))
            except Exception:
                continue
    return out


def _parse_events(page, ref):
    """Special Events sidebar -> {(date, film_url_norm): [(label, (h,m) or None)]}."""
    ev = {}
    for li in re.findall(r'<li class="ipe-single-container">(.*?)</li>', page, re.S):
        try:
            m = re.search(r"<p><span>([^<]+)</span>.*?<a href=\"([^\"]+)\".*?"
                          r'<span class="ipe-caption">(.*?)</span>', li, re.S)
            if not m:
                continue
            day = _date(m.group(1), ref)
            cap = clean(m.group(3))
            label = _event_label(cap)
            if not label:
                continue
            tm = re.search(r"(?:at|after|following|before)\s+(?:the\s+)?(\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?)", cap, re.I)
            when = None
            if tm:
                t = tm.group(1)
                try:
                    h, mi = parse_time(t if re.search(r"[ap]", t, re.I) else t + "pm")
                    when = (h, mi)
                except ValueError:
                    pass
            ev.setdefault((day, _norm(m.group(2))), []).append((label, when))
        except Exception:
            continue
    return ev


def _event_label(cap):
    c = cap.lower()
    tags = []
    if "q&a" in c or "q & a" in c:
        tags.append("Q&A")
    if re.search(r"\bintro", c):
        tags.append("Intro")
    if "sneak preview" in c:
        tags.append("Sneak Preview")
    if not tags and ("in person" in c or "conversation" in c or "discussion" in c):
        tags.append("Special Event")
    return ", ".join(tags)


def _film_page(url, ref):
    """-> (format or None, [(date, (h,m), ticket_url)])"""
    page = get(url)
    fmt = None
    m = re.search(r"<strong>Format</strong>\s*([^<]+)", page)
    if m:
        f = clean(m.group(1))
        if f and f.upper() != "DCP" and "digital" not in f.lower():
            fmt = f
    rows = []
    sl = re.search(r'<ul class="schedule-list">(.*?)<!--', page, re.S)
    body = sl.group(1) if sl else ""
    for day_blk in re.split(r'<div class="details">', body)[1:]:
        try:
            dm = re.search(r"<strong>([^<]+)</strong>", day_blk)
            if not dm:
                continue
            day = _date(dm.group(1), ref)
            for li in re.findall(r"<li>(.*?)</li>", day_blk, re.S):
                tm = re.search(r"<span>([^<]+)</span>", li)
                if not tm:
                    continue
                try:
                    hhmm = parse_time(tm.group(1))
                except ValueError:
                    continue
                am = re.search(r'<a href="([^"]+)"', li)
                rows.append((day, hhmm, _href(am.group(1)) if am else None))
        except Exception:
            continue
    return fmt, rows


def fetch(days=7):
    ref = today()
    cur = now()
    last = ref + dt.timedelta(days=days - 1)
    page = get(BASE)
    rows = _parse_homepage(page, ref)
    events = _parse_events(page, ref)

    films = {}
    for day, title, furl, hhmm, turl in rows:
        if furl:
            films.setdefault(_norm(furl), (furl, title))

    details = {}

    def load(item):
        key, (furl, _title) = item
        try:
            return key, _film_page(furl, ref)
        except Exception:
            return key, (None, [])

    with ThreadPoolExecutor(max_workers=8) as pool:
        for key, res in pool.map(load, list(films.items())):
            details[key] = res

    # Add film-page schedule rows (covers dates beyond the homepage window).
    for key, (fmt, frows) in details.items():
        furl, title = films[key]
        for day, hhmm, turl in frows:
            rows.append((day, title, furl, hhmm, turl))

    seen = {}
    for day, title, furl, hhmm, turl in rows:
        try:
            if day < ref or day > last:
                continue
            start = iso(day, *hhmm)
            if day == ref and start <= cur.strftime("%Y-%m-%dT%H:%M"):
                continue
            key = (title.lower(), start)
            if key in seen:
                continue
            tags = []
            fmt = details.get(_norm(furl), (None, []))[0]
            if fmt:
                tags.append(fmt)
            for label, when in events.get((day, _norm(furl)), []):
                if when is None or when == hhmm:
                    tags.append(label)
            seen[key] = show(title, start, url=turl or furl,
                             format=", ".join(dict.fromkeys(tags)) or None)
        except Exception:
            continue
    return sorted(seen.values(), key=lambda s: (s["start"], s["title"]))
