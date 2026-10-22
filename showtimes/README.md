# Showtimes NYC

Pulls showtimes from Film Forum, Metrograph, IFC Center, Angelika, The Paris, Nitehawk Prospect Park,
BAM and Film at Lincoln Center into one page that works well on a phone.

## Run it

```
python3 server.py
```

Open http://localhost:8000. To use it on your phone, connect to the same Wi-Fi and open the
"On your phone" address the server prints. In Safari, tap Share → **Add to Home Screen** and it
opens full-screen like an app.

Showtimes are cached in `cache.json` and refreshed every 30 minutes, or right away when you tap
Refresh. `python3 server.py --once` runs every scraper once and prints how many shows each found.
That's the quickest way to see if a theater's website has changed.

## Events and ticket dates

`events.json` is a hand-kept calendar of festivals and series (NYFF, DOC NYC, Tribeca, retrospectives…)
with their ticket milestones: member presales, pass sales, general on-sale. The **Events** tab lists
them, the **Heads up** box flags anything going on sale in the next three weeks, and screenings that
belong to an event get a gold ring and a ★ tag.

A screening is linked to an event when it falls inside the event's dates and matches one of the
event's `match` rules: `keywords`/`series` (found in the title or series), `urls` (found in the
ticket link) or `titles` (film titles at the event's `theaters`). Dates with `"confirmed": false`
are estimates from past years and show an EST. badge. Re-check them as announcements come out.

## Ratings and the weekly digest

Each film shows its Letterboxd and IMDb rating. `ratings.py` matches listing titles to IMDb with
IMDb's title search, reads IMDb ratings from IMDb's free daily `title.ratings.tsv.gz` dataset, and
reads Letterboxd ratings from each film's Letterboxd page. Everything is cached in `ratings.json` and
refreshed in the background, so showtimes never wait on it. If a film matches the wrong movie (the
year next to the rating is the giveaway), pin the right one in `ratings_overrides.json`:
`{"Title as the theater lists it": "tt0049261"}`.

The **Best** tab ranks the films playing in the next 7 days by their average rating: Letterboxd (×2,
so both are out of 10) and IMDb. How many people rated a film doesn't affect its rank; films with
fewer than 100 ratings in total are left out, since their scores are only a handful of opinions. The same ranking is at http://localhost:8000/digest as an email-friendly page, and
`python3 digest.py` prints it as text (`--html` writes `digest.html`).

## Hosting it online (GitHub Pages)

`python3 build.py` scrapes everything and writes a static copy of the app into `static/`
(`data/showtimes.json`, `data/digest.json`, `digest.html`). The app uses the live `api/` endpoints
when `server.py` is running and otherwise falls back to those files, so `static/` can be served by
any plain web host.

`.github/workflows/refresh.yml` (at the repository root) runs `build.py` on GitHub every two hours
and publishes `static/` to GitHub Pages, so the app works anywhere, on any phone, with your Mac off.
To trigger a refresh by hand, open the repository's **Actions** tab → *Refresh showtimes* → **Run workflow**.

## Adding a theater

Add `scrapers/<id>.py` with a `THEATER` dict and a `fetch(days=7)` function (see the contract in
`scrapers/common.py`), then add the id to `SCRAPERS` in `server.py` and give it a color in
`static/index.html`.

Requires only Python 3.9+, with no extra packages.
