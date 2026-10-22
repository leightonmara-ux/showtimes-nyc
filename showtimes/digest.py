"""Weekly digest: the best-rated films playing in the next 7 days.

    python3 digest.py            # print this week's digest as text
    python3 digest.py --html     # write digest.html (a standalone page you can email)
    python3 digest.py --refresh --html   # scrape every theater + update ratings first (for the weekly email)

The server also serves it at /digest (page) and /api/digest (JSON for the app's Best tab).
"""
import argparse
import datetime as dt
import html
import json
import re
from pathlib import Path

import lists as best_lists
import ratings
from scrapers.common import now

ROOT = Path(__file__).parent
TOP_N = 12
MIN_RATINGS = 100  # below this a score is a handful of opinions, not a rating; such films are left out
FILM_PRINT = ("35mm", "16mm", "70mm")


def norm_title(t):
    return ratings.norm(t)


def score(r):
    """Average of Letterboxd (x2, so both are out of 10) and IMDb: highest rated wins, however many
    people rated it, as long as at least MIN_RATINGS did."""
    vals = [v for v in ((r.get("lb") or 0) * 2, r.get("imdb") or 0) if v]
    n = (r.get("lb_count") or 0) + (r.get("imdb_votes") or 0)
    if not vals or n < MIN_RATINGS:
        return None
    return sum(vals) / len(vals)


def event_for(show, theater_id, events):
    """Same rules as the app: inside the event's dates and named by title/series, ticket URL or title list."""
    d = show["start"][:10]
    hay = (show["title"] + " | " + (show.get("series") or "")).lower()
    nt, url = norm_title(show["title"]), (show.get("url") or "").lower()
    for e in events:
        if not e.get("start") or d < e["start"] or d > (e.get("end") or e["start"]):
            continue
        m = e.get("match") or {}
        keys = [k.lower() for k in (m.get("keywords") or []) + (m.get("series") or []) if k]
        if any(k in hay for k in keys) or any(u.lower() in url for u in m.get("urls") or []):
            return e
        if (not e.get("theaters") or theater_id in e["theaters"]) and \
                any(nt.startswith(norm_title(t)) for t in m.get("titles") or []):
            return e
    return None


def build(data, rating_map, events, list_index=None, start=None, days=7, top=TOP_N):
    list_index = list_index or {}
    start = start or now().date()
    end = start + dt.timedelta(days=days - 1)
    cutoff = now().isoformat(timespec="minutes")
    films = {}
    for t in data["theaters"]:
        for s in t["shows"]:
            d = s["start"][:10]
            if not (start.isoformat() <= d <= end.isoformat()) or s["start"] < cutoff:
                continue
            r = rating_map.get(s["title"])
            if not r:
                continue
            key = r["imdb_url"]
            f = films.setdefault(key, {"titles": [], "rating": r, "theaters": {}, "events": {}, "formats": set()})
            f["titles"].append(s["title"])
            th = f["theaters"].setdefault(t["id"], {"id": t["id"], "name": t["name"], "film_url": None, "shows": []})
            th["film_url"] = th["film_url"] or s.get("film_url")
            th["shows"].append(
                {"start": s["start"], "url": s.get("url"), "format": s.get("format")})
            for part in re.split(r",\s*", s.get("format") or ""):
                if part.lower() in FILM_PRINT or part in ("Q&A", "Intro"):
                    f["formats"].add(part.upper() if "mm" in part.lower() else part)
            e = event_for(s, t["id"], events)
            if e:
                f["events"][e["id"]] = {"name": e["name"], "url": e.get("url")}

    picks = []
    for f in films.values():
        sc = score(f["rating"])
        if sc is None:
            continue
        # Prefer a mixed-case listing title over an ALL-CAPS one.
        title = sorted(f["titles"], key=lambda x: (x.isupper(), len(x)))[0]
        theaters = sorted(f["theaters"].values(), key=lambda x: x["name"])
        for th in theaters:
            th["shows"].sort(key=lambda x: x["start"])
        shows = [s for th in theaters for s in th["shows"]]
        last = max(s["start"] for s in shows)
        notes = sorted(f["formats"])
        year = f["rating"].get("year")
        if year and year >= start.year - 1:  # released this year or last: a new film, not a revival
            notes.insert(0, "New")
        if len(shows) <= 2:
            notes.append("Only %d showing%s" % (len(shows), "" if len(shows) == 1 else "s"))
        r = f["rating"]
        picks.append({
            "title": title, "year": r.get("year"), "score": round(sc, 2),
            "lb": r.get("lb"), "lb_count": r.get("lb_count"), "lb_url": r.get("lb_url"),
            "imdb": r.get("imdb"), "imdb_votes": r.get("imdb_votes"), "imdb_url": r.get("imdb_url"),
            "theaters": theaters, "n_shows": len(shows), "next": min(s["start"] for s in shows), "last": last,
            "notes": notes, "events": list(f["events"].values()),
            "lists": list_index.get(r.get("imdb_id"), []),
        })
    picks.sort(key=lambda p: -p["score"])
    return {"start": start.isoformat(), "end": end.isoformat(), "generated": now().isoformat(timespec="minutes"),
            "rated_films": len(picks), "picks": picks[:top]}


# ---------- rendering ----------

def _d(iso):
    return dt.date.fromisoformat(iso[:10])


def _when(iso):
    d = dt.datetime.fromisoformat(iso)
    h = d.hour % 12 or 12
    return "%s %d:%02d%s" % (d.strftime("%a"), h, d.minute, "pm" if d.hour >= 12 else "am")


def _range(a, b):
    a, b = _d(a), _d(b)
    return "%s – %s" % (a.strftime("%b %-d"), b.strftime("%-d") if a.month == b.month else b.strftime("%b %-d"))


def _k(n):
    return "%.1fM" % (n / 1e6) if n >= 1e6 else "%dk" % round(n / 1e3) if n >= 1e3 else str(n)


def render_text(dg):
    lines = ["Best films playing in NYC, %s" % _range(dg["start"], dg["end"]), ""]
    for i, p in enumerate(dg["picks"], 1):
        rt = []
        if p["lb"] is not None:
            rt.append("Letterboxd %.1f" % p["lb"])
        if p["imdb"] is not None:
            rt.append("IMDb %.1f" % p["imdb"])
        lines.append("%d. %s%s  (%s)" % (i, p["title"], " (%s)" % p["year"] if p["year"] else "", " · ".join(rt)))
        for th in p["theaters"]:
            lines.append("     %s: %s" % (th["name"], ", ".join(_when(s["start"]) for s in th["shows"][:6])
                                         + (" +%d more" % (len(th["shows"]) - 6) if len(th["shows"]) > 6 else "")))
        extra = p["notes"] + ["%s #%s" % (l["short"], l["rank"]) for l in p.get("lists", [])] + ["★ " + e["name"] for e in p["events"]]
        if extra:
            lines.append("     " + " · ".join(extra))
    return "\n".join(lines)


def render_html(dg):
    """Self-contained page with inline styles, so it also survives being pasted into an email."""
    e = html.escape
    rows = []
    for i, p in enumerate(dg["picks"], 1):
        chips = []
        if p["lb"] is not None:
            chips.append('<a href="%s" style="background:#dcf3e4;color:#0f7a3d;text-decoration:none;border-radius:6px;'
                         'padding:2px 7px;font-weight:700">Letterboxd %.1f</a>' % (e(p["lb_url"] or "#"), p["lb"]))
        if p["imdb"] is not None:
            chips.append('<a href="%s" style="background:#f8e7a8;color:#6b5200;text-decoration:none;border-radius:6px;'
                         'padding:2px 7px;font-weight:700">IMDb %.1f</a>' % (e(p["imdb_url"]), p["imdb"]))
        votes = (p["lb_count"] or 0) + (p["imdb_votes"] or 0)
        meta = " ".join(chips) + (' <span style="color:#776d63">%s%s ratings</span>' % (
            "%s · " % p["year"] if p["year"] else "", _k(votes)) if votes else "")
        th_rows = "".join(
            '<div style="margin-top:6px"><b style="font-weight:600">%s</b> <span style="color:#776d63">%s</span></div>' % (
                ('<a href="%s" style="color:#1d1916">%s</a>' % (e(th["film_url"]), e(th["name"])) if th.get("film_url") else e(th["name"])),
                " · ".join('<a href="%s" style="color:#c2361f">%s</a>' % (e(s["url"] or "#"), _when(s["start"]))
                           for s in th["shows"][:8]) + (" +%d more" % (len(th["shows"]) - 8) if len(th["shows"]) > 8 else ""))
            for th in p["theaters"])
        tags = "".join(('<span style="display:inline-block;background:#c2361f;color:#fff;border-radius:999px;padding:2px 8px;'
                        'margin:6px 4px 0 0;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase">New</span>'
                        if n == "New" else
                        '<span style="display:inline-block;background:#f0e8dd;border-radius:999px;padding:2px 8px;'
                        'margin:6px 4px 0 0;font-size:12px">%s</span>' % e(n)) for n in p["notes"])
        tags += "".join('<a href="%s" style="display:inline-block;background:#1d1916;color:#f6efe4;border-radius:999px;padding:2px 8px;'
                        'margin:6px 4px 0 0;font-size:12px;font-weight:600;text-decoration:none">%s #%s</a>' % (e(l["url"] or "#"), e(l["short"]), l["rank"])
                        for l in p.get("lists", []))
        tags += "".join('<span style="display:inline-block;background:#fbefcf;color:#7a5600;border-radius:999px;'
                        'padding:2px 8px;margin:6px 4px 0 0;font-size:12px;font-weight:600">★ %s</span>' % e(ev["name"])
                        for ev in p["events"])
        rows.append(
            '<tr><td style="vertical-align:top;padding:16px 12px 16px 0;font:700 28px Georgia,serif;color:#c2361f;width:36px">%d</td>'
            '<td style="padding:16px 0;border-bottom:1px solid #e4dbcf">'
            '<div style="font:600 20px/1.25 Georgia,serif">%s</div><div style="margin-top:4px;font-size:13px">%s</div>'
            '<div style="font-size:14px">%s</div><div>%s</div></td></tr>' % (i, e(p["title"]), meta, th_rows, tags))
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>This week's best · Showtimes NYC</title></head>
<body style="margin:0;background:#f6f1ea;color:#1d1916;font:15px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<div style="max-width:640px;margin:0 auto;padding:24px 16px">
<div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#776d63">Showtimes NYC · weekly digest</div>
<h1 style="margin:6px 0 2px;font:700 30px/1.1 Georgia,serif">This week's best</h1>
<div style="color:#776d63">%s · top %d of %d rated films playing</div>
<table style="width:100%%;border-collapse:collapse;margin-top:8px">%s</table>
<p style="color:#776d63;font-size:12px;margin-top:20px">Ranked by the average of Letterboxd and IMDb ratings (films with
at least 100 ratings). Generated %s.</p>
</div></body></html>""" % (_range(dg["start"], dg["end"]), len(dg["picks"]), dg["rated_films"], "".join(rows),
                           e(dg["generated"].replace("T", " ")))


def from_files():
    data = json.loads((ROOT / "cache.json").read_text())
    try:
        events = json.loads((ROOT / "events.json").read_text())["events"]
    except (OSError, ValueError, KeyError):
        events = []
    return build(data, ratings.public(ratings.load()), events, best_lists.index())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", action="store_true", help="write digest.html instead of printing text")
    ap.add_argument("--refresh", action="store_true", help="scrape all theaters and update ratings first")
    args = ap.parse_args()
    if args.refresh:
        import server
        server.scrape_all(ratings_async=False)
    dg = from_files()
    if args.html:
        (ROOT / "digest.html").write_text(render_html(dg))
        (ROOT / "digest.txt").write_text(render_text(dg))
        (ROOT / "digest.json").write_text(json.dumps({"subject": "This week's best films in NYC · %s" % _range(dg["start"], dg["end"]),
                                                      "picks": len(dg["picks"]), "rated_films": dg["rated_films"]}))
        print("wrote digest.html, digest.txt, digest.json (%d picks)" % len(dg["picks"]))
    else:
        print(render_text(dg))
