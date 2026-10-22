#!/usr/bin/env python3
"""Build a static copy of the app that any web host can serve, with no Python server behind it.

    python3 build.py

Scrapes every theater, updates ratings, then writes into static/:
    data/showtimes.json   what /api/showtimes returns (showtimes + events + ratings)
    data/digest.json      what /api/digest returns (the week's best)
    digest.html           the digest page

The app tries the live /api/ endpoints first and falls back to these files, so the same static/
folder works both behind server.py and on GitHub Pages (refreshed by .github/workflows/refresh.yml).
"""
import json

import digest
import ratings
import server

OUT = server.STATIC


def main():
    data = server.scrape_all(ratings_async=False)
    rating_map = ratings.public(ratings.load())
    events = server.load_events()
    (OUT / "data").mkdir(exist_ok=True)
    (OUT / "data" / "showtimes.json").write_text(json.dumps({**data, "events": events, "ratings": rating_map}))
    dg = digest.build(data, rating_map, events.get("events", []))
    (OUT / "data" / "digest.json").write_text(json.dumps(dg))
    (OUT / "digest.html").write_text(digest.render_html(dg))
    for t in data["theaters"]:
        print("%-30s %4d shows  %s" % (t["name"], len(t["shows"]), t["error"] or ""))
    print("built static/: %d rated films, %d digest picks" % (len(rating_map), len(dg["picks"])))
    # A theater that errored keeps its previous listings only when server.py has them in memory;
    # in CI that's never the case, so fail loudly if most of the site came back empty.
    if sum(1 for t in data["theaters"] if t["shows"]) < len(data["theaters"]) // 2:
        raise SystemExit("too many theaters returned nothing; not publishing")


if __name__ == "__main__":
    main()
