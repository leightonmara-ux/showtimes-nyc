"""BAM Rose Cinemas / BAM Film (Brooklyn).

Source: the JSON endpoint behind bam.org's "Films this week" widget
(/api/BAMApi/GetCalendarEventsByDayWithOnGoing), which lists every BAM event
per day with its performance datetimes and a `genres` field. Only entries whose
genres include "Film" are kept (theater, dance, music, talks are dropped).

Each film's page is then fetched (in parallel) for enrichment:
  * JSON-LD Event list -> per-performance ticket URL (commerce.bam.org/booking/...)
  * /api/BAMApi/GetPerformancesByProduction -> per-performance ticket URL and
    open-caption flags
  * FORMAT block (35mm / 16mm / 70mm) and description notes about Q&As / intros
If enrichment fails for a film, its showtimes are still returned with the
calendar's buy link; if a single film entry is malformed it is skipped.
"""
import datetime as dt
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from .common import clean, get, get_json, iso, now, parse_time, show, today

THEATER = {
    "id": "bam",
    "name": "BAM Rose Cinemas",
    "url": "https://www.bam.org/film",
    "neighborhood": "Fort Greene, Brooklyn",
}

BASE = "https://www.bam.org"
CAL_API = BASE + "/api/BAMApi/GetCalendarEventsByDayWithOnGoing?start=%s&end=%s"
PERF_API = BASE + "/api/BAMApi/GetPerformancesByProduction?ProductionPageId=%s"

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
TIME_RE = re.compile(r"\b(\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)", re.I)
DATE_RE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", re.I)


def _abs(url):
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return BASE + url
    return url


def _mdy(d):
    return "%d/%d/%d" % (d.month, d.day, d.year)


def _local(s):
    """'2026-10-22T16:20:00-04:00' -> '2026-10-22T16:20' (already NY local)."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})", s or "")
    return "%sT%s:%s" % m.groups() if m else None


def _year_for(month, day, ref):
    """Pick the year that puts month/day closest to the reference date."""
    best = None
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            cand = dt.date(y, month, day)
        except ValueError:
            continue
        if best is None or abs((cand - ref).days) < abs((best - ref).days):
            best = cand
    return best


def _notes(text, ref):
    """Parse Q&A / intro notes out of a film description.

    Returns (general, specific): general is a note that applies to every
    screening when no particular screening is named; specific maps
    (date or None, (h, m)) -> note for screenings named by time.
    """
    general = None
    specific = {}  # type: Dict[Tuple[Optional[dt.date], Tuple[int, int]], str]
    for sent in re.split(r"(?<=[.!?])\s+", text):
        low = sent.lower()
        if "q&a" not in low and "introduc" not in low and "in person" not in low:
            continue
        dm = DATE_RE.search(sent)
        date = _year_for(MONTHS[dm.group(1).lower()[:3]], int(dm.group(2)), ref) if dm else None
        times = list(TIME_RE.finditer(sent))
        if not times:
            if "q&a" in low:
                general = "Q&A"
            elif "introduc" in low:
                general = general or "Intro"
            continue
        prev = 0
        last_note = None
        for tm in times:
            seg = low[prev:tm.start()]
            note = "Q&A" if "q&a" in seg else "Intro" if "introduc" in seg else None
            if note is None and seg.strip(" ,") in ("and", "&", "or", ""):
                note = last_note  # "Q&A after the 4pm and 7pm screenings"
            if note is None and "q&a" in low[tm.end():tm.end() + 40]:
                note = "Q&A"  # "the 7pm screening will be followed by a Q&A"
            if note:
                try:
                    specific[(date, parse_time(tm.group(1)))] = note
                except ValueError:
                    pass
            last_note = note
            prev = tm.end()
    return general, specific


def _film_details(more_link, ref):
    """Fetch a film page and return enrichment data (never raises)."""
    info = {"urls": {}, "oc": set(), "gauge": None, "general": None,
            "specific": {}, "count": None}
    try:
        page = get(_abs(more_link))
    except Exception:
        return info
    try:
        fm = re.search(r"<h2>\s*FORMAT\s*</h2>\s*<div[^>]*>(.*?)</div>", page, re.S | re.I)
        if fm:
            g = re.search(r"\b(16|35|70)\s*mm\b", clean(fm.group(1)), re.I)
            if g:
                info["gauge"] = g.group(1) + "mm"
    except Exception:
        pass
    try:
        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
            data = json.loads(block)
            events = data.get("graph") or data.get("@graph") or [data]
            for ev in events:
                if not isinstance(ev, dict) or ev.get("@type") != "Event":
                    continue
                start = _local(ev.get("startDate"))
                url = (ev.get("offers") or {}).get("url")
                if start and url:
                    info["urls"].setdefault(start, url)
    except Exception:
        pass
    try:
        # Description text, minus scripts/styles, for Q&A / intro notes.
        body = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
        descs = re.findall(r'<div class="description">(.*?)</div>', body, re.S)
        text = clean(descs[0]) if descs else ""
        info["general"], info["specific"] = _notes(text, ref)
    except Exception:
        pass
    try:
        pid = re.search(r"ProductionPageId=(\d+)", page)
        if pid:
            perfs = get_json(PERF_API % pid.group(1))
            info["count"] = len(perfs)
            for p in perfs:
                m = re.search(r"href='([^']+)'.*?perfData'>\w+,\s*(\w+)\s+(\d{1,2})\s+at\s+([^<]+)<", p)
                if not m:
                    continue
                d = _year_for(MONTHS.get(m.group(2).lower()[:3], 0) or 1, int(m.group(3)), ref)
                h, mi = parse_time(m.group(4))
                start = iso(d, h, mi)
                info["urls"].setdefault(start, m.group(1))
                if "OPENCAPTION" in p.upper():
                    info["oc"].add(start)
    except Exception:
        pass
    if info["count"] is None and info["urls"]:
        info["count"] = len(info["urls"])
    return info


def fetch(days=7):
    # type: (int) -> List[dict]
    start_d = today()
    end_d = start_d + dt.timedelta(days=max(days, 1) - 1)
    cutoff = now().strftime("%Y-%m-%dT%H:%M")

    events = get_json(CAL_API % (_mdy(start_d), _mdy(end_d)))

    films = []  # (title, start, buyLink, moreLink)
    for ev in events or []:
        try:
            genres = [g.strip().lower() for g in (ev.get("genres") or "").split(",")]
            if "film" not in genres:
                continue
            title = clean(ev.get("name"))
            if not title:
                continue
            for p in ev.get("performances") or []:
                s = _local(p)
                if s:
                    films.append((title, s, ev.get("buyLink"), ev.get("moreLink")))
        except Exception:
            continue

    links = sorted({f[3] for f in films if f[3]})
    details = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for link, info in zip(links, ex.map(lambda l: _film_details(l, start_d), links)):
            details[link] = info

    out = {}
    for title, s, buy, more in films:
        try:
            d = dt.date(int(s[:4]), int(s[5:7]), int(s[8:10]))
            if not (start_d <= d <= end_d) or s <= cutoff:
                continue
            info = details.get(more) or {}
            buy_abs = _abs(buy)
            if buy_abs and (buy_abs.endswith("/production/0") or "#performanceHeader" in buy_abs):
                buy_abs = None
            url = (info.get("urls") or {}).get(s) or buy_abs or _abs(more)

            fmt = []
            if info.get("gauge"):
                fmt.append(info["gauge"])
            key_hm = (int(s[11:13]), int(s[14:16]))
            spec = info.get("specific") or {}
            note = spec.get((d, key_hm)) or spec.get((None, key_hm))
            if note is None and not spec and info.get("general") and (info.get("count") or 0) <= 1:
                note = info["general"]
            if note:
                fmt.append(note)
            if s in (info.get("oc") or ()):
                fmt.append("Open Captions")
            key = (title.lower(), s)
            if key not in out:
                out[key] = show(title, s, url, ", ".join(fmt) or None,
                                film_url=_abs(more))
        except Exception:
            continue

    return sorted(out.values(), key=lambda x: (x["start"], x["title"]))
