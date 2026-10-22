"""IMDb + Letterboxd ratings for the films on the schedule.

    title --(IMDb title search)--> IMDb id + year
    IMDb id --(IMDb's daily title.ratings.tsv.gz dataset)--> IMDb rating, votes
    IMDb id --(letterboxd.com/imdb/<id>/ redirect, page JSON-LD)--> Letterboxd rating

Everything is cached in ratings.json so each film is looked up once; ratings are refreshed every few
days. Matching is by exact title, so a film that shares its title with a more famous one can match
the wrong film: put the right IMDb id in ratings_overrides.json ({"Title as listed": "tt1234567"}).
"""
import gzip
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scrapers.common import UA, get

ROOT = Path(__file__).parent
CACHE = ROOT / "ratings.json"
OVERRIDES = ROOT / "ratings_overrides.json"
IMDB_TSV = ROOT / "imdb_ratings.tsv.gz"
IMDB_TSV_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"

DAY = 86400
LOOKUP_TTL = 30 * DAY   # how long a title -> IMDb id match is trusted
MISS_TTL = 3 * DAY      # retry titles we couldn't match
LB_TTL = 3 * DAY        # Letterboxd rating refresh
TSV_TTL = 1 * DAY       # IMDb publishes the dataset daily
SKIP = re.compile(r"^(shorts?\b|short films?\b|met opera|the met:|mystery movie|secret screening|members?\W)", re.I)
OK_KINDS = {"movie", "tvMovie", "short", "video", "tvMiniSeries", "tvSpecial"}


def norm(t):
    t = t.lower().replace("&", "and")
    t = re.sub(r"^(the|a|an)\s+", "", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def candidates(title):
    """Ways a theater's listing title might be written on IMDb, most literal first."""
    t = re.sub(r"\s+", " ", title).strip()
    out = [t]
    t2 = re.sub(r"\s*(\+|w/|with q&a|–\s*q&a|\(|\[|:\s*\d+(st|nd|rd|th) anniversary).*$", "", t, flags=re.I)                    # "X + Q&A", "X (35mm)"
    t2 = re.sub(r"\s+(in|on)\s+(35|70|16)mm$|\s*-\s*(oc|open captions?)$", "", t2, flags=re.I)  # "X in 35MM", "X - OC"
    out.append(t2)
    out.append(re.sub(r"^[\w.\- ]+[’']s\s+", "", t2))                                             # "Ken Russell's The Devils"
    if ":" in t2:
        head, tail = t2.split(":", 1)
        if re.search(r"\b(presents?|selects|screens|for tots)$", head, re.I):
            out.append(tail)                                                                      # "ACE Presents: Rosemary's Baby"
        out.append(head)                                                                          # "Series: Ch 1-3"
    seen, uniq = set(), []
    for c in out:
        if c and norm(c) and norm(c) not in seen:
            seen.add(norm(c))
            uniq.append(c)
    return uniq


FILM_ERA_END = 2013  # after this, almost nothing new was released on 35mm


def year_hint(title):
    """'Medea (1988)' -> 1988; 'Ghost in the Shell: 30th Anniversary' -> this year - 30."""
    m = re.search(r"\((19\d\d|20\d\d)\)", title)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)(st|nd|rd|th)\s+anniversary", title, re.I)
    if m:
        return time.localtime().tm_year - int(m.group(1))
    return None


def imdb_search(title, on_film=False):
    """Best exact-title IMDb match. Ties go to IMDb's popularity rank, except that a year in the
    title wins outright, and a film print (35mm etc.) means an older film, not a recent namesake."""
    hint = year_hint(title)
    for q in candidates(title):
        url = "https://v3.sg.media-imdb.com/suggestion/x/%s.json" % urllib.parse.quote(q.lower())
        try:
            results = json.loads(get(url)).get("d", [])
        except Exception:
            continue
        films = [r for r in results if r.get("id", "").startswith("tt") and r.get("qid") in OK_KINDS]
        exact = [r for r in films if norm(r.get("l", "")) == norm(q)]
        # Theaters often shorten long titles ("RALPH BUNCHE" for "Ralph Bunche: An American Odyssey").
        if not exact and len(norm(q).split()) >= 2:
            exact = [r for r in films if norm(r.get("l", "")).startswith(norm(q) + " ")][:1]
        if exact and hint:
            best = min(exact, key=lambda r: abs((r.get("y") or 0) - hint))
            return {"imdb_id": best["id"], "year": best.get("y"), "matched": best.get("l")}
        if on_film:
            old = [r for r in exact if (r.get("y") or 0) and r["y"] < FILM_ERA_END]
            exact = old or exact
        if exact:
            best = min(exact, key=lambda r: r.get("rank", 10 ** 9))
            return {"imdb_id": best["id"], "year": best.get("y"), "matched": best.get("l")}
    return None


def letterboxd(imdb_id):
    req = urllib.request.Request("https://letterboxd.com/imdb/%s/" % imdb_id, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        page, url = r.read().decode("utf-8", "replace"), r.geturl()
    if "/film/" not in url:
        return None
    m = re.search(r'"ratingValue":\s*([\d.]+)', page)
    n = re.search(r'"ratingCount":\s*(\d+)', page)
    return {"lb": float(m.group(1)) if m else None, "lb_count": int(n.group(1)) if n else None, "lb_url": url}


def imdb_ratings(ids):
    """{imdb_id: (rating, votes)} for the ids we need, streamed from IMDb's daily dataset."""
    if not IMDB_TSV.exists() or time.time() - IMDB_TSV.stat().st_mtime > TSV_TTL:
        req = urllib.request.Request(IMDB_TSV_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            tmp = IMDB_TSV.with_suffix(".tmp")
            tmp.write_bytes(r.read())
            tmp.replace(IMDB_TSV)
    want, out = set(ids), {}
    with gzip.open(IMDB_TSV, "rt") as f:
        next(f)
        for line in f:
            tid, rating, votes = line.rstrip("\n").split("\t")
            if tid in want:
                out[tid] = (float(rating), int(votes))
    return out


def load():
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def update(titles, log=print):
    """Bring the cache up to date; titles is {listing title: shown on a film print?}.
    Returns {listing title: rating dict}."""
    on_film = dict(titles)
    cache = load()
    try:
        overrides = json.loads(OVERRIDES.read_text())
    except (OSError, ValueError):
        overrides = {}
    now = time.time()
    titles = sorted({t for t in titles if t and not SKIP.search(t)})

    # 1. title -> IMDb id
    def stale(t):
        e = cache.get(t)
        if t in overrides:
            return not e or e.get("imdb_id") != overrides[t]
        if not e:
            return True
        return now - e.get("looked_up", 0) > (LOOKUP_TTL if e.get("imdb_id") else MISS_TTL)

    def lookup(t):
        if t in overrides:
            return t, {"imdb_id": overrides[t], "year": None, "matched": "(override)"}
        return t, imdb_search(t, on_film.get(t, False))

    todo = [t for t in titles if stale(t)]
    if todo:
        log("ratings: matching %d titles on IMDb" % len(todo))
        with ThreadPoolExecutor(6) as ex:
            for t, hit in ex.map(lookup, todo):
                cache[t] = {**(hit or {"imdb_id": None}), "looked_up": now}

    # 2. Letterboxd (a few at a time, to be polite)
    def lb(tid):
        try:
            return tid, letterboxd(tid)
        except Exception:
            return tid, None

    need_lb = {cache[t]["imdb_id"] for t in titles
               if cache.get(t, {}).get("imdb_id") and now - cache[t].get("lb_checked", 0) > LB_TTL}
    if need_lb:
        log("ratings: fetching %d Letterboxd pages" % len(need_lb))
        with ThreadPoolExecutor(4) as ex:
            got = dict(ex.map(lb, sorted(need_lb)))
        for t in titles:
            tid = cache.get(t, {}).get("imdb_id")
            if tid in got:
                cache[t].update(got[tid] or {"lb": None, "lb_url": None})
                cache[t]["lb_checked"] = now

    # 3. IMDb ratings from the dataset
    ids = {cache[t]["imdb_id"] for t in titles if cache.get(t, {}).get("imdb_id")}
    try:
        r = imdb_ratings(ids)
        for t in titles:
            tid = cache.get(t, {}).get("imdb_id")
            if tid:
                cache[t]["imdb"], cache[t]["imdb_votes"] = r.get(tid, (None, None))
    except Exception as e:
        log("ratings: IMDb dataset unavailable (%s)" % e)

    CACHE.write_text(json.dumps(cache, indent=1))
    return public(cache, titles)


def public(cache, titles=None):
    out = {}
    for t, e in cache.items():
        if titles is not None and t not in titles:
            continue
        if not e.get("imdb_id"):
            continue
        out[t] = {"imdb_id": e["imdb_id"], "year": e.get("year"), "imdb": e.get("imdb"), "imdb_votes": e.get("imdb_votes"),
                  "imdb_url": "https://www.imdb.com/title/%s/" % e["imdb_id"],
                  "lb": e.get("lb"), "lb_count": e.get("lb_count"), "lb_url": e.get("lb_url")}
    return out


if __name__ == "__main__":
    import sys
    for t in sys.argv[1:] or ["The Devils", "Ken Russell’s The Devils", "LA BOLA NEGRA", "Giant", "NATIONTIME"]:
        print(t, "->", candidates(t), imdb_search(t), imdb_search(t, on_film=True))
