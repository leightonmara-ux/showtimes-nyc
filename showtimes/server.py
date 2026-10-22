#!/usr/bin/env python3
"""NYC showtimes aggregator: scrapes each theater, caches the result, serves a mobile web app.

    python3 server.py            # http://localhost:8000 (and your LAN IP, for your phone)
    python3 server.py --once     # scrape once, print a summary, exit
"""
import argparse
import importlib
import json
import socket
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import digest
import ratings
from scrapers.common import now

ROOT = Path(__file__).parent
CACHE = ROOT / "cache.json"
EVENTS = ROOT / "events.json"  # hand-curated festival calendar + ticket on-sale dates
STATIC = ROOT / "static"
SCRAPERS = ["filmforum", "metrograph", "ifc", "angelika", "paris", "nitehawk", "bam", "filmlinc"]
TTL = 30 * 60  # seconds between refreshes

_lock = threading.Lock()
_data = None
_ratings = ratings.public(ratings.load())
_ratings_busy = threading.Lock()
FILM_PRINT = ("35mm", "16mm", "70mm")


def scrape_one(name):
    t0 = time.time()
    try:
        mod = importlib.import_module("scrapers." + name)
        shows = mod.fetch()
        return {**mod.THEATER, "shows": shows, "error": None, "secs": round(time.time() - t0, 1)}
    except Exception as e:
        traceback.print_exc()
        return {"id": name, "name": name, "shows": [], "error": "%s: %s" % (type(e).__name__, e),
                "secs": round(time.time() - t0, 1)}


def scrape_all(ratings_async=True):
    with ThreadPoolExecutor(len(SCRAPERS)) as ex:
        theaters = list(ex.map(scrape_one, SCRAPERS))
    data = {"updated": now().isoformat(timespec="minutes"), "fetched_at": time.time(),
            "theaters": theaters}
    # Keep the previous good result for a theater whose scrape failed this round.
    if _data:
        prev = {t["id"]: t for t in _data["theaters"]}
        for i, t in enumerate(theaters):
            old = prev.get(t["id"])
            if t["error"] and old and old["shows"]:
                theaters[i] = {**old, "error": t["error"], "stale": True}
    CACHE.write_text(json.dumps(data))
    if ratings_async:
        threading.Thread(target=refresh_ratings, args=(data,), daemon=True).start()
    else:
        refresh_ratings(data)
    return data


def refresh_ratings(data):
    """Look up ratings for every listed film in the background; showtimes never wait on this."""
    global _ratings
    if not _ratings_busy.acquire(blocking=False):
        return
    try:
        titles = {}
        for t in data["theaters"]:
            for s in t["shows"]:
                fmt = (s.get("format") or "").lower()
                titles[s["title"]] = titles.get(s["title"], False) or any(f in fmt for f in FILM_PRINT)
        _ratings = ratings.update(titles)
        print("ratings: %d of %d titles rated" % (len(_ratings), len(titles)))
    except Exception:
        traceback.print_exc()
    finally:
        _ratings_busy.release()


def get_data(force=False):
    global _data
    with _lock:
        if _data is None and CACHE.exists():
            _data = json.loads(CACHE.read_text())
        if force or _data is None or time.time() - _data.get("fetched_at", 0) > TTL:
            _data = scrape_all()
        return _data


def load_events():
    try:
        return json.loads(EVENTS.read_text())
    except (OSError, ValueError) as e:
        return {"events": [], "error": str(e)}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STATIC), **kw)

    def send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/digest") or self.path.startswith("/digest"):
            dg = digest.build(get_data(), _ratings, load_events().get("events", []))
            if self.path.startswith("/api/"):
                self.send(json.dumps(dg).encode(), "application/json")
            else:
                self.send(digest.render_html(dg).encode(), "text/html; charset=utf-8")
        elif self.path.startswith("/api/showtimes"):
            data = dict(get_data(force="refresh=1" in self.path))
            data["ratings"] = _ratings
            data["events"] = load_events()
            self.send(json.dumps(data).encode(), "application/json")
        else:
            super().do_GET()

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    if args.once:
        for t in get_data(force=True)["theaters"]:
            print("%-28s %4d shows  %5.1fs  %s" % (t["name"], len(t["shows"]), t["secs"], t["error"] or ""))
        return
    def warm():
        refresh_ratings(get_data())
    threading.Thread(target=warm, daemon=True).start()  # warm the caches
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("Showtimes running at http://localhost:%d" % args.port)
    ip = lan_ip()
    if ip:
        print("On your phone (same Wi-Fi):  http://%s:%d" % (ip, args.port))
    srv.serve_forever()


if __name__ == "__main__":
    main()
